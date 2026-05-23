"""
public/api.py — JSON endpoints for the interactive map and share feature.

Lightweight endpoints consumed by ``static/js/map.js`` to render the
Swiss region choropleth and back the per-region tooltip:

* ``/api/ratings/``                        — ``{date_iso: {region_id: rating_int}}``
  filtered by optional ``?d=YYYY-MM-DD`` and ``?country=ch|fr|at|it`` query params.
  Replaces the legacy ``today-summaries`` and ``season-ratings`` endpoints (SNOW-239).
* ``/api/resorts-by-region/``              — ``{region_id: [resort_name, ...]}``.
* ``/api/resorts.geojson``                 — FeatureCollection of geocoded resorts.
* ``/api/regions.geojson``                 — FeatureCollection of L4 region polygons.
* ``/api/major-regions.geojson``           — FeatureCollection of L1 region polygons.
* ``/api/sub-regions.geojson``             — FeatureCollection of L2 region polygons.
* ``/api/region/<region_id>/summary/``     — pre-rendered tooltip HTML for the
  MapLibre Popup anchored to the region's bbox centre; shows the day's danger
  rating chip (``?d=YYYY-MM-DD``-aware), breadcrumb, and resort list.
* ``/api/offline-manifest/map/``           — precache manifest for the offline CTA.

Flag-gated endpoints powering the in-map resort editor (SNOW-74,
``?edit=resorts`` on /map/). Both views check the ``edit_map`` waffle
flag (SNOW-86) and 404 when it is inactive for the request user:

* ``GET  /api/edit/resorts/queue/``                — queue + catalogue payload.
* ``POST /api/edit/resorts/<int:resort_id>/coords/`` — persist clicked lat/lon.

Plain Django ``JsonResponse`` views — no DRF. The choropleth fetches its
three data endpoints in parallel at load time; the per-region summary
endpoint is hit on demand when the user taps a region.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import date
from typing import Any

import waffle
from django.core.cache import cache
from django.db import IntegrityError
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.vary import vary_on_headers
from django_ratelimit.decorators import ratelimit

from bulletins.models import Bulletin, BulletinShare, RegionDayRating
from regions.models import (
    MajorRegion,
    MicroRegion,
    Resort,
    SubRegion,
)

from .views import (
    _resolve_region_for_bulletin,
    _select_bulletin_for_date,
)

# ISO 3166-1 alpha-2 → English country name mapping. Used by the region
# tooltip breadcrumb and the ?country= filter on the GeoJSON endpoints.
COUNTRY_NAMES: dict[str, str] = {
    "CH": "Switzerland",
    "FR": "France",
    "AT": "Austria",
    "IT": "Italy",
}

# Valid values for the mandatory ?country= query param on GeoJSON endpoints.
# Stored as uppercase ISO-2 codes; the query param is accepted case-insensitively.
_VALID_GEOJSON_COUNTRIES: frozenset[str] = frozenset(COUNTRY_NAMES)

# Cache lifetime for static region GeoJSON — region geometry is fixture-backed
# and essentially never changes between deploys. Applied via decorator rather
# than a manual header assignment so Django's cache framework tracks it
# correctly and the session middleware cannot append Vary: Cookie.
_GEOJSON_CACHE_MAX_AGE = 86400

# Cache lifetime for dynamic-but-slow-moving map endpoints (today-summaries,
# resorts-by-region, resorts.geojson). Content only changes when a pipeline
# run lands new bulletins or an operator edits a resort; 5 minutes bounds the
# staleness while letting browsers and edge caches absorb the bulk of repeat
# hits. Pair the decorator with @vary_on_headers("Accept-Encoding") to stop
# SessionMiddleware from appending Vary: Cookie and killing shared caching.
_DYNAMIC_CACHE_MAX_AGE = 300

logger = logging.getLogger(__name__)

# Swiss bounding box (west, south, east, north) in decimal degrees. Used
# by ``_validate_swiss_coords`` for the SNOW-74 resort-edit endpoint;
# the SNOW-9 offline-manifest tile generators that previously also
# consumed this constant were retired in SNOW-79 (PWA shell rewrite).
_SWISS_BBOX: tuple[float, float, float, float] = (5.9, 45.8, 10.5, 47.8)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Compact int encoding for the ratings choropleth. Order matches the
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


def _build_ratings_payload(
    parsed_date: date | None,
    country_param: str,
) -> dict[str, dict[str, int]]:
    """
    Build the ``{date_iso: {region_id: rating_int}}`` payload from the DB.

    Queries ``RegionDayRating`` filtered by the supplied date and/or
    country, encodes each rating via ``_RATING_TO_INT``, and groups by
    ISO date string. Called lazily from ``cache.get_or_set`` in the
    ``ratings`` view so the full query only runs once per cache window.

    Args:
        parsed_date: If set, restrict to rows for this date only.
        country_param: Uppercase ISO-2 country code, e.g. ``"CH"``. Pass
            an empty string to include all countries.

    Returns:
        A dict mapping ISO date string to ``{region_id: rating_int}``.

    """
    qs = RegionDayRating.objects.values_list("date", "region__region_id", "max_rating")
    if parsed_date:
        qs = qs.filter(date=parsed_date)
    if country_param:
        qs = qs.filter(region__subregion__major__country=country_param)
    qs = qs.order_by("date", "region__region_id")
    payload: dict[str, dict[str, int]] = {}
    for row_date, region_id, rating in qs:
        payload.setdefault(row_date.isoformat(), {})[region_id] = _RATING_TO_INT[rating]
    return payload


@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
@vary_on_headers("Accept-Encoding")
def ratings(request: HttpRequest) -> JsonResponse:
    """
    Return a compact ``{date_iso: {region_id: rating_int}}`` ratings bundle.

    Accepts optional query parameters:

    * ``?d=YYYY-MM-DD``          — restrict to a single date (cold-open path).
    * ``?country=ch|fr|at|it``   — restrict to one country (case-insensitive).

    Combining both parameters returns a single-key dict for that date and
    country — the cold-open path uses ``?d=<today>&country=ch`` to fetch
    ~2 KB instead of the full season payload.

    Each rating int maps to the EAWS danger scale: 0=no_rating, 1=low,
    2=moderate, 3=considerable, 4=high, 5=very_high.

    Server-side ``cache.get_or_set`` keyed on ``(country, date)`` keeps DB
    hits to one per cache window. The HTTP ``Cache-Control: public,
    max-age=300`` header is applied by the ``@cache_control`` decorator.

    Errors:
        400 — unknown country code.
        400 — malformed ``?d=`` date string.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse mapping ISO date → {region_id: rating_int}.

    """
    date_param = request.GET.get("d")
    country_param = (request.GET.get("country") or "").upper()

    if country_param and country_param not in _VALID_GEOJSON_COUNTRIES:
        return JsonResponse({"error": "unknown country"}, status=400)

    parsed_date: date | None = None
    if date_param:
        # Enforce the strict YYYY-MM-DD wire format. Python 3.11+
        # accepts "YYYYMMDD" (no separators) via date.fromisoformat(),
        # but we only accept hyphenated ISO dates from callers.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_param):
            return JsonResponse({"error": "malformed date"}, status=400)
        try:
            parsed_date = date.fromisoformat(date_param)
        except ValueError:
            return JsonResponse({"error": "malformed date"}, status=400)

    cache_key = (
        f"ratings:{country_param.lower() or 'all'}:"
        f"{parsed_date.isoformat() if parsed_date else 'season'}"
    )
    # Use a longer TTL for full-season payloads (no single-date
    # column moves intra-day) and the standard 5-minute TTL for
    # single-date responses so today's column updates promptly.
    ttl = 300 if parsed_date else 3600

    payload = cache.get_or_set(
        cache_key,
        lambda: _build_ratings_payload(parsed_date, country_param),
        timeout=ttl,
    )
    return JsonResponse(payload)


@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
@vary_on_headers("Accept-Encoding")
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
    regions = MicroRegion.objects.prefetch_related("resorts").all()
    for region in regions:
        names = [r.name for r in region.resorts.all()]
        if names:
            result[region.region_id] = names
    return JsonResponse(result)


@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
@vary_on_headers("Accept-Encoding")
def resorts_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of all geocoded resorts.

    Each feature is a Point with GeoJSON-ordered ``coordinates: [lon, lat]``
    (RFC 7946) and properties ``id``, ``name``, ``region_id``,
    ``needs_review``. Resorts missing latitude or longitude are skipped.

    Always available (not DEBUG-gated) — the public map will use this
    layer once enough resorts are placed to be worth showing.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload.

    """
    features: list[dict[str, Any]] = []
    for resort in (
        Resort.objects.geocoded().select_related("region").order_by("name").iterator()
    ):
        # GeoJSON ordering: [longitude, latitude] per RFC 7946.
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [resort.longitude, resort.latitude],
                },
                "properties": {
                    "id": resort.pk,
                    "name": resort.name,
                    "region_id": resort.region.region_id,
                    "needs_review": resort.needs_review,
                },
            }
        )
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )


