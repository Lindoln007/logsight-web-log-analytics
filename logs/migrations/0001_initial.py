from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        # ------------------------------------------------------------------
        # Dataset
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="Dataset",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                (
                    "file",
                    models.FileField(blank=True, null=True, upload_to="uploads/"),
                ),
                (
                    "uploaded_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                (
                    "file_size",
                    models.BigIntegerField(
                        blank=True,
                        help_text="File size in bytes.",
                        null=True,
                    ),
                ),
                (
                    "total_entries",
                    models.IntegerField(
                        default=0,
                        help_text="Total number of parsed log entries in this dataset.",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("description", models.TextField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-uploaded_at"],
            },
        ),
        migrations.AddIndex(
            model_name="dataset",
            index=models.Index(
                fields=["status", "uploaded_at"], name="dataset_status_uploaded_idx"
            ),
        ),
        # ------------------------------------------------------------------
        # LogEntry
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="LogEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "dataset",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entries",
                        to="logs.dataset",
                    ),
                ),
                (
                    "timestamp",
                    models.DateTimeField(
                        help_text="Timestamp from the log record."
                    ),
                ),
                (
                    "method",
                    models.CharField(
                        choices=[
                            ("GET", "GET"),
                            ("POST", "POST"),
                            ("PUT", "PUT"),
                            ("DELETE", "DELETE"),
                            ("PATCH", "PATCH"),
                            ("HEAD", "HEAD"),
                            ("OPTIONS", "OPTIONS"),
                            ("CONNECT", "CONNECT"),
                            ("TRACE", "TRACE"),
                        ],
                        max_length=10,
                    ),
                ),
                ("endpoint", models.CharField(max_length=2048)),
                (
                    "status_code",
                    models.SmallIntegerField(
                        help_text="HTTP response status code (e.g. 200, 404, 500)."
                    ),
                ),
                (
                    "ip_address",
                    models.GenericIPAddressField(
                        help_text="Client IP address (IPv4 or IPv6).",
                        protocol="both",
                        unpack_ipv4=True,
                    ),
                ),
                ("user_agent", models.TextField(blank=True, null=True)),
                (
                    "response_size",
                    models.IntegerField(
                        blank=True,
                        help_text="Response body size in bytes.",
                        null=True,
                    ),
                ),
                (
                    "error_flag",
                    models.BooleanField(
                        default=False,
                        help_text="True when status_code >= 400.",
                    ),
                ),
            ],
            options={
                "ordering": ["-timestamp"],
            },
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["timestamp"], name="logentry_timestamp_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["status_code"], name="logentry_status_code_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["ip_address"], name="logentry_ip_address_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["endpoint"], name="logentry_endpoint_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["error_flag"], name="logentry_error_flag_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["dataset", "timestamp"],
                name="logentry_dataset_timestamp_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["dataset", "error_flag"],
                name="logentry_dataset_error_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="logentry",
            index=models.Index(
                fields=["dataset", "status_code"],
                name="logentry_dataset_status_idx",
            ),
        ),
        # ------------------------------------------------------------------
        # AnalyticsSummary
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="AnalyticsSummary",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "dataset",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="summary",
                        to="logs.dataset",
                    ),
                ),
                ("total_requests", models.IntegerField(default=0)),
                ("total_errors", models.IntegerField(default=0)),
                ("unique_ips", models.IntegerField(default=0)),
                (
                    "error_rate",
                    models.DecimalField(
                        decimal_places=3,
                        default=0,
                        help_text="Percentage of requests with status_code >= 400.",
                        max_digits=6,
                    ),
                ),
                (
                    "avg_response_size",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Mean response body size in bytes.",
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "top_endpoints",
                    models.JSONField(
                        default=list,
                        help_text="Top N endpoints by request count, ordered descending.",
                    ),
                ),
                (
                    "status_distribution",
                    models.JSONField(
                        default=dict,
                        help_text="Request counts keyed by HTTP status code.",
                    ),
                ),
                (
                    "hourly_traffic",
                    models.JSONField(
                        default=dict,
                        help_text="Request counts keyed by hour of day (0-23).",
                    ),
                ),
                (
                    "method_distribution",
                    models.JSONField(
                        default=dict,
                        help_text="Request counts keyed by HTTP method.",
                    ),
                ),
                ("date_range_start", models.DateTimeField(blank=True, null=True)),
                ("date_range_end", models.DateTimeField(blank=True, null=True)),
                ("computed_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Analytics Summary",
                "verbose_name_plural": "Analytics Summaries",
            },
        ),
    ]
