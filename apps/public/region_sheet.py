"""
apps/public/region_sheet.py — the body of the map's phone region sheet (SNOW-1098).

Tapping a region on the map selected it and showed nothing about it. The
region popup that used to answer that tap was removed (SNOW-174, SNOW-895)
because it covered the terrain the reader had just tapped. On a phone the
tap now opens ``#region-sheet``, a sheet with the three detents SNOW-1097
gave ``includes/_overlay_sheet.html``, and it opens at PEEK, which leaves the
tapped ground in view. This view renders what goes inside it, one fragment
for all three detents, ordered so each detent reveals the next part:

  - PEEK — the region's name, the day's danger level and the date, and the
    pin and download controls (proxies for the two roundels in the ribbon
    header, so the pin and the download each keep one owner);
  - HALF — where the region is, the day's avalanche problems, and the
    resorts in the region;
  - FULL — a link to the bulletin page. The sheet links to the bulletin
    rather than re-rendering it, because that page is the full-fidelity
    render (``docs/decisions/bulletin-fidelity-over-simplification.md``).

The problems are the bulletin page's own cards
(``apps.public.views.problem_cards_for_bulletin``) from the day's default
bulletin (``select_bulletin_for_date``), the same pair the favourite card
uses, so the sheet cannot describe a problem differently from the page it
links to.

The chip's region panel (``static/js/map_region_panel.js``) keeps its region
summary: the sheet is phone-only and opened by a map tap, the panel is
reached from the chip at every width, and taking the summary out of the
panel would leave a desktop reader with none.
"""

from __future__ import annotations

import logging
from datetime import date

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from apps.bulletins.services.coverage import covered_region_ids
from apps.bulletins.services.selection import select_bulletin_for_date
from apps.core.decorators import require_htmx
from apps.public.api import _PROVIDER_BY_COUNTRY, COUNTRY_NAMES
from apps.public.decorators import lowercase_region_id
from apps.public.views import _DANGER_PANEL_META, problem_cards_for_bulletin
from apps.regions.models import MicroRegion, Resort

logger = logging.getLogger(__name__)


@lowercase_region_id
@require_htmx
def region_sheet_partial(request: HttpRequest, region_id: str) -> HttpResponse:
    """Render the phone region sheet's body for one region and day.

    Query parameters:
        d (optional): ISO date ``YYYY-MM-DD`` — the day the map is showing.
            Defaults to today. A malformed value is a 400, matching
            ``api.region_summary``.

    Args:
        request: The incoming HTMX GET request.
        region_id: EAWS micro-region identifier (e.g. ``"CH-4115"``).

    Returns:
        The rendered ``public/partials/_region_sheet_body.html`` fragment,
        404 for an unknown region, 400 for a malformed date.

    """
    raw_date = request.GET.get("d")
    if raw_date:
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
    country = region.subregion.major.country
    day_rating = RegionDayRating.objects.filter(region=region, date=target_date).first()

    problem_cards: list[dict] = []
    if day_rating is not None:
        bulletin = select_bulletin_for_date(region, target_date)
        if bulletin is not None:
            problem_cards = problem_cards_for_bulletin(bulletin)

    level_meta = _DANGER_PANEL_META.get(day_rating.max_rating) if day_rating else None

    context = {
        "region": region,
        "day_rating": day_rating,
        "level_label": level_meta["label"] if level_meta else "",
        "target_date": target_date,
        "bulletin_url": region.get_absolute_url(target_date if raw_date else None),
        "country_name": COUNTRY_NAMES.get(country, country),
        "covered": region.region_id in covered_region_ids(),
        "provider_name": _PROVIDER_BY_COUNTRY.get(country, ""),
        "problem_cards": problem_cards,
        "resorts": list(Resort.objects.resorts().filter(region=region)),
    }
    return render(request, "public/partials/_region_sheet_body.html", context)
