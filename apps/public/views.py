"""
apps/public/views.py — Views for the public-facing bulletin site.

URL structure:
  /                                          Interactive map homepage (canonical).
  /examples/random/                          Random bulletin (rendered inline).
  /examples/category/<danger_level>/         Random bulletin by danger level.
  /random/                                   Deprecated → /examples/random/.
  /<region_id>/                              Redirects to /<region_id>/<slug>/.
  /<region_id>/<slug>/                       Today's bulletin for a region.
  /<region_id>/<slug>/<date>/                Bulletin for a specific date.
  /<region_id>/season/                       Full-season page (up to 100 panels).

SNOW-344: ``/map/`` is a permanent 301 redirect to ``/``; ``map_view``
has been removed. Edit-resorts mode (``?edit=resorts``) now lives in
``home()`` directly.

Each page represents a single day, identified by the bulletin's ``valid_to``
date.  Two bulletins may cover a day: an evening issue (valid from ~16:00 the
previous day) and a morning update (valid from ~07:00 on the day itself).

* **Previous days**: the morning bulletin is shown when available (it
  overrides the evening forecast); otherwise the evening bulletin is used.
* **Current day**: the bulletin whose validity window contains the current
  time is shown automatically.

The CAAML raw data does not contain the AI-generated summary fields
described in docs/site-structure.md (overallVerdict, activity ratings,
structured weather, etc.). Where possible, equivalent values are derived
from the raw CAAML data; sections with no available data are omitted from
the template context so the template hides them gracefully.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
import random
import uuid
from collections import defaultdict
from typing import Any, NamedTuple, cast

import waffle
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.cache.utils import make_template_fragment_key
from django.db.models import Max, Prefetch
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseGone,
    HttpResponseNotModified,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_cache_control
from django.utils.functional import Promise
from django.utils.html import strip_tags
from django.utils.http import quote_etag
from django.utils.text import slugify
from django.utils.translation import (
    get_language,
    gettext as _gettext,
    gettext_lazy as _,
)
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    condition,
    require_http_methods,
    require_POST,
)
from django_ratelimit.decorators import ratelimit

from apps import analytics
from apps.accounts.identity import request_identity
from apps.accounts.models import Subscription, user_is_verified
from apps.bulletins.models import (
    Bulletin,
    BulletinShare,
    BulletinShareClick,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.schema import ValidTimePeriod
from apps.bulletins.services import day_summary
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    DayCharacter,
    PeriodTransition,
    RenderModelBuildError,
    band_label_for_elevation,
    build_render_model,
    compute_day_character,
    compute_period_transition,
    derive_problem_family,
    detect_prose_spatial,
    problem_types_for,
)
from apps.core.decorators import require_htmx
from apps.core.http import client_ip, is_speculative
from apps.core.services.request_log import capture as capture_request_log
from apps.core.sw_shell import cache_version, cached_cache_version, inject_cache_version
from apps.core.utils import html_to_markdown
from apps.favourites.constants import FAVOURITE_LIST_MAP_VARIANT
from apps.favourites.models import Favourite
from apps.observations.constants import OBSERVATION_LIST_MAP_VARIANT
from apps.observations.models import FieldObservation
from apps.regions.models import MicroRegion, Resort
from apps.routes.constants import ROUTE_LIST_MAP_VARIANT
from apps.weather.models import (
    ForecastCellWeather,
    WeatherSnapshot,
)
from apps.weather.services.weather_display import (
    build_point_forecast_panel,
    build_weather_display,
)
from apps.weather.services.weather_fetcher import (
    POINT_FORECAST_DAYS,
    fetch_weather_async,
    fetch_weather_for_region,
)

from .component_previews import help_illustrations
from .decorators import lowercase_region_id
from .guidance import load_field_guidance
from .headlines import headline_for
from .season_calendar import (
    SeasonRibbon,
    build_season_grid,
    build_season_ribbon,
    season_header,
)
from .site_environment import PWAEnvironmentIdentity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TendencyOutlook — directional outlook block (ALBINA tendency)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TendencyOutlook:
    """Directional outlook block derived from ALBINA tendency data.

    Carries the arrow glyph, human-readable label, optional target date
    (raw ISO string to be formatted by the template), and optional
    highlights prose for the outlook block below Section 3b.

    Attributes:
        tendency_type: CAAML tendency type string (``steady`` / ``increasing``
            / ``decreasing``), or ``None`` for the neutral fallback.
        arrow: Unicode arrow glyph (→ / ↗ / ↘) or empty string for neutral.
        label: Human-readable label (translatable string).
        valid_until: Raw ISO-8601 string for the target date, or ``None``.
        highlights: Forecaster-authored tendency lead prose, or empty string.

    """

    tendency_type: str | None
    arrow: str
    label: str | Promise
    valid_until: str | None
    highlights: str


# CAAML tendency_type canonical values (lowercase per the stored render model).
_TENDENCY_STEADY = "steady"
_TENDENCY_INCREASING = "increasing"
_TENDENCY_DECREASING = "decreasing"

# Arrow glyphs keyed on canonical tendency_type values.
_TENDENCY_ARROW: dict[str, str] = {
    _TENDENCY_STEADY: "→",
    _TENDENCY_INCREASING: "↗",
    _TENDENCY_DECREASING: "↘",
}

# Translatable labels keyed on canonical tendency_type values.
_TENDENCY_LABEL: dict[str, Promise] = {
    _TENDENCY_STEADY: _("Constant avalanche danger"),
    _TENDENCY_INCREASING: _("Increasing avalanche danger"),
    _TENDENCY_DECREASING: _("Decreasing avalanche danger"),
}


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

# Mapping from Bulletin.Source raw values to (display label, agency URL).
# Used by the bulletin detail page to render the Source cell in the metadata strip.
BULLETIN_SOURCE_LINKS: dict[str, tuple[str, str]] = {
    Bulletin.Source.SLF: ("SLF", "https://www.slf.ch"),
    Bulletin.Source.ALBINA: ("ALBINA", "https://avalanche.report"),
    Bulletin.Source.METEOFRANCE: (
        "Météo-France",
        "https://meteofrance.com/meteo-montagne",
    ),
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
    * When ``settings.DEBUG`` is True *or* the request comes from a
      superuser, and a bulletin is present, its raw CAAML ``raw_data``
      payload is embedded as a ``<script type="application/json">`` tag
      so it is visible in the page source but invisible to the reader.

    Args:
        request: The incoming HTTP request.
        context: The template context (this helper adds ``raw_data_json``
            when appropriate but leaves the rest untouched).
        bulletin: The bulletin being rendered, or ``None`` for empty-state pages.

    Returns:
        The rendered ``HttpResponse`` with the debug header (and, when
        DEBUG=True or request.user.is_superuser, the raw-data script tag)
        attached.

    """
    if bulletin is not None and (settings.DEBUG or request.user.is_superuser):
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
    Return calendar-adjacent prev/next dates for day-by-day navigation.

    Navigation steps one calendar day at a time regardless of whether a
    bulletin exists for the adjacent day — users can reach empty-state pages
    by navigating.  The upper bound is tomorrow (dates further in the future
    offer no next link); the lower bound is the oldest bulletin's
    ``valid_to`` date for the region (once the user reaches that day, no
    earlier prev link is offered).

    Args:
        region: The MicroRegion to navigate within.
        current_date: The date currently being viewed.

    Returns:
        A (prev_date, next_date) tuple; either may be None when the
        corresponding bound is reached.

    """
    today = timezone.now().date()
    tomorrow = today + datetime.timedelta(days=1)

    next_date: datetime.date | None = None
    if current_date < tomorrow:
        next_date = current_date + datetime.timedelta(days=1)

    oldest_date = Bulletin.objects.filter(regions=region).earliest_valid_to_date()
    prev_date: datetime.date | None = None
    if oldest_date is not None and current_date > oldest_date:
        prev_date = current_date - datetime.timedelta(days=1)

    return prev_date, next_date


def _has_later_bulletin(region: MicroRegion, page_date: datetime.date) -> bool:
    """
    Return True when a bulletin exists for any date after *page_date*.

    Used on the today branch as a proxy for "a later bulletin has already
    been published for this region", replacing the old ``next_date is None``
    check.  Now that next_date always points to the adjacent calendar day,
    its presence no longer signals the absence of a later bulletin.

    The check is a cheap EXISTS query; callers should short-circuit behind
    ``is_today`` so it only fires on the current-day page.

    Args:
        region: The MicroRegion whose bulletins are searched.
        page_date: The calendar day of the page being rendered.

    Returns:
        True if any bulletin for the region has ``valid_to`` strictly after
        *page_date*.

    """
    return Bulletin.objects.filter(
        regions=region, valid_to__date__gt=page_date
    ).exists()


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


def _edit_locations_context() -> dict[str, str]:
    """Return the URL context the location editor's panel is wired with.

    Three of the five endpoints take a row id, so they are reversed with a
    dummy and string-replaced at runtime in the JS — the same
    ``__ID__`` placeholder trick ``edit_save_url_template`` above uses, and
    the same one ``static/js/map.js`` uses for the region-summary URL.

    Returns:
        The five URLs the panel's data attributes carry.

    """

    def _templated(name: str) -> str:
        """Reverse ``name`` with a dummy id and swap in the placeholder."""
        return reverse(name, args=[0]).replace("/0/", "/__ID__/")

    return {
        "edit_locations_queue_url": reverse("api:edit_locations_queue"),
        "edit_location_create_url": reverse("api:edit_location_create"),
        "edit_location_save_url_template": _templated("api:edit_location_save"),
        "edit_location_link_url_template": _templated("api:edit_location_link"),
        "edit_location_unlink_url_template": _templated("api:edit_location_unlink"),
    }


# The estates the in-map editors can curate. ``?edit=`` names one of these
# or the page is the ordinary map — an unrecognised value is not an error,
# it is simply not an editor, so the URL stays safe to bookmark and safe to
# share (SNOW-755).
_EDIT_TARGETS: frozenset[str] = frozenset({"resorts", "locations"})


def _edit_target(request: HttpRequest) -> str:
    """Return the estate ``?edit=`` selects for this request, or "".

    Both halves have to agree: the querystring names a known estate **and**
    the request user is a superuser (SNOW-724). For everyone else the
    parameter is silently ignored rather than refused, because the whole
    point is that an editor URL pasted into a chat renders the normal map.

    Args:
        request: The incoming HTTP request.

    Returns:
        ``"resorts"``, ``"locations"``, or ``""`` for the normal map.

    """
    target = request.GET.get("edit", "")
    if target in _EDIT_TARGETS and request.user.is_superuser:
        return target
    return ""


def home(request: HttpRequest) -> HttpResponse:
    """
    Render the canonical interactive map page.

    SNOW-314: the homepage is the full-frame map with a dismissable landing
    overlay (``#home-intro``). SNOW-344: ``/map/`` now permanently redirects
    here, so this view handles all map-page traffic.

    Edit mode: when ``?edit=`` names an editable estate **and** the request
    user is a superuser, the page renders that estate's edit panel —
    ``resorts`` for the resort-coordinate editor (SNOW-74 / SNOW-86;
    superuser check since SNOW-724), ``locations`` for the curated
    location estate (SNOW-755). For everyone else the query string is
    silently ignored.

    CH-4115 (Martigny / Verbier) is pre-selected so the readout chip and
    breadcrumb are correct on first paint (SNOW-342); the scrubber paints that
    region's season into the track. The intro overlay provides identity, a
    factual one-liner naming the three providers, an off-season note when today
    is past the season end, and a "Register" link. Its "Explore the map" button
    dismisses the overlay and opens the map-help tour (SNOW-535); the "x"
    dismisses without the tour.

    Context:
      ``ribbon``              — default-region (CH-4115) SeasonRibbon or None.
      ``default_region_id``   — str: "CH-4115".
      ``default_region_name`` — str: the pre-selected region's display name.
      ``default_region_slug`` — str: the pre-selected region's bulletin slug.
      ``default_subregion_name`` — str: L2 sub-region name for the breadcrumb.
      ``default_major_name``  — str: L1 major-region name for the breadcrumb.
      ``show_intro``          — True (the overlay renders on the homepage).
      ``is_offseason``        — True when today is past the active season end.
      ``edit_target``         — "resorts" when resort-edit mode is active,
                                else "". The empty string is the normal map.
      ``edit_queue_url``      — URL for the edit queue API (resorts only).
      ``edit_save_url_template`` — Save URL with ``__ID__`` placeholder (resorts).
      ``edit_create_url``     — URL for the resort-create API (resorts only).
      ``edit_resorts_geojson_url`` — URL for the resorts GeoJSON endpoint (resorts).
      ``edit_locations_*``     — the five location-editor URLs, present only
                                when ``edit_target == "locations"``
                                (see ``_edit_locations_context``).
      ``community_reports_geojson_url`` — URL for the community-reports
                                GeoJSON endpoint (SNOW-419).
      ``forecast_weather_geojson_url`` — URL for the map Weather overlay's
                                resort-anchored GeoJSON endpoint (SNOW-573).
      ``region_weather_geojson_url`` — URL for the map Weather overlay's
                                micro-region-centroid GeoJSON endpoint, the
                                coarse tier drawn below zoom 8 (SNOW-698).
      ``slope_layer_eligible`` — True when ``settings.SLOPE_TILE_URL`` is
                                configured (SNOW-691, SNOW-724).
      ``slope_tile_url``      — XYZ tile template for the slope-angle raster,
                                or "" while ineligible (SNOW-691).

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered homepage embedding the map surface.

    """
    today = datetime.date.today()
    base_ctx = _base_map_context(today)
    ribbon = _build_default_ribbon(today)
    # Name + slug + L2/L1 parents of the pre-selected default region (CH-4115)
    # for the readout chip, its "view bulletin" link, and its breadcrumb.
    (
        default_region_name,
        default_region_slug,
        default_subregion_name,
        default_major_name,
    ) = _default_region_label()

    # The season is considered "off" when today is past the season_end bound
    # already narrowed to actual data in _base_map_context().
    season_end: datetime.date = base_ctx["season_end"]
    is_offseason = today > season_end
    # SNOW-445: label + resume-month for the off-season archive bar and the
    # intro-card reference. Derived from the *data window's* calendar season
    # (November → May of the season containing season_end), NOT from `today`
    # (which drifts into the next calendar season over the summer) nor from
    # the data-narrowed season_start (the first populated date, not the
    # Nov 1 boundary). Precomputed here rather than via a template filter chain.
    archived_season_start, _archived_season_end = _season_date_range(season_end)
    season_label = (
        f"{archived_season_start.year}/{(archived_season_start.year + 1) % 100:02d}"
    )

    # SNOW-344 (merged from map_view): edit mode when the querystring and
    # the request user's superuser bit both agree. Silently ignored for
    # everyone else so the URL is safe to bookmark.
    #
    # SNOW-755: ``edit_target`` names WHICH estate is being edited rather
    # than carrying a boolean that only ever meant "resorts" — there is now
    # a second editor (``?edit=locations``) alongside it, and a flag whose
    # name no longer says what it gates is how the two get confused.
    edit_target = _edit_target(request)
    edit_context: dict[str, Any] = {"edit_target": edit_target}
    if edit_target == "resorts":
        # The save URL contains an :resort_id placeholder — same trick as
        # the region_summary URL in static/js/map.js: reverse with a
        # dummy id, then string-replace at runtime in the JS.
        save_url_template = reverse("api:edit_resort_save", args=[0]).replace(
            "/0/", "/__ID__/"
        )
        edit_context.update(
            {
                "edit_queue_url": reverse("api:edit_resorts_queue"),
                "edit_save_url_template": save_url_template,
                "edit_create_url": reverse("api:edit_resort_create"),
                "edit_resorts_geojson_url": reverse("api:resorts_geojson"),
            }
        )
    elif edit_target == "locations":
        edit_context.update(_edit_locations_context())

    report_ctx = _report_context(request)
    favourites_ctx = _favourites_context(request)
    routes_ctx = _routes_context(request)
    downloads_ctx = _downloads_context(request)
    community_reports_ctx = _community_reports_context(request)
    weather_ctx = _weather_context(request)
    slope_ctx = _slope_context(request)

    return render(
        request,
        "public/home.html",
        {
            **base_ctx,
            **edit_context,
            **report_ctx,
            **favourites_ctx,
            **routes_ctx,
            **downloads_ctx,
            **community_reports_ctx,
            **weather_ctx,
            **slope_ctx,
            "ribbon": ribbon,
            "default_region_id": _DEFAULT_RIBBON_REGION_ID,
            "default_region_name": default_region_name,
            "default_region_slug": default_region_slug,
            "default_subregion_name": default_subregion_name,
            "default_major_name": default_major_name,
            "show_intro": True,
            "is_offseason": is_offseason,
            "season_label": season_label,
            "archived_season_start": archived_season_start,
        },
    )


def terms(request: HttpRequest) -> HttpResponse:
    """
    Render the /terms page.

    Holds the SLF data-licence acknowledgement and Snowdesk's liability
    disclaimer. Introduced for SLF data-licence compliance (SNOW-30);
    the actual legal copy is authored by Hugo separately and edited
    directly into ``apps/public/templates/public/terms.html``. This view
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


def help_page(request: HttpRequest) -> HttpResponse:
    """
    Render the /help page.

    Plain-language "how it works" reference explaining every user-facing
    feature (SNOW-456) — distinct from ``how_to_read_bulletin``, which
    teaches the avalanche domain rather than the product. Named
    ``help_page`` rather than ``help`` to avoid shadowing the ``help``
    builtin. ``sync_log_visible`` (SNOW-482) mirrors the same flag gating
    the manage-page sync-log panel — the one surviving waffle flag; every
    other topic section renders for everyone (SNOW-724).

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered help page.

    SNOW-744: six topics illustrate themselves by rendering the real
    partial for the surface they describe, fed by the synthetic contexts
    in ``apps.public.component_previews``. Those are built in memory, so
    this view still issues no queries — a property its own test pins.

    """
    context: dict[str, Any] = {
        "sync_log_visible": waffle.flag_is_active(request, "sync_log"),
    }
    context.update(help_illustrations())
    return render(request, "public/help.html", context)


def observations_list(request: HttpRequest) -> HttpResponse:
    """
    Render the /observations page — a signed-in stream of recent reports.

    Shows FieldObservation rows from the last 48 hours, newest first. An
    anonymous visitor sees a sign-in call to action instead of the list. A
    signed-in viewer sees their own reports plus other users' reports.

    Every row renders its timestamp as recorded, whoever filed it. Other
    users' were floored to the preceding 15-minute mark until the note
    above ``community_reports_geojson`` in ``apps.public.api``: this page
    shows no names either, so the floor identified nobody and only made
    one report read two ages across two surfaces.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered observations page.

    """
    window_hours = 48
    since = timezone.now() - datetime.timedelta(hours=window_hours)
    rows: list[dict[str, Any]] = []

    if request.user.is_authenticated:
        queryset = FieldObservation.objects.recent(since).select_related(
            "region", "user"
        )

        for observation in queryset:
            is_own = observation.user_id == request.user.pk
            rows.append(
                {
                    "type_label": observation.get_observation_type_display(),
                    "region_name": (
                        observation.region.name
                        if observation.region is not None
                        else "unknown region"
                    ),
                    "region_url": (
                        observation.region.get_absolute_url()
                        if observation.region is not None
                        else None
                    ),
                    "observed_at": observation.observed_at,
                    "is_own": is_own,
                }
            )

    context = {
        "rows": rows,
        "viewer_authenticated": request.user.is_authenticated,
        "signin_url": reverse("accounts:sign_in"),
        "window_hours": window_hours,
    }
    return render(request, "public/observations.html", context)


# User-facing labels for the basemap layer picker (SNOW-58). Keyed by the
# same key as ``settings.BASEMAP_STYLES``; ``gettext_lazy`` so a future
# i18n pass picks them up. Presentation, not config — lives here rather
# than in settings so the picker UI stays close to the view that renders it.
_BASEMAP_LABELS: dict[str, Promise] = {
    "openfreemap_liberty": _("Standard"),
    # National mapping-agency basemaps. Each covers only its own country
    # (blank elsewhere), so labels carry the ISO country suffix to set the
    # expectation. ``swisstopo_light`` stays in BASEMAP_STYLES as a BASEMAP=
    # env override but is intentionally omitted here so it no longer appears
    # in the picker.
    "swisstopo_winter": _("Swisstopo (CH)"),
    "ign_plan": _("IGN (FR)"),
    "basemap_at": _("basemap.at (AT)"),
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


# SNOW-314: The default region whose season ribbon is server-rendered at
# first paint on /. CH-4115 (Martigny / Verbier) is the canonical preview
# region — it has the richest test-data coverage and represents a central
# Swiss backcountry area that most users recognise.
_DEFAULT_RIBBON_REGION_ID = "CH-4115"


def _base_map_context(today: datetime.date) -> dict[str, Any]:
    """
    Build the shared map context for the season scrubber and ribbon.

    Shared between ``home()`` (the canonical map page) and any other view
    that embeds the full map surface.

    The season window is narrowed to the actual ``RegionDayRating`` data
    bounds when rows exist (SNOW-173), and falls back to the calendar Nov 1 /
    May 31 window when the season has not yet started or the DB is empty.
    ``today_pct`` is clamped to [0, 100] so the scrubber thumb always sits
    inside the track.

    Args:
        today: Current date — passed in so callers can freeze time in tests.

    Returns:
        A dict with ``basemaps``, ``default_basemap_key``, ``season_start``,
        ``season_end``, ``today``, ``today_pct``, and ``data_end``
        (the latest ``RegionDayRating.date`` in the window, or ``None`` when
        the season has not started or the DB is empty).

    """
    season_start, season_end = _season_date_range(today)
    data_start, data_end = RegionDayRating.objects.season_date_bounds(
        season_start, season_end
    )
    if data_start is not None and data_end is not None:
        season_start = data_start
        season_end = data_end
    span = (season_end - season_start).days
    elapsed = max(0, min((today - season_start).days, span))
    today_pct = round(elapsed / span * 100, 2) if span else 100.0
    return {
        "basemaps": _basemaps_for_picker(),
        "default_basemap_key": settings.BASEMAP,
        # Origin for home.html's preconnect hint. The map's style JSON and
        # vector tiles are the first cross-origin bytes the page needs, and
        # nothing references that host until map.js has parsed and run — so
        # without this the DNS, TCP and TLS round trips start late and land
        # squarely on the critical path for a mobile connection.
        "basemap_origin": settings.BASEMAP_ORIGIN,
        "season_start": season_start,
        "season_end": season_end,
        "today": today,
        "today_pct": today_pct,
        "data_end": data_end,
    }


def _build_default_ribbon(
    today: datetime.date,
) -> SeasonRibbon | None:
    """
    Build the season ribbon for the default region (CH-4115) for first-paint.

    Returns ``None`` gracefully when the region does not exist in the DB
    (e.g. a freshly migrated empty database in development or CI), so the
    ribbon template renders nothing rather than raising a 500.

    Args:
        today: Current date (passed through to ``build_season_ribbon``).

    Returns:
        A :class:`~apps.public.season_calendar.SeasonRibbon` or ``None``.

    """
    try:
        region = MicroRegion.objects.get_by_natural_key(_DEFAULT_RIBBON_REGION_ID)
    except MicroRegion.DoesNotExist:
        return None
    return build_season_ribbon(region, today)


def _default_region_label() -> tuple[str, str, str, str]:
    """
    Return the name, slug, and L2/L1 parent names of the default ribbon region.

    Used to seed the persistent region-readout chip on the homepage, where
    CH-4115 is pre-selected: the name labels the chip, the slug builds its
    "view bulletin" link, and the sub-region (L2) / major (L1) names seed the
    breadcrumb so it is correct on first paint — before any region-selected
    event fires. One query for all four (the parents are select_related).
    Returns ``("", "", "", "")`` when the region is absent (empty DB) so the
    readout simply stays hidden.

    Returns:
        A ``(name, name_slug, subregion_name, major_name)`` tuple, or four empty
        strings if the region does not exist.

    """
    try:
        region = MicroRegion.objects.select_related(
            "subregion__major"
        ).get_by_natural_key(_DEFAULT_RIBBON_REGION_ID)
    except MicroRegion.DoesNotExist:
        return "", "", "", ""
    sub = region.subregion
    subregion_name = sub.name_en if sub.name_en and sub.name_en != sub.prefix else ""
    major_name = sub.major.name_en or sub.major.name_native
    return region.name, region.name_slug, subregion_name, major_name


def _report_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the field-report affordance.

    ``report_eligible`` is True only when the user is authenticated **and** has
    a verified ``Account`` — eligible users get the report flow.  This mirrors
    the server-side gate in ``apps/observations/views.py`` exactly (both call
    ``apps.accounts.models.user_is_verified``) so the client affordance can never
    invite a request the server will 403 (SNOW-477).  ``report_unverified`` is
    True for an authenticated-but-unverified user — the sheet shows them a
    "verify your email" prompt rather than the anonymous sign-in CTA or a
    silent stall.  Anonymous users are neither eligible nor unverified and get
    the sign-in CTA.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``report_eligible``, ``report_unverified``,
        ``report_form_url``, ``report_submit_url``, ``report_list_url``,
        ``report_signin_url``.

    """
    report_eligible = user_is_verified(request.user)
    report_unverified = request.user.is_authenticated and not report_eligible
    return {
        "report_eligible": report_eligible,
        "report_unverified": report_unverified,
        "report_form_url": reverse("observations:report_form"),
        "report_submit_url": reverse("observations:report_submit"),
        # SNOW-658: the roundel opens a panel listing the user's own reports
        # before it offers to file another, so the panel needs the list
        # endpoint.
        # SNOW-752: ``?variant=map`` asks for map-focus rows, whose label
        # frames the report on the map behind the sheet. The parameter was
        # added when /account/observations/ needed to re-read the same
        # endpoint and must NOT get those rows — there is no map on that
        # page to fly. Same convention, same spelling, as the favourites
        # and routes lists above and below.
        "report_list_url": (
            f"{reverse('observations:list')}?variant={OBSERVATION_LIST_MAP_VARIANT}"
        ),
        "report_signin_url": reverse("accounts:sign_in"),
    }


def _favourites_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the saved-pin favourites affordance.

    ``favourites_eligible`` requires the user to be authenticated —
    favourites.js and map.js branch on this to show the real add/rename/
    delete flow versus an anonymous sign-in CTA.

    The dict also includes the HTMX endpoint URLs, a sign-in URL for the
    anonymous CTA, and two ``__UUID__``-templated URLs (mirroring
    ``edit_save_url_template`` in ``home()``) that favourites.js
    string-replaces at runtime to build the rename/delete requests for a
    pin selected on the map — there is no server-side "fetch one
    favourite" endpoint, so the client reconstructs the same rename/delete
    markup ``favourites/partials/_favourite.html`` renders after create.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``favourites_eligible``, ``favourites_geojson_url``,
        ``favourite_create_url``, ``favourite_list_url``,
        ``favourite_rename_url_template``,
        ``favourite_delete_url_template``, and ``favourites_signin_url``.

    """
    favourites_eligible = request.user.is_authenticated
    # __UUID__ placeholder, mirroring the __ID__ trick used above for
    # edit_save_url_template — reverse with a dummy uuid, then string-
    # replace at runtime with the uuid of the pin actually selected.
    dummy_uuid = uuid.UUID(int=0)
    return {
        "favourites_eligible": favourites_eligible,
        "favourites_geojson_url": reverse("favourites:geojson"),
        "favourite_create_url": reverse("favourites:create"),
        # SNOW-658: the roundel opens a panel listing the user's own pins
        # before it offers to add one, so the panel needs the list endpoint.
        # ``?variant=map`` asks for the sheet's lean row template — same
        # rows and offline sidecar, without the manage page's in-page card
        # panel or its "view on the map" link.
        "favourite_list_url": (
            f"{reverse('favourites:list')}?variant={FAVOURITE_LIST_MAP_VARIANT}"
        ),
        "favourite_rename_url_template": reverse(
            "favourites:rename", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        "favourite_delete_url_template": reverse(
            "favourites:delete", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        "favourites_signin_url": reverse("accounts:sign_in"),
    }


def _routes_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the map's saved-routes panel (SNOW-686).

    ONE gate, since SNOW-724 retired the ``routes`` rollout flag that used
    to sit in front of it: ``routes_eligible`` is authentication, mirroring
    ``favourites_eligible``. routes.js branches on it to show the real list
    and upload control versus an anonymous sign-in CTA
    (tests/js/test_routes_panel_anonymous.js covers that branch), and the
    panel itself is now in the DOM for every visitor.

    No ``__UUID__``-templated delete URL, unlike ``_favourites_context``. A
    route row's Remove is a plain HTMX form rendered server-side into the
    row itself (routes/partials/_route_row_actions.html), so nothing
    client-side ever has to build that URL; only rename does, because its
    commit is a fetch from an inline editor.

    Args:
        request: The current HTTP request.

    SNOW-687 adds ``routes_geojson_url`` — the map layer's own data
    endpoint, rendered onto ``#map`` as ``data-routes-url`` for
    static/js/map.js to fetch. A ``reverse()``, not a query, so the
    homepage's query count is unmoved.

    Returns:
        Dict with ``routes_eligible``, ``route_create_url``,
        ``route_list_url``,
        ``route_rename_url_template``, ``routes_geojson_url`` and
        ``routes_signin_url``.

    """
    # __UUID__ placeholder, mirroring _favourites_context — reverse with a
    # dummy uuid, then string-replace at runtime with the uuid of the row
    # actually being renamed.
    dummy_uuid = uuid.UUID(int=0)
    return {
        "routes_eligible": request.user.is_authenticated,
        "route_create_url": reverse("routes:create"),
        # ``?variant=map`` asks for the sheet's lean row template — the
        # shared includes/_ugc_panel_row.html shape, rather than
        # _route.html's always-visible rename field.
        "route_list_url": f"{reverse('routes:list')}?variant={ROUTE_LIST_MAP_VARIANT}",
        "route_rename_url_template": reverse(
            "routes:rename", args=[dummy_uuid]
        ).replace(str(dummy_uuid), "__UUID__"),
        # SNOW-687: the map layer's data. Emitted only for an eligible user
        # (see the template) — the endpoint 403s for anyone else, and there
        # is nothing to draw.
        "routes_geojson_url": reverse("routes:geojson"),
        "routes_signin_url": reverse("accounts:sign_in"),
    }


def _downloads_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the offline-downloads affordances (SNOW-749).

    ``downloads_eligible`` is authentication, mirroring ``routes_eligible``
    and ``favourites_eligible``. It gates *starting* a download, not reading
    one: static/js/map_region_download.js and
    static/js/map_downloads_manager.js paint a sign-in state on their two
    start controls when it is false, and the controls stay visible either
    way — a hidden control reads as a bug, not as a gate. Nothing here gates
    the manage sheet, which must keep listing an already-downloaded area for
    a signed-out user, because working with no signal is the entire point of
    having downloaded it.

    ``__AREA_ID__`` templating, mirroring ``_favourites_context``'s
    ``__UUID__`` trick: reverse with a placeholder, then string-replace at
    runtime with the id of the area actually being acted on. The placeholder
    literal has to survive ``path("<str:area_id>/")``'s converter, so it is
    ``AREAID`` rather than a bracketed token — ``str`` accepts any non-empty
    run without a slash, and a bare alphanumeric run is the safest thing to
    round-trip through ``reverse()``.

    Areas are addressed by their client-minted ``area_id`` rather than by a
    server uuid because the client always has the area id to hand — it names
    the device's own Cache Storage bucket — while a uuid would have to be
    learnt from a response the mutation queue may have discarded long before
    the write replays.

    **The gate ships unconditionally — there is no rollout flag.** SNOW-749
    carried a ``download_sync`` waffle flag through review and dropped it
    before merge, on cost: the read took the homepage from 5 queries to 8,
    and waffle reads through Django's ``default`` cache, which in production
    is ``DatabaseCache`` against the ``django_cache`` table — so a warm
    cache traded three model queries for a cache-table one rather than for
    none. Three queries on the site's most-requested page, permanently, to
    hold a kill switch open on a gate nobody intended to close, was the
    wrong trade. What was given up is the admin toggle; reverting the
    feature is a deploy, as it is for every other capability here.

    That is why this helper reads no flag and costs no query: every value
    below is a ``reverse()``, and the homepage's count is unmoved.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``downloads_eligible``, ``download_sync_url``,
        ``download_areas_url``, ``download_rename_url_template``,
        ``download_forget_url_template`` and ``downloads_signin_url``.

    """
    # See the docstring: a plain alphanumeric placeholder, because the URL
    # pattern's <str:area_id> converter would not match a bracketed one.
    placeholder = "AREAID"
    return {
        "downloads_eligible": request.user.is_authenticated,
        "download_sync_url": reverse("downloads:sync"),
        "download_areas_url": reverse("downloads:areas"),
        "download_rename_url_template": reverse(
            "downloads:rename", args=[placeholder]
        ).replace(placeholder, "__AREA_ID__"),
        "download_forget_url_template": reverse(
            "downloads:forget", args=[placeholder]
        ).replace(placeholder, "__AREA_ID__"),
        "downloads_signin_url": reverse("accounts:sign_in"),
    }


def _community_reports_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the community-reports map overlay.

    Unlike favourites, there is no per-user eligibility split: the overlay
    shows anonymised, publicly-shared data, so every request sees the
    toggle.

    Args:
        request: The current HTTP request.

    Returns:
        Dict with ``community_reports_geojson_url``.

    """
    return {"community_reports_geojson_url": reverse("api:community_reports_geojson")}


def _weather_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the map Weather overlay (SNOW-573).

    No eligibility split any more: SNOW-724 retired the ``weather_layer``
    rollout flag, so the toggle and the fetch it drives are in the DOM for
    every visitor, exactly like community reports. The ``request``
    argument is kept for signature uniformity with the sibling
    ``_*_context`` builders the map view composes.

    Args:
        request: The current HTTP request.

    SNOW-698 added the second URL: the overlay is one toggle over two
    tiers — resort symbols above zoom 8, micro-region-centroid symbols
    below it — and each tier has its own endpoint. Both are bare
    ``reverse()`` calls, so this builder still issues no query.

    Returns:
        Dict with ``forecast_weather_geojson_url`` and
        ``region_weather_geojson_url``.

    """
    return {
        "forecast_weather_geojson_url": reverse("api:forecast_weather_geojson"),
        "region_weather_geojson_url": reverse("api:region_weather_geojson"),
    }


def _slope_context(request: HttpRequest) -> dict[str, Any]:
    """Build the template context dict for the map slope-angle overlay (SNOW-691).

    Eligibility is ``settings.SLOPE_TILE_URL`` being configured, not
    authentication and — since SNOW-724 — no longer a waffle flag either.
    Unlike Weather, there is no Snowdesk endpoint behind this layer: the
    tiles come straight from a third-party WMTS origin whose licence
    position is still open, so the setting doubles as the operator kill
    switch the flag used to provide. Clearing ``SLOPE_TILE_URL`` in the
    environment is a restart, not a deploy, and it takes the whole layer
    out of the DOM — the row must be absent entirely, not merely disabled,
    and a tile template in an ineligible page's DOM would be an invitation
    to install a layer we just withdrew.

    Args:
        request: The current HTTP request. Unused — kept for signature
            uniformity with the sibling ``_*_context`` builders.

    Returns:
        Dict with ``slope_layer_eligible`` and ``slope_tile_url`` (empty
        string while ineligible).

    """
    eligible = bool(settings.SLOPE_TILE_URL)
    return {
        "slope_layer_eligible": eligible,
        "slope_tile_url": settings.SLOPE_TILE_URL if eligible else "",
    }


def _labelled_counts(raw: "dict[str, int]") -> "list[tuple[str, int]]":
    """Map raw ``OBSERVATION_TYPE`` counts to sorted, human-readable pairs.

    Shared by ``_get_observation_counts`` (region-wide) and
    ``_get_local_observation_counts`` (SNOW-508, point-local) so the
    key→label mapping lives in exactly one place.

    Args:
        raw: Mapping from ``OBSERVATION_TYPE`` value string to count, as
            returned by ``counts_for_region_day`` / ``counts_near_point_for_day``.

    Returns:
        List of ``(label, count)`` pairs sorted by label, where ``label`` is
        the human-readable ``OBSERVATION_TYPE`` label (e.g. "Wind striations").
        An unrecognised key falls back to a title-cased rendering rather than
        raising, so a future observation type doesn't break the page.

    """
    result: list[tuple[str, int]] = []
    for key, count in raw.items():
        try:
            label = FieldObservation.OBSERVATION_TYPE(key).label
        except ValueError:
            label = key.replace("_", " ").title()
        result.append((label, count))
    result.sort(key=lambda pair: pair[0])
    return result


def _get_observation_counts(
    request: HttpRequest,
    region: "MicroRegion",
    day: datetime.date,
) -> "list[tuple[str, int]]":
    """Return per-type field-observation counts for a region on a calendar day.

    Args:
        request: The current HTTP request.
        region: The MicroRegion to count observations for.
        day: The calendar day to count observations on.

    Returns:
        List of ``(label, count)`` pairs sorted by label, where ``label`` is
        the human-readable ``OBSERVATION_TYPE`` label (e.g. "Wind striations").
        Returns an empty list when no observations exist.

    """
    from apps.observations.models import FieldObservation  # noqa: PLC0415

    raw: dict[str, int] = FieldObservation.objects.counts_for_region_day(region, day)
    return _labelled_counts(raw)


@dataclasses.dataclass(frozen=True)
class LocalObservationResult:
    """Structured result for a distance-scoped field-observation lookup.

    Distinguishes "checked, nothing nearby" (``visible=True``, empty
    ``counts``) from "feature off" (``visible=False``) so the template can
    render the correct empty-state copy rather than silently omitting the
    panel. ``scope`` tells the template which heading/empty-state copy to
    use: ``"point"`` when coordinates were available for a point-local
    query, ``"region"`` when falling back to the region-wide count.

    """

    visible: bool
    scope: str
    counts: "list[tuple[str, int]]"


def _get_local_observation_counts(
    request: HttpRequest,
    resort: "Resort",
    day: datetime.date,
) -> LocalObservationResult:
    """Return a distance-scoped field-observation result for a resort page.

    Point-local when the resort has both coordinates (SNOW-508); falls back
    to the existing region-wide count (``counts_for_region_day``) when the
    resort's coordinates are null.

    Args:
        request: The current HTTP request.
        resort: The Resort to look up observations near.
        day: The calendar day to count observations on.

    Returns:
        A ``LocalObservationResult`` — see its docstring for field meanings.

    """
    if resort.latitude is not None and resort.longitude is not None:
        raw = FieldObservation.objects.counts_near_point_for_day(
            resort.latitude,
            resort.longitude,
            settings.FIELD_OBSERVATION_RADIUS_KM,
            day,
        )
        return LocalObservationResult(
            visible=True, scope="point", counts=_labelled_counts(raw)
        )

    raw = FieldObservation.objects.counts_for_region_day(resort.region, day)
    return LocalObservationResult(
        visible=True, scope="region", counts=_labelled_counts(raw)
    )


def _get_observation_has_user_located(
    request: HttpRequest,
    region: "MicroRegion",
    day: datetime.date,
) -> bool:
    """Return True if any user-located report exists for region on day.

    Guarded behind ``is_today`` at the calling site (only invoked on
    today's bulletin page).

    Args:
        request: The current HTTP request.
        region: The MicroRegion to check.
        day: The calendar day to check.

    Returns:
        True when at least one MANUAL or GPS_REFINED report exists for the
        region on the given day.

    """
    from apps.observations.models import FieldObservation  # noqa: PLC0415

    return FieldObservation.objects.user_located_exists_for_region_day(region, day)


def _get_favourites_in_region(
    request: HttpRequest, region: "MicroRegion"
) -> list[Favourite]:
    """Return the requesting user's own favourites resolved to a region (SNOW-507).

    Guarded on ``request.user.is_authenticated`` so anonymous requests issue
    zero extra queries — mirrors ``user_subscribed_to_region``'s per-user
    pattern on the bulletin page.

    Args:
        request: The current HTTP request.
        region: The MicroRegion to resolve favourites for.

    Returns:
        The user's favourites in this region, or an empty list when
        anonymous.

    """
    if not request.user.is_authenticated:
        return []
    return list(Favourite.objects.for_user_region(request.user, region))


def _serve_sw_file(static_relative_path: str) -> HttpResponse:
    """Read a service-worker script off disk and wrap it in SW-required headers.

    Shared helper for ``serve_sw`` (the real PWA shell SW at ``/sw.js``)
    and ``serve_sw_kill`` (the kill-switch SW at ``/sw-kill.js``, SNOW-373).
    Both need identical response headers — the only difference is which
    file they read.

    Args:
        static_relative_path: Path relative to the ``static/`` root, e.g.
            ``"js/sw.js"`` or ``"js/sw-kill.js"``.

    Returns:
        An ``HttpResponse`` with the SW body, ``Service-Worker-Allowed: /``,
        and ``Cache-Control: no-cache``.

    Raises:
        Http404: If the requested file isn't found by the staticfiles
            finders.

    """
    path = finders.find(static_relative_path)
    if path is None:
        raise Http404("Service worker script not found.")
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def _sw_conditional(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    """Stamp an ``ETag`` on a service-worker response and honour revalidation.

    SNOW-622: ``Cache-Control: no-cache`` means the browser revalidates on
    every page load, which is the contract these workers need — but without
    a validator there is nothing to revalidate *against*, so every load
    re-downloaded the whole script. ``sw.js`` is ~2,000 lines. An ``ETag``
    turns the unchanged case into a bodyless 304 while keeping the
    revalidation the workers depend on.

    The tag is computed over the FINAL body, after ``serve_sw``'s
    cache-version and dev-bypass substitutions, so it changes exactly when
    what the browser would receive changes — which is also what makes the
    worker detect an update.

    Args:
        request: The incoming request, read for ``If-None-Match``.
        response: The fully-substituted service-worker response.

    Returns:
        ``response`` with an ``ETag``, or a bodyless 304 when the client
        already holds that exact body.

    """
    etag = quote_etag(hashlib.sha256(response.content).hexdigest())
    response["ETag"] = etag
    if request.headers.get("If-None-Match") == etag:
        not_modified = HttpResponseNotModified()
        # A 304 carries no body, but must repeat the headers that govern
        # how the cached copy may be used — a worker re-served from cache
        # still needs its root scope and its revalidate-every-time rule.
        for header in ("Service-Worker-Allowed", "Cache-Control", "ETag"):
            not_modified[header] = response[header]
        return not_modified
    return response


def serve_sw(request: HttpRequest) -> HttpResponse:
    """
    Serve the service worker script from the root URL path (``/sw.js``).

    Service workers control the scope they are served from. Serving from
    ``/sw.js`` lets the SW control ``/`` (the whole site). The
    ``Service-Worker-Allowed`` header makes that scope explicit, and
    ``Cache-Control: no-cache`` ensures the browser re-validates on every
    page load so SW updates take effect promptly.

    SNOW-590: the ``CACHE_VERSION`` literal on disk is a placeholder, and
    is rewritten here with a name derived from the shell content hash. That
    is what replaced the hand-bumped constant — see ``apps.core.sw_shell``.
    Unlike the bypass substitution below, this one is **required**: a body
    with no substitutable assignment raises, because serving it unchanged
    would pin every client to one frozen cache name and stop shell updates
    reaching anyone (the SNOW-457 regression). Under ``DEBUG`` the version
    is recomputed per request, since the autoreloader only restarts on
    ``.py`` edits and would otherwise serve a stale name after a ``.js`` /
    ``.css`` / template change.

    SNOW-585: when ``settings.SW_DEV_SHELL_BYPASS`` is on, the on-disk
    ``const DEV_SHELL_BYPASS = false;`` literal is rewritten to ``true`` in
    the response body returned by ``_serve_sw_file`` — deliberately done
    here rather than inside that shared helper, so ``/sw-kill.js`` (served
    by ``serve_sw_kill``, which never runs this substitution) can't be
    affected by this dev-only concern even structurally, not merely by
    convention. A key not found in the body is a silent no-op — the
    on-disk default (``false``) is production-safe, so a failed
    substitution fails safe rather than raising.

    Args:
        request: The incoming HTTP request.

    Returns:
        An ``HttpResponse`` with the SW script body and the required
        ``Service-Worker-Allowed`` / ``Cache-Control`` headers.

    Raises:
        Http404: If ``js/sw.js`` is not found by staticfiles finders.
        ValueError: If the body carries no substitutable ``CACHE_VERSION``
            assignment. ``apps.core.checks`` catches this at
            ``manage.py check`` time so it cannot first appear in
            production.

    """
    response = _serve_sw_file("js/sw.js")
    body = response.content.decode("utf-8")

    version = cache_version() if settings.DEBUG else cached_cache_version()
    body = inject_cache_version(body, version=version)

    if settings.SW_DEV_SHELL_BYPASS:
        body = body.replace(
            "const DEV_SHELL_BYPASS = false;",
            "const DEV_SHELL_BYPASS = true;",
        )
    response.content = body.encode("utf-8")
    return _sw_conditional(request, response)


def serve_sw_kill(request: HttpRequest) -> HttpResponse:
    """
    Serve the kill-switch service worker at ``/sw-kill.js`` (SNOW-373).

    Mechanism B of the two-mechanism kill switch (spec §6.3, §6.4). Ops
    activates it by pointing ``SW_URL=/sw-kill.js`` in Render env — the
    ``sw_register.js`` config gate then registers this file instead of
    the real ``/sw.js``, and on activate it wipes caches + IndexedDB and
    unregisters itself. See ``static/js/sw-kill.js`` for the full
    behaviour.

    Same header contract as ``serve_sw`` — root scope, no-cache — because
    once a client is on this SW it must revalidate on every visit so a
    subsequent config flip back to the real ``/sw.js`` picks up promptly.

    Args:
        request: The incoming HTTP request.

    Returns:
        An ``HttpResponse`` with the kill-switch SW script body and the
        required headers.

    Raises:
        Http404: If ``js/sw-kill.js`` is not found by staticfiles finders.

    """
    return _sw_conditional(request, _serve_sw_file("js/sw-kill.js"))


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

    SNOW-399: the manifest ``name`` / ``short_name`` / ``theme_color`` and
    the icon ``src`` prefix all key off
    ``apps.public.site_environment.PWAEnvironmentIdentity.from_settings()`` so
    a staging install lands on the home screen as ``Snowdesk (Staging)``
    with an amber icon set — visibly distinct from the production tile.
    Screenshots stay under ``/static/icons/pwa/screenshots/`` on every
    environment because they show the actual UI, which is identical.

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
    identity = PWAEnvironmentIdentity.from_settings()
    icon_dir = identity.icon_dir.rstrip("/")
    manifest = {
        "name": identity.name_display,
        "short_name": identity.short_name,
        "id": f"{base}/",
        "lang": "en",
        "description": "Daily Swiss avalanche bulletins for the alpine region.",
        "categories": ["weather", "sports", "travel"],
        "start_url": f"{base}/",
        "scope": f"{base}/",
        "display": "standalone",
        "background_color": "#f4f1e8",
        "theme_color": identity.theme_color,
        "icons": [
            {
                "src": f"{icon_dir}/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": f"{icon_dir}/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": f"{icon_dir}/icon-maskable-512.png",
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


def serve_robots(request: HttpRequest) -> HttpResponse:
    """
    Serve ``/robots.txt`` from the site root.

    Snowdesk publishes public-good avalanche-safety data, so the policy is
    open by default (``Allow: /``) — crawlers and AI agents are welcome to
    index the bulletin pages. Only functional, non-content, or token-bearing
    paths are disallowed: the Django admin, the signed-token subscription
    flow (whose links perform account actions), the staff-only resort-edit
    API, the HTMX partial fragments (which 400 on a plain GET anyway), the
    ephemeral share-redirect tokens, the CSP report endpoint, and the
    ``/livez`` + ``/healthz`` infrastructure probes (SNOW-565). The public
    GeoJSON / ratings endpoints under ``/api/`` stay crawlable on purpose so
    agents can find the structured data.

    The ``Sitemap:`` line is an absolute URL built from
    ``settings.SITE_BASE_URL`` (matching ``serve_manifest``) so it resolves
    to the correct host per environment. A short ``Cache-Control`` keeps the
    body fresh after a ``SITE_BASE_URL`` change without re-rendering on every
    crawl.

    Args:
        request: The incoming HTTP request (unused — the body is origin-keyed
            via ``SITE_BASE_URL``, not per-request).

    Returns:
        An ``HttpResponse`` with the ``text/plain`` robots body.

    """
    base = settings.SITE_BASE_URL.rstrip("/")
    lines = [
        "# Snowdesk — daily Swiss avalanche bulletins (SLF CAAML data).",
        "# Public-good safety information: crawlers and AI agents are welcome.",
        f"# Machine-readable site index: {base}/llms.txt",
        "",
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /account/",
        # Legacy prefix — 301-redirects to /account/, but disallow it too so
        # thin crawlers don't fetch it before following the redirect (SNOW-430).
        "Disallow: /subscribe/",
        "Disallow: /api/edit/",
        "Disallow: /partials/",
        "Disallow: /s/",
        "Disallow: /csp/",
        # Infrastructure probes, not content (SNOW-565).
        "Disallow: /livez",
        "Disallow: /healthz",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ]
    response = HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300"
    return response


def serve_llms_txt(request: HttpRequest) -> HttpResponse:
    """
    Serve ``/llms.txt`` — a machine-readable Markdown index of the site.

    Follows the llmstxt.org convention: an H1 with the project name, a
    blockquote summary, then ``##`` sections listing the canonical pages and
    public JSON endpoints as ``[title](url): note`` links. This gives an LLM
    or agent a single low-token entry point describing what the site offers
    and where the structured data lives — complementing the page-level
    schema.org JSON-LD and the XML sitemap.

    All links are absolute URLs built from ``settings.SITE_BASE_URL`` (paths
    resolved via ``reverse()`` so they survive route changes), matching
    ``serve_manifest`` and ``serve_robots``.

    Args:
        request: The incoming HTTP request (unused — the body is origin-keyed
            via ``SITE_BASE_URL``, not per-request).

    Returns:
        An ``HttpResponse`` with the ``text/markdown`` llms.txt body.

    """
    base = settings.SITE_BASE_URL.rstrip("/")

    def link(name: str) -> str:
        """Build an absolute URL for a named route under ``SITE_BASE_URL``."""
        return f"{base}{reverse(name)}"

    def bulletin_link(region_id: str, slug: str) -> str:
        """Build the evergreen bulletin URL for a region under ``SITE_BASE_URL``."""
        path = reverse(
            "public:bulletin",
            kwargs={"region_id": region_id, "slug": slug},
        )
        return f"{base}{path}"

    # Built line-by-line (rather than one triple-quoted block) so each source
    # line stays within the 88-char limit while the rendered Markdown bullets
    # remain single, unwrapped lines.
    lines = [
        "# Snowdesk",
        "",
        "> Daily avalanche bulletins for the Alps — Switzerland (SLF / WSL",
        "> Institute for Snow and Avalanche Research), Austria + South Tyrol +",
        "> Trentino (ALBINA / EUREGIO avalanche.report), and France",
        "> (Météo-France). All providers normalised to CAAML v6 and rendered",
        "> per micro-region with danger ratings, avalanche problems, and",
        "> weather context. Bulletin pages carry schema.org JSON-LD; the",
        "> underlying data is licensed by the issuing warning services (SLF",
        "> CC BY 4.0; others per their own terms).",
        "",
        "## Pages",
        "",
        f"- [Avalanche map]({link('public:home')}): interactive choropleth of "
        "current danger ratings by region; also the site entry point.",
        f"- [How to read a bulletin]({link('public:how_to_read_bulletin')}): "
        "reference guide to the EAWS danger scale, avalanche problems, and "
        "the aspect/elevation rose.",
        f"- [Example bulletin]({link('public:examples_random')}): a randomly "
        "selected bulletin rendered in the canonical layout.",
        "",
        "## Regions",
        "",
        "Snowdesk covers Alpine micro-regions across all four EAWS warning "
        "zones. Each link below is an evergreen URL — it always renders "
        "today's bulletin for the region.",
        "",
        f"- [Switzerland — Martigny / Verbier]"
        f"({bulletin_link('ch-4115', 'martigny-verbier')}): "
        "sample SLF bulletin, Valais.",
        f"- [Austria — Silvretta Ost]"
        f"({bulletin_link('at-07-12', 'silvretta-ost')}): "
        "sample ALBINA bulletin, Tirol.",
        f"- [Italy — Dolomiti di Gardena]"
        f"({bulletin_link('it-32-bz-18', 'dolomiti-di-gardena')}): "
        "sample ALBINA / EUREGIO bulletin, South Tyrol.",
        f"- [France — Mont-Blanc]"
        f"({bulletin_link('fr-03', 'mont-blanc')}): "
        "sample Météo-France bulletin, French Alps.",
        "",
        "## Data",
        "",
        f"- [Sitemap]({link('sitemap')}): XML sitemap of today's published "
        "region bulletins.",
        f"- [Full URL index]({link('llms_full_txt')}): every micro-region's "
        "evergreen bulletin URL, sorted by region_id — the low-token full "
        "index paired with this file (SNOW-393).",
        f"- [Switzerland — RSS feed]({base}/ch/feed.rss): latest SLF "
        "bulletins per micro-region (SNOW-396).",
        f"- [Austria — RSS feed]({base}/at/feed.rss): latest ALBINA "
        "bulletins per Austrian micro-region.",
        f"- [Italy — RSS feed]({base}/it/feed.rss): latest ALBINA / EUREGIO "
        "bulletins per Italian micro-region.",
        f"- [France — RSS feed]({base}/fr/feed.rss): latest Météo-France "
        "bulletins per massif.",
        f"- [Region ratings (JSON)]({link('api:ratings')}): current danger "
        "ratings; accepts ?d=YYYY-MM-DD and ?country=ch|fr|at|it.",
        f"- [Regions (GeoJSON)]({link('api:regions_geojson')}): micro-region "
        "boundary geometry.",
        f"- [Resorts (GeoJSON)]({link('api:resorts_geojson')}): ski-resort "
        "point locations.",
        "",
        "## MCP server",
        "",
        f"- [MCP JSON-RPC endpoint]({link('api:mcp:endpoint')}): POST-only "
        "Model Context Protocol server (JSON-RPC 2.0) with tools to search "
        "regions and resorts, read current conditions, query danger-rating "
        "history, and list resorts in a region.",
        "",
        "## Legal",
        "",
        f"- [Terms & data licences]({link('public:terms')}): source-service "
        "attribution and Snowdesk liability disclaimer.",
        f"- [Privacy]({link('public:privacy')}): how subscriber data is handled.",
        f"- [Terms of service]({link('public:terms_of_service')}): conditions of use.",
        f"- [Colophon]({link('public:colophon')}): technology credits and attribution.",
        "",
    ]
    body = "\n".join(lines)
    response = HttpResponse(body, content_type="text/markdown; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300"
    return response


def serve_llms_full_txt(request: HttpRequest) -> HttpResponse:
    """
    Serve ``/llms-full.txt`` — the full URL index paired with ``/llms.txt``.

    Follows the llmstxt.org convention: pair the short summary
    (``/llms.txt``) with a complete machine-readable listing at
    ``/llms-full.txt``. Each line is one Markdown link — the region's
    evergreen ``/<region_id>/<slug>/`` URL — with a country and
    major-region description, sorted by ``region_id`` for stable
    diffs.

    The queryset joins ``subregion__major`` so country and major-region
    labels are available without an N+1 query per row. A one-hour
    ``Cache-Control`` is applied because the micro-region set only
    changes on fixture ingest, not per-bulletin.

    Args:
        request: The incoming HTTP request (unused — the body is
            origin-keyed via ``SITE_BASE_URL``, not per-request).

    Returns:
        An ``HttpResponse`` with the ``text/markdown`` llms-full.txt body.

    """
    # Function-local import to break a apps.public.views ↔ apps.public.api circular
    # (apps.public.api imports _resolve_region_for_bulletin from this module).
    from apps.public.api import COUNTRY_NAMES

    base = settings.SITE_BASE_URL.rstrip("/")
    lines = [
        "# Snowdesk — full URL index",
        "",
        "> Full machine-readable index paired with /llms.txt. One line per",
        "> Alpine micro-region (SLF / ALBINA / Météo-France coverage), sorted",
        "> by region_id. Each URL is the evergreen form-2 route that always",
        "> renders today's bulletin.",
        "",
        "## Regions",
        "",
    ]
    regions = MicroRegion.objects.select_related("subregion__major").order_by(
        "region_id"
    )
    for region in regions:
        url = f"{base}{region.get_absolute_url()}"
        country_code = region.region_id[:2].upper()
        country_name = COUNTRY_NAMES.get(country_code, country_code)
        major = region.subregion.major if region.subregion_id else None
        major_name = (major.name_en or major.name_native) if major else "—"
        lines.append(f"- [{region.name}]({url}): {country_name} · {major_name}")
    lines.append("")
    body = "\n".join(lines)
    response = HttpResponse(body, content_type="text/markdown; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def serve_favicon(request: HttpRequest) -> HttpResponseRedirect:
    """
    Serve ``/favicon.ico`` by redirecting to the canonical SVG favicon.

    Tools and crawlers (Lighthouse, browser prefetch, social-media
    scrapers) request ``/favicon.ico`` unconditionally from the site
    root. Snowdesk ships only SVG favicons (``favicon.svg`` plus
    per-danger-level variants), so the legacy ``.ico`` path is wired up
    as a 302 redirect to the staticfiles URL — eliminating the 404 from
    server logs without generating an ``.ico`` binary.

    The redirect carries ``Cache-Control: public, max-age=86400`` so that
    CDNs and browsers cache it for one day — the SVG target is
    content-hashed by staticfiles, so a one-day TTL is safe and avoids
    an extra round-trip on every page load.
    """
    response = HttpResponseRedirect(static("favicon.svg"))
    response["Cache-Control"] = "public, max-age=86400"
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
# All three forms first pass through the ``@lowercase_region_id`` decorator,
# which 301-redirects any mixed-case ``region_id`` (e.g. ``/CH-4115/``) to
# the canonical lowercase form (``/ch-4115/``). Once the region_id is
# lowercase, forms 1 and 2 render today's bulletin in place — they do NOT
# redirect further. Only form 3 with additionally non-canonical components
# (e.g. a stale ``ch_4124``-style slug) redirects to the canonical form-3
# URL. The page always advertises the canonical form-3 URL via
# ``<link rel="canonical">`` regardless of which form the user landed on.
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

# SLF grades a rating within its level: "plus" sits at the top of the band,
# "minus" at the bottom, "neutral" in the middle. Until SNOW-727 this reached
# the reader only as a suffix glyph on two surfaces — the problem card's
# level-number chip, and the day-window tile, which is aria-hidden. Removing
# the chip left the tile, so the subdivision is now said in words on the Day
# Risk Profile row: legible, translatable, and reachable by a screen reader
# for the first time. "neutral" stays silent, as it always has — the middle of
# the band is the unremarkable case.
_SUBDIVISION_LABELS: dict[str, Promise] = {
    "-": _("lower end of the band"),
    "+": _("upper end of the band"),
}


def _parse_danger_rating(rating: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(period, main_value, subdivision)`` for a CAAML dangerRating dict.

    This function reads the raw CAAML shape (with ``customData.CH.subdivision``).
    It is kept for use by ``_danger_rank`` and ``_max_rating_per_period`` which
    are used by the ``build_problem_cards`` path. New code should use
    ``_rm_danger_rank`` and ``_max_rm_rating_per_period`` which operate on the
    projected ``danger.ratings`` entries.
    """
    period = rating.get("validTimePeriod") or "all_day"
    level = rating.get("mainValue") or ""
    raw_sub = (rating.get("customData") or {}).get("CH", {}).get("subdivision", "")
    return period, level, raw_sub


def _danger_rank(level: str, sub: str) -> tuple[int, int]:
    """Return a sortable rank for a danger level + raw-token subdivision pair.

    Band index from ``_DANGER_ORDER`` is the primary key; the raw ``customData.CH``
    subdivision token maps to an integer offset (minus → -1, neutral/absent → 0,
    plus → +1). Tuple comparison gives the correct total ordering:
    ``(2, -1) < (2, 0) < (2, 1) < (3, -1)``.

    For projected subdivision display chars (``+``/``-``/``=``), use
    :func:`_rm_danger_rank` instead.
    """
    band = _DANGER_ORDER.index(level)
    offset = {"minus": -1, "neutral": 0, "plus": 1}.get(sub, 0)
    return (band, offset)


def _rm_danger_rank(level: str, sub: str) -> tuple[int, int]:
    """Return a sortable rank for a danger level + projected subdivision display char.

    Mirrors :func:`_danger_rank` but accepts projected subdivision display chars
    (``"+"``, ``"-"``, ``"="``, or ``""``) from ``danger.ratings[*].subdivision``.
    """
    band = _DANGER_ORDER.index(level) if level in _DANGER_ORDER else 0
    offset = {"+": 1, "=": 0, "-": -1}.get(sub, 0)
    return (band, offset)


def _max_rating_per_period(
    ratings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group ``dangerRatings`` by ``validTimePeriod``, keeping the highest-rank.

    For each period, picks the rating with the highest (band, subdivision)
    rank — so elevation-split ratings within a single period are collapsed
    to the more dangerous of the two. Ratings with an unknown ``mainValue``
    are skipped.

    Operates on raw CAAML ``dangerRatings`` dicts. See
    :func:`_max_rm_rating_per_period` for the projected variant.
    """
    by_period: dict[str, dict[str, Any]] = {}
    for r in ratings:
        period, level, sub = _parse_danger_rating(r)
        if level not in _DANGER_ORDER:
            continue
        incumbent = by_period.get(period)
        if incumbent is None:
            by_period[period] = r
            continue
        _, inc_level, inc_sub = _parse_danger_rating(incumbent)
        if _danger_rank(level, sub) > _danger_rank(inc_level, inc_sub):
            by_period[period] = r
    return by_period


def _group_rm_ratings_by_period(
    rm_ratings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group projected ``danger.ratings`` by ``period``, preserving every entry.

    Unlike the older max-collapse, this keeps every rating in a period so
    callers can emit per-elevation-band rows when ALBINA splits a period's
    danger by elevation. Ratings whose ``key`` isn't a canonical EAWS level
    are dropped (defensive — the projection should not emit them).

    Args:
        rm_ratings: The ``danger.ratings`` list from the render model.

    Returns:
        Dict mapping ``period`` string to the ordered list of projected
        rating entries for that period (source order preserved).

    """
    by_period: dict[str, list[dict[str, Any]]] = {}
    for r in rm_ratings:
        period: str = r.get("period") or "all_day"
        level: str = r.get("key") or ""
        if level not in _DANGER_ORDER:
            continue
        by_period.setdefault(period, []).append(r)
    return by_period


def _max_rm_rank_in(ratings: list[dict[str, Any]]) -> tuple[int, int]:
    """Return the highest ``_rm_danger_rank`` across the given ratings."""
    return max(
        (
            _rm_danger_rank(r.get("key") or "", r.get("subdivision") or "")
            for r in ratings
        ),
        default=(0, 0),
    )


def _rm_caaml_elevation(rm_elev: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reconstruct CAAML lower/upper bounds from a projected elevation dict.

    The render model's projected elevation keeps only the numeric bound on
    each side plus a ``treeline_side`` marker recording which bound the
    ``"treeline"`` token originally lived on (recorded by ``_parse_elevation``).
    This helper rebuilds the CAAML-shaped ``{"lowerBound", "upperBound"}`` dict
    — putting ``"treeline"`` back on the correct side — so the shared
    :func:`_format_elevation` machinery can derive both the caption wording and
    the ``bound_type`` used for icon selection. Returns ``None`` when no bounds
    survive (e.g. both bounds absent, or older render models predating the
    ``treeline_side`` field).
    """
    if not rm_elev:
        return None
    caaml_lower: Any = rm_elev.get("lower")
    caaml_upper: Any = rm_elev.get("upper")
    treeline_side = rm_elev.get("treeline_side")
    if treeline_side == "lower":
        caaml_lower = "treeline"
    elif treeline_side == "upper":
        caaml_upper = "treeline"
    return {"lowerBound": caaml_lower, "upperBound": caaml_upper}


def _rm_elevation_bounds(rm_elev: dict[str, Any] | None) -> ElevationBounds:
    """Build :class:`ElevationBounds` for a projected elevation dict.

    Reconstructs the CAAML bounds via :func:`_rm_caaml_elevation` then defers
    to :func:`_format_elevation`, so the caption wording matches the
    problem-card aspect/elevation row. The returned object also carries a
    ``bound_type`` (``LOWER`` = "above", ``UPPER`` = "below", ``BOTH``), which
    the day-windows panel feeds to the ``elevation_icon`` filter to pick the
    mountain glyph for a banded row. Empty (falsey) when no bounds survive.
    """
    return _format_elevation(_rm_caaml_elevation(rm_elev))


def _rm_elevation_caption(rm_elev: dict[str, Any] | None) -> str:
    """Render a projected elevation dict as a short human caption.

    Thin wrapper over :func:`_rm_elevation_bounds` returning just the formatted
    display string (``"below 2400m"``, ``"above treeline"`` …). Returns ``""``
    when no bounds survive.
    """
    return _rm_elevation_bounds(rm_elev).display


def _elevation_sort_key(rating: dict[str, Any]) -> tuple[int, int]:
    """Sort projected ratings within a period so the lower band comes first.

    ALBINA always pairs an ``upperBound="X"`` rating ("below X" — the lower
    band) with a ``lowerBound="X"`` rating ("above X" — the upper band) at the
    same pivot. After projection, only the numeric bound is preserved on each
    side; for the treeline pivot the two ratings collapse to identical
    numeric bounds and are distinguished by ``treeline_side``. The sort key
    orders strictly: below-treeline < below-X < above-X < above-treeline.
    """
    elev = rating.get("elevation") or {}
    lower = elev.get("lower")
    upper = elev.get("upper")
    treeline_side = elev.get("treeline_side")
    # Treeline pivot: when "treeline" was the upperBound, the rating means
    # "below treeline" (the lower band). When it was the lowerBound, "above
    # treeline" (the upper band).
    if treeline_side == "upper":
        return (0, 0)
    if treeline_side == "lower":
        return (3, 0)
    # Numeric pivot: upperBound-only → "below X" (lower band); lowerBound-only
    # → "above X" (upper band). Use the bound value to keep adjacent pivots
    # ordered relative to each other for the rare case where a single period
    # mixes pivots.
    if lower is None and upper is not None:
        return (1, upper)
    if lower is not None:
        return (2, lower)
    return (4, 0)


def _day_window_row_from_rm(rm_rating: dict[str, Any]) -> dict[str, Any]:
    """Build one day-window row dict from a projected ``danger.ratings`` entry.

    Args:
        rm_rating: One entry from ``render_model["danger"]["ratings"]``.

    Returns:
        A window row dict consumed by the day-windows panel partial. The
        ``caption`` slot defaults to ``""`` and is populated by
        :func:`_rows_for_period` only when a period emits multiple
        elevation-band rows.

    """
    period: str = rm_rating.get("period") or "all_day"
    level: str = rm_rating.get("key") or "low"
    if level not in _DANGER_PANEL_META:
        level = "low"
    suffix: str = rm_rating.get("subdivision") or ""
    number = _DANGER_PANEL_META[level]["number"]
    return {
        "type": period,
        "level_key": level,
        "level_css": level.replace("_", "-"),
        "level_label": _DANGER_PANEL_META[level]["label"],
        "level_number": f"{number}{suffix}",
        "subdivision_label": _SUBDIVISION_LABELS.get(suffix, ""),
        "caption": "",
        "pill_label": _DAY_WINDOW_PILL_LABELS.get(period, period),
    }


def _rows_for_period(
    period_ratings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Emit one or more day-window rows for a single time period.

    When every rating in the period resolves to the same (key, subdivision),
    a single row is emitted with no caption — the elevation split is
    informational only and would be noise. Otherwise one row is emitted per
    rating, ordered low-elevation-band first, each carrying an elevation
    caption (``"below 2200 m"``, ``"above treeline"`` …) so the user can see
    which band each level applies to. This is the path ALBINA takes for
    periods where the danger genuinely differs by elevation; SLF never splits
    danger by elevation so it always falls into the single-row branch.

    Suppression rule: when a period mixes banded ratings (those carrying a
    truthy ``elevation``) with unbanded ones (no ``elevation``), the unbanded
    ratings are discarded before any further processing. The banded pair
    already partitions the whole mountain (e.g. "below 2400 m" + "above
    2400 m"), so the extra unbanded entry is logically redundant and
    unplaceable on the elevation axis. When the period contains ONLY unbanded
    ratings (SLF all_day; constant-danger ALBINA) they are kept unchanged.
    """
    if not period_ratings:
        return []
    has_banded = any(r.get("elevation") for r in period_ratings)
    has_unbanded = any(not r.get("elevation") for r in period_ratings)
    if has_banded and has_unbanded:
        period_ratings = [r for r in period_ratings if r.get("elevation")]
    distinct = {
        (r.get("key") or "", r.get("subdivision") or "") for r in period_ratings
    }
    if len(distinct) == 1:
        # All band ratings agree — pick any (they encode the same level) and
        # drop the elevation caption to avoid spurious differentiation.
        return [_day_window_row_from_rm(period_ratings[0])]
    rows: list[dict[str, Any]] = []
    for r in sorted(period_ratings, key=_elevation_sort_key):
        row = _day_window_row_from_rm(r)
        bounds = _rm_elevation_bounds(r.get("elevation"))
        row["caption"] = bounds.display
        # The mountain glyph beside the level tile is driven by bound_type;
        # only banded rows carry it (single SLF rows leave caption empty).
        row["elevation_bounds"] = bounds
        rows.append(row)
    return rows


def _day_windows_from_rm_ratings(
    rm_ratings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Build day-window rows from the projected danger.ratings list.

    SLF style: emits ``all_day`` rows and, when a ``later`` period is also
    present, always appends it — regardless of whether the afternoon level
    equals, exceeds, or is lower than the morning level. This covers
    flat-but-split days (same level AM/PM, different problem mix) as well as
    escalating and de-escalating cases. ALBINA style: emits one row per period
    (earlier → later) when no ``all_day`` entry is present.

    For ALBINA specifically, a single period often carries two ratings split
    by elevation band (one for each of "below X" and "above X" — sometimes
    differing in danger level). When the two band ratings disagree, both
    rows are emitted with elevation captions; when they agree, the row
    collapses to one with no caption. SLF is never affected because it
    publishes one rating per period.
    """
    by_period = _group_rm_ratings_by_period(rm_ratings)
    all_day_list = by_period.get("all_day") or []
    if all_day_list:
        rows = _rows_for_period(all_day_list)
        later_list = by_period.get("later") or []
        # Always include the later period when present — flat-but-split days
        # (same level AM/PM but different problem mix) deserve two rows just
        # as escalating days do.  The old strictly-greater gate is dropped.
        if later_list:
            rows.extend(_rows_for_period(later_list))
        return rows

    # ALBINA-style: no all_day entry — earlier then later, each potentially
    # split into multiple elevation-band rows.
    fallback_rows: list[dict[str, Any]] = []
    for p in ("earlier", "later"):
        fallback_rows.extend(_rows_for_period(by_period.get(p) or []))
    return fallback_rows


def _day_windows_from_raw_ratings(bulletin: Bulletin) -> list[dict[str, Any]]:
    """
    Build day-window rows from raw CAAML dangerRatings.

    Fallback used for bulletins that predate the ``danger.ratings``
    projection (v4 and earlier). Applies the same SLF / ALBINA logic as
    ``_day_windows_from_rm_ratings`` but reads directly from the raw
    ``dangerRatings`` CAAML properties.
    """
    props = _get_properties(bulletin)
    raw_ratings: list[dict[str, Any]] = props.get("dangerRatings") or []
    by_period_raw = _max_rating_per_period(raw_ratings)

    def _day_window_row(rating: dict[str, Any]) -> dict[str, Any]:
        """Build one day-window row dict from a CAAML dangerRating."""
        period, level, sub = _parse_danger_rating(rating)
        suffix = _SUBDIVISION_SUFFIX.get(sub, "")
        number = _DANGER_PANEL_META[level]["number"]
        return {
            "type": period,
            "level_key": level,
            "level_css": level.replace("_", "-"),
            "level_label": _DANGER_PANEL_META[level]["label"],
            "level_number": f"{number}{suffix}",
            "subdivision_label": _SUBDIVISION_LABELS.get(suffix, ""),
            "caption": "",
            "pill_label": _DAY_WINDOW_PILL_LABELS.get(period, period),
        }

    all_day_rating = by_period_raw.get("all_day")
    if all_day_rating is not None:
        windows = [_day_window_row(all_day_rating)]
        later_rating = by_period_raw.get("later")
        # Always include the later period when present — flat-but-split days
        # (same level AM/PM but different problem mix) deserve two rows just
        # as escalating days do.  The old strictly-greater gate is dropped.
        if later_rating is not None:
            windows.append(_day_window_row(later_rating))
        return windows

    # ALBINA-style: one row per period, ordered earlier → later.
    period_order = ("earlier", "later")
    return [
        _day_window_row(by_period_raw[p]) for p in period_order if p in by_period_raw
    ]


def _build_day_windows(
    bulletin: Bulletin,
    render_model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Return the list[Window] consumed by the day-windows panel partial.

    Reads from the projected ``danger.ratings`` list in the render model when
    available. Falls back to reading raw ``dangerRatings`` from CAAML properties
    when the render model is absent or carries no ``danger.ratings`` (e.g. for
    v4 bulletins that predate this projection).

    SLF editorial style: always one ``all_day`` rating, optionally a
    ``later`` overlay when the problem mix changes. Emits one row for the
    ``all_day`` rating; emits the ``later`` overlay whenever present —
    flat-but-split days (same danger level, different problem mix AM/PM)
    deserve two rows just as escalating days do (SNOW-291).

    ALBINA / ALBINA style: no ``all_day``; ratings split by
    ``validTimePeriod`` (and often by elevation within a period). When
    no ``all_day`` rating exists, fall back to one row per period found
    in the source, picking the highest-rank rating across any elevation
    bands for that period. The bulletin's problem cards below the panel
    carry the full per-trait + elevation detail.

    Returns an empty list only when no usable ratings are found — the
    template hides the panel in that case.

    Args:
        bulletin: The Bulletin to build windows for.
        render_model: The render model dict (enriched or raw). When supplied,
            the projected ``danger.ratings`` list is used in preference to
            raw CAAML properties.

    """
    rm_ratings: list[dict[str, Any]] = []
    if render_model:
        rm_ratings = (render_model.get("danger") or {}).get("ratings") or []

    if rm_ratings:
        return _day_windows_from_rm_ratings(rm_ratings)

    # Fallback: read raw dangerRatings from CAAML properties.
    # Used for bulletins that predate the danger.ratings projection (v4 and earlier).
    return _day_windows_from_raw_ratings(bulletin)


def _build_canonical_url(
    region: MicroRegion,
    target_date: datetime.date | None,
) -> str:
    """
    Build the absolute canonical URL for a region (and optional date).

    Used by ``_bulletin_detail_response`` to populate both the
    ``<link rel="canonical">`` tag and ``og:url``. ``target_date``
    selects between the two canonical families (SNOW-99): pass ``None``
    for the form-2 "today" / evergreen URL ``/<region_id>/<slug>/``, or a
    ``date`` for the form-3 dated URL
    ``/<region_id>/<slug>/<YYYY-MM-DD>/``. Defers to
    ``MicroRegion.get_absolute_url`` so the path components stay
    consistent with every other internal URL builder.

    Built from ``settings.SITE_BASE_URL`` rather than
    ``request.build_absolute_uri`` (SNOW-553). The latter self-canonicalises
    to whatever ``Host`` header the requester arrived on — so a crawler
    reaching the site by its ``*.onrender.com`` alias was told that alias
    was the canonical URL, and ``og:url`` disagreed with ``og:image``,
    which has always been origin-keyed. This matches the convention
    ``serve_manifest``, ``serve_robots`` and ``serve_llms_txt`` already
    use.

    """
    base = settings.SITE_BASE_URL.rstrip("/")
    return f"{base}{region.get_absolute_url(target_date)}"


def _resolve_region_for_bulletin(region_id: str) -> MicroRegion:
    """
    Look up a MicroRegion with the prefetches the bulletin page needs.

    ``select_related("subregion__major")`` pre-loads the parent EAWS L2 row
    (which the masthead's H2 reads) and its parent MajorRegion (which
    ``_build_structured_data`` uses for the JSON-LD ``spatialCoverage``
    ``containedInPlace`` field). Without the full chain, every bulletin
    pageview fires an extra SELECT on ``regions_majorregion`` (SNOW-13
    query-count monitor catches regressions). ``neighbours`` is prefetched
    ordered-by-name so the "Adjoining regions" section iterates in display
    order without a per-render sort.
    """
    return get_object_or_404(
        MicroRegion.objects.select_related("subregion__major").prefetch_related(
            Prefetch("neighbours", queryset=MicroRegion.objects.order_by("name")),
        ),
        region_id__iexact=region_id,
    )


def _build_map_url(
    region_id: str, target_date: datetime.date, today: datetime.date
) -> str:
    """Return the context-aware map back-link URL for the bulletin nav bar.

    Omits ``?d=`` when *target_date* is today — the map scrubber defaults to
    today so the query string would be redundant. The URL fragment always
    carries the region ID so the map opens the region sheet at peek (SNOW-183).

    SNOW-344: resolves to ``/`` (the canonical map page) because
    ``public:map`` now redirects there. The back-link URL is used only for
    display and navigation, not for server round-trips, so the redirect is
    transparent.

    Args:
        region_id: The canonical EAWS region identifier (e.g. ``"CH-4115"``).
        target_date: Calendar day the bulletin page represents.
        today: Current date; used to decide whether to include ``?d=``.

    Returns:
        A relative URL string such as ``"/#CH-4115"`` or
        ``"/?d=2025-01-20#CH-4115"``.

    """
    base = reverse("public:home")
    if target_date == today:
        return f"{base}#{region_id}"
    return f"{base}?d={target_date.isoformat()}#{region_id}"


_OG_DESCRIPTION_MAX_CHARS = 155


def _build_og_description(panel: dict[str, Any] | None) -> str:
    """
    Build an OpenGraph ``og:description`` string from the panel context.

    Constructs a human-readable one-liner of the form:

        "Avalanche danger: {label} ({number}). {key_message}"

    Strips any HTML tags from ``key_message`` (the field may contain inline
    markup from the SLF CAAML prose fields).  Truncates to at most
    ``_OG_DESCRIPTION_MAX_CHARS`` characters on a word boundary so the string
    fits comfortably in a SERP snippet.

    Falls back gracefully: returns an empty string when ``panel`` is ``None``
    or when the ``danger_key`` field is absent (empty-state pages).

    Args:
        panel: The panel context dict built by :func:`_build_panel_context`,
            or ``None`` for empty-state pages.

    Returns:
        A plain-text description string of at most ``_OG_DESCRIPTION_MAX_CHARS``
        characters, or ``""`` when no meaningful content is available.

    """
    if not panel or not panel.get("danger_key"):
        return ""

    label = str(panel.get("danger_label") or "")
    number = str(panel.get("danger_number") or "")
    raw_key_message = strip_tags(str(panel.get("key_message") or "")).strip()

    if label and number:
        prefix = _gettext("Avalanche danger: %(label)s (%(number)s).") % {
            "label": label,
            "number": number,
        }
    elif label:
        prefix = _gettext("Avalanche danger: %(label)s.") % {"label": label}
    else:
        prefix = ""

    description = f"{prefix} {raw_key_message}".strip() if raw_key_message else prefix

    if len(description) <= _OG_DESCRIPTION_MAX_CHARS:
        return description

    # Truncate at a word boundary to avoid mid-word splits.
    truncated = description[:_OG_DESCRIPTION_MAX_CHARS]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip(".,;:")


def _build_article_times(
    bulletin: Bulletin,
    panel: dict[str, Any],
) -> tuple[str, str]:
    """
    Return the ``(published, modified)`` ISO-8601 timestamps for a bulletin.

    One derivation, two consumers: the JSON-LD ``Report``'s
    ``datePublished`` / ``dateModified`` and the ``article:published_time``
    / ``article:modified_time`` OG properties on the bulletin page
    (SNOW-555). Deriving them separately would let the structured data and
    the share card disagree about when a bulletin was issued, which is the
    one fact a reader most needs to trust — a bulletin's value is almost
    entirely a function of how recent it is.

    Published time comes from the render model's ``publication_time``,
    falling back to ``bulletin.valid_from`` so the field is always
    present. Modified time is the row's own ``updated_at``, so a re-issued
    bulletin reads as fresher than an untouched one.

    Both source values are timezone-aware (project invariant), so
    ``isoformat()`` already yields the offset-bearing form OG and
    schema.org expect.

    Args:
        bulletin: The ``Bulletin`` being rendered.
        panel: The panel context dict built by :func:`_build_panel_context`.

    Returns:
        A ``(date_published, date_modified)`` pair of ISO-8601 strings.

    """
    render_model_meta: dict[str, Any] = (panel.get("render_model") or {}).get(
        "metadata"
    ) or {}
    date_published: str = (
        render_model_meta.get("publication_time") or bulletin.valid_from.isoformat()
    )
    return date_published, bulletin.updated_at.isoformat()


def _build_structured_data(
    region: MicroRegion,
    bulletin: Bulletin,
    panel: dict[str, Any],
    canonical_url: str,
) -> str:
    r"""
    Build a JSON-LD ``WebPage`` + ``Report`` structured-data payload.

    Serialises the payload to a JSON string suitable for embedding directly
    inside a ``<script type="application/ld+json">`` tag.  The ``</``
    substring is escaped to ``<\/`` so that a stray ``</script>`` in any
    string field cannot terminate the embedding script tag.  JSON decoders
    treat ``\/`` identically to ``/``, so the round-trip through
    ``JSON.parse`` is unaffected.

    Snowdesk is the page *publisher*; SLF (or another source agency) is
    attached to the ``Report`` as ``sourceOrganization``.  The two roles are
    kept strictly separate — Snowdesk displays bulletins, it does not issue
    them.

    Args:
        region: The ``MicroRegion`` the bulletin covers.  Must have
            ``subregion`` and ``subregion.major`` available (i.e. fetched
            with ``select_related("subregion__major")``).
        bulletin: The ``Bulletin`` being rendered.
        panel: The panel context dict built by :func:`_build_panel_context`.
            Must contain a ``danger_key`` entry.
        canonical_url: The absolute canonical URL for the page; used as the
            ``@id`` anchor for both the ``WebPage`` and the ``Report``.

    Returns:
        A JSON string (with ``</`` escaped as ``<\/``) ready for
        ``{{ structured_data_json|safe }}`` in a template.

    """
    # Source organisation details (SLF / ALBINA / METEOFRANCE).
    source_key = (panel.get("render_model") or {}).get("source", "")
    source_name, source_url = BULLETIN_SOURCE_LINKS.get(source_key, ("", ""))

    # Major-region name for spatialCoverage.containedInPlace.
    major = region.subregion.major if region.subregion else None
    major_name = (major.name_en or major.name_native) if major else ""

    # Danger level label and numeric code.
    danger_key: str = panel.get("danger_key") or "low"
    danger_meta = _DANGER_MAP.get(danger_key, _DANGER_MAP["low"])
    danger_label = str(danger_meta["label"])
    danger_number = str(danger_meta["number"])

    # Shared with the article:* OG properties on the page — see
    # _build_article_times for why the derivation is not repeated here.
    date_published, date_modified = _build_article_times(bulletin, panel)

    # ISO-8601 interval for temporalCoverage.
    temporal_coverage = (
        f"{bulletin.valid_from.isoformat()}/{bulletin.valid_to.isoformat()}"
    )

    # SNOW-394: spatialCoverage.geo from MicroRegion.centre. The field
    # is populated at fixture-ingest time as {"lon": …, "lat": …} and
    # covers every real region; guarding the lookup keeps the field
    # optional so a partially-seeded region (dev-only edge) still
    # renders.
    spatial_coverage: dict[str, Any] = {
        "@type": "Place",
        "name": region.name,
        "containedInPlace": {
            "@type": "Place",
            "name": major_name,
        },
    }
    if isinstance(region.centre, dict):
        lat = region.centre.get("lat")
        lon = region.centre.get("lon")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            spatial_coverage["geo"] = {
                "@type": "GeoCoordinates",
                "latitude": float(lat),
                "longitude": float(lon),
            }

    report: dict[str, Any] = {
        "@type": "Report",
        "@id": f"{canonical_url}#report",
        "name": f"Avalanche bulletin — {region.name}",
        "datePublished": date_published,
        # SNOW-394: dateModified reflects the last upsert of the row so
        # LLMs and freshness-aware crawlers can tell a re-issued
        # bulletin from a fresh one.
        "dateModified": date_modified,
        "temporalCoverage": temporal_coverage,
        "inLanguage": get_language() or "en-gb",
        "sourceOrganization": {
            "@type": "Organization",
            "name": source_name,
            "url": source_url,
        },
        "spatialCoverage": spatial_coverage,
        "about": {
            "@type": "DefinedTerm",
            "name": danger_label,
            "termCode": danger_number,
            "inDefinedTermSet": "https://www.avalanches.org/standards/avalanche-danger-scale/",
        },
    }
    # SNOW-394: isBasedOn points at the upstream source document — the
    # per-bulletin ``pdf_url`` populated at ingest by SLF / ALBINA /
    # Météo-France fetchers. Stronger citation than sourceOrganization
    # alone; only emitted when we have a concrete URL to point at.
    if bulletin.pdf_url:
        report["isBasedOn"] = {
            "@type": "CreativeWork",
            "url": bulletin.pdf_url,
            "encodingFormat": "application/pdf",
        }

    payload: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "@id": canonical_url,
        "url": canonical_url,
        "name": f"{region.name} — Snowdesk",
        "inLanguage": get_language() or "en-gb",
        "publisher": {
            "@type": "Organization",
            "name": settings.SITE_NAME,
            "url": settings.SITE_BASE_URL,
        },
        "mainEntity": report,
    }

    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _capture_utm_to_session(request: HttpRequest) -> None:
    """Store UTM query parameters in the session from the bulletin page GET request.

    The subscribe form is submitted via a separate HTMX POST and has no
    direct access to the original GET query string.  Storing UTM params in
    the session bridges the two requests so ``subscription_started`` events
    carry attribution data.

    Only updates when at least one UTM param is non-empty so subsequent
    pageviews without UTM params do not clear previously-captured attribution.

    Args:
        request: The incoming GET request that may carry UTM params.

    """
    utm_params = {
        k: request.GET.get(k, "") for k in ("utm_source", "utm_medium", "utm_campaign")
    }
    if any(utm_params.values()):
        request.session["analytics_utm"] = utm_params


def _track_bulletin_viewed(
    request: HttpRequest,
    region: MicroRegion,
    bulletin: Bulletin,
    panel: dict[str, Any],
) -> None:
    """Emit the ``bulletin_viewed`` analytics event.

    Fires for every bulletin page load where a bulletin is selected
    (non-empty-state) and the path is not under ``/examples/``.  The
    ``/examples/`` guard lives here so the caller in
    ``_bulletin_detail_response`` requires no additional branch.

    The ``distinct_id`` is the account uuid (``apps.accounts.identity``) for
    authenticated visitors — never the sequential ``auth.User`` primary key,
    which leaks user count and growth rate (SNOW-549).  For anonymous
    visitors it is the existing Django session key
    when one is already in the cookie (no DB query), or a request-scoped
    ``anon-<uuid4>`` string when no session exists yet.  The UUID approach
    avoids forcing a session-row INSERT on every anonymous pageview, which
    would add 2–7 extra DB queries per request.

    Args:
        request: The incoming HTTP request.
        region: The ``MicroRegion`` displayed on this page.
        bulletin: The selected ``Bulletin`` being rendered.
        panel: The panel context dict produced by ``_build_panel_context``.

    """
    # Guard: example pages are synthetic demos — do not fire analytics.
    if request.path.startswith("/examples/"):
        return

    # Determine the distinct_id (SNOW-549: Account.uuid, not the user PK).
    if request.user.is_authenticated:
        distinct_id = request_identity(request)
    else:
        # For anonymous users, use the existing session key if one is already
        # present in the cookie (the session was created by a prior request,
        # so reading session_key is free).  When no session exists yet, mint a
        # request-scoped UUID instead of calling session.save() — saving the
        # session forces 2–7 extra DB queries (BEGIN/INSERT/COMMIT + the
        # SessionMiddleware re-save on process_response).  The UUID is not
        # stable across requests, but anonymous event attribution is
        # approximate by nature; the per-session fidelity is acceptable.
        if request.session.session_key:
            distinct_id = request.session.session_key
        else:
            distinct_id = f"anon-{uuid.uuid4()}"

    # Danger level — read from the panel's danger_number (integer 1–5).
    danger_level: int | None = panel.get("danger_number")

    # Days since the bulletin's valid_from date.
    days_since_publish = (timezone.now().date() - bulletin.valid_from.date()).days

    # View context: was this opened via an email link?
    view_context = "email_link" if request.GET.get("ref") == "email" else "direct"

    analytics.track(
        "bulletin_viewed",
        distinct_id,
        {
            "region_id": region.region_id,
            "danger_level": danger_level,
            "days_since_publish": days_since_publish,
            "view_context": view_context,
        },
    )


def _build_morning_rating(panel: dict[str, Any]) -> dict[str, str] | None:
    """
    Project the morning danger level into a small context dict for the hero badge.

    Reads ``morning_key``, ``morning_number``, and ``morning_subdivision`` from
    the panel dict built by :func:`_build_panel_context`. Returns ``None`` when
    the panel carries no usable morning rating (``"no_rating"`` key) so the
    template can gate the badge on truthiness.

    Subdivision is the display character (``"+"``, ``"-"``, ``"="``) already
    resolved by the render-model adapter — SLF bulletins carry it from
    ``customData.CH``; ALBINA and METEOFRANCE return ``""`` so the badge
    renders the bare number without a suffix. No source-conditional branches
    appear here or in the template.

    Args:
        panel: The panel context dict from :func:`_build_panel_context`.

    Returns:
        A dict with ``level_key``, ``level_number``, and ``subdivision`` keys,
        or ``None`` when the morning rating is absent or ``"no_rating"``.

    """
    morning_key: str = panel.get("morning_key") or ""
    if not morning_key or morning_key == "no_rating":
        return None
    return {
        "level_key": morning_key,
        "level_number": str(panel.get("morning_number") or ""),
        "subdivision": str(panel.get("morning_subdivision") or ""),
    }


def _build_period_transition_chip(
    period_transition: PeriodTransition | None,
) -> dict[str, str] | None:
    """
    Project a :class:`PeriodTransition` into a hero chip context dict.

    Returns ``None`` when no transition exists or the direction is ``"none"``
    (flat-but-split — the chip is suppressed and only the Day Risk Profile
    caption surfaces the story).

    The chip text is constructed from:
    - The partition qualifier (temporal: blank; elevation: e.g. "above 2200 m").
    - The direction verb ("rises" or "falls").
    - The destination level number + subdivision.

    Examples:
      - Temporal escalation to L3:      ``"rises to L3"``
      - Temporal de-escalation to L2:   ``"falls to L2"``
      - Elevation rise above 2600 m:    ``"rises above 2600 m to L3"``
      - Elevation fall below 1800 m:    ``"falls below 1800 m to L2"``

    Args:
        period_transition: A :class:`PeriodTransition` from
            :func:`apps.bulletins.services.render_model.compute_period_transition`,
            or ``None``.

    Returns:
        A dict with ``level_key``, ``chip_text``, and ``direction`` keys for
        template rendering, or ``None`` when the chip should not be shown.

    """
    if period_transition is None or period_transition.direction == "none":
        return None

    direction = period_transition.direction
    verb = _gettext("rises") if direction == "rise" else _gettext("falls")
    level_num = period_transition.destination_number
    level_sub = period_transition.destination_subdivision or ""
    label = f"L{level_num}{level_sub}"

    partition_label = period_transition.partition_label
    if partition_label:
        # e.g. "rises above 2600 m to L3"
        chip_text = _gettext("%(verb)s %(partition)s to %(label)s") % {
            "verb": verb,
            "partition": partition_label,
            "label": label,
        }
    else:
        # e.g. "rises to L3"
        chip_text = _gettext("%(verb)s to %(label)s") % {
            "verb": verb,
            "label": label,
        }

    return {
        "level_key": period_transition.destination_key,
        "chip_text": chip_text,
        "direction": direction,
    }


def _region_forecast_panel(
    region: MicroRegion, target_date: datetime.date
) -> Any | None:
    """Build the bulletin page's multi-day forecast panel for a region.

    Reads through the region's centroid ``Location`` (SNOW-696) — the same
    ``ForecastCellWeather`` window and the same ``_forecast_panel.html``
    partial the resort page and the favourite card already use, so the
    bulletin page gets wind and freezing level with no new display code.

    Args:
        region: The region whose bulletin is being rendered.
        target_date: The first day of the forward window.

    Returns:
        The panel, or ``None`` when the region has no centroid location
        yet, its location has no forecast cell, or the cell has no rows in
        the window. Callers render nothing at all in that case — a section
        promising weather it cannot show is worse than no section.

    """
    location = region.centroid_location
    if location is None or location.forecast_cell_id is None:
        return None
    return build_point_forecast_panel(
        list(
            ForecastCellWeather.objects.filter(
                forecast_cell_id=location.forecast_cell_id,
                valid_for_date__gte=target_date,
            ).order_by("valid_for_date")[:POINT_FORECAST_DAYS]
        ),
        timezone.now(),
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
    # Resorts in this region (SNOW-504) — reverse FK, alphabetical per
    # Resort.Meta.ordering. Cross-links the bulletin page to each resort's
    # own page; empty for regions with no fixture-seeded resorts.
    # SNOW-544: kind=RESORT only — see regions.ResortQuerySet.resorts().
    resorts_in_region = list(region.resorts.resorts())
    favourites_in_region = _get_favourites_in_region(request, region)

    _capture_utm_to_session(request)

    # Warm the cache for future region_redirect lookups.
    cache.set(
        _cache_key(region.slug),
        slugify(region.name),
        timeout=_ZONE_NAME_CACHE_TIMEOUT,
    )

    today = timezone.now().date()

    # Build the context-aware back-link for the nav bar (SNOW-183).
    map_url = _build_map_url(region.region_id, target_date, today)

    # Two canonical-URL flavours: the no-date "today" form (form 2)
    # for live / evergreen views, and the dated form (form 3) for
    # historical views. See SNOW-99.
    canonical_url = _build_canonical_url(
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

    # Region forecast (SNOW-696) — the same multi-day panel the resort page
    # and the favourite card already show, read through the region's
    # centroid Location. WeatherSnapshot above keeps its job as the
    # masthead's day/night visual; this is the numbers, and the numbers
    # come from a point (docs/locations.md).
    #
    # Only from today forward. The window ForecastCellWeather holds is a
    # *forecast*, so rendering it beside a historical bulletin would put
    # next week's weather under last week's danger rating — and the
    # ``valid_for_date__gte=target_date`` filter below would silently
    # return today's rows for a date in the past, which is worse than
    # showing nothing.
    #
    # Gated on the date rather than on ``canonical_is_today``: that flag
    # selects which canonical URL to advertise, and is False on the
    # perfectly live dated form (/<region>/<slug>/<today>/).
    forecast_panel = (
        _region_forecast_panel(region, target_date) if target_date >= today else None
    )

    # When the page would otherwise emit the HTMX trigger, warm the snapshot
    # on a background thread so the user's actual click — which comes seconds
    # after the browser prefetch — lands on a server render that bakes weather
    # inline (no HTMX swap, no flash). The HTMX trigger stays in the
    # no-weather template as a safety net for the rare
    # click-before-worker-finishes case. SNOW-164.
    #
    # Today and forward only. This warmed *past* dates off the archive
    # endpoint until that URL was confined to ``backfill_weather``; a past day
    # with no stored row now renders its no-weather state and waits for a
    # backfill run, rather than fetching history on a page view.
    if weather_snapshot is None and target_date >= today:
        fetch_weather_async(region, target_date)

    # Collect every issue that touches the target day and pick the one
    # the caller asked for; otherwise fall back to the 10:00-rule
    # default. Multi-issue days render the requested (or default) issue
    # as the page body.
    issues = _issues_for_date(region, target_date)
    selected = _resolve_selected_issue(issues, target_date, requested_issue_id)

    # The page represents the calendar day chosen in the URL, independent
    # of which issue the viewer has selected — otherwise flipping to the
    # same-day-evening issue would silently bump the header to D+1.
    page_date = target_date

    # Day-based prev/next navigation — shared by both the populated and
    # empty-state branches so the outer nav container always has its
    # data-prev-url / data-next-url attributes.
    prev_date, next_date = _get_nav_dates(region, page_date)

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

    # The masthead subtitles the H1 with the parent EAWS L2 sub-region.
    # Prefer the English name where SLF publishes one, otherwise fall back
    # to the locally-dominant native name. ``MicroRegion.subregion`` is
    # non-nullable so this lookup is always safe.
    subregion_name = (
        region.subregion.name_en or region.subregion.name_native
        if region.subregion
        else ""
    )

    if selected is None:
        response = _render_bulletin_page(
            request,
            {
                "bulletin": None,
                "region": region,
                "region_name": region_name,
                "region_id": region.region_id,
                "slug": slugify(region.name),
                "subregion_name": subregion_name,
                "page_date": target_date,
                "prev_date": prev_date,
                "next_date": next_date,
                "year": datetime.date.today().year,
                "adjoining_regions": adjoining_regions,
                "resorts_in_region": resorts_in_region,
                "favourites_in_region": favourites_in_region,
                "season_calendar": season_header(today),
                "weather_display": weather_display,
                "forecast_panel": forecast_panel,
                "weather_htmx_trigger": weather_display is None,
                "canonical_url": canonical_url,
                "map_url": map_url,
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

    is_today = page_date == today
    next_update_time: datetime.datetime | None = None
    now = timezone.now()
    if (
        is_today
        and not _has_later_bulletin(region, page_date)
        and selected.next_update
        and selected.next_update > now
    ):
        next_update_time = selected.next_update

    panel = _build_panel_context(selected)

    day_windows: list[dict[str, Any]] = _build_day_windows(
        selected, render_model=panel.get("render_model")
    )

    season_calendar = season_header(today)

    # Derive the source label and URL for the metadata strip Source cell
    # (SNOW-211). Read from the panel's render model — not selected.render_model
    # — so v3 bulletins benefit from _build_panel_context's on-the-fly rebuild
    # to v4 (which is the version that introduced the ``source`` key).
    source_key = (panel.get("render_model") or {}).get("source", "")
    bulletin_source_label, bulletin_source_url = BULLETIN_SOURCE_LINKS.get(
        source_key, ("", "")
    )

    # SNOW-324: per-type field-observation counts for the current-day bulletin
    # page.  Only fetched when the flag is active; zero-overhead on historic
    # pages because the ``is_today`` guard short-circuits the query.
    observation_counts: list[tuple[str, int]] = (
        _get_observation_counts(request, region, page_date) if is_today else []
    )

    # SNOW-330: whether any user-located (MANUAL or GPS_REFINED) report exists
    # for this region today.  Drives the public footnote under the counts strip.
    # Only fetched when flag is active and page is today.
    observation_has_user_located: bool = (
        _get_observation_has_user_located(request, region, page_date)
        if is_today
        else False
    )

    # Emit bulletin_viewed (no-ops silently on /examples/* paths).
    _track_bulletin_viewed(request, region, selected, panel)

    # Morning rating badge context (SNOW-246). Projected from the panel's
    # render-model adapter — no source-conditional branches in the template.
    # subdivision is already a display char ("+", "-", "=") or "" for sources
    # that don't carry per-rating subdivision (ALBINA, METEOFRANCE).
    morning_rating: dict[str, str] | None = _build_morning_rating(panel)

    # Period transition — rise/fall/flat-but-split chip beside the hero badge
    # (SNOW-248). Derived from the render model's danger.ratings list; source-
    # neutral (no if source == "slf" branches). ``None`` on all-day bulletins.
    raw_render_model: dict[str, Any] = panel.get("render_model") or {}
    period_transition: PeriodTransition | None = compute_period_transition(
        raw_render_model
    )
    # Hero chip: rise/fall only — flat-but-split (direction="none") is suppressed.
    period_transition_chip: dict[str, str] | None = _build_period_transition_chip(
        period_transition
    )

    # Bulletin headline (SNOW-249) — data-driven copy from the variant matrix.
    # The five inputs are projected from the render model and the period transition.
    rm_source: str = raw_render_model.get("source") or ""
    rm_partition_type: str = (
        period_transition.partition_type if period_transition is not None else "none"
    )
    _rm_danger_info: dict[str, Any] = raw_render_model.get("danger") or {}
    rm_peak_number: str = _rm_danger_info.get("number") or "1"
    rm_peak_subdivision: str = _rm_danger_info.get("subdivision") or ""
    rm_peak_rating: str = (
        f"{rm_peak_number}{rm_peak_subdivision}"
        if rm_peak_subdivision
        else rm_peak_number
    )
    rm_direction: str = (
        period_transition.direction if period_transition is not None else "none"
    )
    # Day movement — the same classification the day summary uses, so the
    # Day Risk Profile's flat-split caption cannot claim a change the
    # summary beside it reports as a static day. 23 of the archive's 101
    # flat splits change nothing the reader can act on; see docs/day-summary.md.
    rm_traits: list[dict[str, Any]] = raw_render_model.get("traits") or []
    day_movement: str = day_summary.classify_movement(
        period_transition.direction
        if period_transition is not None and period_transition.has_split
        else "",
        set(problem_types_for(rm_traits, {"all_day", "earlier"})),
        set(problem_types_for(rm_traits, {"later"})),
    )

    rm_family: str = derive_problem_family(raw_render_model)
    headline: str = headline_for(
        rm_source,
        rm_partition_type,
        rm_peak_rating,
        rm_direction,
        rm_family,
    )

    # SNOW-555: one derivation feeding both the JSON-LD Report below and the
    # article:* OG properties on the page.
    article_published_time, article_modified_time = _build_article_times(
        selected, panel
    )

    context = {
        "region": region,
        "region_name": region_name,
        "region_id": region.region_id.lower(),
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
        # Hero rating badge — morning level + optional subdivision (SNOW-246).
        "morning_rating": morning_rating,
        # Period transition chip — rise/fall beside the hero badge (SNOW-248).
        # None on all-day or flat-but-split bulletins.
        "period_transition": period_transition,
        "period_transition_chip": period_transition_chip,
        # Day movement — gates the flat-split caption (SNOW day-summary work).
        "day_movement": day_movement,
        # Geographic neighbours — see SNOW-82.
        "adjoining_regions": adjoining_regions,
        # Resorts in this region — see SNOW-504.
        "resorts_in_region": resorts_in_region,
        # The user's own favourites in this region — see SNOW-507.
        "favourites_in_region": favourites_in_region,
        # Weather-driven header — see SNOW-98.
        "weather_display": weather_display,
        "forecast_panel": forecast_panel,
        # Trigger HTMX just-in-time fetch when no snapshot exists (SNOW-159).
        "weather_htmx_trigger": weather_display is None,
        # Canonical form-3 URL — see SNOW-99.
        "canonical_url": canonical_url,
        # Context-aware back-link for the nav bar — see SNOW-183.
        "map_url": map_url,
        # Source agency label and URL for the metadata strip Source cell (SNOW-211).
        "bulletin_source_label": bulletin_source_label,
        "bulletin_source_url": bulletin_source_url,
        # OG description — plain-text summary for og:description / twitter:description
        # (SNOW-218).  Built from the panel's danger rating and key message.
        "og_description": _build_og_description(panel),
        # Subscribe panel state — whether the authenticated user already has a
        # Subscription for this region (SNOW-222).  Anonymous users short-circuit
        # to False so no DB query is issued for unauthenticated requests.
        # Subscription lookup: authenticated users with an Account profile are
        # checked; anonymous users and staff-only Users (no profile) return False.
        "user_subscribed_to_region": (
            request.user.is_authenticated
            and hasattr(request.user, "account")
            and Subscription.objects.filter(
                account=request.user.account,
                region=region,
            ).exists()
        ),
        # JSON-LD structured data (SNOW-220) — schema.org WebPage + Report.
        # Serialised with "</"-escaping; rendered unescaped in the template
        # inside a <script type="application/ld+json"> block.
        "structured_data_json": _build_structured_data(
            region, selected, panel, canonical_url
        ),
        # SNOW-555: article:published_time / article:modified_time, from the
        # same helper the JSON-LD above uses, so the share card and the
        # structured data can never disagree about when this was issued.
        "article_published_time": article_published_time,
        "article_modified_time": article_modified_time,
        # Bulletin headline — data-driven variant copy (SNOW-249).
        "headline": headline,
        # SNOW-324: per-type field-observation counts for the current-day
        # bulletin page.  Empty dict on historic pages (flag off or not today).
        "observation_counts": observation_counts,
        # SNOW-330: True when any user-located (MANUAL/GPS_REFINED) report
        # exists for this region today.  Drives a public footnote under the
        # counts strip.  False on historic pages or when flag is inactive.
        "observation_has_user_located": observation_has_user_located,
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


@lowercase_region_id
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
# Resort detail page (SNOW-504)
# ---------------------------------------------------------------------------


class ResortLocationForecast(NamedTuple):
    """One linked location's labelled forecast, for the resort page.

    Attributes:
        location: The ``Location`` itself — the template reads its ``name``
            and ``elevation_m`` for the label.
        role: The ``ResortLocation.ROLE`` this location plays for this
            resort, used as the section's ``data-testid`` suffix.
        is_primary: Whether the resort leads with this one.
        panel: The multi-day panel from ``build_point_forecast_panel``.
        today_row: The day-0 ``ForecastCellWeather`` row, which the hero
            band is fed from when this is the primary location. Separate
            from ``panel`` because the panel is a rendered projection and
            the hero needs the row itself.

    """

    location: Any
    role: str
    is_primary: bool
    panel: Any
    today_row: Any


def _resort_location_forecasts(
    resort: Resort, today: datetime.date
) -> list[ResortLocationForecast]:
    """Return one labelled forecast per linked Location, primary first.

    Ordered primary-first then by ascending elevation, which reads as the
    way up the mountain: village, mid-station, top. Locations with no
    forecast cell, or a cell with no rows in the forward window, are
    omitted rather than rendered empty — the page shows what it has.

    Two queries regardless of how many locations a resort has: one for the
    links (joined to their locations) and one bulk fetch of the window for
    every cell at once, grouped in Python. A per-location query would put
    an unbounded N+1 on a public page.

    Args:
        resort: The resort being rendered.
        today: The first day of the forward window.

    Returns:
        The forecasts, primary first. Empty for an uncurated resort.

    """
    links = list(
        resort.resort_locations.select_related("location")
        .filter(location__forecast_cell__isnull=False)
        .order_by("-is_primary", "location__elevation_m")
    )
    if not links:
        return []

    # The queryset already excludes a null cell; narrowing again here keeps
    # mypy honest and costs nothing.
    cell_ids = {
        link.location.forecast_cell_id
        for link in links
        if link.location.forecast_cell_id is not None
    }
    rows_by_cell: dict[int, list[ForecastCellWeather]] = defaultdict(list)
    for row in (
        ForecastCellWeather.objects.filter(
            forecast_cell_id__in=cell_ids, valid_for_date__gte=today
        )
        .order_by("valid_for_date")
        .iterator()
    ):
        rows_by_cell[row.forecast_cell_id].append(row)

    now = timezone.now()
    forecasts = []
    for link in links:
        cell_id = link.location.forecast_cell_id
        if cell_id is None:
            continue
        rows = rows_by_cell.get(cell_id, [])[:POINT_FORECAST_DAYS]
        if not rows:
            continue
        forecasts.append(
            ResortLocationForecast(
                location=link.location,
                role=link.role,
                is_primary=link.is_primary,
                panel=build_point_forecast_panel(rows, now),
                # Only the day-0 row, and only when it is actually today —
                # a cell whose window starts tomorrow must not hand the
                # hero band a figure for the wrong day.
                today_row=rows[0] if rows[0].valid_for_date == today else None,
            )
        )
    return forecasts


def resort_detail(request: HttpRequest, resort_id: int, slug: str) -> HttpResponse:
    """
    Render the public resort detail page.

    Gives a Resort its own indexable URL (``/resorts/<id>/<slug>/``),
    cross-linking with the bulletin page (which lists "Resorts in this
    region" — see ``_bulletin_detail_response``) and the map's resort-pin
    popup (``apps.public.api.resort_popup``, whose CTA now reads "View resort →"
    and links here instead of straight to the bulletin).

    301-redirects to the canonical slug when the inbound ``slug`` doesn't
    match ``resort.name_slug`` — mirrors the region canonical-slug
    behaviour (``_redirect_to_canonical``) so search engines index one URL
    per resort.

    Reuses the context-building already proven by ``apps.public.api.resort_popup``
    (``favourited`` / ``favourite_uuid`` / ``can_favourite`` / ``signin_url``)
    and by ``apps.public.api.region_summary`` (today's ``RegionDayRating`` lookup
    for the danger chip). Field-observation counts are point-local — scoped
    to ``settings.FIELD_OBSERVATION_RADIUS_KM`` of the resort's own
    coordinates (SNOW-508) — falling back to the region-wide count when the
    resort has no coordinates.

    Weather is **one forecast per linked ``Location``** (SNOW-702), each
    labelled with its name and elevation — "Verbier village · 1436 m",
    "Mont Fort · 3328 m" — with the resort's primary location leading. A
    resort with one linked location renders exactly what it rendered
    before, plus the label it was always missing, so the page degrades
    cleanly across a partially-curated estate.

    The hero band is fed from the **primary location's day-0 row**, not
    from the parent region's ``WeatherSnapshot``. The accepted
    weather-sourcing rule is numbers from points, decoration from
    snapshots, and the page previously broke it by stacking
    region-centroid figures directly above point figures for the same day.
    The snapshot remains the fallback so an uncurated resort loses
    nothing. See ``docs/decisions/resort-page-shows-location-forecasts.md``.

    Args:
        request: The incoming HTTP request.
        resort_id: The Resort's primary key, from the URL.
        slug: The inbound URL slug — checked against ``resort.name_slug``;
            a mismatch 301s to the canonical URL.

    Returns:
        The rendered resort page, or a 301 redirect to the canonical URL.

    """
    resort = get_object_or_404(
        Resort.objects.select_related("region", "forecast_point"),
        pk=resort_id,
    )

    if slug != resort.name_slug:
        return redirect(resort.get_absolute_url(), permanent=True)

    region = resort.region
    today = timezone.localdate()

    day_rating = RegionDayRating.objects.filter(region=region, date=today).first()

    can_favourite = False
    favourite = None
    if request.user.is_authenticated:
        can_favourite = True
        favourite = Favourite.objects.filter(user=request.user, resort=resort).first()

    # One forecast per linked Location (SNOW-702), primary first.
    location_forecasts = _resort_location_forecasts(resort, today)

    # The hero band takes its numbers from the primary location's day-0
    # row. Falling back to the region's WeatherSnapshot when the resort has
    # no curated location yet, or its cell has no rows — an uncurated
    # resort must lose nothing, which is what makes SNOW-701's incremental
    # curation safe to land a few resorts at a time.
    #
    # ``.first()`` on the snapshot is safe because
    # ``unique_together = (region, valid_for_date)``.
    hero_row = location_forecasts[0].today_row if location_forecasts else None
    weather_snapshot = None
    if hero_row is None:
        weather_snapshot = (
            WeatherSnapshot.objects.for_date(today).filter(region=region).first()
        )
    weather_display = build_weather_display(
        hero_row or weather_snapshot, timezone.now()
    )

    # Retained for the legacy single-panel section until every resort is
    # curated: a resort with no linked location still shows the point
    # forecast SNOW-572 gave it, rather than dropping to the region
    # snapshot alone.
    forecast_panel = None
    if not location_forecasts and resort.forecast_point is not None:
        forecast_snapshots = list(
            ForecastCellWeather.objects.forecast_for_point(
                resort.forecast_point, today
            )[:POINT_FORECAST_DAYS]
        )
        forecast_panel = build_point_forecast_panel(forecast_snapshots, timezone.now())

    context = {
        "resort": resort,
        "region": region,
        "region_id": region.region_id,
        "day_rating": day_rating,
        "target_date": today,
        "bulletin_url": region.get_absolute_url(),
        "favourited": favourite is not None,
        "favourite_uuid": str(favourite.uuid) if favourite else "",
        "can_favourite": can_favourite,
        # SNOW-542: the why-it-matters partial shows a curation hint instead
        # of the public blank state for staff, exactly as the popup's
        # metadata rows already do.
        "is_staff": request.user.is_staff,
        "signin_url": reverse("accounts:sign_in"),
        "local_observations": _get_local_observation_counts(request, resort, today),
        "observation_has_user_located": _get_observation_has_user_located(
            request, region, today
        ),
        "weather_display": weather_display,
        "weather_htmx_trigger": weather_display is None,
        "forecast_panel": forecast_panel,
        "location_forecasts": location_forecasts,
    }
    return render(request, "public/resort.html", context)


# ---------------------------------------------------------------------------
# Share redirect (SNOW-217)
# ---------------------------------------------------------------------------


def _record_share_click(request: HttpRequest, share: BulletinShare) -> None:
    """Record one real share-link click: RequestLog, click row, PostHog event.

    Split out of ``share_redirect`` (SNOW-551) so the speculative-request
    branch can skip every write in one place while still redirecting.

    Args:
        request: The incoming HTTP request.
        share: The ``BulletinShare`` being followed.

    """
    # Capture request context into a RequestLog row.  This also resolves geo
    # fields from the client IP via the GeoLite2-City database.
    req_log = capture_request_log(request)

    # visitor_hash: pseudonymous de-dup key computed from IP + UA.
    ip = req_log.ip_address or ""
    ua = req_log.user_agent
    visitor_hash = hashlib.sha256(f"{ip}|{ua}".encode()).hexdigest()[:16]

    BulletinShareClick.objects.create(
        share=share,
        request=req_log,
        visitor_hash=visitor_hash,
    )

    # Emit share_link_clicked alongside the BulletinShareClick DB record.
    distinct_id = (
        request_identity(request)
        if request.user.is_authenticated
        else (request.session.session_key or f"anon-{uuid.uuid4()}")
    )
    share_props: dict[str, object] = {"region_id": share.region.region_id}
    if req_log.country_code:
        share_props["country_code"] = req_log.country_code
    analytics.track("share_link_clicked", distinct_id, share_props)


# Ceiling on share-link follows per (token, IP) per hour (SNOW-551).
# Deliberately generous: a real visitor re-opening their own link, or a
# household behind one address, must never hit it — the cap exists to stop a
# scanner growing RequestLog and BulletinShareClick without bound.
SHARE_CLICK_RATE: str = "30/h"


def _share_rate_limit_key(group: str, request: HttpRequest) -> str:
    """Return the rate-limit bucket for a share-link follow: (token, IP).

    Keying on the pair rather than the IP alone means one visitor
    re-following their own link is unaffected, while a scanner hammering a
    single link is bounded. A flat per-IP cap would make one NATed office
    network share a single budget across unrelated share links.

    Args:
        group: The django-ratelimit group name (unused — one group here).
        request: The current HTTP request.

    Returns:
        An opaque bucket key.

    """
    match = request.resolver_match
    token = match.kwargs.get("token", "") if match is not None else ""
    return f"{token}|{client_ip(request)}"


@require_http_methods(["GET", "HEAD"])
@ratelimit(key=_share_rate_limit_key, rate=SHARE_CLICK_RATE, block=False)
def share_redirect(request: HttpRequest, token: str) -> HttpResponse:
    """Follow a share link: log the click and 302 to the canonical bulletin URL.

    Looks up the ``BulletinShare`` by token (404 if missing). Delegates
    request-context extraction to ``capture_request_log(request)``, which
    creates a ``RequestLog`` row carrying IP, user-agent, session, Referer,
    Sec-Purpose, geo fields, and language. The ``BulletinShareClick`` row
    stores the ``RequestLog`` as a FK (``click.request``).

    Then:
    * If ``share.bulletin`` is None (the linked bulletin was deleted):
      returns 410 Gone with a brief HTML body and ``Cache-Control: no-store``.
    * Otherwise: 302 to the canonical bulletin URL with ``Cache-Control: no-store``.

    No 301 anywhere — 301s are aggressively cached and would defeat click
    tracking on re-visits.

    Abuse bounds (SNOW-551). This endpoint writes three things on arrival —
    a ``RequestLog``, a ``BulletinShareClick``, and a PostHog event — for
    anyone who knows a public share URL:

    * Only GET and HEAD are accepted; no legitimate POST/PUT/DELETE caller
      exists. ``require_GET`` would reject HEAD outright, but a HEAD should
      still get its redirect — it just must not be counted.
    * Speculative requests (HEAD, ``Sec-Purpose: prefetch`` / ``prerender``)
      skip all three writes and still redirect. A browser prefetching a link
      the user never clicks is not a click.
    * The remainder is rate-limited to 30/hour per (token, IP) — generous
      enough that no real visitor notices, tight enough that a scanner
      cannot grow two tables without bound.

    Args:
        request: The incoming HTTP request (GET or HEAD).
        token: URL-safe token from the short URL.

    Returns:
        An HttpResponse — 302, 404, 410, or 429 as described above.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    share = get_object_or_404(BulletinShare, token=token)

    if not is_speculative(request):
        _record_share_click(request, share)

    if share.bulletin is None:
        gone = HttpResponseGone(
            "<html><body><h1>410 Gone</h1>"
            "<p>This share link is no longer available.</p></body></html>",
            content_type="text/html",
        )
        gone["Cache-Control"] = "no-store"
        return gone

    redirect_url = _build_canonical_url(share.region, share.target_date)
    redir = HttpResponseRedirect(redirect_url)
    redir["Cache-Control"] = "no-store"
    return redir


# ---------------------------------------------------------------------------
# Weather snippet — HTMX-triggered just-in-time weather fetch (SNOW-159)
# ---------------------------------------------------------------------------


# @lowercase_region_id is outermost so the casing check short-circuits before the
# HTMX and method guards. preserve_method=True makes it a 308: the panel builds
# its retry URL from the uppercase EAWS id, and a 301 here would replay it as a
# GET and hit @require_POST's 405 (SNOW-650).
@lowercase_region_id(preserve_method=True)
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
    return the rendered fragment.

    ``?variant=panel`` (SNOW-509) selects which template renders the result:
    the resort page's belt-and-braces retry passes it so the response is the
    bare ``includes/_weather_panel.html`` panel (no region ``<h1>``, no
    share button) rather than the full bulletin masthead — the bulletin
    page's retry omits it and gets the masthead back, unchanged from
    before SNOW-509.

    ``weather_htmx_trigger`` is always ``False`` in the returned fragment so
    that a fetch failure never triggers an infinite retry loop — HTMX will not
    re-fire the trigger on the swapped-in response.

    On any error the view still returns HTTP 200 with the no-weather fragment
    (``data-weather-bucket="none"``); the failure is logged server-side only.

    Args:
        request: The incoming HTMX POST request. ``request.GET["variant"]``,
            when equal to ``"panel"``, selects the bare-panel fragment.
        region_id: EAWS micro-region identifier (e.g. ``"CH-4115"``).
        date_str: ISO-8601 date string (``"YYYY-MM-DD"``).

    Returns:
        Rendered ``includes/bulletin_header.html`` (default) or
        ``includes/_weather_panel.html`` (``?variant=panel``) fragment.

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
            # Forecast endpoint only. A past day with no stored snapshot is
            # left to the ``backfill_weather`` command — the archive URL has
            # one caller, and it is not a request-path view. The panel
            # renders its no-weather state instead.
            if target_date >= today:
                result = fetch_weather_for_region(region, target_date, commit=True)
                snapshot = result[0] if result is not None else None
            if snapshot is not None:
                weather_display = build_weather_display(snapshot, timezone.now())
        except Exception:
            logger.exception(
                "weather_snippet fetch failed: region=%s date=%s",
                region_id,
                target_date,
            )

    variant = request.GET.get("variant")
    if variant == "panel":
        return render(
            request,
            "includes/_weather_panel.html",
            {
                "weather_display": weather_display,
                "weather_htmx_trigger": False,
                "region_name": "",
                "subregion_name": "",
                "page_date": target_date,
                "region_id": region.region_id,
                "panel_testid": "resort-weather",
                "testid_prefix": "resort-weather",
                "panel_extra_classes": "rounded-card mb-4",
            },
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


@lowercase_region_id
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

# Lower-case forms of the same three windows, for the problem card's title
# bar where the window follows the provider's wording after a middot
# ("Dry avalanches &middot; all day").  Sentence-cased labels read as a
# second heading there; these read as the aside they are.
_TIME_PERIOD_TITLE_SUFFIXES: dict[str, Promise] = {
    "all_day": _("all day"),
    "earlier": _("earlier"),
    "later": _("later"),
}

# Human labels for ALBINA's EAWS matrix axes. ALBINA publishes the matrix
# *inputs* (size × frequency × snowpack stability) on every problem; SLF
# publishes the output (per-problem danger rating + comment) instead and
# carries none of these. ``frequency`` value ``"none"`` is treated as
# "not reported" and never rendered — only the canonical EAWS triple
# (few / some / many) produces a chip. ``snowpack_stability`` ``"good"``
# never occurs in observed data (good stability = no problem) but is
# included for completeness.
_FREQUENCY_LABELS: dict[str, Promise] = {
    "few": _("Few"),
    "some": _("Some"),
    "many": _("Many"),
}
_STABILITY_LABELS: dict[str, Promise] = {
    "very_poor": _("Very poor"),
    "poor": _("Poor"),
    "fair": _("Fair"),
    "good": _("Good"),
}

# EAWS destructive avalanche size scale (1–5). ALBINA publishes an integer
# size on every avalanche problem; SLF and MeteoFrance publish none. The
# labels follow the standard EAWS size vocabulary (Small → Large → Very
# large → Extremely large). Size 3 = "Large" matches what avalanche.report
# renders for the same field.
_AVALANCHE_SIZE_LABELS: dict[int, Promise] = {
    1: _("Small"),
    2: _("Medium"),
    3: _("Large"),
    4: _("Very large"),
    5: _("Extremely large"),
}

# LWD Tyrolean danger-pattern names (gm.1–gm.10). Raw bulletin data uses
# "DP1"–"DP10" (or "dp1"–"dp10") for these identifiers. The display label
# is normalised to "GM.1"–"GM.10"; the tooltip carries the full English name.
# Authoritative names follow the LWD_Tyrol convention.
_DANGER_PATTERN_NAMES: dict[str, str] = {
    "gm1": "Deep persistent weak layer",
    "gm2": "Gliding snow",
    "gm3": "Rain",
    "gm4": "Cold, loose snow and wind",
    "gm5": "Snowfall after a long cold period",
    "gm6": "Loose snow and warming",
    "gm7": "Snowpack-rain interface",
    "gm8": "Persistent weak layer in old snow",
    "gm9": "Wind-loaded snow on snowpack",
    "gm10": "Spring scenario",
}

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


# Mirrors WhiteRisk's split: a dangerRating whose validTimePeriod is
# ``all_day`` applies in both halves; ``earlier`` (morning-only) and
# ``later`` (afternoon-only) are scoped to one half each.  Used by
# :func:`_resolve_period_danger_from_rm` to pick the projected ratings that
# cover a given half of the day.
_MORNING_PERIODS: frozenset[str] = frozenset({"all_day", "earlier"})
_AFTERNOON_PERIODS: frozenset[str] = frozenset({"all_day", "later"})


def _normalise_danger_pattern(raw: str) -> dict[str, str]:
    """
    Normalise a raw LWD danger-pattern identifier to a display label and tooltip.

    LWD_Tyrol publishes patterns as ``"DP1"``–``"DP10"`` (sometimes lowercase
    ``"dp1"``–``"dp10"``). The display label uses the ``GM.N`` form; the tooltip
    carries the full English name from :data:`_DANGER_PATTERN_NAMES`.

    Unknown patterns are rendered verbatim with no tooltip.

    Args:
        raw: The raw danger-pattern string from the render model.

    Returns:
        Dict with ``"label"`` (e.g. ``"GM.1"``) and ``"title"`` (full name or ``""``).

    """
    # Normalise: strip "DP"/"dp" prefix, leaving the numeric suffix.
    normalised = raw.strip()
    key_candidate = normalised.lower()
    if key_candidate.startswith("dp"):
        num = key_candidate[2:]
        gm_key = f"gm{num}"
        label = f"GM.{num}"
    elif key_candidate.startswith("gm."):
        num = key_candidate[3:]
        gm_key = f"gm{num}"
        label = f"GM.{num}"
    else:
        # Unrecognised format — render as-is.
        return {"label": normalised, "title": ""}
    return {"label": label, "title": _DANGER_PATTERN_NAMES.get(gm_key, "")}


def _best_rating_from_rm_entries(
    entries: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """
    Return the highest (key, subdivision_suffix) from a list of rm rating entries.

    Returns ``None`` when no entry has a recognised key so the caller can
    fall through to an alternative source.
    """
    best_key: str | None = None
    best_sub: str = ""
    for r in entries:
        rk: str = r.get("key") or ""
        if rk not in _DANGER_ORDER:
            continue
        if best_key is None or _DANGER_ORDER.index(rk) >= _DANGER_ORDER.index(best_key):
            best_key = rk
            best_sub = r.get("subdivision") or ""
    if best_key is None:
        return None
    return best_key, best_sub


def _resolve_period_danger_from_rm(
    rm_ratings: list[dict[str, Any]],
    traits: list[dict[str, Any]],
    period_group: frozenset[str],
) -> tuple[str, str]:
    """
    Return the highest danger key + subdivision covering a half of the day.

    Primary source is the projected ``danger.ratings`` list from the render
    model — one entry per CAAML dangerRating with ``period``, ``key``,
    ``subdivision`` (display char ``"+"/"-"/"="`` or ``None``), and
    ``elevation``. Entries whose ``period`` is in ``period_group`` are
    candidates; the highest ``key`` wins. When multiple entries tie on
    ``key``, the subdivision from the last one encountered is used.

    Falls back to the render-model ``traits`` when no projected ratings cover
    the period — this matches the behaviour of the old
    :func:`_resolve_period_danger` fallback and keeps test fixtures that
    populate only ``render_model`` (not ``raw_data``) rendering correctly.

    Args:
        rm_ratings: The ``danger.ratings`` list from the render model.
        traits: The render-model ``traits`` list (fallback when no ratings
            cover the target period).
        period_group: Set of ``validTimePeriod`` tokens covering the target
            half of the day (``_MORNING_PERIODS`` or ``_AFTERNOON_PERIODS``).

    Returns:
        A ``(key, subdivision_suffix)`` tuple where ``subdivision_suffix`` is
        one of ``"+"``, ``"-"``, ``"="``, or ``""`` when absent/None.

    """
    relevant = [r for r in rm_ratings if r.get("period", "all_day") in period_group]
    if relevant:
        result = _best_rating_from_rm_entries(relevant)
        if result is not None:
            return result

    # Fallback: derive the half's level from traits when projected ratings
    # are absent or omit a covering entry.  Tests populate ``render_model``
    # directly and leave ``raw_data`` empty — without this fallback the
    # headline band would read ``no_rating`` on every test bulletin.
    levels: list[int] = []
    for t in traits:
        if t.get("time_period") not in period_group:
            continue
        try:
            level = int(t.get("danger_level") or 0)
        except TypeError, ValueError:
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

    def __bool__(self) -> bool:
        """Return True when the bound has a displayable value."""
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


# Clockwise compass order used when rendering aspect text labels on problem
# cards. The aspect-rose SVG is positional and unaffected; this is text-only.
_CLOCKWISE_ASPECTS: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _sort_aspects_clockwise(aspects: list[str]) -> list[str]:
    """Return aspects in clockwise compass order (N, NE, …, NW).

    Unknown tokens sort to the end, preserving their relative order.

    Args:
        aspects: Raw aspect list from the CAAML bulletin payload.

    Returns:
        The same list reordered into clockwise compass sequence.

    """
    order = {a: i for i, a in enumerate(_CLOCKWISE_ASPECTS)}
    return sorted(aspects, key=lambda a: order.get(a, len(order)))


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
    aspects: list[str] = _sort_aspects_clockwise(problem.get("aspects") or [])
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
    danger_ratings: list[dict[str, Any]] | None = None,
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
        danger_ratings: Projected ``danger.ratings`` list from the render
            model. Used to look up the per-period subdivision suffix for each
            card (SNOW-291). ``None`` and ``[]`` produce empty subdivisions.

    Returns:
        Flat list of card dicts in aggregation order, one per entry.

    """
    resolved_ratings: list[dict[str, Any]] = danger_ratings or []
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

        # SNOW-291: add panel_title, time_period, subdivision, and level_number
        # to match the shape emitted by _problem_cards_from_render_model_traits.
        agg_time_period: str = agg_entry.get("validTimePeriod") or "all_day"
        agg_panel_title: str = agg_entry.get("title") or ""
        card["panel_title"] = agg_panel_title
        card["time_period"] = agg_time_period
        card["title_time_suffix"] = _title_time_suffix(agg_panel_title, agg_time_period)
        agg_subdivision: str = _subdivision_for_period(
            agg_time_period,
            resolved_ratings,
            _DANGER_ORDER[max(int(card["danger_level"]), 1) - 1],
        )
        card["subdivision"] = agg_subdivision
        card["subdivision_label"] = _SUBDIVISION_LABELS.get(agg_subdivision, "")
        card["level_number"] = (
            f"{card['danger_level']}{agg_subdivision}" if agg_subdivision else ""
        )

        cards.append(card)
    return cards


def build_problem_cards(
    raw_problems: list[dict[str, Any]],
    aggregation: list[dict[str, Any]],
    danger_ratings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build one flat presentation card per aggregation entry, in aggregation order.

    Both ``raw_problems`` and ``aggregation`` are expected to be present
    whenever the bulletin carries avalanche problems. Missing either returns
    an empty list; callers (``_resolve_problem_cards``) fall back to the
    render-model traits in that case. Schema violations (missing category,
    empty problemTypes, unresolved problem type) are caught and logged.

    Args:
        raw_problems: The CAAML ``avalancheProblems`` array.
        aggregation: The ``customData.CH.aggregation`` array.
        danger_ratings: Projected ``danger.ratings`` list from the render
            model. Forwarded to ``_problem_cards_from_aggregation`` for
            per-period subdivision lookup (SNOW-291).

    Returns:
        List of flat card dicts in aggregation order, or empty list on error.

    """
    if not raw_problems:
        # Empty avalancheProblems is normal on quiet days and for any
        # bulletin whose risk is described purely in prose. Callers fall
        # back to the render-model traits when this returns [].
        return []
    if not aggregation:
        # ALBINA bulletins never carry customData.CH.aggregation — that's
        # source-specific to SLF. Callers (_resolve_problem_cards) fall back
        # to the render-model traits in that case.
        return []
    index = {p["problemType"]: p for p in raw_problems if p.get("problemType")}
    try:
        return _problem_cards_from_aggregation(aggregation, index, danger_ratings)
    except ValueError:
        logger.exception("build_problem_cards: unexpected aggregation schema")
        return []


def _resolve_problem_cards(
    traits: list[dict[str, Any]],
    danger_patterns: list[str] | None = None,
    danger_ratings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Resolve problem cards from render-model traits.

    Both SLF and ALBINA bulletins now source their problem cards from the
    enriched render-model traits list. The render model synthesises
    aggregation for all sources and records traits in editorial
    (aggregation) order, so trait ordering matches the previous SLF
    aggregation-driven ordering.

    Args:
        traits: Enriched render-model traits list.
        danger_patterns: Bulletin-level danger patterns list from the render
            model (``render_model["danger_patterns"]``). Each card receives
            the full list so the template can render pattern tags on every
            card. ``None`` and ``[]`` are both treated as no patterns.
        danger_ratings: Projected ``danger.ratings`` list from the render
            model. Used to look up the per-period subdivision suffix for
            each card (SNOW-291). ``None`` and ``[]`` produce empty
            subdivision strings on all cards.

    Returns:
        Flat list of card dicts ready for ``_rating_block.html``.

    """
    return _problem_cards_from_render_model_traits(
        traits, danger_patterns or [], danger_ratings or []
    )


def _subdivision_for_period(
    period: str,
    danger_ratings: list[dict[str, Any]],
    level_key: str = "",
) -> str:
    """
    Return the subdivision suffix for a time period and danger level.

    Scans the projected ``danger.ratings`` list for an entry whose ``period``
    matches the requested period token. Returns the first match's
    ``subdivision`` string (e.g. ``"-"``, ``"="``, ``"+"``) or ``""`` when no
    match is found.

    This is a best-effort lookup: the trait and the rating use the same
    ``time_period`` / ``period`` token (``"all_day"``, ``"earlier"``,
    ``"later"``), so a direct equality match is sufficient.

    ``level_key`` narrows the match to ratings at that danger level, and the
    caller must pass it whenever the suffix will be shown against a level
    number (SNOW-739). A period commonly carries several ratings — one per
    elevation band — and a card sitting below the day's peak must not borrow
    the peak's suffix: a level-1 wet card under a "2+" day would otherwise
    read "1+", asserting a within-band grading SLF never published for it.
    Omit it only where the suffix is not attributed to a level.

    Args:
        period: The trait's ``time_period`` token.
        danger_ratings: Projected ``danger.ratings`` list from the render model.
        level_key: Danger key the suffix must belong to (``"moderate"``,
            ``"very_high"``, …). Empty matches on period alone.

    Returns:
        Subdivision suffix string, or empty string when none is found.

    """
    for r in danger_ratings:
        if r.get("period") != period:
            continue
        if level_key and (r.get("key") or "") != level_key:
            continue
        return r.get("subdivision") or ""
    return ""


_TIME_PERIOD_ORDER: dict[str, int] = {"earlier": 0, "all_day": 1, "later": 2}


def _band_sort_key(band_id: str | None) -> int:
    """
    Return a sort key so the high-elevation band sorts before the low band.

    Bands with a numeric lower bound (``"above-{N}"``) sort by descending
    lower bound — the highest lower bound (highest band) comes first.
    Treeline bands sort: above-treeline → above-treeline (0), below-treeline
    (1), all-elevations (2). Unknown slugs fall after all-elevations.

    Args:
        band_id: The band ID slug, or None.

    Returns:
        An integer sort key (lower = rendered first).

    """
    if band_id is None or band_id == "all-elevations":
        return 10_000_000  # sort after any real band
    if band_id == "above-treeline":
        return -1
    if band_id == "below-treeline":
        return 0
    if band_id.startswith("above-"):
        try:
            return -int(band_id[6:])  # descending: higher pivot first
        except ValueError:
            pass
    if band_id.startswith("below-"):
        try:
            return int(band_id[6:])
        except ValueError:
            pass
    return 5_000_000


def _pivot_label(band_elevation: dict[str, Any] | None) -> str:
    """
    Return the pivot value string for a band's elevation (e.g. "2500 m").

    Used to build the pivot-migration sub-header when the band's earlier and
    later pivots differ.

    Args:
        band_elevation: A parsed elevation dict from the render model, or None.

    Returns:
        A human-readable pivot string (e.g. ``"2500 m"`` or ``"treeline"``).

    """
    if not band_elevation:
        return ""
    treeline: bool = band_elevation.get("treeline", False)
    if treeline:
        return _gettext("treeline")
    lower: int | None = band_elevation.get("lower")
    upper: int | None = band_elevation.get("upper")
    pivot = lower if lower is not None else upper
    if pivot is not None:
        return f"{pivot} m"
    return ""


def _build_band_time_subheader(
    earlier_pivot: str,
    later_pivot: str,
) -> str:
    """
    Build the pivot-migration prose sub-header, or empty string when unchanged.

    Returns a localised string of the form
    "Wet line at {earlier_pivot} earlier, {later_pivot} later"
    only when the two pivots differ.  When they are the same (or either is
    blank) an empty string is returned so the template hides the sub-header.

    Args:
        earlier_pivot: Pivot label for the earlier period (e.g. ``"2500 m"``).
        later_pivot: Pivot label for the later period (e.g. ``"2800 m"``).

    Returns:
        Localised sub-header string, or empty string.

    """
    if not earlier_pivot or not later_pivot or earlier_pivot == later_pivot:
        return ""
    return _gettext("Wet line at %(earlier)s earlier, %(later)s later") % {
        "earlier": earlier_pivot,
        "later": later_pivot,
    }


def _title_time_suffix(panel_title: str, time_period: str) -> str | Promise:
    """
    Return the window suffix for a card's title bar, or "" when redundant.

    The title bar reads "<provider title> &middot; <window>" — SLF's own
    wording for the trait, then the window it covers (SNOW-739). SLF names
    the window itself on some traits and not others ("Dry avalanches" beside
    "Dry avalanches, whole day"; "Wet-snow avalanches, as the day
    progresses"), always as a clause after a comma. A title carrying that
    clause already answers the question, so the suffix is suppressed rather
    than saying the window twice in two different vocabularies.

    The provider's title is never edited: it renders verbatim either way,
    which is what ``customData.CH.aggregation[].title`` is declared as in
    tests/sentinels/fidelity.py.

    Args:
        panel_title: The trait's title, in the provider's words (may be "").
        time_period: The trait's ``time_period`` token.

    Returns:
        A lower-case window label, or "" when the title already names it.

    """
    if "," in panel_title:
        return ""
    return _TIME_PERIOD_TITLE_SUFFIXES.get(time_period, "")


def _build_single_trait_card(
    trait: dict[str, Any],
    normalised_patterns: list[dict[str, str]],
    resolved_ratings: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Build a single problem card dict from an enriched render-model trait.

    Returns ``None`` when the trait carries no problems (skip silently).

    Args:
        trait: An enriched trait dict from the render model.
        normalised_patterns: Pre-normalised danger-pattern list shared by all
            cards in the same bulletin (computed once by the caller).
        resolved_ratings: Projected ``danger.ratings`` list, used to look up the
            per-period subdivision suffix that feeds ``subdivision`` /
            ``level_number`` (SNOW-291). ``None`` produces empty subdivision.

    Returns:
        A card dict, or ``None`` when the trait has no problems.

    """
    resolved_ratings = resolved_ratings or []
    category: str = trait.get("category") or ""
    danger_level: int = trait.get("danger_level") or 1
    time_period: str = trait.get("time_period") or "all_day"
    time_period_label: str | Promise = _TIME_PERIOD_LABELS.get(time_period, "")
    problems: list[dict[str, Any]] = trait.get("problems") or []
    if not problems:
        return None
    first = problems[0]
    problem_labels = [
        str(
            _PROBLEM_LABELS.get(
                p.get("problem_type", ""),
                p.get("problem_type", "").replace("_", " ").capitalize(),
            )
        )
        for p in problems
    ]
    label = " + ".join(problem_labels) if problem_labels else (trait.get("title") or "")
    max_danger_level = danger_level
    for p in problems:
        drv: str = p.get("danger_rating_value") or ""
        plevel = _DANGER_RATING_INT.get(drv, 0)
        if plevel > max_danger_level:
            max_danger_level = plevel
    # SNOW-673. Guidance is enriched onto each *problem*; the card is composed
    # from the trait, so it has to be carried up or it never reaches a
    # template — which is exactly why it went unrendered for so long.
    #
    # A list rather than a single string because a trait can hold more than
    # one problem type, and the card's label merges them ("Wind slab + New
    # snow"). Every other spatial field here takes `first` and ignores the
    # rest; doing that with guidance would attribute one problem's note to a
    # card naming two. Deduplicated by problem type, in trait order.
    seen_guidance: set[str] = set()
    field_guidance: list[dict[str, str]] = []
    for p in problems:
        note = p.get("field_guidance")
        ptype = p.get("problem_type") or ""
        if not note or ptype in seen_guidance:
            continue
        seen_guidance.add(ptype)
        field_guidance.append(
            {
                "label": str(
                    _PROBLEM_LABELS.get(ptype, ptype.replace("_", " ").capitalize())
                ),
                "text": note,
            }
        )

    frequency_raw: str | None = first.get("frequency") or None
    stability_raw: str | None = first.get("snowpack_stability") or None
    frequency_label: Promise | None = _FREQUENCY_LABELS.get(frequency_raw or "")
    stability_label: Promise | None = _STABILITY_LABELS.get(stability_raw or "")
    avalanche_size_raw: int | None = first.get("avalanche_size")
    avalanche_size_label: Promise | None = (
        _AVALANCHE_SIZE_LABELS.get(avalanche_size_raw)
        if avalanche_size_raw is not None
        else None
    )
    # SNOW-291: editorial panel title from the trait and per-period subdivision
    # suffix from the projected danger.ratings list.
    panel_title: str = trait.get("title") or ""
    danger_key: str = _DANGER_ORDER[max(max_danger_level, 1) - 1]
    # The suffix is looked up at the card's own level, not the day's peak —
    # see _subdivision_for_period. The card renders it beside that level.
    subdivision: str = _subdivision_for_period(
        time_period, resolved_ratings, danger_key
    )
    # level_number combines the danger integer with the subdivision suffix
    # (e.g. "2-", "2=", "2+") — only set when the card carries a subdivision
    # (SLF). Empty for ALBINA and MeteoFrance cards so the chip is suppressed.
    level_number: str = f"{max_danger_level}{subdivision}" if subdivision else ""
    return {
        "category": category,
        "danger_level": max_danger_level,
        "danger_level_key": danger_key.replace("_", "-"),
        "label": label,
        "time_period_label": time_period_label,
        "time_period": time_period,
        "panel_title": panel_title,
        "title_time_suffix": _title_time_suffix(panel_title, time_period),
        "subdivision": subdivision,
        "subdivision_label": _SUBDIVISION_LABELS.get(subdivision, ""),
        "level_number": level_number,
        "aspects": _sort_aspects_clockwise(first.get("aspects") or []),
        "elevation": first.get("elevation"),
        "comment_html": first.get("comment_html") or "",
        "core_zone_text": first.get("core_zone_text") or "",
        "hide_comment": False,
        "avalanche_type": first.get("avalanche_type"),
        "avalanche_size": avalanche_size_raw,
        "avalanche_size_label": avalanche_size_label,
        "frequency_label": frequency_label,
        "stability_label": stability_label,
        "danger_patterns": normalised_patterns,
        "prose_mentions_spatial": first.get("prose_mentions_spatial", False),
        "field_guidance": field_guidance,
    }


def _cards_for_band(
    bid: str,
    band_traits_list: list[dict[str, Any]],
    band_label_str: str,
    subheader: str,
    normalised_patterns: list[dict[str, str]],
    resolved_ratings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build the card list for a single ALBINA elevation band.

    Stamps ``band_id``, and (on the first card only) ``band_label`` and
    optionally ``time_subheader``.

    Args:
        bid: The band ID slug.
        band_traits_list: Traits belonging to this band (already sorted).
        band_label_str: Human-readable label for the band (e.g. "Above 2200 m").
        subheader: Pivot-migration sub-header or empty string.
        normalised_patterns: Pre-normalised danger patterns shared by all cards.
        resolved_ratings: Projected ``danger.ratings`` list, threaded through to
            each card for the SNOW-291 per-period subdivision lookup.

    Returns:
        List of card dicts for this band.

    """
    cards: list[dict[str, Any]] = []
    is_first = True
    for trait in band_traits_list:
        card = _build_single_trait_card(trait, normalised_patterns, resolved_ratings)
        if card is None:
            continue
        card["band_id"] = bid
        if is_first:
            card["band_label"] = band_label_str
            if subheader:
                card["time_subheader"] = subheader
            is_first = False
        cards.append(card)
    return cards


def _peak_danger_for_band(band_traits_list: list[dict[str, Any]]) -> int:
    """
    Return the maximum ``danger_level`` across all traits in an elevation band.

    Used as the primary sort key when ordering bands by descending danger so the
    highest-risk band card group leads the presentation.

    Args:
        band_traits_list: All traits belonging to a single band.

    Returns:
        The maximum ``danger_level`` integer found, or 0 when the list is empty.

    """
    return max((t.get("danger_level") or 0 for t in band_traits_list), default=0)


def _build_albina_band_cards(
    traits: list[dict[str, Any]],
    normalised_patterns: list[dict[str, str]],
    resolved_ratings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build the ordered card list for an ALBINA bulletin with elevation bands.

    Groups traits by ``band_id``, sorts bands by **descending peak danger**
    (so the most hazardous band renders first), breaking ties with
    ``_band_sort_key`` (highest elevation wins within equal-danger bands).
    Sorts within each band by time period (earlier → all_day → later), and
    stamps ``band_label`` and (when pivot migrates) ``time_subheader`` on the
    first card of each band.

    Args:
        traits: Enriched ALBINA traits list (all carry ``band_id``).
        normalised_patterns: Pre-normalised danger patterns shared by all cards.
        resolved_ratings: Projected ``danger.ratings`` list, threaded through to
            each card for the SNOW-291 per-period subdivision lookup.

    Returns:
        Flat list of card dicts in presentation order.

    """
    band_order: list[str] = []
    band_traits: dict[str, list[dict[str, Any]]] = {}
    band_elevations: dict[str, dict[str, Any] | None] = {}
    for trait in traits:
        bid: str = trait.get("band_id") or "all-elevations"
        if bid not in band_traits:
            band_order.append(bid)
            band_traits[bid] = []
            band_elevations[bid] = trait.get("elevation")
        band_traits[bid].append(trait)

    # Primary sort: descending peak danger (higher danger renders first).
    # Tie-break: _band_sort_key so equal-danger bands still render
    # high-elevation-first (matching the calendar's "high on top" convention).
    band_order.sort(
        key=lambda bid: (-_peak_danger_for_band(band_traits[bid]), _band_sort_key(bid))
    )
    for bid in band_order:
        band_traits[bid].sort(
            key=lambda t: _TIME_PERIOD_ORDER.get(t.get("time_period") or "all_day", 1)
        )

    band_subheaders = _build_band_subheaders(band_order, band_traits, band_elevations)

    cards: list[dict[str, Any]] = []
    for bid in band_order:
        band_label_str = band_label_for_elevation(band_elevations.get(bid))
        cards.extend(
            _cards_for_band(
                bid,
                band_traits[bid],
                band_label_str,
                band_subheaders.get(bid, ""),
                normalised_patterns,
                resolved_ratings,
            )
        )
    return cards


def _build_band_subheaders(
    band_order: list[str],
    band_traits: dict[str, list[dict[str, Any]]],
    band_elevations: dict[str, dict[str, Any] | None],
) -> dict[str, str]:
    """
    Build the pivot-migration sub-header string for the first band.

    In a 2×2 ALBINA bulletin the pivot itself migrates through the day (e.g.
    wet line at 2500 m earlier, 2800 m later).  The four traits carry FOUR
    DISTINCT band_ids — ``above-2500/earlier``, ``below-2500/earlier``,
    ``above-2800/later``, ``below-2800/later`` — so looking for a single band
    that has both ``earlier`` and ``later`` traits never matches.

    The correct approach is bulletin-level: gather the pivot (numeric or
    treeline label) associated with each time period by scanning ALL traits,
    then emit one sub-header attached only to the first band in ``band_order``
    (so it appears once, above the earliest card group).

    Args:
        band_order: Ordered list of band ID slugs (danger-descending,
            elevation-descending on ties).
        band_traits: Mapping of band_id → traits list.
        band_elevations: Mapping of band_id → parsed elevation dict.

    Returns:
        Dict of ``band_id → sub-header string`` (empty for all but the first
        band when pivots differ across periods; empty everywhere otherwise).

    """
    # Collect one pivot label per time-period across all bands.
    period_pivot: dict[str, str] = {}
    for bid in band_order:
        elevation = band_elevations.get(bid)
        if not elevation:
            continue
        for t in band_traits[bid]:
            period = t.get("time_period") or "all_day"
            if period not in period_pivot:
                label = _pivot_label(elevation)
                if label:
                    period_pivot[period] = label

    subheader = _build_band_time_subheader(
        period_pivot.get("earlier", ""), period_pivot.get("later", "")
    )

    # Attach the sub-header to the first band only; all others stay empty.
    result: dict[str, str] = {bid: "" for bid in band_order}
    if subheader and band_order:
        result[band_order[0]] = subheader
    return result


def _problem_cards_from_render_model_traits(
    traits: list[dict[str, Any]],
    danger_patterns: list[str] | None = None,
    danger_ratings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build one problem card per render-model trait.

    Used as a fallback for ALBINA bulletins which carry no
    ``customData.CH.aggregation``, so ``build_problem_cards`` returns [].
    The traits list comes from the **enriched** render model (already processed
    by ``enrich_render_model``), so elevation and label fields are already in
    the presentation-ready shape the ``_rating_block.html`` partial expects.

    One card is emitted per trait using the first problem in each trait for
    spatial data (aspects / elevation) — ALBINA aggregation entries always
    contain a single problem type per time-period group.

    For ALBINA bulletins the traits carry ``band_id`` and ``elevation`` keys
    (set in v7 of the render model).  This function groups those traits by
    band, sorts bands high-first, and sets ``band_label`` and (when the
    pivot migrates) ``time_subheader`` only on the **first card of each band**
    so the template renders one heading per band without forloop logic.
    SLF and MeteoFrance traits never carry ``band_id`` so the existing flat
    layout is unchanged for those sources.

    Args:
        traits: Enriched render model traits list.
        danger_patterns: Bulletin-level danger patterns (raw strings such as
            ``"DP1"``). Each card receives the normalised list so the template
            can render ``GM.N`` tags with tooltips. Pass ``None`` or ``[]`` for
            bulletins that carry no patterns (SLF, MeteoFrance).
        danger_ratings: Projected ``danger.ratings`` list from the render model.
            Used to look up the per-period subdivision suffix for each card
            (SNOW-291). ``None`` and ``[]`` produce empty subdivision strings.

    Returns:
        Flat list of card dicts, one per trait, in trait order.

    """
    normalised_patterns: list[dict[str, str]] = [
        _normalise_danger_pattern(p) for p in (danger_patterns or [])
    ]
    resolved_ratings: list[dict[str, Any]] = danger_ratings or []
    # Route to band layout only when at least one trait carries a real
    # elevation-specific band_id.  Constant-danger ALBINA bulletins store
    # band_id="all-elevations" (truthy but not a real split) — exclude that
    # sentinel so the flat card path handles them, producing no band headings.
    is_albina = any(
        t.get("band_id") and t.get("band_id") != "all-elevations" for t in traits
    )
    if not is_albina:
        return [
            c
            for c in (
                _build_single_trait_card(t, normalised_patterns, resolved_ratings)
                for t in traits
            )
            if c is not None
        ]
    return _build_albina_band_cards(traits, normalised_patterns, resolved_ratings)


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
        guidance: Field guidance dict from
            :func:`apps.public.guidance.load_field_guidance`.
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

    aspects: list[str] = _sort_aspects_clockwise(rm_problem.get("aspects") or [])
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
        "aspects": aspects,
        "label": label,
        "time_period_label": time_period_label,
        "elevation": elevation_bounds,
        "summary": summary,
        "field_guidance": field_guidance,
        "hide_comment": hide_comment,
        "danger_level_css": danger_level_css,
        "prose_mentions_spatial": detect_prose_spatial(comment_html),
    }


def enrich_render_model(
    render_model: dict[str, Any],
) -> dict[str, Any]:
    """
    Add presentation-ready fields to the render model's traits and problems.

    Converts raw render model problem dicts (int elevation bounds) into the
    richer shape ``_rating_block.html`` expects, adding labels, ElevationBounds,
    field_guidance, and hide_comment. Called from both the bulletin page view
    and the map drawer endpoint (``apps.public.api.region_summary``) so the two
    rendering paths share a single data shape.

    Args:
        render_model: A render model dict as produced by
            :func:`apps.bulletins.services.render_model.build_render_model`.

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
        logger.exception(
            "Bulletin %s render model rebuild failed during view render: %s",
            bulletin.bulletin_id,
            exc,
        )
        # Return error sentinel for this render only — do NOT write to DB.
        return {
            "version": 0,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }


def problem_cards_for_bulletin(bulletin: Bulletin) -> list[dict[str, Any]]:
    """
    Return the enriched problem cards for a bulletin.

    Pure extraction of the render-model → problem-cards steps
    ``_build_panel_context`` already runs, exposed as a named helper so
    other surfaces (the favourites card, SNOW-422) can reuse the exact same
    cards the bulletin page renders without duplicating the resolution
    logic.

    Args:
        bulletin: The Bulletin to resolve problem cards for.

    Returns:
        Flat list of card dicts ready for ``public/_rating_block.html``.

    """
    props = _get_properties(bulletin)
    raw_rm = _get_render_model(bulletin, props)
    render_model = enrich_render_model(raw_rm)
    traits = render_model.get("traits") or []
    rm_danger = raw_rm.get("danger") or {}
    return _resolve_problem_cards(
        traits, raw_rm.get("danger_patterns") or [], rm_danger.get("ratings") or []
    )


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

    # Retrieve or build the render model. Bulletins ingested before this
    # feature was deployed will have render_model_version == 0; build on
    # the fly so the page renders correctly while a backfill job catches up.
    raw_render_model = _get_render_model(bulletin, props)

    # Enrich the render model with presentation-ready fields (labels,
    # ElevationBounds, field_guidance, hide_comment per trait).
    render_model = enrich_render_model(raw_render_model)

    # Danger key and subdivision come from the render model projection.
    # The render model reads customData.CH.subdivision for SLF; ALBINA
    # and METEOFRANCE carry None. This avoids re-reading raw customData here.
    rm_danger: dict[str, Any] = raw_render_model.get("danger") or {}
    danger_key: str = rm_danger.get("key") or "low"
    danger_subdivision: str = rm_danger.get("subdivision") or ""
    if not danger_key or danger_key not in _DANGER_PANEL_META:
        logger.error(
            "_build_panel_context: %s has no usable danger key in render model",
            bulletin.pk,
        )
        danger_key = "low"
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

    traits: list[dict[str, Any]] = render_model.get("traits") or []

    # Per-half danger resolution for the AM/PM split headline. Reads the
    # projected danger.ratings list from the render model — one entry per
    # CAAML dangerRating with period, key, subdivision, and elevation.
    # Primary source is the projected list; traits are the fallback.
    # Also threaded into problem cards so each card can resolve its
    # per-period subdivision suffix (SNOW-291).
    rm_ratings: list[dict[str, Any]] = rm_danger.get("ratings") or []

    # Problem cards: both SLF and ALBINA bulletins are now served from
    # render-model traits. The render model synthesises aggregation for all
    # sources and records traits in editorial (aggregation) order, so trait
    # ordering matches the previous SLF aggregation-driven ordering.
    # Bulletin-level danger patterns (ALBINA only; [] for SLF/MeteoFrance)
    # are threaded through so each card can render GM.N annotation tags.
    # danger.ratings are passed so each card can carry panel_title and
    # subdivision (SNOW-291).
    rm_danger_patterns: list[str] = raw_render_model.get("danger_patterns") or []
    problem_cards = _resolve_problem_cards(traits, rm_danger_patterns, rm_ratings)
    morning_key, morning_subdivision = _resolve_period_danger_from_rm(
        rm_ratings, traits, _MORNING_PERIODS
    )
    afternoon_key, afternoon_subdivision = _resolve_period_danger_from_rm(
        rm_ratings, traits, _AFTERNOON_PERIODS
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
        "danger_source": "render_model.danger.key (highest)",
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
    panel["day_character"] = _resolve_day_lead(raw_render_model)
    panel["tendency_outlook"] = _resolve_tendency_outlook(raw_render_model)
    return panel


def _resolve_day_lead(raw_render_model: dict[str, Any]) -> DayCharacter:
    """
    Return the eyebrow callout to show above the rating blocks.

    All bulletin sources are classified via the five-rule cascade in
    ``compute_day_character`` (Stable / Manageable / Hard-to-read /
    Widespread / Dangerous).  ALBINA bulletins previously used
    ``tendency_lead`` prose as the callout; that prose now feeds the
    dedicated :func:`_resolve_tendency_outlook` block instead.

    Args:
        raw_render_model: The render model dict as returned by
            :func:`apps.bulletins.services.render_model.build_render_model` or
            retrieved from ``Bulletin.render_model``.

    Returns:
        A :class:`DayCharacter` with label and explainer fields populated.

    """
    return compute_day_character(raw_render_model)


def _resolve_tendency_outlook(
    raw_render_model: dict[str, Any],
) -> TendencyOutlook | None:
    """
    Build a :class:`TendencyOutlook` from the render model's tendency block.

    Returns ``None`` (no warning) when no tendency data is present or when
    ``tendency_type`` is ``None`` — the normal SLF / no-tendency case.

    For a non-``None`` but unrecognised ``tendency_type``, a warning is
    logged and a neutral fallback outlook is returned (no directional arrow,
    generic label) so the unknown value is surfaced rather than silently
    dropped.

    Args:
        raw_render_model: The render model dict as stored in
            ``Bulletin.render_model``.

    Returns:
        A :class:`TendencyOutlook` instance, or ``None`` when the block
        should be suppressed.

    """
    prose: dict[str, Any] = raw_render_model.get("prose") or {}
    tendency_list: list[dict[str, Any]] = prose.get("tendency") or []
    if not tendency_list:
        return None

    first: dict[str, Any] = tendency_list[0]
    tendency_type: str | None = first.get("tendency_type")
    if tendency_type is None:
        return None

    valid_until: str | None = first.get("valid_until")
    highlights: str = prose.get("tendency_lead") or ""

    if tendency_type not in _TENDENCY_ARROW:
        logger.warning(
            "_resolve_tendency_outlook: unknown tendency_type %r — neutral fallback",
            tendency_type,
        )
        return TendencyOutlook(
            tendency_type=tendency_type,
            arrow="",
            label=_("Avalanche danger outlook"),
            valid_until=valid_until,
            highlights=highlights,
        )

    return TendencyOutlook(
        tendency_type=tendency_type,
        arrow=_TENDENCY_ARROW[tendency_type],
        label=_TENDENCY_LABEL[tendency_type],
        valid_until=valid_until,
        highlights=highlights,
    )
