"""
Pre-compute results for datasets that are already in the database.

    python manage.py warm_datasets            # every completed dataset
    python manage.py warm_datasets --dataset 4 --dataset 7

New uploads are prepared automatically when parsing finishes.  Datasets
uploaded earlier (or whose cache was cleared) would otherwise do this work on
their first visit; running this command once moves that wait out of the way.
It builds each dataset's hourly roll-up, the dashboard figures, and the default
anomaly, forecast and security results, and stores them in the result cache.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from logs.models import Dataset
from logs.services import rollup
from logs.services.analytics import (
    get_error_breakdown,
    get_hourly_traffic,
    get_method_distribution,
    get_status_distribution,
    get_summary_metrics,
    get_top_endpoints,
)
from logs.services.dataset_management import _prewarm
from logs.services.ml_runtime import _import_ml_stack


class Command(BaseCommand):
    help = "Pre-compute dashboard and analysis results for existing datasets."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", type=int, action="append", default=[],
                            help="Only this dataset id (may be repeated).")

    def handle(self, *args, **options):
        datasets = Dataset.objects.filter(status=Dataset.STATUS_COMPLETED).select_related("owner").order_by("pk")
        if options["dataset"]:
            datasets = datasets.filter(pk__in=options["dataset"])
        datasets = list(datasets)
        if not datasets:
            self.stdout.write("No completed datasets to prepare.")
            return

        started = time.perf_counter()
        _import_ml_stack()
        self.stdout.write(f"Loaded the ML libraries in {time.perf_counter() - started:.1f}s")

        for dataset in datasets:
            started = time.perf_counter()
            owner = dataset.owner
            rollup.ensure(dataset.pk)
            get_summary_metrics(dataset.pk, owner)
            get_status_distribution(dataset.pk, owner)
            get_top_endpoints(dataset.pk, owner)
            get_hourly_traffic(dataset.pk, owner)
            get_method_distribution(dataset.pk, owner)
            get_error_breakdown(dataset.pk, owner)
            _prewarm(dataset.pk)
            self.stdout.write(
                f"  {dataset.name} ({dataset.total_entries:,} entries): "
                f"ready in {time.perf_counter() - started:.1f}s"
            )

        self.stdout.write(self.style.SUCCESS(f"Prepared {len(datasets)} dataset(s)."))
