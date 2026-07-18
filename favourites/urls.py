"""
favourites/urls.py — URL routing for the favourites application.

Mounted under ``/favourites/`` by ``config/urls.py`` (registered before
``public.urls`` so its generic ``<str:region_id>/`` catch-all does not
swallow this prefix).

URL structure:
  favourites/partials/create/               POST — save a new favourite
  favourites/partials/<uuid>/rename/         POST — rename a favourite
  favourites/partials/<uuid>/delete/         POST — delete a favourite
  favourites/favourites.geojson              GET  — the user's own pins
"""

from django.urls import path

from . import views

app_name = "favourites"

urlpatterns = [
    path(
        "partials/create/",
        views.favourite_create,
        name="create",
    ),
    path(
        "partials/<uuid:uuid>/rename/",
        views.favourite_rename,
        name="rename",
    ),
    path(
        "partials/<uuid:uuid>/delete/",
        views.favourite_delete,
        name="delete",
    ),
    path(
        "favourites.geojson",
        views.favourites_geojson,
        name="geojson",
    ),
]
