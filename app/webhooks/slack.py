import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.execution.idempotency import enqueue_once
from app.execution.jobs.webhook_job import run_webhook_job
from app.models import Integration
from app.observability.logging import get_logger, new_correlation_id

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

SLACK_TIMESTAMP_TOLERANCE_SECONDS = 300


def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> None:
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid timestamp")

    if abs(time.time() - ts) > SLACK_TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="Request timestamp too old — possible replay")

    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        settings.SLACK_SIGNING_SECRET.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")


def _find_integration_for_team(team_id: str | None, db: Session) -> Integration | None:
    if not team_id:
        return None
    return (
        db.query(Integration)
        .filter_by(provider="slack", status="active")
        .filter(Integration.scopes.any())
        .first()
    )


@router.post("/slack/events")
async def slack_events(request: Request):
    new_correlation_id()
    log = get_logger()

    body = await request.body()

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    _verify_slack_signature(body, timestamp, signature)

    payload = json.loads(body)

    # Slack sends this once to verify the endpoint
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload["challenge"]})

    event_id = payload.get("event_id") or str(uuid.uuid4())
    team_id = payload.get("team_id")

    log.info("webhook_received", event_id=event_id, team_id=team_id, event_type=payload.get("event", {}).get("type"))

    db = SessionLocal()
    try:
        integration = _find_integration_for_team(team_id, db)
        if integration is None:
            # No matching integration — ack and drop
            log.warning("webhook_no_integration", team_id=team_id)
            return Response(status_code=200)

        enqueue_once(
            job_fn=run_webhook_job,
            idempotency_key=f"webhook:{event_id}",
            tenant_id=integration.tenant_id,
            integration_id=integration.id,
            provider="slack",
            job_type="webhook",
            payload=payload,
            db=db,
        )
        log.info("webhook_enqueued", event_id=event_id)
    finally:
        db.close()

    # Must respond within Slack's 3-second window
    return Response(status_code=200)
