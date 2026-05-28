"""
Real-time log simulator service for LogSight.

Generates synthetic Apache-style log entries and appends them to a dataset.
Useful for demos, testing, and UI development when real log files are not
available.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone as dt_timezone
from typing import Any

SAMPLE_IPS = (
    ["192.168.1." + str(i) for i in range(1, 30)]
    + ["10.0.0." + str(i) for i in range(1, 20)]
)
SAMPLE_ENDPOINTS = [
    "/",
    "/api/login",
    "/api/data",
    "/api/users",
    "/dashboard",
    "/static/main.js",
    "/api/logout",
    "/health",
    "/api/search",
    "/admin/",
]
SAMPLE_METHODS = ["GET"] * 7 + ["POST"] * 2 + ["DELETE"]
SAMPLE_STATUS = (
    [200] * 50
    + [201] * 10
    + [301] * 5
    + [404] * 15
    + [500] * 8
    + [403] * 7
    + [401] * 5
)


def generate_log_entries(dataset_id: int, owner, count: int = 10) -> dict[str, Any]:
    """
    Generate *count* synthetic log entries and append them to *dataset_id*.

    The dataset must be owned by *owner*.  Raises Dataset.DoesNotExist if the
    dataset is not found for that owner.

    Args:
        dataset_id: Primary key of the target Dataset.
        owner:      Django user instance — used to enforce ownership.
        count:      Number of entries to generate (caller should cap at 50).

    Returns a dict with the count of generated entries and their serialised data.
    """
    from logs.models import Dataset, LogEntry

    dataset = Dataset.objects.get(pk=dataset_id, owner=owner)
    now = datetime.now(tz=dt_timezone.utc)
    entries: list[LogEntry] = []

    for _ in range(count):
        status = random.choice(SAMPLE_STATUS)
        entries.append(
            LogEntry(
                dataset=dataset,
                timestamp=now,
                method=random.choice(SAMPLE_METHODS),
                endpoint=random.choice(SAMPLE_ENDPOINTS),
                status_code=status,
                ip_address=random.choice(SAMPLE_IPS),
                response_size=random.randint(200, 50_000),
                user_agent="LogSight-Simulator/1.0",
            )
        )

    LogEntry.objects.bulk_create(entries)

    # Re-count so total_entries stays accurate.
    dataset.total_entries = dataset.entries.count()
    dataset.save(update_fields=["total_entries"])

    serialized = [
        {
            "timestamp": e.timestamp.isoformat(),
            "method": e.method,
            "endpoint": e.endpoint,
            "status_code": e.status_code,
            "ip_address": e.ip_address,
            "response_size": e.response_size,
        }
        for e in entries
    ]

    return {"generated": len(entries), "entries": serialized}
