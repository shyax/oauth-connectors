import uuid
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.config import settings
from app.redis_client import redis_conn

_fernet = Fernet(settings.ENCRYPTION_KEY.encode())

REFRESH_LOCK_TTL_MS = 30_000  # 30 seconds


def encrypt_token(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


def needs_refresh(integration) -> bool:
    if integration.expires_at is None:
        return False
    buffer = timedelta(seconds=settings.REFRESH_BUFFER_SECONDS)
    return integration.expires_at < datetime.now(timezone.utc) + buffer


def refresh_integration(integration_id: uuid.UUID, db: Session):
    from app.models import Integration
    from app.auth.oauth_google import google_refresh
    from app.auth.oauth_slack import slack_refresh
    from app.observability.logging import get_logger

    log = get_logger()
    lock_key = f"refresh_lock:{integration_id}"

    acquired = redis_conn.set(lock_key, "1", px=REFRESH_LOCK_TTL_MS, nx=True)
    if not acquired:
        # Another process holds the lock — wait briefly then re-read
        import time
        time.sleep(0.5)
        return db.get(Integration, integration_id)

    try:
        integration = db.get(Integration, integration_id)
        if integration is None:
            raise ValueError(f"Integration {integration_id} not found")

        # Double-check inside lock
        if not needs_refresh(integration):
            return integration

        log.info("token_refresh_started", integration_id=str(integration_id), provider=integration.provider)

        if integration.provider == "google":
            token_data = google_refresh(decrypt_token(integration.refresh_token))
        elif integration.provider == "slack":
            token_data = slack_refresh(integration)
        else:
            raise ValueError(f"Unknown provider: {integration.provider}")

        integration.access_token = encrypt_token(token_data["access_token"])
        if token_data.get("refresh_token"):
            # Google may issue a new refresh_token — always persist when present
            integration.refresh_token = encrypt_token(token_data["refresh_token"])
        if token_data.get("expires_at"):
            integration.expires_at = token_data["expires_at"]

        integration.status = "active"
        db.commit()
        db.refresh(integration)

        log.info("token_refresh_succeeded", integration_id=str(integration_id))
        return integration

    except Exception as e:
        log.error("token_refresh_failed", integration_id=str(integration_id), error=str(e))
        integration = db.get(Integration, integration_id)
        if integration and "invalid_grant" in str(e).lower():
            integration.status = "revoked"
            db.commit()
        raise
    finally:
        redis_conn.delete(lock_key)


def get_valid_access_token(integration, db: Session) -> str:
    if needs_refresh(integration):
        integration = refresh_integration(integration.id, db)
    return decrypt_token(integration.access_token)
