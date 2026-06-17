"""Pydantic schemas for data validation."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ArticleBase(BaseModel):
    """Base article schema."""

    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)


class ArticleCreate(ArticleBase):
    """Schema for creating an article."""

    pass


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
