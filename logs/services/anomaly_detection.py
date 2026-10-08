"""
Anomaly detection service.

Two interchangeable detection methods work on the same hourly time-buckets:

``statistical``  — mean ± k·σ thresholding (the original method).
    Looks at one metric at a time and flags:
      1. Request-volume spikes  — hours where request count is far above the mean.
      2. Error-rate spikes      — hours where the fraction of failed requests is
                                  far above the mean error rate.
    Simple, transparent, and straightforward to explain.  Sensitivity sets *k*:
      low    → k = 3.0  (only extreme outliers flagged)
      medium → k = 2.0  (moderate outliers)
      high   → k = 1.5  (more sensitive, more false-positives)

``isolation_forest``  — unsupervised machine learning (scikit-learn).
    Each hour becomes one row of features (request count, error rate, unique
    visitors, average response size, requests per visitor, hour of day).  An
    Isolation Forest is trained on the uploaded dataset itself — no labels and
    no pre-trained model — and scores how easily each hour can be separated
    from the rest.  Because it looks at all features together it also catches
    hours that are unusual only *in combination* (e.g. normal volume, but from
    two visitors and mostly errors), which single-metric thresholds miss.
"""
from __future__ import annotations

import logging
import math

from logs.services import rollup
from logs.services.scope import cached_result

logger = logging.getLogger(__name__)

METHOD_STATISTICAL = "statistical"
METHOD_ISOLATION_FOREST = "isolation_forest"
METHODS = (METHOD_STATISTICAL, METHOD_ISOLATION_FOREST)

SENSITIVITY_K: dict[str, float] = {
    "low":    3.0,
    "medium": 2.0,
    "high":   1.5,
}

# Minimum number of hourly buckets required before running detection.
MIN_BUCKETS = 3

# The Isolation Forest needs enough hours to learn what "normal" looks like.
MIN_BUCKETS_ISOLATION_FOREST = 24

# Isolation Forest decision scores are centred on 0: the more negative, the
# easier the hour was to isolate.  An hour is flagged when its score falls
# below the cut-off for the chosen sensitivity.  Cut-offs were calibrated on
# real access logs so that "medium" flags roughly the most unusual 3 % of hours
# in a noisy log and nothing at all in a uniformly quiet one.
ISOLATION_FOREST_CUTOFF: dict[str, float] = {
    "low":    -0.12,
    "medium": -0.08,
    "high":   -0.05,
}
ISOLATION_FOREST_TREES = 200


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@cached_result("anomalies")
def get_anomalies(
    dataset_id: int,
    owner,
    sensitivity: str = "medium",
    method: str = METHOD_STATISTICAL,
) -> dict:
    """
    Detect anomalous hours in *dataset_id*'s log data.

    Returns
    -------
    {
        "anomalies": [
            {
                "type":        "request_spike" | "error_rate_spike"
                               | "traffic_drop" | "unusual_pattern",
                "timestamp":   ISO-8601 str,
                "label":       human-readable label,
                "value":       float,          # measured value
                "threshold":   float,          # upper bound / score cut-off
                "mean":        float,
                "std":         float,
                "severity":    "high" | "medium" | "low",
                "explanation": str,
                "score":       float,          # isolation_forest only
            },
            ...
        ],
        "summary": {
            "total_anomalies": int,
            "high_severity":   int,
            "medium_severity": int,
            "low_severity":    int,
        },
        "sensitivity":       str,
        "k_factor":          float,
        "method":            method that actually ran,
        "requested_method":  method the caller asked for,
        "note":              str | None,   # set when a fallback happened
        "historical_series": [{"label", "value"}, ...],   # hourly request counts
    }
    """
    if sensitivity not in SENSITIVITY_K:
        sensitivity = "medium"
    if method not in METHODS:
        method = METHOD_STATISTICAL
    k = SENSITIVITY_K[sensitivity]

    hours = _hourly_buckets(dataset_id, owner)

    used_method = method
    note = None
    model_info = None
    anomalies: list[dict] | None = None

    if method == METHOD_ISOLATION_FOREST:
        if len(hours) < MIN_BUCKETS_ISOLATION_FOREST:
            note = (
                f"Isolation Forest needs at least {MIN_BUCKETS_ISOLATION_FOREST} hours of "
                f"data to learn a baseline; this dataset has {len(hours)}. "
                "Showing the statistical method instead."
            )
        else:
            try:
                anomalies, model_info = _detect_with_isolation_forest(hours, sensitivity)
            except ImportError:
                note = (
                    "scikit-learn is not installed on the server, so the "
                    "statistical method was used instead."
                )
            except Exception:
                logger.exception("Isolation Forest failed for dataset_id=%s", dataset_id)
                note = (
                    "The Isolation Forest model could not be fitted to this "
                    "dataset, so the statistical method was used instead."
                )

    if anomalies is None:
        used_method = METHOD_STATISTICAL
        anomalies = []
        anomalies.extend(_detect_request_spikes(hours, k))
        anomalies.extend(_detect_error_rate_spikes(hours, k))

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
        "sensitivity":      sensitivity,
        "k_factor":         k,
        "method":           used_method,
        "requested_method": method,
        "note":             note,
        "model":            model_info,
        "historical_series": [
            {"label": _label(h["hour"]), "value": h["total"]} for h in hours
        ],
    }


