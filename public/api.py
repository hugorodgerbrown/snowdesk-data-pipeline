"""
public/api.py — JSON endpoints for the interactive map.

Three lightweight endpoints consumed by ``static/js/map.js`` to render the
Swiss region choropleth:

* ``/api/today-summaries/``  — per-region danger summary for today.
* ``/api/resorts-by-region/`` — ``{region_id: [resort_name, ...]}``.
* ``/api/regions.geojson``   — FeatureCollection of region polygons.

Plain Django ``JsonResponse`` views — no DRF. The map page fetches the
three in parallel at load time; switching data sources later is a matter
of changing the URL in ``map.js``.
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from pipeline.models import Bulletin, Region, RegionBulletin

from .views import _PROBLEM_LABELS, _select_default_issue

logger = logging.getLogger(__name__)


_ASPECT_JOIN = ", "


def _format_elevation(elevation: dict[str, Any] | None) -> str:
    """
    Render a render_model ``elevation`` dict as a human-readable band.

    The render model stores elevation as ``{lower, upper, treeline}``.
    This returns strings like ``"above 2200 m"``, ``"below 1800 m"``,
    ``"1200 m – 2400 m"``, or ``""`` when no bounds are set.

    Args:
        elevation: Render-model elevation dict, or ``None``.

    Returns:
        A concise display string (may be empty).

    """
    if not elevation:
        return ""
    lower = elevation.get("lower")
    upper = elevation.get("upper")
    if lower and upper:
        return f"{lower} m – {upper} m"
    if lower:
        return f"above {lower} m"
    if upper:
        return f"below {upper} m"
    if elevation.get("treeline"):
        return "around the treeline"
    return ""


def _format_aspects(aspects: list[str] | None) -> str:
    """
    Render a render_model ``aspects`` list as a display string.

    Eight aspects → ``"all aspects"``; otherwise a comma-joined list
    preserving the input order.

    Args:
        aspects: Render-model aspects list, or ``None``.

    Returns:
        A comma-joined display string (may be empty).

    """
    if not aspects:
        return ""
    if len(aspects) >= 8:
        return "all aspects"
    return _ASPECT_JOIN.join(aspects)


def _summary_for_bulletin(bulletin: Bulletin, region_name: str) -> dict[str, Any]:
    """
    Build a single-region summary dict from a bulletin's render_model.

    Pulls the headline danger rating and the dominant (first) trait's
    first problem for the dashboard sheet. The JS only displays
    ``rating``/``subdivision`` in the current design, but the other
    fields are populated so future sheet layouts can show them without
    a round-trip change.

    Args:
        bulletin: The Bulletin to summarise.
        region_name: Region display name at the time of this bulletin.

    Returns:
        A dict with ``rating``, ``subdivision``, ``problem``,
        ``elevation``, ``aspects``, ``valid_from``, ``valid_to``,
        and ``name`` keys.

    """
    rm = bulletin.render_model or {}
    danger = rm.get("danger") or {}
    rating = danger.get("key") or "no_rating"
    subdivision = danger.get("subdivision")

    problem_label: str = ""
    elevation_text: str = ""
    aspects_text: str = ""

    traits = rm.get("traits") or []
    if traits:
        first_trait = traits[0]
        problems = first_trait.get("problems") or []
        if problems:
            first_problem = problems[0]
            ptype = first_problem.get("problem_type", "")
            problem_label = _PROBLEM_LABELS.get(
                ptype, ptype.replace("_", " ").capitalize()
            )
            elevation_text = _format_elevation(first_problem.get("elevation"))
            aspects_text = _format_aspects(first_problem.get("aspects"))

    return {
        "rating": rating,
        "subdivision": subdivision,
        "problem": problem_label,
        "elevation": elevation_text,
        "aspects": aspects_text,
        "valid_from": bulletin.valid_from.isoformat(),
        "valid_to": bulletin.valid_to.isoformat(),
        "name": region_name,
    }


def today_summaries(request: HttpRequest) -> JsonResponse:
    """
    Return per-region danger summaries for today.

    Response shape::

        {
          "CH-4115": {
            "rating": "considerable",
            "subdivision": "plus",
            "problem": "Persistent weak layers",
            "elevation": "above 2200 m",
            "aspects": "all aspects",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_to":   "2026-03-15T17:00:00+00:00",
            "name":       "Martigny – Verbier"
          },
          ...
        }

    Regions with no covering bulletin today are simply absent from the
    response — the map's fill layer falls back to ``no_rating`` colour.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse mapping region_id → summary dict.

    """
    today = timezone.localdate()

    # Batch fetch: every RegionBulletin link whose bulletin touches today.
    # One query, materialised in memory — we then run per-region selection
    # logic (morning-vs-evening rules) on the grouped lists.
    links = (
        RegionBulletin.objects.filter(
            bulletin__valid_from__date__lte=today,
            bulletin__valid_to__date__gte=today,
        )
        .select_related("region", "bulletin")
        .order_by("bulletin__valid_from")
    )

    issues_by_region: dict[str, list[Bulletin]] = {}
    names_by_region: dict[str, str] = {}
    for link in links:
        region_id = link.region.region_id
        issues_by_region.setdefault(region_id, []).append(link.bulletin)
        # Prefer the region_name_at_time from the latest bulletin; falls
        # back to the Region.name via the FK.
        names_by_region[region_id] = link.region_name_at_time or link.region.name

    summaries: dict[str, dict[str, Any]] = {}
    for region_id, issues in issues_by_region.items():
        selected = _select_default_issue(issues, today)
        if selected is None:
            continue
        summaries[region_id] = _summary_for_bulletin(
            selected, names_by_region[region_id]
        )

    return JsonResponse(summaries)


def resorts_by_region(request: HttpRequest) -> JsonResponse:
    """
    Return the ``{region_id: [resort_name, ...]}`` lookup.

    Response shape::

        {
          "CH-4115": ["La Chaux", "Verbier"],
          "CH-5221": ["Lenzerheide", "Valbella"],
          ...
        }

    Regions without any linked resorts are omitted. Resort order is
    determined by the ``Resort.Meta.ordering`` (alphabetical by name).

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse mapping region_id → list of resort names.

    """
    # Walk Region → resorts via the reverse FK. One query with
    # prefetch_related; the ``resorts`` relation is ordered alphabetically
    # by Resort.Meta.ordering so the output order is stable.
    result: dict[str, list[str]] = {}
    regions = Region.objects.prefetch_related("resorts").all()
    for region in regions:
        names = [r.name for r in region.resorts.all()]
        if names:
            result[region.region_id] = names
    return JsonResponse(result)


def regions_geojson(request: HttpRequest) -> JsonResponse:
    """
    Return a FeatureCollection of all regions with populated boundaries.

    Each feature has ``properties.id`` (region_id) and ``properties.name``
    alongside the raw boundary geometry. Regions whose ``boundary`` is
    ``None`` are skipped — there is no geometry to draw.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JsonResponse with a FeatureCollection payload.

    """
    features: list[dict[str, Any]] = []
    for region in Region.objects.exclude(boundary__isnull=True).iterator():
        features.append(
            {
                "type": "Feature",
                "geometry": region.boundary,
                "properties": {
                    "id": region.region_id,
                    "name": region.name,
                },
            }
        )
    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
        }
    )
