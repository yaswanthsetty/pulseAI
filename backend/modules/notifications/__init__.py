"""Notifications module: turns notification-rule matches into deliveries."""

from backend.modules.notifications.service import deliver_event

__all__ = ["deliver_event"]
