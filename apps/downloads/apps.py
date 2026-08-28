"""
apps/downloads/apps.py — AppConfig for the downloads application.

The downloads app owns ``DownloadArea``: the *definition* of an offline
basemap area an authenticated user has downloaded on some device. The
tiles themselves are never stored here — they live in that device's own
pinned Cache Storage bucket, and always will. See ``models.py`` for what
is and is not synced, and why.
"""

from django.apps import AppConfig


class DownloadsConfig(AppConfig):
    """AppConfig for the downloads application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.downloads"
    verbose_name = "Downloads"
