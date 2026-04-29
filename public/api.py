"""
public/api.py — JSON endpoints for the interactive map.

Lightweight endpoints consumed by ``static/js/map.js`` to render the
Swiss region choropleth and back the per-region bottom sheet:

* ``/api/today-summaries/``                — per-region danger summary for today.
* ``/api/season-ratings/``                 — ``{date: {region_id: rating_int}}``
  for the entire stored dataset (consumed by the timelapse debug button and,
  later, the season scrubber).
* ``/api/resorts-by-region/``              — ``{region_id: [resort_name, ...]}``.
* ``/api/resorts.geojson``                 — FeatureCollection of geocoded resorts.
* ``/api/regions.geojson``                 — FeatureCollection of L4 region polygons.
* ``/api/major-regions.geojson``           — FeatureCollection of L1 region polygons.
* ``/api/sub-regions.geojson``             — FeatureCollection of L2 region polygons.
* ``/api/region/<region_id>/summary/``     — pre-rendered peek + expanded HTML
  for the region's current bulletin (consumed by the bottom sheet).
* ``/api/offline-manifest/map/``           — precache manifest for the offline CTA.

DEBUG-only endpoints powering the in-map resort editor (SNOW-74,
``?edit=resorts`` on /map/):

* ``GET  /api/edit/resorts/queue/``                — queue + catalogue payload.
* ``POST /api/edit/resorts/<int:resort_id>/coords/`` — persist clicked lat/lon.

Plain Django ``JsonResponse`` views — no DRF. The choropleth fetches its
three data endpoints in parallel at load time; the per-region summary
endpoint is hit on demand when the user taps a region.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from pipeline.models import (
    Bulletin,
    EawsMajorRegion,
    EawsSubRegion,
    Region,
    RegionBulletin,
    RegionDayRating,
    Resort,
)

from .views import (
    _PROBLEM_LABELS,
    _select_bulletin_for_date,
    _select_default_issue,
    enrich_render_model,
)

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


def major_regions_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of L1 EAWS major regions with boundaries.

    Each feature carries ``properties.prefix`` (e.g. ``CH-4``) and
    ``properties.name_en`` alongside the boundary geometry computed by
    ``refresh_eaws_fixtures`` from the union of L4 children. Entries
    without a boundary are skipped.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload.

    """
    features: list[dict[str, Any]] = []
    for major in EawsMajorRegion.objects.exclude(boundary__isnull=True).iterator():
        features.append(
            {
                "type": "Feature",
                "geometry": major.boundary,
                "properties": {
                    "prefix": major.prefix,
                    "name_en": major.name_en,
                },
            }
        )
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )


