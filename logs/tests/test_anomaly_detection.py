from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from logs.services.anomaly_detection import get_anomalies

from .helpers import add_hour, make_dataset, make_user, save_entries

ODD_HOUR = 40


class AnomalyDetectionTests(TestCase):
    """
    71 ordinary hours (about 60 requests from 12 visitors, 2 errors) plus one
    hour with the *same* request volume that comes from a single visitor and
    fails almost every time.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.dataset = make_dataset(cls.user)
        entries = []
        for hour in range(72):
            if hour == ODD_HOUR:
                entries += add_hour(cls.dataset, hour, 60, errors=55, unique_ips=1)
            else:
                entries += add_hour(cls.dataset, hour, 56 + hour % 9, errors=2, unique_ips=12)
        save_entries(cls.dataset, entries)

    def setUp(self):
        cache.clear()

    def test_statistical_method_does_not_see_a_volume_spike(self):
        result = get_anomalies(self.dataset.pk, self.user, method="statistical")
        self.assertEqual(result["method"], "statistical")
        self.assertFalse([a for a in result["anomalies"] if a["type"] == "request_spike"])

    def test_isolation_forest_flags_the_odd_hour(self):
        result = get_anomalies(self.dataset.pk, self.user, method="isolation_forest")
        self.assertEqual(result["method"], "isolation_forest")
        self.assertIsNone(result["note"])
        self.assertEqual(result["model"]["hours_analysed"], 72)

        flagged = {a["label"]: a for a in result["anomalies"]}
        self.assertIn("Mar 03 16:00", flagged)        # START + 40 hours
        odd = flagged["Mar 03 16:00"]
        self.assertLess(odd["score"], result["model"]["score_cutoff"])
        self.assertIn("error rate", odd["explanation"])
        # The odd hour is the most isolated point in the dataset.
        self.assertEqual(min(result["anomalies"], key=lambda a: a["score"])["label"], "Mar 03 16:00")

    def test_higher_sensitivity_never_flags_fewer_hours(self):
        counts = [
            get_anomalies(self.dataset.pk, self.user, sensitivity=level, method="isolation_forest")
            ["summary"]["total_anomalies"]
            for level in ("low", "medium", "high")
        ]
        self.assertEqual(counts, sorted(counts))

    def test_summary_matches_anomaly_list_and_series_is_returned(self):
        result = get_anomalies(self.dataset.pk, self.user, method="isolation_forest")
        summary = result["summary"]
        self.assertEqual(summary["total_anomalies"], len(result["anomalies"]))
        self.assertEqual(
            summary["high_severity"] + summary["medium_severity"] + summary["low_severity"],
            len(result["anomalies"]),
        )
        self.assertEqual(len(result["historical_series"]), 72)
        self.assertEqual(result["historical_series"][ODD_HOUR], {"label": "Mar 03 16:00", "value": 60})

    def test_unknown_method_and_sensitivity_use_defaults(self):
        result = get_anomalies(self.dataset.pk, self.user, sensitivity="extreme", method="magic")
        self.assertEqual(result["method"], "statistical")
        self.assertEqual(result["sensitivity"], "medium")

    def test_results_are_scoped_to_the_owner(self):
        stranger = make_user("stranger")
        result = get_anomalies(self.dataset.pk, stranger, method="isolation_forest")
        self.assertEqual(result["anomalies"], [])
        self.assertEqual(result["historical_series"], [])

    def test_endpoint_accepts_method_parameter(self):
        self.client.force_login(self.user)
        url = reverse("logs:anomalies", args=[self.dataset.pk])
        response = self.client.get(url, {"method": "isolation_forest", "sensitivity": "high"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["method"], "isolation_forest")
        self.assertEqual(body["sensitivity"], "high")

    def test_endpoint_requires_login(self):
        response = self.client.get(reverse("logs:anomalies", args=[self.dataset.pk]))
        self.assertEqual(response.status_code, 302)


class ShortDatasetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.dataset = make_dataset(self.user)
        entries = []
        for hour in range(10):
            entries += add_hour(self.dataset, hour, 400 if hour == 5 else 20)
        save_entries(self.dataset, entries)

    def test_isolation_forest_falls_back_with_a_note(self):
        result = get_anomalies(self.dataset.pk, self.user, method="isolation_forest")
        self.assertEqual(result["requested_method"], "isolation_forest")
        self.assertEqual(result["method"], "statistical")
        self.assertIn("at least 24 hours", result["note"])
        # The fallback still does its job.
        self.assertEqual([a["type"] for a in result["anomalies"]], ["request_spike"])
