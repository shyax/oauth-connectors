import uuid
from contextvars import ContextVar

import structlog

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_ctx):
    return structlog.get_logger(**initial_ctx)


def new_correlation_id() -> str:
    cid = str(uuid.uuid4())
    _correlation_id.set(cid)
    structlog.contextvars.bind_contextvars(correlation_id=cid)
    return cid


def bind_request_context(tenant_id: str | None = None, **kwargs) -> None:
    ctx = {k: v for k, v in kwargs.items() if v is not None}
    if tenant_id:
        ctx["tenant_id"] = tenant_id
    structlog.contextvars.bind_contextvars(**ctx)


def bind_job_context(job_id: str, provider: str, job_type: str) -> None:
    structlog.contextvars.bind_contextvars(job_id=job_id, provider=provider, job_type=job_type)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
