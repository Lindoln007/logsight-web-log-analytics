from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from logs.forms import DEFAULT_MAX_UPLOAD_SIZE, LogUploadForm
from logs.models import AlertRule, Dataset
from logs.services import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    auto_check_alerts,
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
    if request.method == "POST":
        from logs.services.rbac import get_or_create_profile
        try:
            if get_or_create_profile(request.user).role not in ("admin", "analyst"):
                return JsonResponse({"error": "You need Analyst or Admin access to upload log files."}, status=403)
        except Exception:
            return JsonResponse({"error": "Role check failed."}, status=403)

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
    from logs.services.rbac import get_or_create_profile
    try:
        if get_or_create_profile(request.user).role not in ("admin", "analyst"):
            return JsonResponse({"error": "You need Analyst or Admin access to delete datasets."}, status=403)
    except Exception:
        return JsonResponse({"error": "Role check failed."}, status=403)
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


def _parse_date_param(request: HttpRequest, name: str, end_of_day: bool = False):
    """
    Parse a YYYY-MM-DD query-string date into an aware datetime, or return None.
    When end_of_day=True the time is set to 23:59:59 so the whole day is included.
    """
    from datetime import date as _date, datetime as _datetime, timezone as _tz
    val = request.GET.get(name, "").strip()
    if not val:
        return None
    try:
        d = _date.fromisoformat(val)
        if end_of_day:
            return _datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=_tz.utc)
        return _datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=_tz.utc)
    except ValueError:
        return None


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
    Accepts ?date_from=YYYY-MM-DD and ?date_to=YYYY-MM-DD for date filtering.
    """
    _get_user_dataset(request, dataset_id)
    date_from = _parse_date_param(request, "date_from")
    date_to   = _parse_date_param(request, "date_to", end_of_day=True)
    return JsonResponse(
        get_status_distribution(dataset_id, owner=request.user, date_from=date_from, date_to=date_to),
        status=200,
    )


@login_required
@require_GET
def analytics_request_trends_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/request-trends/
    Accepts ?interval=hour|day, ?limit=N, ?date_from=YYYY-MM-DD, ?date_to=YYYY-MM-DD.
    """
    _get_user_dataset(request, dataset_id)
    interval  = request.GET.get("interval", "hour")
    limit     = _parse_int_param(request, "limit", DEFAULT_TRENDS_LIMIT)
    date_from = _parse_date_param(request, "date_from")
    date_to   = _parse_date_param(request, "date_to", end_of_day=True)
    return JsonResponse(
        get_request_trends(
            dataset_id, owner=request.user,
            interval=interval, limit=limit,
            date_from=date_from, date_to=date_to,
        ),
        status=200,
    )


