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


class ChatResponse(BaseModel):
    """Final SSE payload for both fast-path and deep-path chat."""

    message: str
    conversation_id: uuid.UUID
    evidence: list[EvidenceItem] = Field(default_factory=list)
    # FR-22: 0.0–1.0 (fraction of citations with mutual textual support)
    agreement: float | None = None


class ReportRequest(BaseModel):
    topic: str
    timeframe: str | None = None


class ReportResponse(BaseModel):
    id: uuid.UUID
    topic: str
    status: str
    created_at: datetime


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
