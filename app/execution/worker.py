from rq import Worker

from app.execution.queue import dead_letter_queue, default_queue, retry_queue
from app.observability.logging import configure_logging
from app.redis_client import redis_conn

if __name__ == "__main__":
    configure_logging()
    worker = Worker(
        queues=[default_queue, retry_queue, dead_letter_queue],
        connection=redis_conn,
    )
    worker.work(with_scheduler=True)
