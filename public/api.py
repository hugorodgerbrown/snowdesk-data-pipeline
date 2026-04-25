"""
public/api.py — JSON endpoints for the interactive map.

Lightweight endpoints consumed by ``static/js/map.js`` to render the
Swiss region choropleth and back the per-region bottom sheet:

* ``/api/today-summaries/``                — per-region danger summary for today.
* ``/api/season-ratings/``                 — ``{date: {region_id: rating_int}}``
  for the entire stored dataset (consumed by the timelapse debug button and,
  later, the season scrubber).
* ``/api/resorts-by-region/``              — ``{region_id: [resort_name, ...]}``.
* ``/api/regions.geojson``                 — FeatureCollection of region polygons.
* ``/api/region/<region_id>/summary/``     — pre-rendered peek + expanded HTML
  for the region's current bulletin (consumed by the bottom sheet).
* ``/api/offline-manifest/map/``           — precache manifest for the offline CTA.

Plain Django ``JsonResponse`` views — no DRF. The choropleth fetches its
three data endpoints in parallel at load time; the per-region summary
endpoint is hit on demand when the user taps a region.
"""

from __future__ import annotations

import datetime
import logging
import math
import urllib.parse
from typing import Any

import requests
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from pipeline.models import Bulletin, Region, RegionBulletin, RegionDayRating

from .views import _PROBLEM_LABELS, _select_bulletin_for_date, _select_default_issue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Offline manifest constants
# ---------------------------------------------------------------------------

_OFFLINE_MANIFEST_VERSION = "map-shell-v1"

# Swiss bounding box (west, south, east, north) in decimal degrees.
_SWISS_BBOX: tuple[float, float, float, float] = (5.9, 45.8, 10.5, 47.8)

# Vector tile zoom levels z5–z10 give good coverage without excessive tile counts.
_VECTOR_TILE_ZOOM: range = range(5, 11)

# Natural Earth raster at z5–z6 only (low zoom, small file count).
_RASTER_TILE_ZOOM: range = range(5, 7)

_OFM_BASE = "https://tiles.openfreemap.org"
_OFM_VECTOR_TILEJSON = _OFM_BASE + "/planet"
_OFM_VECTOR_FALLBACK_TEMPLATE = _OFM_BASE + "/planet/{z}/{x}/{y}.pbf"
_OFM_RASTER_TEMPLATE = _OFM_BASE + "/natural_earth/ne2sr/{z}/{x}/{y}.png"
_OFM_SPRITE_BASE = _OFM_BASE + "/sprites/ofm_f384"

# TileJSON fetch timeout in seconds — tight enough to keep the manifest
# endpoint responsive when OFM is slow, loose enough for a healthy round-trip.
_OFM_TILEJSON_TIMEOUT = 3.0

# MapLibre version loaded from CDN in public/templates/public/map.html.
# Must stay in sync with the <script> and <link> tags in that template.
_MAPLIBRE_VERSION = "4.7.1"
_MAPLIBRE_CDN = f"https://unpkg.com/maplibre-gl@{_MAPLIBRE_VERSION}/dist"

# Latin + Latin-1 Supplement glyph ranges — sufficient for Swiss place names.
_GLYPH_RANGES = ["0-255", "256-511"]
_GLYPH_FONTSTACKS = [
    "Noto Sans Regular",
    "Noto Sans Bold",
    "Noto Sans Italic",
]

# ---------------------------------------------------------------------------
# Tile-URL helpers (module-private)
# ---------------------------------------------------------------------------


def _lon_to_tile_x(lon_deg: float, zoom: int) -> int:
    """Convert longitude in degrees to a slippy-map tile X coordinate.

    Args:
        lon_deg: Longitude in decimal degrees (−180 … +180).
        zoom: Tile zoom level (0 … 22).

    Returns:
        Integer X tile coordinate.

    """
    # ``2**zoom`` is typed ``Any`` by mypy (the result type depends on the
    # sign of the exponent at type-check time), which propagates through the
    # expression and makes ``math.floor`` return ``Any``. ``int(...)`` pins
    # the result back to ``int``.
    return int(math.floor((lon_deg + 180.0) / 360.0 * (2**zoom)))


