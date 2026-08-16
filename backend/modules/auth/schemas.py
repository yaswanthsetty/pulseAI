"""Pydantic schemas for auth, users, and API keys (spec §20-22)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

ApiKeyScope = Literal["read", "chat", "reports"]


class RegisterRequest(BaseModel):
    """Local registration (auth_provider=none). Creates a ``user``-role account."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str | None
    role: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    """Login/refresh/session response. The refresh token travels only as an
    httpOnly cookie (§21) — it is deliberately absent from this body."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead


class ApiKeyCreate(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    scopes: list[ApiKeyScope] = Field(default_factory=lambda: ["read"])


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str | None
    scopes: list[str]
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyRead):
    """Response for key creation — includes the raw key, returned exactly once."""

    key: str


class RoleUpdate(BaseModel):
    role: Literal["user", "analyst", "admin"]
