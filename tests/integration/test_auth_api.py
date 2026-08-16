"""Integration tests: auth flow, refresh rotation, API keys, CSRF (spec §20-23)."""

import hashlib
import uuid

from backend.core.config import settings
from backend.db.models import ApiKey, AuditLog, RefreshToken, User
from backend.modules.auth import security

PASSWORD = "CorrectHorse!42"
EMAIL = "alice@example.com"


def _register(client, email=EMAIL, password=PASSWORD):
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "Alice"},
    )
    assert resp.status_code == 201, resp.text
    return resp


def _login(client, email=EMAIL, password=PASSWORD):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _bearer(client):
    login = _login(client)
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


class TestRegister:
    def test_registers_user_with_user_role(self, client, db):
        resp = _register(client)
        body = resp.json()
        assert body["role"] == "user"
        assert body["email"] == EMAIL
        assert body["display_name"] == "Alice"
        assert "password" not in body

        row = db.query(User).filter(User.email == EMAIL).one()
        assert row.password_hash and row.password_hash != PASSWORD
        assert security.verify_password(PASSWORD, row.password_hash)
        assert db.query(AuditLog).filter(AuditLog.action == "user_registered").count() == 1

    def test_duplicate_email_conflict(self, client):
        _register(client)
        resp = client.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONFLICT"

    def test_short_password_rejected(self, client):
        resp = client.post("/api/v1/auth/register", json={"email": EMAIL, "password": "short"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_invalid_email_rejected(self, client):
        resp = client.post(
            "/api/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD}
        )
        assert resp.status_code == 422


class TestLogin:
    def test_returns_access_token_and_sets_cookies(self, client):
        _register(client)
        resp = _login(client)
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == settings.jwt_access_ttl_minutes * 60
        assert "refresh_token" not in body  # refresh travels only via the cookie (§21)

        claims = security.decode_access_token(body["access_token"])
        assert claims["email"] == EMAIL
        assert claims["role"] == "user"

        set_cookie = resp.headers.get("set-cookie", "")
        assert "HttpOnly" in set_cookie
        assert client.cookies.get(settings.access_cookie_name)
        assert client.cookies.get(settings.refresh_cookie_name)
        assert client.cookies.get(settings.csrf_cookie_name)

    def test_wrong_password_401(self, client):
        _register(client)
        resp = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "Wrong-Pass-1"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    def test_unknown_user_401(self, client):
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": PASSWORD},
        )
        assert resp.status_code == 401

    def test_failed_login_is_audited(self, client, db):
        _register(client)
        client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "Wrong-Pass-1"})
        assert db.query(AuditLog).filter(AuditLog.action == "login_failed").count() == 1


