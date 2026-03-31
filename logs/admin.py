from django.contrib import admin
from django.utils.html import format_html

from .models import AnalyticsSummary, Dataset, LogEntry


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "uploaded_at",
        "status",
        "total_entries",
        "file_size_display",
        "description_short",
    )
    list_filter = ("status", "uploaded_at")
    search_fields = ("name", "description")
    readonly_fields = ("uploaded_at", "total_entries", "file_size")
    ordering = ("-uploaded_at",)

    @admin.display(description="File Size")
    def file_size_display(self, obj):
        return obj.file_size_display

    @admin.display(description="Description")
    def description_short(self, obj):
        if obj.description:
            return obj.description[:80] + ("…" if len(obj.description) > 80 else "")
        return "—"


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "method",
        "protocol",
        "endpoint_display",
        "status_code_display",
        "ip_address",
        "response_size",
        "error_flag",
        "dataset",
    )
    list_filter = ("method", "error_flag", "dataset", "status_code")
    search_fields = ("ip_address", "endpoint", "referrer", "user_agent")
    list_select_related = ("dataset",)
    raw_id_fields = ("dataset",)
    ordering = ("-timestamp",)
    date_hierarchy = "timestamp"

    # Disable the total count query — extremely expensive on millions of rows.
    show_full_result_count = False
    list_per_page = 100

    @admin.display(description="Endpoint")
    def endpoint_display(self, obj):
        endpoint = obj.endpoint
        if len(endpoint) > 60:
            return endpoint[:60] + "…"
        return endpoint

    @admin.display(description="Status")
    def status_code_display(self, obj):
        code = obj.status_code
        if code < 300:
            colour = "#16a34a"  # green
        elif code < 400:
            colour = "#d97706"  # amber
        elif code < 500:
            colour = "#dc2626"  # red
        else:
            colour = "#7c3aed"  # purple
        return format_html(
            '<span style="color:{};font-weight:600;">{}</span>', colour, code
        )


@admin.register(AnalyticsSummary)
class AnalyticsSummaryAdmin(admin.ModelAdmin):
    list_display = (
        "dataset",
        "total_requests",
        "total_errors",
        "error_rate_display",
        "unique_ips",
        "avg_response_size",
        "date_range_start",
        "date_range_end",
        "computed_at",
    )
    list_select_related = ("dataset",)
    raw_id_fields = ("dataset",)
    readonly_fields = ("computed_at",)
    ordering = ("-computed_at",)

    @admin.display(description="Error Rate")
    def error_rate_display(self, obj):
        rate = float(obj.error_rate)
        colour = "#16a34a" if rate < 5 else "#dc2626"
        return format_html(
            '<span style="color:{};font-weight:600;">{:.2f}%</span>', colour, rate
        )
