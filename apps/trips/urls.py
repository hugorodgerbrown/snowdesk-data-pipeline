"""
apps/trips/urls.py — URL routing for the trips application.

Mounted under ``/trips/`` by ``config/urls.py``, registered BEFORE
``apps.public.urls`` so that app's generic ``<str:region_id>/`` catch-all
does not resolve "trips" as a region — the same ordering constraint
``favourites/``, ``routes/`` and ``downloads/`` carry.

URL structure:
  trips/new/                         GET  — the authoring form
  trips/partials/create/             POST — write a trip (SNOW-820)
  trips/partials/<uuid>/edit/        POST — update the plan
  trips/partials/<uuid>/delete/      POST — delete the trip
  trips/<uuid>/                      GET  — the trip's own page

The ``partials/`` prefix marks the HTMX fragment endpoints
(``@require_htmx``); the two pages sit outside it because they are
navigations and not fragments.
"""

from django.urls import path

from . import views

app_name = "trips"

urlpatterns = [
    path(
        "new/",
        views.trip_new,
        name="new",
    ),
    path(
        "partials/create/",
        views.trip_create,
        name="create",
    ),
    path(
        "partials/<uuid:uuid>/edit/",
        views.trip_edit,
        name="edit",
    ),
    path(
        "partials/<uuid:uuid>/delete/",
        views.trip_delete,
        name="delete",
    ),
    path(
        "<uuid:uuid>/",
        views.trip_detail,
        name="detail",
    ),
]
