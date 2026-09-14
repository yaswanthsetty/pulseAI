"""Unit tests for the library module (saved searches, bookmarks, rules)."""

import uuid
from datetime import UTC, datetime

import pytest
from backend.db.models import (
    Article,
    NotificationDelivery,
    SavedSearch,
    Source,
    User,
)
from backend.modules.library import service
from sqlalchemy import select


def _user_id(db, email: str) -> uuid.UUID:
    """Resolve a user's id by email (registered through the client fixture)."""
    row = db.execute(select(User).where(User.email == email)).scalars().one()
    return row.id


@pytest.fixture
def user_id(db, client, make_user):
    email = f"lib-{uuid.uuid4().hex[:8]}@example.com"
    make_user(email=email)
    return _user_id(db, email)


@pytest.fixture
def article_id(db):
    source = Source(
        name=f"Lib Source {uuid.uuid4().hex[:6]}",
        rss_url="https://fixture.example.com/feed.xml",
        status="active",
        poll_interval_minutes=15,
    )
    db.add(source)
    db.flush()
    article = Article(
        source_id=source.id,
        title="Library test article",
        url="https://fixture.example.com/library-test",
        url_hash=uuid.uuid4().hex,
        published_at=datetime.now(UTC),
    )
    db.add(article)
    db.commit()
    return article.id


# ── Saved searches ──────────────────────────────────────────────────────────


class TestSavedSearches:
    def test_create_and_list(self, db, user_id):
        service.create_saved_search(db, user_id, "quantum computing", None)
        rows = service.list_saved_searches(db, user_id)
        assert len(rows) == 1
        assert rows[0].query == "quantum computing"

    def test_delete_scoped_to_owner(self, db, user_id):
        row = service.create_saved_search(db, user_id, "to delete", None)
        assert service.delete_saved_search(db, uuid.uuid4(), row.id) is False
        assert service.delete_saved_search(db, user_id, row.id) is True
        assert db.get(SavedSearch, row.id) is None

    def test_delete_missing_returns_false(self, db, user_id):
        assert service.delete_saved_search(db, user_id, uuid.uuid4()) is False


# ── Bookmarks ───────────────────────────────────────────────────────────────


class TestBookmarks:
    def test_add_is_idempotent(self, db, user_id, article_id):
        assert service.add_bookmark(db, user_id, article_id) is True
        assert service.add_bookmark(db, user_id, article_id) is True
        rows = service.list_bookmarks(db, user_id)
        assert len(rows) == 1
        assert rows[0].title == "Library test article"
        assert rows[0].name.startswith("Lib Source")

    def test_add_unknown_article(self, db, user_id):
        assert service.add_bookmark(db, user_id, uuid.uuid4()) is False

    def test_remove(self, db, user_id, article_id):
        service.add_bookmark(db, user_id, article_id)
        assert service.remove_bookmark(db, user_id, article_id) is True
        assert service.remove_bookmark(db, user_id, article_id) is False

    def test_bookmarked_article_ids(self, db, user_id, article_id):
        service.add_bookmark(db, user_id, article_id)
        assert service.bookmarked_article_ids(db, user_id) == {article_id}


# ── Notification rules ──────────────────────────────────────────────────────


class TestNotificationRules:
    def test_create_requires_keyword_or_category(self, db, user_id):
        with pytest.raises(ValueError):
            service.create_notification_rule(db, user_id, None, None, "in_app")

    def test_create_and_list(self, db, user_id):
        service.create_notification_rule(db, user_id, "AI safety", None, "in_app")
        rows = service.list_notification_rules(db, user_id)
        assert len(rows) == 1
        assert rows[0].keyword_or_topic == "AI safety"
        assert rows[0].channel == "in_app"

    def test_delete_scoped_to_owner(self, db, user_id):
        row = service.create_notification_rule(db, user_id, "kw", None, "email")
        assert service.delete_notification_rule(db, uuid.uuid4(), row.id) is False
        assert service.delete_notification_rule(db, user_id, row.id) is True


# ── Deliveries inbox ────────────────────────────────────────────────────────


class TestDeliveries:
    def test_list_and_mark_read(self, db, user_id):
        rule = service.create_notification_rule(db, user_id, "kw", None, "in_app")
        db.add(
            NotificationDelivery(
                rule_id=rule.id,
                user_id=user_id,
                channel="in_app",
                status="sent",
                detail="hello",
            )
        )
        db.commit()

        items, unread = service.list_deliveries(db, user_id)
        assert len(items) == 1
        assert unread == 1

        updated = service.mark_deliveries_read(db, user_id)
        assert updated == 1
        _items, unread = service.list_deliveries(db, user_id)
        assert unread == 0
