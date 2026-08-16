"""API tests: source management (FR-4) and the spec §19 error envelope."""

import uuid
from pathlib import Path

import pytest
from backend.modules.ingestion import service

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def feed_content() -> str:
    return (FIXTURES / "feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def no_enqueue(monkeypatch):
    monkeypatch.setattr(service.queue, "enqueue_process_article", lambda aid: f"process-{aid}")
    monkeypatch.setattr(service.queue, "enqueue_poll", lambda sid: f"poll-{sid}")


class TestCreateSource:
    def test_creates_active_source_after_feed_validation(
        self, client, make_user, feed_content, no_enqueue, monkeypatch
    ):
        admin = make_user(role="admin")
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        resp = client.post(
            "/api/v1/sources",
            json={
                "name": "Fixture Feed",
                "rss_url": "https://fixture.example.com/feed.xml",
                "credibility_score": 0.9,
                "poll_interval_minutes": 10,
            },
            headers=admin,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Fixture Feed"
        assert body["status"] == "active"
        assert body["credibility_score"] == 0.9
        assert body["poll_interval_minutes"] == 10
        assert body["consecutive_failures"] == 0

    def test_rejects_invalid_feed(self, client, make_user, no_enqueue, monkeypatch):
        admin = make_user(role="admin")

        def boom(url, timeout=None):
            raise service.FetchError("unreachable host")

        monkeypatch.setattr(service, "fetch_url", boom)
        resp = client.post(
            "/api/v1/sources",
            json={"name": "Bad Feed", "rss_url": "https://bad.example.com/feed"},
            headers=admin,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_rejects_malformed_payload(self, client, make_user):
        admin = make_user(role="admin")
        resp = client.post(
            "/api/v1/sources",
            json={"name": "", "rss_url": "not-a-url", "poll_interval_minutes": 2},
            headers=admin,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_rejects_poll_interval_below_minimum(
        self, client, make_user, feed_content, no_enqueue, monkeypatch
    ):
        admin = make_user(role="admin")
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        resp = client.post(
            "/api/v1/sources",
            json={
                "name": "Too Fast",
                "rss_url": "https://fixture.example.com/feed.xml",
                "poll_interval_minutes": 2,
            },
            headers=admin,
        )
        assert resp.status_code == 422


class TestListAndUpdate:
    def test_list_sources(self, client, make_user, feed_content, no_enqueue, monkeypatch):
        admin = make_user(role="admin")
        user = make_user()
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        client.post(
            "/api/v1/sources",
            json={"name": "A Feed", "rss_url": "https://fixture.example.com/feed.xml"},
            headers=admin,
        )
        resp = client.get("/api/v1/sources", headers=user)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "A Feed"
        assert body["page"] == 1

    def test_patch_updates_source(self, client, make_user, feed_content, no_enqueue, monkeypatch):
        admin = make_user(role="admin")
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        created = client.post(
            "/api/v1/sources",
            json={"name": "A Feed", "rss_url": "https://fixture.example.com/feed.xml"},
            headers=admin,
        ).json()
        resp = client.patch(
            f"/api/v1/sources/{created['id']}",
            json={"credibility_score": 0.42, "status": "disabled"},
            headers=admin,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["credibility_score"] == 0.42
        assert body["status"] == "disabled"

    def test_patch_missing_source_returns_404_envelope(self, client, make_user):
        admin = make_user(role="admin")
        resp = client.patch(
            f"/api/v1/sources/{uuid.uuid4()}",
            json={"credibility_score": 0.5},
            headers=admin,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"
        assert "request_id" in resp.json()["error"]

    def test_manual_poll_queues_job(self, client, make_user, feed_content, no_enqueue, monkeypatch):
        admin = make_user(role="admin")
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        created = client.post(
            "/api/v1/sources",
            json={"name": "A Feed", "rss_url": "https://fixture.example.com/feed.xml"},
            headers=admin,
        ).json()
        resp = client.post(f"/api/v1/sources/{created['id']}/poll", headers=admin)
        assert resp.status_code == 202
        assert resp.json()["status"] == "queued"
        assert resp.json()["job_id"].startswith("poll-")


class TestArticles:
    def test_article_not_found_envelope(self, client):
        resp = client.get(f"/api/v1/articles/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    def test_empty_list_is_paginated(self, client):
        resp = client.get("/api/v1/articles")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"items": [], "page": 1, "page_size": 20, "total": 0}

    def test_validation_error_on_bad_page_size(self, client):
        resp = client.get("/api/v1/articles?page_size=1000")
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
