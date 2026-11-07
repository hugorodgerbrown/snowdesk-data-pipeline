"""
apps/public/map_shared.py — What the map shares with the help and offline pages.

Three answers the map and an apps/public page must give identically:

- ``map_modes_enabled`` — whether a request gets the map's Dated / Terrain
  toggle. The /help/ page reads it too, so the topic describing the toggle
  shows exactly where the toggle does.
- ``map_3d_view_enabled`` — whether a request gets the map's 3D view: the
  ``map_3d`` flag alone (SNOW-1451) for signed-in readers, or a route
  share's 3D grant for anyone (SNOW-1469). The home page, ``/offline/`` and
  the nav's wordmark read it too.
- ``trip_url_templates`` — the ``__UUID__`` / ``__TOKEN__`` URL templates a
  trip's geometry and page are composed from. The map renders them onto
  ``#map``, and the /offline/ page's Update composes an area's trips from
  the same two.

One of the modules ``apps.map`` may import from apps/public
(SNOW-1428, docs/decisions/public-splits-by-surface.md).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import waffle
from django.http import (
    HttpRequest,
)
from django.urls import reverse

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
