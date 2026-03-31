from django.urls import path

from . import views

app_name = "logs"

urlpatterns = [
    # ------------------------------------------------------------------
    # Dataset management
    # ------------------------------------------------------------------
    path("upload/", views.upload_log_view, name="upload"),
    path("datasets/", views.dataset_list_view, name="dataset-list"),
    path("datasets/<int:dataset_id>/", views.dataset_detail_view, name="dataset-detail"),
    path("datasets/<int:dataset_id>/delete/", views.delete_dataset_view, name="dataset-delete"),

    # ------------------------------------------------------------------
    # Log entries — paginated viewer data
    # ------------------------------------------------------------------
    #
    # GET /logs/datasets/<id>/entries/
    #   ?page=1 &page_size=25 &status=all|2xx|4xx|5xx|errors &search=<str>
    #
    path(
        "datasets/<int:dataset_id>/entries/",
        views.log_entries_view,
        name="dataset-entries",
    ),

    # ------------------------------------------------------------------
    # Analytics  (all scoped under /logs/datasets/<id>/analytics/)
    # ------------------------------------------------------------------

    # Full overview — all analytics sections in one response.
    # Useful for the initial dashboard render.
    path(
        "datasets/<int:dataset_id>/analytics/",
        views.analytics_overview_view,
        name="analytics-overview",
    ),

    # Individual sections — for targeted chart refreshes.
    path(
        "datasets/<int:dataset_id>/analytics/summary/",
        views.analytics_summary_view,
        name="analytics-summary",
    ),
    path(
        "datasets/<int:dataset_id>/analytics/status-distribution/",
        views.analytics_status_distribution_view,
        name="analytics-status-distribution",
    ),
    path(
        "datasets/<int:dataset_id>/analytics/request-trends/",
        views.analytics_request_trends_view,
        name="analytics-request-trends",
    ),
    path(
        "datasets/<int:dataset_id>/analytics/top-endpoints/",
        views.analytics_top_endpoints_view,
        name="analytics-top-endpoints",
    ),
    path(
        "datasets/<int:dataset_id>/analytics/hourly-traffic/",
        views.analytics_hourly_traffic_view,
        name="analytics-hourly-traffic",
    ),
    path(
        "datasets/<int:dataset_id>/analytics/method-distribution/",
        views.analytics_method_distribution_view,
        name="analytics-method-distribution",
    ),
    path(
        "datasets/<int:dataset_id>/analytics/errors/",
        views.analytics_error_breakdown_view,
        name="analytics-errors",
    ),

    # Force-recompute — invalidates cache and re-runs all aggregations.
    path(
        "datasets/<int:dataset_id>/analytics/refresh/",
        views.analytics_refresh_view,
        name="analytics-refresh",
    ),
]
