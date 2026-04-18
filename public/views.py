"""
public/views.py — Views for the public-facing bulletin site.

URL structure:
  /                                          Marketing homepage.
  /examples/random/                          Random bulletin (rendered inline).
  /examples/category/<danger_level>/         Random bulletin by danger level.
  /random/                                   Deprecated → /examples/random/.
  /<region_id>/                              Redirects to /<region_id>/<slug>/.
  /<region_id>/<slug>/                       Today's bulletin for a region.
  /<region_id>/<slug>/<date>/                Bulletin for a specific date.
  /<region_id>/season/                       Full-season page (up to 100 panels).

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

import calendar as _calendar_module
import dataclasses
import datetime
import json
import logging
import random
from typing import Any, cast
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.db.models import Max
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.functional import Promise
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.translation import gettext as _gettext, gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import condition

from pipeline.decorators import require_htmx
from pipeline.models import Bulletin, Region, RegionBulletin, RegionDayRating
from pipeline.schema import ValidTimePeriod
from pipeline.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
    compute_day_character,
)
from pipeline.utils import html_to_markdown

from .guidance import load_field_guidance

logger = logging.getLogger(__name__)

# Mapping from CAAML danger-level keywords to display metadata.
# Colours taken from EAWS website - https://www.avalanches.org/downloads/#avalanche-danger-scale
_DANGER_MAP: dict[str, dict[str, Any]] = {
    "low": {
        "number": "1",
        "label": _("Low"),
        "verdict": _("GO"),
        "colour": "#ccff66",
    },
    "moderate": {
        "number": "2",
        "label": _("Moderate"),
        "verdict": _("GO"),
        "colour": "#ffff00",
    },
    "considerable": {
        "number": "3",
        "label": _("Considerable"),
        "verdict": _("CAUTION"),
        "colour": "#ff9900",
    },
    "high": {
        "number": "4",
        "label": _("High"),
        "verdict": _("AVOID"),
        "colour": "#ff0000",
    },
    "very_high": {
        "number": "5",
        "label": _("Very High"),
        "verdict": _("AVOID"),
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


def _render_bulletin_page(
    request: HttpRequest,
    context: dict[str, Any],
    bulletin: Bulletin | None,
) -> HttpResponse:
    """
    Render ``public/bulletin.html`` with debugging aids attached to the response.

    Two cross-cutting concerns live here so the three render sites
    (``examples_random``, ``bulletin_detail`` no-bulletin fallback, and
    ``bulletin_detail`` happy path) stay consistent:

    * When ``bulletin`` is not None an ``X-Bulletin-Id`` response header
      carries the bulletin UUID so operators can identify exactly which
      row rendered this page from network tools.
    * When ``settings.DEBUG`` is True and a bulletin is present, its raw
      CAAML ``raw_data`` payload is embedded as a ``<script
      type="application/json">`` tag so it is visible in the page source
      but invisible to the reader.  Never emitted in production.

    Args:
        request: The incoming HTTP request.
        context: The template context (this helper adds ``raw_data_json``
            when appropriate but leaves the rest untouched).
        bulletin: The bulletin being rendered, or ``None`` for empty-state pages.

    Returns:
        The rendered ``HttpResponse`` with the debug header (and, when
        DEBUG=True, the raw-data script tag) attached.

    """
    if bulletin is not None and settings.DEBUG:
        # Escape ``</`` so a stray ``</script>`` substring in the CAAML
        # payload cannot terminate the embedding ``<script>`` tag.  JSON
        # decodes ``\/`` to ``/`` so round-tripping with JSON.parse is
        # unaffected.
        raw = json.dumps(bulletin.raw_data, ensure_ascii=False).replace("</", "<\\/")
        context = {**context, "raw_data_json": raw}
    response = render(request, "public/bulletin.html", context)
    if bulletin is not None:
        response["X-Bulletin-Id"] = str(bulletin.bulletin_id)
    return response


def _issues_for_date(
    region: Region,
    target_date: datetime.date,
) -> list[Bulletin]:
    """
    Return all bulletins whose validity window overlaps a calendar day.

    Up to three SLF issues can touch a single day:

    * the previous-day evening issue (valid ``D-1 17:00 → D 17:00``),
    * the same-day morning update  (valid ``D 08:00  → D 17:00``),
    * the same-day evening issue    (valid ``D 17:00 → D+1 17:00``).

    The query captures all three by asking for windows that *intersect*
    day D: ``valid_from.date() <= D AND valid_to.date() >= D``.

    The result is sorted by ``valid_from`` ascending so that rendering
    the list chronologically matches the mental model of earlier → later
    issue times on the day.

    Args:
        region: The Region to look up.
        target_date: Calendar date identifying the day to display.

    Returns:
        A chronologically-sorted list of Bulletins (possibly empty).

    """
    return list(
        Bulletin.objects.filter(
            regions=region,
            valid_from__date__lte=target_date,
            valid_to__date__gte=target_date,
        ).order_by("valid_from")
    )


def _select_default_issue(
    issues: list[Bulletin],
    target_date: datetime.date,
) -> Bulletin | None:
    """
    Pick the default bulletin from a day's issues.

    * For **today**, prefer the issue whose window contains *now* — the
      bulletin being live-published to the public right this moment.
    * For any other day (past or future), prefer the issue whose window
      contains **10:00 UTC** on that calendar day.  10:00 sits after the
      08:00 morning update but before the 17:00 evening rollover, so it
      picks the morning update when it exists and falls back to the
      previous day's evening issue (which is also valid at 10:00) when
      it doesn't — matching SLF's "what did the current day-time
      forecast say?" convention.

    Falls back to the last issue in the list (the latest by
    ``valid_from``) when nothing spans the pivot moment.  Returns
    ``None`` when ``issues`` is empty.

    Args:
        issues: Day's issues, chronologically sorted.
        target_date: Calendar date identifying the day being displayed.

    Returns:
        The default Bulletin to render, or ``None`` when no issues exist.

    """
    if not issues:
        return None

    now = timezone.now()
    today = now.date()
    if target_date == today:
        pivot = now
    else:
        pivot = datetime.datetime.combine(
            target_date, datetime.time(10, 0), tzinfo=datetime.UTC
        )

    # Iterate newest-first so that when both the previous-day evening
    # issue AND the current-day morning update span the pivot, the
    # morning update wins — its later ``valid_from`` marks it as the
    # authoritative refresh of the earlier forecast.
    for b in reversed(issues):
        if b.valid_from <= pivot <= b.valid_to:
            return b

    # No issue spans the pivot — fall back to the most recently-issued one.
    return issues[-1]


def _select_bulletin_for_date(
    region: Region,
    target_date: datetime.date,
) -> Bulletin | None:
    """
    Return the default bulletin to display for a region on a given date.

    Thin wrapper over :func:`_issues_for_date` +
    :func:`_select_default_issue`.  Exposed as a named helper because
    other views (``examples_random``, ``season_bulletins``) depend on
    picking a single default without knowing about the full issue list.

    Args:
        region: The Region to look up.
        target_date: Calendar date identifying the day to display.

    Returns:
        The default Bulletin for the day, or ``None`` if no bulletins exist.

    """
    return _select_default_issue(_issues_for_date(region, target_date), target_date)


def _authority_windows(
    issues: list[Bulletin],
    day_start: datetime.datetime,
    day_end: datetime.datetime,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """
    Partition a chronologically-sorted list of issues into day-D windows.

    Each issue's authority runs from its own ``valid_from`` until the
    next issue's ``valid_from`` takes over (the last issue runs to its
    own ``valid_to``), clipped to the day-D boundary.

    Args:
        issues: Chronologically-sorted bulletins touching day D.
        day_start: Start-of-day (00:00) instant for day D (tz-aware).
        day_end: End-of-day (next 00:00) instant for day D (tz-aware).

    Returns:
        A list of ``(start, end)`` tuples in the same order as ``issues``.

    """
    windows: list[tuple[datetime.datetime, datetime.datetime]] = []
    for i, b in enumerate(issues):
        start = max(b.valid_from, day_start)
        if i + 1 < len(issues):
            end = min(issues[i + 1].valid_from, day_end)
        else:
            end = min(b.valid_to, day_end)
        windows.append((start, end))
    return windows


def _format_time_on_day(dt: datetime.datetime, day_end: datetime.datetime) -> str:
    """Render a datetime as HH:MM, or "24:00" when it sits exactly at day end."""
    if dt == day_end:
        return "24:00"
    return dt.strftime("%H:%M")


def _classify_issue_role(b: Bulletin, target_date: datetime.date) -> str:
    """Classify an issue by its ``valid_from`` relative to day D."""
    vf: datetime.datetime = b.valid_from
    if vf.date() < target_date:
        return _gettext("Previous evening")
    if vf.hour < 12:
        return _gettext("Morning")
    return _gettext("Evening")


def _build_issue_tabs(
    issues: list[Bulletin],
    selected: Bulletin,
    target_date: datetime.date,
) -> list[dict[str, Any]]:
    """
    Build the per-issue tab entries displayed above the bulletin body.

    The tabs partition day D into non-overlapping *authority* windows
    (see :func:`_authority_windows`).  Label shape depends on whether
    the tab is the active one:

    * **Active** tab → full clipped window ``"HH:MM - HH:MM"``.  This
      is the canonical "what am I reading" indicator on the page.
    * **Inactive** tabs carry a directional stub pointing towards the
      active selection:

      * position before the active tab → ``"< HH:MM"`` — the
        authority-end time on day D (when the following issue takes
        over);
      * position after the active tab → ``"> HH:MM"`` — the
        authority-start time on day D (when it begins to supersede
        the previous issue).

    The long / aria label always carries role + concrete issuance
    time + authority window for screen readers.

    Args:
        issues: All bulletins overlapping ``target_date``, chronological.
        selected: The issue currently being rendered.
        target_date: Calendar date identifying the day on display.

    Returns:
        A list of dicts with ``bulletin_id``, ``short_label``,
        ``long_label``, ``role``, and ``is_active`` keys.

    """
    if not issues:
        return []

    day_start = datetime.datetime.combine(
        target_date, datetime.time(0, 0), tzinfo=datetime.UTC
    )
    day_end = day_start + datetime.timedelta(days=1)
    windows = _authority_windows(issues, day_start, day_end)

    try:
        active_index = next(i for i, b in enumerate(issues) if b.pk == selected.pk)
    except StopIteration:
        active_index = 0

    tabs: list[dict[str, Any]] = []
    for i, b in enumerate(issues):
        start, end = windows[i]
        start_txt = _format_time_on_day(start, day_end)
        end_txt = _format_time_on_day(end, day_end)
        window_text = f"{start_txt} - {end_txt}"
        if i == active_index:
            short_label = window_text
        elif i < active_index:
            short_label = f"< {end_txt}"
        else:
            short_label = f"> {start_txt}"
        role = _classify_issue_role(b, target_date)
        long_label = (
            f"{role}: issued {b.valid_from.strftime('%-d %B %H:%M')} UTC "
            f"(authoritative {window_text})"
        )
        tabs.append(
            {
                "bulletin_id": str(b.bulletin_id),
                "short_label": short_label,
                "long_label": long_label,
                "role": role,
                "is_active": i == active_index,
            }
        )
    return tabs


def _resolve_selected_issue(
    issues: list[Bulletin],
    target_date: datetime.date,
    requested_id: str | None,
) -> Bulletin | None:
    """
    Resolve which issue should render given a user-requested override.

    When ``?issue=<uuid>`` names one of the day's issues, return that
    bulletin.  Otherwise fall back to :func:`_select_default_issue`.
    Silently ignores unknown / malformed IDs so stale bookmarks degrade
    to the default view rather than 404ing.

    Args:
        issues: All bulletins overlapping ``target_date``.
        target_date: Calendar date identifying the day being displayed.
        requested_id: The ``?issue`` query param value, or ``None``.

    Returns:
        The issue to render, or ``None`` when ``issues`` is empty.

    """
    if requested_id:
        for b in issues:
            if str(b.bulletin_id) == requested_id:
                return b
    return _select_default_issue(issues, target_date)


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
    Render the marketing homepage.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered homepage.

    """
    return render(request, "public/home.html")


