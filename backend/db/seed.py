"""Idempotent seeding of reference/lookup data (categories, countries, ...).

Run at application startup; safe to call repeatedly.
"""

import logging

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.db.seed_data import SEED_MODELS

logger = logging.getLogger(__name__)


def seed_reference_data(db: Session) -> None:
    """Insert lookup rows that are missing, leaving existing rows untouched."""
    for model, rows in SEED_MODELS.items():
        for row in rows:
            stmt = insert(model).values(**row).on_conflict_do_nothing()
            db.execute(stmt)
    db.commit()
    logger.info("reference data seeded (%d tables)", len(SEED_MODELS))
