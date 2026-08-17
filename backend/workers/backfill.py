"""One-shot embeddings backfill CLI (Phase 2).

Usage::

    uv run pulseai-backfill-embeddings [--recreate]

Finds every processed article that does not yet have a fully-embedded chunk
set and enqueues an ``embed`` job for it; the ``pulseai-worker`` process picks
the jobs up and runs chunking + embedding. Idempotent: re-running only
enqueues articles still missing embedded chunks (including articles whose
chunks were left ``pending``/``failed`` by an earlier attempt).

``--recreate`` rebuilds everything: it deletes the Qdrant collection (dropping
stale/legacy points) and resets ``article_chunks`` so the corpus is re-chunked
with the current §15 parameters and re-embedded with the current model.
"""

import argparse
import logging

from qdrant_client.http.models import PointIdsList
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


def _scroll_point_ids(client) -> set[str]:
    """All Qdrant point ids in the collection (paginated scroll)."""
    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=service.COLLECTION_NAME,
            limit=1000,
            with_payload=False,
            with_vectors=False,
            offset=offset,
        )
        ids.update(str(point.id) for point in points)
        if not points or offset is None:
            break
    return ids


def _sync_qdrant_points(db, client) -> dict:
    """Spec §11: reconcile Postgres ``article_chunks`` against Qdrant points.

    * Points in Qdrant with no chunk row are orphans (e.g. article deleted,
      legacy ids) and are purged.
    * Chunks marked ``embedded`` whose point vanished from Qdrant are re-marked
      ``failed`` so the enqueue pass re-embeds them.
    * Drift beyond ``reconcile_drift_alert_threshold`` is logged as an alert.

    Returns ``{"orphans": n, "missing": n}``.
    """
    chunks = list(db.execute(select(ArticleChunk)).scalars())
    known_ids = {str(chunk.id) for chunk in chunks}
    qdrant_ids = _scroll_point_ids(client)

    orphans = sorted(qdrant_ids - known_ids)
    missing: list[str] = []
    for chunk in chunks:
        if chunk.embedding_status == "embedded" and str(chunk.id) not in qdrant_ids:
            chunk.embedding_status = "failed"
            chunk.qdrant_point_id = None
            missing.append(str(chunk.id))

    if orphans:
        client.delete(
            collection_name=service.COLLECTION_NAME, point_selector=PointIdsList(points=orphans)
        )
    db.commit()

    drift = len(orphans) + len(missing)
    if drift > settings.reconcile_drift_alert_threshold:
        logger.error(
            "embedding drift alert: %d orphan points, %d missing chunks (sync bug?)",
            len(orphans),
            len(missing),
        )
    elif drift:
        logger.warning(
            "embedding drift: purged %d orphan points, re-marked %d missing chunks",
            len(orphans),
            len(missing),
        )
    return {"orphans": len(orphans), "missing": len(missing)}


def reconcile_embeddings(
    interval_minutes: int | None = None,
    *,
    client=None,
    db=None,
) -> dict:
    """Periodic twin of the one-shot backfill (spec §11 reconciliation).

    Runs at most once per ``interval_minutes`` (Redis TTL marker armed by
    ``acquire_embedding_reconcile``): finds processed articles that still lack
    fully-embedded chunks and enqueues ``embed`` jobs for them, then syncs the
    ``article_chunks``/Qdrant point sets in both directions (orphan purge +
    missing re-mark). Returns ``{"enqueued", "orphans_purged",
    "missing_remarked"}``. Called from the scheduler process; idempotent and
    safe to overlap with in-flight jobs (stable job ids + idempotent embed
    job). ``client``/``db`` are injectable for tests.
    """
    interval = interval_minutes or settings.embedding_reconcile_interval_minutes
    if not acquire_embedding_reconcile(interval * 60):
        return {"enqueued": 0, "orphans_purged": 0, "missing_remarked": 0}

    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        qdrant = client or service.get_qdrant_client()
        service.ensure_collection(qdrant)

        enqueued = 0
        for article in list_articles_needing_embedding(db):
            try:
                enqueue_embed_article(str(article.id))
                enqueued += 1
            except Exception as exc:  # noqa: BLE001 - one bad enqueue must not abort the run
                logger.error("failed to enqueue embed for article %s: %s", article.id, exc)

        sync = _sync_qdrant_points(db, qdrant)
        return {
            "enqueued": enqueued,
            "orphans_purged": sync["orphans"],
            "missing_remarked": sync["missing"],
        }
    finally:
        if owns_db:
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
        exists = any(
            c.name == service.COLLECTION_NAME for c in client.get_collections().collections
        )
        if exists:
            client.delete_collection(service.COLLECTION_NAME)
            logger.info("deleted collection %s", service.COLLECTION_NAME)
        else:
            logger.info("collection %s already absent; skipping delete", service.COLLECTION_NAME)
        db = SessionLocal()
        try:
            reset = db.execute(ArticleChunk.__table__.delete())
            db.commit()
            logger.info("reset %d article_chunks rows", reset.rowcount)
        finally:
            db.close()
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
