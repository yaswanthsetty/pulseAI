"""API tests for the Phase 3 events endpoints (spec §20)."""

import uuid
from datetime import UTC, datetime, timedelta

from backend.db.models import Article, Event, EventArticle, Source
from backend.modules.ingestion.dedupe import url_hash
from sqlalchemy import text as sa_text


def _article(db, *, title="Event story", category_code=None):
    source = Source(
        name=f"Events API Source {uuid.uuid4().hex[:6]}",
        rss_url="https://fixture.example.com/feed.xml",
        status="active",
        poll_interval_minutes=15,
    )
    db.add(source)
    db.flush()
    article = Article(
        source_id=source.id,
        title=title,
        description="The story body for the event timeline.",
        url=f"https://fixture.example.com/articles/{uuid.uuid4().hex}",
        url_hash=url_hash(f"https://fixture.example.com/articles/{uuid.uuid4().hex}"),
        published_at=datetime.now(UTC),
        processed_at=datetime.now(UTC),
        category_code=category_code,
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


class TestListEvents:
    def test_returns_paginated_events(self, client, db):
        event = Event(
            title="Fusion breakthrough",
            summary="A summary.",
            confidence=0.91,
            status="open",
            article_count=2,
        )
        db.add(event)
        db.commit()

        resp = client.get("/api/v1/events")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert body["page_size"] == 20
        item = body["items"][0]
        assert item["id"] == str(event.id)
        assert item["title"] == "Fusion breakthrough"
        assert item["confidence"] == 0.91
        assert item["article_count"] == 2

    def test_filters_by_min_confidence(self, client, db):
        db.add(Event(title="High", confidence=0.9, status="open", article_count=1))
        db.add(Event(title="Low", confidence=0.3, status="open", article_count=1))
        db.commit()

        resp = client.get("/api/v1/events", params={"min_confidence": 0.8})

        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()["items"]]
        assert titles == ["High"]

    def test_filters_by_category(self, client, db):
        tech = _article(db, title="AI story", category_code="technology")
        sports = _article(db, title="Sports story", category_code="sports")
        event = Event(title="Mixed event", status="open", article_count=2)
        db.add(event)
        db.commit()
        db.refresh(event)
        db.add(EventArticle(event_id=event.id, article_id=tech.id))
        db.add(EventArticle(event_id=event.id, article_id=sports.id))
        db.commit()

        resp = client.get("/api/v1/events", params={"category_code": "technology"})

        assert resp.status_code == 200
        assert [i["title"] for i in resp.json()["items"]] == ["Mixed event"]

    def test_pagination(self, client, db):
        for i in range(3):
            db.add(Event(title=f"Event {i}", status="open", article_count=1))
        db.commit()

        resp = client.get("/api/v1/events", params={"page": 1, "page_size": 2})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3


class TestGetEventTimeline:
    """GET /api/v1/events/{id}/timeline — day-grouped evolving summary."""

    def test_groups_articles_by_day(self, client, db):
        day1 = _article(db, title="AI startup raises seed round")
        day2a = _article(db, title="AI startup doubles valuation")
        day2b = _article(db, title="Investors pile into AI startup")
        # Force distinct publication days.
        db.execute(
            sa_text("UPDATE articles SET published_at = :ts WHERE id = :aid"),
            {"ts": datetime.now(UTC) - timedelta(days=2), "aid": day1.id},
        )
        db.execute(
            sa_text("UPDATE articles SET published_at = :ts WHERE id IN (:a, :b)"),
            {"ts": datetime.now(UTC), "a": day2a.id, "b": day2b.id},
        )
        db.commit()
        event = Event(title="AI startup saga", status="open", article_count=3)
        db.add(event)
        db.commit()
        db.refresh(event)
        for a in (day1, day2a, day2b):
            db.add(EventArticle(event_id=event.id, article_id=a.id))
        db.commit()

        resp = client.get(f"/api/v1/events/{event.id}/timeline")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(event.id)
        assert body["total_articles"] == 3
        assert len(body["days"]) == 2
        first, second = body["days"]
        assert first["date"] < second["date"]  # oldest first
        assert first["article_count"] == 1
        assert second["article_count"] == 2
        assert first["headline"] == "AI startup raises seed round"
        titles = set(second["titles"])
        assert titles == {
            "AI startup doubles valuation",
            "Investors pile into AI startup",
        }
        assert body["first_day"] == first["date"]
        assert body["last_day"] == second["date"]

    def test_keywords_extracted_from_titles(self, client, db):
        article = _article(db, title="Central bank raises interest rates again")
        db.execute(
            sa_text("UPDATE articles SET published_at = :ts WHERE id = :aid"),
            {"ts": datetime.now(UTC), "aid": article.id},
        )
        db.commit()
        event = Event(title="Rates", status="open", article_count=1)
        db.add(event)
        db.commit()
        db.refresh(event)
        db.add(EventArticle(event_id=event.id, article_id=article.id))
        db.commit()

        resp = client.get(f"/api/v1/events/{event.id}/timeline")

        assert resp.status_code == 200
        keywords = resp.json()["days"][0]["keywords"]
        assert "interest" in keywords or "central" in keywords or "rates" in keywords

    def test_headline_prefers_centroid_closest_article(self, client, db):
        # Both on the same day; the second has a higher match similarity, so it
        # should win the headline even though the first was published earlier.
        first = _article(db, title="Election results announced")
        closest = _article(db, title="Election: swing states decide outcome")
        db.execute(
            sa_text("UPDATE articles SET published_at = :ts WHERE id IN (:a, :b)"),
            {"ts": datetime.now(UTC), "a": first.id, "b": closest.id},
        )
        db.commit()
        event = Event(title="Election coverage", status="open", article_count=2)
        db.add(event)
        db.commit()
        db.refresh(event)
        db.add(EventArticle(event_id=event.id, article_id=first.id, similarity_at_match=0.71))
        db.add(EventArticle(event_id=event.id, article_id=closest.id, similarity_at_match=0.9))
        db.commit()

        resp = client.get(f"/api/v1/events/{event.id}/timeline")

        assert resp.status_code == 200
        assert resp.json()["days"][0]["headline"] == "Election: swing states decide outcome"

    def test_missing_event_returns_404(self, client):
        resp = client.get(f"/api/v1/events/{uuid.uuid4()}/timeline")

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"


class TestGetEventDetail:
    def test_returns_timeline_in_added_order(self, client, db):
        first = _article(db, title="First report")
        second = _article(db, title="Follow-up report")
        event = Event(title="Election coverage", status="open", article_count=2)
        db.add(event)
        db.commit()
        db.refresh(event)
        db.add(EventArticle(event_id=event.id, article_id=first.id, similarity_at_match=0.9))
        db.add(EventArticle(event_id=event.id, article_id=second.id, similarity_at_match=0.85))
        db.commit()

        resp = client.get(f"/api/v1/events/{event.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Election coverage"
        assert [t["article_id"] for t in body["timeline"]] == [
            str(first.id),
            str(second.id),
        ]
        assert body["timeline"][0]["similarity_at_match"] == 0.9

    def test_missing_event_returns_404(self, client):
        resp = client.get(f"/api/v1/events/{uuid.uuid4()}")

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"
