"""
apps/public/alert_views.py — The Today page, where every alert lands.

SNOW-1328 built it as the alert landing page; SNOW-1497 made it a page of
its own.

``/today/`` lists the bulletin for every place the signed-in person follows:
one card each, A–Z, with that day's danger tile, level and how it moved
since the day before, the avalanche problems with their aspects and heights,
the forecast new snow, the provider and when it released the bulletin, and
links to the map and the provider's own bulletin. Every alert opens it
(Hugo, 2026-11-12): the alert itself only says a bulletin is out.

Which day: today, until any followed place has tomorrow's bulletin (the
evening release), then tomorrow, with a Today · Tomorrow switch either way
(``?day=today`` / ``?day=next``). A place still waiting keeps its card and
says so.

``/today/<date>/`` takes that date as today, to check what the page showed
on a past day (Hugo, 2026-11-14). It opens on the date itself, since every
past day already has the next day's bulletin, and the switch offers the day
after. ``/alerts/<date>/`` redirects there, so an alert sent before SNOW-1497
still opens the day it named.

Nothing is merged or summarised: two favourites in one region are two cards,
and a trip crossing several regions is a card per region. A saved trip on
that day is a card too (SNOW-947), and its link opens the trip. Every region
pin and saved resort is listed, alert switch on or off; dropped pins are not.

Not flag-gated, and open to a visitor, who sees what the page is for and a
way to sign in.
"""

from __future__ import annotations

import datetime
import logging

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from apps.bulletins.models import Bulletin
from apps.bulletins.services import alerts as alerts_service
from apps.bulletins.services.alert_wording import (
    ALERT_TZ,
    aspect_ranges,
    hhmm,
    level_word,
    new_snow,
    provider_name,
    trend,
)
from apps.bulletins.services.bulletin_change import BulletinState, summarise_change
from apps.public.bulletin_panels import problem_cards_for_bulletin
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)


def _parse_day(day: str) -> datetime.date:
    """
    Return a ``YYYY-MM-DD`` path segment as a date.

    Args:
        day: The segment.

    Returns:
        The date.

    Raises:
        Http404: For a malformed date.

    """
    try:
        return datetime.date.fromisoformat(day)
    except ValueError as exc:
        raise Http404 from exc


def alerts_day(request: HttpRequest, day: str) -> HttpResponse:
    """
    Send an alert link from before SNOW-1497 to the Today page on its day.

    Args:
        request: The incoming request.
        day: ``YYYY-MM-DD``.

    Returns:
        A permanent redirect to ``/today/<day>/``.

    Raises:
        Http404: For a malformed date.

    """
    target = _parse_day(day)
    return redirect("public:today_as_of", day=target.isoformat(), permanent=True)


def today(request: HttpRequest, day: str | None = None) -> HttpResponse:
    """
    Render the Today page: every followed place's bulletin for one day.

    Args:
        request: The incoming request.
        day: ``YYYY-MM-DD`` to take as today, or None for the real today.

    Returns:
        The page.

    Raises:
        Http404: For a malformed date.

    """
    real_today = timezone.localdate()
    as_of = real_today if day is None else _parse_day(day)
    base_url = (
        reverse("public:today")
        if day is None
        else reverse("public:today_as_of", kwargs={"day": as_of.isoformat()})
    )
    context: dict = {
        "another_day": as_of != real_today,
        "as_of": as_of,
        "today_url": reverse("public:today"),
    }
    if not request.user.is_authenticated:
        context["heading"] = _("Today")
        return render(request, "public/today.html", context)

    user = request.user
    next_day = as_of + datetime.timedelta(days=1)
    pins = alerts_service.account_places(user, alerts_only=False)
    next_places = alerts_service.places_on(
        pins,
        alerts_service.trip_places(user, next_day),
    )
    has_next = alerts_service.today_target(next_places, as_of) == next_day
    choice = request.GET.get("day")
    if choice == "next":
        shown = next_day
    elif choice == "today" or day is not None:
        shown = as_of
    else:
        shown = next_day if has_next else as_of

    places = (
        next_places
        if shown == next_day
        else alerts_service.places_on(
            pins,
            alerts_service.trip_places(user, shown),
        )
    )
    rows = [row for place in places for row in _rows(place, shown, real_today)]
    # An alert opens the day it was about, so a trip on the day before (as_of,
    # under the next day) would vanish from the page it was announced on.
    # Its cards keep their own day, while that day is not before as_of.
    previous_date = shown - datetime.timedelta(days=1)
    if previous_date >= as_of:
        rows += [
            row
            for place in alerts_service.trip_places(user, previous_date)
            for row in _rows(place, previous_date, real_today, day_label=True)
        ]
    rows.sort(key=lambda r: r["label"].casefold())
    switch = []
    if has_next or shown == next_day:
        switch = [
            {
                "label": _("Today · %(day)s") % {"day": date_format(as_of, "D j")},
                "href": f"{base_url}?day=today",
                "selected": shown == as_of,
            },
            {
                "label": _("Tomorrow · %(day)s")
                % {"day": date_format(next_day, "D j")},
                "href": f"{base_url}?day=next",
                "selected": shown == next_day,
            },
        ]
    context.update(
        rows=rows,
        switch=switch,
        target_date=shown,
        heading=date_format(shown, "l j F"),
    )
    return render(request, "public/today.html", context)


