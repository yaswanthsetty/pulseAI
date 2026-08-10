"""Pydantic schemas for the ingestion API surface (sources, articles)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    rss_url: AnyHttpUrl
    website: AnyHttpUrl | None = None
    credibility_score: float = Field(default=0.5, ge=0.0, le=1.0)
    credibility_method: Literal["manual", "external_dataset", "computed"] = "manual"
    category_code: str | None = None
    poll_interval_minutes: int = Field(default=15, ge=5)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    rss_url: AnyHttpUrl | None = None
    website: AnyHttpUrl | None = None
    credibility_score: float | None = Field(default=None, ge=0.0, le=1.0)
    credibility_method: Literal["manual", "external_dataset", "computed"] | None = None
    category_code: str | None = None
    status: Literal["active", "degraded", "disabled"] | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=5)


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    rss_url: str | None
    website: str | None
    credibility_score: float
    credibility_method: str
    category_code: str | None
    status: str
    poll_interval_minutes: int
    last_polled_at: datetime | None
    consecutive_failures: int
    created_at: datetime


class ArticleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    title: str
    author: str | None
    description: str | None
    content_preview: str | None
    url: str
    image_url: str | None
    language_code: str | None
    category_code: str | None
    country_code: str | None
    published_at: datetime
    processed_at: datetime | None
    event_id: uuid.UUID | None
    created_at: datetime
