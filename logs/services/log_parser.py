"""
Streaming log parser service for Apache Combined and standard Nginx access logs.

Performance notes
-----------------
Large uploads are dominated by per-line overhead, so the hot path avoids the
expensive parts of the previous implementation:

* Timestamps are parsed by slicing fixed positions instead of ``strptime``.
* Parsed lines become plain tuples — no dataclass and no model instance per row.
* Batches are written with a single multi-row ``INSERT`` statement built once
  per batch size, bypassing the ORM's per-field SQL compilation.  If the raw
  insert fails for any reason the batch is retried through ``bulk_create``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from gzip import open as gzip_open
from pathlib import Path
from typing import Iterator, Optional

from django.db import connection, transaction

from logs.models import Dataset, LogEntry

logger = logging.getLogger(__name__)

# Quoted fields may contain backslash-escaped quotes (e.g. a user agent of
# "\"Mozilla/5.0"), so they are matched as "anything but quote/backslash, or an
# escaped character".  Trailing extra fields (request time, upstream, …) that
# some Nginx formats append after the user agent are tolerated.
_QUOTED = r'(?:[^"\\]|\\.)*'
ACCESS_LOG_PATTERN = re.compile(
    r"\s*(?P<ip>\S+)\s+"
    r"\S+\s+"
    r"\S+\s+"
    r"\[(?P<timestamp>[^\]]+)\]\s+"
    rf'"(?P<request>{_QUOTED})"\s+'
    r"(?P<status>\d{3})\s+"
    r"(?P<size>\S+)"
    rf'(?:\s+"(?P<referrer>{_QUOTED})"\s+"(?P<user_agent>{_QUOTED})")?'
)

LOG_TIME_FORMAT = "%d/%b/%Y:%H:%M:%S %z"
DEFAULT_BATCH_SIZE = 1000

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Column limits mirrored from the LogEntry model so one oversized value cannot
# make the database reject an entire batch.
_MAX_METHOD_LENGTH = 10
_MAX_ENDPOINT_LENGTH = 2_048
_MAX_PROTOCOL_LENGTH = 20
_MAX_HOST_LENGTH = 255
_MAX_RESPONSE_SIZE = 2_147_483_647  # 32-bit signed integer column

# Order of values in a parsed row tuple (and of the columns in the raw INSERT).
_ROW_FIELDS = (
    "timestamp",
    "ip_address",
    "method",
    "endpoint",
    "protocol",
    "status_code",
    "response_size",
    "referrer",
    "user_agent",
    "error_flag",
)


@dataclass(slots=True)
class ParsedLogRecord:
    timestamp: datetime
    ip_address: str
    method: str
    endpoint: str
    protocol: Optional[str]
    status_code: int
    response_size: Optional[int]
    referrer: Optional[str]
    user_agent: Optional[str]


@dataclass(slots=True)
class LogParseStats:
    total_lines: int = 0
    parsed_lines: int = 0
    skipped_lines: int = 0
    inserted_entries: int = 0


class LogParsingService:
    def __init__(
        self,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        entry_model=LogEntry,
        relation_field: str = "dataset",
    ) -> None:
        self.batch_size = batch_size
        self.entry_model = entry_model
        self.relation_field = relation_field
        # Consecutive lines usually share a timestamp and always share a UTC
        # offset, so both are memoised.
        self._last_ts_text: Optional[str] = None
        self._last_ts_value: Optional[datetime] = None
        self._offset_cache: dict[str, timedelta] = {}
        self._raw_insert_enabled = True
        self._insert_sql_cache: dict[int, str] = {}

    def parse_dataset(self, dataset: Dataset, *, file_path: Optional[str] = None) -> LogParseStats:
        source_path = Path(file_path or self._resolve_dataset_file_path(dataset))
        stats = LogParseStats()

        logger.info("Starting log parsing for dataset_id=%s path=%s", dataset.pk, source_path)
        dataset.status = Dataset.STATUS_PROCESSING
        dataset.save(update_fields=["status"])

        try:
            pending_rows: list[tuple] = []
            append_row = pending_rows.append
            parse_row = self._parse_row
            batch_size = self.batch_size
            total_lines = 0

            with self._open_log_file(source_path) as log_file:
                for raw_line in log_file:
                    if raw_line.isspace():
                        continue
                    total_lines += 1
                    row = parse_row(raw_line)
                    if row is None:
                        continue
                    append_row(row)

                    if len(pending_rows) >= batch_size:
                        stats.inserted_entries += self._insert_rows(dataset, pending_rows)
                        pending_rows.clear()
                        # Publish progress so the status endpoint can report it.
                        Dataset.objects.filter(pk=dataset.pk).update(
                            total_entries=stats.inserted_entries
                        )

            if pending_rows:
                stats.inserted_entries += self._insert_rows(dataset, pending_rows)

            stats.total_lines = total_lines
            stats.parsed_lines = stats.inserted_entries
            stats.skipped_lines = total_lines - stats.inserted_entries

            if not stats.inserted_entries:
                raise ValueError(
                    "The file is empty."
                    if not total_lines
                    else "No valid access-log lines were found. The file must be in "
                    "Apache Common/Combined or standard Nginx access-log format."
                )

            dataset.total_entries = stats.inserted_entries
            dataset.status = Dataset.STATUS_COMPLETED
            dataset.save(update_fields=["total_entries", "status"])

            logger.info(
                "Completed log parsing for dataset_id=%s total=%s parsed=%s skipped=%s inserted=%s",
                dataset.pk,
                stats.total_lines,
                stats.parsed_lines,
                stats.skipped_lines,
                stats.inserted_entries,
            )
            return stats
        except Exception as exc:
            # Do not leave a half-imported dataset behind.
            try:
                self.entry_model.objects.filter(**{self.relation_field: dataset}).delete()
                dataset.total_entries = 0
                dataset.save(update_fields=["total_entries"])
            except Exception:
                logger.exception(
                    "Could not remove partial entries for dataset_id=%s", dataset.pk
                )
            dataset.status = Dataset.STATUS_FAILED
            # Preserve a human-readable reason so the frontend and operators
            # can diagnose the failure without digging through server logs.
            dataset.processing_error = (
                str(exc).strip() or type(exc).__name__
            )[:1000]  # cap at 1 000 chars to protect the column
            dataset.save(update_fields=["status", "processing_error"])
            logger.exception(
                "Log parsing failed for dataset_id=%s path=%s",
                dataset.pk,
                source_path,
            )
            raise

    def parse_line(self, line: str) -> Optional[ParsedLogRecord]:
        row = self._parse_row(line)
        if row is None:
            return None
        (timestamp, ip_address, method, endpoint, protocol,
         status_code, response_size, referrer, user_agent, _error_flag) = row
        return ParsedLogRecord(
            timestamp=timestamp.replace(tzinfo=dt_timezone.utc),
            ip_address=ip_address,
            method=method,
            endpoint=endpoint,
            protocol=protocol,
            status_code=status_code,
            response_size=response_size,
            referrer=referrer,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------
    # Line parsing (hot path)
    # ------------------------------------------------------------------

    def _parse_row(self, line: str) -> Optional[tuple]:
        """
        Parse one raw line into a tuple ordered as ``_ROW_FIELDS``, or return
        None when the line is not a recognisable access-log record.

        The timestamp is returned as a *naive UTC* datetime; callers attach the
        timezone or format it as the database backend requires.
        """
        match = ACCESS_LOG_PATTERN.match(line)
        if match is None:
            return None
        ip_address, ts_text, request, status, size, referrer, user_agent = match.groups()

        # -- Request line: METHOD <target> [PROTOCOL] ----------------------
        method, _, rest = request.partition(" ")
        if not method or len(method) > _MAX_METHOD_LENGTH or not (method.isalpha() and method.isupper()):
            return None
        rest = rest.strip()
        target, _, tail = rest.rpartition(" ")
        if target and tail.startswith("HTTP/"):
            endpoint = target.rstrip()
            protocol = tail[:_MAX_PROTOCOL_LENGTH]
        else:
            endpoint = rest
            protocol = None
        if not endpoint:
            return None

        # -- Timestamp -----------------------------------------------------
        if ts_text == self._last_ts_text:
            timestamp = self._last_ts_value
        else:
            timestamp = self._parse_timestamp(ts_text)
            if timestamp is None:
                return None
            self._last_ts_text = ts_text
            self._last_ts_value = timestamp

        status_code = int(status)

        if size == "-" or not size.isdigit():
            response_size = None
        else:
            response_size = min(int(size), _MAX_RESPONSE_SIZE)

        if "\x00" in line:
            # PostgreSQL rejects NUL bytes in text columns.
            ip_address = ip_address.replace("\x00", "")
            endpoint = endpoint.replace("\x00", "")
            referrer = referrer.replace("\x00", "") if referrer else referrer
            user_agent = user_agent.replace("\x00", "") if user_agent else user_agent

        return (
            timestamp,
            ip_address[:_MAX_HOST_LENGTH],
            method,
            endpoint[:_MAX_ENDPOINT_LENGTH],
            protocol,
            status_code,
            response_size,
            referrer if referrer and referrer != "-" else None,
            user_agent if user_agent and user_agent != "-" else None,
            status_code >= 400,
        )

    def _parse_timestamp(self, text: str) -> Optional[datetime]:
        """Parse ``17/May/2015:08:05:32 +0000`` into a naive UTC datetime."""
        try:
            if len(text) == 26 and text[20] == " ":
                value = datetime(
                    int(text[7:11]),
                    _MONTHS[text[3:6]],
                    int(text[0:2]),
                    int(text[12:14]),
                    int(text[15:17]),
                    int(text[18:20]),
                )
                offset_text = text[21:]
                offset = self._offset_cache.get(offset_text)
                if offset is None:
                    sign = -1 if offset_text[0] == "-" else 1
                    offset = sign * timedelta(
                        hours=int(offset_text[1:3]), minutes=int(offset_text[3:5])
                    )
                    self._offset_cache[offset_text] = offset
                return value - offset if offset else value
        except (KeyError, ValueError, IndexError):
            pass

        # Unusual layout — fall back to the strict (slow) parser.
        try:
            parsed = datetime.strptime(text, LOG_TIME_FORMAT)
        except ValueError:
            return None
        return parsed.astimezone(dt_timezone.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # Database writes
    # ------------------------------------------------------------------

    def _insert_rows(self, dataset: Dataset, rows: list[tuple]) -> int:
        if not rows:
            return 0
        if self._raw_insert_enabled:
            try:
                with transaction.atomic():
                    self._raw_insert(dataset, rows)
                return len(rows)
            except Exception:
                # Never lose an upload to the fast path: fall back to the ORM
                # for this and all remaining batches.
                self._raw_insert_enabled = False
                logger.exception(
                    "Raw batch insert failed for dataset_id=%s; falling back to bulk_create",
                    dataset.pk,
                )
        return self._bulk_insert(
            [self._build_entry_instance(dataset=dataset, row=row) for row in rows]
        )

    def _raw_insert(self, dataset: Dataset, rows: list[tuple]) -> None:
        """Write *rows* using multi-row INSERT statements."""
        utc = dt_timezone.utc
        if connection.vendor == "sqlite":
            # SQLite stores datetimes as naive UTC text (Django's own format).
            def adapt_timestamp(value: datetime):
                return value.isoformat(" ")
        else:
            def adapt_timestamp(value: datetime):
                return value.replace(tzinfo=utc)

        columns_per_row = len(_ROW_FIELDS) + 1  # + dataset FK
        max_params = connection.features.max_query_params or 30_000
        chunk_size = max(1, min(len(rows), 1_000, max_params // columns_per_row))
        dataset_id = dataset.pk

        with connection.cursor() as cursor:
            for start in range(0, len(rows), chunk_size):
                chunk = rows[start : start + chunk_size]
                params: list = []
                extend = params.extend
                for row in chunk:
                    params.append(dataset_id)
                    params.append(adapt_timestamp(row[0]))
                    extend(row[1:])
                cursor.execute(self._insert_sql(len(chunk)), params)

    def _insert_sql(self, row_count: int) -> str:
        sql = self._insert_sql_cache.get(row_count)
        if sql is None:
            meta = self.entry_model._meta
            quote = connection.ops.quote_name
            field_names = (self.relation_field,) + _ROW_FIELDS
            columns = ", ".join(quote(meta.get_field(name).column) for name in field_names)
            placeholders = "(" + ", ".join(["%s"] * len(field_names)) + ")"
            sql = (
                f"INSERT INTO {quote(meta.db_table)} ({columns}) VALUES "
                + ", ".join([placeholders] * row_count)
            )
            self._insert_sql_cache[row_count] = sql
        return sql

    def _build_entry_instance(self, *, dataset: Dataset, row: tuple):
        values = dict(zip(_ROW_FIELDS, row))
        values["timestamp"] = row[0].replace(tzinfo=dt_timezone.utc)
        values[self.relation_field] = dataset
        return self.entry_model(**values)

    def _bulk_insert(self, objects: list[LogEntry]) -> int:
        if not objects:
            return 0
        with transaction.atomic():
            created = self.entry_model.objects.bulk_create(objects, batch_size=self.batch_size)
        return len(created)

    def _resolve_dataset_file_path(self, dataset: Dataset) -> str:
        if not dataset.file:
            raise FileNotFoundError(f"Dataset {dataset.pk} does not have an uploaded file.")
        try:
            return dataset.file.path
        except (ValueError, NotImplementedError) as exc:
            raise FileNotFoundError(
                f"Dataset {dataset.pk} file path is not available on local disk."
            ) from exc

    @staticmethod
    def _open_log_file(source_path: Path):
        if source_path.suffix.lower() == ".gz":
            return gzip_open(source_path, "rt", encoding="utf-8", errors="replace")
        return source_path.open("r", encoding="utf-8", errors="replace")

    @staticmethod
    def _iter_lines(log_file) -> Iterator[str]:
        for line in log_file:
            stripped = line.strip()
            if stripped:
                yield stripped


def parse_dataset_log(dataset_id: int, *, batch_size: int = DEFAULT_BATCH_SIZE) -> LogParseStats:
    dataset = Dataset.objects.get(pk=dataset_id)
    service = LogParsingService(batch_size=batch_size)
    return service.parse_dataset(dataset)