def _rows(
    place: alerts_service.AlertPlace,
    day: datetime.date,
    today: datetime.date,
    *,
    day_label: bool = False,
) -> list[dict]:
    """
    Return a place's cards: one per name it is followed by, per region.

    Args:
        place: The place.
        day: The day its bulletin is read for.
        today: The local date, for the new snow forecast.
        day_label: Name the day after the label, for a card not on the
            page's own day.

    Returns:
        The cards' contexts.

    """
    regions = place.covered
    out = []
    for name in place.labels or [place.region.name]:
        for region in regions:
            label = name if len(regions) == 1 else f"{name} · {region.name}"
            if day_label:
                label = f"{label} · {date_format(day, 'l')}"
            # A name's own trip page, never another name's: two trips in
            # one region are one place, and a pin beside a trip opens the map.
            url = place.label_urls.get(name, "")
            out.append(_card(label, region, day, today, url, waiting=day > today))
    return out


def _card(
    label: str,
    region: MicroRegion,
    day: datetime.date,
    today: datetime.date,
    trip_url: str,
    *,
    waiting: bool = False,
) -> dict:
    """
    Return one card's context: a region's bulletin on a day.

    Args:
        label: The card's name.
        region: The region whose bulletin it shows.
        day: The day.
        today: The local date.
        trip_url: The trip page when the card is a trip's, else "".
        waiting: The day is after the real today, so a missing bulletin
            is not out yet rather than absent.

    Returns:
        The card.

    """
    bulletin = alerts_service.authoritative_bulletin(region, day)
    current = alerts_service.day_state(region, day)
    card: dict = {
        "label": label,
        "url": f"{reverse('public:map')}?d={day.isoformat()}#{region.region_id}",
        "trip_url": trip_url,
        "rating": None,
        "missing": (
            _("%(weekday)s's bulletin isn't out yet.")
            % {"weekday": date_format(day, "l")}
            if waiting
            else _("No bulletin for this day.")
        ),
    }
    if bulletin is None or current is None:
        return card
    previous = alerts_service.day_state(region, day - datetime.timedelta(days=1))
    card.update(
        rating=_tile(current),
        level=level_word(current),
        trend=trend(summarise_change(previous, current)) if previous else "",
        problems=_problems(bulletin),
        new_snow=new_snow(alerts_service.forecast_new_snow(region, day, today)),
        provenance=_provenance(bulletin),
        provider=provider_name(bulletin.source),
        pdf_url=bulletin.pdf_url,
    )
    return card


def _problems(bulletin: Bulletin) -> list[dict]:
    """
    Return a bulletin's problems as its own page lists them.

    Read from the bulletin page's problem cards, not the alert state, which
    merges a problem's periods into one zone: a split-day bulletin keeps
    each period's aspects and band apart, named by its period.

    Args:
        bulletin: The bulletin.

    Returns:
        One entry per card: name, aspects, where and period.

    """
    out = []
    for card in problem_cards_for_bulletin(bulletin):
        aspects = list(card.get("aspects") or [])
        # ElevationBounds: falsy with no bound, ``display`` otherwise.
        elevation = card.get("elevation")
        display = str(getattr(elevation, "display", "")) if elevation else ""
        out.append(
            {
                "name": card.get("label", ""),
                "aspects": aspects,
                "where": " · ".join(
                    p for p in (aspect_ranges(tuple(aspects)), display) if p
                ),
                "period": (
                    card.get("time_period_label", "")
                    if card.get("time_period") not in ("", None, "all_day")
                    else ""
                ),
            }
        )
    return out


def _tile(state: BulletinState) -> dict:
    """Return what ``_danger_tile.html`` reads, from a bulletin state."""
    return {
        "max_rating": state.rating or "no_rating",
        "max_subdivision": state.subdivision if state.subdivision in ("+", "-") else "",
    }


def _provenance(bulletin: Bulletin) -> str:
    """
    Write who released a bulletin, when, and until when it holds.

    Args:
        bulletin: The bulletin.

    Returns:
        "SLF · released Thu 17:00 · valid to Fri 17:00".

    """
    released = bulletin.issued_at.astimezone(ALERT_TZ)
    until = bulletin.valid_to.astimezone(ALERT_TZ)
    return _("%(provider)s · released %(released)s · valid to %(until)s") % {
        "provider": provider_name(bulletin.source) or _("Unknown provider"),
        "released": f"{date_format(released, 'D')} {hhmm(released)}",
        "until": f"{date_format(until, 'D')} {hhmm(until)}",
    }
