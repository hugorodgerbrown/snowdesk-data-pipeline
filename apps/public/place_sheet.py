"""
apps/public/place_sheet.py — the body of the map's phone place sheet (SNOW-1100).

A tap on a resort pin or a saved pin opened a 320px MapLibre popup anchored
above it (``mountDetailPopup`` in ``static/js/map.js``), which on a phone
covered much of the ground around the place the reader had asked about. On
a phone the tap now opens ``#place-sheet``, a sheet with the three detents
SNOW-1097 gave ``includes/_overlay_sheet.html``, at PEEK. Desktop keeps the
popup (``docs/decisions/sheet-detents-are-phone-only.md``).

One fragment serves both kinds and all three detents, ordered so each
detent reveals the next part of it:

  - PEEK — the region's danger tile, the place's name over the region's
    code, the level and the region's name, then the place's actions and ×.
    A resort's action is its Save star; a saved pin's are the panel row's
    own "…" menu (Share → Rename → Delete, the order every list keeps);
  - HALF — the day's weather at the place;
  - FULL — the region's avalanche problems (a saved pin's annotated against
    its own altitude), the week at the place, then for a resort its curated
    details, and the links onward: the forecast page, the resort page, the
    bulletin.

Both views read the day the map is showing (``?d=``, today by default), as
the region sheet does, so a reader looking at a past day reads that day's
weather and rating. A place with no weather row for the day has nothing for
FULL to reveal, and the fragment narrows its detents to peek and half.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

import waffle
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from apps.bulletins.services.selection import select_bulletin_for_date
from apps.core.decorators import require_htmx
from apps.favourites.models import Favourite
from apps.favourites.relevance import annotate_problem_relevance
from apps.locations.models import Location
from apps.locations.services.what3words import what3words_map_url
from apps.public.views import _DANGER_PANEL_META, problem_cards_for_bulletin
from apps.regions.models import MicroRegion, Resort
from apps.weather.models import Weather
from apps.weather.services.weather_display import (
    build_point_forecast_panel,
    build_weather_display,
)

logger = logging.getLogger(__name__)


def _target_date(request: HttpRequest) -> date | None:
    """Return the day the map is showing, or None for a malformed ``?d=``.

    Args:
        request: The incoming request.

    Returns:
        The ``d`` query parameter as a date, today when it is absent, or
        None when it does not parse.

    """
    raw = request.GET.get("d")
    if not raw:
        return timezone.localdate()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _place_context(
    *,
    region: MicroRegion | None,
    location: Location | None,
    target_date: date,
    elevation: float | None,
) -> dict[str, Any]:
    """Build what both kinds of place say about their region and weather.

    Args:
        region: The region the place sits in, or None outside coverage.
        location: The place's own Location, read for its weather.
        target_date: The day the map is showing.
        elevation: The place's altitude, to judge each problem against; None
            leaves the problems unannotated, as the favourite card does for
            a place with no stated height.

    Returns:
        The shared half of the template context.

    """
    day_rating = None
    problem_cards: list[dict[str, Any]] = []
    if region is not None:
        day_rating = RegionDayRating.objects.filter(
            region=region, date=target_date
        ).first()
        bulletin = select_bulletin_for_date(region, target_date)
        if bulletin is not None:
            cards = problem_cards_for_bulletin(bulletin)
            problem_cards = (
                annotate_problem_relevance(cards, elevation)
                if elevation is not None
                else cards
            )
    level_meta = _DANGER_PANEL_META.get(day_rating.max_rating) if day_rating else None

    # A forward day has no row of its own: the fetch writes today's row and
    # carries the days after it in that row's ``forecast`` column, which is
    # what the map's weather layer draws from too. So a forward day reads
    # today's row, and the day itself is that row's matching forecast entry.
    today = timezone.localdate()
    row_date = min(target_date, today)
    weather = (
        Weather.objects.for_location(location).on_date(row_date).first()
        if location is not None
        else None
    )
    now = timezone.now()
    forecast_panel = build_point_forecast_panel(weather, now)
    forward_day = None
    if forecast_panel is not None and target_date > today:
        forward_day = next(
            (day for day in forecast_panel["days"] if day["date"] == target_date),
            None,
        )
    return {
        "region": region,
        "day_rating": day_rating,
        "level_label": level_meta["label"] if level_meta else "",
        "target_date": target_date,
        "bulletin_url": region.get_absolute_url(target_date)
        if region is not None
        else "",
        "problem_cards": problem_cards,
        "location": location,
        "weather_display": build_weather_display(weather, now)
        if row_date == target_date
        else None,
        "forward_day": forward_day,
        "forecast_panel": forecast_panel,
        "forecast_url": (
            f"{location.get_absolute_url()}?date={target_date.isoformat()}"
            if location is not None
            else ""
        ),
    }


@require_htmx
def resort_sheet_partial(request: HttpRequest, slug: str) -> HttpResponse:
    """Render the phone place sheet's body for one resort.

    Public, as the resort popup is: resorts are a reference layer. Only the
    Save star is per-user, so the response is never cached.

    Args:
        request: The incoming HTMX GET request.
        slug: The Resort's slug, the ``id`` the resorts feed emits.

    Returns:
        The rendered ``public/partials/_place_sheet_body.html`` fragment,
        404 for an unknown resort, 400 for a malformed date.

    """
    target_date = _target_date(request)
    if target_date is None:
        return JsonResponse({"error": "bad_date"}, status=400)
    resort = get_object_or_404(Resort.objects.select_related("region"), slug=slug)

    # The primary link is where the resort leads, normally the village.
    link = (
        resort.resort_locations.select_related("location")
        .order_by("-is_primary", "role", "location__name")
        .first()
    )
    location = link.location if link is not None else None

    favourite = None
    if request.user.is_authenticated:
        favourite = Favourite.objects.filter(user=request.user, resort=resort).first()

    context = _place_context(
        region=resort.region,
        location=location,
        target_date=target_date,
        elevation=location.elevation_m if location is not None else None,
    )
    context.update(
        {
            "kind": "resort",
            "name": resort.name,
            "resort": resort,
            "can_favourite": request.user.is_authenticated,
            "favourited": favourite is not None,
            "favourite_uuid": str(favourite.uuid) if favourite else "",
            "signin_url": reverse("accounts:sign_in"),
            "resort_url": resort.get_absolute_url(),
            "is_staff": request.user.is_staff,
        }
    )
    response = render(request, "public/partials/_place_sheet_body.html", context)
    response["Cache-Control"] = "private, no-store"
    return response


@require_htmx
def favourite_sheet_partial(request: HttpRequest, uuid: UUID) -> HttpResponse:
    """Render the phone place sheet's body for one of the reader's saved pins.

    Owner-scoped, as the favourite card is: another reader's uuid is a 404,
    never a 403, so the endpoint is no existence oracle.

    Args:
        request: The incoming HTMX GET request.
        uuid: The Favourite's uuid.

    Returns:
        The rendered ``public/partials/_place_sheet_body.html`` fragment,
        404 for a pin that is not the reader's, 400 for a malformed date.

    """
    target_date = _target_date(request)
    if target_date is None:
        return JsonResponse({"error": "bad_date"}, status=400)
    if not request.user.is_authenticated:
        return HttpResponse(status=404)
    favourite = get_object_or_404(
        Favourite.objects.for_user(request.user).select_related(
            "region", "location", "resort"
        ),
        uuid=uuid,
    )

    # The row menu's Share hands over "<name> - <what3words link>", composed
    # here so autoescaping owns the name (favourites.views._attach_share_text).
    location = favourite.location
    words = (
        location.three_word_address
        if location is not None and waffle.flag_is_active(request, "what3words")
        else None
    )
    url = what3words_map_url(words)
    favourite.w3w_share_text = (  # type: ignore[attr-defined]
        f"{favourite.name} - {url}" if url and favourite.name else (url or "")
    )

    context = _place_context(
        region=favourite.region,
        location=location,
        target_date=target_date,
        elevation=favourite.elevation,
    )
    context.update(
        {
            "kind": "favourite",
            "name": favourite.name,
            "favourite": favourite,
            "resort": favourite.resort,
            "resort_url": favourite.resort.get_absolute_url()
            if favourite.resort
            else "",
        }
    )
    response = render(request, "public/partials/_place_sheet_body.html", context)
    response["Cache-Control"] = "private, no-store"
    return response
