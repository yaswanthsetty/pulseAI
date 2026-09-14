"""Unit tests for notification delivery (in-app, webhook, email fallback)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from backend.db.models import (
    Event,
    NotificationDelivery,
    NotificationRule,
    User,
)
from backend.modules.notifications import service as notif
from sqlalchemy import select


@pytest.fixture
def user(db, client, make_user):
    email = f"notif-{uuid.uuid4().hex[:8]}@example.com"
    make_user(email=email)
    return db.execute(select(User).where(User.email == email)).scalars().one()


@pytest.fixture
def event(db):
    return Event(
        title="Amazon announces new AI datacenter",
        summary="Amazon invests in AI infrastructure.",
        confidence=0.9,
        status="open",
        article_count=3,
        created_at=datetime.now(UTC),
        last_updated=datetime.now(UTC),
    )


def _rule(db, user, channel: str) -> NotificationRule:
    rule = NotificationRule(
        user_id=user.id,
        keyword_or_topic="amazon",
        category_code=None,
        channel=channel,
        is_active=True,
    )
    db.add(rule)
    db.flush()
    return rule


class TestDeliverEvent:
    def test_in_app_delivery_recorded(self, db, user, event):
        db.add(event)
        rule = _rule(db, user, "in_app")
        db.commit()

        delivered = notif.deliver_event(db, event, [(rule, user.email)])
        assert delivered == 1
        row = db.execute(select(NotificationDelivery)).scalars().one()
        assert row.channel == "in_app"
        assert row.status == "sent"
        assert row.event_id == event.id
        assert row.user_id == user.id

    def test_webhook_without_url_fails_gracefully(self, db, user, event, monkeypatch):
        monkeypatch.setattr(notif.settings, "notification_webhook_url", None)
        db.add(event)
        rule = _rule(db, user, "webhook")
        db.commit()

        delivered = notif.deliver_event(db, event, [(rule, user.email)])
        assert delivered == 1  # delivered = attempted
        row = db.execute(select(NotificationDelivery)).scalars().one()
        assert row.status == "failed"
        assert "webhook_url" in (row.detail or "") or "configured" in (row.detail or "")

    def test_webhook_success(self, db, user, event, monkeypatch):
        monkeypatch.setattr(
            notif.settings, "notification_webhook_url", "http://hook.example/ingest"
        )

        class _Resp:
            status_code = 200

        with patch.object(notif.httpx, "post", return_value=_Resp()) as mock_post:
            db.add(event)
            rule = _rule(db, user, "webhook")
            db.commit()
            delivered = notif.deliver_event(db, event, [(rule, user.email)])

        assert delivered == 1
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert payload["event_title"].startswith("Amazon")
        row = db.execute(select(NotificationDelivery)).scalars().one()
        assert row.status == "sent"

    def test_email_without_smtp_fails_gracefully(self, db, user, event, monkeypatch):
        monkeypatch.setattr(notif.settings, "smtp_host", None)
        db.add(event)
        rule = _rule(db, user, "email")
        db.commit()

        delivered = notif.deliver_event(db, event, [(rule, user.email)])
        assert delivered == 1
        row = db.execute(select(NotificationDelivery)).scalars().one()
        assert row.status == "failed"
        assert "SMTP" in (row.detail or "")

    def test_one_bad_rule_does_not_block_others(self, db, user, event):
        db.add(event)
        bad = _rule(db, user, "email")  # SMTP unconfigured -> recorded failure
        good = _rule(db, user, "in_app")
        db.commit()

        delivered = notif.deliver_event(db, event, [(bad, user.email), (good, user.email)])
        assert delivered == 2
        rows = db.execute(select(NotificationDelivery)).scalars().all()
        assert {r.status for r in rows} == {"sent", "failed"}

    def test_delivery_exception_contained(self, db, user, event):
        db.add(event)
        rule = _rule(db, user, "in_app")
        db.commit()

        with patch.object(notif, "_send_in_app", side_effect=RuntimeError("boom")):
            delivered = notif.deliver_event(db, event, [(rule, user.email)])

        assert delivered == 0
        row = db.execute(select(NotificationDelivery)).scalars().one()
        assert row.status == "failed"
        assert "unexpected delivery error" in (row.detail or "")
