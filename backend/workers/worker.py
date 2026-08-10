"""RQ worker process (spec §12).

Consumes the ``ingest``, ``embed``, and ``cluster`` queues. ``with_scheduler``
enables RQ's built-in scheduler so delayed jobs (FR-3 backoff retries) fire
without a separate scheduler process.

Run with: ``uv run pulseai-worker`` (or ``python -m backend.workers.worker``).
"""

import logging
import multiprocessing
import os

from rq import Queue, Worker
from rq.worker import SimpleWorker

from backend.core.logging import setup_logging
from backend.core.queue import CLUSTER_QUEUE, EMBED_QUEUE, INGEST_QUEUE, get_redis

logger = logging.getLogger(__name__)

# The default RQ worker forks a child "horse" process per job (os.fork), which
# is POSIX-only. Windows has no fork, so we fall back to SimpleWorker (runs
# each job in-process). SimpleWorker is also handy for very small deployments.
_WORKER_CLASS = SimpleWorker if os.name == "nt" else Worker


def main() -> None:
    multiprocessing.freeze_support()  # console-script safety on Windows
    setup_logging()
    redis = get_redis()
    queues = [
        Queue(INGEST_QUEUE, connection=redis),
        Queue(EMBED_QUEUE, connection=redis),
        Queue(CLUSTER_QUEUE, connection=redis),
    ]
    worker = _WORKER_CLASS(queues, connection=redis, name="pulseai-worker")
    logger.info(
        "worker starting (class=%s); queues=%s",
        _WORKER_CLASS.__name__,
        [q.name for q in queues],
    )
    # with_scheduler is intentionally NOT enabled — RQ's in-process scheduler
    # subprocess is POSIX-only. Delayed/backoff jobs are driven by the separate
    # ``pulseai-scheduler`` process (Redis TTL keys), which works everywhere.
    worker.work()


if __name__ == "__main__":
    main()