def map_view(request: HttpRequest) -> HttpResponse:
    """
    Render the interactive region-choropleth map page.

    The page is a MapLibre GL JS client that fetches three JSON
    endpoints (``/api/regions.geojson``, ``/api/today-summaries/``,
    ``/api/resorts-by-region/``) and colours each region by today's
    danger rating. The map template is a standalone page today but the
    DOM is structured so it can be embedded inside the marketing
    homepage later.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered map page.

    """
    return render(request, "public/map.html")


def random_redirect(request: HttpRequest) -> HttpResponse:
    """
    Redirect ``/random/`` to ``/examples/random/`` (deprecated).

    .. deprecated::
        Use ``/examples/random/`` instead. This URL will be removed in a
        future release.

    Args:
        request: The incoming HTTP request.

    Returns:
        A permanent redirect to ``/examples/random/``.

    """
    logger.warning("Deprecated URL /random/ accessed — use /examples/random/ instead")
    return redirect("public:examples_random", permanent=True)


# Map URL-safe danger level slugs to CAAML ``mainValue`` strings.
_DANGER_SLUG_TO_KEY: dict[str, str] = {
    "low": "low",
    "moderate": "moderate",
    "considerable": "considerable",
    "high": "high",
    "very-high": "very_high",
}


@never_cache
def examples_random(request: HttpRequest) -> HttpResponse:
    """
    Render a random region's bulletin inline (no redirect).

    Decorated with ``@never_cache`` because the region is picked at
    random per request — caching would freeze the "random" choice for
    every client behind a shared cache / CDN.

    Finds the most recent bulletin issue date, picks a random region from
    that issue, and renders the bulletin template directly at the current
    URL. Refreshing the page picks a different region each time.

    Falls back to the marketing homepage if there are no bulletins.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered bulletin page, or a redirect to the homepage.

    """
    latest = Bulletin.objects.order_by("-issued_at").first()
    if not latest:
        return redirect("public:home")

    region_ids = (
        RegionBulletin.objects.filter(bulletin__issued_at__date=latest.issued_at.date())
        .values_list("region_id", flat=True)
        .distinct()
    )

    regions = Region.objects.filter(pk__in=region_ids)
    if not regions.exists():
        return redirect("public:home")

    region = random.choice(list(regions))  # noqa: S311 — not crypto
    today = timezone.now().date()
    issues = _issues_for_date(region, today)
    requested_issue_id = request.GET.get("issue") or None
    selected = _resolve_selected_issue(issues, today, requested_issue_id)

    if selected is None:
        return _render_bulletin_page(
            request,
            {
                "bulletin": None,
                "region_name": region.name,
                "region_id": region.region_id,
                "year": today.year,
            },
            bulletin=None,
        )

    page_date = today
    issue_tabs = _build_issue_tabs(issues, selected, today)

    link = (
        RegionBulletin.objects.filter(bulletin=selected, region=region)
        .values_list("region_name_at_time", flat=True)
        .first()
    )
    region_name = link or region.name

    prev_date, next_date = _get_nav_dates(region, page_date)

    sibling_links = (
        RegionBulletin.objects.filter(bulletin=selected)
        .exclude(region=region)
        .select_related("region")
    )
    related_regions = [
        {
            "name": sib.region_name_at_time or sib.region.name,
            "region_id": sib.region.region_id,
            "slug": sib.region.slug,
        }
        for sib in sibling_links
    ]

    is_today = page_date == today
    next_update_time: datetime.datetime | None = None
    now = timezone.now()
    if (
        is_today
        and next_date is None
        and selected.next_update
        and selected.next_update > now
    ):
        next_update_time = selected.next_update

    panel = _build_panel_context(selected)

    context = {
        "region": region,
        "region_name": region_name,
        "region_id": region.region_id,
        "slug": slugify(region.name),
        "bulletin": selected,
        "panel": panel,
        "page_date": page_date,
        "is_today": is_today,
        "prev_date": prev_date,
        "next_date": next_date,
        "next_update_time": next_update_time,
        "related_regions": related_regions,
        "year": today.year,
        "issue_tabs": issue_tabs,
    }
    return _render_bulletin_page(request, context, bulletin=selected)


