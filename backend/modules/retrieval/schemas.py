"""Pydantic schemas for the retrieval module (FR-11, FR-12)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """FR-12 filters applied to semantic search (Qdrant payload filter)."""

    date_from: datetime | None = None
    date_to: datetime | None = None
    source_id: uuid.UUID | None = None
    category_code: str | None = None
    country_code: str | None = None
    language_code: str | None = None
    event_id: uuid.UUID | None = None


class SearchQuery(BaseModel):
    """Semantic search request body (spec §20)."""

    query: str = Field(..., min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=100)
    limit: int | None = Field(
        default=None, ge=1, le=100, deprecated=True, description="alias for top_k"
    )
    mode: Literal["semantic", "keyword", "hybrid"] = "semantic"
    intent: Literal["recency", "default", "historical"] | None = Field(
        default=None,
        description="Ranking intent override. Omit to auto-detect from query.",
    )
    filters: SearchFilters | None = None


class SearchResult(BaseModel):
    """A single semantic hit, resolved to its source article."""

    article_id: uuid.UUID
    title: str
    source_id: uuid.UUID
    similarity_score: float
    published_at: datetime | None = None
    chunk_id: uuid.UUID | None = None