# ---------------------------------------------------------------------------
# Shared hourly aggregation
# ---------------------------------------------------------------------------

def _hourly_buckets(dataset_id: int, owner) -> list[dict]:
    """
    Return one row per hour that contains at least one request::

        {"hour": datetime, "total": int, "errors": int,
         "unique_ips": int, "avg_size": float | None}

    Read from the dataset's hourly roll-up, so no raw log rows are scanned.
    """
    return [row for row in rollup.hourly_rows(dataset_id, owner) if row["total"] > 0]


def _label(hour) -> str:
    return hour.strftime("%b %d %H:%M") if hour else ""


# ---------------------------------------------------------------------------
# Statistical detection (mean ± k·σ)
# ---------------------------------------------------------------------------

def _detect_request_spikes(hours: list[dict], k: float) -> list[dict]:
    """Flag hours whose request count is more than k standard deviations above the mean."""
    if len(hours) < MIN_BUCKETS:
        return []

    counts = [item["total"] for item in hours]
    mean, std = _mean_std(counts)
    if std == 0:
        return []

    upper = mean + k * std
    anomalies: list[dict] = []

    for item in hours:
        c = item["total"]
        if c > upper:
            deviation = (c - mean) / std
            severity  = _severity_from_deviation(deviation, k)
            h = item["hour"]
            anomalies.append({
                "type":        "request_spike",
                "timestamp":   h.isoformat() if h else "",
                "label":       _label(h),
                "value":       c,
                "threshold":   round(upper, 1),
                "mean":        round(mean, 1),
                "std":         round(std, 1),
                "severity":    severity,
                "explanation": (
                    f"Request count {c:,} exceeded the upper threshold "
                    f"{upper:,.0f} ({k:.1f}σ above mean {mean:.0f})"
                ),
            })

    return anomalies


def _detect_error_rate_spikes(hours: list[dict], k: float) -> list[dict]:
    """Flag hours whose error rate (%) is more than k standard deviations above the mean."""
    if len(hours) < MIN_BUCKETS:
        return []

    rates = [_error_rate(item) for item in hours]

    mean, std = _mean_std(rates)
    if std == 0:
        return []

    upper = mean + k * std
    # Suppress alerts when the threshold itself is below a meaningful baseline.
    if upper < 1.0:
        return []

    anomalies: list[dict] = []

    for idx, item in enumerate(hours):
        rate = rates[idx]
        if rate > upper:
            deviation = (rate - mean) / std
            severity  = _severity_from_deviation(deviation, k)
            h = item["hour"]
            anomalies.append({
                "type":        "error_rate_spike",
                "timestamp":   h.isoformat() if h else "",
                "label":       _label(h),
                "value":       round(rate, 2),
                "threshold":   round(upper, 2),
                "mean":        round(mean, 2),
                "std":         round(std, 2),
                "severity":    severity,
                "explanation": (
                    f"Error rate {rate:.1f}% exceeded upper threshold "
                    f"{upper:.1f}% ({k:.1f}σ above mean {mean:.1f}%)"
                ),
            })

    return anomalies


# ---------------------------------------------------------------------------
# Machine-learning detection (Isolation Forest)
# ---------------------------------------------------------------------------

# (key, label shown to the user, value formatter)
_FEATURES = (
    ("requests",        "requests",                lambda v: f"{v:,.0f}"),
    ("error_rate",      "error rate",              lambda v: f"{v:.1f}%"),
    ("unique_ips",      "unique visitors",         lambda v: f"{v:,.0f}"),
    ("avg_size",        "average response size",   lambda v: f"{v:,.0f} B"),
    ("requests_per_ip", "requests per visitor",    lambda v: f"{v:,.1f}"),
)


