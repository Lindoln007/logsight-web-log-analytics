"""
Django signals for the logs app.

Registers a post_save handler on the User model that automatically creates
a UserProfile for every new user.  If the user being created is the very
first user in the system, their profile is given the "admin" role.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created: bool, **kwargs) -> None:
    """Auto-create a UserProfile whenever a new User record is saved."""
    if not created:
        return

    from logs.models import UserProfile

    # Determine role: first-ever user becomes admin.
    role = (
        UserProfile.ROLE_ADMIN
        if User.objects.count() == 1
        else UserProfile.ROLE_PENDING
    )

    profile, was_created = UserProfile.objects.get_or_create(
        user=instance,
        defaults={"role": role},
    )

    if was_created:
        logger.info(
            "Auto-created UserProfile for user_id=%s username=%s role=%s",
            instance.pk,
            instance.username,
            role,
        )
