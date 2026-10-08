from __future__ import annotations

import gzip
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase

from logs.models import Dataset, LogEntry
from logs.services.log_parser import LogParsingService

from .helpers import make_user

COMBINED = (
    '93.180.71.3 - - [17/May/2015:08:05:32 +0000] "GET /downloads/product_1 HTTP/1.1" '
    '304 0 "-" "Debian APT-HTTP/1.3 (0.8.16~exp12ubuntu10.21)"'
)
COMMON = 'unicomp6.unicomp.net - - [01/Jul/1995:00:00:06 -0400] "GET /shuttle/countdown/ HTTP/1.0" 200 3985'


class ParseLineTests(SimpleTestCase):
    def setUp(self):
        self.parser = LogParsingService()

    def test_combined_format(self):
        record = self.parser.parse_line(COMBINED)
        self.assertEqual(record.ip_address, "93.180.71.3")
        self.assertEqual(record.method, "GET")
        self.assertEqual(record.endpoint, "/downloads/product_1")
        self.assertEqual(record.protocol, "HTTP/1.1")
        self.assertEqual(record.status_code, 304)
        self.assertEqual(record.response_size, 0)
        self.assertIsNone(record.referrer)
        self.assertEqual(record.user_agent, "Debian APT-HTTP/1.3 (0.8.16~exp12ubuntu10.21)")
        self.assertEqual(record.timestamp, datetime(2015, 5, 17, 8, 5, 32, tzinfo=timezone.utc))

    def test_common_format_with_hostname_and_offset(self):
        record = self.parser.parse_line(COMMON)
        self.assertEqual(record.ip_address, "unicomp6.unicomp.net")
        self.assertIsNone(record.user_agent)
        # 00:00:06 at UTC-4 is 04:00:06 UTC.
        self.assertEqual(record.timestamp, datetime(1995, 7, 1, 4, 0, 6, tzinfo=timezone.utc))

    def test_positive_offset_crossing_midnight(self):
        line = '1.2.3.4 - - [21/Jun/2012:00:35:33 +0200] "GET / HTTP/1.0" 200 12'
        record = self.parser.parse_line(line)
        self.assertEqual(record.timestamp, datetime(2012, 6, 20, 22, 35, 33, tzinfo=timezone.utc))

    def test_escaped_quote_in_user_agent(self):
        line = (
            '1.202.218.8 - - [20/Jun/2012:19:05:12 +0200] "GET /robots.txt HTTP/1.0" '
            '404 492 "-" "\\"Mozilla/5.0"'
        )
        record = self.parser.parse_line(line)
        self.assertIsNotNone(record)
        self.assertEqual(record.endpoint, "/robots.txt")
        self.assertEqual(record.user_agent, '\\"Mozilla/5.0')

    def test_space_in_url_and_unusual_protocol(self):
        line = '5.9.112.67 - - [15/May/2022:16:52:30 +0000] "GET /blog/tags/net neutrality HTTP/1.1" 404 7861'
        record = self.parser.parse_line(line)
        self.assertEqual(record.endpoint, "/blog/tags/net neutrality")
        self.assertEqual(record.protocol, "HTTP/1.1")

        line = 'host - - [01/Jul/1995:01:49:11 -0400] "GET /a.html HTTP/V1.0" 200 3985'
        record = self.parser.parse_line(line)
        self.assertEqual(record.endpoint, "/a.html")
        self.assertEqual(record.protocol, "HTTP/V1.0")

    def test_request_without_protocol(self):
        record = self.parser.parse_line('1.2.3.4 - - [17/May/2015:08:05:32 +0000] "GET /old" 200 5')
        self.assertEqual(record.endpoint, "/old")
        self.assertIsNone(record.protocol)

    def test_missing_and_oversized_response_size(self):
        record = self.parser.parse_line('1.2.3.4 - - [17/May/2015:08:05:32 +0000] "GET / HTTP/1.1" 200 -')
        self.assertIsNone(record.response_size)
        record = self.parser.parse_line(
            '1.2.3.4 - - [17/May/2015:08:05:32 +0000] "GET / HTTP/1.1" 200 99999999999'
        )
        self.assertEqual(record.response_size, 2_147_483_647)

    def test_query_string_is_kept(self):
        record = self.parser.parse_line(
            "1.2.3.4 - - [17/May/2015:08:05:32 +0000] \"GET /item?id=1' OR '1'='1 HTTP/1.1\" 200 5"
        )
        self.assertEqual(record.endpoint, "/item?id=1' OR '1'='1")

    def test_extra_trailing_fields_are_tolerated(self):
        record = self.parser.parse_line(COMBINED + ' 0.004 "-"')
        self.assertEqual(record.status_code, 304)

    def test_unrecognised_lines_are_rejected(self):
        for line in (
            "",
            "this is not an access log line",
            '1.2.3.4 - - [not a date] "GET / HTTP/1.1" 200 5',
            '1.2.3.4 - - [31/Feb/2015:08:05:32 +0000] "GET / HTTP/1.1" 200 5',
            '1.2.3.4 - - [17/May/2015:08:05:32 +0000] "\\x16\\x03\\x01" 400 5',
            '1.2.3.4 - - [17/May/2015:08:05:32 +0000] "GET" 400 5',
        ):
            self.assertIsNone(self.parser.parse_line(line), line)


class ParseDatasetTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name: str, text: str) -> str:
        path = Path(self.tmp.name) / name
        if name.endswith(".gz"):
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(text)
        else:
            path.write_text(text, encoding="utf-8")
        return str(path)

    def _dataset(self) -> Dataset:
        return Dataset.objects.create(owner=self.user, name="upload.log")

    def test_parses_file_into_rows(self):
        text = "\n".join([COMBINED, "garbage line", "", COMMON,
                          '9.9.9.9 - - [17/May/2015:09:00:00 +0000] "POST /login HTTP/1.1" 401 77']) + "\n"
        dataset = self._dataset()
        stats = LogParsingService(batch_size=2).parse_dataset(dataset, file_path=self._write("a.log", text))

        self.assertEqual(stats.total_lines, 4)       # blank line is not counted
        self.assertEqual(stats.inserted_entries, 3)
        self.assertEqual(stats.skipped_lines, 1)

        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.STATUS_COMPLETED)
        self.assertEqual(dataset.total_entries, 3)

        first = LogEntry.objects.get(dataset=dataset, ip_address="93.180.71.3")
        self.assertEqual(first.timestamp, datetime(2015, 5, 17, 8, 5, 32, tzinfo=timezone.utc))
        self.assertEqual(first.user_agent, "Debian APT-HTTP/1.3 (0.8.16~exp12ubuntu10.21)")
        self.assertFalse(first.error_flag)

        failed_login = LogEntry.objects.get(dataset=dataset, endpoint="/login")
        self.assertTrue(failed_login.error_flag)
        self.assertEqual(failed_login.method, "POST")

        hostname_row = LogEntry.objects.get(dataset=dataset, ip_address="unicomp6.unicomp.net")
        self.assertIsNone(hostname_row.user_agent)
        self.assertIsNone(hostname_row.referrer)

    def test_gzip_file(self):
        dataset = self._dataset()
        stats = LogParsingService().parse_dataset(
            dataset, file_path=self._write("a.log.gz", COMBINED + "\n" + COMMON + "\n")
        )
        self.assertEqual(stats.inserted_entries, 2)

    def test_file_with_no_valid_lines_fails_with_clear_reason(self):
        dataset = self._dataset()
        with self.assertRaises(ValueError), self.assertLogs("logs.services.log_parser", level="ERROR"):
            LogParsingService().parse_dataset(
                dataset, file_path=self._write("notes.txt", "hello\nworld\n")
            )
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.STATUS_FAILED)
        self.assertIn("No valid access-log lines", dataset.processing_error)

    def test_falls_back_to_bulk_create_when_raw_insert_fails(self):
        dataset = self._dataset()
        service = LogParsingService(batch_size=1)
        with mock.patch.object(service, "_raw_insert", side_effect=RuntimeError("boom")) as raw, \
                self.assertLogs("logs.services.log_parser", level="ERROR"):
            stats = service.parse_dataset(
                dataset, file_path=self._write("a.log", COMBINED + "\n" + COMMON + "\n")
            )
        self.assertEqual(raw.call_count, 1)          # disabled after the first failure
        self.assertEqual(stats.inserted_entries, 2)
        self.assertEqual(LogEntry.objects.filter(dataset=dataset).count(), 2)
