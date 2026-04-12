"""
Traffic forecasting service.

Uses two simple statistical methods to predict future request volumes:
  - Moving average  : average of the last N historical periods, extrapolated forward.
  - Linear regression: fits y = mx + b to all historical data, extrapolates forward.

Both approaches are implemented with pure Python (no external ML libraries) so the
code is easy to read, explain in an academic context, and has zero additional
dependencies.
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
    periods : int
        Number of future time-slots to predict (default 24 = next 24 hours).
    method  : "moving_avg" | "linear_regression"

    Returns
    -------
    {
        "historical": [{"label": str, "value": int, "timestamp": str}, ...],
        "forecast"  : [{"label": str, "value": int, "timestamp": str, "is_forecast": True}, ...],
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
    for item in qs:
        h = item["hour"]
        historical.append({
            "label":     h.strftime("%b %d %H:%M") if h else "",
            "value":     item["count"],
            "timestamp": h.isoformat() if h else "",
        })

    if not historical:
        return {
            "historical": [],
            "forecast":   [],
            "method":     method,
            "trend":      "stable",
            "summary":    {"total_historical_points": 0, "forecast_periods": periods},
        }

    values = [h["value"] for h in historical]

    # Choose forecasting algorithm.
    if method == "linear_regression":
        forecast_values = _linear_regression_forecast(values, periods)
    else:
        forecast_values = _moving_average_forecast(values, periods)

    trend = _determine_trend(values)

    # Build forecast entries anchored to the last historical timestamp.
    forecast: list[dict] = []
    last_ts_str = historical[-1]["timestamp"]
    try:
        last_dt = datetime.fromisoformat(last_ts_str)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=dt_timezone.utc)
    except (ValueError, TypeError):
        last_dt = datetime.now(tz=dt_timezone.utc)

    for i, val in enumerate(forecast_values):
        next_dt = last_dt + timedelta(hours=i + 1)
        forecast.append({
            "label":       next_dt.strftime("%b %d %H:%M"),
            "value":       max(0, round(val)),
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

def _moving_average_forecast(values: list[int], periods: int, window: int = 6) -> list[float]:
    """
    Extrapolate using a simple moving average of the last *window* data points.
    A trend correction term (the per-period slope) is added so the forecast
    rises or falls rather than projecting a flat line.
    """
    if not values:
        return [0.0] * periods

    w = min(window, len(values))
    recent = values[-w:]
    base = sum(recent) / len(recent)
    slope = _trend_slope(values)

    return [max(0.0, base + slope * (i + 1)) for i in range(periods)]


def _linear_regression_forecast(values: list[int], periods: int) -> list[float]:
    """
    Fit y = m·x + b to the full historical series using ordinary least squares,
    then extrapolate *periods* steps beyond the last observed point.
    """
    n = len(values)
    if n < 2:
        v = float(values[0]) if values else 0.0
        return [v] * periods

    x_vals = list(range(n))
    mean_x = sum(x_vals) / n
    mean_y = sum(values) / n

    num = sum((x_vals[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((x_vals[i] - mean_x) ** 2 for i in range(n))

    m = num / den if den else 0.0
    b = mean_y - m * mean_x

    return [max(0.0, m * (n + i) + b) for i in range(periods)]


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
    slope = _trend_slope(values)
    relative = slope / mean
    if relative > 0.02:
        return "increasing"
    if relative < -0.02:
        return "decreasing"
    return "stable"
