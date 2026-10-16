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
  routes/partials/share/<token>/claim/
                                     POST — claim a copy (SNOW-764)
  routes/routes.geojson              GET  — owner's routes as GeoJSON (SNOW-687)
  routes/<uuid>/share/               POST — mint a share link (SNOW-764)
  routes/s/<token>/                  GET  — follow a share link (SNOW-764)

``routes.geojson`` sits OUTSIDE the ``partials/`` prefix on purpose: that
prefix marks the HTMX fragment endpoints (``@require_htmx``), and this one
is a plain-JSON layer consumed by a ``fetch()`` from static/js/map.js.
Mirrors ``favourites/favourites.geojson``.

The two SNOW-764 endpoints outside that prefix are outside it for the same
rule, each for its own reason. ``<uuid>/share/`` answers JSON to a plain
``fetch()`` — its body goes to the native share sheet, not into the page.
``s/<token>/`` is a NAVIGATION: it is the URL a person is sent in a
message and opens in a browser, so it is short (``/routes/s/…``, the shape
``/s/…`` already set for bulletin shares) and it is emphatically not a
fragment. Only the claim is, and it sits under ``partials/`` with the rest.
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
    path(
        "partials/share/<str:token>/claim/",
        views.route_share_claim,
        name="share_claim",
    ),
    path(
        "routes.geojson",
        views.routes_geojson,
        name="geojson",
    ),
    path(
        "<uuid:uuid>/share/",
        views.route_share_create,
        name="share_create",
    ),
    path(
        "s/<str:token>/",
        views.route_share_redirect,
        name="share_redirect",
    ),
]
