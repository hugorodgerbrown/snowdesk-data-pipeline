"""
favourites/views.py — HTMX endpoints + GeoJSON layer for the favourites app.

Provides five HTMX-only fragment views and one plain-JSON endpoint used by
the map page's saved-pins feature (SNOW-413) and the favourite detail card
/ manage-page list (SNOW-415):

- ``favourite_create`` (POST) — validates lat/lon/name, creates a
  Favourite, returns the saved-pin partial.
- ``favourite_rename`` (POST) — owner-checked rename of an existing
  Favourite, returns the updated partial.
- ``favourite_delete`` (POST) — owner-checked deletion of a Favourite.
- ``favourite_card`` (GET) — owner-checked detail card: name/coords,
  altitude, the containing region's current danger rating, and a 7-day
  point forecast panel with a near-term hourly detail (SNOW-415, SNOW-417).
- ``favourite_list`` (GET) — the requesting user's own favourites,
  rendered for the manage page's "My favourites" section (SNOW-415).
- ``favourites_geojson`` (GET) — a FeatureCollection of the requesting
  user's own favourites, for the map's saved-pins layer. Not
  ``@require_htmx`` — this is consumed by a JS ``fetch()`` call, not an
  HTMX swap.

All six are:
  - flag-gated on ``favourites`` (404 when inactive);
  - authentication-gated (403 for anonymous users).

``favourite_card`` and ``favourite_rename``/``favourite_delete`` are
owner-scoped via ``Favourite.objects.for_user()`` — another user's uuid
returns 404, never 403, so a probing request can't distinguish "not yours"
from "doesn't exist" (no existence oracle).

``favourite_create`` additionally applies django-ratelimit (10/m, keyed on
``user`` since these endpoints are auth-only) and returns 429 when the
limit is exceeded.

Coordinate-argument convention: every function in this module takes
latitude/longitude in that order — ``(latitude, longitude)`` — matching
``favourites.services``.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import waffle
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from bulletins.models import ForecastPointWeather, RegionDayRating
from bulletins.services.weather_display import build_point_forecast_panel
from bulletins.services.weather_fetcher import POINT_FORECAST_DAYS
from core.coordinates import validate_coordinates
from core.decorators import require_htmx
from core.freshness import (
    DEFAULT_UNSAFE_AFTER_SECONDS,
    apply_freshness_headers,
    freshness_state as compute_freshness_state,
)
from favourites.models import Favourite
from favourites.relevance import annotate_problem_relevance
from favourites.services import (
    FavouriteLimitReached,
    create_favourite,
    delete_favourite,
)
from public.templatetags.snowdesk_time import danger_level_digit
from public.views import _select_bulletin_for_date, problem_cards_for_bulletin

if TYPE_CHECKING:
    from bulletins.services.weather_display import ForecastPanel

logger = logging.getLogger(__name__)

# Must match Favourite.name's max_length. Checked here so an over-length
# submission is turned into a handled 400 instead of a DB DataError (500).
_NAME_MAX_LENGTH = 100


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_favourites_flag(request: HttpRequest) -> None:
    """Raise Http404 unless the ``favourites`` waffle flag is active.

    Mirrors the pattern used by ``observations.views._require_field_observations_flag``.
    Flag is seeded with ``superusers=True`` by
    ``favourites/migrations/0002_seed_favourites_flag.py``; extend / disable
    via ``/admin/waffle/flag/favourites/``.

    Args:
        request: The current HTTP request.

    Raises:
        Http404: When the flag is inactive for the current request.

    """
    from django.http import Http404  # noqa: PLC0415

    if not waffle.flag_is_active(request, "favourites"):
        raise Http404("favourites flag is inactive for this request.")


def _parse_latlon(
    lat_str: str | None, lon_str: str | None
) -> tuple[float, float] | None:
    """Parse latitude and longitude strings into floats.

    Returns None when either value is missing or unparseable.

    Args:
        lat_str: Raw latitude string from the request POST body.
        lon_str: Raw longitude string from the request POST body.

    Returns:
        ``(latitude, longitude)`` float pair, or None on failure.

    """
    if not lat_str or not lon_str:
        return None
    try:
        lat, lon = float(lat_str), float(lon_str)
        # Rejects NaN/±Inf and out-of-range values before the Open-Meteo
        # elevation lookup and DB write (InvalidCoordinatesError is a
        # ValueError, caught below). SNOW-464.
        validate_coordinates(lat, lon)
    except ValueError, TypeError:
        return None
    return lat, lon


def _point_forecast_panel(
    favourite: Favourite, now: datetime.datetime
) -> tuple["ForecastPanel | None", datetime.datetime | None]:
    """Return the multi-day point forecast panel for a favourite, or None.

    Queries the forward-looking ``ForecastPointWeather`` window for the
    favourite's ``forecast_point`` (today onwards, capped at
    ``POINT_FORECAST_DAYS`` rows) and builds the panel context via
    ``build_point_forecast_panel``. Deliberately does **not** fall back to
    the region-centroid ``WeatherSnapshot`` — that would misrepresent the
    point's own conditions, which is exactly what this feature exists to
    fix. Returns ``(None, None)`` when no rows have been fetched yet, so
    ``_favourite_card.html`` falls back to its "Forecast coming soon" empty
    state.

    Args:
        favourite: The Favourite whose point forecast is being resolved.
        now: The reference instant for each day's day/night icon decision.

    Returns:
        A ``(ForecastPanel | None, latest_fetched_at)`` pair — the panel
        context (or ``None`` when no rows exist), and the most recent
        ``fetched_at`` across the queried rows (or ``None`` when there are
        no rows) for the caller's freshness-header stamp. Computed
        in-memory from the already-materialised row list rather than a
        second ``aggregate(Max(...))`` query.

    """
    snapshots = list(
        ForecastPointWeather.objects.forecast_for_point(
            favourite.forecast_point, timezone.localdate()
        )[:POINT_FORECAST_DAYS]
    )
    panel = build_point_forecast_panel(snapshots, now)
    latest_fetched_at = max((row.fetched_at for row in snapshots), default=None)
    return panel, latest_fetched_at


def _card_freshness(
    day_rating: RegionDayRating | None,
    weather_fetched_at: datetime.datetime | None = None,
) -> tuple[datetime.datetime, int | None]:
    """Resolve the ``(generated_at, unsafe_after)`` freshness pair for a favourite.

    Collects whichever of the rating / weather timestamps is present and
    uses the OLDER one as ``generated_at`` — a record mixing both
    constituents is only as fresh as its stalest one. Falls back to
    ``timezone.now()`` when neither is present (nothing to attach a real
    timestamp to — a "no coverage, no weather yet" favourite is trivially
    "current"). ``unsafe_after`` only governs the safety-critical rating
    half: it is ``DEFAULT_UNSAFE_AFTER_SECONDS`` whenever a rating exists,
    and ``None`` otherwise (weather is non-safety and never escalates to
    "unsafe" per the spec's freshness contract).

    Args:
        day_rating: Today's RegionDayRating for the favourite's region,
            or None.
        weather_fetched_at: The latest point-weather ``fetched_at`` (from
            ``_point_forecast_panel``), or None when no forecast rows exist.

    Returns:
        ``(generated_at, unsafe_after)``.

    """
    present = [
        ts
        for ts in (
            day_rating.updated_at if day_rating else None,
            weather_fetched_at,
        )
        if ts is not None
    ]
    generated_at = min(present) if present else timezone.now()
    unsafe_after = DEFAULT_UNSAFE_AFTER_SECONDS if day_rating else None
    return generated_at, unsafe_after


def _build_favourite_cache_record(
    favourite: Favourite,
    day_rating: RegionDayRating | None,
    bulletin_url: str,
    generated_at: datetime.datetime,
    unsafe_after: int | None,
) -> dict[str, Any]:
    """Build the JSON-serialisable client-cache record for one favourite.

    Shared by ``favourite_card`` and ``favourite_list`` so
    ``static/js/favourites_offline.js`` sees one consistent shape
    regardless of which endpoint populated ``data:favourites``. Each
    record carries its own ``generated_at`` / ``unsafe_after_seconds`` so
    the offline client can classify every cached pin independently
    (SNOW-418).

    Args:
        favourite: The Favourite the record describes.
        day_rating: Today's RegionDayRating for favourite.region, or None.
        bulletin_url: Evergreen bulletin URL for favourite.region, or ""
            when favourite.region is None.
        generated_at: The freshness ``generated_at`` instant for this
            record (from ``_card_freshness``).
        unsafe_after: Seconds after which this record's rating is
            EXPIRED, or None when there is no rating to expire.

    Returns:
        A dict matching the SNOW-418 cache-record shape.

    """
    region_payload: dict[str, Any] | None = None
    if favourite.region is not None:
        region_payload = {
            "id": favourite.region.pk,
            "name": favourite.region.name,
            "bulletin_url": bulletin_url,
        }
    rating_payload: dict[str, Any] | None = None
    if day_rating is not None:
        rating_payload = {
            "max_rating": day_rating.max_rating,
            "max_subdivision": day_rating.max_subdivision,
            "digit": danger_level_digit(day_rating.max_rating),
        }
    return {
        "uuid": str(favourite.uuid),
        "name": favourite.name,
        "latitude": favourite.latitude,
        "longitude": favourite.longitude,
        "elevation": favourite.elevation,
        "region": region_payload,
        "rating": rating_payload,
        # Offline weather caching is out of scope for SNOW-418: the SNOW-417
        # forecast panel is re-fetched online only, so the cached record
        # carries no weather and the offline card omits the forecast.
        "weather": None,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "unsafe_after_seconds": unsafe_after,
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@require_htmx
@require_POST
@ratelimit(key="user", rate="10/m", block=False)
def favourite_create(request: HttpRequest) -> HttpResponse:
    """Create a Favourite and return the saved-pin partial.

    Validates the POST body:
    - ``lat`` / ``lon`` must be present and parseable (400 otherwise).
    - ``name`` is optional, but rejected with 400 if it exceeds
      ``Favourite.name``'s max_length (would otherwise raise a DB DataError).
    - Rate-limited to 10 creations per minute per user (429 on excess).

    When the user has reached ``settings.FAVOURITES_MAX_PER_USER``, renders
    ``_favourite_limit.html`` at HTTP 200 (so HTMX swaps the error message
    into the target) instead of creating a row.

    Args:
        request: The incoming HTMX POST request.

    Returns:
        Rendered saved-pin or limit-reached partial, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before saving another pin.",
            status=429,
        )

    coords = _parse_latlon(request.POST.get("lat"), request.POST.get("lon"))
    if coords is None:
        return HttpResponse("Valid lat and lon are required.", status=400)

    latitude, longitude = coords
    name = request.POST.get("name", "")
    if len(name) > _NAME_MAX_LENGTH:
        return HttpResponse(
            f"name must be at most {_NAME_MAX_LENGTH} characters.", status=400
        )

    try:
        favourite = create_favourite(request.user, latitude, longitude, name=name)
    except FavouriteLimitReached:
        logger.info("Favourite create blocked: user=%s hit the cap", request.user.pk)
        return render(
            request, "favourites/partials/_favourite_limit.html", {}, status=200
        )

    return render(
        request,
        "favourites/partials/_favourite.html",
        {"favourite": favourite},
    )


