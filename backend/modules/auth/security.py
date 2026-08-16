"""Cryptographic primitives for the auth module (spec §21/§23).

* Passwords — bcrypt-hashed, never stored in plain text.
* Access tokens — signed JWTs, HS256 for the local provider, RS256 verified
  against the managed provider's JWKS (Clerk/Auth0).
* Refresh tokens / API keys — high-entropy random values persisted only as
  SHA-256 digests, so a database leak never exposes usable credentials.
"""

import hashlib
import logging
import secrets
import time
import uuid
from typing import Any

import bcrypt
import httpx
import jwt

from backend.core.config import settings

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TYPE = "access"
API_KEY_PREFIX = "pls_"
VALID_ROLES = ("user", "analyst", "admin")

_JWKS_TTL_SECONDS = 3600
_PROVIDER_ISSUER_TEMPLATE = {
    "clerk": "https://{domain}",
    "auth0": "https://{domain}/",
}

# In-memory, TTL'd JWKS cache: provider -> (fetched_at, [jwk dicts]).
_jwks_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class TokenError(ValueError):
    """Raised when a token fails verification (signature, expiry, type...)."""


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password* (self-contained salt)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time bcrypt comparison; False on malformed stored hashes."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# High-entropy random tokens (stored hashed, per spec §21)
# ---------------------------------------------------------------------------


def hash_token(raw: str) -> str:
    """SHA-256 digest of a raw token — the only form ever persisted."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_refresh_token() -> tuple[str, str]:
    """Return ``(raw_token, hash)``; only the hash is stored in ``refresh_tokens``."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def generate_api_key() -> tuple[str, str]:
    """Return ``(raw_key, hash)``; the raw key is shown to the user exactly once."""
    raw = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return raw, hash_token(raw)


# ---------------------------------------------------------------------------
# Local access tokens (HS256; auth_provider=none and session/refresh in all modes)
# ---------------------------------------------------------------------------


def create_access_token(*, user_id: uuid.UUID, email: str, role: str) -> tuple[str, int]:
    """Mint an access JWT; returns ``(token, expires_in_seconds)`` (§20: 15 min TTL)."""
    now = int(time.time())
    ttl_seconds = settings.jwt_access_ttl_minutes * 60
    claims = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    return token, ttl_seconds


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify a PulseAI access JWT; raises TokenError on any failure."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid access token") from exc
    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise TokenError("Not an access token")
    return claims


# ---------------------------------------------------------------------------
# Managed provider tokens (Clerk / Auth0 — RS256 via JWKS, §21)
# ---------------------------------------------------------------------------


def _provider_domain() -> str | None:
    return {"clerk": settings.clerk_domain, "auth0": settings.auth0_domain}.get(
        settings.auth_provider
    )


def _provider_jwks_url() -> str | None:
    domain = _provider_domain()
    return f"https://{domain}/.well-known/jwks.json" if domain else None


def _provider_issuer() -> str | None:
    domain = _provider_domain()
    template = _PROVIDER_ISSUER_TEMPLATE.get(settings.auth_provider)
    return template.format(domain=domain) if template and domain else None


def _fetch_jwks(url: str) -> list[dict[str, Any]]:
    """Fetch and TTL-cache the provider's JWKS key set."""
    now = time.monotonic()
    cached = _jwks_cache.get(settings.auth_provider)
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    keys = response.json().get("keys", [])
    if not keys:
        raise TokenError("Provider JWKS contained no keys")
    _jwks_cache[settings.auth_provider] = (now, keys)
    return keys


def decode_provider_token(token: str) -> dict[str, Any]:
    """Verify an RS256 token issued by the configured managed provider.

    Tries every key in the (cached) JWKS so key rotation does not break auth.
    """
    jwks_url = _provider_jwks_url()
    issuer = _provider_issuer()
    if settings.auth_provider == "none" or not jwks_url:
        raise TokenError("No managed auth provider is configured")
    audience = settings.auth0_audience if settings.auth_provider == "auth0" else None

    last_error: jwt.PyJWTError | None = None
    for jwk in _fetch_jwks(jwks_url):
        try:
            key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
            return jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=audience,
                options={"verify_aud": audience is not None},
            )
        except jwt.PyJWTError as exc:
            last_error = exc
    raise TokenError("Provider token verification failed") from last_error


def provider_role(claims: dict[str, Any]) -> str | None:
    """Extract an RBAC role from provider claims (Clerk/Auth0 metadata)."""
    candidates = (
        ("public_metadata", "role"),  # Clerk
        ("app_metadata", "role"),  # Auth0
        ("role",),
        ("https://pulseai/role",),  # Auth0 namespaced claim
    )
    for path in candidates:
        value: Any = claims
        try:
            for key in path:
                value = value[key]
        except KeyError, TypeError:
            continue
        if isinstance(value, str) and value in VALID_ROLES:
            return value
    return None
