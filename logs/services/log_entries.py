"""
Log entry retrieval service — paginated and filterable.

Handles all data access for the Log Viewer.  Kept separate from the analytics
service because it returns raw row data (for human inspection) rather than
aggregated metrics.

Design constraints
------------------
* All queries are scoped to a dataset_id — no cross-dataset leakage.
* ``only()`` restricts the SELECT to the columns the viewer actually needs so
  the database never fetches large TEXT fields (user_agent, referrer) unless
  explicitly requested.
* Ordering uses the ``(dataset, timestamp)`` compound index for fast pagination.
* ``count()`` is issued before slicing so the total record count is accurate
  for pagination even when filters are applied.
* Search is intentionally restricted to ``endpoint`` and ``ip_address`` —
  both are indexed — to keep large-dataset queries fast.  Searching unbounded
  TEXT columns (user_agent, referrer) would require a sequential scan.
"""
from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from logs.models import LogEntry

User = get_user_model()

logger = logging.getLogger(__name__)

PAGE_SIZE_DEFAULT: int = 25
PAGE_SIZE_MAX: int = 100

# Map the viewer's status-filter tokens to ORM filter kwargs.
_STATUS_FILTERS: dict[str, dict] = {
    "1xx": {"status_code__gte": 100, "status_code__lt": 200},
    "2xx": {"status_code__gte": 200, "status_code__lt": 300},
    "3xx": {"status_code__gte": 300, "status_code__lt": 400},
    "4xx": {"status_code__gte": 400, "status_code__lt": 500},
    "5xx": {"status_code__gte": 500},
    "errors": {"error_flag": True},
}


def get_log_entries(
    dataset_id: int,
    owner: User,
    *,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
    status_filter: str = "all",
    search: str = "",
    order_by: str = "-timestamp",
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    """
    Return a paginated slice of log entries for a single dataset.

    Args:
        dataset_id:    The dataset to query (strictly enforced).
        page:          1-based page number.
        page_size:     Rows per page (1–100).
        status_filter: One of "all", "1xx", "2xx", "3xx", "4xx", "5xx",
                       or "errors".  Invalid tokens default to "all".
        search:        Substring match against ``endpoint`` or ``ip_address``.
                       Blank string disables search filtering.
        order_by:      ORM order expression.  Default is newest-first.

    Returns a dict shaped as::

        {
            "dataset_id": int,
            "items": [
                {
                    "id": int,
                    "timestamp": "ISO8601",
                    "ip_address": "str",
                    "method": "GET",
                    "endpoint": "/api/v1/...",
                    "status_code": 200,
                    "response_size": 1024 | null,
                    "raw": "reconstructed access-log line",
                },
                ...
            ],
            "total": int,
            "page": int,
            "page_size": int,
            "total_pages": int,
        }
    """
    page = max(1, page)
    page_size = min(max(1, page_size), PAGE_SIZE_MAX)

    qs = LogEntry.objects.filter(dataset_id=dataset_id, dataset__owner=owner)

    # -- Status filter ---------------------------------------------------
    status_kwargs = _STATUS_FILTERS.get(status_filter)
    if status_kwargs:
        qs = qs.filter(**status_kwargs)

    # -- Date range filter (uses dataset+timestamp compound index) -------
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()
    if date_from:
        dt = parse_datetime(date_from)
        if dt is not None:
            qs = qs.filter(timestamp__gte=dt)
        else:
            logger.debug("Ignoring unparseable date_from=%r for dataset_id=%s", date_from, dataset_id)
    if date_to:
        dt = parse_datetime(date_to)
        if dt is not None:
            qs = qs.filter(timestamp__lte=dt)
        else:
            logger.debug("Ignoring unparseable date_to=%r for dataset_id=%s", date_to, dataset_id)

    # -- Search filter (indexed fields only) -----------------------------
    search = (search or "").strip()
    if search:
        qs = qs.filter(
            Q(endpoint__icontains=search) | Q(ip_address__icontains=search)
        )

    # -- Total count (before pagination) ---------------------------------
    total: int = qs.count()
    total_pages: int = max(1, (total + page_size - 1) // page_size)
    # Clamp page to a valid range after applying filters.
    page = min(page, total_pages)
    offset: int = (page - 1) * page_size

    # -- Fetch only the columns the viewer needs -------------------------
    entries = (
        qs
        .only(
            "id",
            "timestamp",
            "ip_address",
            "method",
            "endpoint",
            "protocol",
            "status_code",
            "response_size",
        )
        .order_by(order_by)[offset : offset + page_size]
    )

    items: list[dict[str, Any]] = [
        {
            "id": entry.pk,
            "timestamp": entry.timestamp.isoformat(),
            "ip_address": entry.ip_address,
            "method": entry.method,
            "endpoint": entry.endpoint,
            "status_code": entry.status_code,
            "response_size": entry.response_size,
            "raw": _reconstruct_log_line(entry),
        }
        for entry in entries
    ]

    return {
        "dataset_id": dataset_id,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _reconstruct_log_line(entry: LogEntry) -> str:
    """
    Build an Apache Combined-style representation from stored fields.

    The original raw text is not stored (only structured columns are kept), so
    we reconstruct a human-readable representation for the viewer.
    """
    ts = entry.timestamp.strftime("%d/%b/%Y:%H:%M:%S %z")
    proto = entry.protocol or "HTTP/1.1"
    size = str(entry.response_size) if entry.response_size is not None else "-"
    return (
        f'{entry.ip_address} - - [{ts}] '
        f'"{entry.method} {entry.endpoint} {proto}" '
        f"{entry.status_code} {size}"
    )