@login_required
@require_GET
def analytics_top_endpoints_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/top-endpoints/
    Accepts ?limit=N, ?date_from=YYYY-MM-DD, ?date_to=YYYY-MM-DD.
    """
    _get_user_dataset(request, dataset_id)
    limit     = _parse_int_param(request, "limit", DEFAULT_TOP_ENDPOINTS_LIMIT)
    date_from = _parse_date_param(request, "date_from")
    date_to   = _parse_date_param(request, "date_to", end_of_day=True)
    return JsonResponse(
        get_top_endpoints(
            dataset_id, owner=request.user,
            limit=min(limit, MAX_TOP_ENDPOINTS_LIMIT),
            date_from=date_from, date_to=date_to,
        ),
        status=200,
    )


@login_required
@require_GET
def analytics_hourly_traffic_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/hourly-traffic/
    Accepts ?date_from=YYYY-MM-DD, ?date_to=YYYY-MM-DD.
    """
    _get_user_dataset(request, dataset_id)
    date_from = _parse_date_param(request, "date_from")
    date_to   = _parse_date_param(request, "date_to", end_of_day=True)
    return JsonResponse(
        get_hourly_traffic(dataset_id, owner=request.user, date_from=date_from, date_to=date_to),
        status=200,
    )


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

    # Advanced search parameters (Module 5)
    ip_address = request.GET.get("ip_address", "").strip()
    method = request.GET.get("method", "").strip()
    endpoint = request.GET.get("endpoint", "").strip()

    status_min = None
    status_max = None
    try:
        status_min = int(request.GET["status_min"])
    except (KeyError, ValueError):
        pass
    try:
        status_max = int(request.GET["status_max"])
    except (KeyError, ValueError):
        pass

    data = get_log_entries(
        dataset_id,
        request.user,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
        date_from=date_from,
        date_to=date_to,
        ip_address=ip_address,
        method=method,
        endpoint=endpoint,
        status_min=status_min,
        status_max=status_max,
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

    # POST — create rule (analyst/admin only)
    role_err = _require_analyst(request)
    if role_err:
        return role_err

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
    role_err = _require_analyst(request)
    if role_err:
        return role_err
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


@login_required
@require_GET
def auto_alerts_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/alerts/auto/

    Run the two built-in smart health checks (error rate + traffic spike)
    without requiring user-defined rules. Returns results immediately.
    """
    dataset = _get_user_dataset(request, dataset_id)
    error   = _dataset_ready_or_error(dataset)
    if error:
        return error

    data = auto_check_alerts(dataset_id, owner=request.user)
    return JsonResponse(data, status=200)


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


# ---------------------------------------------------------------------------
# Security insights view  (Module 1)
# ---------------------------------------------------------------------------


@login_required
@require_GET
def security_insights_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/security/

    Run security checks (brute force, path scanning, sensitive paths,
    high error concentration) and return findings + risk score.
    """
    dataset = _get_user_dataset(request, dataset_id)
    error = _dataset_ready_or_error(dataset)
    if error:
        return error
    from logs.services.security import get_security_insights
    data = get_security_insights(dataset_id, owner=request.user)
    return JsonResponse(data, status=200)


# ---------------------------------------------------------------------------
# Dataset comparison view  (Module 2)
# ---------------------------------------------------------------------------


@login_required
@require_GET
def comparison_view(request: HttpRequest) -> JsonResponse:
    """
    GET /logs/compare/?a=<id>&b=<id>

    Return side-by-side analytics for two datasets plus percentage-change
    deltas.  Both datasets must belong to the authenticated user.
    """
    a = request.GET.get("a", "").strip()
    b = request.GET.get("b", "").strip()

    if not a or not b:
        return JsonResponse(
            {"error": "Both 'a' and 'b' dataset IDs are required as query parameters."},
            status=400,
        )

    try:
        dataset_id_a = int(a)
        dataset_id_b = int(b)
    except ValueError:
        return JsonResponse({"error": "'a' and 'b' must be integer dataset IDs."}, status=400)

    from logs.services.comparison import compare_datasets
    try:
        data = compare_datasets(dataset_id_a, dataset_id_b, owner=request.user)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=404)

    return JsonResponse(data, status=200)


# ---------------------------------------------------------------------------
# Analytics filtered summary view  (Module 3)
# ---------------------------------------------------------------------------


