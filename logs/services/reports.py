"""
Report generation service.

Produces two output formats from a dataset's analytics:
  - CSV  : tabular summary suitable for spreadsheet import.
  - JSON : structured dict returned directly to the caller (serialised by the view).

Both formats include: summary metrics, top endpoints, status distribution,
error breakdown, and detected anomalies.  No third-party libraries are
required — CSV uses Python's built-in csv module.
"""
from __future__ import annotations

import csv
import io

from django.utils import timezone


def generate_csv_report(dataset_id: int, owner, dataset_name: str = "") -> str:
    """
    Build a CSV report string for *dataset_id*.

    Sections:
      1. Report header (dataset name, generation timestamp)
      2. Summary metrics
      3. Top 20 endpoints
      4. Status code distribution
      5. Error breakdown (4xx vs 5xx)
      6. Detected anomalies

    Returns the raw CSV text as a Python string.
    """
    from logs.services.analytics import (
        get_summary_metrics,
        get_top_endpoints,
        get_error_breakdown,
        get_status_distribution,
    )
    from logs.services.anomaly_detection import get_anomalies

    metrics   = get_summary_metrics(dataset_id, owner=owner)
    top       = get_top_endpoints(dataset_id, owner=owner, limit=20)
    status    = get_status_distribution(dataset_id, owner=owner)
    errors    = get_error_breakdown(dataset_id, owner=owner)
    anomalies = get_anomalies(dataset_id, owner=owner)

    output = io.StringIO()
    w = csv.writer(output)

    # ── 1. Header ────────────────────────────────────────────────────────────
    w.writerow(["LogSight Analytics Report"])
    w.writerow(["Dataset", dataset_name or f"ID {dataset_id}"])
    w.writerow(["Generated", timezone.now().strftime("%Y-%m-%d %H:%M UTC")])
    w.writerow([])

    # ── 2. Summary metrics ───────────────────────────────────────────────────
    w.writerow(["=== SUMMARY METRICS ==="])
    w.writerow(["Metric", "Value"])
    dr = metrics.get("date_range", {})
    rows = [
        ("Total Requests",           metrics.get("total_requests", 0)),
        ("Total Errors",             metrics.get("total_errors", 0)),
        ("Error Rate (%)",           metrics.get("error_rate", 0)),
        ("Unique IP Addresses",      metrics.get("unique_ips", 0)),
        ("Avg. Response Size (B)",   metrics.get("avg_response_size", 0)),
        ("Log Start Date",           dr.get("start", "N/A")),
        ("Log End Date",             dr.get("end", "N/A")),
        ("Duration (hours)",         dr.get("duration_hours", "N/A")),
    ]
    w.writerows(rows)
    w.writerow([])

    # ── 3. Top endpoints ─────────────────────────────────────────────────────
    w.writerow(["=== TOP ENDPOINTS ==="])
    w.writerow(["Rank", "Endpoint", "Request Count", "Percentage (%)"])
    for i, ep in enumerate(top.get("endpoints", []), 1):
        w.writerow([i, ep["endpoint"], ep["count"], ep.get("percentage", "")])
    w.writerow([])

    # ── 4. Status distribution ───────────────────────────────────────────────
    w.writerow(["=== STATUS CODE DISTRIBUTION ==="])
    w.writerow(["Status Code", "Count"])
    for item in status.get("by_code", []):
        w.writerow([item["status_code"], item["count"]])
    w.writerow([])

    # ── 5. Error breakdown ───────────────────────────────────────────────────
    w.writerow(["=== ERROR BREAKDOWN ==="])
    w.writerow(["Error Class", "Count"])
    w.writerow(["4xx Client Errors", errors.get("errors_4xx", 0)])
    w.writerow(["5xx Server Errors", errors.get("errors_5xx", 0)])
    w.writerow([])
    w.writerow(["Top Error Status Codes"])
    w.writerow(["Status Code", "Count"])
    for item in errors.get("top_error_codes", []):
        w.writerow([item.get("status_code", ""), item.get("count", "")])
    w.writerow([])

    # ── 6. Anomalies ─────────────────────────────────────────────────────────
    anomaly_list = anomalies.get("anomalies", [])
    summary      = anomalies.get("summary", {})
    w.writerow(["=== DETECTED ANOMALIES ==="])
    w.writerow([
        "Total", summary.get("total_anomalies", 0),
        "High", summary.get("high_severity", 0),
        "Medium", summary.get("medium_severity", 0),
        "Low", summary.get("low_severity", 0),
    ])
    w.writerow([])
    if anomaly_list:
        w.writerow(["Time", "Type", "Severity", "Measured Value", "Threshold", "Explanation"])
        for a in anomaly_list:
            w.writerow([
                a.get("label", ""),
                a.get("type", ""),
                a.get("severity", ""),
                a.get("value", ""),
                a.get("threshold", ""),
                a.get("explanation", ""),
            ])
    else:
        w.writerow(["No anomalies detected."])

    return output.getvalue()


def generate_json_report(dataset_id: int, owner, dataset_name: str = "") -> dict:
    """
    Build a structured JSON-serialisable report dict for *dataset_id*.
    The view converts this to a downloadable HttpResponse.
    """
    from logs.services.analytics import (
        get_summary_metrics,
        get_top_endpoints,
        get_error_breakdown,
        get_status_distribution,
    )
    from logs.services.anomaly_detection import get_anomalies

    anomalies = get_anomalies(dataset_id, owner=owner)
    # The hourly chart series is only needed by the Anomaly Detection page.
    anomalies.pop("historical_series", None)

    return {
        "report_generated_at": timezone.now().isoformat(),
        "dataset_id":          dataset_id,
        "dataset_name":        dataset_name or f"ID {dataset_id}",
        "summary":             get_summary_metrics(dataset_id, owner=owner),
        "top_endpoints":       get_top_endpoints(dataset_id, owner=owner, limit=20),
        "status_distribution": get_status_distribution(dataset_id, owner=owner),
        "error_breakdown":     get_error_breakdown(dataset_id, owner=owner),
        "anomalies":           anomalies,
    }