def _lat_to_tile_y(lat_deg: float, zoom: int) -> int:
    """Convert latitude in degrees to a slippy-map tile Y coordinate.

    The slippy-map Y axis is inverted relative to latitude: higher
    latitudes yield lower Y numbers.

    Args:
        lat_deg: Latitude in decimal degrees (−85 … +85).
        zoom: Tile zoom level (0 … 22).

    Returns:
        Integer Y tile coordinate.

    """
    lat_rad = math.radians(lat_deg)
    # See ``_lon_to_tile_x`` for why the ``int(...)`` wrap is needed.
    return int(
        math.floor(
            (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
            / 2.0
            * (2**zoom)
        )
    )


def _generate_tile_urls(
    url_template: str,
    bbox: tuple[float, float, float, float],
    zoom_range: range,
) -> list[str]:
    """Enumerate slippy-map tile URLs covering a bounding box.

    Args:
        url_template: URL with ``{z}``, ``{x}``, ``{y}`` placeholders.
        bbox: ``(west, south, east, north)`` in decimal degrees.
        zoom_range: Python ``range`` of zoom levels to enumerate.

    Returns:
        Flat list of fully-resolved tile URLs, one per covered tile.

    """
    west, south, east, north = bbox
    urls: list[str] = []
    for z in zoom_range:
        x_min = _lon_to_tile_x(west, z)
        x_max = _lon_to_tile_x(east, z)
        # North gives the *lower* Y because the Y axis is inverted.
        y_min = _lat_to_tile_y(north, z)
        y_max = _lat_to_tile_y(south, z)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                urls.append(url_template.format(z=z, x=x, y=y))
    return urls


def _fetch_vector_tile_template() -> str:
    """Resolve the current OpenFreeMap vector-tile URL template.

    OpenFreeMap embeds a build-version segment into the vector-tile URL
    template exposed by its TileJSON endpoint (e.g. ``planet/20260415_001001_pt
    /{z}/{x}/{y}.pbf``). MapLibre requests tiles via that versioned URL
    at runtime, so our precache manifest must match it exactly — otherwise
    cached tiles live under URLs the browser never requests and the offline
    cache is effectively empty for vector tiles.

    The fallback (unversioned ``/planet/{z}/{x}/{y}.pbf``) is deliberately
    kept as a safety net: if OpenFreeMap is temporarily unreachable the
    manifest endpoint still responds, and the fallback URL still resolves
    to a valid tile when fetched directly (OFM serves both paths with the
    same content).

    Returns:
        The fully-qualified tile URL template with ``{z}``, ``{x}``, ``{y}``
        placeholders, as published by OFM's TileJSON.

    """
    try:
        resp = requests.get(_OFM_VECTOR_TILEJSON, timeout=_OFM_TILEJSON_TIMEOUT)
        resp.raise_for_status()
        tiles = resp.json().get("tiles")
        if isinstance(tiles, list) and tiles and isinstance(tiles[0], str):
            return tiles[0]
        logger.warning(
            "OFM TileJSON returned an unexpected shape; "
            "falling back to the unversioned template"
        )
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "OFM TileJSON fetch failed (%s); falling back to the unversioned template",
            exc,
        )
    return _OFM_VECTOR_FALLBACK_TEMPLATE


# ---------------------------------------------------------------------------
# Existing helpers
# ---------------------------------------------------------------------------

_ASPECT_JOIN = ", "


def _format_elevation(elevation: dict[str, Any] | None) -> str:
    """
    Render a render_model ``elevation`` dict as a human-readable band.

    The render model stores elevation as ``{lower, upper, treeline}``.
    This returns strings like ``"above 2200 m"``, ``"below 1800 m"``,
    ``"1200 m – 2400 m"``, or ``""`` when no bounds are set.

    Args:
        elevation: Render-model elevation dict, or ``None``.

    Returns:
        A concise display string (may be empty).

    """
    if not elevation:
        return ""
    lower = elevation.get("lower")
    upper = elevation.get("upper")
    if lower and upper:
        return f"{lower} m – {upper} m"
    if lower:
        return f"above {lower} m"
    if upper:
        return f"below {upper} m"
    if elevation.get("treeline"):
        return "around the treeline"
    return ""


