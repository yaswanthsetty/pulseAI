"""Ingestion service layer (FR-1..FR-7).

Pure orchestration over the data model: poll a source, dedupe entries, store
articles, fetch + process article bodies, and handle fetch failures with the
FR-3 backoff schedule. RQ jobs and the API router call into this layer; it
never knows about HTTP routing.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core import queue
from backend.core.audit import write_audit
from backend.core.config import settings
from backend.core.storage import get_storage
from backend.db.models import Article, Source
from backend.modules.ingestion.classifier import (
    classify_category,
    detect_language,
    is_known_language,
)
from backend.modules.ingestion.dedupe import find_fuzzy_duplicate, normalize_url, url_hash
from backend.modules.ingestion.fetcher import FetchError, UnsafeUrlError, fetch_url
from backend.modules.ingestion.parser import (
    FeedEntry,
    extract_main_content,
    parse_feed,
    validate_feed,
)

logger = logging.getLogger(__name__)


class SourceNotFoundError(Exception):
    """Raised when a source id does not exist."""


class FeedValidationError(Exception):
    """Raised when a candidate feed fails FR-4 well-formedness validation."""


@dataclass
class FeedValidationResult:
    valid: bool
    title: str = ""
    feed_type: str = ""
    entry_count: int = 0
    error: str = ""


@dataclass
class PollOutcome:
    """Result of one source-poll attempt."""

    status: str  # ok | retry_scheduled | degraded | skipped | not_found
    added: int = 0
    detail: str = ""
    retry_delay_minutes: int | None = None


# ---------------------------------------------------------------------------
# Feed validation (FR-4)
# ---------------------------------------------------------------------------


def validate_feed_url(url: str) -> FeedValidationResult:
    """Fetch and validate a candidate feed URL (FR-4, before activation)."""
    try:
        content = fetch_url(url, timeout=settings.feed_fetch_timeout_seconds)
    except (FetchError, UnsafeUrlError) as exc:
        return FeedValidationResult(valid=False, error=str(exc))
    result = validate_feed(content)
    return FeedValidationResult(
        valid=result.valid,
        title=result.title,
        feed_type=result.feed_type,
        entry_count=result.entry_count,
        error=result.error,
    )


# ---------------------------------------------------------------------------
# Source CRUD (used by the admin API)
# ---------------------------------------------------------------------------


def create_source(
    db: Session,
    *,
    name: str,
    rss_url: str,
    website: str | None = None,
    credibility_score: float = 0.5,
    credibility_method: str = "manual",
    category_code: str | None = None,
    poll_interval_minutes: int | None = None,
) -> Source:
    """Create an active source after validating its feed (FR-4)."""
    interval = poll_interval_minutes or settings.default_poll_interval_minutes
    if interval < settings.min_poll_interval_minutes:
        raise FeedValidationError(
            f"poll_interval_minutes must be >= {settings.min_poll_interval_minutes}"
        )

    validation = validate_feed_url(rss_url)
    if not validation.valid:
        write_audit(
            db,
            "feed_validation_failed",
            target_type="source",
            metadata={"rss_url": rss_url, "error": validation.error},
        )
        raise FeedValidationError(validation.error or "feed validation failed")

    source = Source(
        name=name,
        rss_url=rss_url,
        website=website,
        credibility_score=credibility_score,
        credibility_method=credibility_method,
        category_code=category_code,
        status="active",
        poll_interval_minutes=interval,
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    write_audit(
        db,
        "source_added",
        target_type="source",
        target_id=str(source.id),
        metadata={"name": name, "rss_url": rss_url},
    )
    logger.info("source created: %s (%s)", name, source.id)
    return source


def update_source(db: Session, source_id, updates: dict[str, Any]) -> Source:
    """Apply admin edits to a source. Re-validates the feed when the URL changes."""
    source = db.get(Source, source_id)
    if source is None:
        raise SourceNotFoundError(source_id)

    allowed = {
        "name",
        "rss_url",
        "website",
        "credibility_score",
        "credibility_method",
        "category_code",
        "status",
        "poll_interval_minutes",
    }
    changes = {k: v for k, v in updates.items() if k in allowed and v is not None}

    if "rss_url" in changes and changes["rss_url"] != source.rss_url:
        validation = validate_feed_url(changes["rss_url"])
        if not validation.valid:
            raise FeedValidationError(validation.error or "feed validation failed")

    if "poll_interval_minutes" in changes and (
        changes["poll_interval_minutes"] < settings.min_poll_interval_minutes
    ):
        raise FeedValidationError(
            f"poll_interval_minutes must be >= {settings.min_poll_interval_minutes}"
        )

    if changes.get("status") == "active":
        # Re-activation resets the failure counter.
        changes["consecutive_failures"] = 0

    for key, value in changes.items():
        setattr(source, key, value)
    db.commit()
    db.refresh(source)

    write_audit(
        db,
        "source_updated",
        target_type="source",
        target_id=str(source.id),
        metadata={"changes": list(changes.keys())},
    )
    return source


# ---------------------------------------------------------------------------
# Polling (FR-1, FR-3)
# ---------------------------------------------------------------------------


def poll_source(db: Session, source_id) -> PollOutcome:
    """Fetch a source's feed and ingest new entries (called by the poll job)."""
    source = db.get(Source, source_id)
    if source is None:
        return PollOutcome(status="not_found", detail="source not found")
    if source.status == "disabled":
        return PollOutcome(status="skipped", detail="source disabled")
    if not source.rss_url:
        return _handle_fetch_failure(db, source, "no rss_url configured")

    try:
        content = fetch_url(source.rss_url, timeout=settings.feed_fetch_timeout_seconds)
    except (FetchError, UnsafeUrlError) as exc:
        return _handle_fetch_failure(db, source, str(exc))

    # A successful fetch clears the failure state.
    source.consecutive_failures = 0
    if source.status == "degraded":
        source.status = "active"
    source.last_polled_at = datetime.now(UTC)
    queue.clear_retry(str(source.id))
    db.commit()

    entries = parse_feed(content)
    added = ingest_entries(db, source, entries)
    logger.info("polled %s: %d new articles", source.name, added)
    return PollOutcome(status="ok", added=added)


