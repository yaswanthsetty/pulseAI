"""Redis connection and RQ job-queue wiring (spec §12).

Three named queues model the ingestion pipeline stages:

* ``ingest``   — RSS source polling (FR-1/FR-3)
* ``embed``    — chunk embedding (Phase 2)
* ``cluster``  — incremental event clustering (Phase 3)

The ``embed``/``cluster`` queues are declared now so the worker can bind to
them and later phases plug in without changing process topology.
"""

import redis
from rq import Queue, Retry

from backend.core.config import settings

INGEST_QUEUE = "ingest"
EMBED_QUEUE = "embed"
CLUSTER_QUEUE = "cluster"

# Lock TTL: keep a source's poll locked while a (possibly slow) fetch is in flight.
POLL_LOCK_TTL_SECONDS = 15 * 60


def get_redis() -> redis.Redis:
    """Return a Redis client for the configured URL."""
    return redis.Redis.from_url(settings.redis_url, decode_responses=False)


def get_ingest_queue() -> Queue:
    return Queue(INGEST_QUEUE, connection=get_redis())


def get_embed_queue() -> Queue:
    return Queue(EMBED_QUEUE, connection=get_redis())


def get_cluster_queue() -> Queue:
    return Queue(CLUSTER_QUEUE, connection=get_redis())


def acquire_poll_lock(source_id: str) -> bool:
    """Atomically acquire the per-source poll lock; True if this caller won."""
    client = get_redis()
    return bool(client.set(f"poll_lock:{source_id}", "1", nx=True, ex=POLL_LOCK_TTL_SECONDS))


def release_poll_lock(source_id: str) -> None:
    get_redis().delete(f"poll_lock:{source_id}")


def enqueue_poll(source_id: str) -> str:
    """Enqueue an immediate source poll job.

    The stable ``job_id`` de-duplicates: an identical job already queued or
    scheduled is not enqueued twice, which prevents scheduler/poll overlap.
    """
    queue = get_ingest_queue()
    from backend.modules.ingestion.jobs import poll_source_job  # deferred import

    # RQ job ids allow only letters, numbers, underscores, and dashes.
    job = queue.enqueue(
        poll_source_job,
        source_id,
        job_id=f"poll-{source_id}",
        job_timeout=120,
        result_ttl=300,
    )
    return job.id


def schedule_retry(source_id: str, delay_minutes: int) -> None:
    """FR-3: mark a source as awaiting a retry poll in ``delay_minutes``.

    Implemented as a Redis key with a TTL, consumed by the scheduler process
    (which enqueues the retry poll once the key expires). This avoids RQ's
    POSIX-only delayed-job scheduler so the whole flow works on Windows too.
    """
    get_redis().set(f"retry:{source_id}", "1", ex=delay_minutes * 60)


def has_pending_retry(source_id: str) -> bool:
    """True while a source is still inside its backoff window."""
    return bool(get_redis().exists(f"retry:{source_id}"))


def clear_retry(source_id: str) -> None:
    """Clear any pending retry marker (called after a successful poll)."""
    get_redis().delete(f"retry:{source_id}")


def enqueue_process_article(article_id: str) -> str:
    """Enqueue the article-processing job (fetch body, classify, store)."""
    queue = get_ingest_queue()
    from backend.modules.ingestion.jobs import process_article_job  # deferred import

    job = queue.enqueue(
        process_article_job,
        article_id,
        job_id=f"process-{article_id}",
        job_timeout=90,
        result_ttl=300,
    )
    return job.id


def acquire_embedding_reconcile(interval_seconds: int) -> bool:
    """Atomically arm the periodic embedding-reconcile marker (spec §11).

    Returns True for exactly one caller per ``interval_seconds`` window (the
    one that sets the key); every other caller within the window gets False
    and skips its run. Same Redis-TTL pattern as the FR-3 retry markers, so it
    works on Windows too (no RQ delayed-job scheduler needed).
    """
    return bool(get_redis().set("reconcile:embeddings", "1", nx=True, ex=interval_seconds))


def enqueue_embed_article(article_id: str) -> str:
    """Enqueue the Phase 2 embed job (chunk + embed + upsert to Qdrant).

    Uses the stable ``embed-{article_id}`` job id so a job already queued is
    not enqueued twice; the job itself is idempotent (skips embedded chunks).
    """
    queue = get_embed_queue()
    from backend.modules.retrieval.jobs import embed_article_job  # deferred import

    job = queue.enqueue(
        embed_article_job,
        article_id,
        job_id=f"embed-{article_id}",
        job_timeout=600,  # first run may download the model
        result_ttl=300,
        retry=Retry(max=3, interval=[60, 300]),
    )
    return job.id
