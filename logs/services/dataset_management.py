from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.contrib.auth import get_user_model
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
    stats: Optional[LogParseStats] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.dataset.status == Dataset.STATUS_COMPLETED


def create_dataset_from_upload(
    *,
    owner: User,
    uploaded_file,
    name: Optional[str] = None,
    description: str = "",
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> UploadProcessingResult:
    """
    Persist an uploaded file, create the dataset record, and trigger parsing.
    """

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
        "Created dataset_id=%s for user_id=%s uploaded file=%s size=%s",
        dataset.pk,
        owner.pk,
        uploaded_file.name,
        getattr(uploaded_file, "size", None),
    )

    try:
        dataset.status = Dataset.STATUS_PROCESSING
        dataset.save(update_fields=["status"])

        stats = parse_dataset_log(dataset.pk, batch_size=batch_size)

        try:
            compute_and_persist_analytics(dataset.pk)
        except Exception:
            logger.exception(
                "Analytics computation failed for dataset_id=%s; "
                "data is still available for on-demand computation.",
                dataset.pk,
            )

        dataset.refresh_from_db(
            fields=["owner", "status", "total_entries", "file", "file_size", "uploaded_at"]
        )
        return UploadProcessingResult(dataset=dataset, stats=stats)
    except Exception as exc:
        dataset.refresh_from_db(
            fields=["owner", "status", "total_entries", "file", "file_size", "uploaded_at"]
        )
        logger.exception("Dataset processing failed dataset_id=%s", dataset.pk)
        return UploadProcessingResult(dataset=dataset, error=str(exc))


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
