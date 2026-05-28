"""
Invite token service for LogSight.

Admins generate single-use invite links that are emailed to recipients.
The registration page only accepts new users who arrive via a valid token.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

_EXPIRY_DAYS = 7


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_invite(email: str, role: str, invited_by) -> dict:
    """
    Create an InviteToken for *email*, send the invite email, and return
    the serialised token.

    Only admin users may call this; the caller is responsible for enforcing
    that check before calling this function.
    """
    from logs.models import InviteToken

    email = email.strip().lower()

    # Invalidate any previous unused invites for the same address so there
    # is never more than one live token per email.
    InviteToken.objects.filter(email=email, used=False).update(
        expires_at=timezone.now()
    )

    token = InviteToken.objects.create(
        email=email,
        role=role,
        invited_by=invited_by,
        expires_at=timezone.now() + timedelta(days=_EXPIRY_DAYS),
    )

    _send_invite_email(token)
    logger.info(
        "Invite created: id=%s email=%s role=%s by user_id=%s",
        token.pk, email, role, invited_by.pk,
    )
    return _serialize(token)


def validate_invite(token_str: str):
    """
    Return the InviteToken instance if *token_str* is a valid, unused,
    non-expired token, otherwise return None.
    """
    from logs.models import InviteToken

    try:
        token = InviteToken.objects.select_related("invited_by").get(token=token_str)
    except (InviteToken.DoesNotExist, ValueError):
        return None

    return token if token.is_valid else None


def redeem_invite(token_str: str, user) -> None:
    """
    Mark *token_str* as used and assign the pre-set role to *user*.
    Called immediately after successful registration.
    """
    from logs.models import InviteToken
    from logs.services.rbac import get_or_create_profile

    try:
        token = InviteToken.objects.get(token=token_str, used=False)
    except InviteToken.DoesNotExist:
        return

    token.used    = True
    token.used_at = timezone.now()
    token.used_by = user
    token.save(update_fields=["used", "used_at", "used_by"])

    profile = get_or_create_profile(user)
    profile.role = token.role
    profile.save(update_fields=["role"])

    logger.info(
        "Invite redeemed: token_id=%s user_id=%s role=%s",
        token.pk, user.pk, token.role,
    )


def list_invites(admin_user) -> list[dict]:
    """Return all invites created by any admin, newest first."""
    from logs.models import InviteToken

    tokens = InviteToken.objects.select_related("invited_by", "used_by").all()
    return [_serialize(t) for t in tokens]


def revoke_invite(invite_id: int, admin_user) -> bool:
    """
    Expire an unused invite immediately.  Returns True if the record was
    found and updated, False if it was already used or does not exist.
    """
    from logs.models import InviteToken

    updated = InviteToken.objects.filter(
        pk=invite_id, used=False
    ).update(expires_at=timezone.now())
    return updated > 0


# ---------------------------------------------------------------------------
# Email helper
# ---------------------------------------------------------------------------

def _send_invite_email(token) -> None:
    """Send the invite link to the recipient. Fails silently on error."""
    try:
        from django.conf import settings
        from django.core.mail import send_mail

        invite_url = (
            getattr(settings, "LOGSIGHT_BASE_URL", "http://127.0.0.1:8000")
            + f"/accounts/register/?invite={token.token}"
        )
        role_label = token.role.capitalize()
        inviter    = token.invited_by.username

        subject = f"You've been invited to LogSight as {role_label}"
        body = (
            f"Hello,\n\n"
            f"{inviter} has invited you to join LogSight as a {role_label}.\n\n"
            f"Click the link below to create your account. "
            f"This link expires in {_EXPIRY_DAYS} days.\n\n"
            f"{invite_url}\n\n"
            f"If you did not expect this invitation, you can ignore this email.\n\n"
            f"— LogSight"
        )

        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[token.email],
            fail_silently=True,
        )
    except Exception:
        logger.exception("Failed to send invite email for token_id=%s", token.pk)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _serialize(token) -> dict:
    return {
        "id":          token.pk,
        "token":       str(token.token),
        "email":       token.email,
        "role":        token.role,
        "invited_by":  token.invited_by.username,
        "created_at":  token.created_at.isoformat(),
        "expires_at":  token.expires_at.isoformat(),
        "used":        token.used,
        "used_at":     token.used_at.isoformat() if token.used_at else None,
        "used_by":     token.used_by.username if token.used_by else None,
        "is_valid":    token.is_valid,
    }
