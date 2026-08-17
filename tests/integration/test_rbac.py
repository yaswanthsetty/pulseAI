"""RBAC matrix tests (spec §22) — endpoint behavior per role."""

import uuid
from pathlib import Path

import pytest
from backend.db.models import AuditLog, User
from backend.modules.ingestion import service as ingestion_service
from backend.modules.retrieval import service as retrieval_service

FIXTURES = Path(__file__).parent.parent / "fixtures"

ADMIN = "admin@example.com"
USER = "user@example.com"


@pytest.fixture
def feed_content() -> str:
    return (FIXTURES / "feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def no_enqueue(monkeypatch):
    monkeypatch.setattr(
        ingestion_service.queue, "enqueue_process_article", lambda aid: f"process-{aid}"
    )
    monkeypatch.setattr(ingestion_service.queue, "enqueue_poll", lambda sid: f"poll-{sid}")


class TestGuest:
    """Guests may browse/search; everything personal or admin is gated."""

    def test_browse_articles(self, client):
        assert client.get("/api/v1/articles").status_code == 200

    def test_search_allowed(self, client, monkeypatch):
        monkeypatch.setattr(
            retrieval_service,
            "search",
            lambda query, limit, mode="semantic", filters=None: [],
        )
        assert client.post("/api/v1/search", json={"query": "anything"}).status_code == 200

    def test_source_listing_requires_auth(self, client):
        assert client.get("/api/v1/sources").status_code == 401

    def test_source_management_requires_auth(self, client):
        assert client.post("/api/v1/sources", json={}).status_code == 401

    def test_users_me_requires_auth(self, client):
        assert client.get("/api/v1/users/me").status_code == 401

    def test_admin_surface_requires_auth(self, client):
        assert client.get("/api/v1/users").status_code == 401


class TestUser:
    def test_can_list_sources(self, client, make_user):
        headers = make_user()
        assert client.get("/api/v1/sources", headers=headers).status_code == 200

    def test_cannot_manage_sources(self, client, make_user):
        headers = make_user()
        assert client.post("/api/v1/sources", json={}, headers=headers).status_code == 403
        assert (
            client.patch(f"/api/v1/sources/{uuid.uuid4()}", json={}, headers=headers).status_code
            == 403
        )
        assert (
            client.post(f"/api/v1/sources/{uuid.uuid4()}/poll", headers=headers).status_code == 403
        )

    def test_cannot_list_users(self, client, make_user):
        headers = make_user()
        assert client.get("/api/v1/users", headers=headers).status_code == 403

    def test_me_returns_own_profile(self, client, make_user):
        headers = make_user()
        resp = client.get("/api/v1/users/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["role"] == "user"


class TestAnalyst:
    def test_cannot_manage_sources(self, client, make_user):
        headers = make_user(role="analyst")
        assert client.post("/api/v1/sources", json={}, headers=headers).status_code == 403

    def test_cannot_list_users(self, client, make_user):
        headers = make_user(role="analyst")
        assert client.get("/api/v1/users", headers=headers).status_code == 403

    def test_can_list_sources(self, client, make_user):
        headers = make_user(role="analyst")
        assert client.get("/api/v1/sources", headers=headers).status_code == 200


class TestAdmin:
    def test_can_manage_sources(self, client, make_user, feed_content, no_enqueue, monkeypatch):
        headers = make_user(email=ADMIN, role="admin")
        monkeypatch.setattr(ingestion_service, "fetch_url", lambda url, timeout=None: feed_content)
        resp = client.post(
            "/api/v1/sources",
            json={"name": "Admin Feed", "rss_url": "https://fixture.example.com/feed.xml"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    def test_can_list_users(self, client, make_user, db):
        make_user(email=USER)
        headers = make_user(email=ADMIN, role="admin")
        resp = client.get("/api/v1/users", headers=headers)
        assert resp.status_code == 200
        emails = {item["email"] for item in resp.json()["items"]}
        assert {USER, ADMIN} <= emails

    def test_can_change_role(self, client, make_user, db):
        make_user(email=USER)
        headers = make_user(email=ADMIN, role="admin")
        user_id = db.query(User).filter(User.email == USER).one().id

        resp = client.patch(
            f"/api/v1/users/{user_id}/role", json={"role": "analyst"}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "analyst"
        assert db.query(User).filter(User.email == USER).one().role == "analyst"
        assert db.query(AuditLog).filter(AuditLog.action == "role_change").count() == 1

    def test_cannot_demote_self(self, client, make_user, db):
        headers = make_user(email=ADMIN, role="admin")
        admin_id = db.query(User).filter(User.email == ADMIN).one().id
        resp = client.patch(
            f"/api/v1/users/{admin_id}/role", json={"role": "user"}, headers=headers
        )
        assert resp.status_code == 400

    def test_role_change_unknown_user_404(self, client, make_user):
        headers = make_user(email=ADMIN, role="admin")
        resp = client.patch(
            f"/api/v1/users/{uuid.uuid4()}/role", json={"role": "user"}, headers=headers
        )
        assert resp.status_code == 404
