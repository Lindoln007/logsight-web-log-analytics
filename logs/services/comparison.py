"""
Dataset comparison service for LogSight.

Computes side-by-side analytics for two datasets owned by the same user,
plus percentage-change deltas between them.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def compare_datasets(dataset_id_a: int, dataset_id_b: int, owner) -> dict[str, Any]:
    """
    Return a structured comparison of two datasets.

    Both datasets must be owned by *owner*.  Raises Dataset.DoesNotExist if
    either ID is not found for that owner.

    Return shape::

        {
            "dataset_a": {
                "id": int,
                "name": str,
                "metrics": {...},
                "status_dist": {...},
                "top_endpoints": [...],
                "hourly_traffic": {...},
            },
            "dataset_b": { same shape },
            "deltas": {
                "total_requests": float,    # % change (B vs A)
                "error_rate": float,         # pp difference (B - A)
                "avg_response_size": float,  # % change (B vs A)
                "unique_visitors": float,    # % change (B vs A)
            },
        }
    """
    from logs.models import Dataset
    from logs.services.analytics import (
        get_summary_metrics,
        get_status_distribution,
        get_top_endpoints,
        get_hourly_traffic,
    )

    ds_a = Dataset.objects.get(pk=dataset_id_a, owner=owner)
    ds_b = Dataset.objects.get(pk=dataset_id_b, owner=owner)

    metrics_a = get_summary_metrics(dataset_id_a, owner=owner)
    metrics_b = get_summary_metrics(dataset_id_b, owner=owner)

    status_a = get_status_distribution(dataset_id_a, owner=owner)
    status_b = get_status_distribution(dataset_id_b, owner=owner)

    endpoints_a = get_top_endpoints(dataset_id_a, owner=owner)
    endpoints_b = get_top_endpoints(dataset_id_b, owner=owner)

    hourly_a = get_hourly_traffic(dataset_id_a, owner=owner)
    hourly_b = get_hourly_traffic(dataset_id_b, owner=owner)

    def _pct_change(a, b) -> float:
        """Percentage change from a to b.  Returns 0.0 when a is zero."""
        a_f = float(a or 0)
        b_f = float(b or 0)
        if a_f == 0:
            return 0.0
        return round((b_f - a_f) / a_f * 100, 2)

    def _pp_diff(a, b) -> float:
        """Percentage-point difference (b - a)."""
        return round(float(b or 0) - float(a or 0), 3)

    deltas = {
        "total_requests": _pct_change(
            metrics_a["total_requests"], metrics_b["total_requests"]
        ),
        "error_rate": _pp_diff(
            metrics_a["error_rate"], metrics_b["error_rate"]
        ),
        "avg_response_size": _pct_change(
            metrics_a.get("avg_response_size") or 0,
            metrics_b.get("avg_response_size") or 0,
        ),
        "unique_visitors": _pct_change(
            metrics_a["unique_ips"], metrics_b["unique_ips"]
        ),
    }

    return {
        "dataset_a": {
            "id": ds_a.pk,
            "name": ds_a.name,
            "metrics": metrics_a,
            "status_dist": status_a,
            "top_endpoints": endpoints_a.get("endpoints", []),
            "hourly_traffic": hourly_a,
        },
        "dataset_b": {
            "id": ds_b.pk,
            "name": ds_b.name,
            "metrics": metrics_b,
            "status_dist": status_b,
            "top_endpoints": endpoints_b.get("endpoints", []),
            "hourly_traffic": hourly_b,
        },
        "deltas": deltas,
    }
