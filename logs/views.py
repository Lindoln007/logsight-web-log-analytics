from __future__ import annotations

import logging
from dataclasses import asdict

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods

from logs.forms import DEFAULT_MAX_UPLOAD_SIZE, LogUploadForm
from logs.models import Dataset
from logs.services import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    compute_and_persist_analytics,
    create_dataset_from_upload,
    delete_dataset_and_file,
    get_analytics_overview,
    get_error_breakdown,
    get_hourly_traffic,
    get_log_entries,
    get_method_distribution,
    get_request_trends,
    get_status_distribution,
    get_summary_metrics,
    get_top_endpoints,
    list_datasets,
    serialize_dataset,
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
        "stats": asdict(result.stats) if result.stats else None,
    }

    if result.error:
        payload["error"] = result.error
        return JsonResponse(payload, status=422)

    return JsonResponse(payload, status=201)


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
