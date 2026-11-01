"""
apps/public/place_sheet.py — the body of the map's place sheet (SNOW-1100).

A tap on a resort pin or a saved pin opened a 320px MapLibre popup anchored
above it (``mountDetailPopup`` in ``static/js/map.js``), which on a phone
covered much of the ground around the place the reader had asked about. On
a phone the tap now opens ``#place-sheet``, a sheet with the three detents
SNOW-1097 gave ``includes/_overlay_sheet.html``, at PEEK. Since SNOW-1161 a
resort opens this sheet on a desktop too, as the inset panel with no
detents (``docs/decisions/the-map-opens-a-resort-in-the-sheet.md``); a
saved pin keeps its desktop popup.

One fragment serves both kinds and all three detents. PEEK is one row for
both: the region's danger tile, the place's name over the region's code,
the level and the region's name, then the place's actions and ×. A
resort's action is its Save star; a saved pin's are the panel row's own
"…" menu (Share → Rename → Delete, the order every list keeps).

Under the peek row a RESORT shows the resort page's own body for the map's
day (SNOW-1164, ``views.build_resort_body`` and
``public/partials/_resort_body.html``), so on a phone the sheet at full
height is the page: the bulletin card, the day's problem cards, the
forecast, the season calendar and the facts. The sheet's own links
onward ("Read the bulletin", "View resort") went with it. A SAVED PIN
keeps its own: HALF the day's weather at the place and the link to its
forecast page, FULL the region's problems annotated against
the pin's altitude, then the day's bulletin and a saved resort's page.

Both views read the day the map is showing (``?d=``, today by default), as
the region sheet does, so a reader looking at a past day reads that day's
weather and rating. A saved pin with no rating for the day and no resort
page has nothing for FULL to reveal, and the fragment narrows its detents
to peek and half.
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
from django.utils.cache import patch_cache_control

from apps.accounts.identity import attest_principal
from apps.bulletins.models import RegionDayRating
from apps.bulletins.services.selection import select_bulletin_for_date
from apps.core.decorators import require_htmx
from apps.favourites.models import Favourite
from apps.favourites.relevance import annotate_problem_relevance
from apps.locations.models import Location
from apps.locations.services.what3words import what3words_map_url
from apps.public.region_sheet import CURRENT_DAY_MAX_AGE_S, PAST_DAY_MAX_AGE_S
from apps.public.views import (
    _DANGER_PANEL_META,
    build_resort_body,
    problem_cards_for_bulletin,
)
from apps.regions.models import MicroRegion, Resort
from apps.weather.services.nearest import nearest_forecast
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


def _peek_context(region: MicroRegion | None, target_date: date) -> dict[str, Any]:
    """Build what the peek row says about the place's region on the day.

    Args:
        region: The region the place sits in, or None outside coverage.
        target_date: The day the map is showing.

    Returns:
        ``region``, ``day_rating`` (a RegionDayRating or None) and
        ``level_label`` (the level's name, or "").

    """
    day_rating = (
        RegionDayRating.objects.filter(region=region, date=target_date).first()
        if region is not None
        else None
    )
    level_meta = _DANGER_PANEL_META.get(day_rating.max_rating) if day_rating else None
    return {
        "region": region,
        "day_rating": day_rating,
        "level_label": level_meta["label"] if level_meta else "",
    }


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
        location: The place's own Location; its weather is borrowed from
            the curated point nearest it.
        target_date: The day the map is showing.
        elevation: The place's altitude, to judge each problem against; None
            leaves the problems unannotated, as the favourite card does for
            a place with no stated height.

    Returns:
        The shared half of the template context.

    """
    problem_cards: list[dict[str, Any]] = []
    if region is not None:
        bulletin = select_bulletin_for_date(region, target_date)
        if bulletin is not None:
            cards = problem_cards_for_bulletin(bulletin)
            problem_cards = (
                annotate_problem_relevance(cards, elevation)
                if elevation is not None
                else cards
            )
    # A forward day has no row of its own: the fetch writes today's row and
    # carries the days after it in that row's ``forecast`` column, which is
    # what the map's weather layer draws from too. So a forward day reads
    # today's row, and the day itself is that row's matching forecast entry.
    #
    # The row is BORROWED (Hugo, 2026-10-28): a pin buys no fetch of its
    # own, so the sheet reads the curated point nearest it, at any
    # distance, and names it under the weather.
    today = timezone.localdate()
    row_date = min(target_date, today)
    source = (
        nearest_forecast(location.latitude, location.longitude, row_date)
        if location is not None
        else None
    )
    weather = source.row if source is not None else None
    now = timezone.now()
    # The week is on the forecast point's own page. "" for a location
    # backfill_location_short_ids has not reached: it has no page yet, so
    # the sheet offers no link rather than a bare "?date=".
    base_url = source.location.get_absolute_url() if source is not None else ""
    forecast_panel = build_point_forecast_panel(weather)
    forward_day = None
    if forecast_panel is not None and target_date > today:
        forward_day = next(
            (day for day in forecast_panel["days"] if day["date"] == target_date),
            None,
        )
    return {
        **_peek_context(region, target_date),
        "target_date": target_date,
        "bulletin_url": region.get_absolute_url(target_date)
        if region is not None
        else "",
        "problem_cards": problem_cards,
        "location": location,
        "forecast_source": source,
        "weather_display": build_weather_display(weather, now)
        if row_date == target_date
        else None,
        "forward_day": forward_day,
        # The page opens a forward day inside today's row with that day
        # selected (SNOW-1105), so the link names the day itself.
        "forecast_url": f"{base_url}?date={target_date.isoformat()}"
        if base_url
        else "",
        # SNOW-1313: the point's short id rides on the link, so the map can
        # open its weather sheet in place (map_place_sheet.js) rather than
        # navigate — the sheet a download saved, readable offline.
        "forecast_short_id": source.location.short_id or ""
        if source is not None and base_url
        else "",
    }


@require_htmx
def resort_sheet_partial(request: HttpRequest, slug: str) -> HttpResponse:
    """Render the place sheet's body for one resort.

    Public: resorts are a reference layer, and since SNOW-1318 the body
    reads nothing about the reader — no ``request.user`` at all. The Save
    star is rendered neutral (a disabled outline, and a hidden sign-in
    link beside it) and ``map_place_sheet.js`` paints it from the
    favourites feed ``map.js`` already holds (``window.pwaFavouritedResorts``).
    So one copy serves every reader, and an area download keeps it
    unstamped for an offline read (``'public-partial'`` in
    basemap_cache_core.js), as it keeps the region sheet.

    Args:
        request: The incoming HTMX GET request.
        slug: The Resort's slug, the ``id`` the resorts feed emits.

    Returns:
        The rendered ``public/partials/_place_sheet_body.html`` fragment,
        privately cacheable for a day when ``d`` is a past day and for ten
        minutes otherwise; 404 for an unknown resort, 400 for a malformed
        date.

    """
    target_date = _target_date(request)
    if target_date is None:
        return JsonResponse({"error": "bad_date"}, status=400)
    resort = get_object_or_404(
        Resort.objects.select_related("region", "location"), slug=slug
    )

    # SNOW-1164: under the peek row, the resort page's own body for the
    # map's day — built by the page's own builder, so the two cannot drift.
    context = {
        # The body states the region's level itself (its bulletin callout).
        **build_resort_body(resort, target_date),
        # The day the body's dated eyebrows name (includes/_dated_eyebrow.html;
        # Hugo, 29 Oct 2026) — the sheet's only, since the page's title names it.
        "section_day": target_date,
        "another_day": target_date != timezone.localdate(),
        "kind": "resort",
        "name": resort.name,
        "signin_url": reverse("accounts:sign_in"),
    }
    response = render(request, "public/partials/_place_sheet_body.html", context)
    # SNOW-1318: the region sheet's policy (``region_sheet_partial``) — a
    # past day's body for a day, today's or a forward day's for ten
    # minutes. ``private`` keeps it out of shared caches; the worker keeps
    # its own copy for a downloaded area under path and ``d``.
    settled = target_date < timezone.localdate()
    patch_cache_control(
        response,
        private=True,
        max_age=PAST_DAY_MAX_AGE_S if settled else CURRENT_DAY_MAX_AGE_S,
    )
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

    elevation = favourite.pin_elevation
    context = _place_context(
        region=favourite.region,
        location=location,
        target_date=target_date,
        elevation=elevation,
    )
    context.update(
        {
            "kind": "favourite",
            "name": favourite.name,
            "favourite": favourite,
            "resort": favourite.resort,
            # A resort backfill_resort_slugs has not reached has no page yet,
            # and reversing a null slug raises.
            "resort_url": favourite.resort.get_absolute_url()
            if favourite.resort is not None and favourite.resort.slug
            else "",
        }
    )
    response = render(request, "public/partials/_place_sheet_body.html", context)
    # SNOW-1313: ``private, no-cache`` rather than ``no-store``, so the
    # service worker may keep the owner's copy for an offline read of a
    # downloaded area — stamped with the principal the answer names
    # (``attest_principal``) and served back to that principal alone
    # (``'private-partial'`` in basemap_cache_core.js). ``private`` keeps it
    # out of every shared cache; ``no-cache`` makes an online read
    # revalidate, so a pin deleted elsewhere stops opening the moment the
    # network can say so (its 404 drops the kept copy).
    response["Cache-Control"] = "private, no-cache"
    return attest_principal(response, request)
