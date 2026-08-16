"""Admin + read API for sources and articles.

Endpoints mounted under ``/api/v1`` by the API router. RBAC per spec §22:
source *management* is Admin-only, source *listing* requires any authenticated
user, and article browsing is open to guests.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import queue
from backend.core.database import get_db
from backend.core.pagination import Page, paginate
from backend.db.models import Article, Source, User
from backend.modules.auth.deps import require_role
from backend.modules.ingestion import service
from backend.modules.ingestion.schemas import ArticleRead, SourceCreate, SourceRead, SourceUpdate

router = APIRouter(tags=["ingestion"])


@router.get("/sources", response_model=Page[SourceRead])
def list_sources(
    status: str | None = Query(default=None, pattern="^(active|degraded|disabled)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role("user")),
):
    """List sources with their health/status (any authenticated user)."""
    statement = select(Source).order_by(Source.name)
    if status:
        statement = statement.where(Source.status == status)
    return paginate(db, statement, page, page_size, to_model=SourceRead)


@router.post("/sources", response_model=SourceRead, status_code=201)
def create_source(
    payload: SourceCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    """Add a source; the feed is fetched and validated before activation (FR-4)."""
    try:
        # mode="json" coerces AnyHttpUrl fields to plain strings for the DB.
        return service.create_source(db, **payload.model_dump(mode="json"))
    except service.FeedValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/sources/{source_id}", response_model=SourceRead)
def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    """Update credibility, poll interval, status, or feed URL."""
    try:
        return service.update_source(
            db, source_id, payload.model_dump(exclude_unset=True, mode="json")
        )
    except service.SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Source not found") from exc
    except service.FeedValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sources/{source_id}/poll", status_code=202)
def trigger_poll(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    """Manually queue an immediate poll of a source (ops/testing convenience)."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    job_id = queue.enqueue_poll(str(source_id))
    return {"status": "queued", "job_id": job_id}


@router.get("/articles", response_model=Page[ArticleRead])
def list_articles(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    source_id: uuid.UUID | None = Query(default=None),
    category: str | None = Query(default=None),
    country: str | None = Query(default=None),
    language: str | None = Query(default=None),
    event_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List articles with filters (spec §20), newest first."""
    statement = select(Article).order_by(Article.published_at.desc())
    if date_from is not None:
        statement = statement.where(Article.published_at >= date_from)
    if date_to is not None:
        statement = statement.where(Article.published_at <= date_to)
    if source_id is not None:
        statement = statement.where(Article.source_id == source_id)
    if category is not None:
        statement = statement.where(Article.category_code == category)
    if country is not None:
        statement = statement.where(Article.country_code == country)
    if language is not None:
        statement = statement.where(Article.language_code == language)
    if event_id is not None:
        statement = statement.where(Article.event_id == event_id)
    return paginate(db, statement, page, page_size, to_model=ArticleRead)


@router.get("/articles/{article_id}", response_model=ArticleRead)
def get_article(article_id: uuid.UUID, db: Session = Depends(get_db)):
    """Full article detail (spec §20)."""
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return article
