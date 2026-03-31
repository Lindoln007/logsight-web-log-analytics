"""loganalytics URL configuration."""

from pathlib import Path

from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import include, path
from django.views.static import serve

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def serve_frontend_index(request):
    return render(request, "logs/app_shell.html")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
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