@cache_control(public=True, max_age=_GEOJSON_CACHE_MAX_AGE)
@vary_on_headers("Accept-Encoding")
def regions_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of L4 region polygons for a single country.

    Requires a ``?country=ch|fr|at|it`` query parameter (case-insensitive).
    Returns 400 on an unrecognised value. Each feature carries
    ``properties.id``, ``properties.name``, and ``properties.country``.
    Regions without a boundary are skipped.

    The ``@cache_control(public=True, max_age=86400)`` + ``@vary_on_headers``
    pair prevents Django's ``SessionMiddleware`` from appending
    ``Vary: Cookie`` on the response.  Region geometry is fixture-backed and
    anonymous — it is safe to cache publicly for 24 hours.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or 400 on bad input.

    """
    country_param = request.GET.get("country", "").upper()
    if country_param not in _VALID_GEOJSON_COUNTRIES:
        return JsonResponse(
            {"error": "invalid_country", "valid": sorted(_VALID_GEOJSON_COUNTRIES)},
            status=400,
        )

    features: list[dict[str, Any]] = []
    qs = (
        MicroRegion.objects.filter(
            subregion__major__country=country_param,
            subregion__major__display_on_map=True,
            boundary__isnull=False,
        )
        .select_related("subregion__major")
        .iterator()
    )
    for region in qs:
        # SNOW-188: surface the parent L2 sub-region name on each L4
        # feature so the client search dropdown can show the broader
        # area (e.g. "Lower Valais") alongside the resort list. AT/IT
        # fixtures use the prefix as a placeholder name; emit blank in
        # that case so the client can suppress a redundant code.
        sub = region.subregion
        subregion_name = (
            sub.name_en if sub.name_en and sub.name_en != sub.prefix else ""
        )
        features.append(
            {
                "type": "Feature",
                "geometry": region.boundary,
                "properties": {
                    "id": region.region_id,
                    "name": region.name,
                    "country": sub.major.country,
                    "subregion_name": subregion_name,
                },
            }
        )
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )


@cache_control(public=True, max_age=_GEOJSON_CACHE_MAX_AGE)
@vary_on_headers("Accept-Encoding")
def major_regions_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of L1 EAWS major regions for a single country.

    Requires a ``?country=ch|fr|at|it`` query parameter (case-insensitive).
    Returns 400 on an unrecognised value. Each feature carries
    ``properties.prefix``, ``properties.name_en``, and ``properties.country``.
    Entries without a boundary are skipped.

    The ``@cache_control`` + ``@vary_on_headers`` pair prevents Django's
    ``SessionMiddleware`` from appending ``Vary: Cookie``.  See
    ``regions_geojson`` for the full rationale.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or 400 on bad input.

    """
    country_param = request.GET.get("country", "").upper()
    if country_param not in _VALID_GEOJSON_COUNTRIES:
        return JsonResponse(
            {"error": "invalid_country", "valid": sorted(_VALID_GEOJSON_COUNTRIES)},
            status=400,
        )

    features: list[dict[str, Any]] = []
    for major in MajorRegion.objects.filter(
        country=country_param, boundary__isnull=False, display_on_map=True
    ).iterator():
        features.append(
            {
                "type": "Feature",
                "geometry": major.boundary,
                "properties": {
                    "prefix": major.prefix,
                    "name_en": major.name_en,
                    "country": major.country,
                },
            }
        )
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )


