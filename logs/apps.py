import os
import sys

from django.apps import AppConfig


def _is_serving_requests() -> bool:
    """
    True in the process that will answer web requests: the development
    server's worker process, or a gunicorn worker.  False for migrate, test,
    shell and other management commands, which should not pay for the ML load.
    """
    argv = sys.argv
    if "gunicorn" in os.path.basename(argv[0] if argv else ""):
        return True
    if len(argv) > 1 and argv[1] == "runserver":
        # With auto-reload, Django runs a watcher process plus a worker
        # (RUN_MAIN=true); only the worker serves requests.
        return os.environ.get("RUN_MAIN") == "true" or "--noreload" in argv
    return False


class LogsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'logs'

    def ready(self):
        # Register the post_save signal that auto-creates a UserProfile
        # for every new Django User.
        import logs.signals  # noqa: F401

        # Start loading scikit-learn and the security classifier as soon as the
        # server starts, so the first Forecast / Anomalies / Security request
        # does not wait for them.
        if _is_serving_requests():
            from logs.services.ml_runtime import warm_up
            warm_up()
