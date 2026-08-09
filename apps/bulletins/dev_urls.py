"""
apps/bulletins/dev_urls.py — Development-only URL routes for the SLF bulletin mirror.

Mounted under ``/dev/slf-mirror/`` from ``config/urls.py`` only when
``settings.DEBUG`` is true. Production never imports this module.

The companion Open-Meteo mirror routes live in ``apps/weather/dev_urls.py``
with their own ``app_name = "dev_om"`` to avoid the ``urls.W005`` duplicate-
namespace warning that arises when the same URL module is mounted twice.
"""

from django.urls import path

from apps.bulletins.dev_views import slf_mirror

app_name = "dev"

urlpatterns = [
    path(
        "api/bulletin-list/caaml/<str:lang>/json",
        slf_mirror,
        name="slf_mirror",
    ),
]
