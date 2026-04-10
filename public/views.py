"""
public/views.py — Views for the public-facing bulletin site.

Three views:
  home            Redirects to a random region from the latest bulletin issue.
  zone_redirect   Redirects /<zone>/ to /<zone>/<name>/, caching the
                  zone-slug → name-slug mapping to avoid repeat DB hits.
  bulletin_detail Renders a single bulletin for a region, with prev/next
                  navigation and data extracted from the CAAML payload.

The CAAML raw data does not contain the AI-generated summary fields
described in site-structure.md (overallVerdict, activity ratings, structured
weather, etc.). Where possible, equivalent values are derived from the raw
CAAML data; sections with no available data are omitted from the template
context so the template hides them gracefully.
"""

from __future__ import annotations

import datetime
import logging
import random
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils.text import slugify

from pipeline.models import Bulletin, Region, RegionBulletin

logger = logging.getLogger(__name__)

# Maximum number of recent bulletins to fetch for navigation (~30 days).
_NAV_LIMIT = 60

# Mapping from CAAML danger-level keywords to display metadata.
_DANGER_MAP: dict[str, dict[str, str]] = {
    "low": {"number": "1", "label": "Low", "verdict": "GO", "colour": "green"},
    "moderate": {
        "number": "2",
        "label": "Moderate",
        "verdict": "GO",
        "colour": "green",
    },
    "considerable": {
        "number": "3",
        "label": "Considerable",
        "verdict": "CAUTION",
        "colour": "amber",
    },
    "high": {
        "number": "4",
        "label": "High",
        "verdict": "AVOID",
        "colour": "red",
    },
    "very_high": {
        "number": "5",
        "label": "Very High",
        "verdict": "AVOID",
        "colour": "red",
    },
}


def _get_properties(bulletin: Bulletin) -> dict[str, Any]:
    """
    Extract the CAAML properties dict from a bulletin's GeoJSON envelope.

    Args:
        bulletin: A Bulletin instance.

    Returns:
        The properties dict, or empty dict if absent.
    """
    if bulletin.raw_data:
        return bulletin.raw_data.get("properties", {})
    return {}


def _plain_text(html: str | None) -> str:
    """
    Strip HTML tags and collapse whitespace to produce readable plain text.

    Args:
        html: Raw HTML string (or None).

    Returns:
        Cleaned plain-text string, or empty string.
    """
    if not html:
        return ""
    text = strip_tags(html)
    # Collapse runs of whitespace into single spaces.
    return " ".join(text.split())


def _extract_danger(props: dict[str, Any]) -> dict[str, str | None]:
    """
    Derive danger level, verdict, and colour from CAAML dangerRatings.

    Uses the highest danger rating present in the bulletin.

    Args:
        props: The CAAML properties dict.

    Returns:
        Dict with keys: danger_level, overall_verdict, verdict_colour.
    """
    ratings = props.get("dangerRatings", [])
    if not ratings:
        return {"danger_level": None, "overall_verdict": None, "verdict_colour": None}

    # Find the highest danger level across all rating entries.
    level_order = ["low", "moderate", "considerable", "high", "very_high"]
    highest = "low"
    for r in ratings:
        value = r.get("mainValue", "low")
        if value in level_order and level_order.index(value) > level_order.index(
            highest
        ):
            highest = value

    info = _DANGER_MAP.get(highest, _DANGER_MAP["low"])
    return {
        "danger_level": f"Level {info['number']} \u2014 {info['label']}",
        "overall_verdict": info["verdict"],
        "verdict_colour": info["colour"],
    }


def _extract_hazards(props: dict[str, Any]) -> list[str]:
    """
    Build a list of key-hazard descriptions from CAAML avalanche problems.

    Args:
        props: The CAAML properties dict.

    Returns:
        List of human-readable hazard strings.
    """
    problems = props.get("avalancheProblems", [])
    hazards: list[str] = []
    for p in problems:
        problem_type = p.get("problemType", "unknown").replace("_", " ").capitalize()
        level = p.get("dangerRatingValue", "")
        elevation = p.get("elevation", {})
        lower = elevation.get("lowerBound") if elevation else None
        upper = elevation.get("upperBound") if elevation else None

        parts = [problem_type]
        if level:
            parts.append(f"({level.replace('_', ' ')})")
        if lower:
            parts.append(f"above {lower}m")
        elif upper:
            parts.append(f"below {upper}m")

        comment = _plain_text(p.get("comment"))
        if comment:
            parts.append(f"\u2014 {comment[:120]}")

        hazards.append(" ".join(parts))
    return hazards