def _format_aspects(aspects: list[str] | None) -> str:
    """
    Render a render_model ``aspects`` list as a display string.

    Eight aspects → ``"all aspects"``; otherwise a comma-joined list
    preserving the input order.

    Args:
        aspects: Render-model aspects list, or ``None``.

    Returns:
        A comma-joined display string (may be empty).

    """
    if not aspects:
        return ""
    if len(aspects) >= 8:
        return "all aspects"
    return _ASPECT_JOIN.join(aspects)


def _summary_for_bulletin(bulletin: Bulletin, region_name: str) -> dict[str, Any]:
    """
    Build a single-region summary dict from a bulletin's render_model.

    Pulls the headline danger rating and the dominant (first) trait's
    first problem for the dashboard sheet. The JS only displays
    ``rating``/``subdivision`` in the current design, but the other
    fields are populated so future sheet layouts can show them without
    a round-trip change.

    Args:
        bulletin: The Bulletin to summarise.
        region_name: Region display name at the time of this bulletin.

    Returns:
        A dict with ``rating``, ``subdivision``, ``problem``,
        ``elevation``, ``aspects``, ``valid_from``, ``valid_to``,
        and ``name`` keys.

    """
    rm = bulletin.render_model or {}
    danger = rm.get("danger") or {}
    rating = danger.get("key") or "no_rating"
    subdivision = danger.get("subdivision")

    problem_label: str = ""
    elevation_text: str = ""
    aspects_text: str = ""

    traits = rm.get("traits") or []
    if traits:
        first_trait = traits[0]
        problems = first_trait.get("problems") or []
        if problems:
            first_problem = problems[0]
            ptype = first_problem.get("problem_type", "")
            problem_label = _PROBLEM_LABELS.get(
                ptype, ptype.replace("_", " ").capitalize()
            )
            elevation_text = _format_elevation(first_problem.get("elevation"))
            aspects_text = _format_aspects(first_problem.get("aspects"))

    return {
        "rating": rating,
        "subdivision": subdivision,
        "problem": problem_label,
        "elevation": elevation_text,
        "aspects": aspects_text,
        "valid_from": bulletin.valid_from.isoformat(),
        "valid_to": bulletin.valid_to.isoformat(),
        "name": region_name,
    }


def today_summaries(request: HttpRequest) -> JsonResponse:
    """
    Return per-region danger summaries for today.

    Response shape::

        {
          "CH-4115": {
            "rating": "considerable",
            "subdivision": "plus",
            "problem": "Persistent weak layers",
            "elevation": "above 2200 m",
            "aspects": "all aspects",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_to":   "2026-03-15T17:00:00+00:00",
            "name":       "Martigny – Verbier"
          },
          ...
        }

    Regions with no covering bulletin today are simply absent from the
    response — the map's fill layer falls back to ``no_rating`` colour.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse mapping region_id → summary dict.

    """
    today = timezone.localdate()

    # Batch fetch: every RegionBulletin link whose bulletin touches today.
    # One query, materialised in memory — we then run per-region selection
    # logic (morning-vs-evening rules) on the grouped lists.
    links = (
        RegionBulletin.objects.filter(
            bulletin__valid_from__date__lte=today,
            bulletin__valid_to__date__gte=today,
        )
        .select_related("region", "bulletin")
        .order_by("bulletin__valid_from")
    )

    issues_by_region: dict[str, list[Bulletin]] = {}
    names_by_region: dict[str, str] = {}
    for link in links:
        region_id = link.region.region_id
        issues_by_region.setdefault(region_id, []).append(link.bulletin)
        # Prefer the region_name_at_time from the latest bulletin; falls
        # back to the Region.name via the FK.
        names_by_region[region_id] = link.region_name_at_time or link.region.name

    summaries: dict[str, dict[str, Any]] = {}
    for region_id, issues in issues_by_region.items():
        selected = _select_default_issue(issues, today)
        if selected is None:
            continue
        summaries[region_id] = _summary_for_bulletin(
            selected, names_by_region[region_id]
        )

    return JsonResponse(summaries)


# Compact int encoding for the season-ratings choropleth. Order matches the
# danger scale so the value can also be used directly as a sort key. Promoted
# from SNOW-45's perf spike harness.
_RATING_TO_INT: dict[str, int] = {
    RegionDayRating.Rating.NO_RATING: 0,
    RegionDayRating.Rating.LOW: 1,
    RegionDayRating.Rating.MODERATE: 2,
    RegionDayRating.Rating.CONSIDERABLE: 3,
    RegionDayRating.Rating.HIGH: 4,
    RegionDayRating.Rating.VERY_HIGH: 5,
}


