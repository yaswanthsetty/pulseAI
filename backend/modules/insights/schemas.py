"""Pydantic schemas for the insights module (dashboard, trends, comparison)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ArticlePerDay(BaseModel):
    """One day of ingestion volume."""

    date: str
    count: int


class CategoryCount(BaseModel):
    """Articles per category."""

    category: str
    count: int


class SourceCount(BaseModel):
    """Articles per source (recent window)."""

    source: str
    count: int


class StatsResponse(BaseModel):
    """``GET /api/v1/stats`` — pipeline overview for the dashboard."""

    articles_total: int
    articles_last_24h: int
    events_total: int
    events_open: int
    sources_total: int
    users_total: int
    articles_per_day: list[ArticlePerDay]
    top_categories: list[CategoryCount]
    top_sources: list[SourceCount]


class EventBrief(BaseModel):
    """The event an article belongs to (embedded in article detail)."""

    id: uuid.UUID
    title: str
    status: str


class ArticleDetail(BaseModel):
    """``GET /api/v1/articles/{id}`` — full content + memberships."""

    id: uuid.UUID
    title: str
    author: str | None = None
    description: str | None = None
    content_preview: str | None = None
    content: str | None = None
    url: str
    image_url: str | None = None
    language_code: str | None = None
    category_code: str | None = None
    published_at: datetime
    source: str | None = None
    credibility: float | None = None
    event: EventBrief | None = None
    bookmarked: bool = False


class SourceCoverage(BaseModel):
    """One outlet's coverage inside a cross-source comparison."""

    source: str
    credibility: float | None = None
    article_count: int
    latest: dict[str, Any] | None = None
    top_keywords: list[str] = Field(default_factory=list)


class ComparisonResponse(BaseModel):
    """``GET /api/v1/insights/compare`` — how two outlets cover one topic."""

    query: str
    source_a: SourceCoverage
    source_b: SourceCoverage


class MomentumEntry(BaseModel):
    """One event's momentum (trend detection)."""

    event_id: uuid.UUID
    title: str
    summary: str | None = None
    direction: str
    momentum: float
    recent_24h: int
    previous_24h: int
    total_articles: int
    article_count: int
    confidence: float


class TrendingResponse(BaseModel):
    """``GET /api/v1/insights/trending`` — momentum-ranked open events."""

    items: list[MomentumEntry]
