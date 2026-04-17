import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.execution.idempotency import enqueue_once
from app.models import ConnectorJob


def _make_job(status: str, idempotency_key: str, tenant_id: uuid.UUID, integration_id: uuid.UUID) -> ConnectorJob:
    return ConnectorJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        integration_id=integration_id,
        provider="google",
        job_type="sync",
        payload={},
        status=status,
        retry_count=0,
        max_retries=5,
        idempotency_key=idempotency_key,
    )


@pytest.fixture
def base_args(tenant_id, active_google_integration):
    return dict(
        job_fn=MagicMock(),
        idempotency_key=f"test:{uuid.uuid4()}",
        tenant_id=tenant_id,
        integration_id=active_google_integration.id,
        provider="google",
        job_type="sync",
        payload={},
    )


def test_enqueue_once_creates_job_and_enqueues(base_args, tenant_id, active_google_integration):
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None  # no existing job

    mock_queue = MagicMock()
    result = enqueue_once(**base_args, db=db, queue=mock_queue)

    assert result is not None
    db.add.assert_called_once()
    db.commit.assert_called_once()
    mock_queue.enqueue.assert_called_once()


def test_enqueue_once_deduplicates_pending_job(base_args, tenant_id, active_google_integration):
    existing = _make_job("pending", base_args["idempotency_key"], tenant_id, active_google_integration.id)
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = existing

    mock_queue = MagicMock()
    result = enqueue_once(**base_args, db=db, queue=mock_queue)

    assert result is None
    mock_queue.enqueue.assert_not_called()


def test_enqueue_once_deduplicates_running_job(base_args, tenant_id, active_google_integration):
    existing = _make_job("running", base_args["idempotency_key"], tenant_id, active_google_integration.id)
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = existing

    mock_queue = MagicMock()
    result = enqueue_once(**base_args, db=db, queue=mock_queue)

    assert result is None


def test_enqueue_once_deduplicates_successful_job(base_args, tenant_id, active_google_integration):
    existing = _make_job("success", base_args["idempotency_key"], tenant_id, active_google_integration.id)
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = existing

    mock_queue = MagicMock()
    result = enqueue_once(**base_args, db=db, queue=mock_queue)

    assert result is None


def test_enqueue_once_allows_reenqueue_of_failed_job(base_args, tenant_id, active_google_integration):
    existing = _make_job("failed", base_args["idempotency_key"], tenant_id, active_google_integration.id)
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = existing

    mock_queue = MagicMock()
    result = enqueue_once(**base_args, db=db, queue=mock_queue)

    assert result is not None
    mock_queue.enqueue.assert_called_once()


def test_enqueue_once_handles_integrity_error_race(base_args):
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None
    db.commit.side_effect = IntegrityError("unique violation", {}, None)

    mock_queue = MagicMock()
    result = enqueue_once(**base_args, db=db, queue=mock_queue)

    assert result is None
    db.rollback.assert_called_once()
    mock_queue.enqueue.assert_not_called()
