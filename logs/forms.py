from __future__ import annotations

from pathlib import PurePath

from django import forms
from django.conf import settings


DEFAULT_MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
ALLOWED_LOG_SUFFIXES = {".log", ".txt"}
OPTIONAL_COMPRESSED_SUFFIX = ".gz"


class LogUploadForm(forms.Form):
    """
    Validates uploaded access log files before they are persisted to storage.
    """

    file = forms.FileField()
    name = forms.CharField(max_length=255, required=False)
    description = forms.CharField(required=False, widget=forms.Textarea)

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        suffixes = PurePath((uploaded_file.name or "").lower()).suffixes

        max_upload_size = getattr(
            settings,
            "LOGS_MAX_UPLOAD_SIZE",
            DEFAULT_MAX_UPLOAD_SIZE,
        )

        if uploaded_file.size > max_upload_size:
            raise forms.ValidationError(
                f"File exceeds the maximum upload size of {max_upload_size} bytes."
            )

        if not self._is_supported_extension(suffixes):
            raise forms.ValidationError(
                "Unsupported file type. Allowed files are .log, .txt, .log.gz, and .txt.gz."
            )

        return uploaded_file

    @staticmethod
    def _is_supported_extension(suffixes: list[str]) -> bool:
        if not suffixes:
            return False

        if suffixes[-1] == OPTIONAL_COMPRESSED_SUFFIX:
            return len(suffixes) >= 2 and suffixes[-2] in ALLOWED_LOG_SUFFIXES

        return suffixes[-1] in ALLOWED_LOG_SUFFIXES