def examples_category(request: HttpRequest, danger_level: str) -> HttpResponse:
    """
    Redirect to a random bulletin matching a specific danger level.

    Finds the most recent bulletin whose highest ``mainValue`` matches the
    requested level, picks a random region from that bulletin, and redirects
    to the bulletin detail page. Returns 404 if the slug is unrecognised or
    no matching bulletin exists.

    Args:
        request: The incoming HTTP request.
        danger_level: URL slug for the danger level (e.g. ``"considerable"``
            or ``"very-high"``).

    Returns:
        A redirect response, or 404 if no matching bulletin is found.

    """
    danger_key = _DANGER_SLUG_TO_KEY.get(danger_level)
    if danger_key is None:
        raise Http404(f"Unknown danger level: {danger_level}")

    # Filter in Python because SQLite does not support JSON __contains.
    # The candidate pool is small (most recent 200 bulletins) so this is
    # fast enough for a redirect view.
    candidates = Bulletin.objects.order_by("-issued_at")[:200]
    matching = [
        b
        for b in candidates
        if any(
            r.get("mainValue") == danger_key
            for r in (b.raw_data or {}).get("properties", {}).get("dangerRatings", [])
        )
    ]

    if not matching:
        raise Http404(f"No bulletins found for danger level: {danger_level}")

    bulletin = random.choice(matching)  # noqa: S311 — not crypto
    region_bulletin = (
        RegionBulletin.objects.filter(bulletin=bulletin)
        .select_related("region")
        .first()
    )
    if not region_bulletin:
        raise Http404(f"No regions found for bulletin: {bulletin.bulletin_id}")

    region = region_bulletin.region
    name_slug = _get_name_slug(region)
    date_str = bulletin.valid_to.strftime("%Y-%m-%d")
    return redirect(
        "public:bulletin_date",
        region_id=region.region_id,
        slug=name_slug,
        date_str=date_str,
    )


def region_redirect(request: HttpRequest, region_id: str) -> HttpResponse:
    """
    Redirect ``/<region_id>/`` to ``/<region_id>/<slug>/``.

    Looks up the region name slug from cache first; only hits the
    database on a cache miss.

    Args:
        request: The incoming HTTP request.
        region_id: SLF region identifier (e.g. ``"CH-4115"``).

    Returns:
        A 302 redirect to the canonical ``/<region_id>/<slug>/`` URL.

    """
    region = get_object_or_404(Region, region_id__iexact=region_id)
    name_slug = _get_name_slug(region)
    return redirect(
        "public:bulletin",
        region_id=region.region_id,
        slug=name_slug,
    )


# ---------------------------------------------------------------------------
# Bulletin-detail HTTP caching helpers
# ---------------------------------------------------------------------------
#
# ``bulletin_detail`` is wrapped in ``@condition`` so browsers and CDNs can
# do conditional GETs and serve ``304 Not Modified`` when the underlying
# bulletin data hasn't changed. The two callables below drive the ETag
# and Last-Modified headers; both intentionally do cheap single-aggregate
# queries so the short-circuit is meaningfully cheaper than running the
# full view.
#
# Cache-Control is set inside the view via ``patch_cache_control`` because
# it branches on whether the page date is in the past (immutable, max-age
# 1y) or today (short, max-age aligned to the bulletin's next_update).


def _parse_target_date(date_str: str | None) -> datetime.date:
    """
    Parse a ``YYYY-MM-DD`` URL segment into a date.

    Falls back to today on missing or invalid input so the helper never
    raises — the downstream view handles any date-specific mismatch.
    """
    today = timezone.now().date()
    if not date_str:
        return today
    try:
        return datetime.date.fromisoformat(date_str)
    except ValueError:
        return today


def _bulletin_page_last_modified(
    request: HttpRequest,
    region_id: str,
    slug: str,
    date_str: str | None = None,
) -> datetime.datetime | None:
    """
    Return the latest ``updated_at`` across bulletins covering this page.

    Keyed by (region, target_date). Drives the ``Last-Modified`` header
    and the ``@condition`` short-circuit. Returns ``None`` when no
    bulletins exist so the view still runs for the empty-state render.
    """
    target_date = _parse_target_date(date_str)
    result = Bulletin.objects.filter(
        regions__region_id__iexact=region_id,
        valid_from__date__lte=target_date,
        valid_to__date__gte=target_date,
    ).aggregate(latest=Max("updated_at"))
    return cast("datetime.datetime | None", result["latest"])


