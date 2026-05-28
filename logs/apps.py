from django.apps import AppConfig


class LogsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'logs'

    def ready(self):
        # Register the post_save signal that auto-creates a UserProfile
        # for every new Django User.
        import logs.signals  # noqa: F401
