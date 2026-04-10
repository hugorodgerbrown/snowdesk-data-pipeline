"""
public/views.py — Views for the public-facing bulletin site.

Three views:
  home            Redirects to a random region from the latest bulletin issue.
  zone_redirect   Redirects /<zone>/ to /<zone>/<name>/, caching the
                  zone-slug → name-slug mapping to avoid repeat DB hits.
  bulletin_detail Renders a single bulletin for a region, with day-based
                  navigation and data extracted from the CAAML payload.

Each page represents a single day, identified by the bulletin's ``valid_to``
date.  Two bulletins may cover a day: an evening issue (valid from ~16:00 the
previous day) and a morning update (valid from ~07:00 on the day itself).

* **Previous days**: the morning bulletin is shown when available (it
  overrides the evening forecast); otherwise the evening bulletin is used.
* **Current day**: the bulletin whose validity window contains the current
  time is shown automatically.

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
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify

from pipeline.models import Bulletin, Region, RegionBulletin
from pipeline.utils import html_to_markdown

logger = logging.getLogger(__name__)

# Mapping from CAAML danger-level keywords to display metadata.
# Colours taken from EAWS website - https://www.avalanches.org/downloads/#avalanche-danger-scale
_DANGER_MAP: dict[str, dict[str, str]] = {
    "low": {
        "number": "1",
        "label": "Low",
        "verdict": "GO",
        "colour": "#ccff66",
    },
    "moderate": {
        "number": "2",
        "label": "Moderate",
        "verdict": "GO",
        "colour": "#ffff00",
    },
    "considerable": {
        "number": "3",
        "label": "Considerable",
        "verdict": "CAUTION",
        "colour": "#ff9900",
    },
    "high": {
        "number": "4",
        "label": "High",
        "verdict": "AVOID",
        "colour": "#ff0000",
    },
    "very_high": {
        "number": "5",
        "label": "Very High",
        "verdict": "AVOID",
        "colour": "#ff0000",
    },
}

# Mapping from SLF danger codes to EAWS icons
_DANGER_PROBLEM_TYPE_ICONS = {
    "no_distinct_avalanche_problem": "Icon-Avalanche-Problem-No-Distinct-Avalanche-Problem-EAWS",
    "new_snow": "Icon-Avalanche-Problem-New-Snow-Grey-EAWS",
    "persistent_weak_layers": "Icon-Avalanche-Problem-Persistent-Weak-Layer-Grey-EAWS",
    "wind_slab": "Icon-Avalanche-Problem-Wind-Slab-Grey-EAWS",
    "wet_snow": "Icon-Avalanche-Problem-Wet-Snow-Grey-EAWS",
    "gliding_snow": "Icon-Avalanche-Problem-Gliding-Snow-Grey-EAWS",
    "cornices": "Icon-Avalanche-Problem-Cornices.svg",
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


def _extract_hazards(props: dict[str, Any]) -> list[dict[str, str]]:
    """
    Build a list of key-hazard dicts from CAAML avalanche problems.

    Each dict contains the raw ``problem_type`` (for icon lookup) and a
    human-readable ``description``.

    Args:
        props: The CAAML properties dict.

    Returns:
        List of dicts with ``problem_type`` and ``description`` keys.
    """
    problems = props.get("avalancheProblems", [])
    hazards: list[dict[str, str]] = []
    for p in problems:
        raw_type = p.get("problemType", "unknown")
        label = raw_type.replace("_", " ").capitalize()
        level = p.get("dangerRatingValue", "")
        elevation = p.get("elevation", {})
        lower = elevation.get("lowerBound") if elevation else None
        upper = elevation.get("upperBound") if elevation else None

        parts = [label]
        if level:
            parts.append(f"({level.replace('_', ' ')})")
        if lower:
            parts.append(f"above {lower}m")
        elif upper:
            parts.append(f"below {upper}m")

        comment = _plain_text(p.get("comment"))
        if comment:
            parts.append(f"\u2014 {comment}")

        hazards.append(
            {
                "problem_type": raw_type,
                "description": " ".join(parts),
            }
        )
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


def _extract_weather_review(props: dict[str, Any]) -> str:
    """
    Extract the weather review section as Markdown.

    The CAAML ``weatherReview`` contains an HTML comment summarising
    observed conditions (fresh snow, temperature, wind, etc.).

    Args:
        props: The CAAML properties dict.

    Returns:
        Markdown-formatted weather review, or empty string.
    """
    review = props.get("weatherReview", {})
    if not review:
        return ""
    return html_to_markdown(review.get("comment") or "")


def _extract_weather_forecast(props: dict[str, Any]) -> str:
    """
    Extract the weather forecast section as Markdown.

    The CAAML ``weatherForecast`` contains an HTML comment describing
    expected conditions for the next period.

    Args:
        props: The CAAML properties dict.

    Returns:
        Markdown-formatted weather forecast, or empty string.
    """
    forecast = props.get("weatherForecast", {})
    if not forecast:
        return ""
    return html_to_markdown(forecast.get("comment") or "")


def _select_bulletin_for_date(
    region: Region,
    target_date: datetime.date,
) -> Bulletin | None:
    """
    Select the appropriate bulletin for a region on a given date.

    The target date corresponds to the bulletin's ``valid_to`` date — both
    the evening issue (previous-day valid_from) and the morning update
    (same-day valid_from) share the same ``valid_to`` date.

    For past dates the morning bulletin is preferred (the most up-to-date
    daytime assessment).  For the current day the bulletin whose validity
    window contains *now* is returned.

    Args:
        region: The Region to look up.
        target_date: Calendar date identifying the day to display.

    Returns:
        The best-matching Bulletin, or None if no bulletins exist.
    """
    now = timezone.now()
    today = now.date()

    candidates = list(
        Bulletin.objects.filter(
            regions=region,
            valid_to__date=target_date,
        ).order_by("-valid_from")
    )

    if not candidates:
        return None

    if target_date >= today:
        # Current or future day — pick the bulletin valid right now.
        for b in candidates:
            if b.valid_from <= now <= b.valid_to:
                return b
        # Nothing spans *now*; return the most recently started.
        return candidates[0]

    # Past day — prefer the morning bulletin (valid_from on the same date).
    for b in candidates:
        if b.valid_from.date() == target_date:
            return b
    # No morning bulletin; fall back to the evening issue.
    return candidates[0]


def _get_nav_dates(
    region: Region,
    current_date: datetime.date,
) -> tuple[datetime.date | None, datetime.date | None]:
    """
    Find the previous and next dates with bulletins for a region.

    Dates are derived from the ``valid_to`` field so that each calendar day
    maps to exactly one page.

    Args:
        region: The Region to navigate within.
        current_date: The date currently being viewed.

    Returns:
        A (prev_date, next_date) tuple; either may be None.
    """
    today = timezone.now().date()

    prev_b = (
        Bulletin.objects.filter(
            regions=region,
            valid_to__date__lt=current_date,
        )
        .order_by("-valid_to")
        .only("valid_to")
        .first()
    )
    prev_date = prev_b.valid_to.date() if prev_b else None

    next_date: datetime.date | None = None
    if current_date < today:
        next_b = (
            Bulletin.objects.filter(
                regions=region,
                valid_to__date__gt=current_date,
                valid_to__date__lte=today,
            )
            .order_by("valid_to")
            .only("valid_to")
            .first()
        )
        next_date = next_b.valid_to.date() if next_b else None

    return prev_date, next_date


def _build_bulletin_context(
    bulletin: Bulletin,
    region: Region,
    region_name: str,
    page_date: datetime.date,
    prev_date: datetime.date | None,
    next_date: datetime.date | None,
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
        page_date: The calendar date this page represents.
        prev_date: Previous date with bulletins (or None).
        next_date: Next date with bulletins (or None).
        related_regions: List of dicts with 'name' and 'slug' keys.

    Returns:
        Template context dict.
    """
    props = _get_properties(bulletin)
    danger = _extract_danger(props)

    now = timezone.now()
    today = now.date()
    is_today = page_date == today

    # When viewing today and there is no next date yet, show the time
    # the next bulletin is due as a disabled placeholder in the nav.
    next_update_time: datetime.datetime | None = None
    if (
        is_today
        and next_date is None
        and bulletin.next_update
        and bulletin.next_update > now
    ):
        next_update_time = bulletin.next_update

    return {
        "region": region,
        "region_name": region_name,
        "bulletin": bulletin,
        "page_date": page_date,
        "is_today": is_today,
        "prev_date": prev_date,
        "next_date": next_date,
        "next_update_time": next_update_time,
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
        # Weather sections (derived from CAAML comments, Markdown formatted).
        "weather_review": _extract_weather_review(props),
        "weather_forecast": _extract_weather_forecast(props),
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
            {
                "bulletin": None,
                "region_name": "Snowdesk",
                "year": datetime.date.today().year,
            },
        )

    region_ids = (
        RegionBulletin.objects.filter(bulletin__issued_at__date=latest.issued_at.date())
        .values_list("region_id", flat=True)
        .distinct()
    )

    regions = Region.objects.filter(pk__in=region_ids)
    if not regions.exists():
        return render(
            request,
            "public/bulletin.html",
            {
                "bulletin": None,
                "region_name": "Snowdesk",
                "year": datetime.date.today().year,
            },
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
    Render the bulletin viewer for a given region on a specific day.

    Each page represents a single calendar day.  An optional ``?date=``
    query parameter (``YYYY-MM-DD``) selects the day; without it the view
    defaults to the current day.

    For past days the morning bulletin is shown (the updated daytime
    assessment).  For the current day the bulletin whose validity window
    contains the current time is shown automatically.

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

    # Determine the target date.
    today = timezone.now().date()
    date_param = request.GET.get("date")
    if date_param:
        try:
            target_date = datetime.date.fromisoformat(date_param)
        except ValueError:
            target_date = today
    else:
        target_date = today

    # Select the best bulletin for this region and date.
    selected = _select_bulletin_for_date(region, target_date)

    if selected is None:
        return render(
            request,
            "public/bulletin.html",
            {
                "bulletin": None,
                "region_name": region.name,
                "year": datetime.date.today().year,
            },
        )

    # The page date is the valid_to date of the selected bulletin.
    page_date = selected.valid_to.date()

    # Region name as it appeared in this bulletin.
    link = (
        RegionBulletin.objects.filter(bulletin=selected, region=region)
        .values_list("region_name_at_time", flat=True)
        .first()
    )
    region_name = link or region.name

    # Day-based prev/next navigation.
    prev_date, next_date = _get_nav_dates(region, page_date)

    # Related regions — other regions covered by the same bulletin.
    sibling_links = (
        RegionBulletin.objects.filter(bulletin=selected)
        .exclude(region=region)
        .select_related("region")
    )
    related_regions = [
        {
            "name": link.region_name_at_time or link.region.name,
            "slug": link.region.slug,
        }
        for link in sibling_links
    ]

    context = _build_bulletin_context(
        bulletin=selected,
        region=region,
        region_name=region_name,
        page_date=page_date,
        prev_date=prev_date,
        next_date=next_date,
        related_regions=related_regions,
    )
    return render(request, "public/bulletin.html", context)
