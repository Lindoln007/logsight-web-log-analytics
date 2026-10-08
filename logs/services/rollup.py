"""
Hourly roll-up of a dataset.

Time-based pages (traffic over time, time of day, anomaly detection and
forecasting) all need "what happened in each hour".  Grouping millions of raw
log rows by hour on every request is the slowest thing the application does,
so the answer is stored once per dataset in ``HourlyStat`` — one small row per
hour — and those pages read it in milliseconds whatever the dataset's size.

The roll-up is built for free while a file is being parsed (the parser already
visits every line).  Datasets that were uploaded before the table existed, or
whose entries changed afterwards, are rolled up from the stored entries the
first time it is needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone

from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncHour

from logs.services.scope import owns

logger = logging.getLogger(__name__)

_FLAG_TTL = 60 * 60 * 24 * 30


class HourBuckets:
    """Accumulates per-hour figures while the parser streams through a file."""

    __slots__ = ("_buckets", "_last_ts", "_last_bucket")

    def __init__(self) -> None:
        # hour (naive UTC) -> [requests, errors, {client addresses}, bytes, sized requests]
        self._buckets: dict[datetime, list] = {}
        self._last_ts = None
        self._last_bucket = None

    def add(self, timestamp: datetime, ip_address: str, is_error: bool, size) -> None:
        # Consecutive lines nearly always share a timestamp object (the parser
        # memoises it), so the bucket lookup is skipped for most rows.
        if timestamp is not self._last_ts:
            hour = timestamp.replace(minute=0, second=0, microsecond=0)
            bucket = self._buckets.get(hour)
            if bucket is None:
                bucket = self._buckets[hour] = [0, 0, set(), 0, 0]
            self._last_ts = timestamp
            self._last_bucket = bucket
        else:
            bucket = self._last_bucket
        bucket[0] += 1
        if is_error:
            bucket[1] += 1
        bucket[2].add(ip_address)
        if size is not None:
            bucket[3] += size
            bucket[4] += 1

    def save(self, dataset) -> int:
        """Replace *dataset*'s roll-up with the accumulated buckets."""
        from logs.models import HourlyStat

        rows = [
            HourlyStat(
                dataset=dataset,
                hour=hour.replace(tzinfo=dt_timezone.utc),
                requests=b[0],
                errors=b[1],
                unique_ips=len(b[2]),
                bytes_total=b[3],
                sized_requests=b[4],
            )
            for hour, b in sorted(self._buckets.items())
        ]
        with transaction.atomic():
            HourlyStat.objects.filter(dataset=dataset).delete()
            HourlyStat.objects.bulk_create(rows, batch_size=2_000)
        _mark_built(dataset.pk, sum(r.requests for r in rows))
        return len(rows)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def hourly_rows(dataset_id: int, owner, date_from=None, date_to=None) -> list[dict]:
    """
    Return the dataset's hours in chronological order::

        {"hour": aware datetime, "total": int, "errors": int,
         "unique_ips": int, "avg_size": float | None}

    Only hours that contain at least one request are present.
    """
    from logs.models import HourlyStat

    if not owns(dataset_id, owner):
        return []
    try:
        ensure(dataset_id)
    except DatabaseError:
        # The roll-up table is missing (migrations not applied yet).  Work from
        # the raw entries so the pages still load, and say how to fix it.
        logger.error(
            "Hourly roll-up table is unavailable; run 'python manage.py migrate'. "
            "Falling back to slower per-request aggregation."
        )
        return _rows_from_entries(dataset_id, date_from, date_to)

    qs = HourlyStat.objects.filter(dataset_id=dataset_id)
    if date_from is not None:
        qs = qs.filter(hour__gte=date_from.replace(minute=0, second=0, microsecond=0))
    if date_to is not None:
        qs = qs.filter(hour__lte=date_to)
    return [
        {
            "hour": hour,
            "total": requests,
            "errors": errors,
            "unique_ips": unique_ips,
            "avg_size": (bytes_total / sized) if sized else None,
        }
        for hour, requests, errors, unique_ips, bytes_total, sized in qs.order_by("hour").values_list(
            "hour", "requests", "errors", "unique_ips", "bytes_total", "sized_requests"
        )
    ]