@cache_control(public=True, max_age=_GEOJSON_CACHE_MAX_AGE)
@vary_on_headers("Accept-Encoding")
def sub_regions_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of L2 EAWS sub-regions for a single country.

    Requires a ``?country=ch|fr|at|it`` query parameter (case-insensitive).
    Returns 400 on an unrecognised value. Each feature carries
    ``properties.prefix``, ``properties.name_en``, and ``properties.country``.
    Entries without a boundary are skipped.

    The ``@cache_control`` + ``@vary_on_headers`` pair prevents Django's
    ``SessionMiddleware`` from appending ``Vary: Cookie``.  See
    ``regions_geojson`` for the full rationale.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or 400 on bad input.

    """
    country_param = request.GET.get("country", "").upper()
    if country_param not in _VALID_GEOJSON_COUNTRIES:
        return JsonResponse(
            {"error": "invalid_country", "valid": sorted(_VALID_GEOJSON_COUNTRIES)},
            status=400,
        )

    features: list[dict[str, Any]] = []
    qs = (
        SubRegion.objects.filter(
            major__country=country_param,
            major__display_on_map=True,
            boundary__isnull=False,
        )
        .select_related("major")
        .iterator()
    )
    for sub in qs:
        features.append(
            {
                "type": "Feature",
                "geometry": sub.boundary,
                "properties": {
                    "prefix": sub.prefix,
                    "name_en": sub.name_en,
                    "country": sub.major.country,
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
    Return pre-rendered tooltip HTML for a region's MapLibre Popup.

    Response shape::

        {"html": "<...>", "level": "considerable"}

    The ``html`` key is a server-rendered snippet injected into a
    ``maplibregl.Popup`` anchored to the region's bbox centre on ``/map/``.
    Content includes the day's danger-rating chip and the geographic breadcrumb.
    The ``level`` key exposes the rating string so the JS can stamp a
    ``data-level`` attribute on the popup container to drive the border colour.

    Query parameters:
        d (optional): ISO date string ``YYYY-MM-DD``. When supplied the chip
            reflects that day's ``RegionDayRating`` and the bulletin URL is
            dated. Returns 400 with ``{"error": "bad_date"}`` on a malformed
            value. Defaults to today when absent.

    Returns 404 when the region_id is unknown.

    Args:
        request: The incoming HTTP request.
        region_id: SLF region identifier (e.g. ``"CH-4115"``).

    Returns:
        A JsonResponse with a single ``html`` key containing the tooltip markup.

    """
    raw_date = request.GET.get("d")
    if raw_date is not None:
        try:
            target_date = date.fromisoformat(raw_date)
        except ValueError:
            return JsonResponse({"error": "bad_date"}, status=400)
    else:
        target_date = timezone.localdate()

    region = get_object_or_404(
        MicroRegion.objects.select_related("subregion__major"),
        region_id__iexact=region_id,
    )

    day_rating = RegionDayRating.objects.filter(region=region, date=target_date).first()

    bulletin_url = region.get_absolute_url(None if raw_date is None else target_date)

    country_name = COUNTRY_NAMES.get(
        region.subregion.major.country, region.subregion.major.country
    )

    level = day_rating.max_rating if day_rating else "no_rating"

    return JsonResponse(
        {
            "html": render_to_string(
                "public/_region_tooltip.html",
                {
                    "region": region,
                    "day_rating": day_rating,
                    "bulletin_url": bulletin_url,
                    "country_name": country_name,
                    "target_date": target_date,
                },
                request=request,
            ),
            "level": level,
        }
    )


