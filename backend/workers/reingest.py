"""Phase 1.2 re-extraction CLI: re-extract, re-chunk, re-embed, swap safely.

Usage::

    uv run pulseai-reingest --dry-run            # print per-article diffs, write nothing
    uv run pulseai-reingest --limit 5            # reprocess the first 5 articles
    uv run pulseai-reingest                      # full corpus

Safety contract (see TASK 1.2):

* **embed-then-swap**: new chunks are embedded and upserted *before* any old
  point is deleted, so an article never has zero vectors mid-way; an embed
  failure rolls back the transaction and keeps the old rows and points.
* **idempotent**: an article whose content is unchanged and whose chunks are
  all clean is skipped with no writes.
* **fetch failures keep old text**: only the chunk filter is applied to the
  stored content, and the outcome is reported as ``fetch_failed``.
* ``--dry-run`` performs no writes and no counter increments.
"""

import argparse
import logging
from dataclasses import dataclass

from qdrant_client.http.models import FieldCondition, Filter, MatchValue, PointIdsList
from qdrant_client.models import PointStruct
from sqlalchemy import select

from backend.core import counters
from backend.core.config import settings
from backend.core.content_quality import (
    EXTRACTOR_VERSION,
    MIN_BODY_CHARS,
    body_is_usable,
    count_rejected,
    is_boilerplate,
)
from backend.core.database import SessionLocal
from backend.core.storage import get_storage
from backend.db.models import Article, ArticleChunk, Source
from backend.modules.ingestion.fetcher import FetchError, UnsafeUrlError, fetch_url
from backend.modules.ingestion.parser import extract_article
from backend.modules.retrieval import service as retrieval
from backend.modules.retrieval.chunker import chunk_text, estimate_tokens

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    article_id: str
    status: str  # updated | unchanged | fetch_failed | dry_run | skipped | failed
    detail: str = ""


def _stored_content(article: Article) -> str:
    if article.content_ref:
        try:
            return get_storage().get(article.content_ref).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - missing object must not kill the run
            logger.warning("article %s body unavailable: %s", article.id, exc)
    return article.content_preview or article.description or ""


def _plan_content(article: Article) -> tuple[str, str, str, bool]:
    """Return (content, extractor, quality, fetch_failed)."""
    try:
        html = fetch_url(article.url, timeout=settings.article_fetch_timeout_seconds)
    except (FetchError, UnsafeUrlError) as exc:
        logger.warning("article %s: re-fetch failed (%s); keeping stored text", article.id, exc)
        return (
            _stored_content(article),
            article.extractor or "fallback",
            article.extraction_quality,
            True,
        )

    extraction = extract_article(html)
    content, extractor = extraction.text, extraction.extractor
    if not body_is_usable(content or ""):
        # Bulk re-fetching can hit throttled page variants (short JS shells
        # served instead of the article). Re-ingest must never trade usable
        # stored body text for a feed summary - keep the stored content and
        # report it, so a flaky fetch cannot shrink an article.
        stored = _stored_content(article)
        # Size-only check: stored content from before Phase 1.2 legitimately
        # scores as boilerplate (that's what we're cleaning); the chunk
        # filter will strip its junk on re-chunk. Never trade a full stored
        # body for a feed summary.
        if len((stored or "").strip()) >= MIN_BODY_CHARS:
            logger.warning(
                "article %s: extraction unusable (%d chars) but stored body "
                "is usable; keeping stored content",
                article.id,
                len(content or ""),
            )
            return stored, article.extractor or "fallback", article.extraction_quality, False
        if article.description:
            content, extractor = article.description, "summary"
        return content or "", extractor, "low", False
    return content, extractor, "ok", False


