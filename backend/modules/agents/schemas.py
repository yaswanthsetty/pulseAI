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
    message: str
    conversation_id: uuid.UUID
    evidence: list[EvidenceItem] = Field(default_factory=list)


class ReportRequest(BaseModel):
    topic: str
    timeframe: str | None = None


class ReportResponse(BaseModel):
    id: uuid.UUID
    topic: str
    status: str
    created_at: datetime
