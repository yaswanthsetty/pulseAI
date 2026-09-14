"""Insights service: dashboard stats, article detail, trends, source comparison.

Pure data-access; the router owns HTTP concerns. Heavy lifting is done in SQL
so the endpoints stay cheap enough to leave open (rate-limited) to guests.
"""

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import (
    Article,
    Event,
    EventArticle,
    Source,
    User,
)


def get_stats(db: Session) -> dict:
    """Aggregate pipeline counts for the dashboard overview."""
    now = datetime.now(UTC)

    total_articles = db.execute(select(func.count()).select_from(Article)).scalar_one()
    articles_24h = (
        db.execute(
            select(func.count())
            .select_from(Article)
            .where(Article.created_at >= now - timedelta(hours=24))
        ).scalar_one()
        or 0
    )
    total_events = db.execute(select(func.count()).select_from(Event)).scalar_one()
    open_events = (
        db.execute(
            select(func.count()).select_from(Event).where(Event.status == "open")
        ).scalar_one()
        or 0
    )
    total_sources = db.execute(select(func.count()).select_from(Source)).scalar_one()
    total_users = db.execute(select(func.count()).select_from(User)).scalar_one()

    # Articles per day for the last 14 days (SQL date bucketing).
    since = now - timedelta(days=14)
    rows = db.execute(
        select(
            func.date(Article.created_at).label("day"),
            func.count().label("n"),
        )
        .where(Article.created_at >= since)
        .group_by(func.date(Article.created_at))
        .order_by(func.date(Article.created_at))
    ).all()
    by_day = {str(day): n for day, n in rows}
    articles_per_day = [
        {
            "date": (now - timedelta(days=i)).date().isoformat(),
            "count": by_day.get((now - timedelta(days=i)).date().isoformat(), 0),
        }
        for i in range(13, -1, -1)
    ]

    # Top categories.
    category_rows = db.execute(
        select(Article.category_code, func.count().label("n"))
        .where(Article.category_code.is_not(None))
        .group_by(Article.category_code)
        .order_by(func.count().desc())
        .limit(6)
    ).all()
    top_categories = [{"category": code, "count": n} for code, n in category_rows]

    # Most-active sources by recent article volume.
    source_rows = db.execute(
        select(Source.name, func.count(Article.id).label("n"))
        .join(Article, Article.source_id == Source.id)
        .where(Article.created_at >= since)
        .group_by(Source.name)
        .order_by(func.count(Article.id).desc())
        .limit(5)
    ).all()
    top_sources = [{"source": name, "count": n} for name, n in source_rows]

    return {
        "articles_total": total_articles,
        "articles_last_24h": articles_24h,
        "events_total": total_events,
        "events_open": open_events,
        "sources_total": total_sources,
        "users_total": total_users,
        "articles_per_day": articles_per_day,
        "top_categories": top_categories,
        "top_sources": top_sources,
    }


def get_article_detail(db: Session, article_id: uuid.UUID) -> dict | None:
    """Full article content plus everything it belongs to (event, source)."""
    row = db.execute(
        select(Article, Source.name, Source.credibility_score)
        .join(Source, Source.id == Article.source_id)
        .where(Article.id == article_id)
    ).first()
    if row is None:
        return None
    article, source_name, credibility = row

    event = None
    if article.event_id:
        event = db.get(Event, article.event_id)
    else:
        linked = db.execute(
            select(Event)
            .join(EventArticle, EventArticle.event_id == Event.id)
            .where(EventArticle.article_id == article.id)
            .limit(1)
        ).scalar_one_or_none()
        event = linked

    return {
        "article": article,
        "source_name": source_name,
        "credibility": credibility,
        "event": event,
    }


