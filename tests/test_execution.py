import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.base import ProviderAuthError, RateLimitError
from app.execution.jobs.retry_job import handle_job_failure
from app.execution.rate_limit import handle_rate_limit
from app.models import ConnectorJob


def _job(retry_count=0, max_retries=5, status="running", tenant_id=None, integration_id=None) -> ConnectorJob:
    return ConnectorJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        integration_id=integration_id or uuid.uuid4(),
        provider="google",
        job_type="sync",
        payload={},
        status=status,
        retry_count=retry_count,
        max_retries=max_retries,
        idempotency_key=f"test:{uuid.uuid4()}",
    )


class TestRetryJob:
    def test_increments_retry_count_on_failure(self):
        job = _job(retry_count=0)
        db = MagicMock()

        with patch("app.execution.jobs.retry_job.retry_queue") as mock_queue:
            handle_job_failure(job, RuntimeError("transient"), db)

        assert job.retry_count == 1
        assert job.status == "pending"
        mock_queue.enqueue_in.assert_called_once()

    def test_sends_to_dead_letter_at_max_retries(self):
        job = _job(retry_count=4, max_retries=5)
        db = MagicMock()

        with patch("app.execution.jobs.retry_job.dead_letter_queue") as mock_dl:
            with patch("app.execution.jobs.retry_job.retry_queue") as mock_retry:
                handle_job_failure(job, RuntimeError("final"), db)

        assert job.status == "dead"
        mock_dl.enqueue.assert_called_once()
        mock_retry.enqueue_in.assert_not_called()

    def test_stores_error_detail(self):
        job = _job()
        db = MagicMock()

        with patch("app.execution.jobs.retry_job.retry_queue"):
            handle_job_failure(job, RuntimeError("something went wrong"), db)

        assert "something went wrong" in job.error_detail

    def test_truncates_long_error_detail(self):
        job = _job()
        db = MagicMock()
        long_error = "x" * 1000

        with patch("app.execution.jobs.retry_job.retry_queue"):
            handle_job_failure(job, RuntimeError(long_error), db)

        assert len(job.error_detail) <= 500

    def test_backoff_delay_increases_with_retry_count(self):
        delays = []
        for count in range(1, 4):
            job = _job(retry_count=count - 1)
            db = MagicMock()
            with patch("app.execution.jobs.retry_job.retry_queue") as mock_queue:
                handle_job_failure(job, RuntimeError("err"), db)
                call_args = mock_queue.enqueue_in.call_args
                delay: timedelta = call_args[0][0]
                delays.append(delay.total_seconds())

        assert delays[0] < delays[1] < delays[2]


class TestRateLimit:
    def test_rate_limit_does_not_increment_retry_count(self):
        job = _job(retry_count=2)
        db = MagicMock()

        with patch("app.execution.rate_limit.retry_queue") as mock_queue:
            handle_rate_limit(job, 30, db)

        assert job.retry_count == 2  # unchanged
        assert job.status == "pending"
        mock_queue.enqueue_in.assert_called_once()

    def test_rate_limit_adds_jitter(self):
        delays = set()
        job_factory = lambda: _job()
        db = MagicMock()

        for _ in range(10):
            with patch("app.execution.rate_limit.retry_queue"):
                j = job_factory()
                handle_rate_limit(j, 60, db)
                delays.add(j.scheduled_at)

        # With jitter, not all scheduled_at values should be identical
        assert len(delays) > 1


class TestSyncJobRunner:
    def test_marks_failed_when_integration_inactive(self):
        from app.execution.jobs.sync_job import run_sync_job

        job = _job()
        job.status = "pending"

        inactive_integration = MagicMock()
        inactive_integration.is_active.return_value = False

        with patch("app.execution.jobs.sync_job.db_session") as mock_ctx:
            mock_db = MagicMock()
            mock_db.get.side_effect = [job, inactive_integration]
            mock_ctx.return_value.__enter__.return_value = mock_db

            run_sync_job(str(job.id))

        assert job.status == "failed"
        assert job.error_detail == "integration not active"

    def test_marks_revoked_on_provider_auth_error(self):
        from app.execution.jobs.sync_job import run_sync_job

        job = _job()
        integration = MagicMock()
        integration.is_active.return_value = True

        with patch("app.execution.jobs.sync_job.db_session") as mock_ctx:
            mock_db = MagicMock()
            mock_db.get.side_effect = [job, integration, integration]
            mock_ctx.return_value.__enter__.return_value = mock_db

            with patch("app.execution.jobs.sync_job.get_connector") as mock_conn_factory:
                mock_conn = MagicMock()
                mock_conn.execute.side_effect = ProviderAuthError("token revoked")
                mock_conn_factory.return_value = mock_conn

                run_sync_job(str(job.id))

        assert job.status == "failed"
        integration.status = "revoked"
