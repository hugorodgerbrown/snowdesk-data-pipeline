"""
apps/public/region_sheet.py — the body of the map's phone region sheet (SNOW-1098).

Tapping a region on the map selected it and showed nothing about it. The
region popup that used to answer that tap was removed (SNOW-174, SNOW-895)
because it covered the terrain the reader had just tapped. On a phone the
tap now opens ``#region-sheet``, a sheet with the three detents SNOW-1097
gave ``includes/_overlay_sheet.html``, and it opens at PEEK, which leaves the
tapped ground in view. This view renders what goes inside it, one fragment
for all three detents:

  - PEEK — the region's name, the day's danger level and the date, and the
    pin and download controls (proxies for the two roundels in the ribbon
    header, so the pin and the download each keep one owner);
  - HALF and FULL — the region's bulletin for the day, which is the bulletin
    page's own body (SNOW-1164). On a phone the sheet at full height covers
    the screen, so a link on to the page gave the reader nothing more; the
    sheet now IS the page, under its peek row. Then the resorts in the
    region, which are map objects and so belong to the sheet alone.

The body is ``public/partials/_region_body.html`` with its context from
``apps.public.views.build_bulletin_body``, and the bulletin is chosen by the
page's own rule (``_resolve_selected_issue``), so the sheet and the page
cannot show different issues or describe them differently.

The sheet opens on every width since SNOW-1173: a bottom sheet on a phone,
the inset panel on a desktop. That ticket retired the chip's region panel,
which had kept a smaller copy of the region's summary for the desktop
while the sheet was phone-only; the chip now reopens this sheet.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.cache import patch_cache_control

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.coverage import covered_region_ids
from apps.bulletins.services.problem_terrain import TerrainRule, terrain_rules
from apps.core.decorators import require_htmx
from apps.public.api import _PROVIDER_BY_COUNTRY, COUNTRY_NAMES
from apps.public.decorators import lowercase_region_id
from apps.public.views import (
    _DANGER_PANEL_META,
    _get_properties,
    _get_render_model,
    _issues_for_date,
    _resolve_selected_issue,
    build_bulletin_body,
)
from apps.regions.models import MicroRegion, Resort

logger = logging.getLogger(__name__)

# How long the browser may reuse a body without asking again (2026-10-19).
# A past day's bulletin is settled, so its body keeps for a day; today's and
# a forecast day's are amended during the day, so ten minutes, which is also
# how long map_region_sheet.js keeps a body in memory. Private: the body is
# rendered in the reader's language, and must never sit in a shared cache.
PAST_DAY_MAX_AGE_S = 24 * 60 * 60
CURRENT_DAY_MAX_AGE_S = 10 * 60

# The countries the terrain-class tiles cover (swissALTI3D). Every other
# region's sheet carries no problem-terrain switch until SNOW-693 builds an
# Alps-wide model — a switch that could paint nothing would be a promise.
TERRAIN_COUNTRIES = frozenset({"CH"})


def _problem_terrain_rules(
    country: str, selected: Bulletin | None
) -> list[TerrainRule]:
    """Return the rules the sheet's problem-terrain switch paints (SNOW-979).

    Empty — so the sheet draws no switch — unless the terrain-class tiles
    are configured (the terrain filter's own gate), the region is inside
    their coverage, and the day's bulletin describes some ground.

    Args:
        country: The region's ISO-2 country code.
        selected: The bulletin the body under the switch describes, chosen
            by the page's own rule, so the map and the text cannot describe
            different issues; ``None`` on a day with none.

    Returns:
        The bulletin's problems as terrain rules, possibly empty.

    """
    if selected is None:
        return []
    if not settings.TERRAIN_CLASS_TILE_URL or country not in TERRAIN_COUNTRIES:
        return []
    # The RAW render model, as the panel reads it before enriching — the
    # enriched copy has swapped each problem's elevation for a display object.
    return terrain_rules(_get_render_model(selected, _get_properties(selected)))


# Rule rating → the EAWS level key the legend swatch and label read.
_LEVEL_BY_RATING = {
    1: "low",
    2: "moderate",
    3: "considerable",
    4: "high",
    5: "very_high",
}


def _problem_terrain_key(rules: list[TerrainRule]) -> dict[str, Any]:
    """Return what the switch's colour key lists: only what the day can paint.

    Args:
        rules: From ``_problem_terrain_rules``.

    Returns:
        ``levels`` — one ``{"level", "label"}`` per decided rating, highest
        first; ``undecided`` — whether any rule paints the undecided grey.

    """
    ratings = sorted({r["rating"] for r in rules if r["decided"]}, reverse=True)
    return {
        "levels": [
            {
                "level": _LEVEL_BY_RATING[rating],
                "label": _DANGER_PANEL_META[_LEVEL_BY_RATING[rating]]["label"],
            }
            for rating in ratings
        ],
        "undecided": any(not r["decided"] for r in rules),
    }


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
        privately cacheable for a day when ``d`` is a past day and for ten
        minutes otherwise; 404 for an unknown region, 400 for a malformed
        date.

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

    selected = _resolve_selected_issue(
        _issues_for_date(region, target_date), target_date, None
    )

    level_meta = _DANGER_PANEL_META.get(day_rating.max_rating) if day_rating else None

    body = build_bulletin_body(request, region, target_date, selected)
    terrain = _problem_terrain_rules(country, selected)
    context = {
        # The bulletin page's body (SNOW-1164), from the page's own builder.
        **body,
        "in_map": True,
        "region": region,
        "day_rating": day_rating,
        "level_label": level_meta["label"] if level_meta else "",
        "target_date": target_date,
        # SNOW-1165: the sheet's "Showing … · Back to today" strip.
        "another_day": target_date != timezone.localdate(),
        "country_name": COUNTRY_NAMES.get(country, country),
        "covered": region.region_id in covered_region_ids(),
        "provider_name": _PROVIDER_BY_COUNTRY.get(country, ""),
        "resorts": list(Resort.objects.resorts().filter(region=region)),
        # SNOW-979: the "Show problem terrain on the map" switch and its rules.
        "terrain_rules": terrain,
        "terrain_key": _problem_terrain_key(terrain),
    }
    response = render(request, "public/partials/_region_sheet_body.html", context)
    settled = target_date < timezone.localdate()
    patch_cache_control(
        response,
        private=True,
        max_age=PAST_DAY_MAX_AGE_S if settled else CURRENT_DAY_MAX_AGE_S,
    )
    return response
