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
the issue that rated its day — ``RegionDayRating.source_bulletin``, the
latest issue forecasting the day, which is the pick the shared rule
(``apps.bulletins.services.selection``) makes for its page and sheet — so
the map and the text cannot describe different issues. The rules
themselves are the service's, stored with the render model
(``derived.terrain_rules``, SNOW-1341), so the feed is one query and
computes nothing. A region with a bulletin but no rules (a low day, a
favourable situation) is still listed, with an empty list: the client
paints its steep ground as undescribed, never blank. A region with no
rated bulletin that day is left out, and keeps its danger colour.

Empty unless the terrain-class tiles are configured, and only the
countries those tiles cover (``TERRAIN_COUNTRIES``): a rule for ground
the map cannot paint is a promise nothing keeps.

The URL carries the date, so a response is safe to cache publicly for the
same five minutes as ``/api/ratings/``.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, cast

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.ingest.public import TerrainRule, derived_of
from apps.public.views import _DANGER_PANEL_META, _get_render_model

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
    # The rules the service stored with the model (SNOW-1341). A stale row
    # reads as the version-0 sentinel, which carries none — and describes
    # no ground, as before.
    rules: list[TerrainRule] = derived_of(_get_render_model(bulletin))["terrain_rules"]
    return rules


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

    One query: the day's ratings with the bulletin that rated each
    (``RegionDayRating.source_bulletin``), its CAAML deferred. That
    bulletin is the latest issue forecasting the day, the one the region's
    page shows, and its rules are read from its render model's
    ``derived.terrain_rules``.

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

    ratings = (
        RegionDayRating.objects.filter(
            region__subregion__major__country__in=TERRAIN_COUNTRIES,
            date=target_date,
            source_bulletin__isnull=False,
        )
        .select_related("region", "source_bulletin")
        .only(
            "region",
            "source_bulletin",
            "region__region_id",
            "source_bulletin__bulletin_id",
            "source_bulletin__render_model",
            "source_bulletin__render_model_version",
        )
        .order_by("region__region_id")
    )

    rules_by_id: dict[str, list[TerrainRule]] = {}
    regions: dict[str, str] = {}
    for rating in ratings:
        selected = rating.source_bulletin
        if selected is None:  # pragma: no cover — filtered out above
            continue
        key = str(selected.bulletin_id)
        if key not in rules_by_id:
            rules_by_id[key] = bulletin_terrain_rules(selected)
        regions[rating.region.region_id] = key
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
