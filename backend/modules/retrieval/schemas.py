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


class ChunkExcerpt(BaseModel):
    """An additional matching chunk of the same article (RAG grounding only)."""

    chunk_id: uuid.UUID | None = None
    chunk_index: int | None = None
    chunk_text: str = ""
    score: float = 0.0


class SearchResult(BaseModel):
    """A single semantic hit, resolved to its source article."""

    article_id: uuid.UUID
    title: str
    source_id: uuid.UUID
    similarity_score: float
    published_at: datetime | None = None
    chunk_id: uuid.UUID | None = None
    source_name: str | None = None
    chunk_text: str | None = None
    chunk_index: int | None = None
    # Further chunks of the same article, best first. Populated only when
    # ``search(max_chunks_per_article>1)``; never serialised in API responses.
    extra_chunks: list[ChunkExcerpt] = Field(default_factory=list, exclude=True)
