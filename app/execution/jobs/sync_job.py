import uuid

from app.connectors import get_connector
from app.connectors.base import ProviderAuthError, RateLimitError
from app.db import db_session
from app.execution.jobs.retry_job import handle_job_failure
from app.execution.rate_limit import handle_rate_limit
from app.models import ConnectorJob, Integration
from app.observability.logging import bind_request_context, get_logger


def run_sync_job(job_id: str) -> None:
    log = get_logger()

    with db_session() as db:
        job = db.get(ConnectorJob, uuid.UUID(job_id))
        if job is None:
            return

        bind_request_context(tenant_id=str(job.tenant_id), job_id=job_id)
        job.status = "running"
        db.commit()

        log.info("job_started", job_id=job_id, job_type="sync", provider=job.provider)

        try:
            integration = db.get(Integration, job.integration_id)
            if integration is None or not integration.is_active():
                job.status = "failed"
                job.error_detail = "integration not active"
                db.commit()
                return

            connector = get_connector(job.provider, integration, db)
            result = connector.execute("incremental_sync", job.payload)

            job.status = "success"
            db.commit()
            log.info("job_succeeded", job_id=job_id, result=result)

        except RateLimitError as e:
            job.status = "pending"
            db.commit()
            handle_rate_limit(job, e.retry_after_seconds, db)

        except ProviderAuthError as e:
            log.warning("integration_revoked", job_id=job_id, error=str(e))
            integration = db.get(Integration, job.integration_id)
            if integration:
                integration.status = "revoked"
            job.status = "failed"
            job.error_detail = str(e)
            db.commit()

        except Exception as e:
            log.error("job_failed", job_id=job_id, error=str(e))
            handle_job_failure(job, e, db)
