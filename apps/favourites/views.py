"""
apps/favourites/views.py — HTMX endpoints + GeoJSON layer for the favourites app.

Provides five HTMX-only fragment views and one plain-JSON endpoint used by
the map page's saved-pins feature (SNOW-413) and the favourite detail card
/ account-page list (SNOW-415):

- ``favourite_create`` (POST) — validates lat/lon/name, creates a
  Favourite, returns the saved-pin partial.
- ``favourite_create_from_resort`` (POST) — SNOW-499: creates a Favourite
  from a public ``regions.Resort`` (the resort-pin popup's star), returns
  the same saved-pin partial as ``favourite_create``.
- ``favourite_resort_toggle`` (POST) — SNOW-504: a simple, online-only
  toggle of the ``(user, resort)`` Favourite for the resort detail page's
  favourite button — creates via ``create_resort_favourite`` if absent,
  else deletes; returns the button partial in its new state. Deliberately
  a second, simpler code path from ``favourite_create_from_resort`` — no
  client mutation-queue / offline handling, since the resort page has no
  offline affordance to protect.
- ``favourite_rename`` (POST) — owner-checked rename of an existing
  Favourite, returns the updated partial.
- ``favourite_delete`` (POST) — owner-checked deletion of a Favourite.
- ``favourite_card`` (GET) — owner-checked detail card: name/coords,
  altitude, the containing region's current danger rating, and a 7-day
  point forecast panel with a near-term hourly detail (SNOW-415, SNOW-417).
- ``favourite_detail`` (GET) — SNOW-507: the same card content as
  ``favourite_card``, promoted to a real, bookmarkable full page
  (``/favourites/<uuid>/``) rather than an HTMX-only fragment. Shares its
  context-building with ``favourite_card`` via ``_favourite_card_context``.
- ``favourite_list`` (GET) — the requesting user's own favourites,
  rendered for /account/favourites/ (SNOW-415, moved off the account
  hub by SNOW-668) and, as ``?variant=map``, for the map sheet.
- ``favourites_geojson`` (GET) — a FeatureCollection of the requesting
  user's own favourites, for the map's saved-pins layer. Not
  ``@require_htmx`` — this is consumed by a JS ``fetch()`` call, not an
  HTMX swap.

All nine are authentication-gated (403 for anonymous users).

``favourite_card``, ``favourite_detail``, and ``favourite_rename``/
``favourite_delete`` are owner-scoped via ``Favourite.objects.for_user()``
— another user's uuid returns 404, never 403, so a probing request can't
distinguish "not yours" from "doesn't exist" (no existence oracle).

``favourite_create`` additionally applies django-ratelimit (10/m, keyed on
``user`` since these endpoints are auth-only) and returns 429 when the
limit is exceeded.

Coordinate-argument convention: every function in this module takes
latitude/longitude in that order — ``(latitude, longitude)`` — matching
``apps.favourites.services``.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.bulletins.models import RegionDayRating
from apps.core.coordinates import validate_coordinates
from apps.core.decorators import require_htmx
from apps.core.freshness import (
    DEFAULT_UNSAFE_AFTER_SECONDS,
    apply_freshness_headers,
    freshness_state as compute_freshness_state,
)
from apps.favourites.constants import FAVOURITE_LIST_MAP_VARIANT
from apps.favourites.models import Favourite
from apps.favourites.relevance import annotate_problem_relevance
from apps.favourites.services import (
    FavouriteLimitReached,
    ResortNotGeocoded,
    create_favourite,
    create_resort_favourite,
    delete_favourite,
)
from apps.public.templatetags.snowdesk_time import danger_level_digit
from apps.public.views import _select_bulletin_for_date, problem_cards_for_bulletin
from apps.regions.models import Resort
from apps.weather.models import Weather
from apps.weather.services.weather_display import (
    build_weather_display,
)

logger = logging.getLogger(__name__)

# Must match Favourite.name's max_length. Checked here so an over-length
# submission is turned into a handled 400 instead of a DB DataError (500).
_NAME_MAX_LENGTH = 100

# SNOW-658: favourite_list serves two surfaces. The ``variant`` query
# parameter selects a template out of this fixed map — an unknown (or
# absent) value falls back to the account page's default, so nothing a
# caller sends ever reaches a template path.
_LIST_TEMPLATE_DEFAULT = "favourites/partials/_favourite_list.html"
_LIST_TEMPLATES = {
    FAVOURITE_LIST_MAP_VARIANT: "favourites/partials/_favourite_list_map.html",
}

# SNOW-711: the dummy uuid the rename URL template is reversed with. See
# _rename_url_template below.
_DUMMY_UUID = UUID(int=0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rename_url_template() -> str:
    """Return favourites:rename with ``__UUID__`` where the uuid goes.

    Handed to the account page's list template so
    ``static/js/account_favourites.js`` can build one row's rename URL at
    commit time, from the uuid riding on that row's own pencil. The same
    ``__UUID__`` trick ``apps.public.views._favourites_context`` uses for
    the map panel: reverse with a dummy uuid, then string-replace.

    Reversed per call rather than once at import — this module is imported
    by ``apps.favourites.urls``, so reversing at import time would ask the
    URLconf to resolve itself while it is still being built.

    Returns:
        The rename URL with the uuid replaced by ``__UUID__``.

    """
    return reverse("favourites:rename", args=[_DUMMY_UUID]).replace(
        str(_DUMMY_UUID), "__UUID__"
    )


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


def _card_freshness(
    day_rating: RegionDayRating | None,
    weather_fetched_at: datetime.datetime | None = None,
) -> tuple[datetime.datetime, int | None]:
    """Resolve the ``(generated_at, unsafe_after)`` freshness pair for a favourite.

    Collects whichever of the rating / weather timestamps is present and
    uses the OLDER one as ``generated_at`` — the card mixes both
    constituents, so it is only as fresh as its stalest one. Falls back to
    ``timezone.now()`` when neither is present (nothing to attach a real
    timestamp to, so a "no coverage, no weather" favourite is trivially
    "current"). ``unsafe_after`` governs the safety-critical rating half
    only: it is ``DEFAULT_UNSAFE_AFTER_SECONDS`` whenever a rating exists
    and ``None`` otherwise — weather is non-safety and never escalates a
    card to "unsafe".

    Args:
        day_rating: Today's RegionDayRating for the favourite's region,
            or None.
        weather_fetched_at: ``Weather.fetched_at`` for the row the card
            renders, or None when the pin has no row for today.

    Returns:
        ``(generated_at, unsafe_after)``.

    """
    present = [
        timestamp
        for timestamp in (
            day_rating.updated_at if day_rating else None,
            weather_fetched_at,
        )
        if timestamp is not None
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
    ``_favourite_limit.html`` at HTTP 409 instead of creating a row. Creation
    is submitted through the client mutation queue (``static/js/favourites.js``
    → ``window.pwaMutationQueue``, SNOW-479), which classifies any non-retry
    4xx as a permanent failure; a favourites cap is permanent (only deleting a
    pin clears it), so 409 — not the transient 429 or a swap-friendly 200 — is
    what stops the queue retrying a doomed create and surfaces its
    permanent-failure toast.

    Args:
        request: The incoming HTMX POST request.

    Returns:
        Rendered saved-pin or limit-reached partial, or an error response.

    """
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
            request, "favourites/partials/_favourite_limit.html", {}, status=409
        )

    return render(
        request,
        "favourites/partials/_favourite.html",
        {"favourite": favourite},
    )


