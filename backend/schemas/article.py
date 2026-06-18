"""Pydantic schemas for data validation."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ArticleBase(BaseModel):
    """Base article schema."""

    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)


class ArticleUpdate(BaseModel):
    """Schema for updating an article."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    content: Optional[str] = Field(None, min_length=1)


class Article(ArticleBase):
    """Schema for article response."""

    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

from pydantic import HttpUrl

class SourceCreate(BaseModel):
    name: str
    base_url: str
    feed_type: str
    credibility_score: float

class ArticleCreate(BaseModel):
    title: str
    url: str
    author: Optional[str] = None
    body_text: str
    published_at: datetime