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

import dataclasses
import datetime
import json
import logging
import random
from typing import Any, cast

import waffle
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.cache.utils import make_template_fragment_key
from django.db.models import Max, Prefetch
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_cache_control
from django.utils.functional import Promise
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.translation import gettext as _gettext, gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import condition, require_POST

from bulletins.models import Bulletin, RegionBulletin, WeatherSnapshot
from bulletins.schema import ValidTimePeriod
from bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
    compute_day_character,
)
from bulletins.services.weather_display import build_weather_display
from bulletins.services.weather_fetcher import (
    fetch_archive_for_region,
    fetch_weather_async,
    fetch_weather_for_region,
)
from core.decorators import require_htmx
from core.utils import html_to_markdown
from regions.models import MicroRegion

from .guidance import load_field_guidance
from .season_calendar import build_season_grid, season_header

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
    region: MicroRegion,
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
        region: The MicroRegion to look up.
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
    region: MicroRegion,
    target_date: datetime.date,
) -> Bulletin | None:
    """
    Return the default bulletin to display for a region on a given date.

    Thin wrapper over :func:`_issues_for_date` +
    :func:`_select_default_issue`.  Exposed as a named helper because
    other views (``examples_random``) depend on picking a single
    default without knowing about the full issue list.

    Args:
        region: The MicroRegion to look up.
        target_date: Calendar date identifying the day to display.

    Returns:
        The default Bulletin for the day, or ``None`` if no bulletins exist.

    """
    return _select_default_issue(_issues_for_date(region, target_date), target_date)


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
    region: MicroRegion,
    current_date: datetime.date,
) -> tuple[datetime.date | None, datetime.date | None]:
    """
    Find the previous and next dates with bulletins for a region.

    Dates are derived from the ``valid_to`` field so that each calendar day
    maps to exactly one page.

    Args:
        region: The MicroRegion to navigate within.
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


def _get_name_slug(region: MicroRegion) -> str:
    """
    Return a URL-safe slug derived from the region's human-readable name.

    Caches the result so subsequent requests for the same zone skip the
    database entirely.

    Args:
        region: A MicroRegion instance.

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


def terms(request: HttpRequest) -> HttpResponse:
    """
    Render the /terms page.

    Holds the SLF data-licence acknowledgement and Snowdesk's liability
    disclaimer. Introduced for SLF data-licence compliance (SNOW-30);
    the actual legal copy is authored by Hugo separately and edited
    directly into ``public/templates/public/terms.html``. This view
    is purely a static-template render — no context required.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered terms page.

    """
    return render(request, "public/terms.html")


def colophon(request: HttpRequest) -> HttpResponse:
    """
    Render the /colophon page.

    Static acknowledgement of every framework, data source, icon set,
    font, and hosted service the site depends on. Content is authored
    directly in the template; no runtime context is required.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered colophon page.

    """
    return render(request, "public/colophon.html")


def privacy(request: HttpRequest) -> HttpResponse:
    """
    Render the /privacy page.

    Privacy policy for Snowdesk, covering data collection, legal bases,
    retention periods, third-party providers, and user rights under UK GDPR.
    Content is authored directly in the template; no runtime context required.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered privacy policy page.

    """
    return render(request, "public/privacy.html")


def terms_of_service(request: HttpRequest) -> HttpResponse:
    """
    Render the /terms-of-service page.

    Full Terms of Service for Snowdesk, covering the service description,
    safety disclaimer, acceptable use, and limitation of liability.
    Distinct from /terms/, which covers the SLF data-licence acknowledgement.
    Content is authored directly in the template; no runtime context required.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered terms of service page.

    """
    return render(request, "public/terms_of_service.html")


def _build_guide_examples() -> dict[str, Any]:
    """
    Build hardcoded example context for the how-to-read-a-bulletin page.

    Returns a dict of synthetic data keyed by template variable name. Each
    value is shaped to match the partial's expected context, following the
    same pattern as ``_component_fixtures.py`` for the component library.
    No database access — all values are hand-curated illustrative examples.

    Returns:
        Dict with keys:
        - ``example_day_window_single``: one-row day_windows list (moderate,
          all-day) — danger-level section
        - ``example_day_window_sub_minus``: one-row list showing 3– —
          subdivisions section
        - ``example_day_window_split``: two-row list (considerable– all-day +
          moderate later) — how-the-day-evolves section
        - ``example_new_snow_card``: new snow at moderate danger (all aspects)
        - ``example_persistent_card``: persistent weak layers at considerable
        - ``example_dry_card``: wind slab (used in elevation/aspect section)
        - ``example_multi_card``: combined wind-slab + persistent-weak-layers
          label to illustrate multiple-problem-types
        - ``example_wet_card``: wet snow at moderate, later timing
        - ``example_gliding_card``: gliding snow at moderate, all day

    """

    def _dw(period: str, level: str, pill: str, modifier: str = "") -> dict[str, Any]:
        """Build one day-window row dict matching ``_build_day_windows`` output."""
        labels: dict[str, tuple[str, str]] = {
            "low": ("Low", "1"),
            "moderate": ("Moderate", "2"),
            "considerable": ("Considerable", "3"),
            "high": ("High", "4"),
            "very_high": ("Very high", "5"),
        }
        label, number = labels[level]
        return {
            "type": period,
            "level_key": level,
            "level_css": level.replace("_", "-"),
            "level_label": label,
            "level_number": f"{number}{modifier}",
            "caption": "",
            "pill_label": pill,
        }

    # Danger-level section: single moderate all-day window.
    single_moderate = [_dw("all_day", "moderate", "All day")]

    # Subdivisions section: considerable-minus — sits just above the 2/3 boundary.
    sub_minus = [_dw("all_day", "considerable", "All day", modifier="-")]

    # How-the-day-evolves section: split day (considerable– morning, moderate later).
    split_day = [
        _dw("all_day", "considerable", "All day", modifier="-"),
        _dw("later", "moderate", "Later"),
    ]

    # --- Dry hazard cards ---

    # New snow: moderate, widespread across all aspects above 1600m.
    new_snow_card: dict[str, Any] = {
        "category": "dry",
        "danger_level": 2,
        "danger_level_key": "moderate",
        "problem_type": "new_snow",
        "time_period": "all_day",
        "aspects": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
        "elevation": ElevationBounds(
            lower="1600",
            upper="",
            display="above 1600m",
            bound_type=ELEVATION_LOWER,
        ),
        "comment_html": "",
        "label": "New snow",
        "time_period_label": "",
        "hide_comment": False,
        "core_zone_text": "All aspects, above 1600m",
    }

    # Persistent weak layers: considerable, north-facing aspects above 2600m.
    persistent_card: dict[str, Any] = {
        "category": "dry",
        "danger_level": 3,
        "danger_level_key": "considerable",
        "problem_type": "persistent_weak_layers",
        "time_period": "all_day",
        "aspects": ["N", "NE", "NW", "E"],
        "elevation": ElevationBounds(
            lower="2600",
            upper="",
            display="above 2600m",
            bound_type=ELEVATION_LOWER,
        ),
        "comment_html": "",
        "label": "Persistent weak layers",
        "time_period_label": "",
        "hide_comment": False,
        "core_zone_text": "N to E aspects, above 2600m",
    }

    # Wind slab: considerable, north-facing slopes above 2400m.
    # Used in the elevation-and-aspect section as the lower-bound example.
    dry_card: dict[str, Any] = {
        "category": "dry",
        "danger_level": 3,
        "danger_level_key": "considerable",
        "problem_type": "wind_slab",
        "time_period": "all_day",
        "aspects": ["N", "NE", "NW"],
        "elevation": ElevationBounds(
            lower="2400",
            upper="",
            display="above 2400m",
            bound_type=ELEVATION_LOWER,
        ),
        "comment_html": "",
        "label": "Wind slab",
        "time_period_label": "",
        "hide_comment": False,
        "core_zone_text": "N to NW aspects, above 2400m",
    }

    # Multiple problem types: wind slab + persistent weak layers sharing the same
    # terrain — same aspects and elevation, two contributing hazard types.
    multi_card: dict[str, Any] = {
        "category": "dry",
        "danger_level": 3,
        "danger_level_key": "considerable",
        "problem_type": "wind_slab",
        "time_period": "all_day",
        "aspects": ["N", "NE", "NW", "W"],
        "elevation": ElevationBounds(
            lower="2400",
            upper="",
            display="above 2400m",
            bound_type=ELEVATION_LOWER,
        ),
        "comment_html": "",
        "label": "Wind slab + Persistent weak layers",
        "time_period_label": "",
        "hide_comment": False,
        "core_zone_text": "N to W aspects, above 2400m",
    }

    # --- Wet hazard cards ---

    # Wet snow: moderate, east-to-west slopes below 2200m, afternoon.
    wet_card: dict[str, Any] = {
        "category": "wet",
        "danger_level": 2,
        "danger_level_key": "moderate",
        "problem_type": "wet_snow",
        "time_period": "later",
        "aspects": ["E", "SE", "S", "SW", "W"],
        "elevation": ElevationBounds(
            lower="",
            upper="2200",
            display="below 2200m",
            bound_type=ELEVATION_UPPER,
        ),
        "comment_html": "",
        "label": "Wet snow",
        "time_period_label": "Later",
        "hide_comment": False,
        "core_zone_text": "E to W aspects, below 2200m",
    }

    # Gliding snow: moderate, south-facing slopes below 1800m, active all day.
    gliding_card: dict[str, Any] = {
        "category": "wet",
        "danger_level": 2,
        "danger_level_key": "moderate",
        "problem_type": "gliding_snow",
        "time_period": "all_day",
        "aspects": ["S", "SE", "SW"],
        "elevation": ElevationBounds(
            lower="",
            upper="1800",
            display="below 1800m",
            bound_type=ELEVATION_UPPER,
        ),
        "comment_html": "",
        "label": "Gliding snow",
        "time_period_label": "",
        "hide_comment": False,
        "core_zone_text": "S-facing aspects, below 1800m",
    }

    return {
        "example_day_window_single": single_moderate,
        "example_day_window_sub_minus": sub_minus,
        "example_day_window_split": split_day,
        "example_new_snow_card": new_snow_card,
        "example_persistent_card": persistent_card,
        "example_dry_card": dry_card,
        "example_multi_card": multi_card,
        "example_wet_card": wet_card,
        "example_gliding_card": gliding_card,
    }


