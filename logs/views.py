from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from logs.forms import DEFAULT_MAX_UPLOAD_SIZE, LogUploadForm
from logs.models import AlertRule, Dataset
from logs.services import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    check_and_trigger_alerts,
    compute_and_persist_analytics,
    create_alert_rule,
    create_dataset_from_upload,
    delete_alert_rule,
    delete_dataset_and_file,
    generate_csv_report,
    generate_json_report,
    get_alert_history,
    get_alert_rules,
    get_analytics_overview,
    get_anomalies,
    get_error_breakdown,
    get_hourly_traffic,
    get_log_entries,
    get_method_distribution,
    get_request_trends,
    get_status_distribution,
    get_summary_metrics,
    get_top_endpoints,
    get_traffic_forecast,
    list_datasets,
    serialize_dataset,
    update_alert_rule,
)
from logs.services.analytics import (
    DEFAULT_TOP_ENDPOINTS_LIMIT,
    DEFAULT_TRENDS_LIMIT,
    MAX_TOP_ENDPOINTS_LIMIT,
)

logger = logging.getLogger(__name__)


def _get_user_dataset(request: HttpRequest, dataset_id: int) -> Dataset:
    return get_object_or_404(Dataset, pk=dataset_id, owner=request.user)


# ---------------------------------------------------------------------------
# Dataset CRUD views
# ---------------------------------------------------------------------------


@login_required
@require_GET
def dataset_list_view(request: HttpRequest) -> JsonResponse:
    datasets = [serialize_dataset(dataset) for dataset in list_datasets(owner=request.user)]
    return JsonResponse({"datasets": datasets}, status=200)


@login_required
@require_GET
def dataset_detail_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    dataset = _get_user_dataset(request, dataset_id)
    return JsonResponse({"dataset": serialize_dataset(dataset)}, status=200)