def sub_regions_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of L2 EAWS sub-regions with boundaries.

    Same shape as :func:`major_regions_geojson` — properties expose
    ``prefix`` (e.g. ``CH-41``) and ``name_en``.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload.

    """
    features: list[dict[str, Any]] = []
    for sub in EawsSubRegion.objects.exclude(boundary__isnull=True).iterator():
        features.append(
            {
                "type": "Feature",
                "geometry": sub.boundary,
                "properties": {
                    "prefix": sub.prefix,
                    "name_en": sub.name_en,
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
    for hazard blocks. The render model is fed through
    :func:`public.views.enrich_render_model` first so the partial sees
    the same presentation-ready shape (labels, ``ElevationBounds``,
    period labels) it gets on the bulletin page.

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

    # _rating_block.html requires presentation-ready fields (label,
    # time_period_label, ElevationBounds); without enrichment those rows
    # silently disappear via the partial's {% if %} guards.
    rm = enrich_render_model(bulletin.render_model or {})
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


# ---------------------------------------------------------------------------
# Edit-resorts mode (SNOW-74) — DEBUG-only
# ---------------------------------------------------------------------------


def _require_debug() -> None:
    """Raise Http404 unless ``settings.DEBUG`` is on."""
    if not settings.DEBUG:
        raise Http404("Edit mode is only available when DEBUG=True.")


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


def _serialise_queue_entry(resort: Resort) -> dict[str, Any]:
    """Serialise a Resort to the queue payload shape."""
    return {
        "id": resort.pk,
        "name": resort.name,
        "name_alt": resort.name_alt,
        "region_id": resort.region.region_id,
        "region_name": resort.region.name,
        "canton": resort.canton,
        "latitude": resort.latitude,
        "longitude": resort.longitude,
        "needs_review": resort.needs_review,
    }


def _next_queue_entry(skip_pk: int) -> dict[str, Any] | None:
    """
    Return the next queue entry after ``skip_pk``, or ``None`` if empty.

    Reads the queue afresh — the caller has just saved a row that may
    or may not still match ``needs_geocoding()`` (it shouldn't, but the
    filter is the source of truth).
    """
    nxt = (
        Resort.objects.needs_geocoding()
        .exclude(pk=skip_pk)
        .select_related("region")
        .order_by("region__region_id", "name")
        .first()
    )
    if nxt is None:
        return None
    return _serialise_queue_entry(nxt)


@require_GET
def edit_resorts_queue(request: HttpRequest) -> JsonResponse:
    """
    Return the resort-edit queue + flat catalogue (DEBUG-only).

    Response shape::

        {
          "queue":       [{queue-entry}, ...],     # needs_geocoding()
          "all_resorts": [{catalogue-entry}, ...]  # everything, lightweight
        }

    Queue order: ``region__region_id ASC, name ASC`` — groups resorts by
    their parent region so the operator can sweep through one geographic
    area at a time. The L1 prefix (first 4 chars, e.g. ``CH-4``) is the
    natural break between sections in the panel UI.

    Returns 404 when ``settings.DEBUG`` is off.
    """
    _require_debug()
    queue = [
        _serialise_queue_entry(r)
        for r in (
            Resort.objects.needs_geocoding()
            .select_related("region")
            .order_by("region__region_id", "name")
        )
    ]
    all_resorts = [
        {
            "id": pk,
            "name": name,
            "region_id": region_id,
            "has_coords": lat is not None and lon is not None,
            "needs_review": needs_review,
        }
        for pk, name, region_id, lat, lon, needs_review in (
            Resort.objects.select_related("region")
            .order_by("name")
            .values_list(
                "pk",
                "name",
                "region__region_id",
                "latitude",
                "longitude",
                "needs_review",
            )
        )
    ]
    return JsonResponse({"queue": queue, "all_resorts": all_resorts})


@require_POST
def edit_resort_save_coords(request: HttpRequest, resort_id: int) -> JsonResponse:
    """
    Persist clicked latitude/longitude for a resort (DEBUG-only).

    Request body (JSON)::

        {"latitude": <float>, "longitude": <float>}

    On success, sets ``geocode_source="manual"``,
    ``geocode_confidence=1.0``, ``geocoded_at=now()``, and clears
    ``needs_review``. Returns the updated resort plus the next queue
    entry so the panel can advance without a follow-up GET.

    Errors:
        404 — DEBUG=False, or unknown ``resort_id``.
        400 — invalid JSON; missing or non-float lat/lon; coordinates
              outside the Swiss bounding box.
    """
    _require_debug()

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
    resort.save(
        update_fields=[
            "latitude",
            "longitude",
            "geocode_source",
            "geocode_confidence",
            "geocoded_at",
            "needs_review",
            "updated_at",
        ]
    )

    return JsonResponse(
        {
            "id": resort.pk,
            "name": resort.name,
            "region_id": resort.region.region_id,
            "latitude": resort.latitude,
            "longitude": resort.longitude,
            "geocode_source": resort.geocode_source,
            "geocode_confidence": resort.geocode_confidence,
            "geocoded_at": resort.geocoded_at.isoformat()
            if resort.geocoded_at
            else None,
            "needs_review": resort.needs_review,
            "next_in_queue": _next_queue_entry(skip_pk=resort.pk),
        }
    )


# SNOW-79 retired the ``offline_manifest_map`` endpoint. The PWA shell
# service worker now caches static assets at runtime via
# stale-while-revalidate, so there is no precache manifest for an SW to
# fetch. See ``static/js/sw.js`` and ``docs/offline-map.md``.