# ---------------------------------------------------------------------------
# Edit-resorts mode (SNOW-74) — flag-gated on ``edit_map`` (SNOW-86)
# ---------------------------------------------------------------------------


def _require_edit_map_flag(request: HttpRequest) -> None:
    """Raise Http404 unless the ``edit_map`` waffle flag is active.

    Mirrors the view-level guard ``map_view`` applies before rendering
    the editor panel: an unauthorised caller hitting the API directly
    must see the same 404 the URL conf used to give them when the
    feature was DEBUG-only. Flag is seeded with ``superusers=True`` by
    migration ``pipeline/migrations/0017_seed_edit_map_flag.py``;
    extend / disable via ``/admin/waffle/flag/edit_map/``.
    """
    if not waffle.flag_is_active(request, "edit_map"):
        raise Http404("edit_map flag is inactive for this request.")


def _validate_swiss_coords(lat: float, lon: float) -> str | None:
    """
    Return an error message if (lat, lon) is outside ``_SWISS_BBOX``.

    Returns ``None`` for valid coordinates. Boundary values are accepted.
    """
    west, south, east, north = _SWISS_BBOX
    if not (south <= lat <= north):
        return f"Latitude {lat} outside Swiss bbox {south}–{north}"
    if not (west <= lon <= east):
        return f"Longitude {lon} outside Swiss bbox {west}–{east}"
    return None


