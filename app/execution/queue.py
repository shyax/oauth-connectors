from rq import Queue

from app.redis_client import redis_conn

default_queue = Queue("default", connection=redis_conn)
retry_queue = Queue("retry", connection=redis_conn)
dead_letter_queue = Queue("dead_letter", connection=redis_conn)
