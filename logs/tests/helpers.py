"""Shared fixtures for the logs test-suite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model

from logs.models import Dataset, LogEntry

START = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)  # a Monday


def make_user(username: str = "analyst"):
    # No password: tests log in with force_login(), and hashing one is slow.
    return get_user_model().objects.create_user(username=username)


def make_dataset(owner, name: str = "test.log") -> Dataset:
    return Dataset.objects.create(owner=owner, name=name, status=Dataset.STATUS_COMPLETED)


def add_hour(
    dataset: Dataset,
    hour_index: int,
    requests: int,
    *,
    errors: int = 0,
    unique_ips: int = 10,
    endpoint: str = "/index.html",
    size: int = 1_000,
) -> list[LogEntry]:
    """Build (unsaved) entries for one hour, spread across *unique_ips* clients."""
    hour = START + timedelta(hours=hour_index)
    entries = []
    for i in range(requests):
        status = 500 if i < errors else 200
        entries.append(
            LogEntry(
                dataset=dataset,
                timestamp=hour + timedelta(seconds=i % 3600),
                method="GET",
                endpoint=endpoint,
                protocol="HTTP/1.1",
                status_code=status,
                ip_address=f"10.0.0.{i % unique_ips + 1}",
                response_size=size,
                error_flag=status >= 400,
            )
        )
    return entries


def save_entries(dataset: Dataset, entries: list[LogEntry]) -> None:
    LogEntry.objects.bulk_create(entries, batch_size=2_000)
    dataset.total_entries = dataset.entries.count()
    dataset.save(update_fields=["total_entries"])
