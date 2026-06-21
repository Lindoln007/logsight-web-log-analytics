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
        views.next_semester_redirect,
        {"module_slug": "forecasting"},
        name="forecast",
    ),

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/anomalies/",
        views.next_semester_redirect,
        {"module_slug": "anomalies"},
        name="anomalies",
    ),

    # ------------------------------------------------------------------
    # Alerts  (rules + history, scoped to dataset)
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/alerts/",
        views.next_semester_redirect,
        {"module_slug": "alerts"},
        name="alerts",
    ),
    path(
        "datasets/<int:dataset_id>/alerts/check/",
        views.next_semester_redirect,
        {"module_slug": "alerts"},
        name="alerts-check",
    ),
    path(
        "datasets/<int:dataset_id>/alerts/history/",
        views.next_semester_redirect,
        {"module_slug": "alerts"},
        name="alerts-history",
    ),
    path(
        "datasets/<int:dataset_id>/alerts/auto/",
        views.next_semester_redirect,
        {"module_slug": "alerts"},
        name="alerts-auto",
    ),
    path(
        "datasets/<int:dataset_id>/alerts/<int:rule_id>/",
        views.next_semester_redirect,
        {"module_slug": "alerts"},
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

    # ------------------------------------------------------------------
    # Security insights  (Module 1)
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/security/",
        views.next_semester_redirect,
        {"module_slug": "security-insights"},
        name="security-insights",
    ),

    # ------------------------------------------------------------------
    # Dataset comparison  (Module 2)
    # ------------------------------------------------------------------
    path(
        "compare/",
        views.comparison_view,
        name="compare",
    ),

    # ------------------------------------------------------------------
    # Analytics filtered summary  (Module 3)
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/analytics/filtered-summary/",
        views.analytics_filtered_summary_view,
        name="analytics-filtered-summary",
    ),

    # ------------------------------------------------------------------
    # Notifications  (Module 4)
    # ------------------------------------------------------------------
    path(
        "notifications/",
        views.notifications_list_view,
        name="notifications-list",
    ),
    path(
        "notifications/count/",
        views.notifications_count_view,
        name="notifications-count",
    ),
    path(
        "notifications/read-all/",
        views.notifications_mark_all_read_view,
        name="notifications-read-all",
    ),
    path(
        "notifications/<int:notification_id>/read/",
        views.notifications_mark_read_view,
        name="notifications-mark-read",
    ),
    path(
        "notifications/<int:notification_id>/delete/",
        views.notifications_delete_view,
        name="notifications-delete",
    ),
    path(
        "notifications/delete-all/",
        views.notifications_delete_all_view,
        name="notifications-delete-all",
    ),

    # ------------------------------------------------------------------
    # Log simulator  (Module 6)
    # ------------------------------------------------------------------
    path(
        "datasets/<int:dataset_id>/simulate/",
        views.next_semester_redirect,
        {"module_slug": "live-ingestion"},
        name="simulate-logs",
    ),
    path(
        "datasets/<int:dataset_id>/simulate/error-spike/",
        views.next_semester_redirect,
        {"module_slug": "live-ingestion"},
        name="simulate-error-spike",
    ),
    path(
        "datasets/<int:dataset_id>/simulate/traffic-spike/",
        views.next_semester_redirect,
        {"module_slug": "live-ingestion"},
        name="simulate-traffic-spike",
    ),
    path(
        "datasets/<int:dataset_id>/simulate/download/",
        views.next_semester_redirect,
        {"module_slug": "live-ingestion"},
        name="simulate-download",
    ),
    path(
        "alerts/all-history/",
        views.next_semester_redirect,
        {"module_slug": "alerts"},
        name="alerts-all-history",
    ),

    # ------------------------------------------------------------------
    # RBAC admin  (Module 7)
    # ------------------------------------------------------------------
    path(
        "admin/users/",
        views.next_semester_redirect,
        {"module_slug": "user-management"},
        name="admin-users",
    ),
    path(
        "admin/users/<int:user_id>/role/",
        views.next_semester_redirect,
        {"module_slug": "user-management"},
        name="admin-assign-role",
    ),

    # ------------------------------------------------------------------
    # Invite tokens
    # ------------------------------------------------------------------
    path(
        "invites/",
        views.next_semester_redirect,
        {"module_slug": "user-management"},
        name="invites",
    ),
    path(
        "invites/<int:invite_id>/revoke/",
        views.next_semester_redirect,
        {"module_slug": "user-management"},
        name="invite-revoke",
    ),
]
