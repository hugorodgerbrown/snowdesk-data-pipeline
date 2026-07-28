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
  Each feature carries ``properties.download`` (SNOW-521) when the region
  has a precomputed offline-basemap size summary — see ``regions_geojson``'s
  docstring for the exact shape.
* ``/api/major-regions.geojson``           — FeatureCollection of L1 region polygons.
* ``/api/sub-regions.geojson``             — FeatureCollection of L2 region polygons.
* ``/api/region-basemap-tiles/``           — SNOW-521: the full precomputed
  basemap_download blob (incl. ``z`` tile ranges) for one MicroRegion,
  ``?id=<region_id>``. Fetched on demand when the user clicks the
  region's download icon.
* ``/api/region/<region_id>/summary/``     — pre-rendered tooltip HTML for the
  MapLibre Popup anchored to the region's bbox centre; shows the day's danger
  rating chip (``?d=YYYY-MM-DD``-aware), breadcrumb, and resort list.
* ``/api/resorts/<resort_id>/popup/``      — SNOW-499: pre-rendered minimal
  popup HTML for a resort pin (name, region, favourite star, bulletin link).
  Public (no auth gate — resorts are a public layer); the favourite star
  itself is gated on authentication.
* ``/api/community-reports.geojson``       — anonymised, clustered
  ``FieldObservation`` pins from the last 48 hours (SNOW-419).
* ``/api/offline-manifest/map/``           — precache manifest for the offline CTA.

Flag-gated endpoints powering the in-map resort editor (SNOW-74,
``?edit=resorts`` on /map/). Both views check the ``edit_map`` waffle
flag (SNOW-86) and 404 when it is inactive for the request user:

* ``GET  /api/edit/resorts/queue/``               — queue + catalogue payload.
* ``POST /api/edit/resorts/<int:resort_id>/save/`` — persist the placed
  coordinates and the hand-curated detail fields.

Plain Django ``JsonResponse`` views — no DRF. The choropleth fetches its
three data endpoints in parallel at load time; the per-region summary
endpoint is hit on demand when the user taps a region.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any, cast

import waffle
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.vary import vary_on_headers
from django_ratelimit.decorators import ratelimit

from analytics.signals import emit_server_signal
from bulletins.models import Bulletin, BulletinGrouping, BulletinShare, RegionDayRating
from bulletins.services.coverage import covered_region_ids
from bulletins.services.settled import earliest_mutable_date
from core.freshness import apply_freshness_headers
from favourites.models import Favourite
from observations.models import FieldObservation
from regions.forms import RESORT_DETAIL_FIELDS, ResortDetailsForm
from regions.models import (
    MajorRegion,
    MicroRegion,
    Resort,
    SubRegion,
)
from regions.services.basemap_tiles import blob_summary

from .decorators import lowercase_region_id
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

# ISO 3166-1 alpha-2 → avalanche provider label mapping. Used by the region
# tooltip to identify the upstream data publisher for permanently-uncovered
# regions. Météo-France retains its accent — it is a proper noun.
_PROVIDER_BY_COUNTRY: dict[str, str] = {
    "CH": "SLF",
    "AT": "ALBINA",
    "IT": "ALBINA",
    "FR": "Météo-France",
}

# Valid values for the mandatory ?country= query param on GeoJSON endpoints.
# Stored as uppercase ISO-2 codes; the query param is accepted case-insensitively.
_VALID_GEOJSON_COUNTRIES: frozenset[str] = frozenset(COUNTRY_NAMES)

# Cache lifetime for static region GeoJSON — region geometry is fixture-backed
# and essentially never changes between deploys. Applied via decorator rather
# than a manual header assignment so Django's cache framework tracks it
# correctly and the session middleware cannot append Vary: Cookie.
_GEOJSON_CACHE_MAX_AGE = 86400

# Cache lifetime for dynamic-but-slow-moving map endpoints (ratings,
# resorts-by-region, resorts.geojson). Content only changes when a pipeline
# run lands new bulletins or an operator edits a resort; 5 minutes bounds the
# staleness while letting browsers and edge caches absorb the bulk of repeat
# hits. Pair the decorator with @vary_on_headers("Accept-Encoding") to stop
# SessionMiddleware from appending Vary: Cookie and killing shared caching.
_DYNAMIC_CACHE_MAX_AGE = 300

# Cache lifetime for settled (past the fetcher's earliest-mutable-date
# threshold) bulletin-groupings responses (SNOW-526). Settled geometry is
# immutable on the normal ingest path, but a manual
# ``backfill_bulletin_groupings --commit`` / ``fetch_bulletins --force`` can
# still rewrite history, so this is bounded well below the year-long
# max-age used for historic bulletin pages — see
# docs/decisions/date-aware-cache-policy.md. Offline availability comes
# from the service worker's Cache Storage entry, which ignores max-age
# entirely, so nothing is lost by keeping this short.
_SETTLED_CACHE_MAX_AGE = 604800  # 7 days