def season_ratings(request: HttpRequest) -> JsonResponse:
    """
    Return the whole-season bundle of per-region danger ratings.

    Response shape::

        {
          "2026-01-15": {"CH-4115": 3, "CH-4116": 2, ...},
          "2026-01-16": {"CH-4115": 4, ...},
          ...
        }

    Each rating is encoded as an int on the danger scale (0–5) via
    ``_RATING_TO_INT`` to keep the payload small — the timelapse and
    scrubber consumers only need the tile colour, not the prose summary.
    Regions with no row for a given date are simply absent from that
    date's inner dict.

    The handler iterates ``.values_list`` to avoid instantiating model
    objects; the JSON encoder still materialises the dict in memory but
    that matches what a real ``WholeSeasonResponse`` would look like on
    the wire.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse mapping ISO date → {region_id: rating_int}.

    """
    rows = (
        RegionDayRating.objects.all()
        .values_list("date", "region__region_id", "max_rating")
        .order_by("date", "region__region_id")
    )
    payload: dict[str, dict[str, int]] = {}
    for date, region_id, rating in rows:
        payload.setdefault(date.isoformat(), {})[region_id] = _RATING_TO_INT[rating]
    return JsonResponse(payload)


def resorts_by_region(request: HttpRequest) -> JsonResponse:
    """
    Return the ``{region_id: [resort_name, ...]}`` lookup.

    Response shape::

        {
          "CH-4115": ["La Chaux", "Verbier"],
          "CH-5221": ["Lenzerheide", "Valbella"],
          ...
        }

    Regions without any linked resorts are omitted. Resort order is
    determined by the ``Resort.Meta.ordering`` (alphabetical by name).

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse mapping region_id → list of resort names.

    """
    # Walk Region → resorts via the reverse FK. One query with
    # prefetch_related; the ``resorts`` relation is ordered alphabetically
    # by Resort.Meta.ordering so the output order is stable.
    result: dict[str, list[str]] = {}
    regions = Region.objects.prefetch_related("resorts").all()
    for region in regions:
        names = [r.name for r in region.resorts.all()]
        if names:
            result[region.region_id] = names
    return JsonResponse(result)


