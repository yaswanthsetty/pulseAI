"""Pydantic schemas for the library module (saved searches, bookmarks, rules)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SavedSearchCreate(BaseModel):
    """``POST /library/searches`` payload."""

    query: str = Field(min_length=1, max_length=400)
    filters: dict[str, Any] | None = None


class SavedSearchOut(BaseModel):
    """One saved search."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query: str
    filters: dict[str, Any] | None = None
    created_at: datetime


class SavedSearchListResponse(BaseModel):
    """``GET /library/searches`` response."""

    items: list[SavedSearchOut]


class BookmarkOut(BaseModel):
    """One bookmark (joined with its article's display fields)."""

    article_id: uuid.UUID
    title: str
    url: str
    description: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    category: str | None = None
    source: str | None = None
    created_at: datetime


class BookmarkListResponse(BaseModel):
    """``GET /library/bookmarks`` response."""

    items: list[BookmarkOut]


class NotificationRuleCreate(BaseModel):
    """``POST /library/notification-rules`` payload."""

    keyword_or_topic: str | None = Field(default=None, max_length=200)
    category_code: str | None = Field(default=None, max_length=32)
    channel: str = Field(default="in_app", pattern="^(email|in_app|webhook)$")


class NotificationRuleOut(BaseModel):
    """One notification rule."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    keyword_or_topic: str | None
    category_code: str | None
    channel: str
    is_active: bool
    created_at: datetime


class NotificationRuleListResponse(BaseModel):
    """``GET /library/notification-rules`` response."""

    items: list[NotificationRuleOut]


class NotificationDeliveryOut(BaseModel):
    """One delivered (or failed) notification for the in-app inbox."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID | None
    channel: str
    status: str
    detail: str | None
    created_at: datetime


class NotificationDeliveryListResponse(BaseModel):
    """``GET /library/notifications`` response."""

    items: list[NotificationDeliveryOut]
    unread: int
