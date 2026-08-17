"""Pydantic schemas for the events module (Phase 3, spec §20)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class EventListItem(BaseModel):
    """One event in the paginated ``GET /api/v1/events`` list."""

    id: uuid.UUID
    title: str
    summary: str | None = None
    confidence: float
    status: str
    article_count: int
    created_at: datetime
    last_updated: datetime


class EventTimelineEntry(BaseModel):
    """One article in an event's timeline (ordered by when it was added)."""

    article_id: uuid.UUID
    title: str
    source_id: uuid.UUID
    published_at: datetime | None = None
    similarity_at_match: float | None = None
    added_at: datetime


class EventDetail(EventListItem):
    """Full ``GET /api/v1/events/{id}`` detail incl. timeline + article list."""

    timeline: list[EventTimelineEntry] = Field(default_factory=list)


class EventListResponse(BaseModel):
    """Paginated events response (spec §20)."""

    items: list[EventListItem]
    page: int
    page_size: int
    total: int


class EventDayEntry(BaseModel):
    """One day in the evolving event timeline (``GET /api/v1/events/{id}/timeline``).

    ``headline`` is the day's most representative article title; ``keywords``
    are the distinctive terms that mark what changed that day (extractive — an
    LLM abstractive pass is Phase 5); ``titles`` are the day's article titles
    in publication order.
    """

    date: date
    article_count: int
    headline: str | None = None
    keywords: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)


class EventTimelineResponse(BaseModel):
    """Evolving timeline: the event's articles grouped by day, oldest first."""

    id: uuid.UUID
    title: str
    status: str
    total_articles: int
    first_day: date | None = None
    last_day: date | None = None
    days: list[EventDayEntry] = Field(default_factory=list)