def _bulletin_page_etag(
    request: HttpRequest,
    region_id: str,
    slug: str,
    date_str: str | None = None,
) -> str | None:
    """
    Weak ETag from latest-update + issue tab + render-model + release version.

    - ``?issue=<uuid>`` selects which issue tab renders active, so two
      requests with the same region+date but different ``?issue`` values
      must get different ETags.
    - Baking in ``RENDER_MODEL_VERSION`` means a builder bump invalidates
      every cached response without needing to touch ``updated_at``.
    - Baking in ``settings.RELEASE_VERSION`` (Render's ``RENDER_GIT_COMMIT``
      in production, ``"dev"`` locally) means every deploy invalidates
      otherwise-immutable historic URLs so template / CSS / view-logic
      edits don't get pinned behind year-long CDN caches.
    """
    latest = _bulletin_page_last_modified(request, region_id, slug, date_str)
    if latest is None:
        return None
    issue = request.GET.get("issue") or ""
    return (
        f'W/"{int(latest.timestamp())}'
        f"-{issue}"
        f"-{RENDER_MODEL_VERSION}"
        f'-{settings.RELEASE_VERSION}"'
    )


@condition(
    etag_func=_bulletin_page_etag,
    last_modified_func=_bulletin_page_last_modified,
)
def bulletin_detail(
    request: HttpRequest,
    region_id: str,
    slug: str,
    date_str: str | None = None,
) -> HttpResponse:
    """
    Render the bulletin viewer for a given region on a specific day.

    Without ``date_str`` the view shows today's bulletin. With a date
    segment (``YYYY-MM-DD``) it shows that day's bulletin.

    For past days the morning bulletin is shown (the updated daytime
    assessment).  For the current day the bulletin whose validity window
    contains the current time is shown automatically.

    The ``slug`` segment is cosmetic (for readable URLs) and is not used
    for lookup — the region is resolved entirely from ``region_id``.

    Args:
        request: The incoming HTTP request.
        region_id: SLF region identifier (e.g. ``"CH-4115"``).
        slug: Slugified region name (e.g. ``"valais"``); cosmetic only.
        date_str: Optional date string in ``YYYY-MM-DD`` format.

    Returns:
        The rendered bulletin page.

    """
    region = get_object_or_404(Region, region_id__iexact=region_id)

    # Warm the cache for future region_redirect lookups.
    cache.set(
        _cache_key(region.slug),
        slugify(region.name),
        timeout=_ZONE_NAME_CACHE_TIMEOUT,
    )

    # Determine the target date.
    today = timezone.now().date()
    target_date = today
    if date_str:
        try:
            target_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            target_date = today

    # Collect every issue that touches the target day and pick the one
    # the user asked for via ``?issue=<uuid>``; otherwise fall back to
    # the 10:00-rule default.  Keeping the full list lets the template
    # render an issue-tab strip so readers can swap between the
    # evening / morning / evening issues without losing the URL date.
    issues = _issues_for_date(region, target_date)
    requested_issue_id = request.GET.get("issue") or None
    selected = _resolve_selected_issue(issues, target_date, requested_issue_id)

    if selected is None:
        response = _render_bulletin_page(
            request,
            {
                "bulletin": None,
                "region_name": region.name,
                "region_id": region.region_id,
                "year": datetime.date.today().year,
            },
            bulletin=None,
        )
        # Empty-state: cache briefly so a freshly-ingested bulletin surfaces
        # within a minute without re-running the view on every pageview.
        patch_cache_control(response, public=True, max_age=60)
        return response

    # The page represents the calendar day chosen in the URL, independent
    # of which issue the viewer has selected — otherwise flipping to the
    # same-day-evening issue would silently bump the header to D+1.
    page_date = target_date
    issue_tabs = _build_issue_tabs(issues, selected, target_date)

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
            "name": sib.region_name_at_time or sib.region.name,
            "region_id": sib.region.region_id,
            "slug": sib.region.slug,
        }
        for sib in sibling_links
    ]

    is_today = page_date == today
    next_update_time: datetime.datetime | None = None
    now = timezone.now()
    if (
        is_today
        and next_date is None
        and selected.next_update
        and selected.next_update > now
    ):
        next_update_time = selected.next_update

    panel = _build_panel_context(selected)

    calendar_partial_url = "{}?{}".format(
        reverse(
            "public:calendar_partial",
            kwargs={
                "region_id": region.region_id,
                "year": page_date.year,
                "month": page_date.month,
            },
        ),
        urlencode({"date": page_date.isoformat()}),
    )

    context = {
        "region": region,
        "region_name": region_name,
        "region_id": region.region_id,
        "slug": slugify(region.name),
        "bulletin": selected,
        "panel": panel,
        "page_date": page_date,
        "is_today": is_today,
        "prev_date": prev_date,
        "next_date": next_date,
        "next_update_time": next_update_time,
        "related_regions": related_regions,
        "year": today.year,
        "issue_tabs": issue_tabs,
        # Calendar widget context.
        "calendar_region_id": region.region_id,
        "calendar_partial_url": calendar_partial_url,
        "calendar_current_date": page_date,
    }
    response = _render_bulletin_page(request, context, bulletin=selected)

    # Cache-Control — branch on whether the page date is in the past.
    if page_date < today:
        # Historic bulletins are truly immutable by (bulletin_id, render
        # model version). Cache aggressively at both the browser and any
        # upstream CDN.
        patch_cache_control(response, public=True, max_age=31536000, immutable=True)
    else:
        # Today: short cache, aligned to the bulletin's next_update when
        # present. Clamped to [30s, 300s] so we never go stale for more
        # than 5 minutes regardless of what next_update claims.
        max_age = 60
        if next_update_time:
            remaining = int((next_update_time - timezone.now()).total_seconds())
            max_age = max(30, min(remaining, 300))
        patch_cache_control(response, public=True, max_age=max_age)
    return response


# ---------------------------------------------------------------------------
# Random bulletins list
# ---------------------------------------------------------------------------

# Per-level display metadata used by the compact panel card. Keys match the
# CAAML ``mainValue`` strings; ``icon`` is the filename inside
# ``static/icons/eaws/danger_levels/``.
_DANGER_PANEL_META: dict[str, dict[str, Any]] = {
    "low": {
        "number": "1",
        "label": _("Low"),
        "sub": _("Stable snowpack"),
        "icon": "Dry-Snow-1.svg",
    },
    "moderate": {
        "number": "2",
        "label": _("Moderate"),
        "sub": _("Cautious route selection needed"),
        "icon": "Dry-Snow-2.svg",
    },
    "considerable": {
        "number": "3",
        "label": _("Considerable"),
        "sub": _("Dangerous off-piste conditions"),
        "icon": "Dry-Snow-3.svg",
    },
    "high": {
        "number": "4",
        "label": _("High"),
        "sub": _("Very critical off-piste conditions"),
        "icon": "Dry-Snow-4-5.svg",
    },
    "very_high": {
        "number": "5",
        "label": _("Very high"),
        "sub": _("Do not enter avalanche terrain"),
        "icon": "Dry-Snow-4-5.svg",
    },
    # Defensive fallback for malformed bulletins where an AM/PM half has
    # no covering dangerRating.  In practice every bulletin carries an
    # ``all_day`` rating so both halves always match; this entry keeps
    # ``_DANGER_PANEL_META[key]`` lookups safe when they don't.
    "no_rating": {
        "number": "—",
        "label": _("No rating"),
        "sub": _("No rating available"),
        "icon": "No-Rating.svg",
    },
}

