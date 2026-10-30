"""
apps/public/alert_views.py — The bulletin alert landing page (SNOW-1328).

``/alerts/<date>/`` is where a bundled alert lands: one row per place the
signed-in person follows, each with that day's level, what moved since the
day before in the alert's own words, and a link that opens the place's
region panel on the map for that day. A single-place alert skips this page
and opens the map directly.

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
    previous_date = target - datetime.timedelta(days=1)
    map_url = reverse("public:map")
    rows = []
    for place in alerts_service.account_places(request.user):  # type: ignore[arg-type]  # login_required guarantees a User
        current = alerts_service.day_state(place.region, target)
        previous = alerts_service.day_state(place.region, previous_date)
        sentences: list[str] = []
        if current is not None and previous is not None:
            sentences = change_sentences(summarise_change(previous, current))
        rows.append(
            {
                "label": place.label,
                "region": place.region,
                "headline": headline(current) if current is not None else "",
                "sentences": sentences,
                "has_previous": previous is not None,
                "url": f"{map_url}?d={target.isoformat()}#{place.region.region_id}",
            }
        )
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
            "since_heading": _("Since %(day)s")
            % {"day": date_format(previous_date, "l")},
        },
    )
