import uuid
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.execution.queue import default_queue
from app.models import ConnectorJob
from app.observability.logging import get_logger


def enqueue_once(
    job_fn: Callable,
    idempotency_key: str,
    tenant_id: uuid.UUID,
    integration_id: uuid.UUID,
    provider: str,
    job_type: str,
    payload: dict,
    db: Session,
    max_retries: int = 5,
    queue=None,
) -> uuid.UUID | None:
    log = get_logger()
    if queue is None:
        queue = default_queue

    existing = db.query(ConnectorJob).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        if existing.status in ("pending", "running", "success"):
            log.info("job_deduplicated", idempotency_key=idempotency_key, existing_status=existing.status)
            return None
        # failed job within retry budget — allow re-enqueue by falling through

    job_id = uuid.uuid4()
    job = ConnectorJob(
        id=job_id,
        tenant_id=tenant_id,
        integration_id=integration_id,
        provider=provider,
        job_type=job_type,
        payload=payload,
        status="pending",
        idempotency_key=idempotency_key,
        max_retries=max_retries,
        retry_count=existing.retry_count if existing else 0,
    )

    if existing:
        db.delete(existing)
        db.flush()

    try:
        db.add(job)
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info("job_deduplicated_race", idempotency_key=idempotency_key)
        return None

    queue.enqueue(job_fn, str(job_id), job_id=str(job_id))
    log.info("job_enqueued", job_id=str(job_id), job_type=job_type, idempotency_key=idempotency_key)
    return job_id
