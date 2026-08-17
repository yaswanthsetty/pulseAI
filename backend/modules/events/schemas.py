"""Pydantic schemas for the events module (Phase 3, spec §20)."""

import uuid
from datetime import datetime

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
