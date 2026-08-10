"""Audit log helper (spec §10 ``audit_log`` table)."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import AuditLog

logger = logging.getLogger(__name__)


def write_audit(
    db: Session,
    action: str,
    *,
    user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one row to ``audit_log``. Never raises — auditing must not break flows."""
    try:
        db.add(
            AuditLog(
                user_id=user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip_address=ip_address,
                metadata=metadata,
            )
        )
        db.commit()
    except Exception:  # pragma: no cover - defensive by design
        db.rollback()
        logger.exception("failed to write audit log entry: action=%s", action)
