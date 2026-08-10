"""RQ job entrypoints (consumed by the ``pulseai-worker`` process).

Jobs are thin adapters: they open a request-scoped session, delegate to the
service layer, and close the session. The poll job also takes a Redis lock so
a scheduled poll and a manual/admin-triggered poll of the same source never
run concurrently.
"""

import logging

from backend.core.database import SessionLocal
from backend.core.queue import acquire_poll_lock, release_poll_lock
from backend.modules.ingestion.service import poll_source, process_article

logger = logging.getLogger(__name__)


def poll_source_job(source_id: str) -> dict:
    """RQ job: poll one source and ingest new entries."""
    if not acquire_poll_lock(source_id):
        logger.info("poll for source %s skipped: another poll in flight", source_id)
        return {"status": "skipped", "detail": "another poll already in flight"}

    try:
        db = SessionLocal()
        try:
            outcome = poll_source(db, source_id)
            return {
                "status": outcome.status,
                "added": outcome.added,
                "detail": outcome.detail,
            }
        finally:
            db.close()
    finally:
        release_poll_lock(source_id)


def process_article_job(article_id: str) -> dict:
    """RQ job: fetch + classify + store one article body."""
    db = SessionLocal()
    try:
        content_ref = process_article(db, article_id)
        return {"status": "ok", "content_ref": content_ref}
    finally:
        db.close()
