"""Notification delivery (Phase 7).

Turns ``notification_rules`` matches into real deliveries:

* ``in_app`` — a row in ``notification_deliveries`` (the in-app inbox).
* ``webhook`` — POST the event payload to ``settings.notification_webhook_url``
  (a user-configured sink; failure recorded, never raised into clustering).
* ``email`` — SMTP via ``settings.smtp_*``; skipped with a ``failed`` delivery
  row when SMTP is not configured.

``deliver_event`` is the single entry point, called by the events module
after a slow-path event is committed. Every channel failure is contained:
delivery problems must never break clustering.
"""

import logging
import smtplib
import uuid
from email.mime.text import MIMEText

import httpx
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.models import Event, NotificationDelivery

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0


def _record(
    db: Session,
    rule_id: uuid.UUID,
    user_id: uuid.UUID,
    event: Event,
    channel: str,
    status: str,
    detail: str | None,
) -> None:
    db.add(
        NotificationDelivery(
            rule_id=rule_id,
            user_id=user_id,
            event_id=event.id,
            channel=channel,
            status=status,
            detail=detail,
        )
    )
    db.commit()


def _send_in_app(db: Session, rule, event: Event, title: str, summary: str) -> None:
    _record(db, rule.id, rule.user_id, event, "in_app", "sent", title or summary[:120])


def _send_webhook(db: Session, rule, event: Event, title: str, summary: str) -> None:
    url = settings.notification_webhook_url
    if not url:
        _record(
            db,
            rule.id,
            rule.user_id,
            event,
            "webhook",
            "failed",
            "no notification_webhook_url configured",
        )
        return
    payload = {
        "rule_id": str(rule.id),
        "event_id": str(event.id),
        "event_title": title,
        "event_summary": summary,
        "confidence": event.confidence,
        "article_count": event.article_count,
        "last_updated": event.last_updated.isoformat() if event.last_updated else None,
    }
    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT)
        ok = response.status_code < 400
        _record(
            db,
            rule.id,
            rule.user_id,
            event,
            "webhook",
            "sent" if ok else "failed",
            f"HTTP {response.status_code}",
        )
    except httpx.HTTPError as exc:
        _record(db, rule.id, rule.user_id, event, "webhook", "failed", str(exc)[:500])


def _send_email(db: Session, rule, event: Event, title: str, summary: str, to_email: str) -> None:
    host = settings.smtp_host
    if not host:
        _record(
            db,
            rule.id,
            rule.user_id,
            event,
            "email",
            "failed",
            "SMTP not configured (smtp_host empty)",
        )
        return
    body = (
        f"PulseAI matched an event for your rule:\n\n"
        f"{title}\n\n{summary}\n\n"
        f"Confidence: {event.confidence:.2f} | Articles: {event.article_count}\n"
    )
    message = MIMEText(body)
    message["Subject"] = f"PulseAI: {title[:120]}" if title else "PulseAI event alert"
    message["From"] = settings.smtp_from
    message["To"] = to_email
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(host, settings.smtp_port, timeout=_TIMEOUT)
        else:
            server = smtplib.SMTP(host, settings.smtp_port, timeout=_TIMEOUT)
            server.starttls()
        with server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or "")
            server.sendmail(settings.smtp_from, [to_email], message.as_string())
        _record(db, rule.id, rule.user_id, event, "email", "sent", to_email)
    except (smtplib.SMTPException, OSError) as exc:
        _record(db, rule.id, rule.user_id, event, "email", "failed", str(exc)[:500])


def deliver_event(db: Session, event: Event, matches: list) -> int:
    """Deliver one new event to every matched rule.

    ``matches`` is a list of ``(rule, user_email)`` tuples produced by
    ``events.service.matched_rules_with_users``. Returns delivered count.
    """
    title = event.title or ""
    summary = (event.summary or "")[:500]
    delivered = 0
    for rule, user_email in matches:
        try:
            if rule.channel == "in_app":
                _send_in_app(db, rule, event, title, summary)
                delivered += 1
            elif rule.channel == "webhook":
                _send_webhook(db, rule, event, title, summary)
                delivered += 1
            elif rule.channel == "email":
                _send_email(db, rule, event, title, summary, user_email)
                delivered += 1
        except Exception:  # noqa: BLE001 - one bad rule must not stop the rest
            logger.exception("notification delivery failed (rule=%s)", rule.id)
            _record(
                db,
                rule.id,
                rule.user_id,
                event,
                rule.channel,
                "failed",
                "unexpected delivery error",
            )
    if delivered:
        logger.info("delivered %d notification(s) for event %s", delivered, event.id)
    return delivered