@login_required
@require_GET
def dataset_status_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/status/

    Lightweight polling endpoint for background-processing progress.
    Returns only the fields needed to track processing state and display
    a result — intentionally smaller than the full dataset detail response.

    Response shape
    --------------
    {
        "id": int,
        "name": "access.log",
        "status": "pending" | "processing" | "completed" | "failed",
        "total_entries": int,       # 0 while processing; final count when done
        "file_size": int | null,    # bytes
        "uploaded_at": "ISO8601",
        "processing_error": str | null   # non-null only when status == "failed"
    }
    """
    dataset = _get_user_dataset(request, dataset_id)
    return JsonResponse(
        {
            "id": dataset.pk,
            "name": dataset.name,
            "status": dataset.status,
            "total_entries": dataset.total_entries,
            "file_size": dataset.file_size,
            "uploaded_at": timezone.localtime(dataset.uploaded_at).isoformat(),
            "processing_error": dataset.processing_error,
        },
        status=200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def upload_log_view(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(
            {
                "accepted_types": [".log", ".txt", ".log.gz", ".txt.gz"],
                "max_upload_size": getattr(
                    settings,
                    "LOGS_MAX_UPLOAD_SIZE",
                    DEFAULT_MAX_UPLOAD_SIZE,
                ),
                "required_encoding_note": "Uploaded files must be Apache or Nginx access logs.",
            },
            status=200,
        )

    form = LogUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)

    result = create_dataset_from_upload(
        owner=request.user,
        uploaded_file=form.cleaned_data["file"],
        name=form.cleaned_data.get("name"),
        description=form.cleaned_data.get("description", ""),
    )

    payload = {
        "dataset": serialize_dataset(result.dataset),
        # stats are not available yet — parsing runs in the background.
        # Poll GET /logs/datasets/<id>/ until dataset.status == "completed".
        "stats": None,
    }

    # 202 Accepted: file received, parsing scheduled in background thread.
    return JsonResponse(payload, status=202)


@login_required
@require_http_methods(["POST", "DELETE"])
def delete_dataset_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    dataset = _get_user_dataset(request, dataset_id)
    serialized_dataset = serialize_dataset(dataset)
    delete_dataset_and_file(dataset, owner=request.user)
    return JsonResponse(
        {
            "deleted": True,
            "dataset": serialized_dataset,
        },
        status=200,
    )


# ---------------------------------------------------------------------------
# Analytics views
# ---------------------------------------------------------------------------
#
# All analytics views follow the same pattern:
#   1. Resolve the dataset (404 if not found).
#   2. Reject requests for datasets that are not yet "completed" (409 Conflict).
#   3. Parse optional query-string parameters with safe defaults.
#   4. Delegate to the analytics service layer — no computation here.
#   5. Return the service result as JSON.
#
# The 409 check on the overview / summary views guards against serving
# zero-filled results for datasets that are still processing or failed.
# Individual distribution / breakdown views are intentionally more lenient
# because they return safe zeros for empty querysets regardless.


def _dataset_ready_or_error(dataset: Dataset) -> JsonResponse | None:
    """Return a 409 JsonResponse if the dataset is not completed, else None."""
    if dataset.status != Dataset.STATUS_COMPLETED:
        return JsonResponse(
            {
                "error": "Dataset analytics are not available yet.",
                "status": dataset.status,
                "hint": (
                    "Wait for the dataset to reach 'completed' status, "
                    "or upload a new log file."
                ),
            },
            status=409,
        )
    return None


def _parse_int_param(request: HttpRequest, name: str, default: int, minimum: int = 1) -> int:
    """Read an integer query-string parameter with a fallback default."""
    try:
        value = int(request.GET[name])
        return max(minimum, value)
    except (KeyError, ValueError, TypeError):
        return default


@login_required
@require_GET
def analytics_overview_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/

    Return all analytics sections in one response.  Accepts:
      ?interval=hour|day   — time-series bucket size (default: hour)
      ?limit=N             — top endpoints count (default: 10, max: 100)
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error

    interval = request.GET.get("interval", "hour")
    limit = _parse_int_param(request, "limit", DEFAULT_TOP_ENDPOINTS_LIMIT)

    data = get_analytics_overview(
        dataset_id,
        owner=request.user,
        trends_interval=interval,
        top_endpoints_limit=min(limit, MAX_TOP_ENDPOINTS_LIMIT),
    )
    return JsonResponse(data, status=200)


@login_required
@require_GET
def analytics_summary_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/summary/

    Return high-level aggregate metrics: total requests, error counts, unique
    IPs, error rate, average response size, and log date range.
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error

    return JsonResponse(get_summary_metrics(dataset_id, owner=request.user), status=200)


@login_required
@require_GET
def analytics_status_distribution_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/status-distribution/

    Return per-status-code counts and per-category (1xx–5xx) aggregates.
    """
    _get_user_dataset(request, dataset_id)
    return JsonResponse(get_status_distribution(dataset_id, owner=request.user), status=200)


@login_required
@require_GET
def analytics_request_trends_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/request-trends/

    Return time-series request counts.  Accepts:
      ?interval=hour|day   — bucket granularity (default: hour)
      ?limit=N             — max data points returned (default: 168)
    """
    _get_user_dataset(request, dataset_id)

    interval = request.GET.get("interval", "hour")
    limit = _parse_int_param(request, "limit", DEFAULT_TRENDS_LIMIT)

    return JsonResponse(
        get_request_trends(dataset_id, owner=request.user, interval=interval, limit=limit),
        status=200,
    )


@login_required
@require_GET
def analytics_top_endpoints_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/top-endpoints/

    Return the most frequently accessed endpoints ranked by request count.
    Accepts:
      ?limit=N   — number of endpoints (default: 10, max: 100)
    """
    _get_user_dataset(request, dataset_id)
    limit = _parse_int_param(request, "limit", DEFAULT_TOP_ENDPOINTS_LIMIT)

    return JsonResponse(
        get_top_endpoints(dataset_id, owner=request.user, limit=min(limit, MAX_TOP_ENDPOINTS_LIMIT)),
        status=200,
    )


@login_required
@require_GET
def analytics_hourly_traffic_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/hourly-traffic/

    Return request count for each hour of the day (0–23), plus the peak
    and quiet hours.
    """
    _get_user_dataset(request, dataset_id)
    return JsonResponse(get_hourly_traffic(dataset_id, owner=request.user), status=200)


@login_required
@require_GET
def analytics_method_distribution_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/method-distribution/

    Return request counts grouped by HTTP method (GET, POST, …).
    """
    _get_user_dataset(request, dataset_id)
    return JsonResponse(get_method_distribution(dataset_id, owner=request.user), status=200)


