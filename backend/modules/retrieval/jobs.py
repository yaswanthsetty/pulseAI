"""RQ job entrypoints for the retrieval module (consumed by ``pulseai-worker``).

The embed job is a thin adapter: open a request-scoped session, delegate to
the service layer, close the session. ``EmbeddingError`` propagates so RQ's
retry policy re-enqueues the job with backoff; the affected chunks stay
marked ``failed`` in Postgres for the next reconcile/backfill pass.
"""

import logging

from backend.core.database import SessionLocal
from backend.modules.retrieval.service import embed_article

logger = logging.getLogger(__name__)


def embed_article_job(article_id: str) -> dict:
    """RQ job: chunk + embed one article and upsert vectors into Qdrant."""
    db = SessionLocal()
    try:
        outcome = embed_article(db, article_id)
        return {
            "status": outcome.status,
            "chunks": outcome.chunk_count,
            "detail": outcome.detail,
        }
    finally:
        db.close()
