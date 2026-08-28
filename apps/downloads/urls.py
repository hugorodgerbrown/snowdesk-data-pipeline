"""
apps/downloads/urls.py — URL routing for the downloads application.

Mounted under ``/downloads/`` by ``config/urls.py``, registered before
``apps.public.urls`` so that app's generic ``<str:region_id>/`` catch-all
does not swallow the prefix — the same ordering constraint ``favourites/``
and ``routes/`` carry.

URL structure:
  downloads/partials/sync/                  POST — record one area
  downloads/partials/<area_id>/rename/      POST — rename a custom area
  downloads/partials/<area_id>/forget/      POST — drop the account row
  downloads/areas.json                      GET  — the owner's areas

``areas.json`` sits OUTSIDE the ``partials/`` prefix on purpose: that
prefix marks the HTMX fragment endpoints (``@require_htmx``), and this one
is a plain-JSON layer consumed by a ``fetch()`` from
static/js/downloads_sync.js. Mirrors ``routes/routes.geojson``.

Rows are addressed by their ``area_id``, not by ``uuid``. The area id is
minted client-side and names the device's own Cache Storage bucket, so the
client always has it to hand and never has to learn a server-side
identifier before it can rename or forget an area — which matters because
these writes go through the mutation queue and may replay long after the
response that would have carried a uuid was discarded. ``(user, area_id)``
is unique, so it addresses exactly one row.
"""

from django.urls import path

from . import views

app_name = "downloads"

urlpatterns = [
    path(
        "partials/sync/",
        views.area_sync,
        name="sync",
    ),
    path(
        "partials/<str:area_id>/rename/",
        views.area_rename,
        name="rename",
    ),
    path(
        "partials/<str:area_id>/forget/",
        views.area_forget,
        name="forget",
    ),
    path(
        "areas.json",
        views.areas_json,
        name="areas",
    ),
]
