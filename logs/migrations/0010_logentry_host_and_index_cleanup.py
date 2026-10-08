from django.db import migrations, models


def widen_ip_address(apps, schema_editor):
    """
    Convert LogEntry.ip_address from an IP-only column to plain text.

    PostgreSQL needs an explicit conversion: casting inet straight to varchar
    appends the netmask ("1.2.3.4/32"), so host() is used to keep the bare
    address.  SQLite's char(39) column already accepts any text, so nothing
    needs to change there.
    """
    vendor = schema_editor.connection.vendor
    if vendor == "sqlite":
        return
    LogEntry = apps.get_model("logs", "LogEntry")
    table = schema_editor.quote_name(LogEntry._meta.db_table)
    column = schema_editor.quote_name("ip_address")
    if vendor == "postgresql":
        schema_editor.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE varchar(255) USING host({column})"
        )
    else:
        old_field = models.GenericIPAddressField(protocol="both", unpack_ipv4=True)
        old_field.set_attributes_from_name("ip_address")
        new_field = models.CharField(max_length=255)
        new_field.set_attributes_from_name("ip_address")
        schema_editor.alter_field(LogEntry, old_field, new_field)


class Migration(migrations.Migration):

    dependencies = [
        ("logs", "0009_invitetoken_email_optional"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="logentry", name="logentry_status_code_idx"),
        migrations.RemoveIndex(model_name="logentry", name="logentry_ip_address_idx"),
        migrations.RemoveIndex(model_name="logentry", name="logentry_endpoint_idx"),
        migrations.RemoveIndex(model_name="logentry", name="logentry_error_flag_idx"),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(widen_ip_address, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="logentry",
                    name="ip_address",
                    field=models.CharField(
                        help_text="Client address from the log line (IPv4, IPv6, or hostname).",
                        max_length=255,
                    ),
                ),
            ],
        ),
    ]
