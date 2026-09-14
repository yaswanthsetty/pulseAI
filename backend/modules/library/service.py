"""Library service: saved searches, bookmarks, notification rules, deliveries.

Pure data-access; the router owns HTTP concerns.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import (
    Article,
    Bookmark,
    NotificationDelivery,
    NotificationRule,
    SavedSearch,
    Source,
)

# ── Saved searches ──────────────────────────────────────────────────────────


def list_saved_searches(db: Session, user_id: uuid.UUID) -> list[SavedSearch]:
    return list(
        db.execute(
            select(SavedSearch)
            .where(SavedSearch.user_id == user_id)
            .order_by(SavedSearch.created_at.desc())
        ).scalars()
    )


def create_saved_search(
    db: Session, user_id: uuid.UUID, query: str, filters: dict | None
) -> SavedSearch:
    row = SavedSearch(user_id=user_id, query=query, filters=filters or {})
    db.add(row)
    db.commit()
    return row


def delete_saved_search(db: Session, user_id: uuid.UUID, search_id: uuid.UUID) -> bool:
    row = db.get(SavedSearch, search_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    db.commit()
    return True


# ── Bookmarks ───────────────────────────────────────────────────────────────


def list_bookmarks(db: Session, user_id: uuid.UUID) -> list:
    """Bookmarks joined with their article's display fields, newest first."""
    return list(
        db.execute(
            select(
                Bookmark.article_id,
                Bookmark.created_at,
                Article.title,
                Article.url,
                Article.description,
                Article.author,
                Article.published_at,
                Article.category_code,
                Source.name,
            )
            .join(Article, Article.id == Bookmark.article_id)
            .join(Source, Source.id == Article.source_id)
            .where(Bookmark.user_id == user_id)
            .order_by(Bookmark.created_at.desc())
        ).all()
    )


def add_bookmark(db: Session, user_id: uuid.UUID, article_id: uuid.UUID) -> bool:
    """Bookmark an article; idempotent. Returns False if the article is unknown."""
    if db.get(Article, article_id) is None:
        return False
    existing = db.execute(
        select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.article_id == article_id)
    ).scalar_one_or_none()
    if existing is None:
        db.add(Bookmark(user_id=user_id, article_id=article_id))
        db.commit()
    return True


def remove_bookmark(db: Session, user_id: uuid.UUID, article_id: uuid.UUID) -> bool:
    row = db.execute(
        select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.article_id == article_id)
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def bookmarked_article_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Cheap membership check for search-result bookmark badges."""
    return set(db.execute(select(Bookmark.article_id).where(Bookmark.user_id == user_id)).scalars())


# ── Notification rules ──────────────────────────────────────────────────────


def list_notification_rules(db: Session, user_id: uuid.UUID) -> list[NotificationRule]:
    return list(
        db.execute(
            select(NotificationRule)
            .where(NotificationRule.user_id == user_id)
            .order_by(NotificationRule.created_at.desc())
        ).scalars()
    )


def create_notification_rule(
    db: Session,
    user_id: uuid.UUID,
    keyword_or_topic: str | None,
    category_code: str | None,
    channel: str,
) -> NotificationRule:
    if not keyword_or_topic and not category_code:
        raise ValueError("A rule needs a keyword or a category")
    row = NotificationRule(
        user_id=user_id,
        keyword_or_topic=keyword_or_topic,
        category_code=category_code,
        channel=channel,
    )
    db.add(row)
    db.commit()
    return row


def delete_notification_rule(db: Session, user_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
    row = db.get(NotificationRule, rule_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    db.commit()
    return True


# ── In-app inbox ────────────────────────────────────────────────────────────


def list_deliveries(
    db: Session, user_id: uuid.UUID, limit: int = 50
) -> tuple[list[NotificationDelivery], int]:
    """Recent deliveries for the in-app inbox + unread count."""
    items = list(
        db.execute(
            select(NotificationDelivery)
            .where(NotificationDelivery.user_id == user_id)
            .order_by(NotificationDelivery.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    unread = db.execute(
        select(func.count())
        .select_from(NotificationDelivery)
        .where(
            NotificationDelivery.user_id == user_id,
            NotificationDelivery.read_at.is_(None),
        )
    ).scalar_one()
    return items, unread


def mark_deliveries_read(db: Session, user_id: uuid.UUID) -> int:
    """Mark all of a user's deliveries read; returns rows updated."""
    from datetime import UTC, datetime

    from sqlalchemy import update

    result = db.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.user_id == user_id,
            NotificationDelivery.read_at.is_(None),
        )
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    return result.rowcount or 0
