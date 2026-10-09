"""loganalytics URL configuration."""

from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import include, path
from django.views.static import serve
from logs import views as log_views

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def serve_frontend_index(request):
    # Load the ML libraries in the background while the user is on the
    # dashboard, so the first forecast / anomaly run does not wait for them.
    from logs.services.ml_runtime import warm_up
    warm_up()

    return render(request, "logs/app_shell.html")


urlpatterns = [
    path("accounts/", include("accounts.urls")),
    path("coming-soon/", log_views.coming_soon_view, name="coming-soon"),
    path("logs/", include("logs.urls")),
    # Static SPA assets (paths match index.html: css/..., js/...)
    path(
        "assets/<path:path>",
        serve,
        {"document_root": str(FRONTEND_DIR / "assets")},
    ),
    path(
        "css/<path:path>",
        serve,
        {"document_root": str(FRONTEND_DIR / "css")},
    ),
    path(
        "js/<path:path>",
        serve,
        {"document_root": str(FRONTEND_DIR / "js")},
    ),
    path("", login_required(serve_frontend_index), name="app-shell"),
]