# Memoisation window for ``bulletins.services.settled.earliest_mutable_date()``
# (SNOW-526). That call runs one DB aggregate per registered ``BulletinSource``
# (see its docstring), and ``bulletin_groupings_geojson`` needs the result on
# every request — including a server-cache hit on the payload itself, which
# would otherwise still pay the settled-date DB cost on every scrubber-settle
# fetch. A stale memoised value is safe by construction: staleness can only
# make the threshold OLDER (smaller) than the true value, i.e. it can only
# under-report which dates are settled, never wrongly mark a still-mutable
# date as settled. Kept short (well under ``_DYNAMIC_CACHE_MAX_AGE``) so a
# fresh ingest run's new threshold is picked up promptly. Distinct cache key
# from the per-``(country, date)`` payload cache below — this caches the
# threshold itself, not any one date's answer.
_SETTLED_THRESHOLD_CACHE_KEY = "bulletin_groupings:settled_threshold"
_SETTLED_THRESHOLD_CACHE_TIMEOUT = 60

# Client-side freshness window for the community-reports overlay (SNOW-419).
# The endpoint itself is no-store (per-user flag-gated, SNOW-459), so this is
# not a shared-cache lifetime — it drives only the client's freshness dot.
# Shorter than _DYNAMIC_CACHE_MAX_AGE because the 48h inclusion window moves
# minute-to-minute — a new report should surface promptly and the oldest
# report drop out without a long stale tail.
_COMMUNITY_CACHE_MAX_AGE = 120

# Rolling lookback window for the community-reports overlay. Reports older
# than this are anonymous-by-attrition rather than anonymous-by-design — the
# overlay only ever shows a recent, ephemeral signal, never a durable map of
# where reports have historically clustered.
_COMMUNITY_REPORTS_WINDOW_HOURS = 48

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


