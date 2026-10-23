"""
apps/public/problem_terrain.py — every region's problem-terrain rules (SNOW-1194).

SNOW-979 painted one region's steep ground by its bulletin, with the rules
carried in that region's sheet. Clicking from region to region repainted
one at a time, and Hugo found it choppy (22 Oct), so the layer — the
layers menu's "Highlight problem terrain" row — paints every region at
once, and this feed is where the map gets every region's rules from:

    GET /api/problem-terrain/?d=2026-04-27

    {
      "date": "2026-04-27",
      "bulletins": {"<bulletin_id>": [rule, …], …},
      "regions": {"CH-4115": "<bulletin_id>", …}
    }

The rules are grouped by bulletin, because one Swiss bulletin covers many
micro-regions: a day is a few dozen rule lists, not 149. Each region gets
the issue its own page and sheet show, picked by the shared rule
(``apps.bulletins.services.selection``), so the map and the text cannot
describe different issues. A region with a bulletin but no rules (a low
day, a favourable situation) is still listed, with an empty list: the
client paints its steep ground as undescribed, never blank. A region with
no bulletin that day is left out, and keeps its danger colour.

Empty unless the terrain-class tiles are configured, and only the
countries those tiles cover (``TERRAIN_COUNTRIES``): a rule for ground
the map cannot paint is a promise nothing keeps.

The URL carries the date, so a response is safe to cache publicly for the
same five minutes as ``/api/ratings/``.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date
from typing import Any, cast

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers

from apps.bulletins.models import Bulletin, RegionBulletin
from apps.bulletins.services.problem_terrain import TerrainRule, terrain_rules
from apps.bulletins.services.selection import select_default_issue
from apps.public.views import _DANGER_PANEL_META, _get_properties, _get_render_model

logger = logging.getLogger(__name__)

# The countries the terrain-class tiles cover (swissALTI3D). Every other
# region gets no rules — and its sheet no switch — until SNOW-693 builds an
# Alps-wide model.
TERRAIN_COUNTRIES = frozenset({"CH"})

# Seconds a day's payload is shared between requests, and between browsers.
_CACHE_TTL_S = 300

# Rule rating → the EAWS level key the legend swatch and label read.
_LEVEL_BY_RATING = {
    1: "low",
    2: "moderate",
    3: "considerable",
    4: "high",
    5: "very_high",
}


def bulletin_terrain_rules(bulletin: Bulletin) -> list[TerrainRule]:
    """Return one bulletin's problems as terrain rules.

    Args:
        bulletin: The issue to read.

    Returns:
        Its rules, possibly empty.

    """
    # The RAW render model, as the panel reads it before enriching — the
    # enriched copy has swapped each problem's elevation for a display object.
    return terrain_rules(_get_render_model(bulletin, _get_properties(bulletin)))


def problem_terrain_key(rules: list[TerrainRule] | None) -> dict[str, Any]:
    """Return what a problem-terrain colour key lists.

    The region sheet's key lists only what its bulletin can paint today;
    the map legend's key covers every region, so it lists everything
    (``rules=None``).

    Args:
        rules: One bulletin's rules, or None for the whole scale.

    Returns:
        ``levels`` — one ``{"level", "label"}`` per decided rating, highest
        first; ``undecided`` — whether the undecided grey can show.

    """
    if rules is None:
        ratings = sorted(_LEVEL_BY_RATING, reverse=True)
        undecided = True
    else:
        ratings = sorted({r["rating"] for r in rules if r["decided"]}, reverse=True)
        undecided = any(not r["decided"] for r in rules)
    return {
        "levels": [
            {
                "level": _LEVEL_BY_RATING[rating],
                "label": _DANGER_PANEL_META[_LEVEL_BY_RATING[rating]]["label"],
            }
            for rating in ratings
        ],
        "undecided": undecided,
    }


def build_problem_terrain_payload(target_date: date) -> dict[str, Any]:
    """Return every covered region's rules for one day, grouped by bulletin.

    Two queries: the region↔bulletin links forecasting the day, then the
    bulletins they name. Each region's issue is chosen by the shared rule,
    so it is the one that region's page shows.

    Args:
        target_date: The day the map is showing.

    Returns:
        ``{"date", "bulletins": {id: rules}, "regions": {region_id: id}}``.

    """
    payload: dict[str, Any] = {
        "date": target_date.isoformat(),
        "bulletins": {},
        "regions": {},
    }
    if not settings.TERRAIN_CLASS_TILE_URL:
        return payload

    links = RegionBulletin.objects.filter(
        region__subregion__major__country__in=TERRAIN_COUNTRIES,
        bulletin__target_date=target_date,
    ).values_list("region__region_id", "bulletin_id")
    by_region: dict[str, list[int]] = defaultdict(list)
    for region_id, bulletin_pk in links:
        by_region[region_id].append(bulletin_pk)
    if not by_region:
        return payload

    pks = {pk for region_pks in by_region.values() for pk in region_pks}
    bulletins = {b.pk: b for b in Bulletin.objects.filter(pk__in=pks)}

    rules_by_id: dict[str, list[TerrainRule]] = {}
    regions: dict[str, str] = {}
    for region_id in sorted(by_region):
        issues = sorted(
            (bulletins[pk] for pk in by_region[region_id] if pk in bulletins),
            key=lambda b: b.valid_from,
        )
        selected = select_default_issue(issues, target_date)
        if selected is None:
            continue
        key = str(selected.bulletin_id)
        if key not in rules_by_id:
            rules_by_id[key] = bulletin_terrain_rules(selected)
        regions[region_id] = key
    payload["bulletins"] = rules_by_id
    payload["regions"] = regions
    return payload


@cache_control(public=True, max_age=_CACHE_TTL_S)
@vary_on_headers("Accept-Encoding")
def problem_terrain(request: HttpRequest) -> JsonResponse:
    """Return every covered region's problem-terrain rules for one day.

    Query parameters:
        d (optional): ``YYYY-MM-DD``, the day the map shows. Defaults to
            today. Anything else is a 400, as ``/api/ratings/``.

    Args:
        request: The incoming GET request.

    Returns:
        The payload ``build_problem_terrain_payload`` describes.

    """
    raw = request.GET.get("d")
    if raw:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return JsonResponse({"error": "malformed date"}, status=400)
        try:
            target_date = date.fromisoformat(raw)
        except ValueError:
            return JsonResponse({"error": "malformed date"}, status=400)
    else:
        target_date = timezone.localdate()

    payload = cast(
        "dict[str, Any]",
        cache.get_or_set(
            f"problem-terrain:v1:{target_date.isoformat()}",
            lambda: build_problem_terrain_payload(target_date),
            timeout=_CACHE_TTL_S,
        ),
    )
    return JsonResponse(payload)