@require_htmx
@require_POST
@ratelimit(key="user", rate="10/m", block=False)
def favourite_create_from_resort(request: HttpRequest) -> HttpResponse:
    """Create a Favourite from a public Resort and return the saved-pin partial.

    Entry point is the resort-pin popup's favourite star
    (``apps/public/templates/public/partials/_resort_popup.html``), submitted via
    ``static/js/favourites.js``'s client mutation queue — same offline-capable
    path as ``favourite_create`` (SNOW-499).

    Request body:
    - ``resort_slug`` — the Resort's slug (SNOW-796; the ``id`` the resorts
      feed emits). 400 if missing, 404 if no such Resort exists.

    Mirrors ``favourite_create``'s error-status contract so the mutation
    queue classifies each outcome the same way: 409 (not 200/429) at the
    per-user cap, since only deleting a pin clears it; 422 when the resort
    has no coordinates to favourite from — also a permanent failure, since
    no retry will make the resort geocoded.

    Errors:
        403 — anonymous request.
        400 — non-HTMX request; missing ``resort_slug``.
        404 — unknown ``resort_slug``.
        409 — the user has reached ``settings.FAVOURITES_MAX_PER_USER``.
        422 — the resort has no latitude/longitude set.
        429 — rate limit exceeded (> 10 creations/min per user).

    Args:
        request: The incoming HTMX POST request.

    Returns:
        Rendered saved-pin or limit-reached partial, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before saving another pin.",
            status=429,
        )

    slug = request.POST.get("resort_slug", "").strip()
    if not slug:
        return HttpResponse("resort_slug is required.", status=400)

    try:
        resort = Resort.objects.select_related("region").get(slug=slug)
    except Resort.DoesNotExist:
        return HttpResponse("Resort not found.", status=404)

    try:
        favourite = create_resort_favourite(request.user, resort)
    except FavouriteLimitReached:
        logger.info(
            "Resort favourite create blocked: user=%s hit the cap", request.user.pk
        )
        return render(
            request, "favourites/partials/_favourite_limit.html", {}, status=409
        )
    except ResortNotGeocoded:
        return HttpResponse("This resort has no location set yet.", status=422)

    return render(
        request,
        "favourites/partials/_favourite.html",
        {"favourite": favourite},
    )


@require_htmx
@require_POST
@ratelimit(key="user", rate="10/m", block=False)
def favourite_resort_toggle(request: HttpRequest, slug: str) -> HttpResponse:
    """Toggle the requesting user's Favourite for a resort; return the button partial.

    Entry point is the resort detail page's favourite button
    (``apps/favourites/templates/favourites/partials/_resort_favourite_button.html``,
    included by ``apps/public/templates/public/resort.html``) — a simple, online
    HTMX toggle (SNOW-504), deliberately distinct from the map's offline-
    capable resort star (``favourite_create_from_resort`` / ``favourite_delete``,
    driven by ``static/js/favourites.js``'s client mutation queue): the
    resort page has no offline affordance to protect, and the map star's
    create URL is read off the map element so it can't be reused verbatim
    here.

    When the user already holds a ``(user, resort)`` Favourite it is
    deleted; otherwise one is created via ``create_resort_favourite``.
    Either way the response is the button partial rendered in its NEW
    state, which HTMX swaps in with ``hx-swap="outerHTML"``.

    Errors:
        403 — anonymous request.
        400 — non-HTMX request.
        404 — unknown ``slug``.
        409 — creating would exceed ``settings.FAVOURITES_MAX_PER_USER``.
        422 — the resort has no latitude/longitude set (can't be favourited).
        429 — rate limit exceeded (> 10 toggles/min per user).

    Args:
        request: The incoming HTMX POST request.
        slug: The Resort's slug, from the URL (SNOW-796).

    Returns:
        The rendered favourite-button partial in its new state, or an
        error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    if getattr(request, "limited", False):
        return HttpResponse(
            "Rate limit exceeded — please wait before toggling this favourite.",
            status=429,
        )

    try:
        resort = Resort.objects.select_related("region").get(slug=slug)
    except Resort.DoesNotExist:
        return HttpResponse("Resort not found.", status=404)

    existing = Favourite.objects.filter(user=request.user, resort=resort).first()
    if existing is not None:
        existing.delete()
        logger.info(
            "Resort favourite deleted via toggle: user=%s resort=%s",
            request.user.pk,
            resort.pk,
        )
        favourited = False
    else:
        try:
            create_resort_favourite(request.user, resort)
        except FavouriteLimitReached:
            logger.info(
                "Resort favourite toggle blocked: user=%s hit the cap",
                request.user.pk,
            )
            return render(
                request, "favourites/partials/_favourite_limit.html", {}, status=409
            )
        except ResortNotGeocoded:
            return HttpResponse("This resort has no location set yet.", status=422)
        favourited = True

    return render(
        request,
        "favourites/partials/_resort_favourite_button.html",
        {
            "resort": resort,
            "favourited": favourited,
            "can_favourite": True,
            "signin_url": reverse("accounts:sign_in"),
        },
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
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        delete_favourite(request.user, uuid)
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    return HttpResponse("")


def _favourite_card_context(
    favourite: Favourite, now: datetime.datetime, today: datetime.date
) -> tuple[dict[str, Any], datetime.datetime, int | None]:
    """Build the shared template context for a favourite's detail card.

    Shared by ``favourite_card`` (HTMX fragment) and ``favourite_detail``
    (SNOW-507 full page) so both endpoints render identical card content
    from one code path.

    When ``favourite.region`` is set, resolves today's ``RegionDayRating``
    for it (mirroring ``apps.public.api.region_summary``) so the card can show
    the regional danger rating and a link to the evergreen bulletin page.
    When ``region`` is ``None`` (the pin falls outside every known
    boundary), the card renders a "no bulletin coverage here" note
    instead.

    Also reads the pin's own ``Location`` for today's ``Weather`` row
    (SNOW-761) and builds both the single-day panel and the multi-day
    outlook from it. The lookup is on the ``(location, observed_on)``
    unique constraint; a pre-SNOW-704 row with no location, or a location
    with no row for today, yields ``None`` and the card's weather section
    is absent rather than empty.

    When a region and today's default bulletin both resolve, also builds
    the avalanche-problems section (SNOW-422): the bulletin's problem cards
    (``apps.public.views.problem_cards_for_bulletin``), each annotated with an
    altitude verdict relative to ``favourite.elevation``
    (``apps.favourites.relevance.annotate_problem_relevance``). Every problem the
    bulletin publishes is always included — the annotation only highlights
    relevance, it never suppresses a problem.

    Args:
        favourite: The Favourite the card describes.
        now: The reference instant for the point forecast panel's
            day/night icon decisions.
        today: The reference calendar date for the region's rating and
            default-bulletin lookups.

    Returns:
        A ``(context, generated_at, unsafe_after)`` triple — the template
        context dict for ``_favourite_card.html``, and the freshness pair
        (SNOW-370 / SNOW-418) for the caller to stamp via
        ``apply_freshness_headers``. ``generated_at`` is the older of the
        rating's ``updated_at`` and the weather row's ``fetched_at``;
        ``unsafe_after`` is the 48h horizon only when a safety-critical
        rating is present.

    """
    day_rating = None
    bulletin_url = ""
    problem_cards: list[dict[str, Any]] = []
    if favourite.region is not None:
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

    # SNOW-761: weather for the pin's own Location, read for today on the
    # (location, observed_on) unique constraint. A pre-SNOW-704 row with no
    # location, or a location with no row for today, yields None and the
    # card's weather section is simply absent.
    #
    # Read before the freshness pair, not after: the row's fetched_at is one
    # of the two constituents _card_freshness takes the older of, so the card
    # reports the staleness of the weather it actually renders.
    weather = (
        Weather.objects.for_location(favourite.location).on_date(today).first()
        if favourite.location_id
        else None
    )

    generated_at, unsafe_after = _card_freshness(
        day_rating, weather.fetched_at if weather else None
    )
    state = compute_freshness_state(generated_at, unsafe_after=unsafe_after)
    cache_payload = _build_favourite_cache_record(
        favourite, day_rating, bulletin_url, generated_at, unsafe_after
    )

    context = {
        "favourite": favourite,
        "day_rating": day_rating,
        "bulletin_url": bulletin_url,
        "problem_cards": problem_cards,
        "weather_display": build_weather_display(weather, now),
        # SNOW-783: the day, then a link. The week and the hourly detail
        # live on the location's own forecast page rather than being
        # redrawn inside a card that is already a scrolling surface.
        "forecast_url": (
            favourite.location.get_absolute_url()
            if favourite.location is not None
            else ""
        ),
        "cache_payload": cache_payload,
        "freshness_state": state,
        "freshness_generated_at": generated_at,
    }
    return context, generated_at, unsafe_after


@require_htmx
@require_GET
def favourite_card(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Render the favourite detail card partial.

    Owner-scoped via ``Favourite.objects.for_user()`` — a non-owner's uuid
    returns 404 (never 403), so the endpoint gives no existence oracle.

    Stamps the ``X-Data-Generated-At`` / ``X-Data-Max-Age`` /
    ``X-Data-Unsafe-After`` freshness headers (SNOW-370 / SNOW-418).
    Threads a ``cache_payload`` JSON sidecar + freshness indicator into the
    template context so ``static/js/favourites_offline.js`` can cache this
    pin for offline reads. Context-building is shared with
    ``favourite_detail`` (SNOW-507) via ``_favourite_card_context``.

    Renders the card's title as an ``<h2>`` — see the inline note below;
    the full-page caller ranks the same title differently.

    Args:
        request: The incoming HTMX GET request.
        uuid: The Favourite's uuid, from the URL.

    Returns:
        Rendered ``_favourite_card.html``, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        favourite = (
            Favourite.objects.for_user(request.user)
            .select_related("region", "location")
            .get(uuid=uuid)
        )
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    context, generated_at, unsafe_after = _favourite_card_context(
        favourite, timezone.now(), timezone.localdate()
    )
    # This endpoint has exactly one surface: the per-row disclosure panel on
    # /account/favourites/. The only heading above that panel is the page's
    # own <h1>, so a single favourite's title is an <h2>.
    #
    # It was an <h3> until SNOW-668, and correctly so: the panel sat inside
    # the account hub's "Favourites" <section>, whose _eyebrow heading is an
    # <h2>, and a card ranked beside its own section heading would have been
    # wrong. Moving the list to a page of its own removed that intervening
    # heading, so the rank moved with it — as did the stand-in card
    # static/js/favourites_offline.js paints into this same panel offline,
    # which has to match what this endpoint would have sent.
    #
    # The map's favourites panel never reaches here: its rows are rendered
    # with ``hide_disclosure``, so no chevron ever asks for a card. The
    # partial's default is <h2> and favourite_detail passes <h1> for its own
    # page; this stays explicit because the rank is the caller's to state.
    # See _favourite_card.html's "WHO OWNS THE TITLE'S RANK".
    context["heading_tag"] = "h2"
    response = render(request, "favourites/partials/_favourite_card.html", context)
    return apply_freshness_headers(response, generated_at, unsafe_after=unsafe_after)


@require_GET
def favourite_detail(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Render the favourite's own full, bookmarkable page (SNOW-507).

    Promotes ``favourite_card``'s content behind a real page URL
    (``/favourites/<uuid>/``) rather than only existing as an HTMX
    fragment swapped into the manage page. Deliberately NOT
    ``@require_htmx`` — this is a real page a user can navigate to
    directly or bookmark, not a fragment.

    Same gating and owner scoping as ``favourite_card``: 403 for an
    anonymous request, and 404 (never 403) for a non-owner or unknown
    uuid — no existence oracle. Builds its context via the shared
    ``_favourite_card_context`` helper and applies the same freshness
    headers.

    The response additionally sets ``Cache-Control: private, no-store`` —
    this is a per-user page and must never land in a shared cache, mirroring
    ``favourites_geojson``.

    Args:
        request: The incoming GET request.
        uuid: The Favourite's uuid, from the URL.

    Returns:
        Rendered ``favourites/favourite_detail.html``, or an error response.

    """
    if not request.user.is_authenticated:
        return HttpResponse("Authentication required.", status=403)

    try:
        favourite = (
            Favourite.objects.for_user(request.user)
            .select_related("region", "location")
            .get(uuid=uuid)
        )
    except Favourite.DoesNotExist:
        return HttpResponse("Favourite not found.", status=404)

    context, generated_at, unsafe_after = _favourite_card_context(
        favourite, timezone.now(), timezone.localdate()
    )
    response = render(request, "favourites/favourite_detail.html", context)
    response = apply_freshness_headers(
        response, generated_at, unsafe_after=unsafe_after
    )
    response["Cache-Control"] = "private, no-store"
    return response


@require_htmx
@require_GET
def favourite_list(request: HttpRequest) -> HttpResponse:
    """Render the requesting user's own favourites list partial.

    Powers two surfaces from one endpoint, chosen by the ``variant`` query
    parameter: ``/account/favourites/``, which lazy-loads this endpoint via
    ``hx-get`` on page load (no parameter), and the map sheet's favourites
    panel (``?variant=map``, SNOW-658), which gets the leaner
    ``_favourite_list_map.html`` — no row disclosure, no "view on the map"
    link. Anything other than a known variant falls back to the account
    page's template; the value picks a template out of a fixed map, it is
    never interpolated into a template path.

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
        Rendered ``_favourite_list.html`` (or ``_favourite_list_map.html``
        for ``?variant=map``), or an error response.

    """
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
        _LIST_TEMPLATES.get(request.GET.get("variant", ""), _LIST_TEMPLATE_DEFAULT),
        {
            "favourites": favourites,
            "roster_payload": roster_payload,
            "rename_url_template": _rename_url_template(),
        },
    )
    return apply_freshness_headers(
        response, response_generated_at, unsafe_after=response_unsafe_after
    )


def favourites_geojson(request: HttpRequest) -> JsonResponse:
    """Return a FeatureCollection of the requesting user's own favourites.

    Each feature is a Point with GeoJSON-ordered ``coordinates: [lon, lat]``
    (RFC 7946) and properties ``uuid``, ``name``, ``created_at`` and
    ``resort_slug`` (SNOW-499; ``null`` for a plain dropped-pin favourite).
    ``resort_slug`` lets the map client hide a favourited resort from the
    public resorts layer — it should render only as a favourite star, never
    as a plain dot as well. It is the slug, not the pk, because it has to
    match ``resorts.geojson``'s ``id`` (SNOW-796) and because this feed,
    though per-user, is still a feed. Not ``@require_htmx`` — consumed by the map's
    saved-pins layer via a JS ``fetch()`` call, not an HTMX swap.

    SNOW-658: ``created_at`` is an ISO-8601 timestamp, and it is on the
    feature rather than behind a per-pin fetch because the pin popup that
    renders it as a relative "saved" line must open offline, from the
    payload the map already holds.

    The response is marked ``Cache-Control: private, no-store`` — the
    inverse of the public ``resorts_geojson`` layer — since this payload is
    per-user and must never be shared across users by an intermediate
    cache.

    Args:
        request: The incoming GET request.

    Returns:
        A JsonResponse with a FeatureCollection payload, or a 403/404 error.

    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    favourites = list(Favourite.objects.for_user(request.user).select_related("resort"))

    features: list[dict[str, Any]] = []
    for favourite in favourites:
        properties: dict[str, Any] = {
            "uuid": str(favourite.uuid),
            "name": favourite.name,
            "created_at": favourite.created_at.isoformat(),
            "resort_slug": (
                favourite.resort.slug if favourite.resort is not None else None
            ),
        }
        # GeoJSON ordering: [longitude, latitude] per RFC 7946.
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [favourite.longitude, favourite.latitude],
                },
                "properties": properties,
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
