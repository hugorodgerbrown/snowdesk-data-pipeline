"""
bulletins/dev_urls_openmeteo.py — Development-only URL routes for the Open-Meteo mirror.

Mounted under ``/dev/openmeteo-mirror/`` from ``config/urls.py`` only when
``settings.DEBUG`` is true. Production never imports this module.

Provides ``v1/forecast`` and ``v1/archive`` routes that both map to
``bulletins.dev_views.openmeteo_mirror``, passing a ``kind`` kwarg to
distinguish the two endpoint shapes. Both serve the same
``sample_data/openmeteo_archive.ndjson`` data; the URL difference mirrors
the upstream Open-Meteo API's structure so the fetcher's URL construction
(``f"{base_url}/forecast"`` / ``f"{base_url}/archive"``) resolves correctly.
"""

from django.urls import path

from bulletins.dev_views import openmeteo_mirror

app_name = "dev_om"

urlpatterns = [
    path(
        "v1/forecast",
        openmeteo_mirror,
        {"kind": "forecast"},
        name="openmeteo_mirror_forecast",
    ),
    path(
        "v1/archive",
        openmeteo_mirror,
        {"kind": "archive"},
        name="openmeteo_mirror_archive",
    ),
]
