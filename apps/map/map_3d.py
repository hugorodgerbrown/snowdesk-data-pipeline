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
"""

from __future__ import annotations

import logging

import waffle
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from .models import Map3dShare

logger = logging.getLogger(__name__)

# The basemap the page draws on. swisstopo's winter style is the one whose
# rock and glacier shading reads best tilted, and the class tiles that paint
# Steep terrain only cover Switzerland anyway.
MAP_3D_BASEMAP = "swisstopo_winter"

# AWS's open Terrarium elevation tiles: global, token-less, z0-15. The CSP
# entry for this origin is in config/settings/base.py.
MAP_3D_DEM_TILE_URL = (
    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
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

    Args:
        request: The incoming HTTP request.

    Returns:
        The full page.

    Raises:
        Http404: When the flag is not active for this request and the
            request carries no live share token.

    """
    flagged = map_3d_enabled(request)
    token = request.GET.get("share", "")
    if flagged:
        # The reader's own token, minted the first time they open the page,
        # so Share always has one to put in the link.
        share, _ = Map3dShare.objects.get_or_create(created_by_id=request.user.pk)
        token = share.token
    elif not Map3dShare.objects.for_token(token).exists():
        raise Http404
    context = {
        "share_token": token,
        # A visitor through a share link sees no one's routes: they are
        # private to the account that drew them.
        "show_routes": flagged,
        "style_url": settings.BASEMAP_STYLES[MAP_3D_BASEMAP],
        "dem_tile_url": MAP_3D_DEM_TILE_URL,
        "terrain_class_tile_url": settings.TERRAIN_CLASS_TILE_URL or "",
        "ski_tours_url": reverse("api:ski_tours_geojson"),
        "routes_url": reverse("routes:geojson"),
        "resorts_url": reverse("api:resorts_geojson"),
    }
    return render(request, "public/map_3d.html", context)