def _rings_from_geometry(
    geometry: dict[str, Any],
) -> list[list[list[float]]]:
    """Return all rings from a GeoJSON Polygon or MultiPolygon geometry.

    For ``Polygon`` the rings are the top-level ``coordinates`` list
    (outer ring + any holes).  For ``MultiPolygon`` the rings are the
    concatenation of each member polygon's ring list.

    Args:
        geometry: GeoJSON Polygon or MultiPolygon geometry dict.

    Returns:
        List of rings, each ring being a list of ``[lon, lat]`` vertices.

    """
    geo_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geo_type == "Polygon":
        return list(coordinates)
    if geo_type == "MultiPolygon":
        rings: list[list[list[float]]] = []
        for member in coordinates:
            rings.extend(member)
        return rings
    return []


def _point_in_polygon(lat: float, lon: float, polygon: dict[str, Any]) -> bool:
    """
    Return True if (lat, lon) lies inside a GeoJSON Polygon or MultiPolygon.

    Implements the standard ray-casting algorithm: cast a horizontal ray
    east of the point and count how many polygon edges it crosses. Odd
    crossings = inside. Looping over every ring (outer + any holes) at
    once correctly handles holes: a point inside the outer ring but
    inside a hole gets an even total and is reported as outside, which
    is the right answer.

    For ``MultiPolygon`` the rings from all member polygons are checked
    together. Because each member polygon is topologically independent
    (non-overlapping outer rings), flipping parity across member-polygon
    rings still gives the correct result: a point outside all members
    accumulates an even count; a point inside exactly one member's outer
    ring (and outside its holes) accumulates an odd count.

    Polygon coordinates are stored in GeoJSON order as ``[lon, lat]``;
    we rename to ``x, y`` here so the algorithm reads naturally. Edge
    cases: a point exactly on a horizontal edge can flip either way
    depending on tie-breaking, but we don't need pixel-perfect boundary
    behaviour — the resort save-coords path uses this to pick a
    *containing* region for an admin-placed pin, and the operator can
    always nudge the pin if it lands ambiguously.

    Args:
        lat: Latitude of the test point (WGS 84).
        lon: Longitude of the test point (WGS 84).
        polygon: GeoJSON Polygon or MultiPolygon geometry as stored in
            ``Region.boundary``.

    Returns:
        True if the point lies inside the polygon (or any member of the
        MultiPolygon).

    """
    x, y = lon, lat
    inside = False
    for ring in _rings_from_geometry(polygon):
        # Iterate edges of this ring as (i-1, i) vertex pairs.
        n = len(ring)
        if n < 3:
            continue
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            # Standard ray-cast: count an edge crossing if the test
            # point's y lies between the edge endpoints' y, AND the
            # x of the edge at that y is to the right of the test
            # point. The strict-inequality on yi/yj avoids
            # double-counting at shared vertices.
            if (yi > y) != (yj > y):
                x_at_y = (xj - xi) * (y - yi) / (yj - yi) + xi
                if x < x_at_y:
                    inside = not inside
            j = i
    return inside


def _all_coords_from_geometry(
    polygon: dict[str, Any],
) -> list[tuple[float, float]]:
    """Return every ``(lon, lat)`` coordinate pair across all rings of a geometry.

    Flattens all rings from both ``Polygon`` and ``MultiPolygon`` inputs
    via :func:`_rings_from_geometry` so the bbox computation has a single
    flat list to min/max over.

    Args:
        polygon: GeoJSON Polygon or MultiPolygon geometry dict.

    Returns:
        Flat list of ``(lon, lat)`` pairs; empty when geometry has no rings.

    """
    return [(x, y) for ring in _rings_from_geometry(polygon) for x, y in ring]