def how_to_read_bulletin(request: HttpRequest) -> HttpResponse:
    """
    Render the /how-to-read-a-bulletin page.

    Static reference guide explaining the five-level danger scale,
    subdivisions, dry/wet hazard categories, elevation and aspect
    conventions, how the day evolves, and the narrative sections.
    Content is derived from analysis of 2,159 Swiss avalanche bulletins
    and the SLF Interpretation Guide (November 2025 edition). Inline
    component examples are built by :func:`_build_guide_examples`.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered guide page.

    """
    return render(request, "public/how_to_read_bulletin.html", _build_guide_examples())


# User-facing labels for the basemap layer picker (SNOW-58). Keyed by the
# same key as ``settings.BASEMAP_STYLES``; ``gettext_lazy`` so a future
# i18n pass picks them up. Presentation, not config — lives here rather
# than in settings so the picker UI stays close to the view that renders it.
_BASEMAP_LABELS: dict[str, Promise] = {
    "openfreemap_liberty": _("Standard"),
    "swisstopo_winter": _("Winter"),
    "swisstopo_light": _("Light"),
}


# Avalanche seasons run roughly Nov → May. The canonical boundary is
# November 1 — any date on or after Nov 1 belongs to the season that
# starts in that calendar year; dates before Nov 1 belong to the season
# that started in the previous calendar year. Used by the map scrubber
# + timelapse to size the slider track.
_SEASON_START_MONTH = 11
_SEASON_START_DAY = 1


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


def _basemaps_for_picker() -> list[dict[str, Any]]:
    """Build the ordered ``{key, label, url}`` catalogue for the picker.

    Order follows ``_BASEMAP_LABELS`` (the user-facing intent), not the
    iteration order of ``settings.BASEMAP_STYLES`` — the labels dict is
    where the picker's display order is curated. Any key in settings
    that has no label here is dropped from the picker (still usable as
    a ``BASEMAP=`` env override on the deployed default).
    """
    return [
        {"key": key, "label": label, "url": settings.BASEMAP_STYLES[key]}
        for key, label in _BASEMAP_LABELS.items()
        if key in settings.BASEMAP_STYLES
    ]


def map_view(request: HttpRequest) -> HttpResponse:
    """
    Render the interactive region-choropleth map page.

    The page is a MapLibre GL JS client that fetches three JSON
    endpoints (``/api/regions.geojson``, ``/api/today-summaries/``,
    ``/api/resorts-by-region/``) and colours each region by today's
    danger rating. The map template is a standalone page today but the
    DOM is structured so it can be embedded inside the marketing
    homepage later.

    The basemap layer picker (SNOW-58) is fed two pieces of context:
    ``basemaps`` — an ordered list of ``{key, label, url}`` dicts built
    from ``settings.BASEMAP_STYLES`` × ``_BASEMAP_LABELS`` — and
    ``default_basemap_key`` (``settings.BASEMAP``), the env-resolved
    fallback used when localStorage is empty or names a basemap that
    has since been removed from the catalogue.

    SNOW-74 — when ``?edit=resorts`` is on the URL **and** the
    ``edit_map`` waffle flag is active for the request user (SNOW-86),
    the page boots into resort-edit mode: the side panel is rendered,
    ``static/js/map_edit_resorts.js`` is loaded, and the API URLs
    powering it are passed through context. When the flag is off the
    query string is silently ignored — the flag check lives at the
    API endpoints themselves as well.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered map page.

    """
    today = datetime.date.today()
    season_start, season_end = _season_date_range(today)
    # Clamp so the thumb stays inside the track if the user loads the page
    # outside the nominal season window (e.g. mid-summer development).
    span = (season_end - season_start).days
    elapsed = max(0, min((today - season_start).days, span))
    today_pct = round(elapsed / span * 100, 2) if span else 100.0

    edit_mode = request.GET.get("edit") == "resorts" and waffle.flag_is_active(
        request, "edit_map"
    )
    edit_context: dict[str, Any] = {"edit_mode": edit_mode}
    if edit_mode:
        # The save URL contains an :resort_id placeholder — same trick as
        # the region_summary URL in static/js/map.js: reverse with a
        # dummy id, then string-replace at runtime in the JS.
        save_url_template = reverse("api:edit_resort_save_coords", args=[0]).replace(
            "/0/", "/__ID__/"
        )
        edit_context.update(
            {
                "edit_queue_url": reverse("api:edit_resorts_queue"),
                "edit_save_url_template": save_url_template,
                "edit_resorts_geojson_url": reverse("api:resorts_geojson"),
            }
        )

    return render(
        request,
        "public/map.html",
        {
            "basemaps": _basemaps_for_picker(),
            "default_basemap_key": settings.BASEMAP,
            "season_start": season_start,
            "season_end": season_end,
            "today": today,
            "today_pct": today_pct,
            **edit_context,
        },
    )