def _detect_with_isolation_forest(hours: list[dict], sensitivity: str) -> tuple[list[dict], dict]:
    """
    Fit an Isolation Forest on the dataset's own hourly feature matrix and
    return (anomalies, model_info).

    Raises ImportError when scikit-learn is unavailable so the caller can fall
    back to the statistical method.
    """
    import numpy as np
    from sklearn.ensemble import IsolationForest

    raw = np.array(
        [
            [
                h["total"],
                _error_rate(h),
                h["unique_ips"],
                float(h["avg_size"] or 0.0),
                h["total"] / max(h["unique_ips"], 1),
            ]
            for h in hours
        ],
        dtype=float,
    )

    # Counts are heavy-tailed, so they are log-scaled before modelling.  The
    # hour of day is encoded on a circle (23:00 sits next to 00:00) so the
    # model can learn that a volume that is normal at midday is odd at 3 am.
    hour_angle = np.array([2 * math.pi * h["hour"].hour / 24 for h in hours])
    matrix = np.column_stack(
        [
            np.log1p(raw[:, 0]),
            raw[:, 1],
            np.log1p(raw[:, 2]),
            np.log1p(raw[:, 3]),
            np.log1p(raw[:, 4]),
            np.sin(hour_angle),
            np.cos(hour_angle),
        ]
    )

    model = IsolationForest(
        n_estimators=ISOLATION_FOREST_TREES,
        contamination="auto",
        random_state=42,
    )
    model.fit(matrix)
    scores = model.decision_function(matrix)

    cutoff = ISOLATION_FOREST_CUTOFF[sensitivity]

    # Robust "typical" value and spread per feature, used only to explain
    # *why* an hour was flagged in plain language.
    medians = np.median(raw, axis=0)
    spreads = 1.4826 * np.median(np.abs(raw - medians), axis=0)
    fallback = raw.std(axis=0)
    spreads = np.where(spreads > 0, spreads, np.where(fallback > 0, fallback, 1.0))

    anomalies: list[dict] = []
    for idx in np.where(scores < cutoff)[0]:
        hour = hours[idx]
        score = float(scores[idx])
        deviations = (raw[idx] - medians) / spreads
        order = np.argsort(-np.abs(deviations))
        top = int(order[0])

        reasons = []
        for feature_idx in order[:2]:
            if abs(deviations[feature_idx]) < 1.0:
                continue
            _key, label, fmt = _FEATURES[feature_idx]
            direction = "higher" if deviations[feature_idx] > 0 else "lower"
            reasons.append(
                f"{label} {fmt(raw[idx][feature_idx])} "
                f"({direction} than the typical {fmt(medians[feature_idx])})"
            )
        if not reasons:
            reasons.append("an unusual combination of otherwise normal values")

        anomalies.append({
            "type":        _anomaly_type(_FEATURES[top][0], deviations[top]),
            "timestamp":   hour["hour"].isoformat(),
            "label":       _label(hour["hour"]),
            "value":       hour["total"],
            "threshold":   cutoff,
            "mean":        round(float(medians[0]), 1),
            "std":         round(float(spreads[0]), 1),
            "severity":    _severity_from_score(score, cutoff),
            "score":       round(score, 4),
            "explanation": "Isolation Forest flagged this hour: " + "; ".join(reasons) + ".",
        })

    model_info = {
        "algorithm":      "Isolation Forest",
        "trees":          ISOLATION_FOREST_TREES,
        "hours_analysed": len(hours),
        "features":       [label for _key, label, _fmt in _FEATURES] + ["hour of day"],
        "score_cutoff":   cutoff,
    }
    return anomalies, model_info


def _anomaly_type(feature_key: str, deviation: float) -> str:
    """Name the anomaly after the feature that deviates most from typical."""
    if feature_key == "error_rate" and deviation > 0:
        return "error_rate_spike"
    if feature_key in ("requests", "unique_ips"):
        return "request_spike" if deviation > 0 else "traffic_drop"
    return "unusual_pattern"


def _severity_from_score(score: float, cutoff: float) -> str:
    """The further below the cut-off, the more isolated (and severe) the hour."""
    depth = cutoff - score
    if depth >= 0.08:
        return "high"
    if depth >= 0.03:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Statistical utilities
# ---------------------------------------------------------------------------

def _error_rate(bucket: dict) -> float:
    return (bucket["errors"] / bucket["total"] * 100.0) if bucket["total"] > 0 else 0.0


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
