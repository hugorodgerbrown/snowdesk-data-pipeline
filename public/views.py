"""
public/views.py — Views for the public-facing bulletin site.

Four views:
  home             Redirects to a random region from the latest bulletin issue.
  zone_redirect    Redirects /<zone>/ to /<zone>/<name>/, caching the
                   zone-slug → name-slug mapping to avoid repeat DB hits.
  bulletin_detail  Renders a single bulletin for a region, with day-based
                   navigation and data extracted from the CAAML payload.
  random_bulletins Renders the most recent bulletins for a single region as
                   compact cards on a list page, one per calendar day, in
                   reverse chronological order. Accepts an optional ``?b=N``
                   query parameter to override the number of cards shown.
  season_bulletins Full-season test page for a single region. Renders up to
                   100 bulletin cards in a responsive grid that flows from
                   multi-column on desktop to single-column on mobile.

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
from typing import Any, cast

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify

from pipeline.models import Bulletin, Region, RegionBulletin
from pipeline.schema import ValidTimePeriod
from pipeline.utils import html_to_markdown

from .guidance import load_field_guidance

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
    "no_distinct_avalanche_problem": "Icon-Avalanche-Problem-No-Distinct"
    "-Avalanche-Problem-EAWS",
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
        return cast("dict[str, Any]", bulletin.raw_data.get("properties", {}))
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

    region = random.choice(list(regions))  # noqa: S311 this isn't crypto
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


# ---------------------------------------------------------------------------
# Random bulletins list
# ---------------------------------------------------------------------------

# Per-level display metadata used by the compact panel card. Keys match the
# CAAML ``mainValue`` strings; ``icon`` is the filename inside
# ``static/icons/eaws/danger_levels/``.
_DANGER_PANEL_META: dict[str, dict[str, str]] = {
    "low": {
        "number": "1",
        "label": "Low",
        "sub": "Stable snowpack",
        "icon": "Dry-Snow-1.svg",
    },
    "moderate": {
        "number": "2",
        "label": "Moderate",
        "sub": "Cautious route selection needed",
        "icon": "Dry-Snow-2.svg",
    },
    "considerable": {
        "number": "3",
        "label": "Considerable",
        "sub": "Dangerous off-piste conditions",
        "icon": "Dry-Snow-3.svg",
    },
    "high": {
        "number": "4",
        "label": "High",
        "sub": "Very critical off-piste conditions",
        "icon": "Dry-Snow-4-5.svg",
    },
    "very_high": {
        "number": "5",
        "label": "Very high",
        "sub": "Do not enter avalanche terrain",
        "icon": "Dry-Snow-4-5.svg",
    },
}

# Human labels for the CAAML ``problemType`` enum used on the panel tags.
_PROBLEM_LABELS: dict[str, str] = {
    "new_snow": "New snow",
    "wind_slab": "Wind slab",
    "persistent_weak_layers": "Persistent weak layers",
    "wet_snow": "Wet snow",
    "gliding_snow": "Gliding snow",
    "cornices": "Cornices",
    "no_distinct_avalanche_problem": "No distinct problem",
    "favourable_situation": "Favourable situation",
}

# Human labels for the CAAML ``validTimePeriod`` enum. Derived from the
# ``ValidTimePeriod`` TextChoices so the display strings stay in sync with
# the canonical schema definition.
_TIME_PERIOD_LABELS: dict[str, str] = dict(ValidTimePeriod.choices)

_DANGER_ORDER: tuple[str, ...] = (
    "low",
    "moderate",
    "considerable",
    "high",
    "very_high",
)

# Default number of bulletins to display on the random_bulletins page when
# no ``?b=N`` query parameter is supplied.
_DEFAULT_BULLETIN_COUNT = 10

# Safety cap on the ``?b=N`` query parameter to prevent a crafted request
# from selecting an unbounded number of bulletins.
_MAX_BULLETIN_COUNT = 50


def _highest_danger_key(ratings: list[dict[str, Any]]) -> str:
    """
    Return the highest CAAML ``mainValue`` present in ``ratings``.

    Args:
        ratings: The CAAML ``dangerRatings`` list.

    Returns:
        One of the keys in :data:`_DANGER_PANEL_META`; defaults to ``"low"``
        if no recognised values are found.

    """
    highest = "low"
    for rating in ratings:
        value = rating.get("mainValue", "")
        if value in _DANGER_ORDER and _DANGER_ORDER.index(value) > _DANGER_ORDER.index(
            highest
        ):
            highest = value
    return highest


def _is_numeric_bound(value: Any) -> bool:
    """Return True iff ``value`` is a non-empty string of digits."""
    return value is not None and str(value).isdigit()


def _format_bound(value: Any) -> str:
    """
    Format a single CAAML elevation bound for display.

    Numeric strings get an ``m`` suffix (e.g. ``"2200"`` → ``"2200m"``).
    Non-numeric strings such as ``"treeline"`` are emitted as-is. An
    empty / None input returns an empty string.
    """
    if value is None or value == "":
        return ""
    text = str(value)
    return f"{text}m" if text.isdigit() else text


def _format_elevation(elevation: dict[str, Any] | None) -> str:
    """
    Render a CAAML elevation dict as a short human string.

    Accepts both numeric metre values and the literal ``"treeline"`` (the
    schema permits either). Examples::

        {"lowerBound": "2200"}                       → "above 2200m"
        {"upperBound": "2400"}                       → "below 2400m"
        {"lowerBound": "1800", "upperBound": "2400"} → "1800–2400m"
        {"lowerBound": "treeline"}                   → "above treeline"

    When both bounds are numeric the ``m`` suffix appears only once on
    the right-hand side of the range for readability. Mixed
    numeric/treeline ranges fall back to labelling each end separately.
    Returns an empty string when no bounds are present.
    """
    if not elevation:
        return ""

    lower_raw = elevation.get("lowerBound")
    upper_raw = elevation.get("upperBound")

    if _is_numeric_bound(lower_raw) and _is_numeric_bound(upper_raw):
        return f"{lower_raw}\u2013{upper_raw}m"

    lower = _format_bound(lower_raw)
    upper = _format_bound(upper_raw)

    if lower and upper:
        return f"{lower}\u2013{upper}"
    if lower:
        return f"above {lower}"
    if upper:
        return f"below {upper}"
    return ""


def _problem_signature(
    entry: dict[str, Any],
) -> tuple[Any, Any, tuple[str, ...], str, str]:
    """
    Return a hashable signature describing a CAAML avalanche problem.

    Two problems with the same signature are considered duplicates for
    the purposes of hiding redundant comments on the panel: they must
    match on elevation bounds, aspects (order-independent), time period,
    and (plain-text) comment. Problem type is deliberately NOT part of
    the signature — the user-facing rule groups by geographic scope and
    timing, not by hazard category.
    """
    elevation = entry.get("elevation") or {}
    aspects = tuple(sorted(entry.get("aspects") or []))
    return (
        elevation.get("lowerBound"),
        elevation.get("upperBound"),
        aspects,
        entry.get("validTimePeriod") or "",
        _plain_text(entry.get("comment")),
    )


def _panel_problems(props: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build the list of avalanche problems for the panel.

    Each entry carries the problem type, its human label, a plain-text
    comment (full-length — truncation is handled by the template via
    Django's ``truncatechars`` filter), the human-readable
    ``validTimePeriod`` label, a formatted elevation string, the raw
    CAAML elevation dict, the list of exposed aspects, and a
    ``hide_comment`` flag. The list is NOT deduplicated —
    two entries with the same ``problemType`` but different elevation,
    aspect, period, or comment all render separately.

    When two or more problems share the same signature (elevation +
    aspects + time period + comment), the ``hide_comment`` flag is set
    on every occurrence except the last. The template uses this to show
    a group of identical-scope problems with the shared comment printed
    only once, under the final header.

    Args:
        props: The CAAML properties dict.

    Returns:
        List of problem dicts in CAAML source order with keys:
        ``problem_type``, ``label``, ``comment``, ``time_period``,
        ``time_period_label``, ``elevation``, ``elevation_data``,
        ``aspects``, ``hide_comment``, ``field_guidance``.

    """
    raw_entries = [
        entry
        for entry in props.get("avalancheProblems", [])
        if entry.get("problemType")
    ]
    guidance = load_field_guidance()
    problems: list[dict[str, Any]] = []
    for entry in raw_entries:
        problem_type = entry["problemType"]
        label = _PROBLEM_LABELS.get(
            problem_type, problem_type.replace("_", " ").capitalize()
        )
        comment = _plain_text(entry.get("comment"))
        time_period = entry.get("validTimePeriod", "") or ""
        time_period_label = _TIME_PERIOD_LABELS.get(time_period, "")
        raw_elevation = entry.get("elevation") or {}
        elevation = _format_elevation(raw_elevation or None)
        aspects: list[str] = entry.get("aspects") or []
        field_guidance = guidance.get(problem_type)
        problems.append(
            {
                "problem_type": problem_type,
                "label": label,
                "comment": comment,
                "time_period": time_period,
                "time_period_label": time_period_label,
                "elevation": elevation,
                "elevation_data": raw_elevation,
                "aspects": aspects,
                "hide_comment": False,
                "field_guidance": field_guidance,
            }
        )

    # Hide the comment on every problem that has an identical-signature
    # duplicate later in the list. Only the last occurrence in a run of
    # duplicates keeps its comment visible.
    signatures = [_problem_signature(e) for e in raw_entries]
    for i, problem in enumerate(problems):
        if not problem["comment"]:
            continue
        if any(signatures[i] == later for later in signatures[i + 1 :]):
            problem["hide_comment"] = True

    return problems


