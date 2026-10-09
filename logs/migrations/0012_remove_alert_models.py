from django.db import migrations


def remove_alert_notifications(apps, schema_editor):
    Notification = apps.get_model("logs", "Notification")
    Notification.objects.using(schema_editor.connection.alias).filter(
        notification_type="alert"
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("logs", "0011_remove_user_roles_and_invites"),
    ]

    operations = [
        migrations.RunPython(remove_alert_notifications, migrations.RunPython.noop),
        migrations.DeleteModel(name="Alert"),
        migrations.DeleteModel(name="AlertRule"),
    ]
