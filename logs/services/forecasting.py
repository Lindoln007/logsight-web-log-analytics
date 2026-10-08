"""
Traffic forecasting service.

Three methods predict future hourly request volumes:

  - Seasonal pattern  ("moving_avg"): averages traffic per hour-of-day from the
                       history, then projects those patterns forward with a
                       trend adjustment.  Produces a wavy forecast that follows
                       the dataset's daily rhythm.
  - Linear regression ("linear_regression"): fits y = mx + b to the history and
                       extrapolates.  Good for showing a clean trend direction.
  - Machine learning  ("ml"): a Random Forest regressor (scikit-learn) trained
                       on the uploaded dataset itself.  For every past hour it
                       learns from the hour of day, the day of week, and what
                       traffic looked like 1, 2, 3 and 24 hours earlier, then
                       predicts forward one hour at a time, feeding each
                       prediction back in as history for the next.

Every response also carries an ``evaluation`` block: the most recent part of
the history is hidden, each method forecasts it from the earlier data only,
and the forecasts are scored against what really happened (MAE / RMSE).  This
gives an honest, like-for-like accuracy comparison of the three methods.

The seasonal and linear methods return fixed-percentage confidence bands; the
ML method derives its band from the spread of the individual trees.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.db.models import Count
from django.db.models.functions import TruncHour

logger = logging.getLogger(__name__)

METHOD_SEASONAL = "moving_avg"
METHOD_LINEAR = "linear_regression"
METHOD_ML = "ml"
METHODS = (METHOD_SEASONAL, METHOD_LINEAR, METHOD_ML)

METHOD_LABELS = {
    METHOD_SEASONAL: "Seasonal Pattern",
    METHOD_LINEAR:   "Linear Regression",
    METHOD_ML:       "Machine Learning (Random Forest)",
}

# Only the most recent slice of history is modelled and charted.  Eight weeks
# is plenty to learn daily and weekly rhythms and keeps long-running logs fast.
MAX_HISTORY_HOURS = 8 * 7 * 24

# The ML model looks back 24 hours, so it needs a few full days to train on.
MIN_HOURS_FOR_ML = 72
ML_LAGS = (1, 2, 3, 24)
ML_TREES = 100

# Back-test window: hide the last 20 % of the history (bounded to 6 h – 7 days).
MIN_HOURS_FOR_EVALUATION = 24
EVALUATION_CACHE_TTL = 15 * 60
HOURLY_CACHE_TTL = 15 * 60


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_traffic_forecast(
    dataset_id: int,
    owner,
    periods: int = 24,
    method: str = METHOD_SEASONAL,
) -> dict:
    """
    Forecast future hourly request counts for *dataset_id*.

    Parameters
    ----------
    periods : int   — number of future hours to predict (default 24).
    method  : str   — "moving_avg" (seasonal pattern, default)
                      | "linear_regression" | "ml"

    Returns
    -------
    {
        "historical": [{"label", "value", "timestamp"}, ...],
        "forecast"  : [{"label", "value", "upper", "lower", "timestamp", "is_forecast"}, ...],
        "method"    : method that actually ran,
        "requested_method": method the caller asked for,
        "note"      : str | None,       # set when the ML method fell back
        "trend"     : "increasing" | "decreasing" | "stable",
        "summary"   : {...},
        "evaluation": {...} | None,     # back-test accuracy of every method
    }
    """
    if method not in METHODS:
        method = METHOD_SEASONAL
    periods = max(1, int(periods))

    timestamps, values = _hourly_series(dataset_id, owner)

    if not values:
        return {
            "historical": [],
            "forecast":   [],
            "method":     method,
            "requested_method": method,
            "note":       None,
            "trend":      "stable",
            "summary":    {"total_historical_points": 0, "forecast_periods": periods},
            "evaluation": None,
        }

    historical = [
        {
            "label":     ts.strftime("%b %d %H:%M"),
            "value":     value,
            "timestamp": ts.isoformat(),
        }
        for ts, value in zip(timestamps, values)
    ]
    last_dt = timestamps[-1]

    fcast_vals, uppers, lowers, used_method, note = _run_method(
        method, values, timestamps, periods
    )

    trend = _determine_trend(values)

    # Build forecast point list.
    forecast: list[dict] = []
    for i, (val, upper, lower) in enumerate(zip(fcast_vals, uppers, lowers)):
        next_dt = last_dt + timedelta(hours=i + 1)
        forecast.append({
            "label":       next_dt.strftime("%b %d %H:%M"),
            "value":       max(0, round(val)),
            "upper":       max(0, round(upper)),
            "lower":       max(0, round(lower)),
            "timestamp":   next_dt.isoformat(),
            "is_forecast": True,
        })

    avg_hist = round(sum(values) / len(values), 1) if values else 0

    return {
        "historical": historical,
        "forecast":   forecast,
        "method":     used_method,
        "requested_method": method,
        "note":       note,
        "trend":      trend,
        "summary": {
            "total_historical_points": len(historical),
            "forecast_periods":        periods,
            "avg_historical":          avg_hist,
            "peak_historical":         max(values) if values else 0,
        },
        "evaluation": _cached_evaluation(dataset_id, timestamps, values),
    }


# ---------------------------------------------------------------------------
# Hourly history
# ---------------------------------------------------------------------------

def _hourly_series(dataset_id: int, owner) -> tuple[list[datetime], list[int]]:
    """
    Return a *continuous* hourly series (timestamps, request counts) covering
    the most recent ``MAX_HISTORY_HOURS`` of the dataset.

    Hours with no requests are absent from the GROUP BY result; they are filled
    with zero here so that "the value 24 hours ago" always means exactly that.
    """
    from logs.models import Dataset, LogEntry  # local import avoids circular deps

    # total_entries changes whenever rows are added, which retires the old key.
    total_entries = (
        Dataset.objects.filter(pk=dataset_id, owner=owner)
        .values_list("total_entries", flat=True)
        .first()
    )
    if total_entries is None:
        return [], []
    cache_key = f"forecast:hourly:{dataset_id}:{total_entries}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rows = list(
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .annotate(hour=TruncHour("timestamp"))
        .values("hour")
        .annotate(count=Count("id"))
        .order_by("-hour")[:MAX_HISTORY_HOURS]
    )
    counts: dict[datetime, int] = {}
    for row in rows:
        hour = row["hour"]
        if hour is None:
            continue
        if hour.tzinfo is None:
            hour = hour.replace(tzinfo=dt_timezone.utc)
        counts[hour] = row["count"]

    if not counts:
        return [], []

    last = max(counts)
    first = max(min(counts), last - timedelta(hours=MAX_HISTORY_HOURS - 1))
    total_hours = int((last - first).total_seconds() // 3600) + 1

    timestamps = [first + timedelta(hours=i) for i in range(total_hours)]
    values = [counts.get(ts, 0) for ts in timestamps]
    cache.set(cache_key, (timestamps, values), HOURLY_CACHE_TTL)
    return timestamps, values


# ---------------------------------------------------------------------------
# Method dispatch
# ---------------------------------------------------------------------------

def _run_method(
    method: str,
    values: list[int],
    timestamps: list[datetime],
    periods: int,
) -> tuple[list[float], list[float], list[float], str, str | None]:
    """
    Run *method* and return (forecast, upper, lower, method_used, note).

    The ML method falls back to the seasonal method — with an explanatory
    note — when there is too little history or scikit-learn is unavailable.
    """
    last_dt = timestamps[-1]
    note = None

    if method == METHOD_LINEAR:
        return (*_linear_regression_forecast(values, periods), METHOD_LINEAR, None)

    if method == METHOD_ML:
        if len(values) < MIN_HOURS_FOR_ML:
            note = (
                f"The machine-learning model needs at least {MIN_HOURS_FOR_ML} hours "
                f"(3 days) of history; this dataset has {len(values)}. "
                "Showing the Seasonal Pattern forecast instead."
            )
        else:
            try:
                return (*_ml_forecast(values, timestamps, periods), METHOD_ML, None)
            except ImportError:
                note = (
                    "scikit-learn is not installed on the server, so the "
                    "Seasonal Pattern forecast was used instead."
                )
            except Exception:
                logger.exception("ML forecast failed; falling back to seasonal")
                note = (
                    "The machine-learning model could not be trained on this "
                    "dataset, so the Seasonal Pattern forecast was used instead."
                )

    # "moving_avg" key kept for API compatibility; algorithm is seasonal.
    return (*_seasonal_forecast(values, timestamps, periods, last_dt), METHOD_SEASONAL, note)


# ---------------------------------------------------------------------------
# Forecasting algorithms
# ---------------------------------------------------------------------------

def _seasonal_forecast(
    values: list[int],
    timestamps: list,
    periods: int,
    last_dt: datetime,
) -> tuple[list[float], list[float], list[float]]:
    """
    Seasonal pattern forecast.

    1. Compute the average request count for each hour-of-day (0–23) from the
       full historical series — this is the dataset's "typical day" profile.
    2. For each forecast step, look up which hour-of-day it falls on and use
       that hour's average as the base value.
    3. Apply a trend adjustment: scale the base by how the overall traffic
       level is changing (OLS slope relative to the mean).
    4. Return ±20 % confidence bands around each forecast point.
    """
    if not values:
        zeros = [0.0] * periods
        return zeros, zeros, zeros

    # Build hourly profile from historical data.
    hourly_totals = [0.0] * 24
    hourly_counts = [0]   * 24
    for ts, val in zip(timestamps, values):
        if ts is not None:
            h = ts.hour if hasattr(ts, "hour") else 0
            hourly_totals[h] += val
            hourly_counts[h] += 1

    overall_mean = sum(values) / len(values)
    hourly_avg = [
        hourly_totals[h] / hourly_counts[h] if hourly_counts[h] > 0 else overall_mean
        for h in range(24)
    ]

    slope = _trend_slope(values)

    fcast_vals: list[float] = []
    uppers:     list[float] = []
    lowers:     list[float] = []

    for i in range(periods):
        next_dt = last_dt + timedelta(hours=i + 1)
        hour    = next_dt.hour

        base = hourly_avg[hour]

        # Scale the seasonal base by the overall trend direction.
        if overall_mean > 0:
            trend_factor = 1.0 + (slope * (i + 1)) / overall_mean
            trend_factor = max(0.05, trend_factor)   # floor to avoid negative scale
        else:
            trend_factor = 1.0

        val    = max(0.0, base * trend_factor)
        margin = val * 0.20

        fcast_vals.append(val)
        uppers.append(val + margin)
        lowers.append(max(0.0, val - margin))

    return fcast_vals, uppers, lowers


def _linear_regression_forecast(
    values: list[int],
    periods: int,
) -> tuple[list[float], list[float], list[float]]:
    """
    OLS straight-line extrapolation with ±15 % confidence bands.
    """
    n = len(values)
    if n < 2:
        v = float(values[0]) if values else 0.0
        fv = [v] * periods
        return fv, [v * 1.15] * periods, [max(0.0, v * 0.85)] * periods

    x_vals  = list(range(n))
    mean_x  = sum(x_vals) / n
    mean_y  = sum(values) / n

    num = sum((x_vals[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((x_vals[i] - mean_x) ** 2 for i in range(n))

    m = num / den if den else 0.0
    b = mean_y - m * mean_x

    fcast_vals = [max(0.0, m * (n + i) + b) for i in range(periods)]
    uppers     = [v * 1.15 for v in fcast_vals]
    lowers     = [max(0.0, v * 0.85) for v in fcast_vals]

    return fcast_vals, uppers, lowers


# ---------------------------------------------------------------------------
# Machine-learning forecast (Random Forest on lag features)
# ---------------------------------------------------------------------------

def _ml_features(hour_dt: datetime, history) -> list[float]:
    """
    Describe one hour to the model using only information available *before*
    that hour: its position in the day/week and recent traffic levels.
    *history* holds every hourly count up to (not including) the hour itself.
    """
    angle = 2 * math.pi * hour_dt.hour / 24
    weekday = hour_dt.weekday()
    recent_day = history[-24:]
    return [
        math.sin(angle),
        math.cos(angle),
        float(weekday),
        1.0 if weekday >= 5 else 0.0,
        *(float(history[-lag]) for lag in ML_LAGS),
        sum(history[-6:]) / 6.0,
        sum(recent_day) / len(recent_day),
    ]


def _ml_forecast(
    values: list[int],
    timestamps: list[datetime],
    periods: int,
) -> tuple[list[float], list[float], list[float]]:
    """
    Train a Random Forest on the dataset's own history and forecast *periods*
    hours ahead recursively.  Raises ImportError if scikit-learn is missing.
    """
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor

    max_lag = max(ML_LAGS)
    series = [float(v) for v in values]

    rows = [_ml_features(timestamps[i], series[:i]) for i in range(max_lag, len(series))]
    targets = series[max_lag:]

    model = RandomForestRegressor(
        n_estimators=ML_TREES,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=1,
    )
    model.fit(np.asarray(rows), np.asarray(targets))

    history = list(series)
    last_dt = timestamps[-1]
    fcast_vals: list[float] = []
    uppers: list[float] = []
    lowers: list[float] = []

    for step in range(periods):
        next_dt = last_dt + timedelta(hours=step + 1)
        features = np.asarray([_ml_features(next_dt, history)], dtype=np.float32)
        # Each tree gives its own estimate; their spread is the model's own
        # measure of uncertainty and becomes the confidence band.
        per_tree = np.array(
            [tree.predict(features, check_input=False)[0] for tree in model.estimators_]
        )
        prediction = max(0.0, float(per_tree.mean()))
        fcast_vals.append(prediction)
        lowers.append(max(0.0, float(np.percentile(per_tree, 10))))
        uppers.append(max(prediction, float(np.percentile(per_tree, 90))))
        history.append(prediction)

    return fcast_vals, uppers, lowers


# ---------------------------------------------------------------------------
# Back-test evaluation
# ---------------------------------------------------------------------------

def _cached_evaluation(dataset_id: int, timestamps: list[datetime], values: list[int]) -> dict | None:
    """Evaluate once per dataset state; the result is independent of the chosen method."""
    cache_key = f"forecast:evaluation:{dataset_id}:{len(values)}:{timestamps[-1].isoformat()}:{sum(values)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    try:
        evaluation = _evaluate_methods(timestamps, values)
    except Exception:
        logger.exception("Forecast evaluation failed for dataset_id=%s", dataset_id)
        evaluation = None
    # Cache "no evaluation" as an empty dict so it is not recomputed every call.
    cache.set(cache_key, evaluation or {}, EVALUATION_CACHE_TTL)
    return evaluation


def _evaluate_methods(timestamps: list[datetime], values: list[int]) -> dict | None:
    """
    Hold out the most recent hours, forecast them with every method using only
    the earlier data, and score each forecast against the real values.
    """
    n = len(values)
    if n < MIN_HOURS_FOR_EVALUATION:
        return None

    holdout = max(6, min(7 * 24, n // 5))
    train_values = values[:-holdout]
    train_timestamps = timestamps[:-holdout]
    actual = values[-holdout:]

    results: list[dict] = []
    for method in METHODS:
        if method == METHOD_ML and len(train_values) < MIN_HOURS_FOR_ML:
            continue
        predicted, _upper, _lower, used, _note = _run_method(
            method, train_values, train_timestamps, holdout
        )
        if used != method:
            continue  # method fell back; do not score it under the wrong name
        errors = [p - a for p, a in zip(predicted, actual)]
        mae = sum(abs(e) for e in errors) / holdout
        rmse = math.sqrt(sum(e * e for e in errors) / holdout)
        results.append({
            "method": method,
            "label":  METHOD_LABELS[method],
            "mae":    round(mae, 2),
            "rmse":   round(rmse, 2),
        })

    if not results:
        return None

    best = min(results, key=lambda r: r["mae"])
    mean_actual = sum(actual) / holdout
    return {
        "holdout_hours":  holdout,
        "training_hours": len(train_values),
        "mean_actual":    round(mean_actual, 2),
        "best_method":    best["method"],
        "results":        results,
    }


# ---------------------------------------------------------------------------
# Trend helpers
# ---------------------------------------------------------------------------

def _trend_slope(values: list[int]) -> float:
    """Return the OLS slope of *values* (change per time step)."""
    n = len(values)
    if n < 2:
        return 0.0
    x = list(range(n))
    mean_x = sum(x) / n
    mean_y = sum(values) / n
    num = sum((x[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((x[i] - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def _determine_trend(values: list[int]) -> str:
    """Classify the trend as increasing, decreasing, or stable."""
    if len(values) < 2:
        return "stable"
    mean = sum(values) / len(values)
    if mean == 0:
        return "stable"
    slope    = _trend_slope(values)
    relative = slope / mean
    if relative > 0.02:
        return "increasing"
    if relative < -0.02:
        return "decreasing"
    return "stable"