def compute_event_momentum(db: Session, event_id: uuid.UUID) -> dict | None:
    """Trend detection: is the event's coverage accelerating or decelerating?

    Compares article volume in the most recent 24h window vs. the previous
    24h window, classified into rising / steady / cooling / quiet.
    """
    event = db.get(Event, event_id)
    if event is None:
        return None

    last = event.last_updated or event.created_at or datetime.now(UTC)
    prev_start = last - timedelta(hours=48)
    prev_end = last - timedelta(hours=24)

    recent = (
        db.execute(
            select(func.count())
            .select_from(EventArticle)
            .where(
                EventArticle.event_id == event_id,
                EventArticle.added_at > last - timedelta(hours=24),
            )
        ).scalar_one()
        or 0
    )
    previous = (
        db.execute(
            select(func.count())
            .select_from(EventArticle)
            .where(
                EventArticle.event_id == event_id,
                EventArticle.added_at > prev_start,
                EventArticle.added_at <= prev_end,
            )
        ).scalar_one()
        or 0
    )
    total = (
        db.execute(
            select(func.count()).select_from(EventArticle).where(EventArticle.event_id == event_id)
        ).scalar_one()
        or 0
    )

    if recent == 0 and previous == 0:
        momentum, direction = 0.0, "quiet"
    elif recent == 0:
        momentum, direction = -1.0, "cooling"
    elif previous == 0:
        momentum, direction = 1.0, "rising"
    else:
        momentum = (recent - previous) / max(recent + previous, 1)
        if momentum > 0.15:
            direction = "rising"
        elif momentum < -0.15:
            direction = "cooling"
        else:
            direction = "steady"

    return {
        "event_id": event_id,
        "direction": direction,
        "momentum": round(momentum, 3),
        "recent_24h": recent,
        "previous_24h": previous,
        "total_articles": total,
        "measured_at": last.isoformat(),
    }


def list_trending_events(db: Session, limit: int = 10) -> list[dict]:
    """Momentum for every open event, hottest first."""
    events = list(
        db.execute(
            select(Event)
            .where(Event.status == "open")
            .order_by(Event.last_updated.desc())
            .limit(limit * 2)
        ).scalars()
    )
    scored = []
    for event in events:
        momentum = compute_event_momentum(db, event.id)
        if momentum is None:
            continue
        momentum["title"] = event.title
        momentum["summary"] = event.summary
        momentum["article_count"] = event.article_count
        momentum["confidence"] = event.confidence
        scored.append(momentum)
    scored.sort(key=lambda m: m["momentum"], reverse=True)
    return scored[:limit]


def compare_sources(db: Session, query: str, source_a: str, source_b: str) -> dict:
    """Cross-source comparison: how two outlets cover the same topic.

    Uses full-text ILIKE matching over titles/descriptions, grouped by source.
    """
    pattern = f"%{query}%"

    def _source_by_name(name: str) -> Source | None:
        return db.execute(
            select(Source).where(func.lower(Source.name) == name.lower().strip())
        ).scalar_one_or_none()

    src_a = _source_by_name(source_a)
    src_b = _source_by_name(source_b)
    missing = [name for name, src in ((source_a, src_a), (source_b, src_b)) if src is None]
    if missing:
        return {"error": f"unknown source(s): {', '.join(missing)}"}

    def _coverage(source: Source) -> dict:
        articles = list(
            db.execute(
                select(Article)
                .where(
                    Article.source_id == source.id,
                    (Article.title.ilike(pattern)) | (Article.description.ilike(pattern)),
                )
                .order_by(Article.published_at.desc())
                .limit(50)
            ).scalars()
        )
        if not articles:
            return {
                "source": source.name,
                "credibility": source.credibility_score,
                "article_count": 0,
                "latest": None,
                "top_keywords": [],
            }
        keywords: list[str] = []
        for article in articles[:20]:
            text = f"{article.title or ''} {article.description or ''}".lower()
            keywords.extend(word for word in text.split() if len(word) > 4 and word.isalpha())
        latest = articles[0]
        return {
            "source": source.name,
            "credibility": source.credibility_score,
            "article_count": len(articles),
            "latest": {
                "article_id": latest.id,
                "title": latest.title,
                "published_at": latest.published_at.isoformat() if latest.published_at else None,
            },
            "top_keywords": [word for word, _n in Counter(keywords).most_common(8)],
        }

    return {
        "query": query,
        "source_a": _coverage(src_a),
        "source_b": _coverage(src_b),
    }
