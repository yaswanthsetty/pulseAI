"""Integration tests for the library + insights API routers (Phase 7)."""

import uuid
from datetime import UTC, datetime

import pytest
from backend.db.models import Article, Source


def _make_source(db, name: str) -> Source:
    source = Source(
        name=name,
        rss_url=f"https://fixture.example.com/{uuid.uuid4().hex[:6]}.xml",
        status="active",
        poll_interval_minutes=15,
    )
    db.add(source)
    db.flush()
    return source


def _make_article(db, source: Source, title: str) -> Article:
    article = Article(
        source_id=source.id,
        title=title,
        description=f"Body about {title}",
        url=f"https://fixture.example.com/{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex,
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    db.add(article)
    db.flush()
    return article


@pytest.fixture
def auth(make_user):
    return make_user(role="user")


# ── Library API ─────────────────────────────────────────────────────────────


class TestLibraryApi:
    def test_saved_search_roundtrip(self, client, auth, db):
        resp = client.post(
            "/api/v1/library/searches",
            json={"query": "agent frameworks"},
            headers=auth,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["query"] == "agent frameworks"

        listed = client.get("/api/v1/library/searches", headers=auth)
        assert listed.status_code == 200
        assert any(s["query"] == "agent frameworks" for s in listed.json()["items"])

        deleted = client.delete(f"/api/v1/library/searches/{body['id']}", headers=auth)
        assert deleted.status_code == 204

    def test_saved_search_requires_auth(self, client):
        assert client.get("/api/v1/library/searches").status_code == 401

    def test_bookmark_roundtrip_and_404(self, client, auth, db):
        source = _make_source(db, f"Api Source {uuid.uuid4().hex[:6]}")
        article = _make_article(db, source, "Bookmark api article")
        db.commit()

        added = client.put(f"/api/v1/library/bookmarks/{article.id}", headers=auth)
        assert added.status_code == 204

        listed = client.get("/api/v1/library/bookmarks", headers=auth)
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert any(b["article_id"] == str(article.id) for b in items)

        removed = client.delete(f"/api/v1/library/bookmarks/{article.id}", headers=auth)
        assert removed.status_code == 204
        assert (
            client.delete(f"/api/v1/library/bookmarks/{article.id}", headers=auth).status_code
            == 404
        )

    def test_notification_rule_validation(self, client, auth):
        resp = client.post(
            "/api/v1/library/notification-rules",
            json={"keyword_or_topic": None, "category_code": None, "channel": "in_app"},
            headers=auth,
        )
        assert resp.status_code == 422

    def test_notification_rule_roundtrip(self, client, auth):
        resp = client.post(
            "/api/v1/library/notification-rules",
            json={"keyword_or_topic": "robotics", "channel": "in_app"},
            headers=auth,
        )
        assert resp.status_code == 201, resp.text
        rule_id = resp.json()["id"]
        assert (
            client.delete(f"/api/v1/library/notification-rules/{rule_id}", headers=auth).status_code
            == 204
        )

    def test_notifications_inbox_empty(self, client, auth):
        resp = client.get("/api/v1/library/notifications", headers=auth)
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "unread": 0}

    def test_mark_all_read(self, client, auth):
        resp = client.post("/api/v1/library/notifications/read-all", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["updated"] >= 0


# ── Insights API ────────────────────────────────────────────────────────────


class TestInsightsApi:
    def test_stats_open_endpoint(self, client, db):
        resp = client.get("/api/v1/insights/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["articles_per_day"]) == 14
        assert "articles_total" in body

    def test_trending(self, client, db):
        resp = client.get("/api/v1/insights/trending?limit=5")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_article_detail_with_bookmark_state(self, client, auth, db):
        source = _make_source(db, f"Detail Source {uuid.uuid4().hex[:6]}")
        article = _make_article(db, source, "Detail api article")
        db.commit()

        resp = client.get(f"/api/v1/insights/articles/{article.id}", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Detail api article"
        assert body["bookmarked"] is False
        assert body["source"].startswith("Detail Source")

    def test_article_detail_404(self, client, auth):
        assert (
            client.get(f"/api/v1/insights/articles/{uuid.uuid4()}", headers=auth).status_code == 404
        )

    def test_compare_endpoint(self, client, db):
        src_a = _make_source(db, "CmpA Outlet")
        src_b = _make_source(db, "CmpB Outlet")
        _make_article(db, src_a, "Quantum computing breakthrough announced")
        _make_article(db, src_b, "Quantum computing startups funded")
        db.commit()

        resp = client.get(
            "/api/v1/insights/compare",
            params={"q": "quantum", "a": "CmpA Outlet", "b": "CmpB Outlet"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_a"]["article_count"] >= 1
        assert body["source_b"]["article_count"] >= 1

    def test_compare_unknown_source_404(self, client, db):
        resp = client.get(
            "/api/v1/insights/compare",
            params={"q": "ai", "a": "Ghost", "b": "AlsoGhost"},
        )
        assert resp.status_code == 404
