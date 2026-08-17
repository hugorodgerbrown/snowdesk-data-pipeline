"""
apps/routes/urls.py — URL routing for the routes application.

Mounted under ``/routes/`` by ``config/urls.py`` (registered before
``apps.public.urls`` so its generic ``<str:region_id>/`` catch-all does not
swallow this prefix).

URL structure:
  routes/partials/create/            POST — upload and ingest a .gpx
  routes/partials/<uuid>/rename/     POST — rename a route
  routes/partials/<uuid>/delete/     POST — delete a route
  routes/partials/list/              GET  — owner's routes list (SNOW-686)
"""

from django.urls import path

from . import views

app_name = "routes"

urlpatterns = [
    path(
        "partials/create/",
        views.route_create,
        name="create",
    ),
    path(
        "partials/<uuid:uuid>/rename/",
        views.route_rename,
        name="rename",
    ),
    path(
        "partials/<uuid:uuid>/delete/",
        views.route_delete,
        name="delete",
    ),
    path(
        "partials/list/",
        views.route_list,
        name="list",
    ),
]