def _bbox_of_polygon(
    polygon: dict[str, Any],
) -> tuple[float, float, float, float] | None:
    """Return ``(west, south, east, north)`` of a GeoJSON Polygon or MultiPolygon.

    For ``Polygon`` only the outer ring is used (as before). For
    ``MultiPolygon`` all member polygons' outer rings are considered so
    the returned bbox encloses the full extent.

    Returns ``None`` if the geometry has no usable coordinates. Used by
    :func:`_region_for_point` as a cheap pre-filter so the full
    ray-cast only runs on regions whose bbox could plausibly contain
    the point.
    """
    coords = _all_coords_from_geometry(polygon)
    if not coords:
        return None
    lons = [x for x, _ in coords]
    lats = [y for _, y in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def _region_for_point(lat: float, lon: float) -> MicroRegion | None:
    """Return the MicroRegion whose boundary polygon contains (lat, lon).

    Returns ``None`` if the point falls outside every region.

    Iterates ``MicroRegion.objects.exclude(boundary__isnull=True)`` and runs
    a bbox pre-filter followed by a full ray-cast. Used by the
    edit-resorts save endpoint to auto-correct a resort's parent-region
    FK when the saved pin lands outside the FK's polygon — some
    imported resorts have wrong region tags (e.g. Villars-sur-Ollon
    seeded as CH-1113 but actually in CH-1114), and the operator
    placing a pin is the most authoritative signal we'll get.

    The lookup is O(regions × ring vertices) Python — ~150 regions
    each with a few hundred vertices means single-digit ms per call,
    fine for an interactive admin tool.

    Args:
        lat: Latitude (WGS 84).
        lon: Longitude (WGS 84).

    Returns:
        The first matching MicroRegion, or ``None``. "First" is in the
        MicroRegion default ordering — ties (a point on a shared boundary)
        are unlikely in practice and not worth disambiguating.

    """
    for region in MicroRegion.objects.exclude(boundary__isnull=True).iterator():
        # The ``exclude(boundary__isnull=True)`` filter already drops
        # null rows; the explicit guard here is for mypy's benefit
        # (``MicroRegion.boundary`` is typed as Optional) and as defence in
        # depth against a future schema/migration that lets nulls back
        # in. ``assert`` would be the pythonic check but ruff's S101
        # rejects assertions outside test code.
        boundary = region.boundary
        if boundary is None:
            continue
        bbox = _bbox_of_polygon(boundary)
        if bbox is None:
            continue
        w, s, e, n = bbox
        if not (w <= lon <= e and s <= lat <= n):
            continue
        if _point_in_polygon(lat, lon, boundary):
            return region
    return None


@require_GET
def edit_resorts_queue(request: HttpRequest) -> JsonResponse:
    """Return the flat resort catalogue + L2 labels (flag-gated).

    Response shape::

        {
          "all_resorts": [{catalogue-entry}, ...],
          "sub_regions": {"CH-41": "Lower Valais", ...}
        }

    Catalogue order is L2 prefix → L4 region_id → name. Sorting by
    ``region__region_id`` groups entries by L2 (the L2 prefix is a
    prefix of the full region_id) so the JS can detect L2 transitions
    just by comparing the first 5 chars of consecutive rows'
    ``region_id`` and emit a section header labelled with the L2 name
    from ``sub_regions``.

    Each catalogue entry carries the fields the side panel needs to
    render a row and (on click) a full target readout: ``id``,
    ``name``, ``region_id``, ``region_name``, ``canton``, ``latitude``,
    ``longitude``, ``has_coords``, ``needs_review``.

    ``sub_regions`` maps L2 prefixes (e.g. ``"CH-41"``) to a display
    label — ``name_en`` when SLF publishes one, otherwise ``name_native``.
    L1 grouping was tried first (SNOW-85 addendum 3) but L2 is a more
    useful grouping for the operator: ~25 L2 sections of ~5–10
    resorts each scans better than ~9 L1 sections of ~10–30.

    The endpoint name and URL are kept from SNOW-74 (``edit_resorts_queue``,
    ``/api/edit/resorts/queue/``) for minimal-diff reasons even though
    the SNOW-85 manual workflow no longer surfaces a "queue" of unset
    rows. Renaming the URL would require a coordinated panel-template
    + JS update for no behavioural benefit.

    Returns 404 when the ``edit_map`` waffle flag is inactive for the
    request user (SNOW-86; seeded with ``superusers=True``).
    """
    _require_edit_map_flag(request)
    all_resorts = [
        {
            "id": pk,
            "name": name,
            "region_id": region_id,
            "region_name": region_name,
            "canton": canton,
            "latitude": lat,
            "longitude": lon,
            "has_coords": lat is not None and lon is not None,
            "needs_review": needs_review,
        }
        for pk, name, region_id, region_name, canton, lat, lon, needs_review in (
            Resort.objects.select_related("region")
            # L2 (e.g. "CH-41") is a prefix of L4 (e.g. "CH-4115"), so
            # sorting on region_id alone groups rows by L2 in the right
            # order. ``name`` breaks ties within a region.
            .order_by("region__region_id", "name")
            .values_list(
                "pk",
                "name",
                "region__region_id",
                "region__name",
                "canton",
                "latitude",
                "longitude",
                "needs_review",
            )
        )
    ]
    # Prefer the English name when SLF publishes one (some L2 entries
    # have ``name_en=""``); fall back to the locally-dominant name so
    # the section header is never blank.
    sub_regions = {
        prefix: (name_en or name_native)
        for prefix, name_en, name_native in SubRegion.objects.values_list(
            "prefix",
            "name_en",
            "name_native",
        )
    }
    return JsonResponse(
        {
            "all_resorts": all_resorts,
            "sub_regions": sub_regions,
        }
    )


@require_POST
def edit_resort_save_coords(request: HttpRequest, resort_id: int) -> JsonResponse:
    """Persist clicked latitude/longitude for a resort (flag-gated).

    Request body (JSON)::

        {"latitude": <float>, "longitude": <float>}

    On success, sets ``geocode_source="manual"``,
    ``geocode_confidence=1.0``, ``geocoded_at=now()``, and clears
    ``needs_review``. Auto-rebinds ``resort.region`` if the saved
    point lands inside a different region's polygon (SNOW-85). Returns
    the updated resort fields including the (possibly re-bound)
    ``region_id`` and ``region_name`` so the panel can patch its
    in-memory catalogue without a follow-up GET.

    Errors:
        404 — ``edit_map`` waffle flag inactive, or unknown ``resort_id``.
        400 — invalid JSON; missing or non-float lat/lon; coordinates
              outside the Swiss bounding box.
    """
    _require_edit_map_flag(request)

    try:
        payload = json.loads(request.body or b"")
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_json"}, status=400)

    raw_lat = payload.get("latitude")
    raw_lon = payload.get("longitude")
    if raw_lat is None or raw_lon is None:
        return JsonResponse(
            {
                "error": "invalid_coords",
                "detail": "latitude and longitude are required",
            },
            status=400,
        )
    try:
        lat = float(raw_lat)
        lon = float(raw_lon)
    except (TypeError, ValueError):
        return JsonResponse(
            {
                "error": "invalid_coords",
                "detail": "latitude and longitude must be numbers",
            },
            status=400,
        )

    bbox_error = _validate_swiss_coords(lat, lon)
    if bbox_error:
        return JsonResponse(
            {"error": "out_of_bounds", "detail": bbox_error},
            status=400,
        )

    resort = get_object_or_404(
        Resort.objects.select_related("region"),
        pk=resort_id,
    )

    resort.latitude = lat
    resort.longitude = lon
    resort.geocode_source = "manual"
    resort.geocode_confidence = 1.0
    resort.geocoded_at = timezone.now()
    resort.needs_review = False
    update_fields = [
        "latitude",
        "longitude",
        "geocode_source",
        "geocode_confidence",
        "geocoded_at",
        "needs_review",
        "updated_at",
    ]

    # Auto-rebind the parent region from the clicked location. Some
    # imported resorts have wrong region tags (e.g. Villars-sur-Ollon
    # and Gryon were seeded as CH-1113 but sit in CH-1114) and the
    # operator placing a pin is the most authoritative signal we'll
    # get. If the saved point is outside every region polygon (rare,
    # would need to be in a no-coverage gap), leave the FK alone
    # rather than nulling it. We log when a rebind fires so a
    # subsequent ``dump_resorts_fixture --commit`` makes the data
    # change visible in the diff.
    containing = _region_for_point(lat, lon)
    if containing is not None and containing.pk != resort.region_id:
        logger.info(
            "edit_resort_save_coords: rebinding %s from %s to %s",
            resort.name,
            resort.region.region_id,
            containing.region_id,
        )
        resort.region = containing
        update_fields.append("region")

    resort.save(update_fields=update_fields)

    return JsonResponse(
        {
            "id": resort.pk,
            "name": resort.name,
            "region_id": resort.region.region_id,
            "region_name": resort.region.name,
            "latitude": resort.latitude,
            "longitude": resort.longitude,
            "geocode_source": resort.geocode_source,
            "geocode_confidence": resort.geocode_confidence,
            "geocoded_at": resort.geocoded_at.isoformat()
            if resort.geocoded_at
            else None,
            "needs_review": resort.needs_review,
        }
    )


# SNOW-79 retired the ``offline_manifest_map`` endpoint. The PWA shell
# service worker now caches static assets at runtime via
# stale-while-revalidate, so there is no precache manifest for an SW to
# fetch. See ``static/js/sw.js`` and ``docs/offline-map.md``.


# ---------------------------------------------------------------------------
# Share-create endpoint (SNOW-217)
# ---------------------------------------------------------------------------

# Maximum number of token-generation retries before giving up on collision.
_SHARE_TOKEN_MAX_RETRIES = 2


def _parse_share_request(
    request: HttpRequest,
) -> tuple[JsonResponse, None, None] | tuple[None, str, date]:
    """Parse and validate the share-create request body.

    Returns either ``(error_response, None, None)`` on validation failure,
    or ``(None, region_id, target_date)`` on success.
    """
    try:
        body = json.loads(request.body or b"")
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400), None, None

    if not isinstance(body, dict):
        return JsonResponse({"error": "invalid_json"}, status=400), None, None

    region_id = body.get("region_id")
    date_str = body.get("date")

    if not region_id or not date_str:
        return (
            JsonResponse(
                {
                    "error": "missing_fields",
                    "detail": "region_id and date are required",
                },
                status=400,
            ),
            None,
            None,
        )

    try:
        target_date = date.fromisoformat(str(date_str))
    except (ValueError, TypeError):
        return (
            JsonResponse(
                {"error": "invalid_date", "detail": "date must be YYYY-MM-DD"},
                status=400,
            ),
            None,
            None,
        )

    return None, str(region_id), target_date