def regions_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of all regions with populated boundaries.

    Each feature has ``properties.id`` (region_id) and ``properties.name``
    alongside the raw boundary geometry. Regions whose ``boundary`` is
    ``None`` are skipped — there is no geometry to draw.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload.

    """
    features: list[dict[str, Any]] = []
    for region in Region.objects.exclude(boundary__isnull=True).iterator():
        features.append(
            {
                "type": "Feature",
                "geometry": region.boundary,
                "properties": {
                    "id": region.region_id,
                    "name": region.name,
                },
            }
        )
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )


def region_summary(request: HttpRequest, region_id: str) -> JsonResponse:
    """
    Return pre-rendered peek + expanded HTML for a region's bulletin.

    Response shape::

        {"peek": "<...>", "expanded": "<...>"}

    Both fragments are server-rendered so the bottom sheet on ``/map/``
    can inject them as opaque HTML and let the existing drag controller
    manage transitions. The expanded fragment composes
    ``public/_rating_block.html`` per render-model trait, which means
    the map sheet and the bulletin page share a single rendering path
    for hazard blocks.

    Accepts an optional ``?d=YYYY-MM-DD`` query parameter to fetch the
    bulletin for a specific past or future date — used by the season
    scrubber on ``/map/`` to refresh the open sheet when the displayed
    date changes. Defaults to today.

    Returns 400 when ``?d=`` is present but unparseable.
    Returns 404 when the region exists but has no bulletin covering the
    requested date.
    Returns 404 when the region_id is unknown.

    Args:
        request: The incoming HTTP request.
        region_id: SLF region identifier (e.g. ``"CH-4115"``).

    Returns:
        A JsonResponse with ``peek`` and ``expanded`` HTML strings, or
        a 400 ``{"error": "bad_date"}`` payload for an unparseable
        ``?d=``, or a 404 ``{"error": "no_bulletin"}`` payload when no
        bulletin covers the target date.

    """
    region = get_object_or_404(Region, region_id__iexact=region_id)
    raw_date = request.GET.get("d")
    if raw_date:
        try:
            target_date = datetime.date.fromisoformat(raw_date)
        except ValueError:
            return JsonResponse({"error": "bad_date"}, status=400)
    else:
        target_date = timezone.localdate()
    bulletin = _select_bulletin_for_date(region, target_date)
    if bulletin is None:
        return JsonResponse({"error": "no_bulletin"}, status=404)

    rm = bulletin.render_model or {}
    bulletin_url = reverse("public:bulletin", args=[region.region_id, region.slug])

    ctx = {
        "region": region,
        "rm": rm,
        "bulletin_url": bulletin_url,
    }
    return JsonResponse(
        {
            "peek": render_to_string("public/_region_peek.html", ctx, request=request),
            "expanded": render_to_string(
                "public/_region_expanded.html", ctx, request=request
            ),
        }
    )


def offline_manifest_map(request: HttpRequest) -> JsonResponse:
    """
    Return the precache manifest for the offline /map/ feature.

    The response is consumed by ``static/js/sw.js`` when the user taps
    "Save offline". It lists every URL the service worker should store in
    the versioned ``map-shell-v1`` cache so the map renders without a
    network connection.

    The list covers:
    * Django-served shell assets (HTML, CSS, JS, favicon).
    * The three existing JSON API endpoints.
    * MapLibre GL JS + CSS from the same CDN version loaded by the template.
    * OpenFreeMap style JSON, TileJSON, sprites, glyphs, and vector/raster
      tiles for the Swiss bounding box at the configured zoom ranges.

    This view makes zero database queries. It does make a single outbound
    HTTP call to OpenFreeMap's TileJSON endpoint to resolve the current
    versioned vector-tile URL template — see ``_fetch_vector_tile_template``
    for why that is necessary and how failures degrade gracefully.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with ``version`` and ``urls`` keys.

    """
    # Resolve OFM's current vector-tile URL template so the precache keys
    # match the versioned URLs MapLibre actually requests at runtime.
    vector_template = _fetch_vector_tile_template()

    urls: list[str] = [
        # Shell page.
        reverse("public:map"),
        # Static assets.
        static("css/output.css"),
        static("css/map.css"),
        static("js/map.js"),
        static("js/offline.js"),
        static("favicon.svg"),
        # JSON API endpoints.
        reverse("api:regions_geojson"),
        reverse("api:today_summaries"),
        reverse("api:resorts_by_region"),
        # MapLibre GL JS + CSS from CDN (version must match the template).
        f"{_MAPLIBRE_CDN}/maplibre-gl.js",
        f"{_MAPLIBRE_CDN}/maplibre-gl.css",
        # OpenFreeMap style and TileJSON.
        f"{_OFM_BASE}/styles/liberty",
        _OFM_VECTOR_TILEJSON,
        # Vector tiles (z5–z10) over the Swiss bounding box.
        *_generate_tile_urls(vector_template, _SWISS_BBOX, _VECTOR_TILE_ZOOM),
        # Natural Earth raster tiles (z5–z6) over the Swiss bounding box.
        *_generate_tile_urls(_OFM_RASTER_TEMPLATE, _SWISS_BBOX, _RASTER_TILE_ZOOM),
        # Sprite sheets (standard + high-DPI).
        f"{_OFM_SPRITE_BASE}/ofm.json",
        f"{_OFM_SPRITE_BASE}/ofm.png",
        f"{_OFM_SPRITE_BASE}/ofm@2x.json",
        f"{_OFM_SPRITE_BASE}/ofm@2x.png",
    ]

    # Glyph PBFs: one URL per (fontstack × range) combination.
    # urllib.parse.quote encodes spaces in fontstack names (e.g. "Noto Sans Regular").
    for fontstack in _GLYPH_FONTSTACKS:
        encoded = urllib.parse.quote(fontstack)
        for glyph_range in _GLYPH_RANGES:
            urls.append(f"{_OFM_BASE}/fonts/{encoded}/{glyph_range}.pbf")

    return JsonResponse({"version": _OFFLINE_MANIFEST_VERSION, "urls": urls})
