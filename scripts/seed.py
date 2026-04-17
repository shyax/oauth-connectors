"""
Seed script — creates 2 tenants with mock integrations and enqueues sample jobs.

Usage:
    docker-compose exec api python scripts/seed.py
    # or locally:
    PYTHONPATH=. python scripts/seed.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.token_manager import encrypt_token
from app.db import SessionLocal
from app.execution.idempotency import enqueue_once
from app.execution.jobs.sync_job import run_sync_job
from app.models import Integration
from app.observability.logging import configure_logging, get_logger

configure_logging()
log = get_logger()

TENANTS = [
    {"id": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"), "name": "Acme Corp"},
    {"id": uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"), "name": "Globex Inc"},
]

FAKE_GOOGLE_ACCESS = "ya29.FAKE_ACCESS_TOKEN_GOOGLE"
FAKE_GOOGLE_REFRESH = "1//FAKE_REFRESH_TOKEN_GOOGLE"
FAKE_SLACK_ACCESS = "xoxb-FAKE-SLACK-BOT-TOKEN"


def seed_integrations(db) -> list[Integration]:
    created = []

    for tenant in TENANTS:
        tid = tenant["id"]

        # Google integration — expires in 1 hour
        google = Integration(
            id=uuid.uuid4(),
            tenant_id=tid,
            provider="google",
            access_token=encrypt_token(FAKE_GOOGLE_ACCESS),
            refresh_token=encrypt_token(FAKE_GOOGLE_REFRESH),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["https://www.googleapis.com/auth/drive.readonly", "openid", "email"],
            status="active",
        )
        db.add(google)

        # Slack integration — no expiry
        slack = Integration(
            id=uuid.uuid4(),
            tenant_id=tid,
            provider="slack",
            access_token=encrypt_token(FAKE_SLACK_ACCESS),
            refresh_token=None,
            expires_at=None,
            scopes=["chat:write", "channels:read", "channels:history"],
            status="active",
        )
        db.add(slack)
        created.extend([google, slack])

        log.info("tenant_seeded", tenant_name=tenant["name"], tenant_id=str(tid))

    db.commit()
    return created


def seed_sync_jobs(db, integrations: list[Integration]) -> None:
    google_integrations = [i for i in integrations if i.provider == "google"]
    for integration in google_integrations:
        job_id = enqueue_once(
            job_fn=run_sync_job,
            idempotency_key=f"seed_sync:{integration.id}",
            tenant_id=integration.tenant_id,
            integration_id=integration.id,
            provider="google",
            job_type="sync",
            payload={},
            db=db,
        )
        if job_id:
            log.info("sync_job_enqueued", job_id=str(job_id), integration_id=str(integration.id))
        else:
            log.info("sync_job_already_exists", integration_id=str(integration.id))


def main():
    db = SessionLocal()
    try:
        log.info("seed_start")
        integrations = seed_integrations(db)
        seed_sync_jobs(db, integrations)
        log.info("seed_complete", integrations_created=len(integrations))
        print(f"\nSeeded {len(TENANTS)} tenants, {len(integrations)} integrations.")
        print("\nTenant IDs:")
        for t in TENANTS:
            print(f"  {t['name']}: {t['id']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