def _build_panel_context(bulletin: Bulletin) -> dict[str, Any]:
    """
    Build the template context for a single compact bulletin panel.

    Extracts the minimum set of display fields from a ``Bulletin``'s CAAML
    payload: headline danger level, avalanche problems, a short key message,
    and a footer showing the validity window and region list.

    Each visible field is paired with a ``*_source`` key describing the
    CAAML JSON path (or Bulletin field) the value was derived from, so the
    template can surface provenance as a ``title=`` tooltip. An
    ``admin_url`` key is also populated with the Django admin change URL
    for the underlying bulletin, which the template gates on
    ``user.is_staff``.

    Args:
        bulletin: The Bulletin to summarise.

    Returns:
        A dict consumed by ``public/_bulletin_panel.html``.

    """
    props = _get_properties(bulletin)
    danger_key = _highest_danger_key(props.get("dangerRatings") or [])
    danger_meta = _DANGER_PANEL_META[danger_key]

    # Fallback key-message: used by the template when the bulletin has no
    # avalanche problems. Try avalancheProblems[0].comment first, then
    # snowpackStructure.comment, then weatherReview.comment.
    key_message = ""
    key_message_source = ""
    ap = props.get("avalancheProblems") or []
    if ap:
        key_message = _plain_text(ap[0].get("comment"))
        if key_message:
            key_message_source = "avalancheProblems[0].comment"
    if not key_message:
        key_message = _plain_text((props.get("snowpackStructure") or {}).get("comment"))
        if key_message:
            key_message_source = "snowpackStructure.comment"
    if not key_message:
        key_message = _plain_text((props.get("weatherReview") or {}).get("comment"))
        if key_message:
            key_message_source = "weatherReview.comment"

    snowpack_structure = _plain_text(
        (props.get("snowpackStructure") or {}).get("comment")
    )

    return {
        "bulletin": bulletin,
        "danger_key": danger_key,
        # Hyphenated form for CSS class names (``very_high`` → ``very-high``)
        # so the template can emit ``band-very-high`` / ``level-very-high``
        # matching the stylesheet.
        "danger_css": danger_key.replace("_", "-"),
        "danger_number": danger_meta["number"],
        "danger_label": danger_meta["label"],
        "danger_sub": danger_meta["sub"],
        "danger_icon": danger_meta["icon"],
        "danger_source": "dangerRatings[*].mainValue (highest)",
        "problems": _panel_problems(props),
        "problems_source": "avalancheProblems[*].problemType",
        "key_message": key_message,
        "key_message_source": key_message_source,
        "snowpack_structure": snowpack_structure,
        "footer_date_from": bulletin.valid_from,
        "footer_date_to": bulletin.valid_to,
        "footer_date_source": "Bulletin.valid_from / valid_to",
        "admin_url": reverse("admin:pipeline_bulletin_change", args=[bulletin.pk]),
    }


