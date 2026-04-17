import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.auth.token_manager import encrypt_token
from app.models import ConnectorJob, Integration


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")


@pytest.fixture
def active_google_integration(tenant_id) -> Integration:
    return Integration(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="google",
        access_token=encrypt_token("ya29.fake_access"),
        refresh_token=encrypt_token("1//fake_refresh"),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
        status="active",
    )


@pytest.fixture
def expiring_google_integration(tenant_id) -> Integration:
    return Integration(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="google",
        access_token=encrypt_token("ya29.expiring"),
        refresh_token=encrypt_token("1//fresh_refresh"),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),  # within buffer
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
        status="active",
    )


@pytest.fixture
def active_slack_integration(tenant_id) -> Integration:
    return Integration(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="slack",
        access_token=encrypt_token("xoxb-fake-bot-token"),
        refresh_token=None,
        expires_at=None,
        scopes=["chat:write", "channels:read"],
        status="active",
    )


@pytest.fixture
def pending_job(tenant_id, active_google_integration) -> ConnectorJob:
    return ConnectorJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        integration_id=active_google_integration.id,
        provider="google",
        job_type="sync",
        payload={},
        status="pending",
        retry_count=0,
        max_retries=5,
        idempotency_key=f"test_sync:{uuid.uuid4()}",
    )


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_redis():
    with patch("app.auth.token_manager.redis_conn") as mock:
        mock.set.return_value = True  # lock always acquired
        yield mock
