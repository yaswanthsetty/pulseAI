"""Events API (Phase 3, spec §20).

``GET /api/v1/events`` — paginated list with date/category/min-confidence
filters; ``GET /api/v1/events/{id}`` — event detail including the article
timeline. Open (browse) endpoints; the API-level rate limiter applies.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.db.models import Article, Event, EventArticle, Source
from backend.modules.events.schemas import (
    EventDetail,
    EventListItem,
    EventListResponse,
    EventTimelineEntry,
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