def _parse_bulletin_count(request: HttpRequest) -> int:
    """
    Parse the ``?b=N`` query parameter as a bounded positive integer.

    Returns :data:`_DEFAULT_BULLETIN_COUNT` when the parameter is absent or
    unparseable, and clamps valid values to the closed interval
    ``[1, _MAX_BULLETIN_COUNT]``.
    """
    raw = request.GET.get("b")
    if raw is None:
        return _DEFAULT_BULLETIN_COUNT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_BULLETIN_COUNT
    return max(1, min(value, _MAX_BULLETIN_COUNT))


def _select_recent_bulletins(region: Region, count: int) -> list[Bulletin]:
    """
    Return the ``count`` most recent bulletins for a region, one per day.

    Collapses each calendar day (keyed on ``valid_to``) to a single bulletin
    using the same morning/evening preference logic as the detail view's
    :func:`_select_bulletin_for_date`, so the list never shows two cards
    covering the same day.

    Args:
        region: The Region to list bulletins for.
        count: Maximum number of bulletins to return.

    Returns:
        A list of at most ``count`` Bulletins in reverse chronological order.

    """
    recent_dates = list(
        Bulletin.objects.filter(regions=region).dates("valid_to", "day", order="DESC")[
            :count
        ]
    )
    bulletins: list[Bulletin] = []
    for day in recent_dates:
        selected = _select_bulletin_for_date(region, day)
        if selected is not None:
            bulletins.append(selected)
    return bulletins


