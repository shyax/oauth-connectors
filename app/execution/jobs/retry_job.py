import random
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.execution.queue import dead_letter_queue, retry_queue
from app.models import ConnectorJob
from app.observability.logging import get_logger

BASE_DELAY_SECONDS = 2


def handle_job_failure(job: ConnectorJob, error: Exception, db: Session) -> None:
    log = get_logger()
    job.retry_count += 1
    job.error_detail = str(error)[:500]

    if job.retry_count >= job.max_retries:
        job.status = "dead"
        db.commit()
        dead_letter_queue.enqueue(_record_dead_letter, str(job.id))
        log.warning("job_dead", job_id=str(job.id), provider=job.provider, retries=job.retry_count)
        return

    delay = (BASE_DELAY_SECONDS ** job.retry_count) + random.uniform(0, 1)
    job.status = "pending"
    db.commit()

    from app.execution.rate_limit import _run_job_by_id
    retry_queue.enqueue_in(timedelta(seconds=delay), _run_job_by_id, str(job.id))
    log.info(
        "job_retry_scheduled",
        job_id=str(job.id),
        retry_count=job.retry_count,
        delay_seconds=round(delay, 1),
    )


def _record_dead_letter(job_id: str) -> None:
    from app.db import db_session
    from app.models import ConnectorJob

    with db_session() as db:
        job = db.get(ConnectorJob, uuid.UUID(job_id))
        if job:
            job.status = "dead"
            db.commit()
