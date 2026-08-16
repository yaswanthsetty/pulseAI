"""Auth, user, and API-key endpoints (spec §20, §21, §22)."""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.pagination import Page, paginate
from backend.db.models import User
from backend.modules.auth import security, service
from backend.modules.auth.deps import bearer_scheme, get_current_user, require_role
from backend.modules.auth.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    LoginRequest,
    RegisterRequest,
    RoleUpdate,
    TokenResponse,
    UserRead,
)

router = APIRouter(tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set the httpOnly access+refresh cookies and the CSRF cookie (§21/§23).

    The access token also travels in the body for API clients; the cookie
    covers browser / Next.js server-component flows (CSRF-protected).
    """
    response.set_cookie(
        settings.access_cookie_name,
        access_token,
        max_age=settings.jwt_access_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=settings.refresh_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        secrets.token_urlsafe(32),
        max_age=settings.refresh_ttl_days * 86400,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    for name in (
        settings.access_cookie_name,
        settings.refresh_cookie_name,
        settings.csrf_cookie_name,
    ):
        response.delete_cookie(name, path="/")


def _token_response(tokens: service.IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=UserRead.model_validate(tokens.user),
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.post("/auth/register", response_model=UserRead, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create a local account with role ``user`` (auth_provider=none only)."""
    try:
        return service.register_user(
            db, email=payload.email, password=payload.password, display_name=payload.display_name
        )
    except service.UserExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auth/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    """Exchange credentials for an access token + rotating refresh cookie."""
    try:
        user = service.authenticate_user(
            db, email=payload.email, password=payload.password, ip_address=_client_ip(request)
        )
    except service.InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    tokens = service.issue_tokens(db, user, ip_address=_client_ip(request))
    _set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    return _token_response(tokens)


@router.post("/auth/session", response_model=TokenResponse)
def create_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Managed-provider mode: verify the provider JWT, sync the user, mint tokens.

    Clerk/Auth0 frontends exchange their provider token here once per login;
    afterwards the standard access JWT / refresh cookie flow takes over.
    """
    if settings.auth_provider == "none":
        raise HTTPException(status_code=400, detail="No managed identity provider is configured")
    if not creds:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        claims = security.decode_provider_token(creds.credentials)
        user = service.sync_managed_user(db, claims)
    except security.TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    tokens = service.issue_tokens(db, user, ip_address=_client_ip(request))
    _set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    return _token_response(tokens)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """Rotate the refresh token (old one revoked) and issue a fresh access token."""
    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        tokens = service.rotate_refresh_token(db, raw, ip_address=_client_ip(request))
    except service.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    return _token_response(tokens)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revoke the current refresh token and clear the auth cookies."""
    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw:
        service.revoke_refresh_token(db, raw, ip_address=_client_ip(request))
    _clear_auth_cookies(response)


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


@router.get("/users/me", response_model=UserRead)
def get_me(user: User = Depends(get_current_user)):
    """Current user profile + role (spec §20)."""
    return user


# ---------------------------------------------------------------------------
# API keys (settings surface; raw key returned exactly once)
# ---------------------------------------------------------------------------


@router.get("/api-keys", response_model=list[ApiKeyRead])
def list_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.list_api_keys(db, user)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_key(
    payload: ApiKeyCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row, raw = service.create_api_key(db, user, label=payload.label, scopes=payload.scopes)
    return ApiKeyCreated(
        id=row.id,
        label=row.label,
        scopes=row.scopes,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        key=raw,
    )


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_key(
    key_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not service.revoke_api_key(db, user, key_id):
        raise HTTPException(status_code=404, detail="API key not found")


# ---------------------------------------------------------------------------
# Admin: user/role management (§22 — Manage users/roles)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=Page[UserRead])
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    """List users (admin only)."""
    return paginate(
        db,
        select(User).order_by(User.created_at.desc()),
        page,
        page_size,
        to_model=UserRead,
    )


@router.patch("/users/{user_id}/role", response_model=UserRead)
def change_role(
    user_id: uuid.UUID,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """Change a user's role (admin only); audited as ``role_change``."""
    try:
        return service.set_user_role(db, actor, user_id, payload.role)
    except service.UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
