"""Redis sliding-window rate limiting (spec §12/§19/§23).

Buckets:
  ``ip:{addr}``   — unauthenticated, 30 req/min per client IP
  ``user:{id}``   — authenticated, 120 req/min per user
  ``key:{hash}``  — API-key principals, 120 req/min per key

The limiter is fail-open: if Redis is unreachable the request proceeds
(logged) so API availability is never hostage to the rate-limit store.
"""

import logging
import time
import uuid

import redis
from fastapi import HTTPException, Request

from backend.core.config import settings
from backend.modules.auth import security

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


def get_redis() -> redis.Redis:
    """Redis client for rate-limit counters.

    Defined locally (not via ``core.queue``) so the auth module stays
    independent of the ingestion pipeline (module-boundary contract 4).
    """
    return redis.Redis.from_url(settings.redis_url, decode_responses=False)


class RateLimiter:
    """Sliding-window counter keyed by a bucket string; injectable Redis for tests."""

    def __init__(self, redis=None):
        self._redis = redis

    def _client(self):
        return self._redis or get_redis()

    def allowed(self, bucket: str, limit: int, *, window_seconds: int = _WINDOW_SECONDS) -> bool:
        """Record one request in *bucket*; True while the window count is <= *limit*."""
        key = f"ratelimit:{bucket}"
        now_ms = int(time.time() * 1000)
        member = uuid.uuid4().hex
        try:
            redis = self._client()
            with redis.pipeline() as pipe:
                pipe.zremrangebyscore(key, 0, now_ms - window_seconds * 1000)
                pipe.zadd(key, {member: now_ms})
                pipe.zcard(key)
                pipe.expire(key, window_seconds)
                _, _, count, _ = pipe.execute()
            return count <= limit
        except Exception:  # noqa: BLE001 - fail open by design
            logger.exception("rate limiter unavailable; allowing request")
            return True


def _bearer_or_cookie(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(settings.access_cookie_name)


def _rate_limited() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Rate limit exceeded",
        headers={"Retry-After": str(_WINDOW_SECONDS)},
    )


def rate_limit_dependency(request: Request) -> None:
    """Global ``/api/v1`` dependency: per-user or per-IP sliding window (§19)."""
    if not settings.rate_limit_enabled:
        return
    limiter = RateLimiter()
    token = _bearer_or_cookie(request)

    if token:
        try:
            claims = security.decode_access_token(token)
            bucket, limit = f"user:{claims['sub']}", settings.rate_limit_auth_per_minute
        except security.TokenError:
            if token.startswith(security.API_KEY_PREFIX):
                bucket, limit = (
                    f"key:{security.hash_token(token)[:16]}",
                    (settings.rate_limit_auth_per_minute),
                )
            else:
                bucket, limit = None, None
        if bucket:
            if not limiter.allowed(bucket, limit):
                raise _rate_limited()
            return

    ip = request.client.host if request.client else "unknown"
    if not limiter.allowed(f"ip:{ip}", settings.rate_limit_anon_per_minute):
        raise _rate_limited()
