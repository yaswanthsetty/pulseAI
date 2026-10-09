"""Pydantic schemas for the agents module (Phase 5)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    conversation_id: uuid.UUID | None = None


class EvidenceItem(BaseModel):
    citation_id: int
    article_id: uuid.UUID
    title: str
    source_id: uuid.UUID | None = None
    published_at: datetime | None = None
    score: float
    source_name: str | None = None
    # First ~240 chars of the cited chunk, cut on a word boundary.
    snippet: str | None = None
    chunk_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    """Final SSE payload for both fast-path and deep-path chat."""

    message: str
    conversation_id: uuid.UUID
    evidence: list[EvidenceItem] = Field(default_factory=list)
    # FR-22: 0.0–1.0 (fraction of citations with mutual textual support)
    agreement: float | None = None
    # [#n] markers the model emitted that matched no context block; already
    # stripped from ``message``.
    invalid_citations: list[int] = Field(default_factory=list)


class ReportRequest(BaseModel):
    topic: str
    timeframe: str | None = None


class ReportSource(BaseModel):
    """One evidence source exposed by the reports API.

    Phase 1 evidence contract: ``chunk_id``/``source_name`` must be available
    for every cited source (they were already stored in ``content.sources``;
    this surfaces them on the response schema too).
    """

    citation_id: int
    article_id: uuid.UUID
    title: str
    score: float
    source_id: uuid.UUID | None = None
    source_name: str | None = None
    published_at: datetime | None = None
    snippet: str | None = None
    chunk_id: uuid.UUID | None = None


class ReportResponse(BaseModel):
    id: uuid.UUID
    topic: str
    status: str
    created_at: datetime
    sources: list[ReportSource] = Field(default_factory=list)


class UsageBreakdown(BaseModel):
    operation: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    avg_latency_ms: float


class UsageResponse(BaseModel):
    user_id: str | None
    scope: str  # 'own' | 'all'
    breakdown: list[UsageBreakdown]
    total_tokens: int


class ConversationSummary(BaseModel):
    """One row in the chat history sidebar."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary] = Field(default_factory=list)


class ConversationMessageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    role: str
    content: str
    # Stored as {"items": [...], "error"?: true} in the JSONB column.
    evidence: dict | None = None
    evidence_agreement: float | None = None
    created_at: datetime


class ConversationDetailResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageOut] = Field(default_factory=list)
