"""
Anomaly detection service.

Detects two classes of anomaly per hourly time-bucket:
  1. Request-volume spikes  — hours where request count is far above the mean.
  2. Error-rate spikes      — hours where the fraction of failed requests is
                             far above the mean error rate.

Method: mean ± k·σ (standard deviation) thresholding — simple, transparent,
and straightforward to explain in an academic context.  The caller controls
sensitivity via *k*:
  low    → k = 3.0  (only extreme outliers flagged)
  medium → k = 2.0  (moderate outliers)
  high   → k = 1.5  (more sensitive, more false-positives)
"""
from __future__ import annotations

import math

from django.db.models import Count, Q
from django.db.models.functions import TruncHour


SENSITIVITY_K: dict[str, float] = {
    "low":    3.0,
    "medium": 2.0,
    "high":   1.5,
}

# Minimum number of hourly buckets required before running detection.
MIN_BUCKETS = 3


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_anomalies(
    dataset_id: int,
    owner,
    sensitivity: str = "medium",
) -> dict:
    """
    Detect anomalous hours in *dataset_id*'s log data.

    Returns
    -------
    {
        "anomalies": [
            {
                "type":        "request_spike" | "error_rate_spike",
                "timestamp":   ISO-8601 str,
                "label":       human-readable label,
                "value":       float,          # measured value
                "threshold":   float,          # upper bound (mean + k*σ)
                "mean":        float,
                "std":         float,
                "severity":    "high" | "medium" | "low",
                "explanation": str,
            },
            ...
        ],
        "summary": {
            "total_anomalies": int,
            "high_severity":   int,
            "medium_severity": int,
            "low_severity":    int,
        },
        "sensitivity": str,
        "k_factor":    float,
    }
    """
    k = SENSITIVITY_K.get(sensitivity, 2.0)

    anomalies: list[dict] = []
    anomalies.extend(_detect_request_spikes(dataset_id, owner, k))
    anomalies.extend(_detect_error_rate_spikes(dataset_id, owner, k))

    # Stable sort by timestamp so anomalies appear chronologically in the UI.
    anomalies.sort(key=lambda a: a.get("timestamp", ""))

    high   = sum(1 for a in anomalies if a["severity"] == "high")
    medium = sum(1 for a in anomalies if a["severity"] == "medium")
    low    = sum(1 for a in anomalies if a["severity"] == "low")

    return {
        "anomalies": anomalies,
        "summary": {
            "total_anomalies": len(anomalies),
            "high_severity":   high,
            "medium_severity": medium,
            "low_severity":    low,
        },
        "sensitivity": sensitivity,
        "k_factor":    k,
    }


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _detect_request_spikes(dataset_id: int, owner, k: float) -> list[dict]:
    """Flag hours whose request count is more than k standard deviations above the mean."""
    from logs.models import LogEntry  # local import avoids circular deps

    items = list(
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .annotate(hour=TruncHour("timestamp"))
        .values("hour")
        .annotate(count=Count("id"))
        .order_by("hour")
    )

    if len(items) < MIN_BUCKETS:
        return []

    counts = [item["count"] for item in items]
    mean, std = _mean_std(counts)
    if std == 0:
        return []

    upper = mean + k * std
    anomalies: list[dict] = []

    for item in items:
        c = item["count"]
        if c > upper:
            deviation = (c - mean) / std
            severity  = _severity_from_deviation(deviation, k)
            h = item["hour"]
            anomalies.append({
                "type":        "request_spike",
                "timestamp":   h.isoformat() if h else "",
                "label":       h.strftime("%b %d %H:%M") if h else "",
                "value":       c,
                "threshold":   round(upper, 1),
                "mean":        round(mean, 1),
                "std":         round(std, 1),
                "severity":    severity,
                "explanation": (
                    f"Request count {c:,} exceeded the upper threshold "
                    f"{upper:,.0f} ({k:.1f}\u03c3 above mean {mean:.0f})"
                ),
            })

    return anomalies


def _detect_error_rate_spikes(dataset_id: int, owner, k: float) -> list[dict]:
    """Flag hours whose error rate (%) is more than k standard deviations above the mean."""
    from logs.models import LogEntry

    items = list(
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .annotate(hour=TruncHour("timestamp"))
        .values("hour")
        .annotate(
            total=Count("id"),
            errors=Count("id", filter=Q(error_flag=True)),
        )
        .order_by("hour")
    )

    if len(items) < MIN_BUCKETS:
        return []

    rates = [
        (item["errors"] / item["total"] * 100.0) if item["total"] > 0 else 0.0
        for item in items
    ]

    mean, std = _mean_std(rates)
    if std == 0:
        return []

    upper = mean + k * std
    # Suppress alerts when the threshold itself is below a meaningful baseline.
    if upper < 1.0:
        return []

    anomalies: list[dict] = []

    for idx, item in enumerate(items):
        rate = rates[idx]
        if rate > upper:
            deviation = (rate - mean) / std
            severity  = _severity_from_deviation(deviation, k)
            h = item["hour"]
            anomalies.append({
                "type":        "error_rate_spike",
                "timestamp":   h.isoformat() if h else "",
                "label":       h.strftime("%b %d %H:%M") if h else "",
                "value":       round(rate, 2),
                "threshold":   round(upper, 2),
                "mean":        round(mean, 2),
                "std":         round(std, 2),
                "severity":    severity,
                "explanation": (
                    f"Error rate {rate:.1f}% exceeded upper threshold "
                    f"{upper:.1f}% ({k:.1f}\u03c3 above mean {mean:.1f}%)"
                ),
            })

    return anomalies


# ---------------------------------------------------------------------------
# Statistical utilities
# ---------------------------------------------------------------------------

def _mean_std(values: list[float]) -> tuple[float, float]:
    """Return (mean, population standard deviation) for *values*."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(variance)


def _severity_from_deviation(deviation: float, k: float) -> str:
    """Map the number of standard deviations above the threshold to a severity label."""
    # 'deviation' here is (value - mean) / std, so k is the threshold deviation.
    if deviation >= k * 2.0:
        return "high"
    if deviation >= k * 1.4:
        return "medium"
    return "low"
