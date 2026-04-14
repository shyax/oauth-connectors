import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import DBDep, TenantDep
from app.execution.idempotency import enqueue_once
from app.execution.jobs.action_job import run_action_job
from app.models import Integration
from app.normalization.schemas import SendMessageRequest, SendMessageResponse
from app.observability.logging import get_logger

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=SendMessageResponse, status_code=202)
def send_message(
    body: SendMessageRequest,
    tenant_id: Annotated[uuid.UUID, TenantDep],
    db: Annotated[Session, DBDep],
):
    log = get_logger()

    integration = db.query(Integration).filter_by(
        id=body.integration_id, tenant_id=tenant_id, provider="slack"
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Slack integration not found for this tenant")
    if not integration.is_active():
        raise HTTPException(status_code=409, detail=f"Integration is {integration.status} — cannot send message")

    idempotency_key = f"send_message:{body.integration_id}:{body.channel}:{hash(body.text)}"

    job_id = enqueue_once(
        job_fn=run_action_job,
        idempotency_key=idempotency_key,
        tenant_id=tenant_id,
        integration_id=body.integration_id,
        provider="slack",
        job_type="action",
        payload={"action": "send_message", "channel": body.channel, "text": body.text},
        db=db,
    )

    if job_id is None:
        # Deduplicated — find the existing job to return its ID
        from app.models import ConnectorJob
        existing = db.query(ConnectorJob).filter_by(idempotency_key=idempotency_key).first()
        job_id = existing.id if existing else uuid.uuid4()

    log.info("message_enqueued", job_id=str(job_id), channel=body.channel)
    return SendMessageResponse(job_id=job_id, idempotency_key=idempotency_key)