def serve_sw(request: HttpRequest) -> HttpResponse:
    """
    Serve the service worker script from the root URL path (``/sw.js``).

    Service workers control the scope they are served from. Serving from
    ``/sw.js`` lets the SW control ``/`` (the whole site). The
    ``Service-Worker-Allowed`` header makes that scope explicit, and
    ``Cache-Control: no-cache`` ensures the browser re-validates on every
    page load so SW updates take effect promptly.

    Args:
        request: The incoming HTTP request.

    Returns:
        An ``HttpResponse`` with the SW script body and the required
        ``Service-Worker-Allowed`` / ``Cache-Control`` headers.

    Raises:
        Http404: If ``js/sw.js`` is not found by staticfiles finders.

    """
    path = finders.find("js/sw.js")
    if path is None:
        raise Http404("Service worker script not found.")
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def serve_manifest(request: HttpRequest) -> HttpResponse:
    """
    Serve the web app manifest at ``/manifest.webmanifest`` (SNOW-118).

    The manifest is templated rather than served as a static file so the
    browser-facing identity URLs (``id``, ``start_url``, ``scope``) can
    be rendered as **absolute** URLs derived from ``settings.SITE_BASE_URL``
    — relative paths technically resolve correctly per origin, but explicit
    absolute URLs are the W3C recommendation and survive future changes
    to the manifest's URL or to ``start_url``. The same setting is already
    used to build absolute links in transactional emails, so production
    and dev each point at their own canonical hostname via the existing
    env-var (``http://localhost:8000`` in dev, ``https://snowdesk.info``
    in production).

    The response carries ``Content-Type: application/manifest+json`` so
    Chromium honours the manifest spec strictly. ``Cache-Control:
    public, max-age=300`` is short enough that a SITE_BASE_URL change
    propagates within five minutes but long enough to avoid re-rendering
    on every page load.

    Args:
        request: The incoming HTTP request (unused — the manifest is
            origin-keyed via ``SITE_BASE_URL``, not per-request).

    Returns:
        An ``HttpResponse`` with the JSON manifest body and the required
        ``Content-Type`` and ``Cache-Control`` headers.

    """
    base = settings.SITE_BASE_URL.rstrip("/")
    manifest = {
        "name": "Snowdesk",
        "short_name": "Snowdesk",
        "id": f"{base}/",
        "lang": "en",
        "description": "Daily Swiss avalanche bulletins for the alpine region.",
        "categories": ["weather", "sports", "travel"],
        "start_url": f"{base}/",
        "scope": f"{base}/",
        "display": "standalone",
        "background_color": "#f4f1e8",
        "theme_color": "#1a1a1a",
        "icons": [
            {
                "src": "/static/icons/pwa/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/pwa/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/pwa/icon-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "screenshots": [
            {
                "src": "/static/icons/pwa/screenshots/map-wide.png",
                "sizes": "1280x720",
                "type": "image/png",
                "form_factor": "wide",
                "label": "Avalanche map for the alpine region",
            },
            {
                "src": "/static/icons/pwa/screenshots/bulletin-narrow.png",
                "sizes": "750x1334",
                "type": "image/png",
                "form_factor": "narrow",
                "label": "Daily bulletin for a single region",
            },
        ],
    }
    response = HttpResponse(
        json.dumps(manifest, indent=2),
        content_type="application/manifest+json",
    )
    response["Cache-Control"] = "public, max-age=300"
    return response


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
    that issue, and renders the bulletin page **using the same core
    renderer as the canonical route** so the example is byte-for-byte
    identical to a real bulletin page. Refreshing picks a different
    region each time.

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

    # Match the prefetch shape ``bulletin_detail`` uses so the core renders
    # at the same query budget the SNOW-13 monitor enforces.
    regions = list(
        MicroRegion.objects.filter(pk__in=region_ids)
        .select_related("subregion")
        .prefetch_related(
            Prefetch("neighbours", queryset=MicroRegion.objects.order_by("name")),
        )
    )
    if not regions:
        return redirect("public:home")

    region = random.choice(regions)  # noqa: S311 — not crypto
    requested_issue_id = request.GET.get("issue") or None
    return _bulletin_detail_response(
        request,
        region,
        timezone.now().date(),
        requested_issue_id=requested_issue_id,
        # ``examples_random`` is an evergreen "today's bulletin" demo,
        # so the canonical points at the no-date form-2 URL of the
        # picked region — not the dated form 3 (SNOW-99).
        canonical_is_today=True,
    )


@never_cache
def examples_category(request: HttpRequest, danger_level: str) -> HttpResponse:
    """
    Render a random bulletin matching a specific danger level inline.

    Finds the most recent bulletin whose highest ``mainValue`` matches the
    requested level, picks a region from that bulletin, and renders **the
    same view as the canonical bulletin route** with the matched bulletin
    pinned via ``requested_issue_id`` — guaranteeing the page actually
    reflects the requested danger level (not whatever the 10:00-rule
    default would land on for that date).

    Decorated with ``@never_cache`` so refreshing surfaces a different
    matching bulletin each time. Returns 404 if the slug is unrecognised
    or no matching bulletin exists.

    Args:
        request: The incoming HTTP request.
        danger_level: URL slug for the danger level (e.g. ``"considerable"``
            or ``"very-high"``).

    Returns:
        The rendered bulletin page, or 404 if no matching bulletin is
        found.

    """
    danger_key = _DANGER_SLUG_TO_KEY.get(danger_level)
    if danger_key is None:
        raise Http404(f"Unknown danger level: {danger_level}")

    # Filter in Python because SQLite does not support JSON __contains.
    # The candidate pool is small (most recent 200 bulletins) so this is
    # fast enough for a render view.
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
    # Match ``bulletin_detail``'s prefetch shape so the core renders at
    # the SNOW-13-tracked query budget.
    region_bulletin = (
        RegionBulletin.objects.filter(bulletin=bulletin)
        .select_related("region", "region__subregion")
        .prefetch_related(
            Prefetch(
                "region__neighbours",
                queryset=MicroRegion.objects.order_by("name"),
            ),
        )
        .first()
    )
    if not region_bulletin:
        raise Http404(f"No regions found for bulletin: {bulletin.bulletin_id}")

    region = region_bulletin.region
    target_date = bulletin.valid_to.date()
    # Pin the matched bulletin so the rendered page actually shows the
    # requested danger level. An explicit ``?issue=`` query param wins
    # over the pin so deep-links continue to work.
    requested_issue_id = request.GET.get("issue") or str(bulletin.bulletin_id)
    return _bulletin_detail_response(
        request,
        region,
        target_date,
        requested_issue_id=requested_issue_id,
    )


def _redirect_to_canonical(
    request: HttpRequest,
    region: MicroRegion,
    target_date: datetime.date | None = None,
) -> HttpResponse:
    """
    Build a 302 to the fully-qualified ``/<region_id>/<slug>/<date>/`` URL.

    The canonical bulletin URL has a date segment so search engines and
    shared links resolve a single page per (region, day) pair. All three
    non-canonical entry points funnel through here:

    * Form 1 (``/<region_id>/``)
    * Form 2 (``/<region_id>/<slug>/``)
    * Form 3 with a non-canonical region_id or slug (e.g. preserved
      casing or a stale slug like ``ch_4124``)

    ``target_date`` defaults to today; pass the inbound date when
    redirecting from a form-3 URL so the redirect preserves the day the
    user asked for. Any query string on the inbound request is preserved
    so deep links like ``?issue=<uuid>`` continue to work.
    """
    target = region.get_absolute_url(target_date)
    # The semgrep open-redirect rule fires on the syntactic taint flow
    # from ``request.META`` to ``redirect()``. The sink is provably safe:
    # ``target`` is a server-relative path built via ``MicroRegion.get_absolute_url``
    # (always lowercase region_id + slugified name + today's date), and
    # the query string is appended after a literal ``?`` separator — so
    # QUERY_STRING content cannot change the host of the redirect target.
    # Suppress at the source line where taint enters.
    # noqa is for the line-length cap: the rule id makes the line long.
    qs = request.META.get("QUERY_STRING", "")  # noqa: E501  # nosemgrep: python.django.security.injection.open-redirect.open-redirect
    if qs:
        target = f"{target}?{qs}"
    return redirect(target)


# ---------------------------------------------------------------------------
# Forms 1 + 2 + 3 share ``bulletin_detail`` (see end of this section).
# Forms 1 (``/<region_id>/``) and 2 (``/<region_id>/<slug>/``) render today's
# bulletin in place at the inbound URL — they do NOT redirect. Only form 3
# with non-canonical components (e.g. preserved-case region_id or stale
# ``ch_4124``-style slug) redirects to the canonical form-3 URL. The page
# always advertises the canonical form-3 URL via ``<link rel="canonical">``
# regardless of which form the user landed on.
# ---------------------------------------------------------------------------


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


# Order in which day-window rows appear on the masthead's day-windows
# panel. CAAML's ``validTimePeriod`` doesn't impose an ordering; the design
# handoff fixes this as chronological-with-all-day-in-the-middle so rare
# three-window days (earlier + all_day + later) read top-to-bottom.
_DAY_WINDOW_ORDER: tuple[str, ...] = ("earlier", "all_day", "later")

# Pill copy for each window type — see design_handoff_day_windows/README.md.
# Wrapped in ``gettext_lazy`` so the strings stay translatable for any
# future i18n pass even though the soft launch is English-only.
_DAY_WINDOW_PILL_LABELS: dict[str, Promise] = {
    "earlier": _("Earlier"),
    "all_day": _("All day"),
    "later": _("Later"),
}


def _parse_danger_rating(rating: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(period, main_value, subdivision)`` for a CAAML dangerRating dict."""
    period = rating.get("validTimePeriod") or "all_day"
    level = rating.get("mainValue") or ""
    raw_sub = (rating.get("customData") or {}).get("CH", {}).get("subdivision", "")
    return period, level, raw_sub


def _danger_rank(level: str, sub: str) -> tuple[int, int]:
    """Return a sortable rank for a danger level + subdivision pair.

    Band index from ``_DANGER_ORDER`` is the primary key; subdivision maps to
    an integer offset (minus → -1, neutral/absent → 0, plus → +1).  Tuple
    comparison gives the correct total ordering:
    ``(2, -1) < (2, 0) < (2, 1) < (3, -1)``.
    """
    band = _DANGER_ORDER.index(level)
    offset = {"minus": -1, "neutral": 0, "plus": 1}.get(sub, 0)
    return (band, offset)


def _build_day_windows(bulletin: Bulletin) -> list[dict[str, Any]]:
    """
    Return the list[Window] consumed by the day-windows panel partial.

    Reads ``dangerRatings`` directly from the bulletin's CAAML properties.
    Always emits one row for the ``all_day_*`` rating. Emits a second row for
    the ``later_*`` rating only when its effective rank (band + subdivision
    offset) is strictly higher than the ``all_day`` rank — a later rating that
    is equal or lower is suppressed as it implies no improvement. Returns an
    empty list when no ``all_day`` rating is present — the template hides
    the panel in that case.
    """
    props = _get_properties(bulletin)
    ratings: list[dict[str, Any]] = props.get("dangerRatings") or []

    all_day_rating: dict[str, Any] | None = None
    later_rating: dict[str, Any] | None = None
    for r in ratings:
        period, level, _ = _parse_danger_rating(r)
        if period == "all_day" and level in _DANGER_ORDER:
            all_day_rating = r
        elif period == "later" and level in _DANGER_ORDER:
            later_rating = r

    if all_day_rating is None:
        return []

    def _row(rating: dict[str, Any], chip: str | Promise) -> dict[str, Any]:
        _, level, sub = _parse_danger_rating(rating)
        suffix = _SUBDIVISION_SUFFIX.get(sub, "")
        number = _DANGER_PANEL_META[level]["number"]
        return {
            "type": (rating.get("validTimePeriod") or "all_day"),
            "level_key": level,
            "level_css": level.replace("_", "-"),
            "level_label": _DANGER_PANEL_META[level]["label"],
            "level_number": f"{number}{suffix}",
            "caption": "",
            "pill_label": chip,
        }

    windows = [_row(all_day_rating, _DAY_WINDOW_PILL_LABELS["all_day"])]

    if later_rating is not None:
        _, later_level, later_sub = _parse_danger_rating(later_rating)
        _, all_day_level, all_day_sub = _parse_danger_rating(all_day_rating)
        later_rank = _danger_rank(later_level, later_sub)
        all_day_rank = _danger_rank(all_day_level, all_day_sub)
        if later_rank > all_day_rank:
            windows.append(_row(later_rating, _DAY_WINDOW_PILL_LABELS["later"]))

    return windows


def _build_canonical_url(
    request: HttpRequest,
    region: MicroRegion,
    target_date: datetime.date | None,
) -> str:
    """
    Build the absolute canonical URL for a region (and optional date).

    Used by ``_bulletin_detail_response`` to populate the
    ``<link rel="canonical">`` tag. ``target_date`` selects between the
    two canonical families (SNOW-99): pass ``None`` for the form-2
    "today" / evergreen URL ``/<region_id>/<slug>/``, or a ``date`` for
    the form-3 dated URL ``/<region_id>/<slug>/<YYYY-MM-DD>/``. Defers
    to ``MicroRegion.get_absolute_url`` so the path components stay
    consistent with every other internal URL builder.
    """
    return request.build_absolute_uri(region.get_absolute_url(target_date))


def _resolve_region_for_bulletin(region_id: str) -> MicroRegion:
    """
    Look up a MicroRegion with the prefetches the bulletin page needs.

    ``select_related("subregion")`` pre-loads the parent EAWS L2 row the
    masthead's H2 reads — without it, the subregion lookup adds a second
    SELECT on every bulletin pageview (SNOW-13 query-count monitor
    caught the +1 regression). ``neighbours`` is prefetched ordered-by-
    name so the "Adjoining regions" section in the template iterates in
    display order without a per-render sort.
    """
    return get_object_or_404(
        MicroRegion.objects.select_related("subregion").prefetch_related(
            Prefetch("neighbours", queryset=MicroRegion.objects.order_by("name")),
        ),
        region_id__iexact=region_id,
    )


def _bulletin_detail_response(
    request: HttpRequest,
    region: MicroRegion,
    target_date: datetime.date,
    *,
    requested_issue_id: str | None = None,
    canonical_is_today: bool = False,
) -> HttpResponse:
    """
    Render the bulletin viewer for a resolved ``(region, target_date)``.

    Shared core for the canonical bulletin route and both example
    routes — guarantees the example pages are byte-for-byte identical
    to the real bulletin pages.

    The caller is responsible for resolving ``region`` (with
    ``select_related("subregion")`` and the ``neighbours`` prefetch — see
    ``_resolve_region_for_bulletin``) and parsing ``target_date``.
    ``requested_issue_id`` overrides the default 10:00-rule issue
    selection — pass it from ``request.GET.get("issue")`` for the
    canonical route, or from a bulletin's ``bulletin_id`` to pin a
    specific issue for an example page.

    ``canonical_is_today`` selects between the two canonical URL
    families. Pass ``True`` when the inbound request is the no-date
    "today / evergreen" view (forms 1 and 2, ``examples_random``); the
    page advertises the form-2 URL ``/<region_id>/<slug>/``. Pass
    ``False`` when the request is for a specific calendar day
    (form 3, ``examples_category``); the page advertises the form-3
    URL ``/<region_id>/<slug>/<date>/``. The two URLs render the same
    content today but are semantically distinct destinations — the
    today URL is a live page that follows the calendar; the dated URL
    is a historical record.

    Args:
        request: The incoming HTTP request.
        region: A pre-fetched ``MicroRegion`` with ``subregion`` selected and
            ``neighbours`` prefetched.
        target_date: Calendar day the page represents.
        requested_issue_id: Optional bulletin id (UUID string) to pin
            the active issue tab; falls back to the 10:00 default when
            ``None``.
        canonical_is_today: When ``True``, advertise the form-2
            "today" canonical URL; when ``False``, advertise the
            form-3 dated canonical URL.

    Returns:
        The rendered bulletin page (or empty-state page when no issue
        covers the target day).

    """
    adjoining_regions = list(region.neighbours.all())

    # Warm the cache for future region_redirect lookups.
    cache.set(
        _cache_key(region.slug),
        slugify(region.name),
        timeout=_ZONE_NAME_CACHE_TIMEOUT,
    )

    today = timezone.now().date()
    # Two canonical-URL flavours: the no-date "today" form (form 2)
    # for live / evergreen views, and the dated form (form 3) for
    # historical views. See SNOW-99.
    canonical_url = _build_canonical_url(
        request,
        region,
        None if canonical_is_today else target_date,
    )

    # Weather header data (SNOW-98). The snapshot is one row per
    # (region, valid_for_date); ``.first()`` is fine because the model's
    # ``unique_together = (region, valid_for_date)`` guarantees at most
    # one match. ``weather_display`` is ``None`` when no snapshot exists
    # so the partial can render its safe fallback.
    weather_snapshot = (
        WeatherSnapshot.objects.for_date(target_date).filter(region=region).first()
    )
    weather_display = build_weather_display(weather_snapshot, timezone.now())

    # When the page would otherwise emit the HTMX trigger for a past date,
    # warm the snapshot on a background thread so the user's actual click —
    # which comes seconds after the browser prefetch — lands on a server
    # render that bakes weather inline (no HTMX swap, no flash). The HTMX
    # trigger stays in the no-weather template as a safety net for the rare
    # click-before-worker-finishes case. SNOW-164.
    if weather_snapshot is None and target_date < today:
        fetch_weather_async(region, target_date)

    # Collect every issue that touches the target day and pick the one
    # the caller asked for; otherwise fall back to the 10:00-rule
    # default. Multi-issue days render the requested (or default) issue
    # as the page body.
    issues = _issues_for_date(region, target_date)
    selected = _resolve_selected_issue(issues, target_date, requested_issue_id)

    if selected is None:
        response = _render_bulletin_page(
            request,
            {
                "bulletin": None,
                "region": region,
                "region_name": region.name,
                "region_id": region.region_id,
                "page_date": target_date,
                "year": datetime.date.today().year,
                "adjoining_regions": adjoining_regions,
                "season_calendar": season_header(today),
                "weather_display": weather_display,
                "weather_htmx_trigger": weather_display is None,
                "canonical_url": canonical_url,
            },
            bulletin=None,
        )
        # Empty-state: cache briefly so a freshly-ingested bulletin surfaces
        # within a minute without re-running the view on every pageview.
        # Exception: when the response bakes in the HTMX weather trigger
        # (``weather_display is None``), bypass the cache entirely so the
        # browser does not serve the stale HMTL-with-trigger on reload after
        # the snapshot has been populated — that would re-fire HTMX and cause
        # a visible header swap (flash). See SNOW-161 follow-up.
        if weather_display is None:
            add_never_cache_headers(response)
        else:
            patch_cache_control(response, public=True, max_age=60)
        return response

    # The page represents the calendar day chosen in the URL, independent
    # of which issue the viewer has selected — otherwise flipping to the
    # same-day-evening issue would silently bump the header to D+1.
    page_date = target_date

    # Always use the EAWS canonical name from MicroRegion. The
    # ``RegionBulletin.region_name_at_time`` field stores the per-bulletin
    # label SLF publishes alongside each ``regionID`` — but those labels
    # are not the EAWS canonical names (e.g. SLF labels CH-2133 "Stoos"
    # whereas the EAWS reference calls it "Küssnacht - Arth"). Falling
    # back to that field produced visibly-wrong headers for affected
    # regions; preferring ``region.name`` keeps the page consistent with
    # the URL, the map, and any other view that derives names from the
    # MicroRegion fixture. The field is retained on the model as an
    # ingestion-time audit trail but is no longer used for display.
    region_name = region.name

    # Day-based prev/next navigation.
    prev_date, next_date = _get_nav_dates(region, page_date)

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

    day_windows: list[dict[str, Any]] = _build_day_windows(selected)
    # The masthead subtitles the H1 with the parent EAWS L2 sub-region.
    # Prefer the English name where SLF publishes one, otherwise fall back
    # to the locally-dominant native name. ``MicroRegion.subregion`` is
    # non-nullable so this lookup is always safe.
    subregion_name = (
        region.subregion.name_en or region.subregion.name_native
        if region.subregion
        else ""
    )

    season_calendar = season_header(today)

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
        "year": today.year,
        # Season heatmap — surfaced as a slide-down sheet (SNOW-117).
        "season_calendar": season_calendar,
        # Masthead context.
        "day_windows": day_windows,
        "subregion_name": subregion_name,
        # Geographic neighbours — see SNOW-82.
        "adjoining_regions": adjoining_regions,
        # Weather-driven header — see SNOW-98.
        "weather_display": weather_display,
        # Trigger HTMX just-in-time fetch when no snapshot exists (SNOW-159).
        "weather_htmx_trigger": weather_display is None,
        # Canonical form-3 URL — see SNOW-99.
        "canonical_url": canonical_url,
    }
    response = _render_bulletin_page(request, context, bulletin=selected)

    # Cache-Control — branch on whether the page date is in the past.
    # Exception (above either branch): when the response bakes in the HTMX
    # weather trigger (``weather_display is None``), bypass the cache so the
    # browser does not serve stale HTML-with-trigger on reload after the
    # snapshot has been populated — that would re-fire HTMX and cause a
    # visible header swap (flash). See SNOW-161 follow-up.
    if weather_display is None:
        add_never_cache_headers(response)
    elif page_date < today:
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


def bulletin_detail(
    request: HttpRequest,
    region_id: str,
    slug: str | None = None,
    date_str: str | None = None,
) -> HttpResponse:
    """
    Render the bulletin viewer at any of the three URL forms.

    Single entry point for forms 1 (``/<region_id>/``), 2
    (``/<region_id>/<slug>/``), and 3 (``/<region_id>/<slug>/<date>/``).
    Forms 1 and 2 render today's bulletin in place at the inbound URL —
    they do NOT redirect to form 3. Only form 3 with non-canonical path
    components (e.g. ``/CH-4124/ch_4124/<date>/`` instead of
    ``/ch-4124/val-d-anniviers/<date>/``) 302s to the canonical form.

    The canonical-redirect check compares ``request.path`` against
    ``region.get_absolute_url(target_date)``. ``request.path`` is
    inherently free of fragments and query strings (Django strips both
    before populating it), and the redirect helper preserves the
    inbound query string. The check only fires when ``date_str`` is
    present — no-date hits (forms 1 and 2) render in place even when
    the URL casing or slug is non-canonical.

    Two canonical URL families coexist (SNOW-99): the ``<link rel="canonical">``
    advertises the **form-2** URL when the inbound request had no date
    component (forms 1 and 2 — the live "today" view), and the
    **form-3** URL when the inbound request specified a date (form 3 —
    the historical record). The two render the same bytes today but
    are semantically distinct destinations: the no-date URL follows
    the calendar; the dated URL freezes once the date is past.

    The wrapper does *not* live under ``@condition`` because the
    canonical-redirect must take precedence over conditional-GET — a
    cached non-canonical response should not 304 indefinitely. Once we
    know the URL is canonical (or no date was supplied) we delegate to
    ``_bulletin_detail_render`` which is conditional-GET aware.

    For past days the morning bulletin is shown (the updated daytime
    assessment). For the current day the bulletin whose validity window
    contains the current time is shown automatically. Pass
    ``?issue=<uuid>`` to pin a specific issue tab.

    Args:
        request: The incoming HTTP request.
        region_id: SLF region identifier (e.g. ``"CH-4115"``).
        slug: Slugified region name (cosmetic; ignored for lookup).
            ``None`` when hitting form 1.
        date_str: Date in ``YYYY-MM-DD`` format. ``None`` on forms 1
            and 2 → today; unparseable strings on form 3 also fall
            back to today.

    Returns:
        The rendered bulletin page, or a 302 to the canonical URL when
        a form-3 path is non-canonical.

    """
    region = _resolve_region_for_bulletin(region_id)
    target_date = _parse_target_date(date_str)
    if date_str is not None and request.path != region.get_absolute_url(target_date):
        return _redirect_to_canonical(request, region, target_date)
    return _bulletin_detail_render(request, region_id, slug, date_str)


@condition(
    etag_func=_bulletin_page_etag,
    last_modified_func=_bulletin_page_last_modified,
)
def _bulletin_detail_render(
    request: HttpRequest,
    region_id: str,
    slug: str | None = None,
    date_str: str | None = None,
) -> HttpResponse:
    """
    Render the bulletin page with conditional-GET.

    Internal helper invoked only when ``bulletin_detail`` has confirmed
    the inbound URL is one of the renderable forms (form 1, form 2, or
    canonical form 3). Wrapped in ``@condition`` so browsers and CDNs
    can serve 304 responses when the bulletin data hasn't changed.
    """
    region = _resolve_region_for_bulletin(region_id)
    target_date = _parse_target_date(date_str)
    requested_issue_id = request.GET.get("issue") or None
    return _bulletin_detail_response(
        request,
        region,
        target_date,
        requested_issue_id=requested_issue_id,
        canonical_is_today=date_str is None,
    )


# ---------------------------------------------------------------------------
# Weather snippet — HTMX-triggered just-in-time weather fetch (SNOW-159)
# ---------------------------------------------------------------------------


@require_htmx
@require_POST
def fetch_weather_snippet(
    request: HttpRequest, region_id: str, date_str: str
) -> HttpResponse:
    """
    Fetch and return the weather header fragment for a given region and date.

    Called by HTMX on load when the bulletin page renders without a
    ``WeatherSnapshot`` for the current ``(region, date)`` pair.

    The view first queries the DB for an existing snapshot (belt-and-braces
    guard against race conditions — a concurrent request may have already
    persisted one by the time this endpoint is reached).  Only when no
    snapshot is found does the view hit Open-Meteo (forecast endpoint for
    today/future, archive endpoint for past dates), persist the result, and
    return the rendered ``includes/bulletin_header.html`` fragment.

    ``weather_htmx_trigger`` is always ``False`` in the returned fragment so
    that a fetch failure never triggers an infinite retry loop — HTMX will not
    re-fire the trigger on the swapped-in response.

    On any error the view still returns HTTP 200 with the no-weather fragment
    (``data-weather-bucket="none"``); the failure is logged server-side only.

    Args:
        request: The incoming HTMX POST request.
        region_id: EAWS micro-region identifier (e.g. ``"CH-4115"``).
        date_str: ISO-8601 date string (``"YYYY-MM-DD"``).

    Returns:
        Rendered ``includes/bulletin_header.html`` fragment.

    """
    region = get_object_or_404(
        MicroRegion.objects.select_related("subregion"), region_id__iexact=region_id
    )
    try:
        target_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return HttpResponseBadRequest("Invalid date.")

    today = timezone.localdate()
    snapshot = (
        WeatherSnapshot.objects.for_date(target_date).filter(region=region).first()
    )
    weather_display = None
    if snapshot is not None:
        weather_display = build_weather_display(snapshot, timezone.now())
    else:
        try:
            if target_date < today:
                results = fetch_archive_for_region(
                    region, target_date, target_date, commit=True
                )
                snapshot = results[0][0] if results else None
            else:
                result = fetch_weather_for_region(region, target_date, commit=True)
                snapshot = result[0] if result is not None else None
            if snapshot is not None:
                weather_display = build_weather_display(snapshot, timezone.now())
        except Exception:
            logger.warning(
                "weather_snippet fetch failed: region=%s date=%s",
                region_id,
                target_date,
                exc_info=True,
            )

    subregion_name = (
        region.subregion.name_en or region.subregion.name_native
        if region.subregion
        else ""
    )
    return render(
        request,
        "includes/bulletin_header.html",
        {
            "weather_display": weather_display,
            "weather_htmx_trigger": False,
            "region_name": region.name,
            "subregion_name": subregion_name,
            "page_date": target_date,
            "region_id": region.region_id,
        },
    )


# ---------------------------------------------------------------------------
# Season calendar partial — HTMX-deferred heatmap grid (SNOW-170)
# ---------------------------------------------------------------------------


@require_htmx
def season_calendar_partial(request: HttpRequest, region_id: str) -> HttpResponse:
    """
    Return the season heatmap grid fragment for a given region.

    Called by HTMX on the first open of the season sheet. Subsequent opens
    reuse the cached DOM — no second request fires.

    To guarantee zero DB queries on a cache hit, this view calls
    ``cache.get(cache_key)`` before touching the DB. On a hit it returns
    ``HttpResponse(cached_body)`` immediately. On a miss, ``build_season_grid``
    runs, the template renders, and ``cache.set(cache_key, response.content,
    90000)`` stores the raw bytes for subsequent requests. The key is
    invalidated by ``apply_bulletin_day_ratings`` after each ingest so the
    next open re-queries with fresh data.

    Args:
        request: The incoming HTMX GET request.
        region_id: EAWS micro-region identifier (e.g. ``"CH-4115"``).

    Returns:
        Rendered ``public/partials/_season_calendar.html`` fragment.

    """
    today = timezone.localdate()
    today_iso = today.isoformat()

    # Check the response cache before touching the DB.  ``canonical_region_id``
    # is ``slugify(region_id)`` — computable from the URL parameter alone — so
    # the cache hit path issues zero DB queries.  The key is shared with
    # ``apply_bulletin_day_ratings`` (which deletes it after ingest) so fresh
    # data is always served after the next bulletin lands.
    canonical_id = slugify(region_id)
    cache_key = make_template_fragment_key("season_calendar", [canonical_id, today_iso])
    cached_body: bytes | None = cache.get(cache_key)
    if cached_body is not None:
        return HttpResponse(cached_body)

    region = get_object_or_404(
        MicroRegion.objects.select_related("subregion"), region_id__iexact=region_id
    )
    grid = build_season_grid(region, today)
    response = render(
        request,
        "public/partials/_season_calendar.html",
        {
            "region": region,
            "season_calendar": grid,
            "today_iso": today_iso,
        },
    )
    # Cache the full rendered body so subsequent hits are byte-for-byte
    # identical and issue zero DB queries.  25 hours — safe because ingest
    # invalidates the key via apply_bulletin_day_ratings.
    cache.set(cache_key, response.content, 90000)
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

# Kind derivation for grouping avalanche problems into rating-block cards.
_KIND_MAP: dict[str, str] = {
    "new_snow": "dry",
    "wind_slab": "dry",
    "persistent_weak_layers": "dry",
    "cornices": "dry",
    "no_distinct_avalanche_problem": "dry",
    "favourable_situation": "dry",
    "wet_snow": "wet",
    "gliding_snow": "gliding",
}
_KIND_ORDER: dict[str, int] = {"dry": 0, "wet": 1, "gliding": 2}
_KIND_TITLES: dict[str, Any] = {
    "dry": _("Dry avalanches"),
    "wet": _("Wet-snow avalanches"),
    "gliding": _("Gliding avalanches"),
}
_KIND_CATEGORY: dict[str, str] = {"dry": "dry", "wet": "wet", "gliding": "wet"}
_DANGER_RATING_INT: dict[str, int] = {
    "low": 1,
    "moderate": 2,
    "considerable": 3,
    "high": 4,
    "very_high": 5,
}
# Map CAAML ``customData.CH.subdivision`` strings to display suffixes.
_SUBDIVISION_SUFFIX: dict[str, str] = {
    "minus": "-",
    "neutral": "=",
    "plus": "+",
}


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


def _enrich_avalanche_problem(
    problem: dict[str, Any],
    cluster: list[dict[str, Any]],
    idx: int,
) -> dict[str, Any]:
    """
    Build a presentation-ready dict from a raw CAAML avalancheProblems entry.

    Args:
        problem: One entry from the CAAML ``avalancheProblems`` array.
        cluster: All problems in the same (kind, danger_level) group.
        idx: Index of this problem within ``cluster``.

    Returns:
        Dict with ``problem_type``, ``time_period``, ``aspects``,
        ``elevation``, ``comment_html``, ``label``, ``time_period_label``,
        and ``hide_comment`` keys.

    """
    problem_type: str = problem.get("problemType") or ""
    time_period: str = problem.get("validTimePeriod") or ""
    aspects: list[str] = problem.get("aspects") or []
    comment_html: str = problem.get("comment") or ""
    raw_elevation: dict[str, Any] | None = problem.get("elevation") or None
    elevation = _format_elevation(raw_elevation) if raw_elevation else None
    core_zone_text: str = ((problem.get("customData") or {}).get("CH") or {}).get(
        "coreZoneText"
    ) or ""

    label = _PROBLEM_LABELS.get(
        problem_type, problem_type.replace("_", " ").capitalize()
    )
    time_period_label = _TIME_PERIOD_LABELS.get(time_period, "")

    hide_comment = False
    if comment_html and len(cluster) > 1:
        plain = _plain_text(comment_html)
        later_plains = [_plain_text(p.get("comment") or "") for p in cluster[idx + 1 :]]
        if plain in later_plains:
            hide_comment = True

    return {
        "problem_type": problem_type,
        "time_period": time_period,
        "aspects": aspects,
        "elevation": elevation,
        "comment_html": comment_html,
        "label": label,
        "time_period_label": time_period_label,
        "hide_comment": hide_comment,
        "core_zone_text": core_zone_text,
    }


def _problem_card(raw_p: dict[str, Any], category: str) -> dict[str, Any]:
    """Build a flat presentation card dict from one raw CAAML avalancheProblem."""
    drv = raw_p.get("dangerRatingValue") or ""
    danger_level = _DANGER_RATING_INT.get(drv, 1)
    danger_level_key = drv.replace("_", "-")
    enriched = _enrich_avalanche_problem(raw_p, [raw_p], 0)
    return {
        "category": category,
        "danger_level": danger_level,
        "danger_level_key": danger_level_key,
        **enriched,
    }


def _problem_cards_from_aggregation(
    aggregation: list[dict[str, Any]],
    problem_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build one card per aggregation entry.

    Collapses multiple problem types within an entry into a single card
    with a combined label (e.g. "Wet snow + Gliding snow").

    Per the bulletin guide, multiple problems within one aggregation entry
    always share identical spatial constraints. The only difference is the
    problem type title, so labels are joined with " + ".

    Data-backed assumptions (drawn from analysis of 2,159 SLF bulletins —
    see docs/bulletin-guide.md):
    - Every aggregation entry carries a ``category`` field.
    - Every aggregation entry carries a non-empty ``problemTypes`` list.
    - Every ``problemType`` token in aggregation resolves to a raw problem.

    Raises:
        ValueError: If any of the above invariants are violated, indicating
            an unexpected change in the SLF schema.

    Args:
        aggregation: The ``customData.CH.aggregation`` list.
        problem_index: ``{problemType: raw_problem}`` built from
            ``avalancheProblems``.

    Returns:
        Flat list of card dicts in aggregation order, one per entry.

    """
    cards: list[dict[str, Any]] = []
    for i, agg_entry in enumerate(aggregation):
        category: str | None = agg_entry.get("category")
        if not category:
            raise ValueError(
                f"aggregation entry {i} is missing 'category': {agg_entry!r}"
            )
        problem_types: list[str] = agg_entry.get("problemTypes") or []
        if not problem_types:
            raise ValueError(
                f"aggregation entry {i} has empty 'problemTypes': {agg_entry!r}"
            )
        for pt in problem_types:
            if pt not in problem_index:
                raise ValueError(
                    f"aggregation entry {i} references problem type {pt!r} "
                    f"which is not in avalancheProblems"
                )

        # Use the first problem for spatial data (all share the same constraints).
        card = _problem_card(problem_index[problem_types[0]], category)

        if len(problem_types) > 1:
            labels = [
                str(_PROBLEM_LABELS.get(pt, pt.replace("_", " ").capitalize()))
                for pt in problem_types
            ]
            card["label"] = " + ".join(labels)
            # Use the max danger level across all problems in this entry.
            danger_levels = [
                _DANGER_RATING_INT.get(
                    problem_index[pt].get("dangerRatingValue") or "", 1
                )
                for pt in problem_types
            ]
            max_level = max(danger_levels)
            card["danger_level"] = max_level
            card["danger_level_key"] = _DANGER_ORDER[max_level - 1].replace("_", "-")

        cards.append(card)
    return cards


def build_problem_cards(
    raw_problems: list[dict[str, Any]],
    aggregation: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Build one flat presentation card per aggregation entry, in aggregation order.

    Both ``raw_problems`` and ``aggregation`` are expected to be present
    whenever the bulletin carries avalanche problems. Missing either logs
    an ERROR and returns an empty list. Schema violations (missing category,
    empty problemTypes, unresolved problem type) are caught and logged.

    Args:
        raw_problems: The CAAML ``avalancheProblems`` array.
        aggregation: The ``customData.CH.aggregation`` array.

    Returns:
        List of flat card dicts in aggregation order, or empty list on error.

    """
    if not raw_problems:
        # Empty avalancheProblems is normal on quiet days and for any
        # bulletin whose risk is described purely in prose. Callers fall
        # back to the render-model traits when this returns [].
        return []
    if not aggregation:
        # EUREGIO bulletins never carry customData.CH.aggregation — that's
        # source-specific to SLF. Callers (_resolve_problem_cards) fall back
        # to the render-model traits in that case.
        return []
    index = {p["problemType"]: p for p in raw_problems if p.get("problemType")}
    try:
        return _problem_cards_from_aggregation(aggregation, index)
    except ValueError:
        logger.exception("build_problem_cards: unexpected aggregation schema")
        return []


def _resolve_problem_cards(
    raw_problems: list[dict[str, Any]],
    aggregation: list[dict[str, Any]],
    traits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Resolve problem cards from either the CAAML aggregation or render model traits.

    SLF bulletins carry ``customData.CH.aggregation`` so ``build_problem_cards``
    produces a non-empty list.  EUREGIO bulletins have no aggregation; in that
    case the enriched render-model traits are used as the card source instead.

    Args:
        raw_problems: CAAML avalancheProblems list from the bulletin properties.
        aggregation: SLF aggregation list (may be empty for EUREGIO).
        traits: Enriched render-model traits (used as fallback).

    Returns:
        Flat list of card dicts ready for ``_rating_block.html``.

    """
    cards = build_problem_cards(raw_problems, aggregation)
    if not cards and traits:
        cards = _problem_cards_from_render_model_traits(traits)
    return cards


def _problem_cards_from_render_model_traits(
    traits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Build one problem card per render-model trait.

    Used as a fallback for EUREGIO bulletins which carry no
    ``customData.CH.aggregation``, so ``build_problem_cards`` returns [].
    The traits list comes from the **enriched** render model (already processed
    by ``enrich_render_model``), so elevation and label fields are already in
    the presentation-ready shape the ``_rating_block.html`` partial expects.

    One card is emitted per trait using the first problem in each trait for
    spatial data (aspects / elevation) — EUREGIO aggregation entries always
    contain a single problem type per time-period group.

    Args:
        traits: Enriched render model traits list.

    Returns:
        Flat list of card dicts, one per trait, in trait order.

    """
    cards: list[dict[str, Any]] = []
    for trait in traits:
        category: str = trait.get("category") or ""
        danger_level: int = trait.get("danger_level") or 1
        time_period: str = trait.get("time_period") or "all_day"
        time_period_label: str | Promise = _TIME_PERIOD_LABELS.get(time_period, "")
        title: str = trait.get("title") or ""
        problems: list[dict[str, Any]] = trait.get("problems") or []
        if not problems:
            continue
        # Use the first problem for spatial data; label from the trait title.
        first = problems[0]
        cards.append(
            {
                "category": category,
                "danger_level": danger_level,
                "danger_level_key": _DANGER_ORDER[danger_level - 1].replace("_", "-"),
                "label": title,
                "time_period_label": time_period_label,
                "aspects": first.get("aspects") or [],
                "elevation": first.get("elevation"),
                "comment_html": first.get("comment_html") or "",
                "core_zone_text": first.get("core_zone_text") or "",
                "hide_comment": False,
                # v4: avalanche_type for slab/loose chip (may be None).
                "avalanche_type": first.get("avalanche_type"),
            }
        )
    return cards


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


def enrich_render_model(
    render_model: dict[str, Any],
) -> dict[str, Any]:
    """
    Add presentation-ready fields to the render model's traits and problems.

    Converts raw render model problem dicts (int elevation bounds) into the
    richer shape ``_rating_block.html`` expects, adding labels, ElevationBounds,
    field_guidance, and hide_comment. Called from both the bulletin page view
    and the map drawer endpoint (``public.api.region_summary``) so the two
    rendering paths share a single data shape.

    Args:
        render_model: A render model dict as produced by
            :func:`bulletins.services.render_model.build_render_model`.

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
    raw_problems: list[dict[str, Any]] = props.get("avalancheProblems") or []
    ch_data: dict[str, Any] = (props.get("customData") or {}).get("CH") or {}
    aggregation: list[dict[str, Any]] = ch_data.get("aggregation") or []
    ratings: list[dict[str, Any]] = props.get("dangerRatings") or []
    if not ratings:
        logger.error(
            "_build_panel_context: bulletin %s has no dangerRatings",
            bulletin.pk,
        )
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
    render_model = enrich_render_model(raw_render_model)

    traits: list[dict[str, Any]] = render_model.get("traits") or []

    # SLF bulletins use CH aggregation; EUREGIO bulletins fall back to traits.
    problem_cards = _resolve_problem_cards(raw_problems, aggregation, traits)

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
        "admin_url": reverse("admin:bulletins_bulletin_change", args=[bulletin.pk]),
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
        "problem_cards": problem_cards,
    }
    panel["day_character"] = compute_day_character(raw_render_model)
    return panel
