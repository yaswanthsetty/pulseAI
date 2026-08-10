"""Default sources seeded in development (``SEED_DEFAULT_SOURCES=true``).

These match the original skeleton's feeds. Production deployments should
manage sources through the admin API instead.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.models import Source

logger = logging.getLogger(__name__)

DEFAULT_SOURCES: list[dict] = [
    {
        "name": "TechCrunch AI",
        "rss_url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "website": "https://techcrunch.com",
        "credibility_score": 0.85,
        "category_code": "technology",
    },
    {
        "name": "The Guardian Technology",
        "rss_url": "https://www.theguardian.com/world/technology/rss",
        "website": "https://www.theguardian.com",
        "credibility_score": 0.95,
        "category_code": "technology",
    },
    {
        "name": "BBC Science & Tech",
        "rss_url": "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "website": "https://www.bbc.com",
        "credibility_score": 0.95,
        "category_code": "technology",
    },
]


def seed_default_sources(db: Session) -> int:
    """Insert default sources that do not already exist; returns count added."""
    added = 0
    for data in DEFAULT_SOURCES:
        exists = db.execute(select(Source.id).where(Source.rss_url == data["rss_url"])).first()
        if exists:
            continue
        db.add(
            Source(
                **data,
                credibility_method="manual",
                status="active",
                poll_interval_minutes=settings.default_poll_interval_minutes,
            )
        )
        added += 1
    db.commit()
    if added:
        logger.info("seeded %d default sources", added)
    return added
