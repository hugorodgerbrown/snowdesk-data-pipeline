"""
public/api.py — JSON endpoints for the interactive map.

Lightweight endpoints consumed by ``static/js/map.js`` to render the
Swiss region choropleth and back the per-region tooltip:

* ``/api/today-summaries/``                — per-region danger summary for today.
* ``/api/season-ratings/``                 — ``{date: {region_id: rating_int}}``
  for the entire stored dataset (consumed by the timelapse debug button and,
  later, the season scrubber).
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
from datetime import date
from typing import Any

import waffle
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.vary import vary_on_headers

from bulletins.models import Bulletin, RegionBulletin, RegionDayRating
from regions.models import (
    MajorRegion,
    MicroRegion,
    Resort,
    SubRegion,
)

from .views import (
    _PROBLEM_LABELS,
    _select_default_issue,
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

logger = logging.getLogger(__name__)

# Swiss bounding box (west, south, east, north) in decimal degrees. Used
# by ``_validate_swiss_coords`` for the SNOW-74 resort-edit endpoint;
# the SNOW-9 offline-manifest tile generators that previously also
# consumed this constant were retired in SNOW-79 (PWA shell rewrite).
_SWISS_BBOX: tuple[float, float, float, float] = (5.9, 45.8, 10.5, 47.8)


# ---------------------------------------------------------------------------
# Helpers
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
        # Always use the EAWS canonical name from MicroRegion.
        # ``RegionBulletin.region_name_at_time`` carries the per-bulletin
        # label SLF publishes alongside each ``regionID`` — those labels
        # are not the EAWS canonical name (SLF labels CH-2133 "Stoos"
        # whereas the EAWS reference calls it "Küssnacht - Arth"), so
        # they produced visibly-wrong region labels on the map and
        # bulletin page. The field is kept on the model as an
        # ingestion-time audit trail but is no longer used for display.
        names_by_region[region_id] = link.region.name

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
    for row_date, region_id, rating in rows:
        payload.setdefault(row_date.isoformat(), {})[region_id] = _RATING_TO_INT[rating]
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
    regions = MicroRegion.objects.prefetch_related("resorts").all()
    for region in regions:
        names = [r.name for r in region.resorts.all()]
        if names:
            result[region.region_id] = names
    return JsonResponse(result)


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
            boundary__isnull=False,
        )
        .select_related("subregion__major")
        .iterator()
    )
    for region in qs:
        features.append(
            {
                "type": "Feature",
                "geometry": region.boundary,
                "properties": {
                    "id": region.region_id,
                    "name": region.name,
                    "country": region.subregion.major.country,
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
        country=country_param, boundary__isnull=False
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


def _point_in_polygon(lat: float, lon: float, polygon: dict[str, Any]) -> bool:
    """
    Return True if (lat, lon) lies inside a GeoJSON Polygon geometry.

    Implements the standard ray-casting algorithm: cast a horizontal ray
    east of the point and count how many polygon edges it crosses. Odd
    crossings = inside. Looping over every ring (outer + any holes) at
    once correctly handles holes: a point inside the outer ring but
    inside a hole gets an even total and is reported as outside, which
    is the right answer.

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
        polygon: GeoJSON Polygon geometry as stored in
            ``Region.boundary`` (``{"type": "Polygon", "coordinates":
            [[[lon, lat], ...], ...]}``). Behaviour for non-Polygon
            geometries is undefined — callers must pre-filter.

    Returns:
        True if the point lies inside the polygon.

    """
    x, y = lon, lat
    inside = False
    for ring in polygon.get("coordinates", []):
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


def _bbox_of_polygon(
    polygon: dict[str, Any],
) -> tuple[float, float, float, float] | None:
    """Return ``(west, south, east, north)`` of a GeoJSON Polygon's outer ring.

    Returns ``None`` if the polygon has no usable ring. Used by
    :func:`_region_for_point` as a cheap pre-filter so the full
    ray-cast only runs on regions whose bbox could plausibly contain
    the point.
    """
    rings = polygon.get("coordinates") or []
    if not rings or not rings[0]:
        return None
    w = s = float("inf")
    e = n = float("-inf")
    for x, y in rings[0]:
        if x < w:
            w = x
        if x > e:
            e = x
        if y < s:
            s = y
        if y > n:
            n = y
    return (w, s, e, n)


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
