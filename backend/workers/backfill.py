"""One-shot embeddings backfill CLI (Phase 2).

Usage::

    uv run pulseai-backfill-embeddings [--recreate]

Finds every processed article that does not yet have a fully-embedded chunk
set and enqueues an ``embed`` job for it; the ``pulseai-worker`` process picks
the jobs up and runs chunking + embedding. Idempotent: re-running only
enqueues articles still missing embedded chunks (including articles whose
chunks were left ``pending``/``failed`` by an earlier attempt).

``--recreate`` deletes the Qdrant collection first — dropping stale/legacy
points such as the integer-keyed payloads from a previous schema generation —
and lets the pipeline recreate it with the current UUID-keyed chunk payloads.
"""

import argparse
import logging

from sqlalchemy import select

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.core.logging import setup_logging
from backend.core.queue import acquire_embedding_reconcile, enqueue_embed_article
from backend.db.models import Article, ArticleChunk
from backend.modules.retrieval import service

logger = logging.getLogger(__name__)


def list_articles_needing_embedding(db) -> list[Article]:
    """Processed articles that have no fully-embedded chunk set."""
    statement = (
        select(Article)
        .where(
            Article.processed_at.is_not(None),
            ~select(ArticleChunk.id)
            .where(
                ArticleChunk.article_id == Article.id,
                ArticleChunk.embedding_status == "embedded",
            )
            .exists(),
        )
        .order_by(Article.published_at)
    )
    return list(db.execute(statement).scalars().all())


def reconcile_embeddings(interval_minutes: int | None = None) -> int:
    """Periodic twin of the one-shot backfill (spec §11 nightly reconciliation).

    Runs at most once per ``interval_minutes`` (Redis TTL marker armed by
    ``acquire_embedding_reconcile``): finds processed articles that still lack
    fully-embedded chunks and enqueues ``embed`` jobs for them. Returns the
    number of jobs enqueued (0 when the interval has not elapsed or nothing
    needs embedding). Called from the scheduler process; idempotent and safe
    to overlap with in-flight jobs (stable job ids + idempotent embed job).
    """
    interval = interval_minutes or settings.embedding_reconcile_interval_minutes
    if not acquire_embedding_reconcile(interval * 60):
        return 0

    db = SessionLocal()
    try:
        articles = list_articles_needing_embedding(db)
        enqueued = 0
        for article in articles:
            try:
                enqueue_embed_article(str(article.id))
                enqueued += 1
            except Exception as exc:  # noqa: BLE001 - one bad enqueue must not abort the run
                logger.error("failed to enqueue embed for article %s: %s", article.id, exc)
        return enqueued
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="pulseai-backfill-embeddings",
        description="Enqueue Phase 2 embed jobs for stored articles.",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="delete the Qdrant collection before enqueueing (drops stale points)",
    )
    args = parser.parse_args(argv)

    client = service.get_qdrant_client()
    if args.recreate:
        client.delete_collection(service.COLLECTION_NAME)
        logger.info("deleted collection %s", service.COLLECTION_NAME)
    service.ensure_collection(client)

    db = SessionLocal()
    try:
        articles = list_articles_needing_embedding(db)
        enqueued = 0
        for article in articles:
            try:
                enqueue_embed_article(str(article.id))
                enqueued += 1
            except Exception as exc:  # noqa: BLE001 - one bad enqueue must not abort the run
                logger.error("failed to enqueue embed for article %s: %s", article.id, exc)
        logger.info("backfill complete: enqueued %d of %d articles", enqueued, len(articles))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
