from django.conf import settings
from django.db import models
from django.utils import timezone


class Dataset(models.Model):
    """
    Stores metadata for each uploaded log file.
    One Dataset maps to one parsed log file upload.
    """

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="datasets",
        db_index=True,
    )
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="uploads/", null=True, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now, db_index=True)
    file_size = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="File size in bytes.",
    )
    total_entries = models.IntegerField(
        default=0,
        help_text="Total number of parsed log entries in this dataset.",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    description = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["owner", "uploaded_at"]),
            models.Index(fields=["status", "uploaded_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.owner})"

    @property
    def file_size_display(self):
        """Return a human-readable file size string."""
        if self.file_size is None:
            return "Unknown"
        if self.file_size < 1_024:
            return f"{self.file_size} B"
        if self.file_size < 1_024**2:
            return f"{self.file_size / 1_024:.1f} KB"
        if self.file_size < 1_024**3:
            return f"{self.file_size / 1_024 ** 2:.1f} MB"
        return f"{self.file_size / 1_024 ** 3:.2f} GB"

    @property
    def error_count(self):
        return self.entries.filter(error_flag=True).count()

    @property
    def error_rate(self):
        if self.total_entries == 0:
            return 0.0
        return round((self.error_count / self.total_entries) * 100, 2)


class LogEntry(models.Model):
    """
    Stores a single parsed log record.
    Linked to a Dataset via foreign key; deleting a Dataset cascades to all its entries.
    """

    HTTP_METHOD_CHOICES = [
        ("GET", "GET"),
        ("POST", "POST"),
        ("PUT", "PUT"),
        ("DELETE", "DELETE"),
        ("PATCH", "PATCH"),
        ("HEAD", "HEAD"),
        ("OPTIONS", "OPTIONS"),
        ("CONNECT", "CONNECT"),
        ("TRACE", "TRACE"),
    ]

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="entries",
        db_index=True,
    )
    timestamp = models.DateTimeField(help_text="Timestamp from the log record.")
    method = models.CharField(max_length=10, choices=HTTP_METHOD_CHOICES)
    # CharField with a generous cap is more index-friendly than unbounded TextField.
    endpoint = models.CharField(max_length=2_048)
    protocol = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="HTTP protocol version from the request line (e.g. HTTP/1.1).",
    )
    status_code = models.SmallIntegerField(
        help_text="HTTP response status code (e.g. 200, 404, 500)."
    )
    ip_address = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True,
        help_text="Client IP address (IPv4 or IPv6).",
    )
    referrer = models.TextField(
        null=True,
        blank=True,
        help_text="HTTP referrer, if present in the source log line.",
    )
    user_agent = models.TextField(null=True, blank=True)
    response_size = models.IntegerField(
        null=True,
        blank=True,
        help_text="Response body size in bytes.",
    )
    error_flag = models.BooleanField(
        default=False,
        help_text="True when status_code >= 400.",
    )

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            # Single-column indexes for common filter/sort operations.
            models.Index(fields=["timestamp"], name="logentry_timestamp_idx"),
            models.Index(fields=["status_code"], name="logentry_status_code_idx"),
            models.Index(fields=["ip_address"], name="logentry_ip_address_idx"),
            models.Index(fields=["endpoint"], name="logentry_endpoint_idx"),
            models.Index(fields=["error_flag"], name="logentry_error_flag_idx"),
            # Compound indexes for the most frequent analytics queries:
            # "all entries for a dataset ordered by time"
            models.Index(
                fields=["dataset", "timestamp"],
                name="logentry_dataset_timestamp_idx",
            ),
            # "error entries per dataset"
            models.Index(
                fields=["dataset", "error_flag"],
                name="logentry_dataset_error_idx",
            ),
            # "status code distribution per dataset"
            models.Index(
                fields=["dataset", "status_code"],
                name="logentry_dataset_status_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        # Automatically derive error_flag from the status code.
        self.error_flag = self.status_code >= 400
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ip_address} {self.method} {self.endpoint} → {self.status_code}"


class AnalyticsSummary(models.Model):
    """
    Stores precomputed aggregate statistics for a Dataset.
    Recomputed whenever the dataset finishes processing or on demand.
    Avoids re-scanning millions of LogEntry rows for every dashboard render.
    """

    dataset = models.OneToOneField(
        Dataset,
        on_delete=models.CASCADE,
        related_name="summary",
    )

    # Aggregate counters
    total_requests = models.IntegerField(default=0)
    total_errors = models.IntegerField(default=0)
    unique_ips = models.IntegerField(default=0)

    # Derived rates / averages
    error_rate = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=0,
        help_text="Percentage of requests with status_code >= 400.",
    )
    avg_response_size = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Mean response body size in bytes.",
    )

    # Precomputed distributions stored as JSON blobs.
    # Example shape for top_endpoints:
    #   [{"endpoint": "/api/v1/login", "count": 4821}, ...]
    top_endpoints = models.JSONField(
        default=list,
        help_text="Top N endpoints by request count, ordered descending.",
    )

    # Example shape for status_distribution:
    #   {"200": 91234, "301": 512, "404": 3021, "500": 87}
    status_distribution = models.JSONField(
        default=dict,
        help_text="Request counts keyed by HTTP status code.",
    )

    # Example shape for hourly_traffic:
    #   {"0": 320, "1": 210, ..., "23": 1050}  (keys are hour-of-day strings)
    hourly_traffic = models.JSONField(
        default=dict,
        help_text="Request counts keyed by hour of day (0–23).",
    )

    # Example shape for method_distribution:
    #   {"GET": 85000, "POST": 12000, "DELETE": 300}
    method_distribution = models.JSONField(
        default=dict,
        help_text="Request counts keyed by HTTP method.",
    )

    # Temporal range of the underlying log entries.
    date_range_start = models.DateTimeField(null=True, blank=True)
    date_range_end = models.DateTimeField(null=True, blank=True)

    # When this summary was last computed.
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Analytics Summary"
        verbose_name_plural = "Analytics Summaries"

    def __str__(self):
        return f"Summary for '{self.dataset.name}' (computed {self.computed_at:%Y-%m-%d %H:%M})"
