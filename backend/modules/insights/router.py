"""Insights API: dashboard stats, article detail, trends, source comparison.

Stats / trending are open (guest-accessible, rate-limited) browse endpoints;
the article-detail endpoint also reports the caller's bookmark state.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.db.models import Bookmark, User
from backend.modules.auth.deps import get_optional_user
from backend.modules.insights import service
from backend.modules.insights.schemas import (
    ArticleDetail,
    ComparisonResponse,
    MomentumEntry,
    StatsResponse,
    TrendingResponse,
)

router = APIRouter(prefix="/insights", tags=["insights"])


def _article_bookmarked(db: Session, user: User | None, article_id: uuid.UUID) -> bool:
    if user is None:
        return False
    row = db.execute(
        select(Bookmark.article_id).where(
            Bookmark.user_id == user.id, Bookmark.article_id == article_id
        )
    ).scalar_one_or_none()
    return row is not None


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    """Pipeline overview: article/event/source counts, 14-day volume, top categories."""
    return StatsResponse.model_validate(service.get_stats(db))


@router.get("/trending", response_model=TrendingResponse)
def get_trending(
    limit: int = Query(default=10, ge=1, le=25),
    db: Session = Depends(get_db),
):
    """Open events ranked by coverage momentum (rising > steady > cooling)."""
    items = service.list_trending_events(db, limit)
    return TrendingResponse(items=[MomentumEntry.model_validate(item) for item in items])


@router.get("/compare", response_model=ComparisonResponse)
def compare_sources(
    q: str = Query(min_length=2, max_length=200),
    source_a: str = Query(alias="a"),
    source_b: str = Query(alias="b"),
    db: Session = Depends(get_db),
):
    """Cross-source comparison of how two outlets cover the same topic."""
    result = service.compare_sources(db, q, source_a, source_b)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return ComparisonResponse.model_validate(result)


@router.get("/articles/{article_id}", response_model=ArticleDetail)
def get_article_detail(
    article_id: uuid.UUID,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Full article content plus its source, credibility, and event membership."""
    detail = service.get_article_detail(db, article_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Article not found")
    article = detail["article"]
    event = detail["event"]
    return ArticleDetail(
        id=article.id,
        title=article.title,
        author=article.author,
        description=article.description,
        content_preview=article.content_preview,
        content=article.content_preview,
        url=article.url,
        image_url=article.image_url,
        language_code=article.language_code,
        category_code=article.category_code,
        published_at=article.published_at,
        source=detail["source_name"],
        credibility=detail["credibility"],
        event=({"id": event.id, "title": event.title, "status": event.status} if event else None),
        bookmarked=_article_bookmarked(db, user, article.id),
    )
