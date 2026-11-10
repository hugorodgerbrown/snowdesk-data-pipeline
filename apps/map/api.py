"""
apps/map/api.py — JSON endpoints for the interactive map.

Plain Django ``JsonResponse`` views (no DRF) consumed by ``static/js/map*.js``:

* ``/api/ratings/``                        — ``{date_iso: {region_id: rating_int}}``
  filtered by optional ``?d=YYYY-MM-DD`` and ``?country=ch|fr|at|it`` query params.
* ``/api/resorts-by-region/``              — ``{region_id: [resort_name, ...]}``.
* ``/api/resorts.geojson``                 — FeatureCollection of geocoded resorts.
* ``/api/regions.geojson``                 — FeatureCollection of L4 region polygons,
  each with ``properties.download`` (SNOW-521) when the region has a
  precomputed offline-basemap size summary.
* ``/api/major-regions.geojson`` / ``/api/sub-regions.geojson`` — L1 / L2 polygons.
* ``/api/area-content/``                   — SNOW-953: the micro-regions and
  public weather locations inside one ``?bbox=w,s,e,n`` rectangle.
* ``/api/area-items/``                     — SNOW-965: the signed-in user's
  own trips and pins inside one rectangle. Private, never cached.
* ``/api/region-basemap-tiles/``           — SNOW-521: one MicroRegion's full
  basemap_download blob, ``?id=<region_id>``.
* ``/api/region/<region_id>/summary/``     — pre-rendered tooltip HTML for the
  region popup, ``?d=YYYY-MM-DD``-aware.
* ``/api/bulletin-groupings.geojson``      — the day's bulletin groupings.
* ``/api/community-reports.geojson``       — anonymised, clustered
  ``FieldObservation`` pins from the last 48 hours (SNOW-419).
* ``/api/weather.geojson``                 — the weather overlay's pins.
* ``/api/ski-network.geojson``, ``/api/ski-tours.geojson``,
  ``/api/ski-summits.json``, ``/api/rest-zones.geojson`` — the ski and
  wildlife layers, one rectangle at a time.
* ``/api/weather/<short_id>/detail/``      — one location's weather detail
  (``/api/weather/<int:location_id>/detail/`` survives as a legacy alias).
* ``POST /api/map-3d-test/``               — SNOW-1456: one 3D test result a
  reader chose to send, stored anonymously as a ``Map3dDeviceTest``.

Moved out of ``apps/public/api.py`` by SNOW-1428
(docs/decisions/public-splits-by-surface.md). The URL names stay ``api:…``:
``apps/public/api_urls.py`` routes these views. The in-map editor's
endpoints are in ``editor.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any, cast

from django.core.cache import cache
from django.db.models import Prefetch
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotModified,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.vary import vary_on_headers
from django_ratelimit.decorators import ratelimit
from waffle import flag_is_active

from apps.accounts.identity import attest_principal
from apps.bulletins.models import (
    BulletinGrouping,
    RegionDayRating,
)
from apps.bulletins.services.coverage import covered_region_ids
from apps.bulletins.services.settled import earliest_mutable_date
from apps.core.freshness import apply_freshness_headers
from apps.locations.models import Location
from apps.map.area_items import area_items as build_area_items
from apps.map.models import (
    MAP_3D_TEST_OVERLAYS,
    MAP_3D_TEST_VERDICTS,
    Map3dDeviceTest,
)
from apps.observations.models import FieldObservation, leading_family
from apps.observations.services.ground import report_window
from apps.public.bulletin_panels import (
    COUNTRY_NAMES,
    PROVIDER_BY_COUNTRY,
)
from apps.public.decorators import lowercase_region_id
from apps.public.weather_points import weather_point_context
from apps.regions.models import (
    MajorRegion,
    MicroRegion,
    Resort,
    SubRegion,
)
from apps.regions.services.area_content import (
    RegionBox,
    ResortPoint,
    WeatherPoint,
    area_content as build_area_content,
    micro_region_index,
    resort_index,
    weather_location_index,
)
from apps.regions.services.basemap_tiles import blob_summary
from apps.regions.services.geometry import rfc7946_rewind
from apps.routes.models import RestZone, SkiSegment, SkiTour
from apps.routes.services.rest_zones import zone_feature
from apps.routes.services.ski_network import network_overview
from apps.routes.services.ski_tours import (
    Summit,
    drawn_tours,
    layer_render,
    leg_parts,
    localised,
    sac_time,
    summits,
    tour_key,
    tour_overview,
)
from apps.weather.models import Weather
from apps.weather.services.fetch import FORECAST_DAYS
from apps.weather.services.weather_display import build_point_weather_days

logger = logging.getLogger(__name__)

# Cache lifetime for dynamic-but-slow-moving map endpoints (ratings,
# resorts-by-region, resorts.geojson). Content only changes when a pipeline
# run lands new bulletins or an operator edits a resort; 5 minutes bounds the
# staleness while letting browsers and edge caches absorb the bulk of repeat
# hits. Pair the decorator with @vary_on_headers("Accept-Encoding") to stop
# SessionMiddleware from appending Vary: Cookie and killing shared caching.
# ``apps/public/api.py`` keeps its own copy for the bulletin JSON alternates.
_DYNAMIC_CACHE_MAX_AGE = 300

# Valid values for the mandatory ?country= query param on GeoJSON endpoints.
# Stored as uppercase ISO-2 codes; the query param is accepted case-insensitively.
_VALID_GEOJSON_COUNTRIES: frozenset[str] = frozenset(COUNTRY_NAMES)

# Cache lifetime for static region GeoJSON — region geometry is fixture-backed
# and essentially never changes between deploys. Applied via decorator rather
# than a manual header assignment so Django's cache framework tracks it
# correctly and the session middleware cannot append Vary: Cookie.
_GEOJSON_CACHE_MAX_AGE = 86400

# SNOW-896: server-side cache key for the three region-boundary
# FeatureCollections. ``regions_geojson`` was the only map endpoint with no
# server-side cache at all — its three siblings (``ratings``,
# ``bulletin_groupings_geojson``, ``weather_geojson``) have wrapped their
# payloads in ``cache.get_or_set`` since SNOW-191, and this one was left out.
# It is also the largest payload the map fetches (~180 KB for CH) and it sits
# on the boot critical path, so every cold visitor paid a walk over every
# MicroRegion row plus a re-serialisation of every polygon.
#
# The ``v1`` segment is a manual bust: region geometry only changes when an
# operator runs a fixture import, and an import is a deploy, so a stale entry
# cannot outlive a restart. Bump it if the FEATURE SHAPE changes mid-deploy.
# ``v2`` (SNOW-1375): RFC 7946 winding and ``properties.label``.
_REGION_GEOJSON_CACHE_PREFIX = "region-geojson:v2"

# SNOW-902: how long a region-GeoJSON answer may be REUSED WITHOUT ASKING once
# it has gone stale — the HTTP ``stale-while-revalidate`` window. Same value as
# the server-side memo above and a different job: that one bounds how often the
# DB is walked, this one bounds how long a BROWSER may show an answer it has
# not checked. They are written separately because they answer to different
# pressures and only coincide today.
#
# It used to be the ``max-age``, with no validator on the response, and that
# pair has no way back: a browser holding one of these responses cannot ask
# whether it is still current, so it does not ask for a day. See
# ``_geojson_response``.
_GEOJSON_STALE_WHILE_REVALIDATE = 86400


def _region_geojson_cache_key(tier: str, country: str) -> str:
    """Build the cache key for one boundary tier and country.

    Args:
        tier: ``micro``, ``major`` or ``sub`` — the three FeatureCollections.
        country: The validated upper-case ISO-2 code.

    Returns:
        The cache key string.

    """
    return f"{_REGION_GEOJSON_CACHE_PREFIX}:{tier}:{country.lower()}"


# Cache lifetime for settled (past the fetcher's earliest-mutable-date
# threshold) bulletin-groupings responses (SNOW-526). Settled geometry is
# immutable on the normal ingest path, but a manual
# ``fetch_bulletins --force`` can still rewrite history, so this is bounded
# well below the year-long max-age used for historic bulletin pages — see
# docs/decisions/date-aware-cache-policy.md. Offline availability comes
# from the service worker's Cache Storage entry, which ignores max-age
# entirely, so nothing is lost by keeping this short.
#
# The purge (SNOW-1001) is the one of the three that only ever REMOVES
# features, so a client holding a settled entry from before it keeps
# drawing an outline the server no longer sends. That outline duplicates
# ``regions-line`` by definition — it is the degenerate shape the purge
# exists to stop drawing — so the stale artefact is visually what the
# client saw anyway, and the bound above is the whole mitigation. Nothing
# here invalidates Cache Storage: entries rotate on the worker's own
# version bump or an LRU trim.
_SETTLED_CACHE_MAX_AGE = 604800  # 7 days

# Memoisation window for ``apps.bulletins.services.settled.earliest_mutable_date()``
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
# Shorter than _DYNAMIC_CACHE_MAX_AGE because a new report should surface
# promptly.
_COMMUNITY_CACHE_MAX_AGE = 120

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


def _geojson_response(
    request: HttpRequest, payload: dict[str, Any], *, empty: bool = False
) -> HttpResponse:
    """
    Serialise a region-GeoJSON payload with an ETag and a revalidatable policy.

    SNOW-902. The three region tiers and the per-region tile blob used to be
    decorated ``@cache_control(public=True, max_age=86400)`` and nothing else,
    which is a promise with no way back: with no validator on the response, a
    browser holding one cannot ASK whether it is still current, so it does not
    ask for a day.

    Three things change here, and only the last is about staleness:

    - an **ETag** over the serialised body, so a client that wants to check can
      be told "unchanged" in a couple of hundred bytes instead of being sent
      the geometry again. Compared weakly, per RFC 9110 for GET: Django's
      ``GZipMiddleware`` (production, perf) marks a compressed response's ETag
      weak, so the next request offers ``W/"…"`` for a validator this view
      issued strong, and a byte comparison would answer 200 with the whole
      payload every time;
    - ``max-age`` drops to ``_DYNAMIC_CACHE_MAX_AGE`` with the old day-long
      lifetime moving to ``stale-while-revalidate``. The cached copy is still
      served instantly for a day — the visitor waits for nothing — but the
      cache refreshes behind that answer, so a deployment that redraws a
      boundary reaches a returning visitor in minutes rather than tomorrow;
    - an **empty FeatureCollection gets no stale window at all**. An empty
      answer does not mean "this country has no regions", it means "this
      deployment has no geometry for it yet" — a fixture not loaded, a
      migration not run, a backfill still to come. Pinning that for a day is
      how a database that gained the FR/AT/IT fixtures went on drawing Swiss
      outlines alone until the entry expired, with a hard reload no help
      (these feeds are re-issued by the service worker, which does not carry
      the reload's cache mode). ``_cached_region_payload`` applies the same
      rule to the server-side memo, which would otherwise pin it first.

    Args:
        request: The incoming request, read for ``If-None-Match``.
        payload: The FeatureCollection (or tile blob) to serialise.
        empty: True when the payload carries no geometry, and so must not get
            a stale window. Passed by the caller rather than sniffed from the
            payload, because "empty" is not the same shape in every one —
            ``region_basemap_tiles`` 404s instead of returning a blank blob,
            so it never takes this branch.

    Returns:
        A ``JsonResponse``, or ``HttpResponseNotModified`` when the client's
        ``If-None-Match`` already matches. Both carry the ETag and the cache
        policy.

    """
    response: HttpResponse = JsonResponse(payload)
    etag = f'"{hashlib.blake2b(response.content, digest_size=16).hexdigest()}"'
    if _if_none_match_matches(request, etag):
        # A 304 repeats the validator and the policy below — a bare 304 would
        # leave the client holding an entry it cannot check next time.
        response = HttpResponseNotModified()
    response["ETag"] = etag
    if empty:
        patch_cache_control(response, public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
    else:
        patch_cache_control(
            response,
            public=True,
            max_age=_DYNAMIC_CACHE_MAX_AGE,
            stale_while_revalidate=_GEOJSON_STALE_WHILE_REVALIDATE,
        )
    return response


def _if_none_match_matches(request: HttpRequest, etag: str) -> bool:
    """
    Whether the request's ``If-None-Match`` covers ``etag``, compared weakly.

    RFC 9110 §13.1.2: ``If-None-Match`` on a GET uses the WEAK comparison
    function, so ``W/"abc"`` and ``"abc"`` are a match. That is not pedantry
    here — ``GZipMiddleware`` rewrites this view's strong ETag to the weak
    form when it compresses, which is every production request that accepts
    gzip, so a strict comparison would never match anything the deployed site
    hands out.

    Args:
        request: The incoming request.
        etag: The validator this view generated, quoted and strong.

    Returns:
        True when the client already holds this representation.

    """
    header = request.headers.get("If-None-Match", "")
    if not header:
        return False
    candidates = {value.strip() for value in header.split(",")}
    if "*" in candidates:
        return True
    unweighted = {
        value[2:] if value.startswith("W/") else value for value in candidates
    }
    return etag in unweighted


def _cached_region_payload(
    tier: str, country: str, build: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """
    Memoise one tier's payload for a country, briefly when it has no geometry.

    SNOW-896 put these payloads behind a day-long server-side cache. SNOW-902
    adds the same exception the HTTP policy makes: an EMPTY FeatureCollection
    is not a durable fact about a country, it is a fact about this deployment
    not having its geometry yet — and memoising that for a day would pin the
    blank map a layer below where any cache header could reach it.

    Args:
        tier: ``micro``, ``major`` or ``sub``.
        country: The validated upper-case ISO-2 code.
        build: Builds the payload on a miss.

    Returns:
        The FeatureCollection, from cache or freshly built.

    """
    key = _region_geojson_cache_key(tier, country)
    cached = cache.get(key)
    if cached is not None:
        return cast(dict[str, Any], cached)
    payload = build()
    timeout = (
        _DYNAMIC_CACHE_MAX_AGE if not payload["features"] else _GEOJSON_CACHE_MAX_AGE
    )
    cache.set(key, payload, timeout=timeout)
    return payload


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

    ``Vary: Cookie`` stays absent because nothing on this path reads
    ``request.user`` or the session. (Until SNOW-1182 that also took an
    exemption from PostHog's middleware, SNOW-299.)

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
    # SNOW-544: the prefetch is filtered to kind=RESORT so lift-less touring
    # terrain never reaches a surface that presents its rows as resorts.
    regions = MicroRegion.objects.prefetch_related(
        Prefetch("resorts", queryset=Resort.objects.resorts())
    ).all()
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
    ``needs_review``, ``tier``. Resorts with no pin are skipped; the
    coordinate is ``Resort.pin_coordinates`` — the pin ``Location``, or
    transitionally the mirrored columns of a resort not yet backfilled
    (SNOW-1144; SNOW-1147 removes the fallback).

    ``id`` is the resort's **slug**, not its primary key (SNOW-796,
    ``docs/decisions/no-integer-pks-in-urls.md``): this feed is public and
    cacheable, so anything it emits is exported. The slug is also what the
    map hands to the place sheet (``public:resort_sheet_partial``) and what
    ``favourites.geojson`` carries as ``resort_slug``, so the two feeds share
    one key.

    SNOW-543: ``tier`` (``CORE`` / ``STANDARD`` / ``MINOR``) is the curated
    map-prominence verdict, and the public map sizes its pins from it. This
    response is shared by the public map and the edit panel and cached for
    5 minutes, so the property is added once here rather than fetched
    per-pin.

    Always available (not DEBUG-gated) — the public map will use this
    layer once enough resorts are placed to be worth showing.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload.

    """
    features: list[dict[str, Any]] = []
    # SNOW-544: kind=RESORT only — touring terrain is real avalanche terrain
    # kept for its bulletin relevance, but drawing it as a resort pin would
    # tell the user a lift-served area exists where none does.
    for resort in (
        Resort.objects.resorts()
        .geocoded()
        .select_related("region", "location")
        .order_by("name")
        .iterator()
    ):
        # SNOW-1144: the pin, read through the one accessor (the Location,
        # or transitionally the mirrored columns until SNOW-1147).
        # ``geocoded()`` guarantees one; the guard only narrows the type.
        pin = resort.pin_coordinates
        if pin is None:
            continue
        latitude, longitude = pin
        # GeoJSON ordering: [longitude, latitude] per RFC 7946.
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [longitude, latitude],
                },
                "properties": {
                    "id": resort.slug,
                    "name": resort.name,
                    "region_id": resort.region.region_id,
                    "needs_review": resort.needs_review,
                    # SNOW-543: drives pin radius in ``installResortsLayer``
                    # (static/js/map.js). Always present — a MapLibre
                    # ``match`` expression on a missing property falls to its
                    # default, which would silently flatten every pin back to
                    # one size.
                    "tier": resort.tier,
                },
            }
        )
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )


