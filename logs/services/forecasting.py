"""
Traffic forecasting service.

Uses two methods to predict future request volumes:
  - Seasonal pattern : averages traffic per hour-of-day from the full history,
                       then projects those patterns forward with a trend adjustment.
                       Produces a realistic wavy forecast that rises and falls with
                       the dataset's natural daily rhythm.
  - Linear regression: fits y = mx + b to all historical data and extrapolates.
                       Good for showing a clean trend direction.

Both methods also return upper/lower confidence bands (±20 % of the forecast value).
Pure Python — no external ML libraries required.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db.models import Count
from django.db.models.functions import TruncHour


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_traffic_forecast(
    dataset_id: int,
    owner,
    periods: int = 24,
    method: str = "moving_avg",
) -> dict:
    """
    Forecast future hourly request counts for *dataset_id*.

    Parameters
    ----------
    periods : int   — number of future hours to predict (default 24).
    method  : str   — "moving_avg" (seasonal pattern, default) | "linear_regression"

    Returns
    -------
    {
        "historical": [{"label", "value", "timestamp"}, ...],
        "forecast"  : [{"label", "value", "upper", "lower", "timestamp", "is_forecast"}, ...],
        "method"    : str,
        "trend"     : "increasing" | "decreasing" | "stable",
        "summary"   : {...},
    }
    """
    from logs.models import LogEntry  # local import avoids circular deps

    qs = (
        LogEntry.objects
        .filter(dataset_id=dataset_id, dataset__owner=owner)
        .annotate(hour=TruncHour("timestamp"))
        .values("hour")
        .annotate(count=Count("id"))
        .order_by("hour")
    )

    historical = []
    raw_timestamps = []   # datetime objects for seasonal calc
    for item in qs:
        h = item["hour"]
        historical.append({
            "label":     h.strftime("%b %d %H:%M") if h else "",
            "value":     item["count"],
            "timestamp": h.isoformat() if h else "",
        })
        raw_timestamps.append(h)

    if not historical:
        return {
            "historical": [],
            "forecast":   [],
            "method":     method,
            "trend":      "stable",
            "summary":    {"total_historical_points": 0, "forecast_periods": periods},
        }

    values = [h["value"] for h in historical]

    # Anchor the forecast to the last historical timestamp.
    last_ts_str = historical[-1]["timestamp"]
    try:
        last_dt = datetime.fromisoformat(last_ts_str)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=dt_timezone.utc)
    except (ValueError, TypeError):
        last_dt = datetime.now(tz=dt_timezone.utc)

    # Choose forecasting algorithm.
    if method == "linear_regression":
        fcast_vals, uppers, lowers = _linear_regression_forecast(values, periods)
    else:
        # "moving_avg" key kept for API compatibility; algorithm is now seasonal.
        fcast_vals, uppers, lowers = _seasonal_forecast(values, raw_timestamps, periods, last_dt)

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
        "method":     method,
        "trend":      trend,
        "summary": {
            "total_historical_points": len(historical),
            "forecast_periods":        periods,
            "avg_historical":          avg_hist,
            "peak_historical":         max(values) if values else 0,
        },
    }


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
