"""Double-submit CSRF protection (spec §23).

Cookie-authenticated state-changing requests must echo the ``pulseai_csrf``
cookie value in the ``X-CSRF-Token`` header. Requests carrying an
``Authorization`` header (bearer / API-key auth) are immune to CSRF and
skipped, as are the auth entry points that establish the session itself
(login/register/session) — they run before any session exists.

SameSite=Lax already blocks most cross-site cookie sending; the double-submit
token is the mandated defense-in-depth layer on top.
"""

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.core.config import settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/session",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject unsafe cookie-authenticated requests without a matching CSRF token."""

    async def dispatch(self, request: Request, call_next):
        if (
            settings.csrf_enabled
            and request.method in _UNSAFE_METHODS
            and request.url.path not in _EXEMPT_PATHS
            and "authorization" not in request.headers
        ):
            if not request.cookies:
                # No session cookies at all — nothing to protect; let the
                # route answer (e.g. 401 for a cookie-less refresh).
                return await call_next(request)
            cookie = request.cookies.get(settings.csrf_cookie_name)
            header = request.headers.get("X-CSRF-Token")
            if not cookie or not header or cookie != header:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "CSRF_FAILED",
                            "message": "CSRF token missing or invalid",
                            "request_id": request.headers.get("X-Request-ID") or uuid.uuid4().hex,
                        }
                    },
                )
        return await call_next(request)