@cache_control(public=True, max_age=_DYNAMIC_CACHE_MAX_AGE)
@vary_on_headers("Accept-Encoding")
def weather_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of weather at every public ``Location``.

    Powers the map's Weather overlay. **One feed, one anchor** — SNOW-761
    replaced two endpoints (a resort-anchored ``forecast-weather.geojson``
    and a region-anchored ``region-weather.geojson``) with this. A region
    centroid is a ``Location`` like any other now, so there is no tier to
    choose between and nothing for a zoom threshold to switch.

    **Privacy contract.** Filtered by ``Location.objects.public()`` — the
    curated estate, reachable from a ``ResortLocation`` or a
    ``MicroRegion.centroid_location``. Never ``active()``, which also
    includes the locations a ``Favourite`` reaches: that is the set worth
    paying Open-Meteo for, not the set anyone may see, and serving it here
    would put a stranger's private pin and its coordinates on a public map.
    The retired resort-anchored feed excluded favourite-only points for
    exactly this reason and the contract survives.

    Response shape::

        {
          "type": "FeatureCollection",
          "features": [
            {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [7.5, 46.1]},
              "properties": {
                "short_id": "Ab3dE_fGh1J",
                "name": "Mont Fort",
                "elevation_m": 3328.0,
                "days": {"2026-08-30": {"code": 71, "tmax": 4.0}, ...}
              }
            },
            ...
          ]
        }

    Four properties and no more. The map draws a condition icon and a
    label; the icon is resolved from ``code`` in ``map_weather_core.js``
    and the label is ``tmax`` beside ``elevation_m``. There is no ``kind``,
    no ``resort_id`` and no ``region_id``, because nothing on the map reads
    them — a feature carrying fields no layer touches is payload every
    visitor downloads and no visitor sees.

    ``short_id`` is ``Location.short_id``, never the primary key (SNOW-797,
    ``docs/decisions/no-integer-pks-in-urls.md``): this feed is
    unauthenticated and cacheable, so anything it emits is public to every
    visitor. It is what the map hands to ``weather_detail`` on a tap.

    ``elevation_m`` is nullable: it is resolved out of band, so a
    freshly-imported location has none yet and the label falls back to the
    temperature alone rather than printing a gap.

    ``days`` covers today and the days after it, read off **one** ``Weather``
    row per location — its ``observed_on`` plus its ``forecast`` column.
    That is one row per location, not one per location per day, which is
    what makes the whole window one query. A location with no row for today
    still gets a feature, with an empty ``days``: the map filters it out at
    draw time, and an absent feature would be indistinguishable from a
    location we do not know about.

    Server-side ``cache.get_or_set`` bounds DB hits to one per cache
    window. Nothing on this path may read ``request.user`` or the session:
    that would make ``SessionMiddleware`` append ``Vary: Cookie`` and
    defeat the public caching.

    ``generated_at`` (for the freshness headers) is the OLDEST ``fetched_at``
    across the payload's rows, following ``_card_freshness``'s rule that a
    record mixing several constituents is only as fresh as its stalest
    one. ``unsafe_after`` is ``None`` — weather is not safety-critical, so
    the freshness state saturates at "stale" and never escalates.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse carrying a GeoJSON FeatureCollection.

    """
    observed_on = timezone.localdate()
    # v2: SNOW-797 renamed ``location_id`` to ``short_id`` in the payload,
    # and a v1 entry cached across the deploy would hand the map a shape
    # its tap handler no longer reads. v3: SNOW-1343 added each day's
    # ``icon`` — the map no longer derives one from ``code`` — so a v2
    # entry cached across the deploy would draw no symbol at all.
    cache_key = f"weather-geojson:v3:{observed_on.isoformat()}"
    payload, generated_at = cast(
        "tuple[dict[str, Any], datetime | None]",
        cache.get_or_set(
            cache_key,
            lambda: _build_weather_payload(observed_on),
            timeout=_DYNAMIC_CACHE_MAX_AGE,
        ),
    )
    response = JsonResponse(payload)
    apply_freshness_headers(
        response,
        generated_at=generated_at or timezone.now(),
        unsafe_after=None,
    )
    return response


def _build_weather_payload(
    observed_on: date,
) -> tuple[dict[str, Any], datetime | None]:
    """
    Build the weather FeatureCollection from the DB.

    Two queries and no N+1: one over ``Location.objects.public()``, then one
    bulk ``Weather`` fetch for the week from ``observed_on`` across those
    locations, grouped by ``location_id`` in Python. Each day of the week is
    its own row (SNOW-1337), so the week is that one query.

    The row lookup is keyed on the ``(location, observed_on)`` unique
    constraint — never ordered by ``fetched_at``, since today's row is
    updated in place rather than appended. ``hourly`` is deferred
    (SNOW-1343): the map draws a day's symbol, never its hours, and the
    column is the bulk of each row.

    Args:
        observed_on: The day whose rows to read — today, in the site's
            timezone.

    Returns:
        A ``(payload, generated_at)`` pair. ``generated_at`` is the oldest
        ``fetched_at`` across the rows, or ``None`` when there are none.

    """
    locations = list(Location.objects.public().order_by("id"))
    week: dict[int, list[Weather]] = defaultdict(list)
    for row in (
        Weather.objects.filter(location__in=locations)
        .window(observed_on, FORECAST_DAYS)
        .defer("hourly")
    ):
        week[row.location_id].append(row)
    # A place's map entry leads with today's row; one with no row for today
    # has no entry at all, whatever later rows it holds.
    rows = {
        location_id: days
        for location_id, days in week.items()
        if days[0].observed_on == observed_on
    }
    oldest_fetched_at: datetime | None = None
    features: list[dict[str, Any]] = []
    for location in locations:
        days = rows.get(location.pk, [])
        today = days[0] if days else None
        if today is not None and (
            oldest_fetched_at is None or today.fetched_at < oldest_fetched_at
        ):
            oldest_fetched_at = today.fetched_at
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    # GeoJSON ordering: [longitude, latitude] per RFC 7946.
                    "type": "Point",
                    "coordinates": [location.longitude, location.latitude],
                },
                "properties": {
                    "short_id": location.short_id,
                    "name": location.name,
                    "elevation_m": location.elevation_m,
                    "days": build_point_weather_days(today, days[1:]),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}, oldest_fetched_at


@vary_on_headers("Accept-Encoding")
def regions_geojson(request: HttpRequest) -> HttpResponse:
    """
    Return a FeatureCollection of L4 region polygons for a single country.

    Requires a ``?country=ch|fr|at|it`` query parameter (case-insensitive).
    Returns 400 on an unrecognised value. Each feature carries
    ``properties.id``, ``properties.name``, and ``properties.country``.
    Regions without a boundary are skipped.

    SNOW-521: each feature also carries ``properties.download`` — this
    region's precomputed offline-basemap size summary (``{count, mb,
    over_ceiling, centre_tile}``, ``apps.regions.services.basemap_tiles.
    blob_summary``) — omitted entirely when ``basemap_download`` is
    unset. The ``#region-readout`` chip's download icon reads this
    straight off the always-loaded ``regions.geojson`` payload with no
    extra fetch; the full blob (incl. ``z`` tile ranges) is fetched on
    demand from ``region_basemap_tiles`` only when the user clicks it.

    SNOW-896: the payload is also cached SERVER-side for
    ``_GEOJSON_CACHE_MAX_AGE``, keyed on country. This was the only map
    endpoint without one — its siblings have had ``cache.get_or_set`` since
    SNOW-191 — and it is both the largest payload the map fetches and on the
    boot critical path. Note the knock-on: ``covered_region_ids()`` is
    separately cached for an hour, so a region gaining its first
    ``RegionDayRating`` now takes up to 24 hours rather than one to show as
    covered. That is the right trade for a map (nobody needs it inside the
    hour) but it is a real change, not an oversight.

    SNOW-902: the response policy comes from ``_geojson_response``, not a
    decorator — an ETag, a five-minute ``max-age`` over a day-long
    ``stale-while-revalidate``, and neither the stale window nor the day-long
    server-side memo on an empty answer. ``@vary_on_headers`` stays, and is
    what prevents Django's ``SessionMiddleware`` from appending
    ``Vary: Cookie`` on the response, as long as nothing on the path reads
    ``request.user`` or the session.

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

    # SNOW-896: validate BEFORE the cache lookup. An unrecognised country must
    # 400 without becoming a cache key of its own.
    payload = _cached_region_payload(
        "micro", country_param, lambda: _build_micro_regions_payload(country_param)
    )
    return _geojson_response(request, payload, empty=not payload["features"])


