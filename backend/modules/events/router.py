"""Events API (Phase 3, spec §20).

``GET /api/v1/events`` — paginated list with date/category/min-confidence
filters; ``GET /api/v1/events/{id}`` — event detail including the article
timeline; ``GET /api/v1/events/{id}/timeline`` — the same articles grouped by
day, with a per-day headline and distinctive keywords, so the event's
coverage can be read as an evolving story. Open (browse) endpoints; the
API-level rate limiter applies.
"""

import uuid
from collections import Counter, defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.db.models import Article, Event, EventArticle, Source, User
from backend.modules.auth.deps import require_role
from backend.modules.events.schemas import (
    EventDayEntry,
    EventDetail,
    EventListItem,
    EventListResponse,
    EventTimelineEntry,
    EventTimelineResponse,
)

router = APIRouter(tags=["events"])


@router.get("/events", response_model=EventListResponse)
def list_events(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    category_code: str | None = None,
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Paginated event list with optional filters (spec §20)."""
    statement = select(Event)
    if date_from is not None:
        statement = statement.where(Event.last_updated >= date_from)
    if date_to is not None:
        statement = statement.where(Event.last_updated <= date_to)
    if min_confidence is not None:
        statement = statement.where(Event.confidence >= min_confidence)
    if category_code is not None:
        statement = statement.join(EventArticle, EventArticle.event_id == Event.id).join(
            Article, Article.id == EventArticle.article_id
        )
        statement = statement.where(Article.category_code == category_code)
        statement = statement.distinct()

    total = db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one()
    events = (
        db.execute(
            statement.order_by(Event.last_updated.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return EventListResponse(
        items=[_to_list_item(e) for e in events],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/events/{event_id}", response_model=EventDetail)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)):
    """Event detail incl. timeline (articles ordered by when they were added)."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")

    rows = db.execute(
        select(Article, EventArticle, Source)
        .join(EventArticle, EventArticle.article_id == Article.id)
        .outerjoin(Source, Source.id == Article.source_id)
        .where(EventArticle.event_id == event.id)
        .order_by(EventArticle.added_at)
    ).all()
    timeline = [
        EventTimelineEntry(
            article_id=article.id,
            title=article.title,
            source_id=article.source_id,
            published_at=article.published_at,
            similarity_at_match=ea.similarity_at_match,
            added_at=ea.added_at,
        )
        for article, ea, _source in rows
    ]
    return EventDetail(
        id=event.id,
        title=event.title,
        summary=event.summary,
        confidence=event.confidence,
        status=event.status,
        article_count=event.article_count,
        created_at=event.created_at,
        last_updated=event.last_updated,
        timeline=timeline,
    )


