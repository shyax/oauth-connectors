import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.execution.queue import retry_queue
from app.models import ConnectorJob
from app.observability.logging import get_logger

MAX_JITTER_SECONDS = 5


def handle_rate_limit(job: ConnectorJob, retry_after_seconds: int, db: Session) -> None:
    log = get_logger()
    jitter = random.uniform(0, MAX_JITTER_SECONDS)
    delay = retry_after_seconds + jitter

    job.status = "pending"
    job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    db.commit()

    # Rate limit retries do NOT increment retry_count — this is expected provider behavior
    retry_queue.enqueue_in(timedelta(seconds=delay), _run_job_by_id, str(job.id))
    log.info(
        "rate_limit_retry_scheduled",
        job_id=str(job.id),
        delay_seconds=round(delay, 1),
        provider=job.provider,
    )


def _run_job_by_id(job_id: str) -> None:
    from app.execution.jobs.sync_job import run_sync_job
    from app.execution.jobs.action_job import run_action_job
    from app.execution.jobs.webhook_job import run_webhook_job
    from app.db import db_session
    from app.models import ConnectorJob

    with db_session() as db:
        job = db.get(ConnectorJob, uuid.UUID(job_id))
        if job is None:
            return
        dispatcher = {"sync": run_sync_job, "action": run_action_job, "webhook": run_webhook_job}
        fn = dispatcher.get(job.job_type)
        if fn:
            fn(job_id)