def _create_share_with_retry(
    bulletin: Bulletin,
    region: MicroRegion,
    target_date: date,
) -> BulletinShare | None:
    """Create a BulletinShare with a unique token, retrying on collision.

    Returns the created share, or None if all retries exhausted.
    """
    for _attempt in range(_SHARE_TOKEN_MAX_RETRIES):
        token = secrets.token_urlsafe(8)
        try:
            return BulletinShare.objects.create(
                token=token,
                bulletin=bulletin,
                region=region,
                target_date=target_date,
            )
        except IntegrityError:
            if _attempt == _SHARE_TOKEN_MAX_RETRIES - 1:
                logger.error(
                    "share_create: token collision after %d retries",
                    _SHARE_TOKEN_MAX_RETRIES,
                )
            else:
                logger.warning("share_create: token collision, retrying")
    return None


@require_POST
@ratelimit(key="ip", rate="20/m", block=False)
def share_create(request: HttpRequest) -> JsonResponse:
    """Create a tokenised share link for a bulletin page.

    Request body (JSON)::

        {"region_id": "ch-4222", "date": "2026-04-08"}

    Response (200)::

        {"url": "https://snowdesk.app/s/<token>/"}

    Errors:
        400 — missing/malformed JSON, missing ``region_id`` or ``date``
              field, malformed date string.
        404 — unknown ``region_id`` or no bulletin covers the given date.
        429 — rate limit exceeded (> 20 requests/min per IP).

    Args:
        request: The incoming HTTP request (must be POST).

    Returns:
        A JsonResponse with ``{"url": "..."}`` on success.

    """
    if getattr(request, "limited", False):
        return JsonResponse({"error": "rate_limit_exceeded"}, status=429)

    error_response, region_id, target_date = _parse_share_request(request)
    if error_response is not None:
        return error_response

    try:
        region = _resolve_region_for_bulletin(region_id)  # type: ignore[arg-type]
    except Http404:
        return JsonResponse({"error": "region_not_found"}, status=404)

    bulletin = _select_bulletin_for_date(region, target_date)  # type: ignore[arg-type]
    if bulletin is None:
        return JsonResponse({"error": "bulletin_not_found"}, status=404)

    share = _create_share_with_retry(bulletin, region, target_date)  # type: ignore[arg-type]
    if share is None:
        return JsonResponse({"error": "token_collision"}, status=500)

    url = request.build_absolute_uri(
        reverse("public:share_redirect", args=[share.token])
    )
    return JsonResponse({"url": url})
