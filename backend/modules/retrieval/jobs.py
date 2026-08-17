"""RQ job entrypoints for the retrieval module (consumed by ``pulseai-worker``).

The embed job is a thin adapter: open a request-scoped session, delegate to
the service layer, close the session. ``EmbeddingError`` propagates so RQ's
retry policy re-enqueues the job with backoff; the affected chunks stay
marked ``failed`` in Postgres for the next reconcile/backfill pass.

Phase 3 handoff (FR-18): once an article is successfully embedded, the job
enqueues the fast-path ``cluster`` job through the core queue hub — the same
pattern as ingestion → embed (business modules never import each other).
"""

import logging

from backend.core.database import SessionLocal
from backend.core.queue import enqueue_cluster_article
from backend.modules.retrieval.service import embed_article

logger = logging.getLogger(__name__)


def embed_article_job(article_id: str) -> dict:
    """RQ job: chunk + embed one article and upsert vectors into Qdrant."""
    db = SessionLocal()
    try:
        outcome = embed_article(db, article_id)
        if outcome.status in ("ok", "already_embedded"):
            enqueue_cluster_article(article_id)
        return {
            "status": outcome.status,
            "chunks": outcome.chunk_count,
            "detail": outcome.detail,
        }
    finally:
        db.close()