def _extract_summary(props: dict[str, Any]) -> str:
    """
    Extract a summary paragraph from the snowpack-structure comment.

    Falls back to the weather-review comment if snowpack is absent.

    Args:
        props: The CAAML properties dict.

    Returns:
        Plain-text summary, or empty string.
    """
    snowpack = props.get("snowpackStructure", {})
    text = _plain_text(snowpack.get("comment") if snowpack else None)
    if text:
        return text

    review = props.get("weatherReview", {})
    return _plain_text(review.get("comment") if review else None)


def _extract_outlook(props: dict[str, Any]) -> str:
    """
    Extract an outlook paragraph from the tendency comments.

    Args:
        props: The CAAML properties dict.

    Returns:
        Plain-text outlook, or empty string.
    """
    tendency = props.get("tendency", [])
    if not tendency:
        return ""
    comments = [
        _plain_text(t.get("comment"))
        for t in tendency
        if isinstance(t, dict) and t.get("comment")
    ]
    return " ".join(comments)


def _build_bulletin_context(
    bulletin: Bulletin,
    region: Region,
    region_name: str,
    prev_bulletin: Bulletin | None,
    next_bulletin: Bulletin | None,
    related_regions: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Assemble the full template context for a bulletin page.

    Extracts displayable data from the CAAML raw payload and combines it
    with navigation and region metadata.

    Args:
        bulletin: The selected Bulletin to display.
        region: The Region being viewed.
        region_name: Human-readable region name (from RegionBulletin).
        prev_bulletin: The older bulletin (or None).
        next_bulletin: The newer bulletin (or None).
        related_regions: List of dicts with 'name' and 'slug' keys.

    Returns:
        Template context dict.
    """
    props = _get_properties(bulletin)
    danger = _extract_danger(props)

    return {
        "region": region,
        "region_name": region_name,
        "bulletin": bulletin,
        "prev_bulletin": prev_bulletin,
        "next_bulletin": next_bulletin,
        "year": datetime.date.today().year,
        # Danger / verdict (derived from CAAML dangerRatings).
        "danger_level": danger["danger_level"],
        "overall_verdict": danger["overall_verdict"],
        "verdict_colour": danger["verdict_colour"],
        # Text sections (derived from CAAML comments).
        "summary": _extract_summary(props),
        "outlook": _extract_outlook(props),
        # Activity ratings — requires AI-generated data (not in CAAML).
        "on_piste": None,
        "off_piste": None,
        "ski_touring": None,
        # Key hazards (derived from CAAML avalanche problems).
        "key_hazards": _extract_hazards(props),
        # Structured weather — requires AI-generated data (not in CAAML).
        "weather": None,
        # Related regions covered by the same bulletin.
        "related_regions": related_regions,
    }


def _cache_key(zone_slug: str) -> str:
    """
    Return the cache key for a zone-slug → name-slug mapping.

    Args:
        zone_slug: The region's URL slug (e.g. "ch-4115").

    Returns:
        A namespaced cache key string.
    """
    return f"public:zone_name:{zone_slug}"


# Cache timeout for zone-slug → name-slug mappings (1 hour).
_ZONE_NAME_CACHE_TIMEOUT = 60 * 60


def _get_name_slug(region: Region) -> str:
    """
    Return a URL-safe slug derived from the region's human-readable name.

    Caches the result so subsequent requests for the same zone skip the
    database entirely.

    Args:
        region: A Region instance.

    Returns:
        Slugified region name (e.g. "valais").
    """
    name_slug = slugify(region.name)
    cache.set(_cache_key(region.slug), name_slug, timeout=_ZONE_NAME_CACHE_TIMEOUT)
    return name_slug


def home(request: HttpRequest) -> HttpResponse:
    """
    Redirect to a random region's bulletin page.

    Finds the most recent bulletin issue date, picks a random region from
    that issue, and redirects. Returns a simple empty-state page if there
    are no bulletins in the database.

    Args:
        request: The incoming HTTP request.

    Returns:
        A redirect response, or an empty-state HTML response.
    """
    latest = Bulletin.objects.order_by("-issued_at").first()
    if not latest:
        return render(
            request,
            "public/bulletin.html",
            {"bulletin": None, "region_name": "Snowdesk", "year": datetime.date.today().year},
        )

    region_ids = RegionBulletin.objects.filter(
        bulletin__issued_at__date=latest.issued_at.date()
    ).values_list("region_id", flat=True).distinct()

    regions = Region.objects.filter(pk__in=region_ids)
    if not regions.exists():
        return render(
            request,
            "public/bulletin.html",
            {"bulletin": None, "region_name": "Snowdesk", "year": datetime.date.today().year},
        )

    region = random.choice(list(regions))
    name_slug = _get_name_slug(region)
    return redirect("public:bulletin", zone=region.slug, name=name_slug)


def zone_redirect(request: HttpRequest, zone: str) -> HttpResponse:
    """
    Redirect a naked zone URL to the full /<zone>/<name>/ URL.

    Looks up the region name slug from cache first; only hits the database
    on a cache miss. Query parameters (e.g. ``?id=...``) are preserved on
    the redirect.

    Args:
        request: The incoming HTTP request.
        zone: The region URL slug (e.g. "ch-4115").

    Returns:
        A 302 redirect to the canonical /<zone>/<name>/ URL.
    """
    name_slug = cache.get(_cache_key(zone))
    if name_slug is None:
        region = get_object_or_404(Region, slug=zone)
        name_slug = _get_name_slug(region)

    url = reverse("public:bulletin", kwargs={"zone": zone, "name": name_slug})
    if request.GET:
        url = f"{url}?{request.GET.urlencode()}"
    return redirect(url)


def bulletin_detail(request: HttpRequest, zone: str, name: str) -> HttpResponse:
    """
    Render the bulletin viewer for a given region.

    Fetches up to 60 recent bulletins for the region (newest first).
    Accepts an optional ``?id=<bulletinId>`` query parameter to view a
    specific historical bulletin; defaults to the latest.

    The ``name`` segment is cosmetic (for readable URLs) and is not used
    for lookup — the region is resolved entirely from ``zone``.

    Args:
        request: The incoming HTTP request.
        zone: The region URL slug (e.g. "ch-4115").
        name: Slugified region name (e.g. "valais"); not used for lookup.

    Returns:
        The rendered bulletin page.
    """
    region = get_object_or_404(Region, slug=zone)

    # Warm the cache so that zone_redirect can serve future requests
    # without a DB hit.
    cache.set(_cache_key(zone), slugify(region.name), timeout=_ZONE_NAME_CACHE_TIMEOUT)

    # Fetch recent bulletins for this region, newest first.
    links = (
        RegionBulletin.objects.filter(region=region)
        .select_related("bulletin")
        .order_by("-bulletin__issued_at")[:_NAV_LIMIT]
    )
    bulletins = [link.bulletin for link in links]
    region_names = {link.bulletin_id: link.region_name_at_time for link in links}

    if not bulletins:
        return render(
            request,
            "public/bulletin.html",
            {
                "bulletin": None,
                "region_name": region.name,
                "year": datetime.date.today().year,
            },
        )

    # Select the requested bulletin, or default to the latest.
    selected_id = request.GET.get("id")
    selected: Bulletin | None = None
    selected_idx = 0

    if selected_id:
        for i, b in enumerate(bulletins):
            if b.bulletin_id == selected_id:
                selected = b
                selected_idx = i
                break
        if selected is None:
            # Requested bulletin not in this region — fall back to latest.
            selected = bulletins[0]
            selected_idx = 0
    else:
        selected = bulletins[0]
        selected_idx = 0

    # Prev (older) and next (newer) for navigation.
    prev_bulletin = bulletins[selected_idx + 1] if selected_idx < len(bulletins) - 1 else None
    next_bulletin = bulletins[selected_idx - 1] if selected_idx > 0 else None

    # Region name as it appeared in this bulletin.
    region_name = region_names.get(selected.pk, region.name) or region.name

    # Related regions — other regions covered by the same bulletin.
    sibling_links = (
        RegionBulletin.objects.filter(bulletin=selected)
        .exclude(region=region)
        .select_related("region")
    )
    related_regions = [
        {"name": link.region_name_at_time or link.region.name, "slug": link.region.slug}
        for link in sibling_links
    ]

    context = _build_bulletin_context(
        bulletin=selected,
        region=region,
        region_name=region_name,
        prev_bulletin=prev_bulletin,
        next_bulletin=next_bulletin,
        related_regions=related_regions,
    )
    return render(request, "public/bulletin.html", context)
