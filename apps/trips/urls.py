"""
apps/trips/urls.py — URL routing for the trips application.

Mounted under ``/trips/`` by ``config/urls.py``, registered BEFORE
``apps.public.urls`` so that app's generic ``<str:region_id>/`` catch-all
does not resolve "trips" as a region — the same ordering constraint
``favourites/``, ``routes/`` and ``downloads/`` carry.

URL structure:
  trips/                             GET  — the reader's own trips (SNOW-823)
  trips/new/                         GET  — the authoring form
  trips/partials/create/             POST — write a trip (SNOW-820)
  trips/partials/<uuid>/edit/        POST — update the plan
  trips/partials/<uuid>/delete/      POST — delete the trip
  trips/partials/s/<token>/join/     POST — join the trip (SNOW-822)
  trips/partials/<uuid>/leave/       POST — leave it
  trips/partials/<uuid>/save-route/  POST — copy the route (SNOW-824)
  trips/partials/s/<token>/save-route/
                                     POST — the same, for a link-holder
  trips/<uuid>/                      GET  — the trip's own page
  trips/<uuid>/route.geojson         GET  — the route, for the map (SNOW-828)
  trips/<uuid>/share/                POST — mint/rotate the link (SNOW-821)
  trips/<uuid>/share/revoke/         POST — null the token
  trips/s/<token>/                   GET  — the public page behind the link
  trips/s/<token>/route.geojson      GET  — the same route, for a link-holder

The ``partials/`` prefix marks the HTMX fragment endpoints
(``@require_htmx``); the pages sit outside it because they are navigations
and not fragments.

JOIN IS ADDRESSED BY TOKEN AND LEAVE BY UUID, deliberately. Holding a live
link is what lets somebody join, and the token is the only identifier a
non-participant has been given — the uuid keys the participant-scoped
endpoints, so a link-holder must never see one. Leaving is a participant's
act and a participant has the uuid.

The three SNOW-821 endpoints are outside it too, each for its own reason.
The two ``share/`` writes answer JSON to a plain ``fetch()`` — their bodies
go to the native share sheet, not into the page. ``s/<token>/`` is the URL
a person is SENT in a message and opens in a browser, so it is short
(``/trips/s/…``, the shape ``/s/…`` already set for bulletin and route
shares) and it is emphatically not a fragment.
"""

from django.urls import path

from . import views

app_name = "trips"

urlpatterns = [
    path(
        "",
        views.trip_list,
        name="list",
    ),
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
        "partials/s/<str:token>/join/",
        views.trip_join,
        name="join",
    ),
    path(
        "partials/<uuid:uuid>/leave/",
        views.trip_leave,
        name="leave",
    ),
    path(
        "partials/<uuid:uuid>/save-route/",
        views.trip_route_save,
        name="save_route",
    ),
    path(
        "partials/s/<str:token>/save-route/",
        views.trip_route_save_shared,
        name="save_route_shared",
    ),
    path(
        "<uuid:uuid>/share/",
        views.trip_share_create,
        name="share_create",
    ),
    path(
        "<uuid:uuid>/share/revoke/",
        views.trip_share_revoke,
        name="share_revoke",
    ),
    path(
        "s/<str:token>/",
        views.trip_share_page,
        name="share_page",
    ),
    path(
        "s/<str:token>/route.geojson",
        views.trip_share_route_geojson,
        name="share_route_geojson",
    ),
    path(
        "<uuid:uuid>/route.geojson",
        views.trip_route_geojson,
        name="route_geojson",
    ),
    path(
        "<uuid:uuid>/",
        views.trip_detail,
        name="detail",
    ),
]
