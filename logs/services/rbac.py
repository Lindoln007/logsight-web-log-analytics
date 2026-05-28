"""
Role-Based Access Control (RBAC) service for LogSight.

Manages UserProfile creation, role assignment, and role-check decorators.
All model imports are local to avoid circular dependency issues.
"""
from __future__ import annotations

import functools
import logging
from typing import Any

from django.http import JsonResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def get_or_create_profile(user) -> Any:
    """
    Return (or create) the UserProfile for *user*.

    If this is the very first user in the system (User.objects.count() == 1),
    the profile is created with the "admin" role.  All subsequent users default
    to "pending" (awaiting role assignment).

    Returns the UserProfile instance.
    """
    from logs.models import UserProfile
    from django.contrib.auth import get_user_model

    User = get_user_model()

    profile, created = UserProfile.objects.get_or_create(
        user=user,
        defaults={"role": UserProfile.ROLE_PENDING},
    )

    if created and User.objects.count() == 1:
        # This is the only user ever created — make them the system admin.
        profile.role = UserProfile.ROLE_ADMIN
        profile.save(update_fields=["role"])
        logger.info("Auto-assigned ADMIN role to first user user_id=%s", user.pk)

    return profile


def get_all_users_with_profiles() -> list[dict]:
    """
    Return a list of all users with their profile data.

    Intended for the admin user-management page.
    """
    from logs.models import UserProfile
    from django.contrib.auth import get_user_model

    User = get_user_model()

    # Fetch profiles with their users in a single query.
    profiles = (
        UserProfile.objects
        .select_related("user")
        .order_by("created_at")
    )

    # Build a set of users that have profiles so we can detect profileless users.
    profiled_user_ids = {p.user_id for p in profiles}

    result = [_serialize_profile(p) for p in profiles]

    # Include users that somehow lack a profile (e.g., created before signals).
    for user in User.objects.exclude(pk__in=profiled_user_ids).order_by("date_joined"):
        result.append(
            {
                "user_id": user.pk,
                "username": user.username,
                "email": user.email,
                "role": None,
                "is_staff": user.is_staff,
                "date_joined": user.date_joined.isoformat(),
                "profile_id": None,
            }
        )

    return result


def assign_role(target_user_id: int, role: str, requesting_user) -> dict:
    """
    Assign *role* to the user identified by *target_user_id*.

    Only users whose own profile has the "admin" role may assign roles.

    Args:
        target_user_id:   PK of the user whose role should change.
        role:             New role string (must be one of UserProfile.ROLE_CHOICES keys).
        requesting_user:  The authenticated user making the request.

    Returns a dict with the updated profile data.

    Raises PermissionError if *requesting_user* is not an admin.
    Raises ValueError for invalid role values.
    Raises UserProfile.DoesNotExist / User.DoesNotExist on missing records.
    """
    from logs.models import UserProfile
    from django.contrib.auth import get_user_model

    User = get_user_model()

    # Enforce that the requesting user is an admin.
    requester_profile = get_or_create_profile(requesting_user)
    if not requester_profile.is_admin:
        raise PermissionError("Only admins can assign roles.")

    valid_roles = {choice[0] for choice in UserProfile.ROLE_CHOICES}
    if role not in valid_roles:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {sorted(valid_roles)}.")

    target_user = User.objects.get(pk=target_user_id)
    profile = get_or_create_profile(target_user)
    profile.role = role
    profile.save(update_fields=["role"])

    logger.info(
        "Role assigned: user_id=%s → %s by admin user_id=%s",
        target_user_id,
        role,
        requesting_user.pk,
    )
    return _serialize_profile(profile)


# ---------------------------------------------------------------------------
# Decorator factory
# ---------------------------------------------------------------------------

def require_role(*roles: str):
    """
    View decorator factory that restricts access to users whose profile role
    is in *roles*.

    Usage::

        @require_role("admin", "analyst")
        def my_view(request, ...):
            ...

    Returns 403 JSON if the role check fails or the user has no profile.
    Returns 401 JSON if the user is not authenticated.
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({"error": "Authentication required."}, status=401)
            try:
                profile = get_or_create_profile(request.user)
            except Exception:
                return JsonResponse({"error": "User profile not found."}, status=403)

            if profile.role not in roles:
                return JsonResponse(
                    {
                        "error": "You do not have permission to perform this action.",
                        "required_roles": list(roles),
                        "your_role": profile.role,
                    },
                    status=403,
                )
            return view_func(request, *args, **kwargs)

        return _wrapped
    return decorator


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_profile(profile) -> dict:
    return {
        "profile_id": profile.pk,
        "user_id": profile.user_id,
        "username": profile.user.username,
        "email": profile.user.email,
        "role": profile.role,
        "is_staff": profile.user.is_staff,
        "date_joined": profile.user.date_joined.isoformat(),
        "created_at": profile.created_at.isoformat(),
    }