def _rows_from_entries(dataset_id: int, date_from=None, date_to=None) -> list[dict]:
    """Aggregate the raw entries directly (fallback when the roll-up cannot be used)."""
    from logs.models import LogEntry

    qs = LogEntry.objects.filter(dataset_id=dataset_id)
    if date_from is not None:
        qs = qs.filter(timestamp__gte=date_from)
    if date_to is not None:
        qs = qs.filter(timestamp__lte=date_to)
    rows = (
        qs.annotate(bucket=TruncHour("timestamp"))
        .values("bucket")
        .annotate(
            total=Count("id"),
            errors=Count("id", filter=Q(error_flag=True)),
            unique_ips=Count("ip_address", distinct=True),
            bytes_total=Sum("response_size"),
            sized=Count("response_size"),
        )
        .order_by("bucket")
    )
    return [
        {
            "hour": row["bucket"],
            "total": row["total"],
            "errors": row["errors"],
            "unique_ips": row["unique_ips"],
            "avg_size": (row["bytes_total"] / row["sized"]) if row["sized"] else None,
        }
        for row in rows
        if row["bucket"] is not None
    ]


# ---------------------------------------------------------------------------
# Building from stored entries
# ---------------------------------------------------------------------------

def _flag_key(dataset_id: int) -> str:
    return f"rollup:built:{dataset_id}"


def _mark_built(dataset_id: int, requests: int) -> None:
    cache.set(_flag_key(dataset_id), requests, _FLAG_TTL)


def ensure(dataset_id: int) -> None:
    """
    Make sure the roll-up exists and matches the dataset's entry count.

    The check is one cache read in the normal case.  Without the flag (first
    use, or after the cache was cleared) the roll-up's total is compared with
    ``Dataset.total_entries`` and rebuilt only if they disagree.
    """
    from logs.models import Dataset, HourlyStat

    total_entries = Dataset.objects.filter(pk=dataset_id).values_list("total_entries", flat=True).first()
    if total_entries is None:
        return
    if cache.get(_flag_key(dataset_id)) == total_entries:
        return
    rolled = HourlyStat.objects.filter(dataset_id=dataset_id).aggregate(n=Sum("requests"))["n"] or 0
    if rolled == total_entries:
        _mark_built(dataset_id, total_entries)
        return
    rebuild(dataset_id)


def rebuild(dataset_id: int) -> int:
    """Recompute the roll-up from the stored log entries (one pass over them)."""
    from logs.models import Dataset, HourlyStat, LogEntry

    rows = (
        LogEntry.objects.filter(dataset_id=dataset_id)
        .annotate(bucket=TruncHour("timestamp"))
        .values("bucket")
        .annotate(
            requests=Count("id"),
            errors=Count("id", filter=Q(error_flag=True)),
            unique_ips=Count("ip_address", distinct=True),
            bytes_total=Sum("response_size"),
            sized_requests=Count("response_size"),
        )
        .order_by("bucket")
    )
    stats = [
        HourlyStat(
            dataset_id=dataset_id,
            hour=row["bucket"],
            requests=row["requests"],
            errors=row["errors"],
            unique_ips=row["unique_ips"],
            bytes_total=row["bytes_total"] or 0,
            sized_requests=row["sized_requests"],
        )
        for row in rows
        if row["bucket"] is not None
    ]
    with transaction.atomic():
        HourlyStat.objects.filter(dataset_id=dataset_id).delete()
        HourlyStat.objects.bulk_create(stats, batch_size=2_000)

    # Remember the build against the dataset's own count so ``ensure`` does not
    # rebuild again even if that count is out of step with the stored entries.
    total_entries = Dataset.objects.filter(pk=dataset_id).values_list("total_entries", flat=True).first()
    _mark_built(dataset_id, total_entries if total_entries is not None else sum(s.requests for s in stats))
    logger.info("Rebuilt hourly roll-up for dataset_id=%s (%d hours)", dataset_id, len(stats))
    return len(stats)
