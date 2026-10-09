from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("logs", "0010_logentry_host_and_index_cleanup"),
    ]

    operations = [
        migrations.DeleteModel(name="InviteToken"),
        migrations.DeleteModel(name="UserProfile"),
    ]
