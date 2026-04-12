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
    path("datasets/<int:dataset_id>/status/", views.dataset_status_view, name="dataset-status"),
    path("datasets/<int:dataset_id>/delete/", views.delete_dataset_view, name="dataset-delete"),

    # ------------------------------------------------------------------
    # Log entries — paginated viewer data
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/entries/",
        views.log_entries_view,
        name="dataset-entries",
    ),

    # ------------------------------------------------------------------
    # Analytics  (all scoped under /logs/datasets/<id>/analytics/)
    # ------------------------------------------------------------------

    path(
        "datasets/<int:dataset_id>/analytics/",
        views.analytics_overview_view,
        name="analytics-overview",
    ),
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
    path(
        "datasets/<int:dataset_id>/analytics/refresh/",
        views.analytics_refresh_view,
        name="analytics-refresh",
    ),

    # ------------------------------------------------------------------
    # Traffic forecasting
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/forecast/",
        views.forecast_view,
        name="forecast",
    ),

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/anomalies/",
        views.anomaly_detection_view,
        name="anomalies",
    ),

    # ------------------------------------------------------------------
    # Alerts  (rules + history, scoped to dataset)
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/alerts/",
        views.alerts_view,
        name="alerts",
    ),
    path(
        "datasets/<int:dataset_id>/alerts/check/",
        views.alerts_check_view,
        name="alerts-check",
    ),
    path(
        "datasets/<int:dataset_id>/alerts/history/",
        views.alert_history_view,
        name="alerts-history",
    ),
    path(
        "datasets/<int:dataset_id>/alerts/<int:rule_id>/",
        views.alert_rule_detail_view,
        name="alert-rule-detail",
    ),

    # ------------------------------------------------------------------
    # Report generation  (download CSV or JSON)
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/report/",
        views.report_view,
        name="report",
    ),

    # ------------------------------------------------------------------
    # Dashboard preferences  (per-user, not per-dataset)
    # ------------------------------------------------------------------
    path(
        "preferences/",
        views.dashboard_preferences_view,
        name="preferences",
    ),
]
