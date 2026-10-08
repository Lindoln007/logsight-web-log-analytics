"""Tests for the hourly roll-up, query scoping and result caching."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from logs.models import Dataset, HourlyStat, LogEntry
from logs.services import rollup
from logs.services.analytics import (
    get_hourly_traffic,
    get_request_trends,
    get_top_endpoints,
    invalidate_analytics_cache,
)
from logs.services.anomaly_detection import get_anomalies
from logs.services.log_entries import get_log_entries
from logs.services.log_parser import LogParsingService
from logs.services.scope import entries_for

from .helpers import add_hour, make_dataset, make_user, save_entries


def _line(ip, when, path="/", status=200, size="100"):
    return f'{ip} - - [{when} +0000] "GET {path} HTTP/1.1" {status} {size}\n'


class ParserRollupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_parser_stores_one_row_per_hour(self):
        text = (
            _line("1.1.1.1", "17/May/2015:08:05:32")
            + _line("1.1.1.1", "17/May/2015:08:59:59", status=500)
            + _line("2.2.2.2", "17/May/2015:08:10:00", size="-")
            + _line("3.3.3.3", "17/May/2015:10:00:00", size="300")
        )
        path = Path(self.tmp.name) / "a.log"
        path.write_text(text, encoding="utf-8")
        dataset = Dataset.objects.create(owner=self.user, name="a.log")
        LogParsingService().parse_dataset(dataset, file_path=str(path))

        stats = list(HourlyStat.objects.filter(dataset=dataset).order_by("hour"))
        self.assertEqual([s.hour for s in stats], [
            datetime(2015, 5, 17, 8, tzinfo=timezone.utc),
            datetime(2015, 5, 17, 10, tzinfo=timezone.utc),
        ])
        first = stats[0]
        self.assertEqual((first.requests, first.errors, first.unique_ips), (3, 1, 2))
        self.assertEqual((first.bytes_total, first.sized_requests), (200, 2))

        # Rebuilding from the stored entries gives exactly the same roll-up.
        built = list(HourlyStat.objects.filter(dataset=dataset).values_list(
            "hour", "requests", "errors", "unique_ips", "bytes_total", "sized_requests"))
        rollup.rebuild(dataset.pk)
        rebuilt = list(HourlyStat.objects.filter(dataset=dataset).values_list(
            "hour", "requests", "errors", "unique_ips", "bytes_total", "sized_requests"))
        self.assertEqual(built, rebuilt)

    def test_time_based_pages_do_not_touch_raw_entries_after_parsing(self):
        path = Path(self.tmp.name) / "b.log"
        path.write_text(_line("1.1.1.1", "17/May/2015:08:05:32") * 30, encoding="utf-8")
        dataset = Dataset.objects.create(owner=self.user, name="b.log")
        LogParsingService().parse_dataset(dataset, file_path=str(path))

        with mock.patch.object(LogEntry.objects, "filter", side_effect=AssertionError("raw rows were queried")):
            trends = get_request_trends(dataset.pk, self.user)
            hours = get_hourly_traffic(dataset.pk, self.user)
            anomalies = get_anomalies(dataset.pk, self.user)
        self.assertEqual(trends["data"], [{"timestamp": "2015-05-17T08:00:00+00:00", "count": 30}])
        self.assertEqual(hours["peak_hour"], 8)
        self.assertEqual(len(anomalies["historical_series"]), 1)


class RollupFromStoredEntriesTests(TestCase):
    """Datasets that pre-date the roll-up table are rolled up on first use."""

    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.dataset = make_dataset(self.user)
        entries = []
        for hour, requests in ((0, 4), (1, 6), (25, 10)):       # two days
            entries += add_hour(self.dataset, hour, requests, errors=1)
        save_entries(self.dataset, entries)

    def test_trends_by_hour_and_by_day(self):
        self.assertFalse(HourlyStat.objects.exists())
        hourly = get_request_trends(self.dataset.pk, self.user, interval="hour")
        self.assertEqual([p["count"] for p in hourly["data"]], [4, 6, 10])
        self.assertEqual(HourlyStat.objects.filter(dataset=self.dataset).count(), 3)

        daily = get_request_trends(self.dataset.pk, self.user, interval="day")
        self.assertEqual([p["count"] for p in daily["data"]], [10, 10])
        self.assertEqual(get_request_trends(self.dataset.pk, self.user, limit=2)["total_buckets"], 2)

    def test_date_range_and_hour_of_day(self):
        day_two = datetime(2026, 3, 3, tzinfo=timezone.utc)
        ranged = get_request_trends(self.dataset.pk, self.user, date_from=day_two)
        self.assertEqual([p["count"] for p in ranged["data"]], [10])

        pattern = get_hourly_traffic(self.dataset.pk, self.user)
        counts = {t["hour"]: t["count"] for t in pattern["traffic"]}
        self.assertEqual((counts[0], counts[1]), (4, 16))        # hour 1 on both days
        self.assertEqual(pattern["peak_hour"], 1)

    def test_rollup_is_rebuilt_when_entries_are_added(self):
        get_request_trends(self.dataset.pk, self.user)
        save_entries(self.dataset, add_hour(self.dataset, 30, 5))   # total_entries changes
        counts = [p["count"] for p in get_request_trends(self.dataset.pk, self.user)["data"]]
        self.assertEqual(counts, [4, 6, 10, 5])

    def test_pages_still_work_when_the_rollup_table_is_missing(self):
        from django.db import OperationalError

        with mock.patch("logs.services.rollup.ensure", side_effect=OperationalError("no such table: logs_hourlystat")), \
                self.assertLogs("logs.services.rollup", level="ERROR") as logged:
            counts = [p["count"] for p in get_request_trends(self.dataset.pk, self.user)["data"]]
            rows = rollup.hourly_rows(self.dataset.pk, self.user)
        self.assertEqual(counts, [4, 6, 10])
        self.assertEqual((rows[0]["errors"], rows[0]["unique_ips"], rows[0]["avg_size"]), (1, 4, 1000))
        self.assertIn("manage.py migrate", logged.output[0])

    def test_other_users_see_nothing(self):
        stranger = make_user("stranger")
        self.assertEqual(get_request_trends(self.dataset.pk, stranger)["data"], [])
        self.assertEqual(rollup.hourly_rows(self.dataset.pk, stranger), [])
        self.assertFalse(entries_for(self.dataset.pk, stranger).exists())
        self.assertEqual(entries_for(self.dataset.pk, self.user).count(), 20)


class CachingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.dataset = make_dataset(self.user)
        entries = []
        for hour in range(30):
            entries += add_hour(self.dataset, hour, 12, errors=2, endpoint=f"/page-{hour % 4}")
        save_entries(self.dataset, entries)

    def test_repeat_analysis_is_served_from_cache(self):
        first = get_anomalies(self.dataset.pk, self.user, method="isolation_forest")
        with mock.patch("logs.services.anomaly_detection._hourly_buckets", side_effect=AssertionError("recomputed")):
            # Keyword and positional spellings of the same call share one entry.
            again = get_anomalies(self.dataset.pk, owner=self.user, sensitivity="medium", method="isolation_forest")
        self.assertEqual(first, again)

        # A different sensitivity is a different result and is computed.
        other = get_anomalies(self.dataset.pk, self.user, sensitivity="high", method="isolation_forest")
        self.assertEqual(other["sensitivity"], "high")

    def test_invalidation_retires_cached_results(self):
        get_anomalies(self.dataset.pk, self.user)
        invalidate_analytics_cache(self.dataset.pk)
        with mock.patch("logs.services.anomaly_detection._hourly_buckets", return_value=[]) as recomputed:
            get_anomalies(self.dataset.pk, self.user)
        self.assertTrue(recomputed.called)

    def test_cached_results_are_not_served_to_other_users(self):
        self.assertEqual(len(get_anomalies(self.dataset.pk, self.user)["historical_series"]), 30)
        self.assertEqual(get_anomalies(self.dataset.pk, make_user("stranger"))["historical_series"], [])

    def test_top_endpoints_are_computed_once_for_every_limit(self):
        top_two = get_top_endpoints(self.dataset.pk, self.user, limit=2)
        self.assertEqual(len(top_two["endpoints"]), 2)
        with mock.patch("logs.services.analytics.entries_for", side_effect=AssertionError("recomputed")):
            top_four = get_top_endpoints(self.dataset.pk, self.user, limit=4)
        self.assertEqual(len(top_four["endpoints"]), 4)
        self.assertEqual(top_four["endpoints"][:2], top_two["endpoints"])
        self.assertEqual(top_four["total_unique_endpoints"], 4)

    def test_entry_counts_avoid_counting_rows(self):
        # No filters: the total comes from the dataset record.
        with mock.patch("django.db.models.query.QuerySet.count", side_effect=AssertionError("counted rows")):
            page = get_log_entries(self.dataset.pk, self.user, page_size=10)
        self.assertEqual((page["total"], page["total_pages"], len(page["items"])), (360, 36, 10))

        # With a filter: counted once, then remembered for the following pages.
        first = get_log_entries(self.dataset.pk, self.user, status_filter="errors", page_size=10)
        self.assertEqual(first["total"], 60)
        with mock.patch("django.db.models.query.QuerySet.count", side_effect=AssertionError("counted rows")):
            second = get_log_entries(self.dataset.pk, self.user, status_filter="errors", page_size=10, page=2)
        self.assertEqual((second["total"], second["page"]), (60, 2))

        # Another user gets an empty result, not the owner's count.
        self.assertEqual(get_log_entries(self.dataset.pk, make_user("stranger"))["total"], 0)
