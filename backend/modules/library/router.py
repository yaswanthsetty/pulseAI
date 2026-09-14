"""Library API: saved searches, bookmarks, notification rules, in-app inbox.

All routes require authentication (JWT or API key); every row is scoped to
the authenticated principal, so one user can never read or delete another's.
The spec §20 resources are nested under ``/api/v1/library``.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.db.models import User
from backend.modules.auth.deps import get_current_user
from backend.modules.library import service
from backend.modules.library.schemas import (
    BookmarkListResponse,
    BookmarkOut,
    NotificationDeliveryListResponse,
    NotificationDeliveryOut,
    NotificationRuleCreate,
    NotificationRuleListResponse,
    NotificationRuleOut,
    SavedSearchCreate,
    SavedSearchListResponse,
    SavedSearchOut,
)

router = APIRouter(prefix="/library", tags=["library"])


# ── Saved searches ──────────────────────────────────────────────────────────


@router.get("/searches", response_model=SavedSearchListResponse)
def list_saved_searches(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List the authenticated user's saved searches."""
    return SavedSearchListResponse(
        items=[
            SavedSearchOut.model_validate(row) for row in service.list_saved_searches(db, user.id)
        ]
    )


@router.post("/searches", response_model=SavedSearchOut, status_code=201)
def create_saved_search(
    payload: SavedSearchCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a query (and optional filters) for one-click re-run."""
    row = service.create_saved_search(db, user.id, payload.query, payload.filters)
    return SavedSearchOut.model_validate(row)


@router.delete("/searches/{search_id}", status_code=204)
def delete_saved_search(
    search_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one of the user's saved searches (404 for other users' rows)."""
    if not service.delete_saved_search(db, user.id, search_id):
        raise HTTPException(status_code=404, detail="Saved search not found")


# ── Bookmarks ───────────────────────────────────────────────────────────────


@router.get("/bookmarks", response_model=BookmarkListResponse)
def list_bookmarks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List the authenticated user's bookmarks, newest first."""
    rows = service.list_bookmarks(db, user.id)
    items = [
        BookmarkOut(
            article_id=r.article_id,
            title=r.title,
            url=r.url,
            description=r.description,
            author=r.author,
            published_at=r.published_at,
            category=r.category_code,
            source=r.name,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return BookmarkListResponse(items=items)


@router.put("/bookmarks/{article_id}", status_code=204)
def add_bookmark(
    article_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bookmark an article (idempotent)."""
    if not service.add_bookmark(db, user.id, article_id):
        raise HTTPException(status_code=404, detail="Article not found")


@router.delete("/bookmarks/{article_id}", status_code=204)
def remove_bookmark(
    article_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a bookmark (404 if not bookmarked)."""
    if not service.remove_bookmark(db, user.id, article_id):
        raise HTTPException(status_code=404, detail="Bookmark not found")


# ── Notification rules ──────────────────────────────────────────────────────


@router.get("/notification-rules", response_model=NotificationRuleListResponse)
def list_notification_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List the authenticated user's notification rules."""
    return NotificationRuleListResponse(
        items=[
            NotificationRuleOut.model_validate(row)
            for row in service.list_notification_rules(db, user.id)
        ]
    )


@router.post("/notification-rules", response_model=NotificationRuleOut, status_code=201)
def create_notification_rule(
    payload: NotificationRuleCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a notification rule (keyword and/or category)."""
    try:
        row = service.create_notification_rule(
            db, user.id, payload.keyword_or_topic, payload.category_code, payload.channel
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NotificationRuleOut.model_validate(row)


@router.delete("/notification-rules/{rule_id}", status_code=204)
def delete_notification_rule(
    rule_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one of the user's notification rules."""
    if not service.delete_notification_rule(db, user.id, rule_id):
        raise HTTPException(status_code=404, detail="Notification rule not found")


# ── In-app notification inbox ───────────────────────────────────────────────


@router.get("/notifications", response_model=NotificationDeliveryListResponse)
def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The in-app notification inbox, newest first, with an unread count."""
    rows, unread = service.list_deliveries(db, user.id, limit)
    return NotificationDeliveryListResponse(
        items=[NotificationDeliveryOut.model_validate(r) for r in rows],
        unread=unread,
    )


@router.post("/notifications/read-all")
def mark_notifications_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mark every in-app notification as read."""
    return {"updated": service.mark_deliveries_read(db, user.id)}