def _build_micro_regions_payload(country_param: str) -> dict[str, Any]:
    """Build the L4 FeatureCollection for one country.

    Split out of ``regions_geojson`` by SNOW-896 so the view is
    validate-then-``get_or_set``, matching the shape ``ratings`` and
    ``bulletin_groupings_geojson`` already use.

    Args:
        country_param: A validated upper-case ISO-2 country code.

    Returns:
        The FeatureCollection dict.

    """
    features: list[dict[str, Any]] = []
    covered = covered_region_ids()
    qs = (
        MicroRegion.objects.filter(
            subregion__major__country=country_param,
            subregion__major__display_on_map=True,
            boundary__isnull=False,
        )
        .select_related("subregion__major", "centroid_location")
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
        # SNOW-1375: where the map's level label sits — the region's centroid
        # Location, which link_region_centroid_locations derives from the
        # boundary. A region without one simply has no label.
        centroid = region.centroid_location
        if centroid is not None:
            properties["label"] = [
                round(centroid.longitude, 5),
                round(centroid.latitude, 5),
            ]
        features.append(
            {
                "type": "Feature",
                # SNOW-1375: RFC 7946 winding, so the map's inside edge band
                # (a positive line-offset, an inset) lands inside every region.
                "geometry": rfc7946_rewind(region.boundary),
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


@vary_on_headers("Accept-Encoding")
def major_regions_geojson(request: HttpRequest) -> HttpResponse:
    """
    Return a FeatureCollection of L1 EAWS major regions for a single country.

    Requires a ``?country=ch|fr|at|it`` query parameter (case-insensitive).
    Returns 400 on an unrecognised value. Each feature carries
    ``properties.prefix``, ``properties.name_en``, and ``properties.country``.
    Entries without a boundary are skipped.

    SNOW-521: offline-basemap download is a **MicroRegion-only** feature
    — this tier never carries a ``properties.download`` key.

    SNOW-896: cached server-side for ``_GEOJSON_CACHE_MAX_AGE``, keyed on
    country, like its two siblings. This tier is lazy-loaded rather than on
    the boot path, so the win is smaller — but leaving two of three uncached
    would recreate exactly the inconsistency that ticket closed.

    ``@vary_on_headers`` prevents Django's ``SessionMiddleware`` from
    appending ``Vary: Cookie``; the response policy itself comes from
    ``_geojson_response`` (SNOW-902).  See ``regions_geojson`` for the full
    rationale.

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

    payload = _cached_region_payload(
        "major", country_param, lambda: _build_major_regions_payload(country_param)
    )
    return _geojson_response(request, payload, empty=not payload["features"])


def _build_major_regions_payload(country_param: str) -> dict[str, Any]:
    """Build the L1 FeatureCollection for one country.

    Args:
        country_param: A validated upper-case ISO-2 country code.

    Returns:
        The FeatureCollection dict.

    """
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
    return {"type": "FeatureCollection", "features": features}


@vary_on_headers("Accept-Encoding")
def sub_regions_geojson(request: HttpRequest) -> HttpResponse:
    """
    Return a FeatureCollection of L2 EAWS sub-regions for a single country.

    Requires a ``?country=ch|fr|at|it`` query parameter (case-insensitive).
    Returns 400 on an unrecognised value. Each feature carries
    ``properties.prefix``, ``properties.name_en``, and ``properties.country``.
    Entries without a boundary are skipped.

    SNOW-521: offline-basemap download is a **MicroRegion-only** feature
    — this tier never carries a ``properties.download`` key.

    SNOW-896: cached server-side for ``_GEOJSON_CACHE_MAX_AGE``, keyed on
    country, like its two siblings. This tier is lazy-loaded rather than on
    the boot path, so the win is smaller — but leaving two of three uncached
    would recreate exactly the inconsistency that ticket closed.

    ``@vary_on_headers`` prevents Django's ``SessionMiddleware`` from
    appending ``Vary: Cookie``; the response policy itself comes from
    ``_geojson_response`` (SNOW-902).  See ``regions_geojson`` for the full
    rationale.

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

    payload = _cached_region_payload(
        "sub", country_param, lambda: _build_sub_regions_payload(country_param)
    )
    return _geojson_response(request, payload, empty=not payload["features"])


def _build_sub_regions_payload(country_param: str) -> dict[str, Any]:
    """Build the L2 FeatureCollection for one country.

    Args:
        country_param: A validated upper-case ISO-2 country code.

    Returns:
        The FeatureCollection dict.

    """
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
    return {"type": "FeatureCollection", "features": features}


@vary_on_headers("Accept-Encoding")
@require_GET
def region_basemap_tiles(request: HttpRequest) -> HttpResponse:
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
    data, so it takes the same ``_geojson_response`` policy (SNOW-902) and the
    same ``@vary_on_headers`` treatment as the
    geojson endpoints. It never reaches that helper's empty branch: a region
    with no computed blob 404s above rather than returning a blank one. See
    ``regions_geojson`` for the full ``Vary: Cookie`` rationale.

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

    return _geojson_response(request, region.basemap_download)


# Cache key segment for the two reference indexes behind ``/api/area-content/``.
# ``v1`` so a change to either index's SHAPE (a field added to a row, an
# ordering rule) cannot be answered from an entry built by the previous
# deploy, following ``weather_geojson``'s ``v2`` precedent.
_AREA_CONTENT_INDEX_CACHE_PREFIX = "area-content-index:v1"


@vary_on_headers("Accept-Encoding")
@require_GET
def area_content(request: HttpRequest) -> HttpResponse:
    """
    Return the micro-regions and weather locations inside one rectangle.

    SNOW-953. What a downloaded area's boundary contains, so a client
    planning an offline download does not have to work it out from every
    country's region outlines — 764 KB over the wire to discover roughly
    55 KB of pages, on the thin connection the feature exists to serve.

    ``?bbox=west,south,east,north`` in degrees, and nothing else. Date-free
    on purpose: the client already holds its own day window (today to the
    last published day ahead — forward only since SNOW-1312) and composes
    the bulletin URLs itself, so this
    answer is the same for every caller and every day — which is what
    makes it cacheable.

    Response shape::

        {"regions": [{"id": "CH-4115", "slug": "brunig-lungern"}, ...],
         "weather": [{"short_id": "Ab3dE_fGh1J"}, ...],
         "resorts": [{"slug": "verbier"}, ...]}

    SNOW-1318 added ``resorts``: each resort the map pins inside the
    rectangle, by the same point rule as ``weather``, so a download warms
    each one's place sheet for every forward day.

    The selection rule is unchanged by the move and is the one
    ``docs/decisions/inside-the-boundary-is-complete.md`` states: an
    edge-inclusive rectangle over each region's bounding box, never real
    geometry, deliberately over-inclusive. What the move removes is the
    failure mode SNOW-931 closed — the candidate set being whatever
    countries the client happened to have loaded — which stops being
    possible here, because the candidate set is the whole mapped estate on
    every request.

    Both indexes are fixture-backed reference data (a region's boundary
    moves on a deploy; the location estate moves on an import), so each is
    memoised for ``_GEOJSON_CACHE_MAX_AGE`` and the request itself costs no
    query in the common case — an EMPTY index for a much shorter one, see
    ``_cached_area_content_index``. The response takes ``_geojson_response``'s
    ETag and revalidatable policy — it is not GeoJSON, but it is exactly
    the static-reference kind of answer that helper was written for — and
    the same ``@vary_on_headers`` treatment
    the geojson endpoints get, or ``Vary: Cookie`` defeats the caching.
    See ``regions_geojson`` for the full rationale.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with the payload above, or 400 for a bbox that is
        missing, non-numeric, the wrong length, out of range or inverted.

    """
    bbox = _parse_bbox_param(request.GET.get("bbox", ""))
    if bbox is None:
        return JsonResponse({"error": "invalid_bbox"}, status=400)

    regions = cast(
        "list[RegionBox]",
        _cached_area_content_index("regions", micro_region_index),
    )
    locations = cast(
        "list[WeatherPoint]",
        _cached_area_content_index("weather", weather_location_index),
    )
    resorts = cast(
        "list[ResortPoint]",
        _cached_area_content_index("resorts", resort_index),
    )
    payload = build_area_content(
        bbox, regions=regions, locations=locations, resorts=resorts
    )
    # The empty branch turns on the INDEX, not on the answer. An area over
    # open water legitimately contains nothing, and that is a durable fact
    # about the rectangle; an estate with no regions in it at all is a fact
    # about this deployment not having its geometry yet, and pinning that
    # for a day is what the branch exists to avoid — here it would pin
    # "your download needs no bulletins" over every rectangle on Earth.
    return _geojson_response(request, payload, empty=not regions)


@require_GET
@cache_control(private=True, no_store=True)
def area_items(request: HttpRequest) -> HttpResponse:
    """
    Return the requester's own trips and pins inside one rectangle.

    SNOW-965. The private twin of :func:`area_content`: a downloaded area
    should carry the trips the reader is on and the forecasts their pins
    borrow, and neither is public content. ``?bbox=west,south,east,north``
    in degrees, validated by the same parser.

    Response shape::

        {"trips": [{"uuid": "…", "name": "…"}, ...],
         "pins": [{"uuid": "…", "name": "…",
                   "forecast": {"short_id": "…"} | null}, ...]}

    SNOW-1308: ``name`` is the label each thing is listed under on the
    ``/offline/`` panels — a trip's own or its route's, a pin's own or its
    resort's, ``""`` for an unnamed pin.

    An anonymous caller gets ``200`` with both lists empty and costs no
    query: someone signed out has nothing of their own to carry, and a
    ``403`` would make an existing area's refresh read as a failure.

    **Never cached.** ``private, no-store`` and ``Vary: Cookie``, and no
    ETag: the answer is per-user, and a shared or service-worker cache
    holding one reader's trips must never answer for another. The client
    composes the urls it fetches from this answer; this answer itself is
    not one of them.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with the payload above, or 400 for a bbox that is
        missing, non-numeric, the wrong length, out of range or inverted.

    """
    bbox = _parse_bbox_param(request.GET.get("bbox", ""))
    if bbox is None:
        response = JsonResponse({"error": "invalid_bbox"}, status=400)
    else:
        # SNOW-1308: and it names its viewer (``X-Snowdesk-Principal``), so
        # a client stamps the names it records with the account the server
        # answered for — not with a page's render-time ``pwa-user-id``,
        # which a sign-in in another tab can leave stale. Anonymous sends
        # no header.
        response = attest_principal(
            JsonResponse(
                build_area_items(request.user, bbox, today=timezone.localdate())
            ),
            request,
        )
    patch_vary_headers(response, ["Cookie"])
    return response


def _cached_area_content_index(name: str, build: Callable[[], list[Any]]) -> list[Any]:
    """
    Memoise one reference index, briefly when it came back empty.

    The same exception ``_cached_region_payload`` makes, for the same
    reason and one layer further in. An empty index is not a durable fact
    about the estate — it is a fixture not loaded, a migration not run, a
    backfill still to come — and it is more dangerous here than it is
    there: a blank map is visibly blank, while an empty plan looks like a
    complete one, so a client would fetch nothing, land everything it
    fetched, and stamp the area complete for the day the entry lived.

    Args:
        name: ``regions``, ``weather`` or ``resorts`` — the index to memoise.
        build: Builds the index on a miss.

    Returns:
        The index, from cache or freshly built.

    """
    key = f"{_AREA_CONTENT_INDEX_CACHE_PREFIX}:{name}"
    cached = cache.get(key)
    if cached is not None:
        return cast("list[Any]", cached)
    index = build()
    timeout = _DYNAMIC_CACHE_MAX_AGE if not index else _GEOJSON_CACHE_MAX_AGE
    cache.set(key, index, timeout=timeout)
    return index


def _parse_bbox_param(raw: str) -> list[float] | None:
    """
    Parse and validate a ``west,south,east,north`` query parameter.

    Rejects an inverted box (``west > east`` or ``south > north``) rather
    than normalising it: the two rectangles a client could have meant are
    different sets of bulletins, and quietly picking one would plan a
    download nobody asked for.

    Args:
        raw: The raw ``?bbox=`` value, possibly empty.

    Returns:
        ``[west, south, east, north]``, or ``None`` when the value is
        missing, non-numeric, not four parts, out of range or inverted.

    """
    parts = [part.strip() for part in raw.split(",")] if raw else []
    if len(parts) != 4:
        return None
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        return None
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return None
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        return None
    if west > east or south > north:
        return None
    return [west, south, east, north]


# SNOW-1276: the widest rectangle /api/ski-network.geojson answers, in
# degrees each way — a little more than a phone's view at z11. A wider ask
# is refused rather than truncated: the network is drawn and snapped to
# only when zoomed in, and a country's worth of lines is megabytes.
SKI_NETWORK_MAX_SPAN_DEG = 0.5

# The most segments one answer carries; a dense half-degree of the Valais
# holds about two thousand. Past it the answer says ``truncated``.
SKI_NETWORK_MAX_FEATURES = 4000


@vary_on_headers("Accept-Encoding")
@require_GET
def ski_network_geojson(request: HttpRequest) -> HttpResponse:
    """
    Return swisstopo's ski-touring segments that cross one rectangle.

    SNOW-1276. The vector network a drawn route snaps to (SNOW-1277) —
    ``SkiSegment.objects.skiable()``, so the snowshoe-only lines are left
    out — selected by each segment's bounding box, edge-inclusive and
    over-inclusive, as ``area_content`` selects regions.

    ``?bbox=west,south,east,north`` in degrees, at most
    ``SKI_NETWORK_MAX_SPAN_DEG`` each way. Response shape::

        {"type": "FeatureCollection",
         "truncated": false,
         "features": [{"type": "Feature", "id": "<service uuid>",
                       "geometry": {"type": "MultiLineString", ...},
                       "properties": {"segment_id": 123}}, ...]}

    ``?overview=1`` instead of a bbox answers the whole network at once
    (SNOW-1322), for draw mode at z9-z11: every line thinned as the tours
    overview is (``network_overview``), with no id and no properties,
    because it is drawn and never snapped to.

    Reference data, the same for everyone and changed about once a year,
    so it takes ``_geojson_response``'s ETag and revalidatable policy. The
    licence is swisstopo's OGD; the map credits "© swisstopo".

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with the payload above, or 400 for a bbox that is
        missing, malformed or wider than the cap.

    """
    if request.GET.get("overview") == "1":
        overview = network_overview()
        return _geojson_response(
            request,
            {"type": "FeatureCollection", "truncated": False, "features": overview},
            empty=not overview,
        )
    bbox = _parse_bbox_param(request.GET.get("bbox", ""))
    if bbox is None:
        return JsonResponse({"error": "invalid_bbox"}, status=400)
    west, south, east, north = bbox
    if (
        east - west > SKI_NETWORK_MAX_SPAN_DEG
        or north - south > SKI_NETWORK_MAX_SPAN_DEG
    ):
        return JsonResponse({"error": "bbox_too_large"}, status=400)
    rows = list(
        SkiSegment.objects.skiable()
        .intersecting(west, south, east, north)
        .order_by("segment_id")
        .only("source_uuid", "segment_id", "geometry")[: SKI_NETWORK_MAX_FEATURES + 1]
    )
    features = [
        {
            "type": "Feature",
            "id": str(row.source_uuid),
            "geometry": row.geometry,
            "properties": {"segment_id": row.segment_id},
        }
        for row in rows[:SKI_NETWORK_MAX_FEATURES]
    ]
    payload = {
        "type": "FeatureCollection",
        "truncated": len(rows) > SKI_NETWORK_MAX_FEATURES,
        "features": features,
    }
    return _geojson_response(request, payload, empty=not features)


# The most tours one answer carries. A half-degree around the Silvretta holds
# about four hundred; past the cap the answer says ``truncated``.
SKI_TOURS_MAX_FEATURES = 1500


def _summit_features(chosen: Iterable[Summit]) -> list[dict[str, Any]]:
    """Return these summits as GeoJSON point Features.

    Args:
        chosen: The summits to draw.

    Returns:
        One Feature per summit, its id ``summit:<key>``.

    """
    return [
        {
            "type": "Feature",
            "id": f"summit:{summit.key}",
            "geometry": {"type": "Point", "coordinates": [summit.lon, summit.lat]},
            "properties": summit.as_properties(),
        }
        for summit in chosen
    ]


@vary_on_headers("Accept-Encoding", "Accept-Language", "Cookie")
@require_GET
def ski_tours_geojson(request: HttpRequest) -> HttpResponse:
    """
    Return SAC's ski tours, and the summits they go to, inside one rectangle.

    SNOW-1278. What "Find a route" draws and is tapped on: each tour's line
    with what its list row says, and a point per summit or hut (the
    handles, apps/routes/services/ski_tours.py). ``?bbox=`` as for
    ``ski_network_geojson``, with the same cap. Response shape::

        {"type": "FeatureCollection",
         "truncated": false,
         "features": [
           {"type": "Feature",
            "geometry": {"type": "LineString", ...},
            "properties": {"kind": "tour", "uuid": "…", "summit": "<key>",
                           "name": "Von der Silvrettahütte SAC",
                           "target_name": "Signalhorn (GR)",
                           "target_altitude_m": 3207, "grade": "PD+",
                           "ascent_m": 870, "descent_m": 870,
                           "time": "3 h 30", "climbing": true}},
           {"type": "Feature", "id": "summit:<key>",
            "geometry": {"type": "Point", ...},
            "properties": {"kind": "summit" | "hut", "key": "…",
                           "name": "Signalhorn (GR)", "altitude_m": 3207,
                           "up": 2, "down": 2}}, ...]}

    Names are in the request's language where SAC has one, hence the Vary.

    ``?overview=1`` instead of a bbox answers every tour at once, for a map
    zoomed out past the per-view load (SNOW-1285): each line thinned
    (``tour_overview``) and carrying only ``kind``, ``uuid``, ``summit`` and
    ``climbing``, with every summit as above. ``truncated`` is always false.

    A tour is one Feature per leg (SNOW-1310, ``leg_parts``): ``climbing``
    is the leg's direction, read from the tour's stored render as a
    route's legs are (SNOW-1309), and null for a tour with no terrain yet,
    drawn as SAC's own line.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with the payload above, or 400 for a bad bbox.

    """
    if request.GET.get("overview") == "1":
        overview = [*tour_overview(), *_summit_features(summits().values())]
        return _geojson_response(
            request,
            {"type": "FeatureCollection", "truncated": False, "features": overview},
            empty=not overview,
        )
    bbox = _parse_bbox_param(request.GET.get("bbox", ""))
    if bbox is None:
        return JsonResponse({"error": "invalid_bbox"}, status=400)
    west, south, east, north = bbox
    if (
        east - west > SKI_NETWORK_MAX_SPAN_DEG
        or north - south > SKI_NETWORK_MAX_SPAN_DEG
    ):
        return JsonResponse({"error": "bbox_too_large"}, status=400)
    rows = list(
        drawn_tours(
            SkiTour.objects.intersecting(west, south, east, north).order_by("sac_id")
        )[: SKI_TOURS_MAX_FEATURES + 1]
    )
    # SNOW-1310: a Feature per leg, as a route is drawn.
    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": line},
            "properties": {
                "kind": "tour",
                "uuid": str(row.source_uuid),
                "summit": tour_key(row),
                "name": localised(row.names, row.name),
                "target_name": row.target_name,
                "target_altitude_m": row.target_altitude_m,
                "grade": localised(row.grades, row.grade) or row.grade,
                "ascent_m": row.ascent_m,
                "descent_m": row.descent_m,
                "time": sac_time(row.ascent_time_label),
                "climbing": climbing,
            },
        }
        for row in rows[:SKI_TOURS_MAX_FEATURES]
        for climbing, line in leg_parts(layer_render(row), row.geometry)
    ]
    features.extend(
        _summit_features(
            summit
            for summit in summits().values()
            if west <= summit.lon <= east and south <= summit.lat <= north
        )
    )
    payload = {
        "type": "FeatureCollection",
        "truncated": len(rows) > SKI_TOURS_MAX_FEATURES,
        "features": features,
    }
    return _geojson_response(request, payload, empty=not features)


# SNOW-1384: the widest rectangle /api/rest-zones.geojson answers, in degrees
# each way. The layer is drawn from z10, where a 2,000 px desktop view is
# about 1.4 degrees across; a wider ask is refused rather than truncated, as
# ski-network's. All of Switzerland's zones fit REST_ZONES_MAX_FEATURES.
REST_ZONES_MAX_SPAN_DEG = 2.0

# The most zones one answer carries. All of Switzerland holds about 1,040.
REST_ZONES_MAX_FEATURES = 1500

# FOEN's credit, carried in the payload for a client that draws it.
REST_ZONES_ATTRIBUTION = "© BAFU"


@vary_on_headers("Accept-Encoding", "Accept-Language", "Cookie")
@require_GET
def rest_zones_geojson(request: HttpRequest) -> HttpResponse:
    """
    Return FOEN's wildlife rest zones that touch one rectangle, for one day.

    SNOW-1384. The map's "Wildlife rest zones" layer, behind the
    ``route_drawing`` flag: a 404 for a request the flag does not cover.
    Rows are selected by bounding box, edge-inclusive and over-inclusive,
    as ``ski_network_geojson`` selects segments.

    ``?bbox=west,south,east,north`` in degrees, at most
    ``REST_ZONES_MAX_SPAN_DEG`` each way; ``?d=YYYY-MM-DD``, the map's
    day, decides each zone's ``in_force`` (today when absent). Response
    shape::

        {"type": "FeatureCollection",
         "truncated": false,
         "attribution": "© BAFU",
         "features": [{"type": "Feature", "id": "<service uuid>",
                       "geometry": {"type": "MultiPolygon", ...},
                       "properties": {"name": "…", "binding": true,
                                      "restriction": "…",
                                      "period": "21.12. - 30.04.",
                                      "in_force": true, "canton": "GR",
                                      "url": "https://…"}}, ...]}

    The restriction is in the request's language where FOEN has one,
    hence the Vary on Accept-Language; and the flag is read off the
    session, hence the Vary on Cookie, so a shared cache never hands one
    reader's answer to a reader the flag does not cover.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with the payload above, or 400 for a bbox that is
        missing, malformed or wider than the cap, or a malformed ``d``.

    Raises:
        Http404: The ``route_drawing`` flag is off for this request.

    """
    if not flag_is_active(request, "route_drawing"):
        raise Http404("Not found.")
    day = timezone.localdate()
    raw_date = request.GET.get("d")
    if raw_date:
        # Strict YYYY-MM-DD: fromisoformat alone also accepts "20261102".
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            return JsonResponse({"error": "invalid_date"}, status=400)
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            return JsonResponse({"error": "invalid_date"}, status=400)
    bbox = _parse_bbox_param(request.GET.get("bbox", ""))
    if bbox is None:
        return JsonResponse({"error": "invalid_bbox"}, status=400)
    west, south, east, north = bbox
    if east - west > REST_ZONES_MAX_SPAN_DEG or north - south > REST_ZONES_MAX_SPAN_DEG:
        return JsonResponse({"error": "bbox_too_large"}, status=400)
    rows = list(
        RestZone.objects.intersecting(west, south, east, north)
        .order_by("feature_id")
        .only(
            "source_uuid",
            "name",
            "binding",
            "restriction",
            "period",
            "canton",
            "url",
            "geometry",
        )[: REST_ZONES_MAX_FEATURES + 1]
    )
    features = [zone_feature(row, day) for row in rows[:REST_ZONES_MAX_FEATURES]]
    payload = {
        "type": "FeatureCollection",
        "truncated": len(rows) > REST_ZONES_MAX_FEATURES,
        "attribution": REST_ZONES_ATTRIBUTION,
        "features": features,
    }
    return _geojson_response(request, payload, empty=not features)


@vary_on_headers("Accept-Encoding")
@require_GET
def ski_summits_json(request: HttpRequest) -> HttpResponse:
    """
    Return every SAC summit and hut as a row for the map's search.

    SNOW-1294. What search matches a typed name against: the summits the
    Summits layer draws (``summits()``, cached until an import changes the
    tours), without their tours' lines, so the whole list is a few tens of
    kilobytes and is read once, the first time search is focused. A summit
    is not a Location and buys no forecast fetch
    (docs/decisions/snowdesk-decides-where-a-forecast-is-fetched.md).
    Response shape::

        {"summits": [{"key": "piz-buin-3312", "name": "Piz Buin",
                      "altitude_m": 3312, "kind": "summit" | "hut",
                      "lon": 10.12, "lat": 46.84, "tours": 4}, ...]}

    Names are SAC's own and not localised, so the answer varies on nothing
    but its encoding. Anonymous and read-only, like the layer's own feed.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with the payload above, ETagged.

    """
    rows = [
        {
            "key": summit.key,
            "name": summit.name,
            "altitude_m": summit.altitude_m,
            "kind": "hut" if summit.hut else "summit",
            "lon": summit.lon,
            "lat": summit.lat,
            "tours": summit.up + summit.down,
        }
        for summit in summits().values()
    ]
    return _geojson_response(request, {"summits": rows}, empty=not rows)


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

    **An empty ``features`` list is a valid answer, not an error.** Since
    SNOW-1001 a row exists only for a bulletin covering two or more
    boundaried micro-regions, so a day on which every provider in view
    issued one bulletin per region legitimately returns zero features — and
    Météo-France, 1:1 across its whole archive, never contributes one. The
    map draws nothing and that is the correct reading: no provider grouped
    anything that day (see
    ``docs/decisions/a-grouping-outline-asserts-an-aggregation.md``).

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
      ``apps.bulletins.services.settled`` — the earliest date any provider's
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

    ``earliest_mutable_date()`` (``apps.bulletins.services.settled``) runs one DB
    aggregate per registered ``BulletinSource`` — cheap in isolation, but
    ``bulletin_groupings_geojson`` needs the answer on every request
    (including a server-cache hit on the payload itself), so calling it
    unmemoised would add that DB cost to every scrubber-settle fetch.
    Memoised here, at the call site, rather than inside
    ``apps.bulletins.services.settled`` itself — that module stays a pure,
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
    # the previous evening's, which both satisfy ``target_date == X``. Both
    # get a BulletinGrouping row where they aggregate at all (a single-region
    # bulletin gets none — SNOW-1001), so without this filter the endpoint
    # returns two near-identical overlapping outlines for most days —
    # invisible while
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
    provider_name = PROVIDER_BY_COUNTRY.get(region.subregion.major.country, "")

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


def weather_detail_legacy_redirect(
    request: HttpRequest, location_id: int
) -> HttpResponse:
    """301 the pre-SNOW-797 ``/api/weather/<id>/detail/`` to the short-id form.

    Kept because the integer form is what any cached ``weather.geojson``
    payload still names for a few minutes after the deploy, and because
    every changed route keeps a permanent redirect from its integer form
    (``docs/decisions/no-integer-pks-in-urls.md``). Same estate rule as the
    endpoint it redirects to — a private pin's pk is a 404 here too — and
    the ``?date=`` query string rides along.

    Args:
        request: The incoming HTTP request.
        location_id: The Location's primary key, from the legacy URL.

    Returns:
        A 301 to the short-id endpoint, or 404 outside the public estate.

    """
    location = get_object_or_404(Location.objects.public(), pk=location_id)
    url = reverse("api:weather_detail", kwargs={"short_id": location.short_id})
    return _redirect_with_query(request, url)


def _redirect_with_query(request: HttpRequest, url: str) -> HttpResponse:
    """Permanent-redirect to ``url``, carrying the request's query string.

    Args:
        request: The request whose query string to forward.
        url: The redirect target path.

    Returns:
        A 301 response.

    """
    query = request.META.get("QUERY_STRING", "")
    return redirect(f"{url}?{query}" if query else url, permanent=True)


def weather_detail(request: HttpRequest, short_id: str) -> JsonResponse:
    """Return pre-rendered sheet HTML for a tapped weather symbol.

    Response shape::

        {"html": "<...>"}

    Mirrors ``region_summary``'s contract — a server-rendered snippet the
    map injects — but the map puts this one in the weather SHEET rather than
    an anchored popup. **The sheet is the page** (Hugo, 24 Oct 2026): under
    its peek row it renders the location forecast page's own body — the
    week as a day picker opened on the map's day, the selected day's line,
    its meteogram — from the page's own ``_location_forecast_context``. It
    was a one-day card with a "This week →" link to that page until then.

    **Public, and only over the public estate.** ``Location.objects.public()``
    is the same filter :func:`weather_geojson` uses, and it is load-bearing
    twice over: a location outside it has no symbol on the map to tap, so
    accepting one here would answer for a place the map never offered — and
    ``public()`` excludes the locations a ``Favourite`` reaches, so without
    it this endpoint would hand back the name and elevation of a stranger's
    private pin to anyone who guessed its id. A favourite-only location is a
    404 here, exactly as it is absent from the feed.

    ``date`` is an optional ISO-8601 query parameter, so the sheet follows
    the season scrubber rather than always answering for today. An absent,
    malformed or out-of-range value falls back to today rather than 400-ing:
    the scrubber is a slider and the cost of a bad value is the wrong day,
    not a broken sheet.

    **A forward day reads today's row** (SNOW-1105). The fetch writes one
    row per location per day and carries the days after it in that row's
    ``forecast`` column, so a date after today has no row of its own — an
    exact-date lookup for it always missed, and the sheet said "No weather
    recorded" for every day the map's weather layer was drawing. The sheet
    now describes the matching ``forecast`` entry and marks it as a forecast,
    stating when it was issued: it is what the week looked like this
    morning, not a record of the day.

    A location with no row for the requested day still renders — the template
    says so in a sentence. That is the normal state for a past date until the
    historical backfill lands, and an error would misdescribe it.

    Deliberately **not** cached. It is one row for one location on one day,
    behind a tap rather than a page load, and the response embeds a
    ``target_date`` that varies per request.

    Errors:
        404 — unknown ``short_id``, or one outside the public estate.

    Args:
        request: The incoming HTTP request.
        short_id: The Location's opaque short id (SNOW-797).

    Returns:
        A JsonResponse with a single ``html`` key containing the sheet body.

    """
    location = get_object_or_404(Location.objects.public(), short_id=short_id)

    target_date = timezone.localdate()
    raw_date = request.GET.get("date", "")
    if raw_date:
        try:
            target_date = date.fromisoformat(raw_date)
        except ValueError:
            logger.debug("weather_detail: ignoring unparseable date %r", raw_date)

    # The page's own context (the sheet is the page, Hugo, 24 Oct 2026):
    # the views module builds it once for both containers.
    return JsonResponse(
        {
            "html": render_to_string(
                "public/partials/_weather_detail.html",
                weather_point_context(location, target_date, timezone.now()),
                request=request,
            ),
        }
    )


# SNOW-459: private/no-store, NOT public — CDN/proxy caching of this
# response is left for SNOW-469 (tracked separately, out of scope here).
# SNOW-1480: and since each report's ``mine`` is the reader's own answer,
# the response must never be shared between readers at all.
@cache_control(private=True, no_store=True)
def community_reports_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of anonymised community field reports.

    The map's "On the ground" layer (SNOW-419, reshaped by SNOW-1290) — a
    Waze-like shared layer of what people found out there. ``?d=YYYY-MM-DD``
    picks the map's day (default today);
    ``apps.observations.services.ground.report_window`` turns it into the
    window read — the same window the region panel reads. One feature per
    *report*: the rows one ``submission`` wrote (SNOW-1289) share a
    location and a time, so they are one pin listing every type, coloured
    by the most serious family.

    Anonymisation is applied server-side, once, here — the client never
    receives full-precision coordinates or anything that identifies the
    reporting account:

    * ``coordinates`` are ``[longitude, latitude]`` rounded to 3 decimal
      places (~80-110 m), never the raw ``latitude``/``longitude`` fields.
    * ``gps_latitude``/``gps_longitude``/``accuracy_radius_km``/``user``/
      the row's primary key are never serialised. ``submission`` is a
      random uuid minted per report; it names the report, never the
      reporter, and lets the region panel point at the pin (SNOW-1291).

    ``observed_at`` is sent as recorded — with no name anywhere in the
    payload there is nothing for a precise time to identify.

    ``mine`` (SNOW-1480) is whether the signed-in reader wrote the report —
    true when any of its rows is theirs, always false for an anonymous
    reader. It is computed here, against ``request.user``, so the payload
    still carries no user id: the only thing it says about identity is
    "this one is yours", and only to its author. The map paints those pins
    blue; the offline cache strips it before keeping the payload.

    Response shape::

        {
          "type": "FeatureCollection",
          "features": [
            {
              "type": "Feature",
              "geometry": {"type": "Point", "coordinates": [7.123, 46.456]},
              "properties": {
                "submission": "0b6f…",
                "types": ["WHUMPFING", "POWDER"],
                "type_labels": ["Whumpfing", "Powder"],
                "family": "WARNING",
                "observed_at": "2026-07-19T10:15:00+00:00",
                "observed_on": "2026-07-19",
                "region_id": "CH-4115",
                "region_name": "Martigny-Verbier",
                "mine": false
              }
            },
            ...
          ]
        }

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or a 400 for a
        malformed ``?d=``.

    """
    today = timezone.localdate()
    raw_date = request.GET.get("d")
    day = today
    if raw_date:
        # Strict YYYY-MM-DD, as the region-ratings endpoint: fromisoformat
        # alone also accepts the separator-less form.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            return JsonResponse({"error": "malformed date"}, status=400)
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            return JsonResponse({"error": "malformed date"}, status=400)

    window = report_window(day, today)
    viewer_id = request.user.pk if request.user.is_authenticated else None
    rows = (
        FieldObservation.objects.in_window(*window)
        # ``location`` joined: each report's coordinate is its Location's,
        # and a per-row fetch would grow with the number of reports.
        .select_related("region", "location")
        .order_by("-observed_at", "pk")
        if window is not None
        else FieldObservation.objects.none()
    )
    # Declaration order of OBSERVATION_TYPE is family order, most serious
    # first, so a report's types list reads warning signs before the trail.
    type_rank = {
        value: i for i, value in enumerate(FieldObservation.OBSERVATION_TYPE.values)
    }
    reports: dict[uuid.UUID, dict[str, Any]] = {}
    newest: datetime | None = None
    for obs in rows.iterator():
        if newest is None or obs.observed_at > newest:
            newest = obs.observed_at
        report = reports.get(obs.submission)
        if report is None:
            report = reports[obs.submission] = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    # GeoJSON ordering: [longitude, latitude] per RFC 7946.
                    # Rounded to 3 dp (~80-110 m) — never the raw field.
                    "coordinates": [
                        round(obs.location.longitude, 3),
                        round(obs.location.latitude, 3),
                    ],
                },
                "properties": {
                    "submission": str(obs.submission),
                    "types": [],
                    "observed_at": obs.observed_at.isoformat(),
                    # The local day — settings.TIME_ZONE, the bulletins'
                    # boundary — so the client draws live / recent / day
                    # without working out a timezone (SNOW-1290).
                    "observed_on": timezone.localdate(obs.observed_at).isoformat(),
                    "region_id": obs.region.region_id if obs.region else None,
                    "region_name": obs.region.name if obs.region else None,
                    "mine": False,
                },
            }
        report["properties"]["types"].append(obs.observation_type)
        # A report is the reader's when any of its rows is theirs. An
        # anonymous reader's ``None`` matches no row: ``user`` is required.
        report["properties"]["mine"] |= obs.user_id == viewer_id

    labels = dict(FieldObservation.OBSERVATION_TYPE.choices)
    features: list[dict[str, Any]] = []
    for report in reports.values():
        properties = report["properties"]
        types = sorted(
            properties["types"], key=lambda t: type_rank.get(t, len(type_rank))
        )
        properties["types"] = types
        properties["type_labels"] = [str(labels.get(t, t)) for t in types]
        properties["family"] = leading_family(types)
        features.append(report)

    response = JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )
    # Observations are non-safety-critical (unlike ratings) — unsafe_after=None
    # so the freshness state saturates at "stale" and never escalates to
    # "unsafe". Empty result set (no reports in the window) falls back to
    # "now". max_age is pinned to _COMMUNITY_CACHE_MAX_AGE rather than the
    # apply_freshness_headers default (24h): it is the client-side freshness
    # window for the overlay (the dot flips to "stale" after 120s). The
    # response itself is no-store (see the decorator) — this window drives
    # only the client freshness dot, not any shared cache.
    apply_freshness_headers(
        response,
        generated_at=newest or timezone.now(),
        max_age=_COMMUNITY_CACHE_MAX_AGE,
        unsafe_after=None,
    )
    return response


# ── The 3D test's results (SNOW-1456) ───────────────────────────────────────

# The largest body /api/map-3d-test/ reads. A real result is about 1 KiB.
MAP_3D_TEST_MAX_BYTES = 16 * 1024

# At most this many camera passes in one result (PROBE_PASSES has two).
_MAP_3D_TEST_MAX_PASSES = 4

# Each pass's figures: the key, and the largest value it may take.
_MAP_3D_TEST_PASS_NUMBERS: dict[str, float] = {
    "median_ms": 60_000,
    "p95_ms": 60_000,
    "worst_ms": 60_000,
    "over_budget": 1,
}
_MAP_3D_TEST_PASS_COUNTS: dict[str, int] = {
    "frames": 100_000,
    "slow_windows": 10_000,
    "settle_ms": 600_000,
}


class _BadResult(ValueError):
    """A posted 3D test result whose shape the endpoint refuses."""


def _number(value: Any, top: float) -> float:
    """Return ``value`` as a float between 0 and ``top``, or raise.

    Args:
        value: A decoded JSON value.
        top: The largest value allowed.

    Returns:
        The number.

    Raises:
        _BadResult: For a non-number (a bool included), or one out of range.

    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _BadResult("not a number")
    if not math.isfinite(value) or not 0 <= value <= top:
        raise _BadResult("out of range")
    return float(value)


def _count(value: Any, top: int) -> int:
    """Return ``value`` as an int between 0 and ``top``, or raise.

    Args:
        value: A decoded JSON value.
        top: The largest value allowed.

    Returns:
        The count.

    Raises:
        _BadResult: For a non-integer (a bool included), or one out of range.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _BadResult("not a count")
    if not 0 <= value <= top:
        raise _BadResult("out of range")
    return value


def _text(value: Any, longest: int) -> str:
    """Return ``value`` as a string of at most ``longest`` characters, or raise.

    Args:
        value: A decoded JSON value.
        longest: The most characters allowed.

    Returns:
        The string.

    Raises:
        _BadResult: For a non-string or one too long.

    """
    if not isinstance(value, str) or len(value) > longest:
        raise _BadResult("not a short string")
    return value


def _map_3d_test_fields(payload: Any) -> dict[str, Any]:
    """Validate a posted 3D test result into ``Map3dDeviceTest`` fields.

    Args:
        payload: The decoded JSON body.

    Returns:
        The model's field values.

    Raises:
        _BadResult: For any shape, type, range, verdict or overlay the
            endpoint does not accept.

    """
    if not isinstance(payload, dict):
        raise _BadResult("not an object")
    verdict = payload.get("verdict")
    if verdict not in MAP_3D_TEST_VERDICTS:
        raise _BadResult("unknown verdict")
    overlays = payload.get("overlays", [])
    if not isinstance(overlays, list) or any(
        key not in MAP_3D_TEST_OVERLAYS for key in overlays
    ):
        raise _BadResult("unknown overlay")
    passes = payload.get("passes")
    if not isinstance(passes, list) or not 1 <= len(passes) <= _MAP_3D_TEST_MAX_PASSES:
        raise _BadResult("no passes")
    probe = payload.get("probe", False)
    if not isinstance(probe, bool):
        raise _BadResult("probe is not a bool")
    return {
        "verdict": verdict.upper(),
        "gpu": _text(payload.get("gpu", ""), 255),
        "device_pixel_ratio": _number(payload.get("device_pixel_ratio"), 16),
        "pixel_ratio": _number(payload.get("pixel_ratio"), 16),
        "canvas_width": _count(payload.get("canvas_width"), 20_000),
        "canvas_height": _count(payload.get("canvas_height"), 20_000),
        "user_agent": _text(payload.get("user_agent", ""), 512),
        "client_version": _text(payload.get("client_version", ""), 64),
        "probe": probe,
        # Each key once, in the allowlist's order.
        "overlays": [key for key in MAP_3D_TEST_OVERLAYS if key in overlays],
        "passes": [_map_3d_test_pass(item) for item in passes],
    }


def _map_3d_test_pass(item: Any) -> dict[str, Any]:
    """Validate one camera pass's figures from a posted 3D test result.

    Args:
        item: One entry of the result's ``passes``.

    Returns:
        The pass, with only the known keys.

    Raises:
        _BadResult: For a non-object, or any figure missing or out of range.

    """
    if not isinstance(item, dict):
        raise _BadResult("a pass is not an object")
    clean: dict[str, Any] = {"label": _text(item.get("label"), 64)}
    for key, top in _MAP_3D_TEST_PASS_NUMBERS.items():
        clean[key] = _number(item.get(key), top)
    for key, count_top in _MAP_3D_TEST_PASS_COUNTS.items():
        clean[key] = _count(item.get(key), count_top)
    return clean


@require_POST
@ratelimit(key="ip", rate="10/h", block=False)
def map_3d_test_receive(request: HttpRequest) -> HttpResponse:
    """Store one 3D test result a reader chose to send (SNOW-1456).

    Open to signed-in and anonymous readers alike, and CSRF-protected (the
    panel, static/js/map_3d_test.js, posts with the page's token in
    ``X-CSRFToken``). The row holds no user, no session and no IP.

    Answers 204 when the row is written, and 204 too when the per-IP rate
    limit (10 an hour) drops it, with nothing written: the panel has
    nothing better to tell the reader. 400 for a bad shape, an unknown
    verdict or an unknown overlay; 413 over ``MAP_3D_TEST_MAX_BYTES``. The
    CSRF middleware answers 403 and ``require_POST`` 405.

    Args:
        request: The incoming HTTP request.

    Returns:
        An empty response with the status above.

    """
    if getattr(request, "limited", False):
        logger.info("map_3d_test: dropped a result over the rate limit")
        return HttpResponse(status=204)
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    if declared > MAP_3D_TEST_MAX_BYTES or len(request.body) > MAP_3D_TEST_MAX_BYTES:
        return HttpResponse(status=413)
    try:
        fields = _map_3d_test_fields(json.loads(request.body))
    except (ValueError, _BadResult) as exc:
        logger.info("map_3d_test: refused a result: %s", exc)
        return HttpResponse(status=400)
    Map3dDeviceTest.objects.create(**fields)
    return HttpResponse(status=204)
