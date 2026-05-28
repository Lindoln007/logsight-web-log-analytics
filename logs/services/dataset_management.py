from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.db import connection as _db_connection
from django.db import transaction
from django.utils import timezone

from logs.models import Dataset
from logs.services.analytics import compute_and_persist_analytics
from logs.services.log_parser import DEFAULT_BATCH_SIZE, LogParseStats, parse_dataset_log

logger = logging.getLogger(__name__)
User = get_user_model()


@dataclass(slots=True)
class UploadProcessingResult:
    dataset: Dataset
    # stats is None when processing runs in the background.
    # Poll GET /logs/datasets/<id>/ and check dataset.status for completion.
    stats: Optional[LogParseStats] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        # A background-scheduled upload always returns status=pending initially.
        # "succeeded" here means the file was accepted and processing was scheduled.
        return self.error is None


def create_dataset_from_upload(
    *,
    owner: User,
    uploaded_file,
    name: Optional[str] = None,
    description: str = "",
    batch_size: Optional[int] = None,
) -> UploadProcessingResult:
    """
    Persist an uploaded file, create the dataset record, and schedule background parsing.

    Returns immediately with the new Dataset in PENDING status.
    Parsing and analytics computation run in a daemon thread.
    Callers should poll GET /logs/datasets/<id>/ to track progress.
    """
    effective_batch_size = batch_size or getattr(settings, "LOGS_PARSE_BATCH_SIZE", DEFAULT_BATCH_SIZE)

    dataset_name = (name or _derive_dataset_name(uploaded_file.name)).strip()
    dataset = Dataset.objects.create(
        owner=owner,
        name=dataset_name[:255] or "Uploaded Log Dataset",
        file=uploaded_file,
        file_size=getattr(uploaded_file, "size", None),
        description=(description or "").strip(),
        status=Dataset.STATUS_PENDING,
    )

    logger.info(
        "Created dataset_id=%s for user_id=%s file=%s size=%s bytes — scheduling background parse",
        dataset.pk,
        owner.pk,
        uploaded_file.name,
        getattr(uploaded_file, "size", None),
    )

    thread = threading.Thread(
        target=_process_dataset_in_background,
        kwargs={"dataset_id": dataset.pk, "batch_size": effective_batch_size},
        daemon=True,
        name=f"logsight-parse-{dataset.pk}",
    )
    thread.start()

    return UploadProcessingResult(dataset=dataset)


def _process_dataset_in_background(*, dataset_id: int, batch_size: int) -> None:
    """
    Background worker: parse the uploaded log file and compute analytics.

    Runs in a daemon thread started by create_dataset_from_upload().
    Each thread gets its own Django DB connection (Django's ORM is thread-safe
    but connections are not shared).  The connection is explicitly closed on exit
    to return it to the pool and avoid leaking persistent connections.

    Failure contract
    ----------------
    * If parse_dataset_log() raises, it already sets STATUS_FAILED and writes
      processing_error before re-raising.
    * _ensure_dataset_not_stuck() is a last-resort guard: if any exception
      escapes this function while the dataset is still in PENDING or PROCESSING
      (e.g., a DB connectivity error before parse_dataset_log() started), it
      transitions the dataset to FAILED so the record is never stuck.
    """
    try:
        # Django creates a new per-thread connection lazily on first query.
        # Discard any stale state inherited from the parent thread.
        close_old_connections()

        stats = parse_dataset_log(dataset_id, batch_size=batch_size)

        logger.info(
            "Background parse completed dataset_id=%s inserted=%s skipped=%s",
            dataset_id,
            stats.inserted_entries,
            stats.skipped_lines,
        )

        try:
            compute_and_persist_analytics(dataset_id)
        except Exception:
            logger.exception(
                "Analytics computation failed for dataset_id=%s; "
                "data is still accessible for on-demand computation.",
                dataset_id,
            )

        # Notify the owner that their dataset is ready.
        try:
            dataset_obj = Dataset.objects.get(pk=dataset_id)
            from logs.services.notifications import create_notification
            create_notification(
                dataset_obj.owner,
                f"Dataset '{dataset_obj.name}' finished processing — "
                f"{dataset_obj.total_entries:,} entries ready.",
                notification_type="dataset_ready",
                dataset=dataset_obj,
            )
        except Exception:
            logger.exception(
                "Failed to create dataset_ready notification for dataset_id=%s",
                dataset_id,
            )

    except Exception:
        logger.exception("Background processing failed for dataset_id=%s", dataset_id)
        # Guard: transition to FAILED if parse_dataset_log() did not already
        # do so (i.e., if the error occurred before parsing started).
        _ensure_dataset_not_stuck(dataset_id)
    finally:
        # Always release the connection so it doesn't linger after the thread exits.
        try:
            _db_connection.close()
        except Exception:
            pass


