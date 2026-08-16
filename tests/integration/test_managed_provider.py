"""Managed-provider (Clerk/Auth0) verification and sync (spec §21).

The provider is simulated: an in-test RSA key pair serves as the provider's
signing key, and the JWKS fetch is stubbed so no network is involved.
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from backend.core.config import settings
from backend.db.models import AuditLog, User
from backend.modules.auth import security
from backend.modules.auth.security import TokenError
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64_int(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


@pytest.fixture
def provider_env(monkeypatch):
    """Point the app at a fake Clerk provider with a stubbed JWKS fetch."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "test-key-1",
        "n": _b64_int(public.n),
        "e": _b64_int(public.e),
    }

    monkeypatch.setattr(settings, "auth_provider", "clerk")
    monkeypatch.setattr(settings, "clerk_domain", "unit-test.clerk.accounts.dev")
    security._jwks_cache.pop("clerk", None)

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"keys": [jwk]}

    monkeypatch.setattr(security.httpx, "get", lambda url, timeout=None: _FakeResponse())

    def make_token(email="prov@example.com", role=None, **overrides):
        now = datetime.now(UTC)
        claims = {
            "sub": f"user_{uuid.uuid4().hex[:12]}",
            "email": email,
            "iss": "https://unit-test.clerk.accounts.dev",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        if role:
            claims["public_metadata"] = {"role": role}
        claims.update(overrides)
        return pyjwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key-1"})

    return make_token


class TestProviderVerification:
    def test_verifies_provider_token(self, provider_env):
        claims = security.decode_provider_token(provider_env())
        assert claims["email"] == "prov@example.com"

    def test_rejects_token_signed_by_another_key(self, provider_env):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = pyjwt.encode(
            {"sub": "x", "email": "x@example.com", "exp": datetime.now(UTC) + timedelta(minutes=5)},
            other,
            algorithm="RS256",
        )
        with pytest.raises(TokenError):
            security.decode_provider_token(token)

    def test_rejects_expired_token(self, provider_env):
        token = provider_env(exp=datetime.now(UTC) - timedelta(hours=1))
        with pytest.raises(TokenError):
            security.decode_provider_token(token)

    def test_local_mode_has_no_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_provider", "none")
        with pytest.raises(TokenError, match="No managed"):
            security.decode_provider_token("anything")


def security_sync(db, claims):
    """Local alias so imports stay readable."""
    from backend.modules.auth import service

    return service.sync_managed_user(db, claims)


class TestManagedSync:
    def test_sync_creates_user_with_provider_role(self, provider_env, db):
        claims = security.decode_provider_token(provider_env(role="analyst"))
        user = security_sync(db, claims)
        assert user.email == "prov@example.com"
        assert user.role == "analyst"
        assert db.query(User).filter(User.email == "prov@example.com").count() == 1
        assert db.query(AuditLog).filter(AuditLog.action == "user_synced").count() == 1

    def test_sync_updates_role_when_claimed(self, provider_env, db):
        security_sync(db, security.decode_provider_token(provider_env(role="analyst")))
        security_sync(db, security.decode_provider_token(provider_env(role="admin")))
        user = db.query(User).filter(User.email == "prov@example.com").one()
        assert user.role == "admin"
        assert db.query(User).count() == 1

    def test_sync_preserves_role_without_claim(self, provider_env, db):
        security_sync(db, security.decode_provider_token(provider_env(role="analyst")))
        security_sync(db, security.decode_provider_token(provider_env()))
        user = db.query(User).filter(User.email == "prov@example.com").one()
        assert user.role == "analyst"

    def test_sync_rejects_token_without_email(self, provider_env, db):
        from backend.modules.auth import service

        claims = security.decode_provider_token(provider_env(email=""))
        with pytest.raises(service.InvalidTokenError):
            security_sync(db, claims)

    def test_sync_audits_only_when_user_changes(self, provider_env, db):
        claims = security.decode_provider_token(provider_env(role="analyst"))
        security_sync(db, claims)
        security_sync(db, claims)  # unchanged user — no new audit row
        assert db.query(AuditLog).filter(AuditLog.action == "user_synced").count() == 1
        security_sync(db, security.decode_provider_token(provider_env(role="admin")))
        assert db.query(AuditLog).filter(AuditLog.action == "user_synced").count() == 2


class TestSessionEndpoint:
    def test_session_exchanges_provider_token_for_tokens(self, provider_env, client):
        token = provider_env(role="admin")
        resp = client.post("/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["email"] == "prov@example.com"
        assert body["user"]["role"] == "admin"
        assert body["access_token"]
        assert client.cookies.get(settings.refresh_cookie_name)

    def test_session_in_local_mode_400(self, client, monkeypatch):
        monkeypatch.setattr(settings, "auth_provider", "none")
        resp = client.post("/api/v1/auth/session", headers={"Authorization": "Bearer not-a-token"})
        assert resp.status_code == 400

    def test_session_without_token_401(self, provider_env, client):
        resp = client.post("/api/v1/auth/session")
        assert resp.status_code == 401


class TestDirectProviderAuth:
    def test_provider_token_works_on_protected_endpoint(self, provider_env, client):
        token = provider_env()
        resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "prov@example.com"
        assert resp.json()["role"] == "user"

    def test_provider_token_without_email_is_401_not_500(self, provider_env, client):
        token = provider_env(email="")
        resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_garbage_token_401(self, provider_env, client):
        resp = client.get("/api/v1/users/me", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
