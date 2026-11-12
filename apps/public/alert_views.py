"""
apps/public/alert_views.py — The bulletin alert landing page (SNOW-1328).

``/alerts/<date>/`` is where every alert lands (Hugo, 2026-11-12): the alert
itself only says a bulletin is out, and this page holds the detail. One
card per place the signed-in person follows, A–Z, each with that day's
danger tile, level and how it moved since the day before, the avalanche
problems with their aspects and heights, the forecast new snow, the
provider and when it released the bulletin, and links to the map and the
provider's own bulletin.

Nothing is merged or summarised: two favourites in one region are two cards,
and a trip crossing several regions is a card per region. A saved trip on
that day is a card too (SNOW-947), and its link opens the trip.

Sign-in required, not flag-gated: an account that opted in keeps its alerts
if the rollout flag narrows, so the page they open must keep working too.
"""

from __future__ import annotations

import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
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


@login_required
def alerts_day(request: HttpRequest, day: str) -> HttpResponse:
    """
    Render every followed place's bulletin for one day.

    Args:
        request: The incoming request.
        day: ``YYYY-MM-DD``.

    Returns:
        The page.

    Raises:
        Http404: For a malformed date.

    """
    try:
        target = datetime.date.fromisoformat(day)
    except ValueError as exc:
        raise Http404 from exc
    user = request.user
    today = timezone.localdate()
    places = alerts_service.places_on(
        alerts_service.account_places(user),  # type: ignore[arg-type]  # login_required guarantees a User
        alerts_service.trip_places(user, target),  # type: ignore[arg-type]  # as above
    )
    rows = [row for place in places for row in _rows(place, target, today)]
    # An alert links to the latest day it names, so a trip on the day
    # before (today, under tomorrow's link) would vanish from the page it
    # was announced on. Its cards keep their own day, while that day is not
    # yet past.
    previous_date = target - datetime.timedelta(days=1)
    if previous_date >= today:
        rows += [
            row
            for place in alerts_service.trip_places(user, previous_date)  # type: ignore[arg-type]  # as above
            for row in _rows(place, previous_date, today, day_label=True)
        ]
    rows.sort(key=lambda r: r["label"].casefold())
    return render(
        request,
        "public/alerts_day.html",
        {
            "rows": rows,
            "target_date": target,
            "heading": date_format(target, "l j F"),
        },
    )


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
            out.append(_card(label, region, day, today, url))
    return out


def _card(
    label: str,
    region: MicroRegion,
    day: datetime.date,
    today: datetime.date,
    trip_url: str,
) -> dict:
    """
    Return one card's context: a region's bulletin on a day.

    Args:
        label: The card's name.
        region: The region whose bulletin it shows.
        day: The day.
        today: The local date.
        trip_url: The trip page when the card is a trip's, else "".

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