class TestRefreshRotation:
    def test_rotation_issues_new_token_and_revokes_old(self, client, db, csrf_headers):
        _register(client)
        _login(client)
        old_refresh = client.cookies.get(settings.refresh_cookie_name)
        old_hash = security.hash_token(old_refresh)

        resp = client.post("/api/v1/auth/refresh", headers=csrf_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["email"] == EMAIL
        assert security.decode_access_token(body["access_token"])["role"] == "user"

        new_refresh = client.cookies.get(settings.refresh_cookie_name)
        assert new_refresh and new_refresh != old_refresh

        row = db.query(RefreshToken).filter(RefreshToken.token_hash == old_hash).one()
        assert row.revoked_at is not None
        assert db.query(RefreshToken).count() == 2

    def test_old_refresh_token_rejected_after_rotation(self, client, csrf_headers):
        _register(client)
        _login(client)
        old_refresh = client.cookies.get(settings.refresh_cookie_name)
        client.post("/api/v1/auth/refresh", headers=csrf_headers())
        csrf = client.cookies.get(settings.csrf_cookie_name)

        resp = client.post(
            "/api/v1/auth/refresh",
            cookies={
                settings.refresh_cookie_name: old_refresh,
                settings.csrf_cookie_name: csrf,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 401

    def test_refresh_without_cookie_401(self, client):
        resp = client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401

    def test_refresh_without_csrf_header_403(self, client):
        _register(client)
        _login(client)
        resp = client.post("/api/v1/auth/refresh")  # cookies present, no CSRF header
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "CSRF_FAILED"


class TestLogout:
    def test_logout_revokes_token_and_clears_cookies(self, client):
        _register(client)
        _login(client)
        old_refresh = client.cookies.get(settings.refresh_cookie_name)
        old_csrf = client.cookies.get(settings.csrf_cookie_name)

        resp = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": old_csrf})
        assert resp.status_code == 204
        assert client.cookies.get(settings.refresh_cookie_name) is None
        assert client.cookies.get(settings.csrf_cookie_name) is None

        # The revoked refresh token no longer works.
        resp = client.post(
            "/api/v1/auth/refresh",
            cookies={
                settings.refresh_cookie_name: old_refresh,
                settings.csrf_cookie_name: old_csrf,
            },
            headers={"X-CSRF-Token": old_csrf},
        )
        assert resp.status_code == 401


class TestMe:
    def test_me_returns_profile(self, client):
        _register(client)
        headers = _bearer(client)
        resp = client.get("/api/v1/users/me", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == EMAIL
        assert body["role"] == "user"
        assert body["is_active"] is True

    def test_me_via_access_cookie(self, client):
        _register(client)
        _login(client)
        resp = client.get("/api/v1/users/me")  # cookie-authenticated, no header
        assert resp.status_code == 200
        assert resp.json()["email"] == EMAIL

    def test_me_requires_auth(self, client):
        resp = client.get("/api/v1/users/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"


class TestApiKeys:
    def test_create_returns_raw_once_and_stores_hash(self, client, db):
        _register(client)
        headers = _bearer(client)
        resp = client.post(
            "/api/v1/api-keys", json={"label": "cli", "scopes": ["read"]}, headers=headers
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["key"].startswith(security.API_KEY_PREFIX)
        assert body["label"] == "cli"
        assert body["scopes"] == ["read"]

        row = db.query(ApiKey).one()
        assert row.key_hash == hashlib.sha256(body["key"].encode()).hexdigest()
        assert row.key_hash != body["key"]
        assert db.query(AuditLog).filter(AuditLog.action == "api_key_created").count() == 1

    def test_list_never_returns_raw_key(self, client):
        _register(client)
        headers = _bearer(client)
        created = client.post("/api/v1/api-keys", json={"label": "a"}, headers=headers).json()
        client.post("/api/v1/api-keys", json={"label": "b"}, headers=headers)
        raw = created["key"]

        resp = client.get("/api/v1/api-keys", headers=headers)
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        assert all("key" not in item for item in items)
        assert raw not in resp.text

    def test_revoked_key_cannot_authenticate(self, client, db):
        _register(client)
        headers = _bearer(client)
        created = client.post("/api/v1/api-keys", json={}, headers=headers).json()
        key_headers = {"Authorization": f"Bearer {created['key']}"}

        assert client.get("/api/v1/users/me", headers=key_headers).status_code == 200
        assert db.query(ApiKey).one().last_used_at is not None

        resp = client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)
        assert resp.status_code == 204
        assert db.query(AuditLog).filter(AuditLog.action == "api_key_revoked").count() == 1
        assert client.get("/api/v1/users/me", headers=key_headers).status_code == 401

    def test_revoke_unknown_key_404(self, client):
        _register(client)
        headers = _bearer(client)
        resp = client.delete(f"/api/v1/api-keys/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    def test_invalid_scope_rejected(self, client):
        _register(client)
        headers = _bearer(client)
        resp = client.post("/api/v1/api-keys", json={"scopes": ["admin"]}, headers=headers)
        assert resp.status_code == 422

    def test_endpoints_require_auth(self, client):
        assert client.get("/api/v1/api-keys").status_code == 401
        assert client.post("/api/v1/api-keys", json={}).status_code == 401
        assert client.delete(f"/api/v1/api-keys/{uuid.uuid4()}").status_code == 401


class TestCSRF:
    def test_cookie_mutation_without_header_403(self, client):
        _register(client)
        _login(client)
        resp = client.post("/api/v1/api-keys", json={})  # cookies sent, no header
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "CSRF_FAILED"

    def test_cookie_mutation_with_header_ok(self, client, csrf_headers):
        _register(client)
        _login(client)
        resp = client.post("/api/v1/api-keys", json={}, headers=csrf_headers())
        assert resp.status_code == 201

    def test_bearer_mutation_skips_csrf(self, client):
        _register(client)
        headers = _bearer(client)
        resp = client.post("/api/v1/api-keys", json={}, headers=headers)
        assert resp.status_code == 201

    def test_login_is_exempt(self, client):
        _register(client)
        resp = _login(client)  # no CSRF header, no cookies — must still work
        assert resp.status_code == 200
