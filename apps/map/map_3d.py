"""
apps/map/map_3d.py — the 3-D terrain map at /map/3d/ (SNOW-1438).

A sandbox, separate from /map/: the relief drawn in 3-D with MapLibre's own
terrain, and Steep terrain, SAC's ski tours, the reader's own routes and the
resorts draped over it. It exists so the 3-D view can be tried on real data
before anything about /map/ changes
(docs/decisions/a-3d-map-is-maplibre-terrain.md).

Behind the ``map_3d`` waffle flag. Outside it the page is a 404, not a
redirect or a teaser: there is nothing here for anyone else yet. The one
way past the flag is a share link: ``?share=<token>`` naming a live
``Map3dShare`` opens the page for anyone, signed in or not, and deleting
that row in the admin stops it.

A ROUTE share opens it too. ``?route_share=<token>`` naming a live
``RouteShare`` whose sharer holds a ``Map3dShare`` opens the page on that
route, for anyone. The visitor sees the shared route and no one else's: the
routes layer is ``routes.geojson``, which serves a request its own routes
and the shares its session has followed, and nothing more. The link people
are sent is ``/routes/s/<token>/3d/`` (``routes:share_redirect_3d``), the
route share URL with ``3d/`` on the end. Deleting the sharer's
``Map3dShare`` stops their route links opening in 3-D as well.
"""

from __future__ import annotations

import logging

import waffle
from django.conf import settings
from django.db import models
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django_ratelimit.core import is_ratelimited

from apps.core.http import is_top_level_navigation
from apps.routes.models import RouteShare
from apps.routes.services.shares import (
    SHARE_FOLLOW_GROUP,
    SHARE_FOLLOW_RATE,
    add_pending_token,
    pending_tokens,
    share_follow_bucket,
)

from .models import Map3dShare

logger = logging.getLogger(__name__)

# The basemap the page draws on. swisstopo's winter style is the one whose
# rock and glacier shading reads best tilted, and the class tiles that paint
# Steep terrain only cover Switzerland anyway.
MAP_3D_BASEMAP = "swisstopo_winter"

# Mapterhorn's open Terrarium elevation tiles: 512 px WebP, served from
# Europe with a 7-day cache, built over Switzerland from swisstopo's
# swissALTI3D (Open Government Data) and elsewhere from Copernicus GLO-30.
# Sharper relief than AWS's Terrarium set, which had no cache headers and
# answered from us-east. The CSP entry for this origin is in
# config/settings/base.py.
MAP_3D_DEM_TILE_URL = "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"

# The placeholder the page's script swaps for a route's uuid in the
# share-create URL template, as apps/map/views.py's route templates spell it.
_UUID_PLACEHOLDER = "__UUID__"
_DUMMY_UUID = "00000000-0000-0000-0000-000000000000"


def _shares_for_3d() -> models.QuerySet[RouteShare]:
    """Return the route shares that may open the 3-D map.

    Live (``RouteShare.objects.active``), not the example route (every
    visitor is offered that one, and it must not become a way past the
    flag), and made by someone who holds a ``Map3dShare`` — a reader of
    the 3-D map, whose row is what revokes it.

    Returns:
        The queryset.

    """
    return RouteShare.objects.active().filter(
        is_example=False, created_by__map_3d_share__isnull=False
    )


def route_share_for_3d(token: str) -> RouteShare | None:
    """Return the route share ``token`` names, if it may open the 3-D map.

    Args:
        token: The ``?route_share=`` value from a link.

    Returns:
        The share, or None for a blank, unknown, dead or ineligible token.

    """
    if not token:
        return None
    return _shares_for_3d().filter(token=token).first()


def pending_route_share_for_3d(request: HttpRequest) -> str:
    """Return the newest followed route share that may open in 3-D.

    For /map/'s "3D" roundel: a friend sent a route by a 3-D reader gets
    the roundel, carrying this token, even outside the flag. Free for a
    session that has followed no share link: the session is read and no
    query runs.

    Args:
        request: The incoming HTTP request.

    Returns:
        The token, or "" when there is none.

    """
    tokens = pending_tokens(request.session)
    if not tokens:
        return ""
    eligible = set(
        _shares_for_3d().filter(token__in=tokens).values_list("token", flat=True)
    )
    for token in reversed(tokens):
        if token in eligible:
            return token
    return ""