@login_required
@require_GET
def analytics_filtered_summary_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    GET /logs/datasets/<id>/analytics/filtered-summary/

    Return aggregate metrics for the dataset optionally bounded by a date range.

    Query parameters
    ----------------
    date_from : ISO 8601 datetime string — inclusive lower bound
    date_to   : ISO 8601 datetime string — inclusive upper bound
    """
    from django.utils.dateparse import parse_datetime
    from logs.services.analytics import get_filtered_summary

    _get_user_dataset(request, dataset_id)

    date_from = None
    date_to = None

    raw_from = request.GET.get("date_from", "").strip()
    raw_to = request.GET.get("date_to", "").strip()

    if raw_from:
        date_from = parse_datetime(raw_from)
        if date_from is None:
            return JsonResponse(
                {"error": f"Cannot parse date_from='{raw_from}'. Use ISO 8601 format."},
                status=400,
            )

    if raw_to:
        date_to = parse_datetime(raw_to)
        if date_to is None:
            return JsonResponse(
                {"error": f"Cannot parse date_to='{raw_to}'. Use ISO 8601 format."},
                status=400,
            )

    data = get_filtered_summary(dataset_id, owner=request.user, date_from=date_from, date_to=date_to)
    return JsonResponse(data, status=200)


# ---------------------------------------------------------------------------
# Notification views  (Module 4)
# ---------------------------------------------------------------------------


@login_required
@require_GET
def notifications_list_view(request: HttpRequest) -> JsonResponse:
    """GET /logs/notifications/ — return recent notifications and unread count."""
    from logs.services.notifications import get_notifications, get_unread_count

    unread_only = request.GET.get("unread_only", "").lower() in ("1", "true", "yes")
    limit = min(_parse_int_param(request, "limit", 50), 200)

    notifications = get_notifications(request.user, unread_only=unread_only, limit=limit)
    unread_count = get_unread_count(request.user)

    return JsonResponse(
        {"notifications": notifications, "unread_count": unread_count},
        status=200,
    )


@login_required
@require_POST
def notifications_mark_read_view(request: HttpRequest, notification_id: int) -> JsonResponse:
    """POST /logs/notifications/<id>/read/ — mark a single notification as read."""
    from logs.services.notifications import mark_read

    updated = mark_read(notification_id, user=request.user)
    if not updated:
        return JsonResponse({"error": "Notification not found."}, status=404)
    return JsonResponse({"marked_read": True, "notification_id": notification_id}, status=200)


@login_required
@require_POST
def notifications_mark_all_read_view(request: HttpRequest) -> JsonResponse:
    """POST /logs/notifications/read-all/ — mark all notifications as read."""
    from logs.services.notifications import mark_all_read

    count = mark_all_read(user=request.user)
    return JsonResponse({"marked_read": count}, status=200)


@login_required
@require_GET
def notifications_count_view(request: HttpRequest) -> JsonResponse:
    """GET /logs/notifications/count/ — return the unread notification count."""
    from logs.services.notifications import get_unread_count

    return JsonResponse({"unread_count": get_unread_count(request.user)}, status=200)


# ---------------------------------------------------------------------------
# Log simulator view  (Module 6)
# ---------------------------------------------------------------------------


def _require_analyst(request):
    """Return a 403 JsonResponse if the user is not admin or analyst, else None."""
    from logs.services.rbac import get_or_create_profile
    try:
        role = get_or_create_profile(request.user).role
        if role not in ("admin", "analyst"):
            return JsonResponse({"error": "You need Analyst or Admin access to perform this action."}, status=403)
    except Exception:
        return JsonResponse({"error": "Role check failed."}, status=403)
    return None


@login_required
@require_POST
def simulate_logs_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """
    POST /logs/datasets/<id>/simulate/

    Generate synthetic log entries and append them to the dataset.

    Query parameters
    ----------------
    count : int — number of entries to generate (default 10, max 50)
    """
    err = _require_analyst(request)
    if err:
        return err
    _get_user_dataset(request, dataset_id)

    count = min(_parse_int_param(request, "count", 10), 50)

    from logs.services.simulator import generate_log_entries
    from logs.services.alerts import auto_check_alerts
    try:
        data = generate_log_entries(dataset_id, owner=request.user, count=count)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    # Run health check after ingestion so alerts fire automatically.
    try:
        check = auto_check_alerts(dataset_id, owner=request.user)
        data["alert_status"] = check.get("status", "ok")
    except Exception:
        data["alert_status"] = "ok"

    return JsonResponse(data, status=201)


@login_required
@require_POST
def simulate_error_spike_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """POST /logs/datasets/<id>/simulate/error-spike/ — inject high-error entries."""
    err = _require_analyst(request)
    if err:
        return err
    dataset = _get_user_dataset(request, dataset_id)
    error   = _dataset_ready_or_error(dataset)
    if error:
        return error

    from logs.services.simulator import simulate_error_spike
    from logs.services.alerts import auto_check_alerts
    try:
        data  = simulate_error_spike(dataset_id, owner=request.user)
        check = auto_check_alerts(dataset_id, owner=request.user)
        data["alert_status"] = check.get("status", "ok")
        data["alert_results"] = check.get("results", [])
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(data, status=201)


@login_required
@require_POST
def simulate_traffic_spike_view(request: HttpRequest, dataset_id: int) -> JsonResponse:
    """POST /logs/datasets/<id>/simulate/traffic-spike/ — inject burst entries."""
    err = _require_analyst(request)
    if err:
        return err
    dataset = _get_user_dataset(request, dataset_id)
    error   = _dataset_ready_or_error(dataset)
    if error:
        return error

    from logs.services.simulator import simulate_traffic_spike
    from logs.services.alerts import auto_check_alerts
    try:
        data  = simulate_traffic_spike(dataset_id, owner=request.user)
        check = auto_check_alerts(dataset_id, owner=request.user)
        data["alert_status"] = check.get("status", "ok")
        data["alert_results"] = check.get("results", [])
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(data, status=201)


@login_required
@require_GET
def simulate_download_view(request: HttpRequest, dataset_id: int) -> HttpResponse:
    """GET /logs/datasets/<id>/simulate/download/ — download simulated entries as CSV."""
    import csv
    _get_user_dataset(request, dataset_id)

    from logs.models import LogEntry
    entries = (
        LogEntry.objects
        .filter(
            dataset_id=dataset_id,
            dataset__owner=request.user,
            user_agent__startswith="LogSight-Simulator",
        )
        .order_by("-timestamp")[:5000]
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="simulated_logs_{dataset_id}.csv"'

    writer = csv.writer(response)
    writer.writerow(["timestamp", "method", "endpoint", "status_code", "ip_address", "response_size"])
    for e in entries:
        writer.writerow([
            e.timestamp.isoformat(),
            e.method,
            e.endpoint,
            e.status_code,
            e.ip_address,
            e.response_size or "",
        ])

    return response


@login_required
@require_GET
def all_alerts_history_view(request: HttpRequest) -> JsonResponse:
    """GET /logs/alerts/all-history/ — return triggered alerts across all datasets."""
    from logs.services.alerts import get_all_alert_history
    limit   = min(_parse_int_param(request, "limit", 100), 500)
    history = get_all_alert_history(owner=request.user, limit=limit)
    return JsonResponse({"alerts": history, "count": len(history)}, status=200)


# ---------------------------------------------------------------------------
# RBAC admin views  (Module 7)
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def invites_view(request: HttpRequest) -> JsonResponse:
    """
    GET  /logs/invites/  — list all invites (admin only).
    POST /logs/invites/  — create a new invite (admin only).
    """
    from logs.services.rbac import get_or_create_profile
    from logs.services.invites import create_invite, list_invites

    profile = get_or_create_profile(request.user)
    if not profile.is_admin:
        return JsonResponse({"error": "Admin access required."}, status=403)

    if request.method == "GET":
        return JsonResponse({"invites": list_invites(request.user)}, status=200)

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    email = (body.get("email") or "").strip().lower()
    role  = body.get("role", "analyst")

    send_email = bool(body.get("send_email", True))

    if send_email and not email:
        return JsonResponse({"error": "Email is required when sending an email invite."}, status=400)
    if role not in ("analyst", "viewer"):
        return JsonResponse({"error": "Role must be analyst or viewer."}, status=400)

    try:
        invite = create_invite(email=email or None, role=role, invited_by=request.user, send_email=send_email)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({"invite": invite}, status=201)


@login_required
@require_http_methods(["DELETE", "POST"])
def revoke_invite_view(request: HttpRequest, invite_id: int) -> JsonResponse:
    """
    DELETE /logs/invites/<id>/  — revoke an unused invite (admin only).
    """
    from logs.services.rbac import get_or_create_profile
    from logs.services.invites import revoke_invite

    profile = get_or_create_profile(request.user)
    if not profile.is_admin:
        return JsonResponse({"error": "Admin access required."}, status=403)

    revoked = revoke_invite(invite_id, admin_user=request.user)
    if not revoked:
        return JsonResponse({"error": "Invite not found or already used."}, status=404)
    return JsonResponse({"revoked": True, "invite_id": invite_id}, status=200)


@login_required
@require_GET
def user_management_view(request: HttpRequest) -> JsonResponse:
    """
    GET /logs/admin/users/

    Return all users with their profiles.  Admin only.
    """
    from logs.services.rbac import get_all_users_with_profiles, get_or_create_profile

    profile = get_or_create_profile(request.user)
    if not profile.is_admin:
        return JsonResponse({"error": "Admin access required."}, status=403)

    users = get_all_users_with_profiles()
    return JsonResponse({"users": users, "count": len(users)}, status=200)


@login_required
@require_POST
def assign_role_view(request: HttpRequest, user_id: int) -> JsonResponse:
    """
    POST /logs/admin/users/<user_id>/role/

    Assign a role to a user.  Body: {"role": "analyst"}  Admin only.
    """
    from logs.services.rbac import assign_role

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    role = body.get("role", "").strip()
    if not role:
        return JsonResponse({"error": "'role' field is required."}, status=400)

    try:
        profile = assign_role(user_id, role, requesting_user=request.user)
    except PermissionError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=404)

    return JsonResponse({"profile": profile}, status=200)
