from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logs", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="logentry",
            name="protocol",
            field=models.CharField(
                blank=True,
                help_text="HTTP protocol version from the request line (e.g. HTTP/1.1).",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="logentry",
            name="referrer",
            field=models.TextField(
                blank=True,
                help_text="HTTP referrer, if present in the source log line.",
                null=True,
            ),
        ),
    ]