def _handle_fetch_failure(db: Session, source: Source, error: str) -> PollOutcome:
    """FR-3: exponential backoff retries (1/5/30 min), then mark ``degraded``."""
    source.consecutive_failures += 1
    failures = source.consecutive_failures
    db.commit()

    backoff = settings.retry_backoff_minutes
    if failures <= len(backoff):
        delay = backoff[failures - 1]
        queue.schedule_retry(str(source.id), delay)
        logger.warning(
            "source %s fetch failed (attempt %d/%d); retry in %d min: %s",
            source.name,
            failures,
            len(backoff),
            delay,
            error,
        )
        return PollOutcome(
            status="retry_scheduled", detail=f"retry in {delay} min", retry_delay_minutes=delay
        )

    source.status = "degraded"
    db.commit()
    write_audit(
        db,
        "source_degraded",
        target_type="source",
        target_id=str(source.id),
        metadata={"consecutive_failures": failures, "error": error},
    )
    logger.error(
        "source %s marked degraded after %d consecutive failures: %s",
        source.name,
        failures,
        error,
    )
    return PollOutcome(status="degraded", detail="source marked degraded")


# ---------------------------------------------------------------------------
# Entry ingestion & article processing (FR-2, FR-5, FR-6, FR-7)
# ---------------------------------------------------------------------------


def ingest_entries(db: Session, source: Source, entries: list[FeedEntry]) -> int:
    """Dedupe and persist feed entries, enqueueing processing for new articles."""
    added = 0
    pending_ids: list[str] = []

    for entry in entries:
        normalized = normalize_url(entry.url)
        if not normalized:
            continue

        digest = url_hash(normalized)
        exists = db.execute(select(Article.id).where(Article.url_hash == digest)).first()
        if exists:
            continue  # exact URL duplicate (FR-2, fast path)

        if find_fuzzy_duplicate(db, source.id, entry.title, entry.published_at):
            continue  # fuzzy title duplicate (FR-2, slow path)

        article = Article(
            source_id=source.id,
            title=entry.title,
            author=entry.author,
            description=entry.summary or None,
            url=normalized,
            url_hash=digest,
            image_url=entry.image_url,
            language_code=entry.language_hint if is_known_language(entry.language_hint) else None,
            published_at=entry.published_at,
            content_preview=(
                entry.summary[: settings.content_preview_chars] if entry.summary else None
            ),
        )
        db.add(article)
        db.flush()
        pending_ids.append(str(article.id))
        added += 1

    db.commit()

    # Enqueue processing only after the rows are committed (no lost-update races).
    for article_id in pending_ids:
        queue.enqueue_process_article(article_id)

    return added


def process_article(db: Session, article_id) -> str:
    """Fetch the article body, extract metadata, and persist to object storage.

    Returns the ``content_ref`` key ("" when there was nothing to store).
    Idempotent: re-running on an already-processed article is a no-op.
    """
    article = db.get(Article, article_id)
    if article is None:
        logger.warning("process_article: article %s not found", article_id)
        return ""
    if article.processed_at is not None:
        return article.content_ref or ""

    content: str | None = None
    try:
        html = fetch_url(article.url, timeout=settings.article_fetch_timeout_seconds)
        content = extract_main_content(html)
    except (FetchError, UnsafeUrlError) as exc:
        logger.warning("article %s body fetch failed; using description: %s", article_id, exc)

    if not content or len(content) < len(article.description or ""):
        content = article.description or content or ""

    content = content[: settings.max_article_storage_chars]

    if content:
        key = f"articles/{article.id}.txt"
        get_storage().put(key, content.encode("utf-8"))
        article.content_ref = key
        article.content_preview = content[: settings.content_preview_chars]

    if not article.language_code:
        detected = detect_language(content[:1000])
        if is_known_language(detected):
            article.language_code = detected

    if not article.category_code:
        article.category_code = classify_category(article.title, content)

    article.processed_at = datetime.now(UTC)
    db.commit()
    return article.content_ref or ""


# ---------------------------------------------------------------------------
# Scheduler support (FR-1: per-source configurable intervals)
# ---------------------------------------------------------------------------


def list_due_sources(db: Session, now: datetime | None = None) -> list[Source]:
    """Active, healthy sources whose poll interval has elapsed (or never polled)."""
    now = now or datetime.now(UTC)
    statement = select(Source).where(
        Source.status == "active",
        Source.consecutive_failures == 0,
        Source.last_polled_at.is_(None)
        | (
            Source.last_polled_at
            <= now - func.make_interval(0, 0, 0, 0, 0, Source.poll_interval_minutes)
        ),
    )
    return list(db.execute(statement).scalars().all())


def list_backoff_sources(db: Session) -> list[Source]:
    """Active sources currently inside an FR-3 backoff window.

    The scheduler checks each one's Redis retry marker: once the marker has
    expired (the backoff delay elapsed), a retry poll is enqueued.
    """
    max_retries = len(settings.retry_backoff_minutes)
    statement = select(Source).where(
        Source.status == "active",
        Source.consecutive_failures >= 1,
        Source.consecutive_failures <= max_retries,
    )
    return list(db.execute(statement).scalars().all())
