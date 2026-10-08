from __future__ import annotations

import math
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from logs.services.forecasting import get_traffic_forecast

from .helpers import START, add_hour, make_dataset, make_user, save_entries


def daily_pattern(hour_index: int) -> int:
    """Busy afternoons, quiet nights: 5 requests at 03:00, 45 at 15:00."""
    return round(25 + 20 * math.sin(2 * math.pi * ((hour_index % 24) - 9) / 24))


class ForecastTests(TestCase):
    """Ten days of traffic that repeats the same daily shape."""

    HOURS = 240

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.dataset = make_dataset(cls.user)
        entries = []
        for hour in range(cls.HOURS):
            entries += add_hour(cls.dataset, hour, daily_pattern(hour))
        save_entries(cls.dataset, entries)

    def setUp(self):
        cache.clear()

    def test_ml_forecast_learns_the_daily_pattern(self):
        result = get_traffic_forecast(self.dataset.pk, self.user, periods=24, method="ml")
        self.assertEqual(result["method"], "ml")
        self.assertIsNone(result["note"])
        self.assertEqual(len(result["forecast"]), 24)

        expected = [daily_pattern(self.HOURS + i) for i in range(24)]
        predicted = [point["value"] for point in result["forecast"]]
        mean_error = sum(abs(p - e) for p, e in zip(predicted, expected)) / 24
        self.assertLess(mean_error, 4, predicted)

        for point in result["forecast"]:
            self.assertGreaterEqual(point["lower"], 0)
            self.assertLessEqual(point["lower"], point["value"])
            self.assertLessEqual(point["value"], point["upper"])
            self.assertTrue(point["is_forecast"])

    def test_forecast_continues_from_the_end_of_the_file(self):
        result = get_traffic_forecast(self.dataset.pk, self.user, periods=3, method="ml")
        last_hour = START + timedelta(hours=self.HOURS - 1)
        self.assertEqual(result["historical"][-1]["timestamp"], last_hour.isoformat())
        self.assertEqual(
            [point["timestamp"] for point in result["forecast"]],
            [(last_hour + timedelta(hours=i)).isoformat() for i in (1, 2, 3)],
        )

    def test_evaluation_scores_every_method_on_the_same_holdout(self):
        evaluation = get_traffic_forecast(self.dataset.pk, self.user)["evaluation"]
        self.assertEqual(evaluation["holdout_hours"], 48)        # 20 % of 240
        self.assertEqual(evaluation["training_hours"], 192)
        scores = {row["method"]: row["mae"] for row in evaluation["results"]}
        self.assertEqual(set(scores), {"moving_avg", "linear_regression", "ml"})
        # A straight line cannot follow a daily cycle; the other two can.
        self.assertLess(scores["ml"], scores["linear_regression"] / 2)
        self.assertLess(scores["moving_avg"], scores["linear_regression"] / 2)
        self.assertIn(evaluation["best_method"], ("ml", "moving_avg"))

    def test_existing_methods_still_work(self):
        for method in ("moving_avg", "linear_regression"):
            result = get_traffic_forecast(self.dataset.pk, self.user, periods=12, method=method)
            self.assertEqual(result["method"], method)
            self.assertEqual(len(result["forecast"]), 12)
            self.assertEqual(result["summary"]["total_historical_points"], self.HOURS)

    def test_unknown_method_uses_default(self):
        result = get_traffic_forecast(self.dataset.pk, self.user, method="crystal_ball")
        self.assertEqual(result["method"], "moving_avg")

    def test_results_are_scoped_to_the_owner(self):
        result = get_traffic_forecast(self.dataset.pk, make_user("stranger"), method="ml")
        self.assertEqual(result["historical"], [])
        self.assertEqual(result["forecast"], [])
        self.assertIsNone(result["evaluation"])

    def test_endpoint_accepts_ml_method(self):
        self.client.force_login(self.user)
        url = reverse("logs:forecast", args=[self.dataset.pk])
        response = self.client.get(url, {"method": "ml", "periods": 6})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["method"], "ml")
        self.assertEqual(len(body["forecast"]), 6)

    def test_endpoint_requires_login(self):
        response = self.client.get(reverse("logs:forecast", args=[self.dataset.pk]))
        self.assertEqual(response.status_code, 302)


class SparseAndShortHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.dataset = make_dataset(self.user)

    def test_hours_without_requests_are_filled_with_zero(self):
        entries = add_hour(self.dataset, 0, 5) + add_hour(self.dataset, 3, 7)
        save_entries(self.dataset, entries)
        result = get_traffic_forecast(self.dataset.pk, self.user)
        self.assertEqual([p["value"] for p in result["historical"]], [5, 0, 0, 7])

    def test_ml_falls_back_when_history_is_too_short(self):
        entries = []
        for hour in range(30):
            entries += add_hour(self.dataset, hour, 10)
        save_entries(self.dataset, entries)

        result = get_traffic_forecast(self.dataset.pk, self.user, periods=5, method="ml")
        self.assertEqual(result["requested_method"], "ml")
        self.assertEqual(result["method"], "moving_avg")
        self.assertIn("at least 72 hours", result["note"])
        self.assertEqual([p["value"] for p in result["forecast"]], [10] * 5)
        # Too little training data to score the ML model, so it is left out.
        methods = [row["method"] for row in result["evaluation"]["results"]]
        self.assertEqual(methods, ["moving_avg", "linear_regression"])

    def test_empty_dataset(self):
        result = get_traffic_forecast(self.dataset.pk, self.user, method="ml")
        self.assertEqual(result["forecast"], [])
        self.assertIsNone(result["evaluation"])