def _delete_stale_points(qdrant, article_id, keep_ids: set[str], fallback_ids: list[str]) -> int:
    """Delete every Qdrant point of this article except ``keep_ids``."""
    try:
        points, _ = qdrant.scroll(
            collection_name=retrieval.COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="article_id", match=MatchValue(value=str(article_id)))]
            ),
            limit=10_000,
            with_payload=False,
        )
        stale = [p.id for p in points if str(p.id) not in keep_ids]
    except Exception as exc:  # noqa: BLE001 - fall back to known-old ids
        logger.warning("scroll failed for article %s, using known ids: %s", article_id, exc)
        stale = [str(i) for i in fallback_ids if str(i) not in keep_ids]
    if stale:
        qdrant.delete(
            collection_name=retrieval.COLLECTION_NAME, points_selector=PointIdsList(points=stale)
        )
    return len(stale)


def reingest_article(
    db,
    article: Article,
    *,
    embedder=None,
    qdrant=None,
    dry_run: bool = False,
) -> Outcome:
    """Re-extract + re-chunk + re-embed one article, swapping only on success."""
    old_content = _stored_content(article)
    old_chunks = list(
        db.execute(
            select(ArticleChunk)
            .where(ArticleChunk.article_id == article.id)
            .order_by(ArticleChunk.chunk_number)
        ).scalars()
    )
    if article.processed_at is None:
        return Outcome(str(article.id), "skipped", "never processed (process_article first)")

    content, extractor, quality, fetch_failed = _plan_content(article)
    text = f"{article.title}\n\n{content}".strip()
    pieces = chunk_text(
        text,
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        single_chunk_max_tokens=settings.single_chunk_max_tokens,
    )
    kept, rejected = count_rejected(pieces)
    content_changed = content.strip() != old_content.strip()
    unchanged = not content_changed and rejected == 0 and len(kept) == len(old_chunks)

    if unchanged:
        status = "fetch_failed" if fetch_failed else "unchanged"
        detail = (
            "no changes" if not fetch_failed else "re-fetch failed; stored chunks already clean"
        )
        return Outcome(str(article.id), status, detail)

    if not kept:
        old_junk_only = bool(old_chunks) and all(is_boilerplate(c.chunk_text) for c in old_chunks)
        if not old_junk_only:
            return Outcome(
                str(article.id),
                "failed",
                "new content has no embeddable chunks; old vectors kept",
            )
        # Every old chunk is classifier-confirmed junk AND the re-fetch
        # found no prose: body-less listing/video pages would keep serving
        # hand-verified junk vectors forever. Converge to the summary/low
        # state a fresh ingest of this URL produces instead.
        prune_plan = (
            f"chars {len(old_content)} -> {len(content)}, extractor={extractor}, "
            f"quality={quality}, chunks {len(old_chunks)} -> 0, rejected={rejected}"
        )
        if dry_run:
            return Outcome(
                str(article.id), "dry_run", f"{prune_plan}; would prune junk-only vectors"
            )
        fallback_ids = [str(c.qdrant_point_id or c.id) for c in old_chunks]
        for chunk in old_chunks:
            db.delete(chunk)
        db.flush()
        client = qdrant or retrieval.get_qdrant_client()
        deleted = _delete_stale_points(client, article.id, set(), fallback_ids)
        summary_text = article.description or ""
        if summary_text:
            key = f"articles/{article.id}.txt"
            get_storage().put(key, summary_text.encode("utf-8"))
            article.content_ref = key
            article.content_preview = summary_text[: settings.content_preview_chars]
        article.extraction_quality = "low"
        article.extractor = "summary"
        article.extractor_version = EXTRACTOR_VERSION
        db.commit()
        counters.incr("pulseai_chunks_rejected", rejected, reason="boilerplate")
        return Outcome(
            str(article.id),
            "pruned",
            f"{prune_plan}; junk-only vectors pruned (deleted={deleted})",
        )

    plan = (
        f"chars {len(old_content)} -> {len(content)}, extractor={extractor}, quality={quality}, "
        f"chunks {len(old_chunks)} -> {len(kept)}, rejected={rejected}"
    )
    if dry_run:
        return Outcome(str(article.id), "dry_run", plan)

    # --- embed-then-swap -------------------------------------------------
    # Free (article_id, chunk_number) inside the transaction so the new rows
    # can take the same numbers. A failed embed rolls back (old rows restored)
    # and the old Qdrant points are never touched before the new upsert lands.
    fallback_ids = [str(c.qdrant_point_id or c.id) for c in old_chunks]
    for chunk in old_chunks:
        db.delete(chunk)
    db.flush()

    new_chunks = [
        ArticleChunk(
            article_id=article.id,
            chunk_number=i,
            chunk_text=piece,
            token_count=estimate_tokens(piece),
        )
        for i, piece in enumerate(kept)
    ]
    db.add_all(new_chunks)
    db.flush()  # assign ids (used as Qdrant point ids)

    try:
        model = embedder or retrieval.get_embedder()
        client = qdrant or retrieval.get_qdrant_client()
        retrieval.ensure_collection(client)
        dense, sparse = retrieval._encode_batch(model, [c.chunk_text for c in new_chunks])
        source = db.get(Source, article.source_id)
        points = [
            PointStruct(
                id=str(chunk.id),
                vector={
                    retrieval.DENSE_VECTOR_NAME: dense[i],
                    retrieval.SPARSE_VECTOR_NAME: retrieval._sparse_vector(sparse[i]),
                },
                payload=retrieval._chunk_payload(article, source, chunk),
            )
            for i, chunk in enumerate(new_chunks)
        ]
        client.upsert(collection_name=retrieval.COLLECTION_NAME, points=points)
    except Exception as exc:  # noqa: BLE001 - any failure must keep the old version
        db.rollback()
        logger.warning("article %s embed failed, old version kept: %s", article.id, exc)
        return Outcome(str(article.id), "failed", f"embed/upsert failed, old kept: {exc}")

    # Upsert succeeded - now the swap (old points never coexist badly: new are in).
    keep_ids = {str(c.id) for c in new_chunks}
    deleted = _delete_stale_points(client, article.id, keep_ids, fallback_ids)
    for chunk in new_chunks:
        chunk.embedding_status = "embedded"
        chunk.qdrant_point_id = chunk.id

    if content_changed:
        key = f"articles/{article.id}.txt"
        get_storage().put(key, content.encode("utf-8"))
        article.content_ref = key
        article.content_preview = content[: settings.content_preview_chars]
    article.extraction_quality = quality
    article.extractor = extractor
    article.extractor_version = EXTRACTOR_VERSION
    db.commit()

    if rejected:
        counters.incr("pulseai_chunks_rejected", rejected, reason="boilerplate")

    status = "fetch_failed" if fetch_failed else "updated"
    detail = f"{plan}; stale points deleted={deleted}"
    return Outcome(str(article.id), status, detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 1.2 clean re-extraction")
    parser.add_argument("--dry-run", action="store_true", help="print per-article diffs, no writes")
    parser.add_argument("--limit", type=int, default=None, help="process at most N articles")
    args = parser.parse_args(argv)

    embedder = None if args.dry_run else retrieval.get_embedder()
    qdrant = retrieval.get_qdrant_client()

    counts: dict[str, int] = {}
    with SessionLocal() as db:
        articles = list(
            db.execute(select(Article).order_by(Article.created_at, Article.id)).scalars()
        )
        for article in articles:
            # One article's failure must not abort the run: the embed-then-
            # swap transaction rolls back, and a re-run picks up the rest.
            try:
                outcome = reingest_article(
                    db, article, embedder=embedder, qdrant=qdrant, dry_run=args.dry_run
                )
            except Exception as exc:  # noqa: BLE001 - report and continue
                logger.exception("article %s: re-ingest crashed", article.id)
                db.rollback()
                outcome = Outcome(str(article.id), "failed", f"{type(exc).__name__}: {exc}")
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
            print(f"{outcome.status:14s} {outcome.article_id}  {outcome.detail}")
            if args.limit is not None and sum(counts.values()) >= args.limit:
                break

    print("totals:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