@router.get("/events/{event_id}/timeline", response_model=EventTimelineResponse)
def get_event_timeline(event_id: uuid.UUID, db: Session = Depends(get_db)):
    """Evolving timeline: the event's articles grouped by day (oldest first).

    Each day carries its article count, a headline (the day's most
    representative title — the one closest to the event centroid or the
    first published), the distinctive keywords that mark what changed that
    day, and the day's titles in publication order.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")

    rows = db.execute(
        select(Article, EventArticle, Source)
        .join(EventArticle, EventArticle.article_id == Article.id)
        .outerjoin(Source, Source.id == Article.source_id)
        .where(EventArticle.event_id == event.id)
        .order_by(Article.published_at, EventArticle.added_at)
    ).all()

    by_day: dict[object, list] = defaultdict(list)
    for article, ea, _source in rows:
        day = _article_day(article)
        if day is not None:
            by_day[day].append((article, ea))

    days: list[EventDayEntry] = []
    for day in sorted(by_day):
        articles = by_day[day]
        titles = [a.title for a, _ in by_day[day]]
        days.append(
            EventDayEntry(
                date=day,
                article_count=len(articles),
                headline=_day_headline(articles),
                keywords=_day_keywords(articles),
                titles=titles,
            )
        )

    # total_articles derives from the rows actually returned: the stored
    # event.article_count counter can drift (e.g. a failed centroid write, a
    # manual edit), and the response must be internally consistent.
    total = sum(len(by_day[day]) for day in sorted(by_day))
    return EventTimelineResponse(
        id=event.id,
        title=event.title,
        status=event.status,
        total_articles=total,
        first_day=days[0].date if days else None,
        last_day=days[-1].date if days else None,
        days=days,
    )


_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "for",
    "of",
    "on",
    "in",
    "to",
    "at",
    "with",
    "from",
    "by",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "his",
    "her",
    "their",
    "they",
    "he",
    "she",
    "we",
    "you",
    "i",
    "after",
    "before",
    "over",
    "under",
    "about",
    "up",
    "down",
    "out",
    "off",
    "not",
    "no",
    "yes",
    "has",
    "have",
    "had",
    "will",
    "would",
    "could",
    "should",
    "can",
    "may",
    "might",
    "must",
    "does",
    "did",
    "do",
    "than",
    "then",
    "so",
    "if",
    "while",
    "during",
    "against",
    "between",
    "into",
    "through",
    "via",
    "per",
    "new",
    "say",
    "says",
    "said",
    "report",
    "reports",
}


def _article_day(article: Article):
    """The calendar day an article belongs to (published_at, else added_at)."""
    ts = article.published_at or article.created_at
    return ts.date() if ts is not None else None


def _day_headline(articles: list) -> str | None:
    """Most representative title: prefer the one closest to the event centroid
    (highest ``similarity_at_match``), else the first published."""
    scored = [pair for pair in articles if pair[1].similarity_at_match is not None]
    if scored:
        best = max(scored, key=lambda pair: pair[1].similarity_at_match or 0)
        return best[0].title
    return articles[0][0].title if articles else None


def _day_keywords(articles: list, limit: int = 5) -> list[str]:
    """Distinctive terms across the day's titles (extractive summary signal)."""
    counts: Counter[str] = Counter()
    for article, _ea in articles:
        for token in _tokens(article.title):
            counts[token] += 1
    return [word for word, _count in counts.most_common(limit)]


def _tokens(text: str) -> list[str]:
    """Lowercased alphabetic words, minus stopwords and tiny fragments."""
    import re

    return [w for w in re.findall(r"[a-z][a-z'-]{2,}", text.lower()) if w not in _STOPWORDS]


def _to_list_item(event: Event) -> EventListItem:
    return EventListItem(
        id=event.id,
        title=event.title,
        summary=event.summary,
        confidence=event.confidence,
        status=event.status,
        article_count=event.article_count,
        created_at=event.created_at,
        last_updated=event.last_updated,
    )


# ---------------------------------------------------------------------------
# Admin: event merge (Phase 3 completion)
# ---------------------------------------------------------------------------


class MergeEventsRequest(BaseModel):
    source_event_id: uuid.UUID
    target_event_id: uuid.UUID


@router.post("/events/merge")
def merge_events(
    payload: MergeEventsRequest,
    db: Session = Depends(get_db),
    _admin: "User" = Depends(require_role("admin")),
):
    """Merge source event into target: move all articles, close source.

    Admin-only. Both events must exist and be open.
    """

    source = db.get(Event, payload.source_event_id)
    target = db.get(Event, payload.target_event_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="event not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Cannot merge event into itself")

    # Move all articles from source to target
    links = db.execute(
        select(EventArticle).where(EventArticle.event_id == source.id)
    ).scalars().all()
    moved = 0
    for link in links:
        # Check if target already has this article
        exists = db.execute(
            select(EventArticle).where(
                EventArticle.event_id == target.id,
                EventArticle.article_id == link.article_id,
            )
        ).scalar_one_or_none()
        if exists is None:
            link.event_id = target.id
            moved += 1
        else:
            db.delete(link)

    # Update target article count
    target.article_count = db.execute(
        select(func.count()).select_from(
            select(EventArticle).where(EventArticle.event_id == target.id).subquery()
        )
    ).scalar_one()

    # Close source
    source.status = "merged"
    db.commit()

    return {
        "source_event_id": str(source.id),
        "target_event_id": str(target.id),
        "articles_moved": moved,
    }
