"""
Streaming log parser service for Apache Combined and standard Nginx access logs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from gzip import open as gzip_open
from pathlib import Path
from typing import Iterator, Optional

from django.db import transaction
from django.utils import timezone

from logs.models import Dataset, LogEntry

logger = logging.getLogger(__name__)

ACCESS_LOG_PATTERN = re.compile(
    r"^(?P<ip>\S+)\s+"
    r"(?P<identd>\S+)\s+"
    r"(?P<authuser>\S+)\s+"
    r"\[(?P<timestamp>[^\]]+)\]\s+"
    r'"(?P<request>[^"]*)"\s+'
    r"(?P<status>\d{3})\s+"
    r"(?P<size>\S+)"
    r'(?:\s+"(?P<referrer>[^"]*)"\s+"(?P<user_agent>[^"]*)")?'
    r"\s*$"
)

REQUEST_PATTERN = re.compile(
    r"^(?P<method>[A-Z]+)\s+"
    r"(?P<endpoint>\S+)"
    r"(?:\s+(?P<protocol>HTTP/\d(?:\.\d)?))?$"
)

LOG_TIME_FORMAT = "%d/%b/%Y:%H:%M:%S %z"
DEFAULT_BATCH_SIZE = 1000


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

    def parse_dataset(self, dataset: Dataset, *, file_path: Optional[str] = None) -> LogParseStats:
        source_path = Path(file_path or self._resolve_dataset_file_path(dataset))
        stats = LogParseStats()

        logger.info("Starting log parsing for dataset_id=%s path=%s", dataset.pk, source_path)
        dataset.status = Dataset.STATUS_PROCESSING
        dataset.save(update_fields=["status"])

        try:
            pending_objects = []

            with self._open_log_file(source_path) as log_file:
                for line_number, raw_line in enumerate(self._iter_lines(log_file), start=1):
                    stats.total_lines += 1
                    record = self.parse_line(raw_line)

                    if record is None:
                        stats.skipped_lines += 1
                        logger.debug(
                            "Skipping malformed or unsupported line dataset_id=%s line=%s",
                            dataset.pk,
                            line_number,
                        )
                        continue

                    pending_objects.append(self._build_entry_instance(dataset=dataset, record=record))
                    stats.parsed_lines += 1

                    if len(pending_objects) >= self.batch_size:
                        inserted = self._bulk_insert(pending_objects)
                        stats.inserted_entries += inserted
                        pending_objects.clear()

            if pending_objects:
                inserted = self._bulk_insert(pending_objects)
                stats.inserted_entries += inserted

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
        except Exception:
            dataset.status = Dataset.STATUS_FAILED
            dataset.save(update_fields=["status"])
            logger.exception(
                "Log parsing failed for dataset_id=%s path=%s",
                dataset.pk,
                source_path,
            )
            raise

    def parse_line(self, line: str) -> Optional[ParsedLogRecord]:
        match = ACCESS_LOG_PATTERN.match(line)
        if not match:
            return None

        request_match = REQUEST_PATTERN.match(match.group("request"))
        if not request_match:
            return None

        try:
            timestamp = datetime.strptime(match.group("timestamp"), LOG_TIME_FORMAT)
        except ValueError:
            return None

        if timezone.is_naive(timestamp):
            timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())

        return ParsedLogRecord(
            timestamp=timestamp,
            ip_address=match.group("ip"),
            method=request_match.group("method"),
            endpoint=request_match.group("endpoint"),
            protocol=self._normalize_optional_value(request_match.group("protocol")),
            status_code=int(match.group("status")),
            response_size=self._parse_response_size(match.group("size")),
            referrer=self._normalize_optional_value(match.group("referrer")),
            user_agent=self._normalize_optional_value(match.group("user_agent")),
        )

    def _build_entry_instance(self, *, dataset: Dataset, record: ParsedLogRecord):
        return self.entry_model(
            **{
                self.relation_field: dataset,
                "timestamp": record.timestamp,
                "ip_address": record.ip_address,
                "method": record.method,
                "endpoint": record.endpoint,
                "protocol": record.protocol,
                "status_code": record.status_code,
                "response_size": record.response_size,
                "referrer": record.referrer,
                "user_agent": record.user_agent,
                "error_flag": record.status_code >= 400,
            }
        )

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

    @staticmethod
    def _parse_response_size(value: str) -> Optional[int]:
        if value in {"", "-"}:
            return None
        return int(value)

    @staticmethod
    def _normalize_optional_value(value: Optional[str]) -> Optional[str]:
        if value in (None, "", "-"):
            return None
        return value


def parse_dataset_log(dataset_id: int, *, batch_size: int = DEFAULT_BATCH_SIZE) -> LogParseStats:
    dataset = Dataset.objects.get(pk=dataset_id)
    service = LogParsingService(batch_size=batch_size)
    return service.parse_dataset(dataset)