def _over_follow_budget(request: HttpRequest, token: str) -> bool:
    """Count one follow of ``token`` and say whether the budget is spent.

    The budget the share follows in apps/routes/views.py spend: one group,
    one (token, IP) bucket, so this page and the two links share it.

    Args:
        request: The incoming HTTP request.
        token: The route share token being followed.

    Returns:
        True when this follow is over the budget.

    """
    return bool(
        is_ratelimited(
            request,
            group=SHARE_FOLLOW_GROUP,
            key=lambda group, req: share_follow_bucket(token, req),
            rate=SHARE_FOLLOW_RATE,
            increment=True,
        )
    )


def map_3d_enabled(request: HttpRequest) -> bool:
    """Whether this request may open the 3-D map.

    Short-circuited for an anonymous request, as ``map_modes_enabled`` is,
    so a signed-out visitor pays no waffle query for a page that 404s for
    them anyway.

    Args:
        request: The incoming HTTP request.

    Returns:
        True when the ``map_3d`` flag is active for the requesting user.

    """
    return bool(
        request.user.is_authenticated and waffle.flag_is_active(request, "map_3d")
    )


def map_3d_page(request: HttpRequest) -> HttpResponse:
    """Render the 3-D terrain map, or 404 outside the ``map_3d`` flag.

    Every URL the page's script fetches travels as a data attribute on
    ``#map-3d``, so ``static/js/map_3d.js`` builds none of them itself. The
    class-tile template is empty where the environment has not configured
    it, and the script then leaves Steep terrain out.

    Three ways in, checked in this order:

    * the ``map_3d`` flag: the reader's own routes are drawn, and Share
      can send one of them (``route_share_url_template``);
    * ``?route_share=<token>`` (``route_share_for_3d``): the shared route
      is drawn, through the session's pending shares, and framed;
    * ``?share=<token>`` naming a ``Map3dShare``: no routes at all.

    A route share's token is seated in the session here as well as by
    ``routes:share_redirect_3d``, so a copied ``/map/3d/?route_share=…``
    address works as well as the link it came from.

    Args:
        request: The incoming HTTP request.

    Returns:
        The full page.

    Raises:
        Http404: When the flag is not active for this request and the
            request carries no live share token.

    A route share that would seat its token past the follows' rate-limit
    budget answers 429, as the share links do.

    """
    flagged = map_3d_enabled(request)
    route_share = route_share_for_3d(request.GET.get("route_share", ""))
    token = request.GET.get("share", "")
    if flagged:
        # The reader's own token, minted the first time they open the page,
        # so Share always has one to put in the link.
        share, _ = Map3dShare.objects.get_or_create(created_by_id=request.user.pk)
        token = share.token
    elif route_share is not None:
        # The sharer's Map3dShare token stays theirs: a route link opens
        # the page on its own, and a visitor's Share hands on the route.
        token = ""
    elif not Map3dShare.objects.for_token(token).exists():
        raise Http404
    if route_share is not None and is_top_level_navigation(request):
        # The share follows' own budget, so a copied address cannot seat
        # tokens (and grow the session table) faster than the link can.
        if _over_follow_budget(request, route_share.token):
            return HttpResponse(status=429)
        add_pending_token(request.session, route_share.token)
    context = {
        "share_token": token,
        "route_share_token": route_share.token if route_share else "",
        # Only a reader inside the flag shares their own routes from here.
        "route_share_url_template": (
            reverse("routes:share_create", args=[_DUMMY_UUID]).replace(
                _DUMMY_UUID, _UUID_PLACEHOLDER
            )
            if flagged
            else ""
        ),
        # A visitor through a Map3dShare link sees no one's routes: they
        # are private to the account that drew them. A route share shows
        # its one route, which routes.geojson serves from the session.
        "show_routes": flagged or route_share is not None,
        "style_url": settings.BASEMAP_STYLES[MAP_3D_BASEMAP],
        "dem_tile_url": MAP_3D_DEM_TILE_URL,
        "terrain_class_tile_url": settings.TERRAIN_CLASS_TILE_URL or "",
        "ski_tours_url": reverse("api:ski_tours_geojson"),
        "routes_url": reverse("routes:geojson"),
        "resorts_url": reverse("api:resorts_geojson"),
    }
    return render(request, "public/map_3d.html", context)
