from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("logs", "0005_add_processing_error"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AlertRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="Alert Rule", max_length=255)),
                (
                    "error_rate_threshold",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Trigger when the dataset's overall error rate exceeds this % value.",
                        max_digits=5,
                        null=True,
                    ),
                ),
                (
                    "request_spike_multiplier",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Trigger when the most-recent hourly count exceeds mean * this multiplier.",
                        max_digits=5,
                        null=True,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "dataset",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alert_rules",
                        to="logs.dataset",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alert_rules",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="DashboardPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "hidden_widgets",
                    models.JSONField(
                        default=list,
                        help_text="List of widget IDs that should be hidden on the dashboard.",
                    ),
                ),
                (
                    "preferences",
                    models.JSONField(
                        default=dict,
                        help_text="Arbitrary preference key-value pairs (filters, layout, etc.).",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dashboard_preference",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Alert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("triggered_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("alert_type", models.CharField(max_length=50)),
                (
                    "value",
                    models.DecimalField(
                        decimal_places=3,
                        help_text="The measured value that triggered this alert.",
                        max_digits=14,
                    ),
                ),
                (
                    "threshold",
                    models.DecimalField(
                        decimal_places=3,
                        help_text="The threshold that was exceeded.",
                        max_digits=14,
                    ),
                ),
                ("severity", models.CharField(default="medium", max_length=20)),
                ("message", models.TextField()),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alerts",
                        to="logs.alertrule",
                    ),
                ),
            ],
            options={
                "ordering": ["-triggered_at"],
            },
        ),
    ]
