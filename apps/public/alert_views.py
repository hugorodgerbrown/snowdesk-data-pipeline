"""
apps/public/alert_views.py — The bulletin alert landing page (SNOW-1328).

``/alerts/<date>/`` is where a bundled alert lands: one row per place the
signed-in person follows, each with that day's level, what moved since the
day before in the alert's own words, and a link that opens the place's
region panel on the map for that day. A saved trip on that day is a row too
(SNOW-947), its region the route's start, and its link opens the trip. A
single-place alert skips this page and opens the map, or the trip, directly.

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

from apps.bulletins.services import alerts as alerts_service
from apps.bulletins.services.alert_wording import change_sentences, headline
from apps.bulletins.services.bulletin_change import summarise_change

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
    places = alerts_service.places_on(
        alerts_service.account_places(user),  # type: ignore[arg-type]  # login_required guarantees a User
        alerts_service.trip_places(user, target),  # type: ignore[arg-type]  # as above
    )
    rows = [_row(place, target) for place in places]
    # A bundled alert links to the latest day it names, so a trip on the day
    # before (today, under tomorrow's link) would vanish from the page it
    # was announced on. Its row keeps its own day, while that day is not
    # yet past.
    previous_date = target - datetime.timedelta(days=1)
    if previous_date >= timezone.localdate():
        rows += [
            _row(place, previous_date, day_label=True)
            for place in alerts_service.trip_places(user, previous_date)  # type: ignore[arg-type]  # as above
        ]
    # Places that moved first, as in the alert itself.
    rows.sort(key=lambda r: (not r["sentences"], r["label"]))
    return render(
        request,
        "public/alerts_day.html",
        {
            "rows": rows,
            "target_date": target,
            "is_today": target == timezone.localdate(),
            "heading": _("Your places on %(day)s")
            % {"day": date_format(target, "l j F")},
        },
    )


def _row(
    place: alerts_service.AlertPlace, day: datetime.date, *, day_label: bool = False
) -> dict:
    """
    Return one card's context: a place's bulletin on a day, and what moved.

    Args:
        place: The place.
        day: The day its bulletin is read for.
        day_label: Name the day after the label, for a row not on the
            page's own day.

    Returns:
        The row.

    """
    previous_date = day - datetime.timedelta(days=1)
    current = alerts_service.day_state(place.region, day)
    previous = alerts_service.day_state(place.region, previous_date)
    sentences: list[str] = []
    if current is not None and previous is not None:
        sentences = change_sentences(summarise_change(previous, current))
    label = place.label
    if day_label:
        label = f"{label} · {date_format(day, 'l')}"
    return {
        "label": label,
        "region": place.region,
        "headline": headline(current) if current is not None else "",
        "sentences": sentences,
        "since_heading": _("Since %(day)s") % {"day": date_format(previous_date, "l")},
        "has_previous": previous is not None,
        "url": f"{reverse('public:map')}?d={day.isoformat()}#{place.region.region_id}",
        "trip_url": place.trip_url,
    }