# Human labels for the CAAML ``problemType`` enum used on the panel tags.
_PROBLEM_LABELS: dict[str, Any] = {
    "new_snow": _("New snow"),
    "wind_slab": _("Wind slab"),
    "persistent_weak_layers": _("Persistent weak layers"),
    "wet_snow": _("Wet snow"),
    "gliding_snow": _("Gliding snow"),
    "cornices": _("Cornices"),
    "no_distinct_avalanche_problem": _("No distinct problem"),
    "favourable_situation": _("Favourable situation"),
}

# Human labels for the CAAML ``validTimePeriod`` enum. Derived from the
# ``ValidTimePeriod`` TextChoices so the display strings stay in sync with
# the canonical schema definition.
_TIME_PERIOD_LABELS: dict[str, str | Promise] = dict(ValidTimePeriod.choices)

_DANGER_ORDER: tuple[str, ...] = (
    "low",
    "moderate",
    "considerable",
    "high",
    "very_high",
)

# Map CAAML ``customData.CH.subdivision`` strings to display suffixes.
_SUBDIVISION_SUFFIX: dict[str, str] = {
    "minus": "-",
    "neutral": "=",
    "plus": "+",
}

# Default number of bulletins to display on the random_bulletins page when
# no ``?b=N`` query parameter is supplied.
_DEFAULT_BULLETIN_COUNT = 10

# Safety cap on the ``?b=N`` query parameter to prevent a crafted request
# from selecting an unbounded number of bulletins.
_MAX_BULLETIN_COUNT = 50


def _highest_danger_key(ratings: list[dict[str, Any]]) -> tuple[str, str]:
    """
    Return the highest CAAML ``mainValue`` and its subdivision suffix.

    When multiple ratings share the same highest ``mainValue``, the
    subdivision from the last one encountered is used.

    Args:
        ratings: The CAAML ``dangerRatings`` list.

    Returns:
        A ``(key, subdivision_suffix)`` tuple. *key* is one of the keys in
        :data:`_DANGER_PANEL_META` (defaults to ``"low"``).
        *subdivision_suffix* is ``"-"``, ``"="``, ``"+"``, or ``""`` if
        no subdivision is present.

    """
    highest = "low"
    subdivision = ""
    for rating in ratings:
        value = rating.get("mainValue", "")
        if value in _DANGER_ORDER and _DANGER_ORDER.index(value) >= _DANGER_ORDER.index(
            highest
        ):
            highest = value
            raw = (rating.get("customData") or {}).get("CH", {}).get("subdivision", "")
            subdivision = _SUBDIVISION_SUFFIX.get(raw, "")
    return highest, subdivision


# Mirrors WhiteRisk's split: a dangerRating whose validTimePeriod is
# ``all_day`` applies in both halves; ``earlier`` (morning-only) and
# ``later`` (afternoon-only) are scoped to one half each.  Used by
# :func:`_resolve_period_danger` to pick the ratings that cover a given
# half of the day.
_MORNING_PERIODS: frozenset[str] = frozenset({"all_day", "earlier"})
_AFTERNOON_PERIODS: frozenset[str] = frozenset({"all_day", "later"})


