"""
pipeline/dev_urls.py — Development-only URL routes.

Mounted under ``/dev/slf-mirror/`` from ``config/urls.py`` only when
``settings.DEBUG`` is true. Production never imports this module.
"""

from django.urls import path

from pipeline.dev_views import slf_mirror

app_name = "dev"

urlpatterns = [
    path(
        "api/bulletin-list/caaml/<str:lang>/json",
        slf_mirror,
        name="slf_mirror",
    ),
]
