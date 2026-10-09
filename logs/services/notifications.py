"""
Notification service for LogSight.

Provides helpers to create, query, and mark notifications for a user.
All model imports are local to avoid circular dependency issues.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_notification(
    user,
    message: str,
    severity: str = "info",
    notification_type: str = "general",
    dataset=None,
) -> Any:
    """
    Create and return a new Notification for *user*.

    Args:
        user:              The Django user instance to notify.
        message:           Human-readable notification text.
        severity:          "info" | "warning" | "critical"
        notification_type: Arbitrary string categorising the notification
                           (e.g. "dataset_ready", "general").
        dataset:           Optional Dataset instance to associate.

    Returns the saved Notification instance.
    """
    from logs.models import Notification

    notification = Notification.objects.create(
        user=user,
        message=message,
        severity=severity,
        notification_type=notification_type,
        dataset=dataset,
    )
    logger.debug(
        "Created notification id=%s user=%s type=%s severity=%s",
        notification.pk,
        user.pk,
        notification_type,
        severity,
    )

    return notification


def get_notifications(user, unread_only: bool = False, limit: int = 50) -> list[dict]:
    """
    Return serialised notifications for *user*, newest first.

    Args:
        unread_only: When True, only return notifications where is_read=False.
        limit:       Maximum number of notifications to return.
    """
    from logs.models import Notification

    qs = Notification.objects.filter(user=user)
    if unread_only:
        qs = qs.filter(is_read=False)
    qs = qs.select_related("dataset").order_by("-created_at")[:limit]
    return [_serialize_notification(n) for n in qs]


def mark_read(notification_id: int, user) -> bool:
    """
    Mark a single notification as read.

    Returns True if the notification was found and updated, False otherwise.
    """
    from logs.models import Notification

    updated = Notification.objects.filter(pk=notification_id, user=user).update(is_read=True)
    return updated > 0


def mark_all_read(user) -> int:
    """
    Mark all unread notifications for *user* as read.

    Returns the number of records updated.
    """
    from logs.models import Notification

    return Notification.objects.filter(user=user, is_read=False).update(is_read=True)


def get_unread_count(user) -> int:
    """Return the count of unread notifications for *user*."""
    from logs.models import Notification

    return Notification.objects.filter(user=user, is_read=False).count()


def delete_notification(notification_id: int, user) -> bool:
    """Delete a single notification. Returns True if found and deleted."""
    from logs.models import Notification

    deleted, _ = Notification.objects.filter(pk=notification_id, user=user).delete()
    return deleted > 0


def delete_all_notifications(user) -> int:
    """Delete all notifications for *user*. Returns the number deleted."""
    from logs.models import Notification

    deleted, _ = Notification.objects.filter(user=user).delete()
    return deleted


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_notification(notification) -> dict:
    return {
        "id": notification.pk,
        "message": notification.message,
        "severity": notification.severity,
        "notification_type": notification.notification_type,
        "is_read": notification.is_read,
        "created_at": notification.created_at.isoformat(),
        "dataset_id": notification.dataset_id,
        "dataset_name": notification.dataset.name if notification.dataset else None,
    }