@require_htmx
@require_POST
def favourite_rename(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Rename an existing Favourite owned by the requesting user.

    Args:
        request: The incoming HTMX POST request. Expects a ``name`` field,
            rejected with 400 if it exceeds ``Favourite.name``'s max_length.
        uuid: The Favourite's uuid, from the URL.

    Returns:
        Rendered updated saved-pin partial, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        favourite = Favourite.objects.for_user(request.user).get(uuid=uuid)
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    name = request.POST.get("name", "")
    if len(name) > _NAME_MAX_LENGTH:
        return HttpResponse(
            f"name must be at most {_NAME_MAX_LENGTH} characters.", status=400
        )

    favourite.name = name
    # updated_at is auto_now — it must be in update_fields or the DB column
    # is left stale, since save(update_fields=...) skips every field not
    # explicitly listed (auto_now is applied in Python, not by the DB).
    favourite.save(update_fields=["name", "updated_at"])

    return render(
        request,
        "favourites/partials/_favourite.html",
        {"favourite": favourite},
    )


@require_htmx
@require_POST
def favourite_delete(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Delete an existing Favourite owned by the requesting user.

    Args:
        request: The incoming HTMX POST request.
        uuid: The Favourite's uuid, from the URL.

    Returns:
        An empty 200 response on success, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        delete_favourite(request.user, uuid)
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    return HttpResponse("")


@require_htmx
@require_GET
def favourite_card(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Render the favourite detail card partial.

    Owner-scoped via ``Favourite.objects.for_user()`` — a non-owner's uuid
    returns 404 (never 403), so the endpoint gives no existence oracle.

    When ``favourite.region`` is set, resolves today's ``RegionDayRating``
    for it (mirroring ``public.api.region_summary``) so the card can show
    the regional danger rating and a link to the evergreen bulletin page.
    When ``region`` is ``None`` (the pin falls outside every known
    boundary), the card renders a "no bulletin coverage here" note
    instead. The point's own forecast comes from ``_point_forecast_panel``,
    which returns ``None`` until at least one ``ForecastPointWeather`` row
    has been fetched for the point (empty "coming soon" state).

    When a region and today's default bulletin both resolve, also builds
    the avalanche-problems section (SNOW-422): the bulletin's problem cards
    (``public.views.problem_cards_for_bulletin``), each annotated with an
    altitude verdict relative to ``favourite.elevation``
    (``favourites.relevance.annotate_problem_relevance``). Every problem the
    bulletin publishes is always included — the annotation only highlights
    relevance, it never suppresses a problem.

    Stamps the ``X-Data-Generated-At`` / ``X-Data-Max-Age`` /
    ``X-Data-Unsafe-After`` freshness headers (SNOW-370 / SNOW-418) via
    ``_card_freshness``: ``generated_at`` is the OLDER of the rating's
    ``updated_at`` and the latest forecast row's ``fetched_at`` (so the
    stamp is never fresher than the card's stalest part), and
    ``unsafe_after`` is the 48h horizon only when a safety-critical rating
    is present. Threads a ``cache_payload`` JSON sidecar + freshness
    indicator into the template context so
    ``static/js/favourites_offline.js`` can cache this pin for offline
    reads.

    Args:
        request: The incoming HTMX GET request.
        uuid: The Favourite's uuid, from the URL.

    Returns:
        Rendered ``_favourite_card.html``, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        favourite = (
            Favourite.objects.for_user(request.user)
            .select_related("region", "forecast_point")
            .get(uuid=uuid)
        )
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    day_rating = None
    bulletin_url = ""
    problem_cards: list[dict[str, Any]] = []
    if favourite.region is not None:
        today = timezone.localdate()
        day_rating = RegionDayRating.objects.filter(
            region=favourite.region, date=today
        ).first()
        # No date arg — the evergreen "today" bulletin URL.
        bulletin_url = favourite.region.get_absolute_url()

        # Avalanche problems — this location (SNOW-422). Rides today's
        # default bulletin; highlight-never-suppress, so every card the
        # bulletin publishes is annotated, never dropped.
        bulletin = _select_bulletin_for_date(favourite.region, today)
        if bulletin is not None:
            cards = problem_cards_for_bulletin(bulletin)
            problem_cards = annotate_problem_relevance(cards, favourite.elevation)

    forecast_panel, latest_fetched_at = _point_forecast_panel(favourite, timezone.now())

    # The card mixes a safety-critical rating and (non-safety) point weather.
    # generated_at is the OLDER of the two present constituents; unsafe_after
    # is governed by the rating alone (SNOW-418).
    generated_at, unsafe_after = _card_freshness(day_rating, latest_fetched_at)
    state = compute_freshness_state(generated_at, unsafe_after=unsafe_after)
    cache_payload = _build_favourite_cache_record(
        favourite, day_rating, bulletin_url, generated_at, unsafe_after
    )

    response = render(
        request,
        "favourites/partials/_favourite_card.html",
        {
            "favourite": favourite,
            "day_rating": day_rating,
            "bulletin_url": bulletin_url,
            "forecast_panel": forecast_panel,
            "problem_cards": problem_cards,
            "cache_payload": cache_payload,
            "freshness_state": state,
            "freshness_generated_at": generated_at,
        },
    )
    return apply_freshness_headers(response, generated_at, unsafe_after=unsafe_after)


@require_htmx
@require_GET
def favourite_list(request: HttpRequest) -> HttpResponse:
    """Render the requesting user's own favourites list partial.

    Powers the manage page's "My favourites" section, which lazy-loads
    this endpoint via ``hx-get`` on page load.

    Batches today's ``RegionDayRating`` lookup for every favourite's
    region into a single query (never N+1 as the favourite count grows),
    stamps the freshness headers (SNOW-418) using the oldest present
    rating's ``updated_at`` (or ``now()`` when none exist), and threads a
    ``roster_payload`` JSON sidecar into the template context so
    ``static/js/favourites_offline.js`` can cache the whole list for
    offline reads.

    Args:
        request: The incoming HTMX GET request.

    Returns:
        Rendered ``_favourite_list.html``, or an error response.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    favourites = list(Favourite.objects.for_user(request.user).select_related("region"))

    today = timezone.localdate()
    region_ids = [f.region_id for f in favourites if f.region_id is not None]
    ratings_by_region_id: dict[int, RegionDayRating] = {
        rating.region_id: rating
        for rating in RegionDayRating.objects.filter(
            region_id__in=region_ids, date=today
        )
    }

    roster_payload: list[dict[str, Any]] = []
    present_ratings: list[RegionDayRating] = []
    for favourite in favourites:
        day_rating = (
            ratings_by_region_id.get(favourite.region_id)
            if favourite.region_id is not None
            else None
        )
        bulletin_url = favourite.region.get_absolute_url() if favourite.region else ""
        generated_at, unsafe_after = _card_freshness(day_rating)
        roster_payload.append(
            _build_favourite_cache_record(
                favourite, day_rating, bulletin_url, generated_at, unsafe_after
            )
        )
        if day_rating is not None:
            present_ratings.append(day_rating)

    response_generated_at = (
        min(rating.updated_at for rating in present_ratings)
        if present_ratings
        else timezone.now()
    )
    response_unsafe_after = DEFAULT_UNSAFE_AFTER_SECONDS if present_ratings else None

    response = render(
        request,
        "favourites/partials/_favourite_list.html",
        {"favourites": favourites, "roster_payload": roster_payload},
    )
    return apply_freshness_headers(
        response, response_generated_at, unsafe_after=response_unsafe_after
    )


def favourites_geojson(request: HttpRequest) -> JsonResponse:
    """Return a FeatureCollection of the requesting user's own favourites.

    Each feature is a Point with GeoJSON-ordered ``coordinates: [lon, lat]``
    (RFC 7946) and properties ``uuid`` and ``name``. Not ``@require_htmx`` —
    consumed by the map's saved-pins layer via a JS ``fetch()`` call, not an
    HTMX swap.

    The response is marked ``Cache-Control: private, no-store`` — the
    inverse of the public ``resorts_geojson`` layer — since this payload is
    per-user and must never be shared across users by an intermediate
    cache.

    Args:
        request: The incoming GET request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or a 403/404 error.

    """
    _require_favourites_flag(request)

    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    features: list[dict[str, Any]] = []
    for favourite in Favourite.objects.for_user(request.user).iterator():
        # GeoJSON ordering: [longitude, latitude] per RFC 7946.
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [favourite.longitude, favourite.latitude],
                },
                "properties": {
                    "uuid": str(favourite.uuid),
                    "name": favourite.name,
                },
            }
        )

    response = JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response
