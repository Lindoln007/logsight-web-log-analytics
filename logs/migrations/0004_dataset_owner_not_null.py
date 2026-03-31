"""
Make Dataset.owner non-nullable.

Data migration step: delete any orphaned datasets (owner IS NULL) before
altering the column, so the NOT NULL constraint is never violated.
Orphaned datasets can only exist if records were inserted before migration
0003 added the owner field, or via the Django admin / shell without setting
an owner.  Deleting them is the safest option — they are inaccessible
through the normal application anyway.
"""
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


def delete_ownerless_datasets(apps, schema_editor):
    Dataset = apps.get_model("logs", "Dataset")
    deleted, _ = Dataset.objects.filter(owner__isnull=True).delete()
    if deleted:
        print(f"\n  Deleted {deleted} ownerless dataset(s) before applying NOT NULL constraint.")


class Migration(migrations.Migration):

    dependencies = [
        ("logs", "0003_rename_dataset_status_uploaded_idx_logs_datase_status_14a506_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Remove orphaned rows so the ALTER succeeds.
        migrations.RunPython(delete_ownerless_datasets, migrations.RunPython.noop),
        # 2. Drop the null constraint.
        migrations.AlterField(
            model_name="dataset",
            name="owner",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="datasets",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
