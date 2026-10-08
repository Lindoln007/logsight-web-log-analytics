"""
Shared query scoping and result caching for the analytics services.

Scoping
-------
``entries_for(dataset_id, owner)`` confirms ownership with one primary-key
lookup and then returns log entries filtered by ``dataset_id`` alone.  The
services used to filter on ``dataset__owner`` in every query, which added a
join to each one and kept the database from using the dataset-prefixed
indexes directly.

Caching
-------
A dataset never changes after it has been parsed, so anything computed from
it can be kept for a long time.  ``cached_result`` stores a function's return
value under a key made of the dataset id, the dataset's *cache version* and
the call's arguments.  ``bump_version`` retires every cached result for a
dataset at once (used when its entries change or analytics are recalculated).
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

#: Results derived from an immutable dataset can live a long time.
RESULT_TTL = 60 * 60 * 24 * 7        # 7 days
_VERSION_TTL = 60 * 60 * 24 * 30


def owns(dataset_id: int, owner) -> bool:
    """True when *dataset_id* exists and belongs to *owner*."""
    from logs.models import Dataset

    owner_id = getattr(owner, "pk", owner)
    return Dataset.objects.filter(pk=dataset_id, owner_id=owner_id).exists()


def entries_for(dataset_id: int, owner):
    """Log entries of *dataset_id*, or an empty queryset if *owner* does not own it."""
    from logs.models import LogEntry

    if not owns(dataset_id, owner):
        return LogEntry.objects.none()
    return LogEntry.objects.filter(dataset_id=dataset_id)


# ---------------------------------------------------------------------------
# Versioned result cache
# ---------------------------------------------------------------------------

def _version_key(dataset_id: int) -> str:
    return f"dataset:version:{dataset_id}"


def dataset_version(dataset_id: int) -> int:
    version = cache.get(_version_key(dataset_id))
    if version is None:
        version = 1
        cache.set(_version_key(dataset_id), version, _VERSION_TTL)
    return version


def bump_version(dataset_id: int) -> None:
    """Retire every ``cached_result`` entry for *dataset_id*."""
    cache.set(_version_key(dataset_id), dataset_version(dataset_id) + 1, _VERSION_TTL)


def result_key(prefix: str, dataset_id: int, *parts) -> str:
    raw = ":".join(str(p) for p in parts)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16] if raw else "-"
    return f"result:{prefix}:{dataset_id}:v{dataset_version(dataset_id)}:{digest}"


def cached_result(prefix: str):
    """
    Cache ``fn(dataset_id, owner, *args, **kwargs)`` per dataset and arguments.

    Ownership is checked on every call, before the cache is consulted, so a
    cached result is never served to a user who does not own the dataset; for
    them the wrapped function runs and returns its normal empty result.
    """
    def decorator(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Bind the call so positional and keyword spellings of the same
            # arguments (and omitted defaults) share one cache entry.
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            params = dict(bound.arguments)
            dataset_id, owner = params.pop("dataset_id"), params.pop("owner")
            if not owns(dataset_id, owner):
                return fn(*args, **kwargs)
            key = result_key(prefix, dataset_id, sorted(params.items()))
            hit = cache.get(key)
            if hit is not None:
                return hit
            value = fn(*args, **kwargs)
            try:
                cache.set(key, value, RESULT_TTL)
            except Exception:  # an unpicklable or oversized value must not break the page
                logger.exception("Could not cache %s for dataset_id=%s", prefix, dataset_id)
            return value
        return wrapper
    return decorator
