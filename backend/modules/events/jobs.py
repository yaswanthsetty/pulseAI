"""RQ job entrypoints for the events module (consumed by ``pulseai-worker``).

``cluster_article_job`` is the fast-path adapter (FR-18): it runs the
centroid match for one freshly-embedded article. Thin session handling only —
all logic lives in the service layer.
"""

import logging

from backend.core.database import SessionLocal
from backend.modules.events.service import match_article_to_event

logger = logging.getLogger(__name__)


def cluster_article_job(article_id: str) -> dict:
    """RQ job: fast-path match one article against open-event centroids."""
    db = SessionLocal()
    try:
        event = match_article_to_event(db, article_id)
        return {
            "status": "matched" if event is not None else "unmatched",
            "event_id": str(event.id) if event is not None else None,
        }
    finally:
        db.close()
