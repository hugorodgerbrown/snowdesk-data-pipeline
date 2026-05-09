"""
bulletins/dev_urls.py — Development-only URL routes for bulletin sources.

Mounted from ``config/urls.py`` only when ``settings.DEBUG`` is true.
Production never imports this module.

Contains routes for two mirrors:

- ``/dev/slf-mirror/…`` — SLF CAAML bulletin-list mirror (``slf_mirror``).
- ``/dev/openmeteo-mirror/v1/forecast`` — Open-Meteo forecast mirror.
- ``/dev/openmeteo-mirror/v1/archive`` — Open-Meteo archive mirror.

Both Open-Meteo mirror routes share the same ``openmeteo_mirror`` view;
the ``kind`` URL parameter distinguishes them within the view, but the
behaviour is identical — both replay ``sample_data/openmeteo_archive.ndjson``.
"""

from django.urls import path

from bulletins.dev_views import openmeteo_mirror, slf_mirror

app_name = "dev"

urlpatterns = [
    path(
        "api/bulletin-list/caaml/<str:lang>/json",
        slf_mirror,
        name="slf_mirror",
    ),
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