def random_bulletins(request: HttpRequest, region_id: str) -> HttpResponse:
    """
    Render the most recent bulletins for a single region as compact panels.

    Lists up to ``?b=N`` (default :data:`_DEFAULT_BULLETIN_COUNT`, max
    :data:`_MAX_BULLETIN_COUNT`) bulletins for ``region_id``, one per
    calendar day, in reverse chronological order. The region is looked up
    case-insensitively against ``Region.region_id`` so both ``CH-4115`` and
    ``ch-4115`` resolve to the same page.

    Args:
        request: The incoming HTTP request.
        region_id: SLF region identifier from the URL (e.g. ``"CH-4115"``).

    Returns:
        The rendered random_bulletins page, or a 404 if the region is
        unknown.

    """
    region = get_object_or_404(Region, region_id__iexact=region_id)
    count = _parse_bulletin_count(request)
    bulletins = _select_recent_bulletins(region, count)
    panels = [_build_panel_context(b) for b in bulletins]
    return render(
        request,
        "public/random_bulletins.html",
        {
            "region": region,
            "region_name": region.name,
            "count": count,
            "panels": panels,
            "year": datetime.date.today().year,
        },
    )


# ---------------------------------------------------------------------------
# Season test page
# ---------------------------------------------------------------------------

# Avalanche seasons run roughly Nov → May. The canonical boundary is
# November 1 — any date on or after Nov 1 belongs to the season that
# starts in that calendar year; dates before Nov 1 belong to the season
# that started in the previous calendar year.
_SEASON_START_MONTH = 11
_SEASON_START_DAY = 1

# Hard cap on bulletins rendered on the season test page.
_MAX_SEASON_BULLETINS = 100


def _season_date_range(reference: datetime.date) -> tuple[datetime.date, datetime.date]:
    """
    Return the date range for the avalanche season containing ``reference``.

    The season runs from November 1 of the start year to May 31 of the
    following year. Dates before November belong to the season that
    started the previous November.

    Args:
        reference: Any date within the desired season.

    Returns:
        A ``(season_start, season_end)`` tuple of ``datetime.date`` objects.

    """
    if reference.month >= _SEASON_START_MONTH:
        start_year = reference.year
    else:
        start_year = reference.year - 1
    season_start = datetime.date(start_year, _SEASON_START_MONTH, _SEASON_START_DAY)
    season_end = datetime.date(start_year + 1, 5, 31)
    return season_start, season_end


def _select_season_bulletins(
    region: Region,
    season_start: datetime.date,
    season_end: datetime.date,
) -> list[Bulletin]:
    """
    Return up to 100 season bulletins for a region, one per day.

    Filters to the given date range, collapses each calendar day to a
    single bulletin using the same morning/evening preference logic as the
    detail view, and returns at most :data:`_MAX_SEASON_BULLETINS` results.

    Args:
        region: The Region to list bulletins for.
        season_start: Inclusive start date of the range.
        season_end: Inclusive end date of the range.

    Returns:
        A list of Bulletins in reverse chronological order.

    """
    season_dates = list(
        Bulletin.objects.filter(
            regions=region,
            valid_to__date__gte=season_start,
            valid_to__date__lte=season_end,
        ).dates("valid_to", "day", order="DESC")[:_MAX_SEASON_BULLETINS]
    )
    bulletins: list[Bulletin] = []
    for day in season_dates:
        selected = _select_bulletin_for_date(region, day)
        if selected is not None:
            bulletins.append(selected)
    return bulletins


def season_bulletins(request: HttpRequest, region_id: str) -> HttpResponse:
    """
    Full-season test page showing up to 100 bulletin panels for a region.

    Renders every bulletin card for the current avalanche season
    (November → May) in a responsive grid that flows from multiple
    columns on wide screens to a single column on mobile. Intended as a
    UI test harness for the bulletin panel, not a production page.

    Args:
        request: The incoming HTTP request.
        region_id: SLF region identifier from the URL (e.g. ``"CH-4115"``).

    Returns:
        The rendered season_bulletins page, or a 404 if the region is
        unknown.

    """
    region = get_object_or_404(Region, region_id__iexact=region_id)

    today = timezone.now().date()
    season_start, season_end = _season_date_range(today)
    season_label = f"{season_start:%b %Y} – {season_end:%b %Y}"

    bulletins = _select_season_bulletins(region, season_start, season_end)
    panels = [_build_panel_context(b) for b in bulletins]

    return render(
        request,
        "public/season_bulletins.html",
        {
            "region": region,
            "region_name": region.name,
            "season_label": season_label,
            "panel_count": len(panels),
            "panels": panels,
            "year": today.year,
        },
    )