def _download_summary_or_none(blob: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a stored ``basemap_download`` blob to its API summary, or None.

    Args:
        blob: A region's ``basemap_download`` field value (may be None —
            not yet computed, e.g. a region without geometry).

    Returns:
        ``blob_summary(blob)``, or ``None`` if ``blob`` is falsy.

    """
    return blob_summary(blob) if blob else None


def _build_ratings_payload(
    parsed_date: date | None,
    country_param: str,
) -> tuple[dict[str, dict[str, int]], datetime | None]:
    """
    Build the ``{date_iso: {region_id: rating_int}}`` payload from the DB.

    Queries ``RegionDayRating`` filtered by the supplied date and/or
    country, encodes each rating via ``_RATING_TO_INT``, and groups by
    ISO date string. Called lazily from ``cache.get_or_set`` in the
    ``ratings`` view so the full query only runs once per cache window.

    The paired ``generated_at`` is the newest ``updated_at`` across the
    returned rows — the moment the freshest fact in this payload was
    last touched by the pipeline (SNOW-370, spec §5.4). Returns ``None``
    when the query yielded no rows so the view can emit a "now"
    fallback rather than lying with a stale timestamp.

    Args:
        parsed_date: If set, restrict to rows for this date only.
        country_param: Uppercase ISO-2 country code, e.g. ``"CH"``. Pass
            an empty string to include all countries.

    Returns:
        ``(payload, generated_at)`` — the mapping and the newest
        ``updated_at`` across the returned rows, or ``None`` for empty.

    """
    qs = RegionDayRating.objects.values_list(
        "date", "region__region_id", "max_rating", "updated_at"
    )
    if parsed_date:
        qs = qs.filter(date=parsed_date)
    if country_param:
        qs = qs.filter(region__subregion__major__country=country_param)
    qs = qs.order_by("date", "region__region_id")
    payload: dict[str, dict[str, int]] = {}
    newest: datetime | None = None
    for row_date, region_id, rating, updated_at in qs:
        payload.setdefault(row_date.isoformat(), {})[region_id] = _RATING_TO_INT[rating]
        if newest is None or updated_at > newest:
            newest = updated_at
    return payload, newest


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

    The ``@vary_on_headers("Accept-Encoding")`` decorator is necessary but
    not sufficient on its own — ``Vary: Cookie`` is only reliably absent
    because this path is exempt from ``PosthogContextMiddleware`` via
    ``_POSTHOG_EXEMPT_PATHS`` in ``config/settings/base.py`` (SNOW-299).

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

    # Version prefix bumped for SNOW-370: the cached value is now a
    # ``(payload, generated_at)`` tuple rather than a bare dict, so a
    # crossover deploy would otherwise unpack a legacy dict and crash.
    cache_key = (
        f"ratings:v2:{country_param.lower() or 'all'}:"
        f"{parsed_date.isoformat() if parsed_date else 'season'}"
    )
    # Use a longer TTL for full-season payloads (no single-date
    # column moves intra-day) and the standard 5-minute TTL for
    # single-date responses so today's column updates promptly.
    ttl = 300 if parsed_date else 3600

    # ``cache.get_or_set`` is typed to return ``Any | None``, but the
    # default here (a lambda that always returns a 2-tuple) guarantees a
    # non-None result — the cast documents that guarantee to mypy.
    cached = cast(
        "tuple[dict[str, dict[str, int]], datetime | None]",
        cache.get_or_set(
            cache_key,
            lambda: _build_ratings_payload(parsed_date, country_param),
            timeout=ttl,
        ),
    )
    payload, generated_at = cached
    response = JsonResponse(payload)
    # ``generated_at`` is None only when the query returned zero rows —
    # a rare edge case in production (empty country/date window). Fall
    # back to "now" so the client still sees a valid timestamp; the
    # empty body itself communicates the no-data state.
    apply_freshness_headers(
        response,
        generated_at=generated_at or timezone.now(),
    )
    return response


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

    SNOW-521: each feature also carries ``properties.download`` — this
    region's precomputed offline-basemap size summary (``{count, mb,
    over_ceiling, centre_tile}``, ``regions.services.basemap_tiles.
    blob_summary``) — omitted entirely when ``basemap_download`` is
    unset. The ``#region-readout`` chip's download icon reads this
    straight off the always-loaded ``regions.geojson`` payload with no
    extra fetch; the full blob (incl. ``z`` tile ranges) is fetched on
    demand from ``region_basemap_tiles`` only when the user clicks it.

    The ``@cache_control(public=True, max_age=86400)`` + ``@vary_on_headers``
    pair prevents Django's ``SessionMiddleware`` from appending
    ``Vary: Cookie`` on the response.  Region geometry is fixture-backed and
    anonymous — it is safe to cache publicly for 24 hours.  ``Vary: Cookie``
    is also suppressed by exempting this path from ``PosthogContextMiddleware``
    via ``_POSTHOG_EXEMPT_PATHS`` in ``config/settings/base.py``
    (SNOW-299) — without that exemption the middleware would read
    ``request.user`` and cause ``SessionMiddleware`` to append the header.

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
    covered = covered_region_ids()
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
        # SNOW-314 prototype: L1 major-region display name, so the season-header
        # readout can build a Major (L1) › Minor (L2) › Micro (L4) breadcrumb
        # that mirrors the visible map overlays without a second fetch. Mirrors
        # the popup breadcrumb's field choice (_region_tooltip.html): English
        # name with the native name as fallback.
        major_name = sub.major.name_en or sub.major.name_native
        properties: dict[str, Any] = {
            "id": region.region_id,
            "name": region.name,
            # SNOW-314: name-derived slug for the bulletin URL's second
            # path component, so the season-ribbon readout can link
            # straight to /<region_id>/<slug>/<date>/ without a lookup.
            "slug": region.name_slug,
            "country": sub.major.country,
            "subregion_name": subregion_name,
            "major_name": major_name,
            # SNOW-54: True when the pipeline has ever produced a rating
            # for this region; False for permanently-uncovered regions
            # (e.g. Swiss Lowlands / Jura, non-EUREGIO AT/IT areas).
            "covered": region.region_id in covered,
        }
        download = _download_summary_or_none(region.basemap_download)
        if download is not None:
            properties["download"] = download
        features.append(
            {
                "type": "Feature",
                "geometry": region.boundary,
                "properties": properties,
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

    SNOW-521: offline-basemap download is a **MicroRegion-only** feature
    — this tier never carries a ``properties.download`` key.

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
        properties: dict[str, Any] = {
            "prefix": major.prefix,
            "name_en": major.name_en,
            "country": major.country,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": major.boundary,
                "properties": properties,
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

    SNOW-521: offline-basemap download is a **MicroRegion-only** feature
    — this tier never carries a ``properties.download`` key.

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
        properties: dict[str, Any] = {
            "prefix": sub.prefix,
            "name_en": sub.name_en,
            "country": sub.major.country,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": sub.boundary,
                "properties": properties,
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
@require_GET
def region_basemap_tiles(request: HttpRequest) -> JsonResponse:
    """
    Return the full precomputed ``basemap_download`` blob for one MicroRegion.

    ``?id=<region_id>`` — resolved against ``MicroRegion.region_id`` (L4)
    only; offline-basemap download is a MicroRegion-only feature (SNOW-521).
    404 for an unknown id or a region with no computed blob yet.

    Unlike the ``download`` property inlined on ``regions_geojson`` (a
    small summary — no ``z`` ranges), this returns the **full** blob
    including ``z`` — the per-zoom tile-index ranges the client expands
    into tile URLs (``rangesToTileURLs`` in ``basemap_download_core.js``).
    Fetched on demand, only when the user clicks the download icon (not
    preloaded with the geojson), since most users never trigger a
    download for most regions.

    Region geometry — and therefore this blob — is static reference
    data, so the same ``@cache_control(public=True, max_age=86400)`` +
    ``@vary_on_headers`` + ``_POSTHOG_EXEMPT_PATHS`` treatment as the
    geojson endpoints applies. See ``regions_geojson`` for the full
    ``Vary: Cookie`` rationale.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with the blob, or 400 (missing ``?id=``) / 404
        (unknown region or no computed blob).

    """
    region_id = request.GET.get("id", "")
    if not region_id:
        return JsonResponse({"error": "missing_id"}, status=400)

    region = MicroRegion.objects.filter(region_id=region_id).first()
    if region is None or not region.basemap_download:
        raise Http404(f"No computed basemap_download for region {region_id!r}")

    return JsonResponse(region.basemap_download)


@vary_on_headers("Accept-Encoding")
def bulletin_groupings_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a single date's FeatureCollection of dissolved bulletin boundaries.

    Response shape::

        {
          "type": "FeatureCollection",
          "features": [
            {
              "type": "Feature",
              "geometry": <GeoJSON Polygon or MultiPolygon>,
              "properties": {
                "bulletin_id": "...",
                "date": "YYYY-MM-DD",
                "countries": ["AT", "IT"]
              }
            },
            ...
          ]
        }

    Each feature is one bulletin's dissolved L4 micro-region boundary for
    the requested forecast day. Powered by ``BulletinGrouping`` rows
    computed at ingest time.

    The endpoint is deliberately **single-date** (``?d=`` is required). An
    earlier version returned the whole season keyed by date in one payload;
    once the historical backfill landed, serialising every day's dissolved
    geometry at once pushed the web worker past its 512 MB limit. The map
    now fetches one day at a time, debounced on the scrubber settling, so
    the response — and the worker's peak memory — stays bounded regardless
    of how much history accumulates (see ``docs/map-and-api.md``).

    Query parameters:

    * ``?d=YYYY-MM-DD`` (**required**) — the forecast day to fetch.
    * ``?country=ch|fr|at|it`` (case-insensitive) — restrict to groupings
      whose ``countries`` list **contains** the requested country.

    Server-side ``cache.get_or_set`` keyed on ``(country, date)`` mirrors
    the ``ratings`` view so DB hits are bounded to one per cache window
    (5 minutes, same as other dynamic endpoints) regardless of the
    requested date's settled state.

    ``Cache-Control`` is date-aware (SNOW-526), set via
    ``patch_cache_control`` rather than the ``@cache_control`` decorator so
    it can branch per response:

    * a settled date (before ``earliest_mutable_date()`` —
      ``bulletins.services.settled`` — the earliest date any provider's
      next ingest run could still change) gets
      ``public, max_age=604800, immutable`` — the ``sw.js`` service worker
      treats ``immutable`` as its signal to persist the response for
      offline use. The threshold itself is memoised for
      ``_SETTLED_THRESHOLD_CACHE_TIMEOUT`` seconds (a stale value can only
      under-report which dates are settled, never mis-mark a mutable one)
      so this doesn't add a per-``BulletinSource`` DB query to every
      request;
    * today and the still-mutable tail keep the existing
      ``public, max_age=300`` and stay network-only.

    Errors:
        400 — missing ``?d=`` date.
        400 — malformed ``?d=`` date string.
        400 — unrecognised ``?country=`` value.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse holding one GeoJSON FeatureCollection, or 400 on an
        invalid/absent parameter.

    """
    date_param = request.GET.get("d")
    country_param = (request.GET.get("country") or "").upper()

    if country_param and country_param not in _VALID_GEOJSON_COUNTRIES:
        return JsonResponse(
            {"error": "invalid_country", "valid": sorted(_VALID_GEOJSON_COUNTRIES)},
            status=400,
        )

    if not date_param:
        return JsonResponse(
            {"error": "date_required", "hint": "pass ?d=YYYY-MM-DD"},
            status=400,
        )
    # Enforce the strict hyphenated YYYY-MM-DD wire format, mirroring the
    # ``ratings`` view: ``date.fromisoformat`` also accepts "YYYYMMDD" on
    # Python 3.11+, but callers only ever send hyphenated ISO dates.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_param):
        return JsonResponse({"error": "malformed date"}, status=400)
    try:
        parsed_date = date.fromisoformat(date_param)
    except ValueError:
        return JsonResponse({"error": "malformed date"}, status=400)

    cache_key = (
        f"bulletin_groupings:{country_param.lower() or 'all'}:{parsed_date.isoformat()}"
    )

    payload = cache.get_or_set(
        cache_key,
        lambda: _build_groupings_payload(parsed_date, country_param),
        timeout=_DYNAMIC_CACHE_MAX_AGE,
    )
    response = JsonResponse(payload)
    if parsed_date < _cached_earliest_mutable_date():
        patch_cache_control(
            response, public=True, max_age=_SETTLED_CACHE_MAX_AGE, immutable=True
        )
    else:
        patch_cache_control(response, public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
    return response


def _cached_earliest_mutable_date() -> date:
    """
    Return ``earliest_mutable_date()``, memoised for a short window (SNOW-526).

    ``earliest_mutable_date()`` (``bulletins.services.settled``) runs one DB
    aggregate per registered ``BulletinSource`` — cheap in isolation, but
    ``bulletin_groupings_geojson`` needs the answer on every request
    (including a server-cache hit on the payload itself), so calling it
    unmemoised would add that DB cost to every scrubber-settle fetch.
    Memoised here, at the call site, rather than inside
    ``bulletins.services.settled`` itself — that module stays a pure,
    uncached derivation any other caller can trust for a live answer.

    A stale cached value is safe by construction: staleness can only make
    the returned threshold OLDER (smaller) than the true current value, so
    it can only under-report which dates are settled — never mark a
    still-mutable date as settled early. See
    ``docs/decisions/date-aware-cache-policy.md``.

    Returns:
        The (possibly up to ``_SETTLED_THRESHOLD_CACHE_TIMEOUT`` seconds
        stale) earliest-mutable-date threshold.

    """
    result = cache.get_or_set(
        _SETTLED_THRESHOLD_CACHE_KEY,
        earliest_mutable_date,
        timeout=_SETTLED_THRESHOLD_CACHE_TIMEOUT,
    )
    return cast(date, result)


def _build_groupings_payload(
    target_date: date,
    country_param: str,
) -> dict[str, Any]:
    """
    Build one date's groupings FeatureCollection from the DB.

    Queries the ``BulletinGrouping`` rows for ``target_date`` (optionally
    filtered to those whose ``countries`` list contains the requested
    country) and returns a single GeoJSON FeatureCollection dict.

    Args:
        target_date: The forecast day to build the FeatureCollection for.
        country_param: Uppercase ISO-2 country code (e.g. ``"CH"``), or
            an empty string to include all countries.

    Returns:
        A GeoJSON FeatureCollection dict for the requested date.

    """
    # Restrict to bulletins that actually won at least one region for the day.
    #
    # Two bulletins routinely target the same day: the morning-of-X issue and
    # the previous evening's, which both satisfy ``_target_day(b) == X``. Both
    # get a BulletinGrouping row, so without this filter the endpoint returns
    # two near-identical overlapping outlines for most days — invisible while
    # the layer was an opt-in dashed overlay, obvious now it is drawn
    # alongside every micro-region view. ``recompute_region_day`` already
    # arbitrates between them per region; reusing its verdict here keeps the
    # boundary consistent with the colour it encloses rather than
    # re-implementing the morning-wins rule.
    winning_bulletin_ids = set(
        RegionDayRating.objects.filter(
            date=target_date, source_bulletin__isnull=False
        ).values_list("source_bulletin_id", flat=True)
    )

    qs = (
        BulletinGrouping.objects.for_date(target_date)
        .filter(bulletin_id__in=winning_bulletin_ids)
        .select_related("bulletin")
        .order_by("bulletin__bulletin_id")
    )

    date_key = target_date.isoformat()
    # ``countries`` is a JSON list (e.g. ["AT", "IT"]). We filter membership
    # in Python rather than with a DB-level JSONField lookup so that the query
    # is backend-agnostic — Django's JSONField ``__contains`` list-membership
    # check is not supported by SQLite, which the dev and test environments use.
    # A single day's rows are a tiny set, so the extra Python iteration is
    # negligible.
    features: list[dict[str, Any]] = []
    for grouping in qs.iterator():
        if country_param and country_param not in grouping.countries:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": grouping.boundary,
                "properties": {
                    "bulletin_id": grouping.bulletin.bulletin_id,
                    "date": date_key,
                    "countries": grouping.countries,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@lowercase_region_id
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

    # SNOW-54: resolve coverage and provider label for the tooltip branch.
    covered = region.region_id in covered_region_ids()
    provider_name = _PROVIDER_BY_COUNTRY.get(region.subregion.major.country, "")

    level = day_rating.max_rating if day_rating else "no_rating"

    response = JsonResponse(
        {
            "html": render_to_string(
                "public/_region_tooltip.html",
                {
                    "region": region,
                    "day_rating": day_rating,
                    "bulletin_url": bulletin_url,
                    "country_name": country_name,
                    "target_date": target_date,
                    "covered": covered,
                    "provider_name": provider_name,
                },
                request=request,
            ),
            "level": level,
        }
    )
    # Freshness contract: use the specific day-rating's updated_at when we
    # have one, else "now" — a no-rating tooltip has no source data to
    # attach a timestamp to, and the client's freshness dot will show
    # green off "now" which correctly says "this is the current view".
    apply_freshness_headers(
        response,
        generated_at=day_rating.updated_at if day_rating else timezone.now(),
    )
    return response


def resort_popup(request: HttpRequest, resort_id: int) -> JsonResponse:
    """Return pre-rendered minimal popup HTML for a resort-pin tap.

    Response shape::

        {"html": "<...>"}

    The ``html`` key is a server-rendered snippet injected into a
    ``maplibregl.Popup`` on ``/map/`` when a resort pin is tapped (SNOW-499),
    mirroring ``region_summary``'s ``{"html": ...}`` shape. Content: resort
    name (+ alternative name), parent region name, curated metadata rows
    (operator, website, lift/run counts, piste length, elevation range,
    typical season — SNOW-501), a favourite/unfavourite star, and a
    "View resort →" link.

    SNOW-501: every metadata row always renders, even when the underlying
    field is blank — a blank field shows a dashed-box placeholder rather
    than being omitted, so missing curation stays visible. ``is_staff`` in
    the render context switches that placeholder from a public em-dash to
    an explicit "Add <field>" curation hint.

    Unlike ``region_summary``, this endpoint is **public** — resorts are a
    public reference layer, so anonymous visitors see the same name/region/
    resort-link content everyone else does. Only the favourite star is
    gated: it renders (with the correct favourited state + the existing
    favourite's uuid, for unfavourite) when the requester is authenticated;
    otherwise the popup shows a sign-in CTA in its place
    (``can_favourite=False``).

    SNOW-504: the CTA now reads "View resort →" and links to the resort's
    own page (``resort.get_absolute_url()``) rather than straight to the
    region bulletin — the bulletin link now lives on that page instead.

    Deliberately **not** cached (unlike ``resorts_geojson``) — the
    favourited state is per-user, so caching this response would leak one
    user's favourite state to another (see the SNOW-499 plan's cache-policy
    note: ``resorts_geojson`` stays anonymous and unchanged specifically so
    this per-user popup doesn't have to fight a shared-cache Vary: Cookie
    problem).

    Errors:
        404 — unknown ``resort_id``.

    Args:
        request: The incoming HTTP request.
        resort_id: The Resort's primary key.

    Returns:
        A JsonResponse with a single ``html`` key containing the popup markup.

    """
    resort = get_object_or_404(
        Resort.objects.select_related("region"),
        pk=resort_id,
    )

    can_favourite = False
    favourite = None
    if request.user.is_authenticated:
        can_favourite = True
        favourite = Favourite.objects.filter(user=request.user, resort=resort).first()

    response = JsonResponse(
        {
            "html": render_to_string(
                "public/partials/_resort_popup.html",
                {
                    "resort": resort,
                    "region": resort.region,
                    "favourited": favourite is not None,
                    "favourite_uuid": str(favourite.uuid) if favourite else "",
                    "can_favourite": can_favourite,
                    "signin_url": reverse("accounts:sign_in"),
                    "resort_url": resort.get_absolute_url(),
                    "is_staff": request.user.is_staff,
                },
                request=request,
            ),
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response


# ---------------------------------------------------------------------------
# Community reports overlay (SNOW-419)
# ---------------------------------------------------------------------------


def _truncate_to_quarter_hour(value: datetime) -> datetime:
    """Floor a timezone-aware datetime to the preceding 15-minute mark.

    Coarsens ``FieldObservation.observed_at`` before it crosses the wire —
    part of the SNOW-419 anonymisation contract alongside coordinate
    rounding, so the timestamp alone cannot be combined with other public
    data to pin down exactly when a specific report was filed.

    Args:
        value: A timezone-aware datetime.

    Returns:
        ``value`` with ``minute`` floored to the nearest lower multiple of
        15, and ``second``/``microsecond`` zeroed. ``tzinfo`` is preserved.

    """
    floored_minute = (value.minute // 15) * 15
    return value.replace(minute=floored_minute, second=0, microsecond=0)


# SNOW-459: private/no-store, NOT public — CDN/proxy caching of this
# response is left for SNOW-469 (tracked separately, out of scope here).
@cache_control(private=True, no_store=True)
def community_reports_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of anonymised community field reports.

    Covers ``FieldObservation`` rows from the last 48 hours
    (``_COMMUNITY_REPORTS_WINDOW_HOURS``), rendered as a "Community
    reports" overlay on ``/map/`` (SNOW-419) — a Waze-like shared layer of
    clustered pins.

    Anonymisation is applied server-side, once, here — the client never
    receives full-precision coordinates or anything that identifies the
    reporting account:

    * ``coordinates`` are ``[longitude, latitude]`` rounded to 3 decimal
      places (~80-110 m), never the raw ``latitude``/``longitude`` fields.
    * ``observed_at`` is floored to the nearest 15 minutes.
    * ``gps_latitude``/``gps_longitude``/``accuracy_radius_km``/``user``/
      the row's primary key are never serialised.

    Response shape::

        {
          "type": "FeatureCollection",
          "features": [
            {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [7.123, 46.456]},
              "properties": {
                "type": "WHUMPFING",
                "type_label": "Whumpfing",
                "observed_at": "2026-07-19T10:15:00+00:00",
                "region_name": "Martigny-Verbier"
              }
            },
            ...
          ]
        }

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload.

    """
    since = timezone.now() - timedelta(hours=_COMMUNITY_REPORTS_WINDOW_HOURS)
    features: list[dict[str, Any]] = []
    newest: datetime | None = None
    for obs in (
        FieldObservation.objects.recent(since).select_related("region").iterator()
    ):
        if newest is None or obs.observed_at > newest:
            newest = obs.observed_at
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    # GeoJSON ordering: [longitude, latitude] per RFC 7946.
                    # Rounded to 3 dp (~80-110 m) — never the raw field.
                    "coordinates": [round(obs.longitude, 3), round(obs.latitude, 3)],
                },
                "properties": {
                    "type": obs.observation_type,
                    "type_label": obs.get_observation_type_display(),
                    "observed_at": _truncate_to_quarter_hour(
                        obs.observed_at
                    ).isoformat(),
                    "region_name": obs.region.name if obs.region is not None else None,
                },
            }
        )

    response = JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )
    # Observations are non-safety-critical (unlike ratings) — unsafe_after=None
    # so the freshness state saturates at "stale" and never escalates to
    # "unsafe". Empty result set (no recent reports) falls back to "now".
    # max_age is pinned to _COMMUNITY_CACHE_MAX_AGE rather than the
    # apply_freshness_headers default (24h): it is the client-side freshness
    # window for the overlay (the dot flips to "stale" after 120s), matching
    # the cadence at which the 48h inclusion window turns over. The response
    # itself is no-store (see the decorator) — this window drives only the
    # client freshness dot, not any shared cache.
    apply_freshness_headers(
        response,
        generated_at=newest or timezone.now(),
        max_age=_COMMUNITY_CACHE_MAX_AGE,
        unsafe_after=None,
    )
    return response


# ---------------------------------------------------------------------------
# Edit-resorts mode (SNOW-74) — flag-gated on ``edit_map`` (SNOW-86)
# ---------------------------------------------------------------------------


def _require_edit_map_flag(request: HttpRequest) -> None:
    """Raise Http404 unless the ``edit_map`` waffle flag is active.

    Mirrors the view-level guard ``map_view`` applies before rendering
    the editor panel: an unauthorised caller hitting the API directly
    must see the same 404 the URL conf used to give them when the
    feature was DEBUG-only. Flag is seeded with ``superusers=True`` by
    migration ``regions/migrations/0002_seed_edit_map_flag.py``;
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
    ``longitude``, ``has_coords``, ``needs_review``, plus a ``details``
    object holding every hand-curated metadata field
    (``regions.forms.RESORT_DETAIL_FIELDS``) so the panel can populate
    its edit form on selection without a per-resort round trip.

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
            "id": row["pk"],
            "name": row["name"],
            "region_id": row["region__region_id"],
            "region_name": row["region__name"],
            "canton": row["canton"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "has_coords": row["latitude"] is not None and row["longitude"] is not None,
            "needs_review": row["needs_review"],
            "details": {field: row[field] for field in RESORT_DETAIL_FIELDS},
        }
        for row in (
            Resort.objects
            # L2 (e.g. "CH-41") is a prefix of L4 (e.g. "CH-4115"), so
            # sorting on region_id alone groups rows by L2 in the right
            # order. ``name`` breaks ties within a region.
            .order_by("region__region_id", "name").values(
                "pk",
                "name",
                "region__region_id",
                "region__name",
                "canton",
                "latitude",
                "longitude",
                "needs_review",
                *RESORT_DETAIL_FIELDS,
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


def _resort_details_payload(resort: Resort) -> dict[str, Any]:
    """Return the hand-curated metadata fields of ``resort`` as a dict.

    The single shape the panel reads on both legs of the round trip — the
    catalogue's ``details`` object and the save response's — so a saved
    row can be patched into the in-memory catalogue verbatim.
    """
    return {field: getattr(resort, field) for field in RESORT_DETAIL_FIELDS}


def _bind_resort_details(
    resort: Resort,
    raw_details: Any,
) -> tuple[JsonResponse | None, list[str]]:
    """Validate and apply the incoming detail fields to ``resort`` in memory.

    An absent key means "leave this field alone": the incoming values are
    merged over the instance's current ones before binding, so the panel
    can send a partial object and a field it never renders can never be
    blanked by omission.

    Nothing is written to the database — the caller saves once, with the
    coordinate columns, so a rejected detail field cannot leave a
    half-applied row behind.

    Args:
        resort: The instance to apply the values to (mutated in place on
            success).
        raw_details: The request body's ``details`` value, unvalidated.

    Returns:
        ``(error_response, [])`` when the payload is malformed or fails
        validation, otherwise ``(None, changed_field_names)`` — the names
        to add to the caller's ``update_fields``.

    """
    if raw_details is None:
        return None, []
    if not isinstance(raw_details, dict):
        return (
            JsonResponse(
                {"error": "invalid_details", "detail": "details must be an object"},
                status=400,
            ),
            [],
        )

    data = {
        field: raw_details.get(field, getattr(resort, field))
        for field in RESORT_DETAIL_FIELDS
    }
    form = ResortDetailsForm(data, instance=resort)
    if not form.is_valid():
        return (
            JsonResponse(
                {
                    "error": "invalid_details",
                    "detail": "one or more detail fields are invalid",
                    "fields": form.errors,
                },
                status=400,
            ),
            [],
        )
    # commit=False applies the cleaned values to the instance without
    # touching the database; the caller's single save() persists them
    # alongside the coordinates.
    form.save(commit=False)
    return None, list(RESORT_DETAIL_FIELDS)


@require_POST
def edit_resort_save(request: HttpRequest, resort_id: int) -> JsonResponse:
    """Persist the placed coordinates and detail fields of a resort (flag-gated).

    Request body (JSON)::

        {
          "latitude": <float>,
          "longitude": <float>,
          "details": {"num_lifts": 12, "website": "https://…", …}
        }

    ``details`` is optional and may be partial — every key it omits keeps
    its stored value (see ``_bind_resort_details``); its permitted keys
    are ``regions.forms.RESORT_DETAIL_FIELDS``.

    On success, sets ``geocode_source="manual"``,
    ``geocode_confidence=1.0``, ``geocoded_at=now()``, and clears
    ``needs_review`` — the operator has the pin under the placement
    marker whenever they save, so every save is a manual confirmation of
    the position, not just of the detail fields. Auto-rebinds
    ``resort.region`` if the saved point lands inside a different
    region's polygon (SNOW-85). Returns the updated resort fields
    including the (possibly re-bound) ``region_id`` and ``region_name``
    and the saved ``details`` so the panel can patch its in-memory
    catalogue without a follow-up GET.

    Errors:
        404 — ``edit_map`` waffle flag inactive, or unknown ``resort_id``.
        400 — invalid JSON; missing or non-float lat/lon; coordinates
              outside the Swiss bounding box; a detail field that fails
              model validation (``{"error": "invalid_details", "fields":
              {…}}``, keyed by field name).
    """
    _require_edit_map_flag(request)

    try:
        payload = json.loads(request.body or b"")
    except ValueError, json.JSONDecodeError:
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
    except TypeError, ValueError:
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

    details_error, changed_details = _bind_resort_details(
        resort,
        payload.get("details"),
    )
    if details_error is not None:
        return details_error

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
        *changed_details,
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
            "edit_resort_save: rebinding %s from %s to %s",
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
            "details": _resort_details_payload(resort),
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
    except ValueError, json.JSONDecodeError:
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
    except ValueError, TypeError:
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


# ---------------------------------------------------------------------------
# PWA shell contract — version + kill-switch config (SNOW-369, SNOW-372)
# ---------------------------------------------------------------------------


@require_GET
def version(request: HttpRequest) -> JsonResponse:
    """Server-authoritative version + kill state for the PWA client.

    Returns the fixed shape the offline-first spec requires so the client
    can (a) detect a new build to pick up (``current``), (b) enter a forced
    Update Required state when it falls below ``min_supported``, and (c)
    trigger the Mechanism-A kill switch when ``kill`` is true.

    The response is cacheable at the CDN for 60 seconds because the values
    only change on deploy or on an explicit ops flip — the client itself
    fetches this on cold start and every 15 minutes while active, so a
    stale entry at the edge is bounded by the polling window.
    """
    response = JsonResponse(
        {
            "current": settings.APP_VERSION,
            "min_supported": settings.APP_MIN_VERSION,
            "released_at": settings.APP_RELEASED_AT,
            "kill": bool(settings.SW_KILL),
        }
    )
    response["Cache-Control"] = "public, max-age=60"
    # SNOW-381 (spec §16.2): capture the endpoint hit so the observability
    # dashboards can chart poll cadence and detect clients that stop
    # phoning home.
    # SNOW-384: client_version is read from the X-Client-Version request
    # header. Client sends this header via static/js/pwa_client_version.js
    # (SNOW-388) on every same-origin fetch/HTMX request.
    emit_server_signal(
        "pwa.version.endpoint.hit",
        {"client_version": request.headers.get("X-Client-Version", "")},
    )
    return response


@require_GET
def sw_config(request: HttpRequest) -> JsonResponse:
    """Kill-switch and SW-URL config consumed at SW-registration time.

    The client fetches this before ``navigator.serviceWorker.register`` so
    ops can (a) swap the active SW URL — e.g. from ``/sw.js`` to
    ``/sw-kill.js`` — without a code deploy, and (b) hard-disable the SW
    for every installed client by flipping ``SW_KILL=true`` in Render env.

    Never cache this response — the whole point is that the client sees the
    latest state on every launch. ``no-store`` is stronger than ``no-cache``
    and correct here because we don't want intermediaries to hold it either.
    """
    response = JsonResponse(
        {
            "sw_url": settings.SW_URL,
            "kill": bool(settings.SW_KILL),
        }
    )
    response["Cache-Control"] = "no-store"
    # SNOW-381 (spec §16.2): capture the endpoint hit so we can spot
    # SW-registration attempts that never proceed to install/activate.
    kill = bool(settings.SW_KILL)
    sw_config_properties: dict[str, object] = {
        "sw_url": settings.SW_URL,
        "kill": kill,
        # SNOW-384: client_version — see the identical note on
        # ``version()`` above. Client sends this header via
        # static/js/pwa_client_version.js (SNOW-388).
        "client_version": request.headers.get("X-Client-Version", ""),
    }
    if kill:
        # SNOW-384: "reason" identifies what drove the kill decision.
        # ``SW_KILL`` (config/settings/base.py) is the only kill trigger
        # today, so the reason is a fixed string. If a second trigger is
        # ever added (e.g. a per-version kill list), give it its own
        # reason string here rather than overloading this one.
        sw_config_properties["reason"] = "env_kill_switch"
    emit_server_signal("pwa.sw_config.hit", sw_config_properties)
    return response


# ---------------------------------------------------------------------------
# Per-bulletin JSON endpoints (SNOW-394)
# ---------------------------------------------------------------------------
# Two representations of a single stored bulletin, keyed by ``bulletin_id``
# (the CharField, not the auto PK). The two shapes are exposed as two
# distinct URLs — rather than one URL with ``Accept`` content negotiation —
# so each is advertised on the bulletin HTML page with its own
# ``<link rel="alternate" type="application/json">``, so generic
# fetchers (``Accept: */*``) still discover both, and so CDN caches key
# on URL alone without a ``Vary: Accept`` fragmentation.


@require_GET
@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
def bulletin_render_model(request: HttpRequest, bulletin_id: str) -> JsonResponse:
    """
    Serve the render model for one bulletin as JSON.

    The render model is Snowdesk's canonical, versioned view of the
    bulletin — the same shape the map tooltip and the season sheet
    consume. It is the shape a downstream consumer usually wants: no
    provider-specific CAAML idiosyncrasies, just the presentation-ready
    fields plus a schema version.

    Args:
        request: The incoming HTTP request (unused — the response is
            keyed on ``bulletin_id`` alone).
        bulletin_id: The public identifier of the bulletin (the
            ``Bulletin.bulletin_id`` CharField, not the auto PK).

    Returns:
        A ``JsonResponse`` with the render model as the body, or 404 if
        no bulletin exists for that id.

    """
    bulletin = get_object_or_404(Bulletin, bulletin_id=bulletin_id)
    return JsonResponse(bulletin.render_model or {})


@require_GET
@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
def bulletin_caaml(request: HttpRequest, bulletin_id: str) -> JsonResponse:
    """
    Serve the raw CAAML payload (in its GeoJSON Feature envelope) as JSON.

    The stored shape is the source-of-truth representation for the
    bulletin — every provider's CAAML v6 JSON wrapped in a
    ``{"type": "Feature", "geometry": null, "properties": {…}}``
    envelope (see the ``geojson-feature-envelope`` ADR). Consumers who
    want just the CAAML take ``.properties``; consumers who want the
    Feature envelope take the whole payload.

    Args:
        request: The incoming HTTP request (unused).
        bulletin_id: The public identifier of the bulletin.

    Returns:
        A ``JsonResponse`` with the stored raw payload as the body, or
        404 if no bulletin exists for that id.

    """
    bulletin = get_object_or_404(Bulletin, bulletin_id=bulletin_id)
    return JsonResponse(bulletin.raw_data or {})
