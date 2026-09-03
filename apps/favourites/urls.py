"""
apps/favourites/urls.py — URL routing for the favourites application.

Mounted under ``/favourites/`` by ``config/urls.py`` (registered before
``apps.public.urls`` so its generic ``<str:region_id>/`` catch-all does not
swallow this prefix).

URL structure:
  favourites/partials/create/               POST — save a new favourite
  favourites/partials/resort/create/        POST — save a resort favourite (SNOW-499)
  favourites/partials/resort/<slug>/toggle/  POST — toggle a resort favourite,
                                             online-only (SNOW-504)
  favourites/partials/<uuid>/rename/         POST — rename a favourite
  favourites/partials/<uuid>/delete/         POST — delete a favourite
  favourites/partials/<uuid>/card/           GET  — detail card (SNOW-415)
  favourites/partials/list/                  GET  — owner's favourites list (SNOW-415)
  favourites/favourites.geojson              GET  — the user's own pins
  favourites/<uuid>/                         GET  — 301 to the pin's weather
                                             page (SNOW-800; was the SNOW-507
                                             detail page)
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
        "partials/resort/create/",
        views.favourite_create_from_resort,
        name="resort_create",
    ),
    path(
        "partials/resort/<slug:slug>/toggle/",
        views.favourite_resort_toggle,
        name="resort_toggle",
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
        "partials/<uuid:uuid>/card/",
        views.favourite_card,
        name="card",
    ),
    path(
        "partials/list/",
        views.favourite_list,
        name="list",
    ),
    path(
        "favourites.geojson",
        views.favourites_geojson,
        name="geojson",
    ),
    # SNOW-800: the detail page is gone — a favourite is a map pin, not a
    # document — but the URL was bookmarkable, so it 301s to the pin's
    # weather page. The old ``favourites:detail`` name is deliberately NOT
    # kept: a template that still reversed it would link to a redirect,
    # and a NoReverseMatch is the louder failure.
    path(
        "<uuid:uuid>/",
        views.favourite_detail_redirect,
        name="detail_redirect",
    ),
]