@login_required
@require_GET
def analytics_error_breakdown_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/errors/

    Return 4xx vs 5xx breakdown with top error status codes and the endpoints
    that generated the most errors.
    """
    _get_user_dataset(request, dataset_id)
    return JsonResponse(get_error_breakdown(dataset_id, owner=request.user), status=200)


@login_required
@require_GET
def log_entries_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/entries/

    Return a paginated, optionally filtered list of log entries for a dataset.

    Query parameters
    ----------------
    page        : int   — 1-based page number (default: 1)
    page_size   : int   — rows per page (default: 25, max: 100)
    status      : str   — one of all | 1xx | 2xx | 3xx | 4xx | 5xx | errors
    search      : str   — substring match on endpoint or IP address

    Response shape
    --------------
    {
        "dataset_id": int,
        "items": [
            {
                "id": int,
                "timestamp": "ISO8601",
                "ip_address": "str",
                "method": "GET",
                "endpoint": "/api/...",
                "status_code": 200,
                "response_size": 1024 | null,
                "raw": "reconstructed access-log line"
            },
            ...
        ],
        "total": int,
        "page": int,
        "page_size": int,
        "total_pages": int
    }
    """
    _get_user_dataset(request, dataset_id)

    page = _parse_int_param(request, "page", 1)
    page_size = _parse_int_param(request, "page_size", PAGE_SIZE_DEFAULT)
    page_size = min(page_size, PAGE_SIZE_MAX)
    status_filter = request.GET.get("status", "all").strip().lower()
    search = request.GET.get("search", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    data = get_log_entries(
        dataset_id,
        request.user,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )
    return JsonResponse(data, status=200)


@login_required
@require_http_methods(["POST"])
def analytics_refresh_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    POST /logs/datasets/<id>/analytics/refresh/

    Force-recompute all analytics for a dataset, invalidate the cache, and
    persist the results to AnalyticsSummary.  Responds with the timestamp of
    the freshly computed summary.
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error

    summary = compute_and_persist_analytics(dataset_id)
    return JsonResponse(
        {
            "refreshed": True,
            "dataset_id": dataset_id,
            "computed_at": summary.computed_at.isoformat(),
            "total_requests": summary.total_requests,
            "total_errors": summary.total_errors,
        },
        status=200,
    )


# ---------------------------------------------------------------------------
# Traffic forecasting view
# ---------------------------------------------------------------------------


@login_required
@require_GET
def forecast_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/forecast/

    Predict future request volume from historical trends.

    Query parameters
    ----------------
    periods : int   — number of future hours to forecast (default 24, max 168)
    method  : str   — "moving_avg" (default) | "linear_regression"
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error

    periods = min(_parse_int_param(request, "periods", 24), 168)
    method  = request.GET.get("method", "moving_avg")
    if method not in ("moving_avg", "linear_regression"):
        method = "moving_avg"

    data = get_traffic_forecast(dataset_id, owner=request.user, periods=periods, method=method)
    return JsonResponse(data, status=200)


# ---------------------------------------------------------------------------
# Anomaly detection view
# ---------------------------------------------------------------------------


@login_required
@require_GET
def anomaly_detection_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/anomalies/

    Detect statistical anomalies in request frequency and error rates.

    Query parameters
    ----------------
    sensitivity : "low" | "medium" (default) | "high"
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error

    sensitivity = request.GET.get("sensitivity", "medium")
    if sensitivity not in ("low", "medium", "high"):
        sensitivity = "medium"

    data = get_anomalies(dataset_id, owner=request.user, sensitivity=sensitivity)
    return JsonResponse(data, status=200)


# ---------------------------------------------------------------------------
# Alert management views
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def alerts_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET  /logs/datasets/<id>/alerts/  — list all alert rules for the dataset.
    POST /logs/datasets/<id>/alerts/  — create a new alert rule.

    POST body (JSON):
    {
        "name": "High error rate",
        "error_rate_threshold": 5.0,
        "request_spike_multiplier": 3.0,
        "is_active": true
    }
    """
    _get_user_dataset(request, dataset_id)

    if request.method == "GET":
        rules   = get_alert_rules(dataset_id, owner=request.user)
        history = get_alert_history(dataset_id, owner=request.user, limit=20)
        return JsonResponse({"rules": rules, "recent_alerts": history}, status=200)

    # POST — create rule
    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    rule = create_alert_rule(dataset_id, owner=request.user, data=body)
    return JsonResponse({"rule": rule}, status=201)


@login_required
@require_http_methods(["PUT", "PATCH", "DELETE"])
def alert_rule_detail_view(request: HttpRequest, dataset_id: int, rule_id: int) -> JsonResponse:
    """
    PUT/PATCH /logs/datasets/<id>/alerts/<rule_id>/  — update a rule.
    DELETE    /logs/datasets/<id>/alerts/<rule_id>/  — delete a rule.
    """
    _get_user_dataset(request, dataset_id)

    # Verify the rule belongs to this user.
    get_object_or_404(AlertRule, pk=rule_id, owner=request.user, dataset_id=dataset_id)

    if request.method == "DELETE":
        delete_alert_rule(rule_id, owner=request.user)
        return JsonResponse({"deleted": True, "rule_id": rule_id}, status=200)

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    rule = update_alert_rule(rule_id, owner=request.user, data=body)
    return JsonResponse({"rule": rule}, status=200)


@login_required
@require_http_methods(["POST"])
def alerts_check_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    POST /logs/datasets/<id>/alerts/check/

    Evaluate all active rules and create Alert records for any that fire.
    Returns the newly-triggered alerts (empty list if none fired).
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error

    triggered = check_and_trigger_alerts(dataset_id, owner=request.user)
    return JsonResponse({"triggered": triggered, "count": len(triggered)}, status=200)


@login_required
@require_GET
def alert_history_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/alerts/history/

    Return recent alert instances for the dataset (newest first).
    """
    _get_user_dataset(request, dataset_id)
    limit   = min(_parse_int_param(request, "limit", 50), 200)
    history = get_alert_history(dataset_id, owner=request.user, limit=limit)
    return JsonResponse({"alerts": history, "count": len(history)}, status=200)


# ---------------------------------------------------------------------------
# Report generation views
# ---------------------------------------------------------------------------


@login_required
@require_GET
def report_view(request: HttpRequest, dataset_id: int) -> HttpResponse:
    """
    GET /logs/datasets/<id>/report/

    Download a report for the dataset.

    Query parameters
    ----------------
    format : "csv" (default) | "json"
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error

    fmt = request.GET.get("format", "csv").lower()

    if fmt == "json":
        data     = generate_json_report(dataset_id, owner=request.user, dataset_name=dataset.name)
        response = JsonResponse(data, status=200)
        response["Content-Disposition"] = (
            f'attachment; filename="logsight_report_{dataset_id}.json"'
        )
        return response

    # Default: CSV
    csv_text = generate_csv_report(dataset_id, owner=request.user, dataset_name=dataset.name)
    response = HttpResponse(csv_text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="logsight_report_{dataset_id}.csv"'
    )
    return response


# ---------------------------------------------------------------------------
# Dashboard preferences view
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def dashboard_preferences_view(request: HttpRequest) -> JsonResponse:
    """
    GET  /logs/preferences/  — retrieve the current user's dashboard preferences.
    POST /logs/preferences/  — save preferences.

    POST body (JSON):
    {
        "hidden_widgets": ["stat-latency", "chart-traffic"],
        "preferences": { "default_interval": "day" }
    }
    """
    from logs.models import DashboardPreference

    if request.method == "GET":
        pref, _ = DashboardPreference.objects.get_or_create(user=request.user)
        return JsonResponse(
            {
                "hidden_widgets": pref.hidden_widgets,
                "preferences":    pref.preferences,
                "updated_at":     pref.updated_at.isoformat() if pref.updated_at else None,
            },
            status=200,
        )

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    pref, _ = DashboardPreference.objects.get_or_create(user=request.user)
    if "hidden_widgets" in body:
        pref.hidden_widgets = body["hidden_widgets"] if isinstance(body["hidden_widgets"], list) else []
    if "preferences" in body:
        pref.preferences = body["preferences"] if isinstance(body["preferences"], dict) else {}
    pref.save()

    return JsonResponse(
        {
            "saved":          True,
            "hidden_widgets": pref.hidden_widgets,
            "preferences":    pref.preferences,
        },
        status=200,
    )
