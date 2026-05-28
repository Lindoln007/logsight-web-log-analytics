"""
Analytics service for LogSight.

All public functions accept a ``dataset_id`` parameter so that every query is
strictly scoped to a single dataset.  No data bleeds across datasets.

Design principles
-----------------
* Database-level aggregation only — no Python-side number crunching on raw rows.
* Results are cached with Django's cache framework (configurable backend).
* The ``AnalyticsSummary`` model acts as a persistent cache written once after
  parsing and refreshed on demand.
* Safe defaults (zeros / empty lists) are returned for empty datasets so callers
  never receive None-propagation errors.
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import ExtractHour, TruncDay, TruncHour
from django.utils.dateparse import parse_datetime

from django.contrib.auth import get_user_model

from logs.models import AnalyticsSummary, Dataset, LogEntry

User = get_user_model()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: How long computed analytics are held in the cache (seconds).
ANALYTICS_CACHE_TTL: int = 60 * 15  # 15 minutes

#: Default and maximum numbers of top endpoints returned.
DEFAULT_TOP_ENDPOINTS_LIMIT: int = 10
MAX_TOP_ENDPOINTS_LIMIT: int = 100

#: Default maximum number of time-series buckets returned by get_request_trends.
DEFAULT_TRENDS_LIMIT: int = 168  # 7 days × 24 hours

# ---------------------------------------------------------------------------
# Cache key templates  (dataset-scoped so they never collide)
# ---------------------------------------------------------------------------

_CK_SUMMARY = "analytics:summary:{dataset_id}"
_CK_STATUS = "analytics:status_dist:{dataset_id}"
_CK_TRENDS = "analytics:trends:{dataset_id}:{interval}"
_CK_ENDPOINTS = "analytics:top_endpoints:{dataset_id}:{limit}"
_CK_HOURLY = "analytics:hourly:{dataset_id}"
_CK_METHODS = "analytics:methods:{dataset_id}"
_CK_ERRORS = "analytics:errors:{dataset_id}"


# ---------------------------------------------------------------------------
# Public analytics functions
# ---------------------------------------------------------------------------


def get_summary_metrics(dataset_id: int, owner: User) -> dict[str, Any]:
    """
    Return high-level aggregate metrics for a single dataset.

    A single aggregation query covers all scalar metrics so the database
    only performs one pass over the ``logentry`` table.

    Return shape::

        {
            "dataset_id": int,
            "total_requests": int,
            "total_errors": int,       # status_code >= 400
            "errors_4xx": int,
            "errors_5xx": int,
            "unique_ips": int,
            "error_rate": float,       # percentage, 0-100
            "avg_response_size": float | None,
            "total_bytes": int | None,
            "date_range": {
                "start": "ISO8601" | None,
                "end":   "ISO8601" | None,
                "duration_hours": float | None,
            },
        }
    """
    cache_key = _CK_SUMMARY.format(dataset_id=dataset_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    agg = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .aggregate(
            total_requests=Count("id"),
            total_errors=Count("id", filter=Q(error_flag=True)),
            errors_4xx=Count("id", filter=Q(status_code__gte=400, status_code__lt=500)),
            errors_5xx=Count("id", filter=Q(status_code__gte=500)),
            unique_ips=Count("ip_address", distinct=True),
            avg_response_size=Avg("response_size"),
            total_bytes=Sum("response_size"),
            date_start=Min("timestamp"),
            date_end=Max("timestamp"),
        )
    )

    total: int = agg["total_requests"] or 0
    total_errors: int = agg["total_errors"] or 0
    error_rate: float = round(total_errors / total * 100, 3) if total > 0 else 0.0

    date_start = agg["date_start"]
    date_end = agg["date_end"]
    duration_hours: float | None = None
    if date_start and date_end:
        duration_hours = round((date_end - date_start).total_seconds() / 3600, 2)

    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "total_requests": total,
        "total_errors": total_errors,
        "errors_4xx": agg["errors_4xx"] or 0,
        "errors_5xx": agg["errors_5xx"] or 0,
        "unique_ips": agg["unique_ips"] or 0,
        "error_rate": error_rate,
        "avg_response_size": (
            float(agg["avg_response_size"]) if agg["avg_response_size"] is not None else None
        ),
        "total_bytes": agg["total_bytes"],
        "date_range": {
            "start": date_start.isoformat() if date_start else None,
            "end": date_end.isoformat() if date_end else None,
            "duration_hours": duration_hours,
        },
    }

    cache.set(cache_key, result, ANALYTICS_CACHE_TTL)
    return result


def get_status_distribution(dataset_id: int, owner: User) -> dict[str, Any]:
    """
    Return request counts grouped by HTTP status code.

    Uses the ``(dataset, status_code)`` compound index for an efficient
    index-only scan.

    Return shape::

        {
            "dataset_id": int,
            "by_code": [
                {"status_code": 200, "count": 91234, "category": "2xx"},
                ...
            ],
            "by_category": {
                "1xx": 0,
                "2xx": 91234,
                "3xx": 512,
                "4xx": 3021,
                "5xx": 87,
                "other": 0,
            },
        }
    """
    cache_key = _CK_STATUS.format(dataset_id=dataset_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    qs = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .values("status_code")
        .annotate(count=Count("id"))
        .order_by("status_code")
    )

    category_counts: dict[str, int] = {
        "1xx": 0, "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0,
    }
    by_code: list[dict[str, Any]] = []

    for row in qs:
        code: int = row["status_code"]
        count: int = row["count"]
        cat = _status_category(code)
        category_counts[cat] += count
        by_code.append({"status_code": code, "count": count, "category": cat})

    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "by_code": by_code,
        "by_category": category_counts,
    }
    cache.set(cache_key, result, ANALYTICS_CACHE_TTL)
    return result


def get_request_trends(
    dataset_id: int,
    owner: User,
    interval: str = "hour",
    limit: int = DEFAULT_TRENDS_LIMIT,
) -> dict[str, Any]:
    """
    Return request counts bucketed into time intervals for time-series charting.

    Uses ``TruncHour`` / ``TruncDay`` at the database level — no Python-side
    grouping.  The query returns the *most recent* ``limit`` buckets so that
    callers always receive a bounded, chart-ready slice.

    Args:
        interval: ``"hour"`` (default) or ``"day"``.
        limit:    Maximum number of buckets to return (1–1000).

    Return shape::

        {
            "dataset_id": int,
            "interval": "hour",
            "total_buckets": int,
            "data": [
                {"timestamp": "2024-01-15T14:00:00+00:00", "count": 342},
                ...
            ],
        }
    """
    if interval not in ("hour", "day"):
        interval = "hour"
    limit = max(1, min(limit, 1000))

    cache_key = _CK_TRENDS.format(dataset_id=dataset_id, interval=interval)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    trunc_fn = TruncHour if interval == "hour" else TruncDay

    # ORDER BY DESC + LIMIT pushes work to the DB; we re-sort ascending in Python
    # after slicing so charts receive chronological order.
    qs = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .annotate(bucket=trunc_fn("timestamp"))
        .values("bucket")
        .annotate(count=Count("id"))
        .order_by("-bucket")[:limit]
    )

    rows = sorted(qs, key=lambda r: r["bucket"] or "")

    data = [
        {
            "timestamp": row["bucket"].isoformat(),
            "count": row["count"],
        }
        for row in rows
        if row["bucket"] is not None
    ]

    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "interval": interval,
        "total_buckets": len(data),
        "data": data,
    }
    cache.set(cache_key, result, ANALYTICS_CACHE_TTL)
    return result


def get_top_endpoints(
    dataset_id: int,
    owner: User,
    limit: int = DEFAULT_TOP_ENDPOINTS_LIMIT,
) -> dict[str, Any]:
    """
    Return the most frequently accessed endpoints ranked by request count.

    Uses the ``endpoint`` index for the GROUP BY aggregation.  A separate
    ``COUNT(DISTINCT endpoint)`` gives the total cardinality without an extra
    full-table scan (it is merged into the first aggregate call).

    Args:
        limit: Number of top endpoints to return (1–100).

    Return shape::

        {
            "dataset_id": int,
            "total_unique_endpoints": int,
            "endpoints": [
                {"endpoint": "/api/v1/login", "count": 4821, "percentage": 12.5},
                ...
            ],
        }
    """
    limit = min(max(1, limit), MAX_TOP_ENDPOINTS_LIMIT)

    cache_key = _CK_ENDPOINTS.format(dataset_id=dataset_id, limit=limit)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    agg = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .aggregate(
            total=Count("id"),
            unique_endpoints=Count("endpoint", distinct=True),
        )
    )
    total: int = agg["total"] or 0
    unique_endpoint_count: int = agg["unique_endpoints"] or 0

    qs = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .values("endpoint")
        .annotate(count=Count("id"))
        .order_by("-count")[:limit]
    )

    endpoints = [
        {
            "endpoint": row["endpoint"],
            "count": row["count"],
            "percentage": round(row["count"] / total * 100, 2) if total > 0 else 0.0,
        }
        for row in qs
    ]

    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "total_unique_endpoints": unique_endpoint_count,
        "endpoints": endpoints,
    }
    cache.set(cache_key, result, ANALYTICS_CACHE_TTL)
    return result


def get_hourly_traffic(dataset_id: int, owner: User) -> dict[str, Any]:
    """
    Return request counts distributed across the 24 hours of the day.

    ``ExtractHour`` operates at the DB level.  All entries across all dates are
    folded into a single 24-bucket distribution, revealing the dataset's daily
    traffic rhythm.  Gaps are filled with 0 so the result is always 24 elements.

    Return shape::

        {
            "dataset_id": int,
            "peak_hour":  14,
            "quiet_hour": 3,
            "traffic": [
                {"hour": 0, "count": 320},
                {"hour": 1, "count": 210},
                ...
                {"hour": 23, "count": 1050},
            ],
        }
    """
    cache_key = _CK_HOURLY.format(dataset_id=dataset_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    qs = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .annotate(hour=ExtractHour("timestamp"))
        .values("hour")
        .annotate(count=Count("id"))
        .order_by("hour")
    )

    hour_map: dict[int, int] = {h: 0 for h in range(24)}
    for row in qs:
        if row["hour"] is not None:
            hour_map[int(row["hour"])] = row["count"]

    traffic = [{"hour": h, "count": hour_map[h]} for h in range(24)]

    non_zero_hours = [h for h, c in hour_map.items() if c > 0]
    peak_hour: int = max(non_zero_hours, key=lambda h: hour_map[h]) if non_zero_hours else 0
    quiet_hour: int = min(non_zero_hours, key=lambda h: hour_map[h]) if non_zero_hours else 0

    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "peak_hour": peak_hour,
        "quiet_hour": quiet_hour,
        "traffic": traffic,
    }
    cache.set(cache_key, result, ANALYTICS_CACHE_TTL)
    return result


def get_method_distribution(dataset_id: int, owner: User) -> dict[str, Any]:
    """
    Return request counts grouped by HTTP method.

    Return shape::

        {
            "dataset_id": int,
            "methods": [
                {"method": "GET",  "count": 85000, "percentage": 89.2},
                {"method": "POST", "count": 12000, "percentage": 12.6},
                ...
            ],
        }
    """
    cache_key = _CK_METHODS.format(dataset_id=dataset_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    total_agg = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .aggregate(total=Count("id"))
    )
    total: int = total_agg["total"] or 0

    qs = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .values("method")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    methods = [
        {
            "method": row["method"],
            "count": row["count"],
            "percentage": round(row["count"] / total * 100, 2) if total > 0 else 0.0,
        }
        for row in qs
    ]

    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "methods": methods,
    }
    cache.set(cache_key, result, ANALYTICS_CACHE_TTL)
    return result


def get_error_breakdown(dataset_id: int, owner: User) -> dict[str, Any]:
    """
    Return a detailed breakdown of 4xx and 5xx error responses.

    Uses the ``(dataset, error_flag)`` compound index to restrict the scan to
    error rows only, then groups by status code and endpoint within that subset.

    Return shape::

        {
            "dataset_id": int,
            "total_errors": int,
            "errors_4xx": int,
            "errors_5xx": int,
            "top_error_codes": [
                {"status_code": 404, "count": 2100, "category": "4xx"},
                ...
            ],
            "top_error_endpoints": [
                {"endpoint": "/missing-page", "error_count": 1890},
                ...
            ],
        }
    """
    cache_key = _CK_ERRORS.format(dataset_id=dataset_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    error_qs = LogEntry.objects.filter(dataset_id=dataset_id, dataset__owner=owner, error_flag=True)

    counts = error_qs.aggregate(
        total_errors=Count("id"),
        errors_4xx=Count("id", filter=Q(status_code__gte=400, status_code__lt=500)),
        errors_5xx=Count("id", filter=Q(status_code__gte=500)),
    )

    top_error_codes = [
        {
            "status_code": row["status_code"],
            "count": row["count"],
            "category": "4xx" if row["status_code"] < 500 else "5xx",
        }
        for row in (
            error_qs
            .values("status_code")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
    ]

    top_error_endpoints = list(
        error_qs
        .values("endpoint")
        .annotate(error_count=Count("id"))
        .order_by("-error_count")[:10]
    )

    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "total_errors": counts["total_errors"] or 0,
        "errors_4xx": counts["errors_4xx"] or 0,
        "errors_5xx": counts["errors_5xx"] or 0,
        "top_error_codes": top_error_codes,
        "top_error_endpoints": top_error_endpoints,
    }
    cache.set(cache_key, result, ANALYTICS_CACHE_TTL)
    return result


def get_analytics_overview(
    dataset_id: int,
    owner: User,
    trends_interval: str = "hour",
    top_endpoints_limit: int = DEFAULT_TOP_ENDPOINTS_LIMIT,
) -> dict[str, Any]:
    """
    Aggregate all analytics sections into one response.

    Useful for the initial dashboard load — avoids seven separate HTTP round-trips.
    Each sub-call is individually cached, so mixing overview and individual endpoint
    calls does not result in duplicate computation.
    """
    return {
        "summary": get_summary_metrics(dataset_id, owner),
        "status_distribution": get_status_distribution(dataset_id, owner),
        "request_trends": get_request_trends(dataset_id, owner, interval=trends_interval),
        "top_endpoints": get_top_endpoints(dataset_id, owner, limit=top_endpoints_limit),
        "hourly_traffic": get_hourly_traffic(dataset_id, owner),
        "method_distribution": get_method_distribution(dataset_id, owner),
        "error_breakdown": get_error_breakdown(dataset_id, owner),
    }


# ---------------------------------------------------------------------------
# Date-range filtered summary  (queries LogEntry directly, no cache)
# ---------------------------------------------------------------------------


def get_filtered_summary(
    dataset_id: int,
    owner: User,
    date_from=None,
    date_to=None,
) -> dict[str, Any]:
    """
    Return aggregate metrics for a dataset filtered by an optional date range.

    Unlike ``get_summary_metrics``, this function queries ``LogEntry`` directly
    and is never cached — it is intended for ad-hoc date-range exploration on
    the dashboard.

    Args:
        date_from: Optional datetime (aware or naive) — inclusive lower bound.
        date_to:   Optional datetime (aware or naive) — inclusive upper bound.

    Return shape::

        {
            "dataset_id": int,
            "total_requests": int,
            "total_errors": int,
            "error_rate": float,
            "avg_response_size": float | None,
            "unique_visitors": int,
            "date_range": {"start": "ISO8601" | None, "end": "ISO8601" | None},
        }
    """
    qs = LogEntry.objects.filter(dataset_id=dataset_id, dataset__owner=owner)

    if date_from is not None:
        qs = qs.filter(timestamp__gte=date_from)
    if date_to is not None:
        qs = qs.filter(timestamp__lte=date_to)

    agg = qs.aggregate(
        total_requests=Count("id"),
        total_errors=Count("id", filter=Q(error_flag=True)),
        unique_visitors=Count("ip_address", distinct=True),
        avg_response_size=Avg("response_size"),
    )

    total: int = agg["total_requests"] or 0
    total_errors: int = agg["total_errors"] or 0
    error_rate: float = round(total_errors / total * 100, 3) if total > 0 else 0.0

    return {
        "dataset_id": dataset_id,
        "total_requests": total,
        "total_errors": total_errors,
        "error_rate": error_rate,
        "avg_response_size": (
            float(agg["avg_response_size"]) if agg["avg_response_size"] is not None else None
        ),
        "unique_visitors": agg["unique_visitors"] or 0,
        "date_range": {
            "start": date_from.isoformat() if date_from else None,
            "end": date_to.isoformat() if date_to else None,
        },
    }


# ---------------------------------------------------------------------------
# Persistence — AnalyticsSummary pre-computation
# ---------------------------------------------------------------------------


def compute_and_persist_analytics(dataset_id: int) -> AnalyticsSummary:
    """
    Compute all analytics, write them to ``AnalyticsSummary``, and prime the cache.

    Called automatically after log parsing completes and on manual refresh requests.
    By writing to ``AnalyticsSummary`` the results survive a cache flush or restart.

    Returns the saved ``AnalyticsSummary`` instance.
    """
    # Resolve the owner so all sub-calls enforce ownership at the DB level.
    owner = Dataset.objects.values_list("owner", flat=True).get(pk=dataset_id)
    owner_obj = User.objects.get(pk=owner)

    # Evict stale cache entries before recomputing so the service functions
    # run fresh queries rather than returning stale cached data.
    invalidate_analytics_cache(dataset_id)

    summary_data = get_summary_metrics(dataset_id, owner_obj)
    status_data = get_status_distribution(dataset_id, owner_obj)
    endpoints_data = get_top_endpoints(dataset_id, owner_obj, limit=20)
    hourly_data = get_hourly_traffic(dataset_id, owner_obj)
    method_data = get_method_distribution(dataset_id, owner_obj)

    # Reshape into the JSON structures that AnalyticsSummary stores.
    status_dist: dict[str, int] = {
        str(row["status_code"]): row["count"] for row in status_data["by_code"]
    }
    hourly_dist: dict[str, int] = {
        str(row["hour"]): row["count"] for row in hourly_data["traffic"]
    }
    method_dist: dict[str, int] = {
        row["method"]: row["count"] for row in method_data["methods"]
    }

    dr = summary_data.get("date_range", {})
    date_start = parse_datetime(dr["start"]) if dr.get("start") else None
    date_end = parse_datetime(dr["end"]) if dr.get("end") else None

    summary, created = AnalyticsSummary.objects.update_or_create(
        dataset_id=dataset_id,
        defaults={
            "total_requests": summary_data["total_requests"],
            "total_errors": summary_data["total_errors"],
            "unique_ips": summary_data["unique_ips"],
            "error_rate": summary_data["error_rate"],
            "avg_response_size": summary_data["avg_response_size"],
            "top_endpoints": endpoints_data["endpoints"],
            "status_distribution": status_dist,
            "hourly_traffic": hourly_dist,
            "method_distribution": method_dist,
            "date_range_start": date_start,
            "date_range_end": date_end,
        },
    )

    logger.info(
        "Analytics summary %s for dataset_id=%s "
        "(total_requests=%s, total_errors=%s, unique_ips=%s)",
        "created" if created else "updated",
        dataset_id,
        summary_data["total_requests"],
        summary_data["total_errors"],
        summary_data["unique_ips"],
    )
    return summary


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def invalidate_analytics_cache(dataset_id: int) -> None:
    """
    Delete all cached analytics entries for a dataset.

    Called before a recompute so stale values are never served, and also
    available for external use when log entries are modified outside the
    normal upload pipeline.
    """
    keys: list[str] = [
        _CK_SUMMARY.format(dataset_id=dataset_id),
        _CK_STATUS.format(dataset_id=dataset_id),
        _CK_HOURLY.format(dataset_id=dataset_id),
        _CK_METHODS.format(dataset_id=dataset_id),
        _CK_ERRORS.format(dataset_id=dataset_id),
    ]
    for interval in ("hour", "day"):
        keys.append(_CK_TRENDS.format(dataset_id=dataset_id, interval=interval))
    for lim in (5, 10, 20, 50, 100):
        keys.append(_CK_ENDPOINTS.format(dataset_id=dataset_id, limit=lim))

    cache.delete_many(keys)
    logger.debug("Invalidated analytics cache for dataset_id=%s (%d keys)", dataset_id, len(keys))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _status_category(code: int) -> str:
    """Map an HTTP status code integer to its category string."""
    if 100 <= code < 200:
        return "1xx"
    if 200 <= code < 300:
        return "2xx"
    if 300 <= code < 400:
        return "3xx"
    if 400 <= code < 500:
        return "4xx"
    if code >= 500:
        return "5xx"
    return "other"
