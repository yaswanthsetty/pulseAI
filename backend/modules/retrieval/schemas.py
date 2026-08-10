"""Pydantic schemas for the retrieval module (FR-11)."""

import uuid

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """Semantic search request body."""

    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=50)


class SearchResult(BaseModel):
    """A single semantic hit, resolved to its source article."""

    article_id: uuid.UUID
    title: str
    source_id: uuid.UUID
    similarity_score: float
