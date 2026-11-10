"""
apps/public/map_shared.py — What the map shares with the help and offline pages.

The answers the map and an apps/public page must give identically:

- ``map_modes_enabled`` — whether a request gets the map's Dated / Terrain
  toggle. The /help/ page reads it too, so the topic describing the toggle
  shows exactly where the toggle does.
- ``map_3d_view_enabled`` — whether a request gets the map's 3D view: the
  ``map_3d`` flag alone (SNOW-1451) for signed-in readers, or a route
  share's 3D grant for anyone (SNOW-1469). The home page, ``/offline/`` and
  the nav's wordmark read it too.
- ``map_beyond_switzerland_enabled`` — whether a request gets the winter
  map past the Swiss border (SNOW-1460): restyled OpenStreetMap, our relief
  and, with the 3D view, a 3D gate across the Alps.
- ``trip_url_templates`` — the ``__UUID__`` / ``__TOKEN__`` URL templates a
  trip's geometry and page are composed from. The map renders them onto
  ``#map``, and the /offline/ page's Update composes an area's trips from
  the same two.
- ``offline_audit_context`` — what the offline report's host element
  renders: ``/offline/``'s panel and, since SNOW-1488, the map's Downloads
  sheet, whose Copy report must run the same checks.

One of the modules ``apps.map`` may import from apps/public
(SNOW-1428, docs/decisions/public-splits-by-surface.md).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import waffle
from django.conf import settings
from django.http import (
    HttpRequest,
)
from django.urls import reverse
from django.utils import timezone

from apps.routes.services.map_3d_grant import map_3d_granted

logger = logging.getLogger(__name__)


def map_modes_enabled(request: HttpRequest) -> bool:
    """Whether this request gets the map's Dated / Terrain toggle (SNOW-1394).

    Behind the ``map_modes`` waffle flag, superusers first. Short-circuited
    for an anonymous request, as ``route_drawing_enabled`` is, so a
    signed-out visitor pays no waffle query for it; rolling it out to
    signed-out visitors means removing that guard deliberately. The /help/
    page reads the same answer, so the topic describing the toggle shows
    exactly where the toggle does.

    Args:
        request: The incoming HTTP request.

    Returns:
        True when the toggle (and its help topic) should render.

    """
    return bool(
        request.user.is_authenticated and waffle.flag_is_active(request, "map_modes")
    )


# Where ``map_3d_view_enabled`` keeps its answer on the request: /map/ asks
# twice (the view, then the nav's tag), and a granted visitor's answer costs
# queries.
_MAP_3D_VIEW_ATTR = "_snowdesk_map_3d_view"


def map_3d_view_enabled(request: HttpRequest) -> bool:
    """Whether the map shows this request its 3D view (SNOW-1453).

    The gate ``apps/map/views.py`` renders ``#map-view-toggle`` on
    (``map_3d_view``). Two ways in:

    - a signed-in reader inside the ``map_3d`` flag (SNOW-1451: the flag
      alone, so a reader without ``map_modes`` gets 3D on the map's one
      dated view);
    - anyone, signed in or not, whose session holds a route share's 3D
      grant that still holds (SNOW-1469,
      ``apps.routes.services.map_3d_grant``): the share is claimable and
      its sharer is inside ``map_3d``.

    A signed-out visitor with no grant pays no query and stays flat; Hugo
    chose signed-in readers only for the launch (7 Nov 2026), with a shared
    route as the one way past it. The answer is kept on the request. Here so
    that ``/offline/`` and the nav's ``{% map_3d_view_enabled %}`` tag can
    ask it without importing apps/map, which only the URL modules may
    (tests/map/test_boundary.py).

    Args:
        request: The incoming HTTP request.

    Returns:
        True when the map would pin elevation tiles for this reader.

    """
    cached = getattr(request, _MAP_3D_VIEW_ATTR, None)
    if cached is not None:
        return bool(cached)
    enabled = bool(
        request.user.is_authenticated and waffle.flag_is_active(request, "map_3d")
    )
    if not enabled:
        session = getattr(request, "session", None)
        enabled = session is not None and map_3d_granted(session)
    setattr(request, _MAP_3D_VIEW_ATTR, enabled)
    return enabled


def map_beyond_switzerland_enabled(request: HttpRequest) -> bool:
    """Whether the winter map is drawn past the Swiss border (SNOW-1460).

    Behind the ``map_beyond_switzerland`` waffle flag, superusers first,
    released separately from ``map_3d``. Short-circuited for an anonymous
    request, the shape ``map_3d_view_enabled`` has, so a signed-out visitor
    pays no waffle query for it.

    Args:
        request: The incoming HTTP request.

    Returns:
        True when the map should load the beyond-Switzerland scripts.

    """
    return bool(
        request.user.is_authenticated
        and waffle.flag_is_active(request, "map_beyond_switzerland")
    )


def trip_url_templates() -> dict[str, Any]:
    """Build the map's context for the trip deep links (SNOW-828).

    Two URL templates and nothing else — the map has no trips panel, no
    trips overlay and no eligibility question to answer. It draws a trip
    only when it arrives carrying ``?trip=`` or ``?trip_share=``, and all
    it needs is where to fetch the geometry from once it does.

    NO ELIGIBILITY GATE, deliberately, unlike ``routes_eligible`` beside
    it. Both endpoints scope themselves — the uuid one to participants,
    the token one to a live link — so emitting the templates to every
    visitor gives an ineligible one a URL that answers 403 or 404, which is
    what it should answer. Gating them here would additionally require the
    server to know, before any parameter has been read, which trip the
    visitor is about to ask for.

    Templated on ``__UUID__`` / ``__TOKEN__`` for the reason
    ``_routes_context`` states: ``static/js`` must not know how this
    project spells its URLs. The two placeholders differ because the two
    endpoints are addressed by different things, and a link-holder must
    never be handed a uuid.

    SNOW-965 adds ``trip_page_url_template``: an area download carries
    the trips inside it (``/api/area-items/``), and composes each one's
    page and route from these two uuid templates.

    Returns:
        Dict with ``trip_route_url_template``,
        ``trip_share_route_url_template`` and ``trip_page_url_template``.

    """
    dummy_uuid = uuid.UUID(int=0)
    return {
        "trip_route_url_template": reverse(
            "trips:route_geojson", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        "trip_page_url_template": reverse("trips:detail", args=[dummy_uuid]).replace(
            str(dummy_uuid), "__UUID__"
        ),
        # ``__TOKEN__`` survives ``<str:token>``'s converter, which accepts
        # any non-empty run without a slash.
        "trip_share_route_url_template": reverse(
            "trips:share_route_geojson", args=["__TOKEN__"]
        ),
    }


def offline_audit_context(request: HttpRequest, *, map_3d_view: bool) -> dict[str, Any]:
    """Return what the offline report's host element renders, for one request.

    The offline report (static/js/offline_audit.js) reads its inputs off the
    element it is collected against: the default basemap, the content feeds
    an area's Update resolves, the bulletin day, and the terrain-class and
    elevation tile templates an area is judged on. ``/offline/`` renders
    them on ``[data-offline-audit]``; since SNOW-1488 the map's Downloads
    sheet renders the same set on its own hidden host, so the report it
    copies runs the same checks. Every value is a ``reverse()`` or a setting,
    so this costs no query.

    Args:
        request: The current HTTP request (its user gates the account feeds).
        map_3d_view: Whether this request gets the map's 3D view, which is
            the gate on the elevation tile template (SNOW-1453). The caller
            passes the answer it already holds rather than reading the flag
            twice.

    Returns:
        Dict with ``default_basemap_key``, ``content_feeds``,
        ``content_countries``, ``today``, ``terrain_class_tile_url`` and
        ``dem_tile_url``: the keys ``includes/_offline_audit_host_attrs.html``
        reads.

    """
    trips = trip_url_templates()
    return {
        "default_basemap_key": settings.BASEMAP,
        # SNOW-925: what the per-row "Update" control needs in order to
        # resolve an area's content from a page with no map on it. The
        # map reads the same endpoints off `#map`'s own dataset; this
        # page has no `#map`, and re-deriving them client-side would be
        # a second copy of the routing that only this view can answer
        # for. `today` is the bulletin day the content is taken for,
        # matching the map's `data-today`.
        "content_feeds": {
            "regions": reverse("api:regions_geojson"),
            "weather": reverse("api:weather_geojson"),
            "weather_detail": reverse(
                "api:weather_detail", kwargs={"short_id": "__SHORTID__"}
            ),
            "favourites": reverse("favourites:geojson")
            if request.user.is_authenticated
            else "",
            "routes": reverse("routes:geojson")
            if request.user.is_authenticated
            else "",
            "community_reports": reverse("api:community_reports_geojson"),
            # SNOW-953: what an area's boundary contains, answered
            # server-side. The region feeds above are still WARMED —
            # the map needs them offline — but nothing here parses
            # them to plan from any more.
            "area_content": reverse("api:area_content"),
            # SNOW-965: the reader's own trips and pins inside an
            # area, and the two templates a trip's urls are composed
            # from. Account-only, like favourites and routes above: a
            # signed-out reader has none, so the Update asks nothing.
            "area_items": reverse("api:area_items")
            if request.user.is_authenticated
            else "",
            "trip_page": trips["trip_page_url_template"],
            "trip_route": trips["trip_route_url_template"],
            # SNOW-1312: the dated ratings feed the map boots its
            # colours from, and the map's region sheet — the two
            # forward-day families the map's download warms besides
            # the bulletin pages, so an area updated from here holds
            # what one updated from the map does. The sheet template
            # is the one `_map_embed.html` renders onto `#map`.
            "ratings": reverse("api:ratings"),
            "region_sheet": reverse(
                "public:region_sheet_partial", kwargs={"region_id": "XX-0000"}
            ),
            # SNOW-1318: and each resort's place sheet, per day — the
            # ``__SLUG__`` template ``_map_embed.html`` renders.
            "resort_sheet": reverse(
                "public:resort_sheet_partial", kwargs={"slug": "__SLUG__"}
            ),
            # SNOW-1313: a saved pin's place sheet, warmed per pin the
            # area-items answer names — the nil-uuid template
            # `_map_embed.html` renders onto `#map`. Account-only, as
            # area_items is.
            "favourite_sheet": reverse(
                "public:favourite_sheet_partial",
                kwargs={"uuid": "00000000-0000-0000-0000-000000000000"},
            )
            if request.user.is_authenticated
            else "",
        },
        # The countries whose region geometry a boundary may overlap.
        # SNOW-931's lesson, applied where there is no
        # `pwaMapCountries` to ask: resolve against EVERY country, not
        # the ones some client-side state happens to hold, or a border
        # area silently drops a country's bulletins.
        "content_countries": " ".join(settings.MAP_COUNTRY_CODES),
        "today": timezone.localdate().isoformat(),
        # SNOW-1138: the terrain-class tile template, so the report
        # judges an area downloaded WITH terrain data on its class
        # tiles, as the Manage downloads sheet does. The map reads the
        # same value off `#map`; empty while the filter is unconfigured,
        # which is the sheet's "not judged" case too.
        "terrain_class_tile_url": settings.TERRAIN_CLASS_TILE_URL,
        # SNOW-1453: the elevation tile template, on the map's own gate
        # for its 3D view (`map_3d_view` in apps/map/views.py).
        "dem_tile_url": settings.MAP_3D_DEM_TILE_URL if map_3d_view else "",
    }