def _ensure_dataset_not_stuck(dataset_id: int) -> None:
    """
    Transition a dataset from PENDING or PROCESSING → FAILED if it is still
    in a non-terminal state.

    This is a no-op when parse_dataset_log() already wrote STATUS_FAILED.
    It fires only when an unexpected error occurred before the parser ran
    (e.g., a DB connection failure at thread start-up).
    """
    try:
        updated = Dataset.objects.filter(
            pk=dataset_id,
            status__in=[Dataset.STATUS_PENDING, Dataset.STATUS_PROCESSING],
        ).update(
            status=Dataset.STATUS_FAILED,
            processing_error="Processing was interrupted before it could complete.",
        )
        if updated:
            logger.warning(
                "Marked dataset_id=%s as failed via safety net (was stuck in non-terminal state)",
                dataset_id,
            )
    except Exception:
        logger.exception(
            "Safety net could not mark dataset_id=%s as failed — "
            "dataset may be stuck in PENDING/PROCESSING",
            dataset_id,
        )


def list_datasets(*, owner: User):
    """
    Return the authenticated user's datasets in reverse upload order.
    """

    return (
        Dataset.objects.only(
            "id",
            "owner_id",
            "name",
            "file",
            "file_size",
            "uploaded_at",
            "total_entries",
            "status",
            "description",
            "processing_error",
        )
        .filter(owner=owner)
        .order_by("-uploaded_at")
    )


def serialize_dataset(dataset: Dataset) -> dict:
    file_name = Path(dataset.file.name).name if dataset.file else None

    return {
        "id": dataset.pk,
        "owner_id": dataset.owner_id,
        "name": dataset.name,
        "file_name": file_name,
        "uploaded_at": timezone.localtime(dataset.uploaded_at).isoformat(),
        "file_size": dataset.file_size,
        "total_entries": dataset.total_entries,
        "status": dataset.status,
        "description": dataset.description,
        "processing_error": dataset.processing_error,
    }


def delete_dataset_and_file(dataset: Dataset, *, owner: User) -> bool:
    """
    Delete the dataset row and cascade to related log entries.
    The file is removed from storage after the DB transaction commits.

    Raises ValueError if the dataset is not owned by ``owner``.
    """
    if dataset.owner_id != owner.pk:
        raise ValueError(
            f"User {owner.pk} does not own dataset {dataset.pk}."
        )

    dataset_id = dataset.pk
    storage = dataset.file.storage if dataset.file else None
    file_name = dataset.file.name if dataset.file else None

    with transaction.atomic():
        dataset.delete()
        if storage and file_name:
            transaction.on_commit(lambda: storage.delete(file_name))

    logger.info("Deleted dataset_id=%s file=%s", dataset_id, file_name)
    return True


def _derive_dataset_name(file_name: str) -> str:
    path = Path(file_name)
    if path.suffix.lower() == ".gz" and len(path.suffixes) >= 2:
        return path.name[: -len(".gz")]
    return path.stem or path.name
