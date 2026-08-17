"""Source-poll scheduler process (FR-1).

A separate, lightweight process that every ``scheduler_tick_seconds`` finds
active sources whose per-source poll interval has elapsed and enqueues their
poll jobs. Decoupled from the API and the worker so ingestion scheduling
keeps running independently of web traffic.

Run with: ``uv run pulseai-scheduler`` (or ``python -m backend.workers.scheduler``).
"""

import logging
import multiprocessing
import time
from datetime import UTC, datetime

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.core.logging import setup_logging
from backend.core.queue import enqueue_poll, has_pending_retry
from backend.modules.ingestion.service import list_backoff_sources, list_due_sources
from backend.workers.backfill import reconcile_embeddings

logger = logging.getLogger(__name__)


def scheduler_tick() -> int:
    """Enqueue polls for due sources (scheduled + FR-3 backoff retries)."""
    db = SessionLocal()
    try:
        enqueued = 0
        now = datetime.now(UTC)

        due = list_due_sources(db, now)
        for source in due:
            job_id = enqueue_poll(str(source.id))
            logger.info(
                "scheduled poll for '%s' (interval=%dmin, job=%s)",
                source.name,
                source.poll_interval_minutes,
                job_id,
            )
            enqueued += 1

        # FR-3: sources inside a backoff window whose Redis retry marker has
        # expired are due for their retry attempt.
        backoff = list_backoff_sources(db)
        for source in backoff:
            if not has_pending_retry(str(source.id)):
                job_id = enqueue_poll(str(source.id))
                logger.info(
                    "enqueued backoff retry for '%s' (failure %d, job=%s)",
                    source.name,
                    source.consecutive_failures,
                    job_id,
                )
                enqueued += 1

        return enqueued
    finally:
        db.close()


def main() -> None:
    multiprocessing.freeze_support()  # Windows console-script safety
    setup_logging()
    logger.info(
        "scheduler starting (tick=%ds, min_interval=%dmin)",
        settings.scheduler_tick_seconds,
        settings.min_poll_interval_minutes,
    )
    while True:
        try:
            count = scheduler_tick()
            if count:
                logger.info("tick enqueued %d source poll(s)", count)

            # Phase 2 (spec §11): re-enqueue embed jobs for articles still
            # missing embedded chunks and sync Postgres/Qdrant point sets,
            # at most once per reconcile interval.
            result = reconcile_embeddings()
            if any(result.values()):
                logger.info(
                    "reconcile: enqueued %d, purged %d orphan points, re-marked %d missing chunks",
                    result["enqueued"],
                    result["orphans_purged"],
                    result["missing_remarked"],
                )
        except Exception:  # noqa: BLE001 - keep the scheduler alive across transient failures
            logger.exception("scheduler tick failed")
        time.sleep(settings.scheduler_tick_seconds)


if __name__ == "__main__":
    main()
