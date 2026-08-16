"""FastAPI authentication / RBAC dependencies (spec §21-22).

``get_current_user`` accepts three credential forms:

* ``Authorization: Bearer <pulseai access JWT>`` — any mode
* ``Authorization: Bearer <provider JWT>`` — clerk/auth0 modes (verified
  against the provider JWKS and synced into ``users``)
* ``Authorization: Bearer pls_<api key>`` — the developer persona (§5)
* the ``pulseai_access`` httpOnly cookie — browser / server-component flows

``require_role(min_role)`` implements the §22 matrix (user < analyst < admin)
and is applied per-route; ``require_scope`` gates API-key principals on the
``read``/``chat``/``reports`` key scopes.
"""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.db.models import User
from backend.modules.auth import service

bearer_scheme = HTTPBearer(auto_error=False)


def _extract_token(creds: HTTPAuthorizationCredentials | None, request: Request) -> str | None:
    if creds:
        return creds.credentials
    return request.cookies.get(settings.access_cookie_name)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the authenticated principal or raise 401."""
    token = _extract_token(creds, request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user, method, api_key = service.resolve_principal(db, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired credentials")
    request.state.auth_method = method
    request.state.api_key = api_key
    return user


def get_optional_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Like ``get_current_user`` but returns None for guests (guest-accessible routes)."""
    token = _extract_token(creds, request)
    if not token:
        return None
    user, _method, _api_key = service.resolve_principal(db, token)
    return user


def require_role(min_role: str):
    """Dependency factory: the principal must hold *min_role* or higher (§22)."""

    def dependency(user: User = Depends(get_current_user)) -> User:
        if service.ROLE_ORDER.get(user.role, 0) < service.ROLE_ORDER[min_role]:
            raise HTTPException(status_code=403, detail=f"Requires role '{min_role}' or higher")
        return user

    return dependency


def require_scope(scope: str):
    """Dependency factory: API-key principals must carry *scope* (§21).

    JWT-authenticated principals are governed by their role instead, so this
    only constrains ``pls_`` keys (used by chat/reports endpoints in Phase 5).
    """

    def dependency(request: Request, user: User = Depends(get_current_user)) -> User:
        method = getattr(request.state, "auth_method", "jwt")
        if method == "api_key":
            key = getattr(request.state, "api_key", None)
            if key is None or scope not in (key.scopes or []):
                raise HTTPException(status_code=403, detail=f"API key lacks scope '{scope}'")
        return user

    return dependency
