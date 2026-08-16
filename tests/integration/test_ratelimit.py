"""Rate-limit tests: Redis sliding window + 429 behavior (spec §19/§23)."""

import time
import uuid

from backend.core.config import settings
from backend.core.queue import get_redis
from backend.modules.auth.ratelimit import RateLimiter


def _bucket_key(bucket: str) -> str:
    return f"ratelimit:{bucket}"


class TestSlidingWindow:
    def test_allows_up_to_limit_then_blocks(self):
        redis = get_redis()
        bucket = f"test-{uuid.uuid4().hex}"
        redis.delete(_bucket_key(bucket))
        try:
            limiter = RateLimiter(redis)
            assert all(limiter.allowed(bucket, 3) for _ in range(3))
            assert not limiter.allowed(bucket, 3)
        finally:
            redis.delete(_bucket_key(bucket))

    def test_buckets_are_isolated(self):
        redis = get_redis()
        a, b = f"test-a-{uuid.uuid4().hex}", f"test-b-{uuid.uuid4().hex}"
        redis.delete(_bucket_key(a), _bucket_key(b))
        try:
            limiter = RateLimiter(redis)
            assert limiter.allowed(a, 1) and not limiter.allowed(a, 1)
            assert limiter.allowed(b, 1)  # separate bucket unaffected
        finally:
            redis.delete(_bucket_key(a), _bucket_key(b))

    def test_window_slides_out_old_entries(self):
        redis = get_redis()
        bucket = f"test-{uuid.uuid4().hex}"
        redis.delete(_bucket_key(bucket))
        try:
            now_ms = int(time.time() * 1000)
            # 50 entries from two minutes ago — outside the 60s window.
            redis.zadd(_bucket_key(bucket), {f"old-{i}": now_ms - 120_000 for i in range(50)})
            assert RateLimiter(redis).allowed(bucket, 10)
        finally:
            redis.delete(_bucket_key(bucket))

    def test_fail_open_when_redis_unreachable(self, monkeypatch):
        def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr("backend.modules.auth.ratelimit.get_redis", _boom)
        assert RateLimiter().allowed("whatever", 1)


class TestGlobalDependency:
    def test_429_when_anonymous_limit_exceeded(self, client, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_enabled", True)
        monkeypatch.setattr(settings, "rate_limit_anon_per_minute", 2)
        redis = get_redis()
        key = _bucket_key("ip:testclient")
        redis.delete(key)
        try:
            assert client.get("/api/v1/articles").status_code == 200
            assert client.get("/api/v1/articles").status_code == 200
            blocked = client.get("/api/v1/articles")
            assert blocked.status_code == 429
            body = blocked.json()
            assert body["error"]["code"] == "RATE_LIMITED"
            assert blocked.headers.get("retry-after")
        finally:
            redis.delete(key)

    def test_authenticated_users_get_the_higher_limit(self, client, make_user, monkeypatch):
        headers = make_user()  # register/login while rate limiting is off
        monkeypatch.setattr(settings, "rate_limit_enabled", True)
        monkeypatch.setattr(settings, "rate_limit_anon_per_minute", 1)
        redis = get_redis()
        try:
            # First call consumes the anonymous IP bucket, but the bearer token
            # routes the request to the per-user bucket instead.
            resp = client.get("/api/v1/users/me", headers=headers)
            assert resp.status_code == 200
        finally:
            for key in redis.scan_iter("ratelimit:*"):
                redis.delete(key)