def _resolve_period_danger(
    ratings: list[dict[str, Any]],
    traits: list[dict[str, Any]],
    period_group: frozenset[str],
) -> tuple[str, str]:
    """
    Return the highest danger key + subdivision covering a half of the day.

    Primary source is the CAAML ``dangerRatings`` list — filtered to entries
    whose ``validTimePeriod`` is in ``period_group`` (defaulting absent values
    to ``"all_day"`` since an unscoped rating applies all day), then reduced
    with :func:`_highest_danger_key` to pick the highest ``mainValue`` and
    its subdivision suffix.

    When ``dangerRatings`` carries nothing for the period, falls back to the
    render-model ``traits`` and returns the highest ``danger_level`` among
    traits whose ``time_period`` covers the half.  Subdivision is ``""`` in
    the fallback path — traits don't carry it.  This branch exists so test
    fixtures that populate only ``render_model`` (not ``raw_data``) still
    render the headline band correctly; real SLF bulletins always populate
    ``dangerRatings`` and hit the primary path.

    Returns ``("no_rating", "")`` only when *both* sources are empty for
    this half.

    Args:
        ratings: The CAAML ``dangerRatings`` list from ``raw_data``.
        traits: The render-model ``traits`` list (used as the fallback).
        period_group: Set of ``validTimePeriod`` tokens covering the target
            half of the day (``_MORNING_PERIODS`` or ``_AFTERNOON_PERIODS``).

    Returns:
        A ``(key, subdivision_suffix)`` tuple, same shape as
        :func:`_highest_danger_key`.

    """
    relevant_ratings = [
        r for r in ratings if r.get("validTimePeriod", "all_day") in period_group
    ]
    if relevant_ratings:
        return _highest_danger_key(relevant_ratings)

    # Fallback: derive the half's level from traits when ``dangerRatings``
    # is absent or omits a covering entry.  Tests populate ``render_model``
    # directly and leave ``raw_data`` empty — without this fallback the
    # headline band would read ``no_rating`` on every test bulletin.
    levels: list[int] = []
    for t in traits:
        if t.get("time_period") not in period_group:
            continue
        try:
            level = int(t.get("danger_level") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= level <= 5:
            levels.append(level)
    if not levels:
        return "no_rating", ""
    return _DANGER_ORDER[max(levels) - 1], ""


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


ELEVATION_LOWER = "LOWER"
ELEVATION_UPPER = "UPPER"
ELEVATION_BOTH = "BOTH"


@dataclasses.dataclass(frozen=True)
class ElevationBounds:
    """
    Structured elevation bounds for a CAAML avalanche problem.

    Provides dot-access to the raw ``lower`` / ``upper`` bound strings,
    a pre-formatted ``display`` string for template rendering, and a
    ``bound_type`` constant (``"LOWER"``, ``"UPPER"``, ``"BOTH"``, or
    ``""`` when no bounds are present) for icon selection.
    Boolean-truthy when at least one bound is present.
    """

    lower: str
    upper: str
    display: str
    bound_type: str

    def __bool__(self) -> bool:  # noqa: D105
        return bool(self.display)


def _elevation_display(lower_raw: Any, upper_raw: Any) -> str:
    """
    Render lower/upper CAAML elevation bounds as a short human string.

    Returns an empty string when neither bound produces a formatted value.
    """
    if _is_numeric_bound(lower_raw) and _is_numeric_bound(upper_raw):
        return f"{lower_raw}\u2013{upper_raw}m"

    lower_fmt = _format_bound(lower_raw)
    upper_fmt = _format_bound(upper_raw)

    if lower_fmt and upper_fmt:
        return f"{lower_fmt}\u2013{upper_fmt}"
    if lower_fmt:
        return _gettext("above %(bound)s") % {"bound": lower_fmt}
    if upper_fmt:
        return _gettext("below %(bound)s") % {"bound": upper_fmt}
    return ""


def _format_elevation(elevation: dict[str, Any] | None) -> ElevationBounds:
    """
    Build an :class:`ElevationBounds` from a CAAML elevation dict.

    Accepts both numeric metre values and the literal ``"treeline"`` (the
    schema permits either). Examples::

        {"lowerBound": "2200"}                       → "above 2200m"
        {"upperBound": "2400"}                       → "below 2400m"
        {"lowerBound": "1800", "upperBound": "2400"} → "1800–2400m"
        {"lowerBound": "treeline"}                   → "above treeline"

    When both bounds are numeric the ``m`` suffix appears only once on
    the right-hand side of the range for readability. Mixed
    numeric/treeline ranges fall back to labelling each end separately.
    Returns an empty-display :class:`ElevationBounds` when no bounds
    are present.
    """
    empty = ElevationBounds(lower="", upper="", display="", bound_type="")
    if not elevation:
        return empty

    lower_raw = elevation.get("lowerBound")
    upper_raw = elevation.get("upperBound")

    lower_str = str(lower_raw) if lower_raw is not None and lower_raw != "" else ""
    upper_str = str(upper_raw) if upper_raw is not None and upper_raw != "" else ""

    if lower_str and upper_str:
        bound_type = ELEVATION_BOTH
    elif lower_str:
        bound_type = ELEVATION_LOWER
    elif upper_str:
        bound_type = ELEVATION_UPPER
    else:
        return empty

    display = _elevation_display(lower_raw, upper_raw)
    if not display:
        return empty

    return ElevationBounds(
        lower=lower_str, upper=upper_str, display=display, bound_type=bound_type
    )


def _problem_summary(
    core_zone_text: str,
    elevation: ElevationBounds,
    aspects: list[str],
) -> str:
    """
    Build a one-line summary for an avalanche problem detail section.

    Prefers the SLF-authored ``customData.CH.coreZoneText`` when present.
    Falls back to a generated string such as
    ``"Affects N, NE aspects above 2200m"``, or
    ``"Affects all aspects and elevations"`` when neither is available.
    """
    if core_zone_text:
        return core_zone_text
    if aspects and elevation:
        aspect_str = ", ".join(aspects)
        return _gettext("Affects %(aspect_str)s aspects %(elevation)s") % {
            "aspect_str": aspect_str,
            "elevation": elevation.display,
        }
    return _gettext("Affects all aspects and elevations")


def _enrich_render_model_problem(
    rm_problem: dict[str, Any],
    guidance: dict[str, Any],
    trait_problems: list[dict[str, Any]],
    problem_index: int,
) -> dict[str, Any]:
    """
    Enrich a render model problem dict with presentation-ready fields.

    Converts the sparse render model representation (which uses int elevation
    bounds) into the richer shape the panel template expects, adding
    ``label``, ``time_period_label``, :class:`ElevationBounds`, ``summary``,
    ``field_guidance``, and ``hide_comment``.

    Args:
        rm_problem: A problem dict from the render model.
        guidance: Field guidance dict from :func:`public.guidance.load_field_guidance`.
        trait_problems: All problems in the same trait (for duplicate detection).
        problem_index: Index of this problem in ``trait_problems``.

    Returns:
        The original dict extended with presentation keys.

    """
    problem_type: str = rm_problem.get("problem_type", "")
    label = _PROBLEM_LABELS.get(
        problem_type, problem_type.replace("_", " ").capitalize()
    )
    time_period: str = rm_problem.get("time_period", "") or ""
    time_period_label = _TIME_PERIOD_LABELS.get(time_period, "")

    # Convert render model elevation (int|None lower/upper) to ElevationBounds.
    # The render model stores treeline as a bool flag; convert back to the
    # CAAML string token so _format_elevation can build the display string.
    rm_elevation: dict[str, Any] | None = rm_problem.get("elevation")
    if rm_elevation:
        lower_raw = rm_elevation.get("lower")
        upper_raw = rm_elevation.get("upper")
        is_treeline = rm_elevation.get("treeline", False)
        # When treeline flag is set and no numeric lower bound, use the token.
        caaml_lower: Any = lower_raw
        if is_treeline and lower_raw is None:
            caaml_lower = "treeline"
        elevation_bounds = _format_elevation(
            {"lowerBound": caaml_lower, "upperBound": upper_raw}
        )
    else:
        elevation_bounds = _format_elevation(None)

    aspects: list[str] = rm_problem.get("aspects") or []
    core_zone_text: str = rm_problem.get("core_zone_text") or ""
    summary = _problem_summary(core_zone_text, elevation_bounds, aspects)
    field_guidance = guidance.get(problem_type)

    # Duplicate comment detection within this trait.
    comment_html = rm_problem.get("comment_html") or ""
    hide_comment = False
    if comment_html and len(trait_problems) > 1:
        plain = _plain_text(comment_html)
        later_plains = [
            _plain_text(p.get("comment_html") or "")
            for p in trait_problems[problem_index + 1 :]
        ]
        if plain in later_plains:
            hide_comment = True

    # Map the problem's own danger rating to its CSS data-level value.
    # Uses the same key set as the danger-band data-level attribute so the
    # same CSS token rules apply. Falls back to empty string (neutral/grey).
    danger_rating_value: str = rm_problem.get("danger_rating_value") or ""
    danger_level_css = (
        danger_rating_value if danger_rating_value in _DANGER_ORDER else ""
    )

    return {
        **rm_problem,
        "label": label,
        "time_period_label": time_period_label,
        "elevation": elevation_bounds,
        "summary": summary,
        "field_guidance": field_guidance,
        "hide_comment": hide_comment,
        "danger_level_css": danger_level_css,
    }


def _enrich_render_model(
    render_model: dict[str, Any],
) -> dict[str, Any]:
    """
    Add presentation-ready fields to the render model's traits and problems.

    Converts raw render model problem dicts (int elevation bounds) into the
    richer shape the panel template expects, adding labels, ElevationBounds,
    field_guidance, and hide_comment.

    Args:
        render_model: A render model dict as produced by
            :func:`pipeline.services.render_model.build_render_model`.

    Returns:
        A new render model dict with enriched trait problems.

    """
    guidance = load_field_guidance()
    enriched_traits: list[dict[str, Any]] = []

    for trait in render_model.get("traits") or []:
        raw_problems: list[dict[str, Any]] = trait.get("problems") or []
        enriched_problems = [
            _enrich_render_model_problem(p, guidance, raw_problems, i)
            for i, p in enumerate(raw_problems)
        ]
        enriched_traits.append({**trait, "problems": enriched_problems})

    return {**render_model, "traits": enriched_traits}


def _get_render_model(
    bulletin: Bulletin,
    props: dict[str, Any],
) -> dict[str, Any]:
    """
    Return the render model for a bulletin, rebuilding on the fly if stale.

    When ``bulletin.render_model_version`` is older than
    ``RENDER_MODEL_VERSION`` the render model is rebuilt from ``props``.
    On ``RenderModelBuildError`` an error sentinel dict is returned so the
    view can render an error card without crashing. The stored DB row is
    never modified here.

    Args:
        bulletin: The Bulletin whose render model is needed.
        props: The CAAML properties dict (from ``bulletin.raw_data``).

    Returns:
        A render model dict (may have version=0 on build failure).

    """
    if bulletin.render_model_version >= RENDER_MODEL_VERSION:
        return cast("dict[str, Any]", bulletin.render_model)

    logger.warning(
        "Bulletin %s has stale render_model (stored version=%d, current=%d);"
        " building on the fly",
        bulletin.bulletin_id,
        bulletin.render_model_version,
        RENDER_MODEL_VERSION,
    )
    try:
        return build_render_model(props)
    except RenderModelBuildError as exc:
        logger.error(
            "Bulletin %s render model rebuild failed during view render: %s",
            bulletin.bulletin_id,
            exc,
            exc_info=True,
        )
        # Return error sentinel for this render only — do NOT write to DB.
        return {
            "version": 0,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }


def _build_panel_context(bulletin: Bulletin) -> dict[str, Any]:
    """
    Build the template context for a single compact bulletin panel.

    Reads ``bulletin.render_model`` directly. If ``render_model_version`` is
    older than ``RENDER_MODEL_VERSION`` the render model is rebuilt on the fly
    and a warning is logged so operators know which rows need the
    ``rebuild_render_models`` command run against them.

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
    ratings: list[dict[str, Any]] = props.get("dangerRatings") or []
    danger_key, danger_subdivision = _highest_danger_key(ratings)
    danger_meta = _DANGER_PANEL_META[danger_key]

    # Fallback key-message: used by the template when the bulletin has no
    # avalanche problems. Try avalancheProblems[0].comment first, then
    # snowpackStructure.comment, then weatherReview.comment.
    key_message = ""
    key_message_source = ""
    ap = props.get("avalancheProblems") or []
    if ap:
        key_message = ap[0].get("comment") or ""
        if key_message:
            key_message_source = "avalancheProblems[0].comment"
    if not key_message:
        key_message = (props.get("snowpackStructure") or {}).get("comment") or ""
        if key_message:
            key_message_source = "snowpackStructure.comment"
    if not key_message:
        key_message = (props.get("weatherReview") or {}).get("comment") or ""
        if key_message:
            key_message_source = "weatherReview.comment"

    snowpack_structure = (props.get("snowpackStructure") or {}).get("comment") or ""

    # Retrieve or build the render model. Bulletins ingested before this
    # feature was deployed will have render_model_version == 0; build on
    # the fly so the page renders correctly while a backfill job catches up.
    raw_render_model = _get_render_model(bulletin, props)

    # Enrich the render model with presentation-ready fields (labels,
    # ElevationBounds, field_guidance, hide_comment per trait).
    render_model = _enrich_render_model(raw_render_model)

    traits: list[dict[str, Any]] = render_model.get("traits") or []

    # Per-half danger resolution for the AM/PM split headline.  Mirrors
    # WhiteRisk's "Morning" + "As the day progresses" maps: the half's
    # level is the highest of any rating that covers it (``all_day`` is
    # always counted, plus ``earlier`` for morning or ``later`` for
    # afternoon).  Primary source is ``dangerRatings``; traits are the
    # fallback when the raw data omits per-period entries.
    morning_key, morning_subdivision = _resolve_period_danger(
        ratings, traits, _MORNING_PERIODS
    )
    afternoon_key, afternoon_subdivision = _resolve_period_danger(
        ratings, traits, _AFTERNOON_PERIODS
    )
    morning_meta = _DANGER_PANEL_META[morning_key]
    afternoon_meta = _DANGER_PANEL_META[afternoon_key]

    # Conditions-change caption trigger.  Fires when any trait is scoped
    # to morning (``earlier``) or afternoon (``later``), even if the
    # AM/PM danger levels happen to match — the problem *mix* still
    # evolves (e.g. dry all day + wet afternoon at the same level) and
    # the caption surfaces that signal beside the headline band, which
    # only carries the level tints.
    is_time_variable = any(t.get("time_period") in {"earlier", "later"} for t in traits)

    panel: dict[str, Any] = {
        "bulletin": bulletin,
        "danger_key": danger_key,
        # Hyphenated form for CSS class names (``very_high`` → ``very-high``)
        # so the template can emit ``band-very-high`` / ``level-very-high``
        # matching the stylesheet.
        "danger_css": danger_key.replace("_", "-"),
        "danger_number": danger_meta["number"],
        "danger_subdivision": danger_subdivision,
        "danger_label": danger_meta["label"],
        "danger_sub": danger_meta["sub"],
        "danger_icon": danger_meta["icon"],
        "danger_source": "dangerRatings[*].mainValue (highest)",
        "key_message": key_message,
        "key_message_source": key_message_source,
        "snowpack_structure": snowpack_structure,
        "footer_date_from": bulletin.valid_from,
        "footer_date_to": bulletin.valid_to,
        "footer_next_update": bulletin.next_update,
        "footer_date_source": "Bulletin.valid_from / valid_to",
        "admin_url": reverse("admin:pipeline_bulletin_change", args=[bulletin.pk]),
        "render_model": render_model,
        "is_time_variable": is_time_variable,
        # Per-half danger fields feed the AM/PM split headline band.
        "morning_key": morning_key,
        "morning_label": morning_meta["label"],
        "morning_number": morning_meta["number"],
        "morning_subdivision": morning_subdivision,
        "afternoon_key": afternoon_key,
        "afternoon_label": afternoon_meta["label"],
        "afternoon_number": afternoon_meta["number"],
        "afternoon_subdivision": afternoon_subdivision,
    }
    panel["day_character"] = compute_day_character(raw_render_model)
    return panel


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


# ---------------------------------------------------------------------------
# Calendar partial
# ---------------------------------------------------------------------------

# Named constant for the number of weeks shown in the calendar grid.
_CALENDAR_WEEKS = 6


@dataclasses.dataclass(frozen=True)
class CalendarCell:
    """
    A single cell in the month-grid calendar.

    Either an in-month cell with rating data, or a padding cell (no date).

    ``min_rating_key`` and ``max_rating_key`` carry the lowest and highest
    danger ratings for the day respectively.  When they are equal the day is
    uniform and the tile renders as a solid fill; when they differ the day is
    variable and the tile renders as a left-to-right gradient.

    ``subdivision`` is the max-bulletin's subdivision suffix.  It is only
    shown on uniform days (no room on split tiles).
    """

    date: datetime.date | None
    """Calendar date, or None for pad cells outside the current month."""
    min_rating_key: str
    """Lowest danger rating key for the day, or ``"no_rating"``."""
    max_rating_key: str
    """Highest danger rating key for the day, or ``"no_rating"``."""
    subdivision: str
    """Subdivision suffix (``"+"``, ``"-"``, ``"="``), or empty string."""
    has_bulletin: bool
    """True when there is a qualifying bulletin to link to."""
    is_selected: bool = False
    """True when this cell corresponds to the currently-viewed bulletin date."""


def _build_calendar_grid(
    ratings: dict[datetime.date, "RegionDayRating"],
    year: int,
    month: int,
    today: datetime.date,
    selected_date: datetime.date | None = None,
) -> list[list[CalendarCell]]:
    """
    Build a 6-row × 7-column calendar grid for a month.

    The week starts on Monday (ISO 8601 / British convention). Pad cells
    outside the month have ``date=None``. In-month cells carry the matching
    RegionDayRating data when available.

    Args:
        ratings: Mapping of date → RegionDayRating for the month.
        year: Calendar year.
        month: Calendar month (1–12).
        today: Today's date (used only for context; not mutated here).
        selected_date: The currently-viewed bulletin date, if any. The matching
            cell will have ``is_selected=True``.

    Returns:
        A list of 6 lists each containing 7 CalendarCell instances.

    """
    # _calendar_module.monthcalendar returns weeks as Mon-Sun rows with 0 for pad.
    raw_weeks = _calendar_module.monthcalendar(year, month)
    # Ensure exactly 6 rows by padding with empty weeks if needed.
    while len(raw_weeks) < _CALENDAR_WEEKS:
        raw_weeks.append([0] * 7)

    grid: list[list[CalendarCell]] = []
    for week in raw_weeks[:_CALENDAR_WEEKS]:
        row: list[CalendarCell] = []
        for day_num in week:
            if day_num == 0:
                row.append(
                    CalendarCell(
                        date=None,
                        min_rating_key="",
                        max_rating_key="",
                        subdivision="",
                        has_bulletin=False,
                    )
                )
            else:
                cell_date = datetime.date(year, month, day_num)
                rdr = ratings.get(cell_date)
                max_rating_key: str
                min_rating_key: str
                if rdr is None:
                    max_rating_key = RegionDayRating.Rating.NO_RATING
                    min_rating_key = RegionDayRating.Rating.NO_RATING
                    subdivision = ""
                    has_bulletin = False
                else:
                    max_rating_key = rdr.max_rating
                    min_rating_key = rdr.min_rating
                    subdivision = rdr.max_subdivision
                    has_bulletin = (
                        rdr.source_bulletin_id is not None
                        and max_rating_key != RegionDayRating.Rating.NO_RATING
                    )
                row.append(
                    CalendarCell(
                        date=cell_date,
                        min_rating_key=min_rating_key,
                        max_rating_key=max_rating_key,
                        subdivision=subdivision,
                        has_bulletin=has_bulletin,
                        is_selected=(
                            selected_date is not None and cell_date == selected_date
                        ),
                    )
                )
        grid.append(row)

    return grid


@require_htmx
def calendar_partial(
    request: HttpRequest,
    region_id: str,
    year: int,
    month: int,
) -> HttpResponse:
    """
    Return the month-grid calendar fragment for a region.

    Restricted to HTMX requests (returns 400 otherwise). The fragment
    wraps itself in ``<div id="bulletin-calendar">`` so prev/next navigation
    can swap the outer element with ``hx-target="#bulletin-calendar"
    hx-swap="outerHTML"``.

    Year/month are clamped to the season start (``settings.SEASON_START_DATE``)
    and today. Requests outside that range are silently clamped rather than
    returning 404 — out-of-bound navigations from old bookmarks degrade
    gracefully.

    Args:
        request: The incoming HTTP request (must be HTMX).
        region_id: SLF region identifier (e.g. ``"CH-4115"``).
        year: Calendar year.
        month: Calendar month as an integer 1–12.

    Returns:
        Rendered HTML fragment, or 400 for non-HTMX requests.

    """
    if not 1 <= month <= 12:
        raise Http404("Invalid month")

    region = get_object_or_404(Region, region_id__iexact=region_id)

    today = timezone.now().date()
    season_start: datetime.date = settings.SEASON_START_DATE

    # Clamp (year, month) to [season_start, today].
    requested = datetime.date(year, month, 1)
    min_month = datetime.date(season_start.year, season_start.month, 1)
    max_month = datetime.date(today.year, today.month, 1)

    if requested < min_month:
        requested = min_month
    elif requested > max_month:
        requested = max_month

    year = requested.year
    month = requested.month

    # Parse optional selected date from query string (e.g. ?date=2026-04-15).
    selected_date: datetime.date | None = None
    raw_date = request.GET.get("date")
    if raw_date:
        try:
            selected_date = datetime.datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            selected_date = None

    # Fetch ratings for the month.
    rating_qs = RegionDayRating.objects.for_region_month(region, year, month)
    ratings: dict[datetime.date, RegionDayRating] = {r.date: r for r in rating_qs}

    grid = _build_calendar_grid(
        ratings, year, month, today, selected_date=selected_date
    )

    # Prev/next URLs — None when at the boundary.
    prev_month = requested - datetime.timedelta(days=1)
    prev_month = datetime.date(prev_month.year, prev_month.month, 1)
    prev_url: str | None = None
    if prev_month >= min_month:
        prev_url = reverse(
            "public:calendar_partial",
            kwargs={
                "region_id": region.region_id,
                "year": prev_month.year,
                "month": prev_month.month,
            },
        )

    next_month_day = _calendar_module.monthrange(year, month)[1]
    next_month = datetime.date(year, month, next_month_day) + datetime.timedelta(days=1)
    next_month = datetime.date(next_month.year, next_month.month, 1)
    next_url: str | None = None
    if next_month <= max_month:
        next_url = reverse(
            "public:calendar_partial",
            kwargs={
                "region_id": region.region_id,
                "year": next_month.year,
                "month": next_month.month,
            },
        )

    context: dict[str, Any] = {
        "region": region,
        "year": year,
        "month": month,
        "month_label": datetime.date(year, month, 1).strftime("%B %Y"),
        "grid": grid,
        "today": today,
        "prev_url": prev_url,
        "next_url": next_url,
        "calendar_current_date": selected_date,
    }
    return render(request, "public/partials/calendar.html", context)
