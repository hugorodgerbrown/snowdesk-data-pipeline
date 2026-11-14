"""
apps/public/views.py — Views for the public-facing bulletin site.

URL structure:
  /                                          Marketing homepage (static; no map JS).
  /map/                                      Interactive map (the app).
  /examples/random/                          Random bulletin (rendered inline).
  /examples/category/<danger_level>/         Random bulletin by danger level.
  /random/                                   Deprecated → /examples/random/.
  /<region_id>/                              Redirects to /<region_id>/<slug>/.
  /<region_id>/<slug>/                       Today's bulletin for a region.
  /<region_id>/<slug>/<date>/                Bulletin for a specific date.
  /<region_id>/season/                       Full-season page (up to 100 panels).

SNOW-344 collapsed the map onto ``/``; SNOW-1047 reversed that split —
``/`` is now the static homepage (``home()``) and the map lives at ``/map/``
(``map_page()``), which is where the ``?edit=`` editor modes are read.

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
from the bulletin's stored render model; sections with no available data are
omitted from the template context so the template hides them gracefully.
The render layer that does that lives in ``bulletin_panels.py``, and the
point-forecast context in ``weather_points.py`` (SNOW-1426).

A read path never reads CAAML and never rebuilds a render model (SNOW-1342;
docs/decisions/read-paths-never-read-raw-data.md); a ``Bulletin`` holds none
since SNOW-1337. The one raw reader here is the superuser / DEBUG raw embed
in ``_render_bulletin_page``, which reads the service's stored CAAML through
``apps.ingest.public.source_caaml`` and embeds nothing when it holds none.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import random
import unicodedata
import uuid
from typing import Any, cast

import waffle
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.db.models import Max, Prefetch
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    HttpResponseGone,
    HttpResponseNotModified,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.html import strip_tags
from django.utils.http import quote_etag
from django.utils.text import slugify
from django.utils.translation import (
    get_language,
    gettext,
    gettext_lazy as _,
    ngettext,
)
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    condition,
    require_http_methods,
)
from django.views.generic import RedirectView
from django_ratelimit.decorators import ratelimit

from apps import analytics
from apps.accounts.identity import request_identity
from apps.bulletins.models import (
    Bulletin,
    BulletinShare,
    BulletinShareClick,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.day_rating import (
    season_calendar_cache_key,
    target_day_for_valid_from,
)
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
)
from apps.bulletins.services.selection import (
    issues_for_date as _issues_for_date,
)
from apps.core.decorators import require_htmx
from apps.core.http import client_ip, is_speculative
from apps.core.services.request_log import capture as capture_request_log
from apps.core.sw_shell import inject_cache_version, served_cache_version
from apps.favourites.models import Favourite
from apps.ingest.public import (
    caaml_envelope,
    source_caaml,
)
from apps.locations.models import Location
from apps.public.errors import error_response
from apps.regions.cantons import canton_label
from apps.regions.models import MicroRegion, Resort

from . import competitor_matrix
from .bulletin_panels import (
    BULLETIN_SOURCE_LINKS,
    COUNTRY_NAMES,
    DANGER_ORDER,
    ELEVATION_LOWER,
    ELEVATION_UPPER,
    ElevationBounds,
    build_bulletin_body,
    build_resort_body,
    resolve_region_for_bulletin,
    resolve_selected_pick,
    rm_elevation_bounds,
)
from .component_previews import help_illustrations
from .daily_read import cached_daily_read, present_daily_read
from .decorators import lowercase_region_id
from .map_shared import (
    map_3d_view_enabled,
    map_modes_enabled,
    offline_audit_context,
)
from .season_calendar import (
    build_season_grid,
    build_season_months,
    season_header,
    summarise_season,
)
from .site_environment import PWAEnvironmentIdentity
from .sitemaps import (
    SITEMAP_FILES,
    SITEMAP_INDEX_CACHE_SECONDS,
    render_sitemap,
    render_sitemap_index,
    sitemap_cache_key,
)
from .templatetags.snowdesk_time import (
    danger_level_digit,
    danger_level_label,
)
from .weather_points import weather_point_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TendencyOutlook — directional outlook block (ALBINA tendency)
# ---------------------------------------------------------------------------


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
    # A bulletin with no rating (SNOW-1437): the provider did not assess the
    # day. It is not on the EAWS scale, so it carries no number, verdict or
    # colour, and the JSON-LD names it without a termCode.
    "no_rating": {
        "number": "",
        "label": _("No rating"),
        "verdict": "",
        "colour": "",
    },
}


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
      superuser, and a bulletin is present, the CAAML the ingest service
      stored for it (``source_caaml``, SNOW-1337), in its GeoJSON Feature
      envelope, is embedded as a ``<script type="application/json">`` tag
      so it is visible in the page source but invisible to the reader. An
      instance whose service holds no such bulletin embeds nothing.

    Args:
        request: The incoming HTTP request.
        context: The template context (this helper adds ``caaml_json``
            when appropriate but leaves the rest untouched).
        bulletin: The bulletin being rendered, or ``None`` for empty-state pages.

    Returns:
        The rendered ``HttpResponse`` with the debug header (and, when
        DEBUG=True or request.user.is_superuser, the raw-data script tag)
        attached.

    """
    if bulletin is not None and (settings.DEBUG or request.user.is_superuser):
        # One query, for this audience only. Escape ``</`` so a stray
        # ``</script>`` substring in the CAAML payload cannot terminate the
        # embedding ``<script>`` tag. JSON decodes ``\/`` to ``/`` so
        # round-tripping with JSON.parse is unaffected.
        caaml = source_caaml(bulletin.bulletin_id)
        if caaml is not None:
            raw = json.dumps(caaml_envelope(caaml), ensure_ascii=False).replace(
                "</", "<\\/"
            )
            context = {**context, "caaml_json": raw}
    response = render(request, "public/bulletin.html", context)
    if bulletin is not None:
        response["X-Bulletin-Id"] = str(bulletin.bulletin_id)
    return response


def _resolve_selected_issue(
    issues: list[Bulletin],
    target_date: datetime.date,
    requested_id: str | None,
) -> Bulletin | None:
    """
    Resolve which issue should render, without the skipped one.

    Args:
        issues: The bulletins forecasting ``target_date``.
        target_date: Calendar date identifying the day being displayed.
        requested_id: The ``?issue`` query param value, or ``None``.

    Returns:
        The issue :func:`resolve_selected_pick` chooses.

    """
    return resolve_selected_pick(issues, target_date, requested_id).bulletin


def _get_nav_dates(
    region: MicroRegion,
    current_date: datetime.date,
) -> tuple[datetime.date | None, datetime.date | None]:
    """
    Return calendar-adjacent prev/next dates for day-by-day navigation.

    Navigation steps one calendar day at a time regardless of whether a
    bulletin exists for the adjacent day — users can reach empty-state pages
    by navigating.  The upper bound is tomorrow (dates further in the future
    offer no next link); the lower bound is the region's earliest rated
    day, its oldest ``RegionDayRating`` row (once the user reaches that
    day, no earlier prev link is offered).

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

    oldest_date = RegionDayRating.objects.earliest_date(region)
    prev_date: datetime.date | None = None
    if oldest_date is not None and current_date > oldest_date:
        prev_date = current_date - datetime.timedelta(days=1)

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


# Query parameters that say where a visitor came from rather than what they
# asked to see. A bare ``/`` with only these renders the homepage; any other
# parameter was meant for the map, which lived at ``/`` until the homepage
# took it, so the request is sent on to ``/map/`` with its query intact.
# static/js/sw.js carries the same two lists (ATTRIBUTION_PARAMS /
# ATTRIBUTION_PARAM_PREFIXES) so the worker can answer the same redirect
# offline; keep them equal (tests/public/test_homepage.py checks).
_ATTRIBUTION_PARAM_PREFIXES = ("utm_",)
_ATTRIBUTION_PARAMS = frozenset({"ref", "fbclid", "gclid", "mc_cid", "mc_eid"})


def _carries_map_state(request: HttpRequest) -> bool:
    """Return True when the query string holds anything but attribution.

    Every parameter the map reads (``?d=``, ``?panel=``, ``?favourite=``,
    ``?route_share=``, ``?trip=``, ``?edit=``, ``?layers=``, …) was minted
    against ``/`` while the map lived there. Listing them here would be a
    second copy that drifts the next time the map gains one, so the test is
    the other way round: anything that is not attribution is map state.

    Args:
        request: The incoming HTTP request.

    Returns:
        True when at least one non-attribution parameter is present.

    """
    return any(
        key not in _ATTRIBUTION_PARAMS
        and not key.startswith(_ATTRIBUTION_PARAM_PREFIXES)
        for key in request.GET
    )


# SNOW-1454: the camera the homepage's 3D card opens the map on, as
# ``?at=lng,lat,zoom,pitch,bearing`` (map_view_core.readCamera): looking
# up the Mattertal past Täsch and Zermatt to the Matterhorn, the view its
# desktop picture shows (Hugo's own framing, 7 Nov 2026). A bare ?view=3d
# would boot from the reader's stored viewport, which may lie outside
# Switzerland (then flat) or be nowhere at all (then z9 over the middle of
# the country).
HOME_3D_CAMERA = (7.748, 46.012, 10.9, 72, 197)


def _home_3d_url() -> str:
    """Build the map URL the homepage's 3D card opens (SNOW-1454).

    ``mode=terrain`` gives a reader in ``map_modes`` the Terrain layers and is
    ignored for everyone else; ``at=`` leaves the address bar once the map
    boots, so a copied link carries only ``mode`` and ``view``.

    Returns:
        ``/map/?mode=terrain&view=3d&at=…`` framed on ``HOME_3D_CAMERA``.

    """
    at = ",".join(f"{value:g}" for value in HOME_3D_CAMERA)
    return f"{reverse('public:map')}?mode=terrain&view=3d&at={at}"


# The same shape as the /observations/ redirect in urls.py: Django appends
# the incoming query string unchanged to a target it resolves itself.
_MAP_REDIRECT = RedirectView.as_view(
    pattern_name="public:map", permanent=True, query_string=True
)


def home(request: HttpRequest) -> HttpResponseBase:
    """
    Render the marketing homepage at ``/``.

    A screenshot of the map, a pitch, and short sections on what Snowdesk
    gives a reader, with a link into the app at ``/map/``. It loads no map
    JavaScript. Under the hero sits the one dynamic section, the daily read
    (SNOW-1357, ``apps/public/daily_read.py``), cached for 15 minutes and
    absent on a day no region is rated. Above it, for a reader who can open
    the map in 3D, the 3D card (SNOW-1454) links to ``map_3d_url``.

    The map was served here until the homepage took ``/``, so bookmarks and
    shared links carry map state on ``/`` (``/?d=…``, ``/?panel=reports``).
    Any request whose query string holds more than attribution parameters is
    permanently redirected to ``/map/`` with the query string preserved.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered homepage, or a 301 to ``/map/`` for map-state requests.

    """
    if _carries_map_state(request):
        return _MAP_REDIRECT(request)
    # SNOW-1357: the daily read under the hero, built only once the request
    # is known to be the homepage's (a map-state 301 never reads it).
    daily_read = present_daily_read(cached_daily_read(timezone.localdate()))
    # SNOW-1454: the 3D card under the hero, for readers who can open the
    # map in 3D (map_3d, SNOW-1451); /map/?view=3d opens flat for everyone
    # else (SNOW-1450). map_3d_view_enabled short-circuits an anonymous
    # request, so most visitors pay no waffle query for it.
    show_3d = map_3d_view_enabled(request)
    return render(
        request,
        "public/home.html",
        {
            "daily_read": daily_read,
            "show_3d": show_3d,
            "map_3d_url": _home_3d_url() if show_3d else "",
        },
    )


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


def compare(request: HttpRequest) -> HttpResponse:
    """
    Render the /compare page (SNOW-836).

    A public, reader-facing comparison of the avalanche apps in this
    category, derived from ``docs/competitors.md`` — the weekly competitor
    scan — with everything written from our own side of the table removed:
    the feature backlog, the scan process, and the reasoning about where we
    are exposed. What is left answers the reader's question instead, which
    is which of these to install.

    Prose is authored directly in the template. The one piece of context is
    the feature matrix from ``apps.public.competitor_matrix`` — data rather
    than table markup because it is 91 cells, and because the honesty
    properties the page rests on (no competitor marked absent on evidence we
    do not have; our own column not a clean sweep) are checkable there and
    nowhere else.

    Both are static: nothing here reads the database. The page states what
    each product does on a stated check date, and a figure assembled at
    request time from our own tables would be a different kind of claim —
    one the reader could not date, and one that would silently disagree with
    every hand-checked line beside it.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered comparison page.

    """
    return render(
        request,
        "public/compare.html",
        {
            "matrix_products": competitor_matrix.PRODUCTS,
            "matrix_rows": competitor_matrix.rows(),
            "matrix_legend": [
                (
                    competitor_matrix.SUPPORT_GLYPHS[support],
                    competitor_matrix.SUPPORT_LABELS[support],
                )
                for support in competitor_matrix.Support
            ],
        },
    )


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
    the manage-page sync-log panel, and ``what3words_visible`` (SNOW-840)
    the flag gating the three word address on a trip's meeting point —
    the two gated topics; every other section renders for everyone
    (SNOW-724; SNOW-1377 took the problem-terrain topic's flag off with
    the layer's). A reader is never shown documentation for a feature their
    own pages do not have. ``map_modes_visible`` (SNOW-1394) is the third
    gated topic, the Dated / Terrain toggle, read from ``map_modes_enabled``.

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
        "what3words_visible": waffle.flag_is_active(request, "what3words"),
        # SNOW-1394: the map-modes topic, with the toggle it describes.
        "map_modes_visible": map_modes_enabled(request),
    }
    context.update(help_illustrations())
    return render(request, "public/help.html", context)


# ---------------------------------------------------------------------------
# offline_page — what this device has saved (SNOW-930)
# ---------------------------------------------------------------------------


def offline_page(request: HttpRequest) -> HttpResponse:
    """
    Render the /offline/ page: what Snowdesk has saved on this device.

    Carries the offline-content audit, the reset-local-data control with
    its size breakdown, and the sync log behind the flag it already had.
    All three used to sit under the "This device" eyebrow on
    ``/account/settings/``, which redirects an unauthenticated visitor to
    sign-in — so the page you need when you have no signal was behind a
    login. Every reading on it is computed client-side from Cache Storage,
    IndexedDB and ``navigator.storage.estimate()``; there is no account in
    any of it. The one server-side read is the waffle flag gating the sync
    log, which is a flag lookup and nothing more.

    Public deliberately, and not merely as a convenience: the page can be
    warmed into the offline shell (``SHELL_PAGES``, ``static/js/sw.js``),
    which a login-gated page can never usefully be — the warm would fetch
    a redirect to sign-in.

    Its cached copy is still principal-partitioned like any other page's.
    ``base.html`` stamps every page with the reader's principal and
    page-side code reads it, so a public page is not an identity-neutral
    document; see
    ``docs/decisions/what-this-device-holds-is-a-public-page.md``.

    Context keys:
        sync_log_visible — True when the ``sync_log`` waffle flag is active
                            for this request (SNOW-482). ``flag_is_active``
                            handles ``AnonymousUser`` without a branch, as
                            ``help_page`` already relies on.
        default_basemap_key — the deployed default basemap
                            (``settings.BASEMAP``), the same value the map
                            page renders as ``data-default-basemap-key``
                            (SNOW-913). The report names the basemap the
                            reader is actually looking at, and a visitor who
                            has never opened the picker has written no
                            ``localStorage`` preference — so the only way to
                            know which style they will see is to be told the
                            default that stands in for it.
        terrain_class_tile_url — the terrain-class tile template
                            (``settings.TERRAIN_CLASS_TILE_URL``, empty
                            while ineligible), the same value the map
                            renders as ``data-terrain-class-tile-url``
                            (SNOW-1138).
        dem_tile_url — the 3D map's elevation tile template
                            (``settings.MAP_3D_DEM_TILE_URL``) for a reader
                            the map shows the 3D view to, empty otherwise
                            (SNOW-1453). The map pins elevation tiles only
                            where its 3D toggle renders, so the report
                            judges them on the same readers.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered offline-content page.

    """
    return render(
        request,
        "public/offline.html",
        {
            "sync_log_visible": waffle.flag_is_active(request, "sync_log"),
            **offline_audit_context(request, map_3d_view=map_3d_view_enabled(request)),
        },
    )


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
    re-downloaded the whole script. ``sw.js`` is ~4,700 lines. An ``ETag``
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

    SNOW-1025: nothing else about the deploy is written into the body. A
    build identity used to be (SNOW-933), so the update banner could name
    builds; it made the worker's bytes differ on every deploy, which
    installed a replacement worker even when no shell source had changed.
    The worker's bytes now change when the shell does, and (SNOW-1029, whose
    cache name carries the release label) once per production release.

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
            assignment. ``apps.core.checks`` catches it at ``manage.py
            check`` time so it cannot first appear in production.

    """
    response = _serve_sw_file("js/sw.js")
    body = response.content.decode("utf-8")

    version = served_cache_version()
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


# The install-sheet screenshots: file stem, form factor, pixel size, label.
# Chrome drops one whose size differs from the file, so these are the sizes
# bin/capture-pwa-screenshots writes.
PWA_SCREENSHOTS = (
    (
        "narrow-1-region",
        "narrow",
        "1170x2532",
        "Every region, rated: today's danger level on one map of the Alps",
    ),
    (
        "narrow-2-bulletin",
        "narrow",
        "1170x2532",
        "The whole bulletin: problems, aspects and elevations, in full",
    ),
    (
        "narrow-3-route",
        "narrow",
        "1170x2532",
        "Your routes, read: slope and aspect under every step of a GPX",
    ),
    (
        "narrow-4-resort",
        "narrow",
        "1170x2532",
        "Every ski resort: its forecast at base, mid and summit",
    ),
    (
        "narrow-5-weather",
        "narrow",
        "1170x2532",
        "Mountain weather: seven days for the place you're going",
    ),
    (
        "narrow-6-map",
        "narrow",
        "1170x2532",
        "Any day of the season: step back through the winter's bulletins",
    ),
    (
        "wide-1-region",
        "wide",
        "2560x1440",
        "Every region, rated: today's danger level on one map of the Alps",
    ),
    (
        "wide-2-bulletin",
        "wide",
        "2560x1440",
        "The whole bulletin: problems, aspects and elevations, in full",
    ),
    (
        "wide-3-route",
        "wide",
        "2560x1440",
        "Your routes, read: slope and aspect under every step of a GPX",
    ),
    (
        "wide-4-resort",
        "wide",
        "2560x1440",
        "Every ski resort: its forecast at base, mid and summit",
    ),
    (
        "wide-5-weather",
        "wide",
        "2560x1440",
        "Mountain weather: seven days for the place you're going",
    ),
)


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
    the icon set all key off
    ``apps.public.site_environment.PWAEnvironmentIdentity.from_settings()`` so
    a staging install lands on the home screen as ``Snowdesk (Staging)``
    with an amber icon set — visibly distinct from the production tile.
    Screenshots stay under ``/static/icons/pwa/screenshots/`` on every
    environment because they show the actual UI, which is identical.

    SNOW-1112: ``background_color`` comes from the same identity, and every
    icon and screenshot URL goes through the staticfiles storage so it carries a
    content hash in a deployed environment. Chrome re-mints an installed
    WebAPK only when a manifest icon URL changes, so an unhashed URL meant
    a redrawn icon never reached anyone who had already installed.

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
    manifest = {
        "name": identity.name_display,
        "short_name": identity.short_name,
        "id": f"{base}/",
        "lang": "en",
        # The first line of Chrome's install sheet, above the screenshots.
        # The homepage's own pitch, so the two never say different things.
        "description": (
            "Today’s avalanche bulletin and weather forecast for where "
            "you’re going in the Alps."
        ),
        "categories": ["weather", "sports", "travel"],
        # The installed app opens on the map, not the marketing homepage at
        # ``/``. ``id`` stays ``/`` — it is the installed app's identity, and
        # changing it would make every existing install a different app.
        "start_url": f"{base}/map/",
        "scope": f"{base}/",
        "display": "standalone",
        # SNOW-878 / SNOW-1112: the colour Chrome paints the generated
        # Android splash and its status bar in, which is --color-bg, the
        # page's own background. There is no per-scheme mechanism in the
        # manifest, so this stays the light value on every platform; the
        # dark launch screen is iOS-only, via the prefers-color-scheme
        # startup images (bin/build-pwa-splash).
        "background_color": identity.background_color,
        # Equal to background_color (SNOW-1112) — anything else is a status
        # bar colour flip at the moment the page first paints.
        "theme_color": identity.theme_color,
        "icons": [
            {
                "src": identity.icon_url("icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": identity.icon_url("icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                # The icon Android draws on its splash, cropped to a circle.
                "src": identity.icon_url("icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        # Captured and captioned by bin/capture-pwa-screenshots (SNOW-1232),
        # whose list of files this must match. Android shows only the narrow
        # ones, desktop Chrome only the wide ones.
        "screenshots": [
            {
                "src": static(f"icons/pwa/screenshots/{name}.webp"),
                "sizes": sizes,
                "type": "image/webp",
                "form_factor": form_factor,
                "label": label,
            }
            for name, form_factor, sizes, label in PWA_SCREENSHOTS
        ],
        # SNOW-1497: long-press the installed icon for the Today page, the
        # bulletin for each place the person follows. The phone app has no
        # tab for it (SNOW-1479).
        "shortcuts": [
            {
                "name": "Today",
                "url": f"{base}/today/",
                "icons": [
                    {
                        "src": identity.icon_url("icon-192.png"),
                        "sizes": "192x192",
                        "type": "image/png",
                    }
                ],
            }
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
        "# Snowdesk — daily avalanche bulletins from SLF, ALBINA and Météo-France"
        " (CAAML v6).",
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


def serve_sitemap_index(request: HttpRequest) -> HttpResponse:
    """
    Serve ``/sitemap.xml`` — the index of the three sitemap files (SNOW-1178).

    The index names ``/sitemap-main.xml``, ``/sitemap-regions.xml`` and
    ``/sitemap-resorts.xml`` (``SITEMAP_FILES``), so each can be cached for
    as long as its own content holds still and a crawler re-reads only the
    file that changed. It runs no query, so it needs no server cache; the
    edge holds it for ``SITEMAP_INDEX_CACHE_SECONDS``.

    Args:
        request: The incoming HTTP request (unused — the body is origin-keyed
            via ``SITE_BASE_URL``, not per-request).

    Returns:
        An ``HttpResponse`` with the ``application/xml`` sitemap index.

    """
    return _sitemap_response(render_sitemap_index(), SITEMAP_INDEX_CACHE_SECONDS)


def serve_sitemap(request: HttpRequest, name: str) -> HttpResponse:
    """
    Serve ``/sitemap-<name>.xml`` from the cache, rendering it on a miss (SNOW-1178).

    Django's ``sitemap`` view re-ran every section's query on every fetch,
    and SNOW-1121's bulletin section aggregates over every bulletin ever
    issued: the single live sitemap took ~3 s, slow enough for Search
    Console to report it as not fetched. ``render_sitemap`` builds a file
    once per its ``cache_seconds`` and a hit is a single cache read.

    The response carries ``Cache-Control: public`` for what remains of that
    lifetime — the body's age is cached with it — so the edge can hold it
    too without stacking a second full lifetime on the first. Possible only
    because the body is rendered
    without the request (see ``render_sitemap``), so nothing reads
    ``request.user`` and no ``Vary: Cookie`` is added.

    Args:
        request: The incoming HTTP request (unused).
        name: The file's name in the URL — a key of ``SITEMAP_FILES``.

    Returns:
        An ``HttpResponse`` with the ``application/xml`` sitemap body.

    Raises:
        Http404: When ``name`` is not a published sitemap file.

    """
    sitemap_file = SITEMAP_FILES.get(name)
    if sitemap_file is None:
        raise Http404("No such sitemap.")
    cache_key = sitemap_cache_key(name)
    now = timezone.now().timestamp()
    cached: tuple[float, bytes] | None = cache.get(cache_key)
    if cached is None:
        cached = (now, render_sitemap(name))
        cache.set(cache_key, cached, sitemap_file.cache_seconds)
    rendered_at, body = cached
    # The edge may hold the body only for what is left of the server entry's
    # lifetime, or the two caches stack and a change takes twice as long.
    max_age = max(0, sitemap_file.cache_seconds - int(now - rendered_at))
    return _sitemap_response(body, max_age)


def _sitemap_response(body: bytes, max_age: int) -> HttpResponse:
    """
    Wrap a rendered sitemap document in its public-cacheable response.

    ``X-Robots-Tag`` is the header Django's sitemap views set: a sitemap is
    for crawlers to read, not a page to list in results.

    Args:
        body: The rendered XML.
        max_age: How long the edge may serve it, in seconds.

    Returns:
        The ``application/xml`` response.

    """
    response = HttpResponse(body, content_type="application/xml")
    response["Cache-Control"] = f"public, max-age={max_age}"
    response["X-Robots-Tag"] = "noindex, noodp, noarchive"
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
        f"- [Home]({link('public:home')}): what Snowdesk is and what it "
        "covers; the site entry point.",
        f"- [Avalanche map]({link('public:map')}): interactive choropleth of "
        "current danger ratings by region.",
        f"- [Swiss ski resorts]({link('public:resorts')}): today's avalanche "
        "danger at the main Swiss resorts, and an index of every resort and "
        "touring area by canton, each linking to its own page.",
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
        "history, and list resorts in a region. Requires a Snowdesk account: "
        "clients connect with OAuth 2.1 (authorization code + PKCE) and send "
        "an `Authorization: Bearer` token (SNOW-1035).",
        f"- [Protected-resource metadata]({base}/.well-known/"
        "oauth-protected-resource/api/mcp/): RFC 9728 discovery document "
        "naming the authorization server for the MCP endpoint.",
        "",
        "## Legal",
        "",
        f"- [Privacy]({link('public:privacy')}): how account data is handled.",
        # SNOW-770: /terms/ now redirects here, so this one entry covers both.
        f"- [Terms of service]({link('public:terms_of_service')}): conditions "
        "of use, source-service attribution, each provider's own disclaimer, "
        "and Snowdesk's liability disclaimer.",
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

    The target is the environment's favicon, so a staging ``/favicon.ico``
    lands on the amber mark rather than the production one.
    """
    favicon = PWAEnvironmentIdentity.from_settings().favicon
    response = HttpResponseRedirect(static(favicon))
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

    # Match the select shape ``bulletin_detail`` uses so the core renders
    # at the same query budget the SNOW-13 monitor enforces. (The
    # ``neighbours`` prefetch went with the adjoining-regions section in
    # SNOW-806.)
    regions = list(
        MicroRegion.objects.filter(pk__in=region_ids).select_related("subregion")
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

    Picks a random bulletin, among the newest 200 whose ratings name the
    requested level (``Bulletin.danger_levels``), picks a region from that
    bulletin, and renders **the
    same view as the canonical bulletin route** with the matched bulletin
    pinned via ``requested_issue_id`` — guaranteeing the page actually
    reflects the requested danger level (not whatever the day's default
    issue would land on for that date).

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

    # The pool is the newest 200 bulletins whose ratings name this level,
    # filtered in SQL before the slice (SNOW-998). "The newest 200
    # bulletins" stopped being enough when SLF de-aggregated: one 2026/27
    # issue is ~149 bulletins, so 200 rows is barely more than one issue,
    # and a level SLF did not use in the last issue fell out of the pool.
    #
    # It reads the bulletin, not RegionDayRating: a region-day is rated
    # from its one latest bulletin, so a superseded issue's level (a
    # moderate evening issue replaced by a considerable morning one) never
    # reaches a rating row, and a filter over ratings would never find it.
    #
    # Since SNOW-1341 the filter is the ``danger_levels`` column — the
    # distinct levels of the render model's danger.ratings, stored as
    # "|moderate|considerable|" — not a LIKE over the CAAML cast to text,
    # so the page reads no CAAML. The delimiters make the match exact
    # ("|high|" is not inside "|very_high|"), so no Python confirmation is
    # needed. Unindexed on purpose: a LIKE '%…%' cannot use one.
    matching = list(
        Bulletin.objects.filter(danger_levels__contains=f"|{danger_key}|")
        .order_by("-issued_at")
        .only("pk", "bulletin_id", "target_date", "valid_from")[:200]
    )

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
    # The day the bulletin forecasts, not the UTC day its validity ends —
    # valid_to need not fall on the forecast day (SNOW-1344). The same
    # rule as compute_bulletin_grouping_boundary.
    target_date = bulletin.target_date or target_day_for_valid_from(bulletin.valid_from)
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
    Return the latest ``updated_at`` across bulletins forecasting this day.

    Keyed by (region, target_date). Drives the ``Last-Modified`` header
    and the ``@condition`` short-circuit. Returns ``None`` when no
    bulletins exist so the view still runs for the empty-state render.
    """
    target_date = _parse_target_date(date_str)
    result = Bulletin.objects.filter(
        regions__region_id__iexact=region_id,
        target_date=target_date,
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


def _rm_danger_rank(level: str, sub: str) -> tuple[int, int]:
    """Return a sortable rank for a danger level + projected subdivision display char.

    Band index from ``DANGER_ORDER`` is the primary key; the projected
    subdivision display char (``"+"``, ``"-"``, ``"="``, or ``""``) from
    ``danger.ratings[*].subdivision`` maps to an offset, so tuple comparison
    orders ``(2, -1) < (2, 0) < (2, 1) < (3, -1)``.
    """
    band = DANGER_ORDER.index(level) if level in DANGER_ORDER else 0
    offset = {"+": 1, "=": 0, "-": -1}.get(sub, 0)
    return (band, offset)


def _max_rm_rank_in(ratings: list[dict[str, Any]]) -> tuple[int, int]:
    """Return the highest ``_rm_danger_rank`` across the given ratings."""
    return max(
        (
            _rm_danger_rank(r.get("key") or "", r.get("subdivision") or "")
            for r in ratings
        ),
        default=(0, 0),
    )


def _rm_elevation_caption(rm_elev: dict[str, Any] | None) -> str:
    """Render a projected elevation dict as a short human caption.

    Thin wrapper over :func:`rm_elevation_bounds` returning just the formatted
    display string (``"below 2400m"``, ``"above treeline"`` …). Returns ``""``
    when no bounds survive.
    """
    return rm_elevation_bounds(rm_elev).display


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


# ---------------------------------------------------------------------------
# Weather (SNOW-761)
# ---------------------------------------------------------------------------


def _build_map_url(
    region_id: str, target_date: datetime.date, today: datetime.date
) -> str:
    """Return the context-aware map back-link URL for the bulletin nav bar.

    Omits ``?d=`` when *target_date* is today — the map scrubber defaults to
    today so the query string would be redundant. The URL fragment always
    carries the region ID so the map opens the region sheet at peek (SNOW-183).

    Resolves to ``/map/``, where the map has lived since the marketing
    homepage took ``/``.

    Args:
        region_id: The canonical EAWS region identifier (e.g. ``"CH-4115"``).
        target_date: Calendar day the bulletin page represents.
        today: Current date; used to decide whether to include ``?d=``.

    Returns:
        A relative URL string such as ``"/map/#CH-4115"`` or
        ``"/map/?d=2025-01-20#CH-4115"``.

    """
    base = reverse("public:map")
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
    # An unrated bulletin has no level number to show (SNOW-1437).
    number = (
        ""
        if panel.get("danger_key") == "no_rating"
        else str(panel.get("danger_number") or "")
    )
    raw_key_message = strip_tags(str(panel.get("key_message") or "")).strip()

    if label and number:
        prefix = gettext("Avalanche danger: %(label)s (%(number)s).") % {
            "label": label,
            "number": number,
        }
    elif label:
        prefix = gettext("Avalanche danger: %(label)s.") % {"label": label}
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


def _build_article_times(bulletin: Bulletin) -> tuple[str, str]:
    """
    Return the ``(published, modified)`` ISO-8601 timestamps for a bulletin.

    One derivation, two consumers: the JSON-LD ``Report``'s
    ``datePublished`` / ``dateModified`` and the ``article:published_time``
    / ``article:modified_time`` OG properties on the bulletin page
    (SNOW-555). Deriving them separately would let the structured data and
    the share card disagree about when a bulletin was issued, which is the
    one fact a reader most needs to trust — a bulletin's value is almost
    entirely a function of how recent it is.

    Published time is the row's own ``issued_at`` — the provider's
    ``publicationTime``, stored at ingest, so it is present on every row
    and needs neither the render model nor the CAAML (SNOW-1342).
    Modified time is the row's own ``updated_at``, so a re-issued bulletin
    reads as fresher than an untouched one.

    Both source values are timezone-aware (project invariant), so
    ``isoformat()`` already yields the offset-bearing form OG and
    schema.org expect.

    Args:
        bulletin: The ``Bulletin`` being rendered.

    Returns:
        A ``(date_published, date_modified)`` pair of ISO-8601 strings.

    """
    return bulletin.issued_at.isoformat(), bulletin.updated_at.isoformat()


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
    # Source organisation details (SLF / ALBINA / METEOFRANCE), from the
    # row's own ``source`` column — its values are BULLETIN_SOURCE_LINKS' keys.
    source_name, source_url = BULLETIN_SOURCE_LINKS.get(bulletin.source, ("", ""))

    # Major-region name for spatialCoverage.containedInPlace.
    major = region.subregion.major if region.subregion else None
    major_name = (major.name_en or major.name_native) if major else ""

    # Danger level label and numeric code.
    danger_key: str = panel.get("danger_key") or "no_rating"
    danger_meta = _DANGER_MAP.get(danger_key, _DANGER_MAP["no_rating"])
    danger_label = str(danger_meta["label"])
    danger_number = str(danger_meta["number"])

    # Shared with the article:* OG properties on the page — see
    # _build_article_times for why the derivation is not repeated here.
    date_published, date_modified = _build_article_times(bulletin)

    # ISO-8601 interval for temporalCoverage.
    temporal_coverage = (
        f"{bulletin.valid_from.isoformat()}/{bulletin.valid_to.isoformat()}"
    )

    # SNOW-394: spatialCoverage.geo from the region's centre, which
    # ``centre_point()`` reads off ``centroid_location`` (falling back to the
    # legacy ``centre`` column). It covers every real region; the None guard
    # keeps the field optional so a partially-seeded region (dev-only edge)
    # still renders rather than 500ing on a missing geo.
    spatial_coverage: dict[str, Any] = {
        "@type": "Place",
        "name": region.name,
        "containedInPlace": {
            "@type": "Place",
            "name": major_name,
        },
    }
    centre = region.centre_point()
    if centre is not None:
        spatial_coverage["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": centre[0],
            "longitude": centre[1],
        }

    report: dict[str, Any] = {
        "@type": "Report",
        "@id": f"{canonical_url}#report",
        "name": _bulletin_document_name(region),
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
    if not danger_number:
        # No rating is not a term of the EAWS scale (SNOW-1437).
        del report["about"]["termCode"]
        del report["about"]["inDefinedTermSet"]
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

    return _serialise_json_ld(payload)


def _bulletin_document_name(region: MicroRegion) -> str:
    """Name a region's bulletin page in JSON-LD, in the active language.

    Shared by the bulletin page's ``Report`` and the resort page's
    ``subjectOf`` so both name the same document the same way (SNOW-1128).

    Args:
        region: The bulletin's micro-region.

    Returns:
        ``"Avalanche bulletin — <region name>"``, translated.

    """
    return gettext("Avalanche bulletin — %(region)s") % {"region": region.name}


def _serialise_json_ld(payload: dict[str, Any]) -> str:
    r"""
    Serialise a JSON-LD payload for a ``<script type="application/ld+json">``.

    ``</`` is escaped to ``<\/`` so a stray ``</script>`` in any string
    field cannot end the embedding tag; JSON decoders read ``\/`` as ``/``.
    Shared by the bulletin and resort pages so the escape cannot drift
    between them (SNOW-1123).

    Args:
        payload: The structured-data dict.

    Returns:
        A JSON string safe for ``{{ …|safe }}`` inside the script tag.

    """
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _build_resort_structured_data(resort: Resort, canonical_url: str) -> str:
    """
    Build a schema.org ``SkiResort`` JSON-LD payload for the resort page.

    Ties the page to the named place people search for (SNOW-1123). A
    ``TOURING_TERRAIN`` row is typed ``Place`` instead — it has no lifts. Every
    optional property is omitted rather than emitted empty: ``geo`` needs
    both coordinates, ``alternateName`` and ``sameAs`` need a curated value,
    and ``address`` needs a canton. The region's evergreen bulletin is
    ``subjectOf`` — the resort is what that bulletin is about, and the
    bulletin stays the full document.

    Args:
        resort: The resort, with ``region__subregion__major`` and
            ``location`` selected.
        canonical_url: The absolute canonical URL of the resort page.

    Returns:
        A JSON string ready for ``{{ structured_data_json|safe }}``.

    """
    base = settings.SITE_BASE_URL.rstrip("/")
    region = resort.region
    payload: dict[str, Any] = {
        "@context": "https://schema.org",
        # Touring terrain (a pass, a side valley) has no lifts, so calling
        # it a SkiResort would be false structured data; it is a Place.
        "@type": ("SkiResort" if resort.kind == Resort.Kind.RESORT else "Place"),
        "@id": canonical_url,
        "name": resort.name,
        "url": canonical_url,
        "containedInPlace": {"@type": "Place", "name": region.name},
        "subjectOf": {
            "@type": "WebPage",
            "name": _bulletin_document_name(region),
            "url": f"{base}{region.get_absolute_url()}",
        },
    }
    if resort.name_alt:
        payload["alternateName"] = resort.name_alt
    # SNOW-1144: the pin, through the one accessor (transitional fallback to
    # the mirrored columns until SNOW-1147).
    pin = resort.pin_coordinates
    if pin is not None:
        payload["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": pin[0],
            "longitude": pin[1],
        }
    if resort.canton:
        address: dict[str, Any] = {
            "@type": "PostalAddress",
            "addressRegion": resort.canton,
        }
        major = region.subregion.major if region.subregion else None
        if major is not None and major.country:
            address["addressCountry"] = major.country
        payload["address"] = address
    if resort.website:
        payload["sameAs"] = resort.website
    return _serialise_json_ld(payload)


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
        page=analytics.page_of(request),
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
    ``resolve_region_for_bulletin``) and parsing ``target_date``.
    ``requested_issue_id`` overrides the default issue (the day's latest)
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
            the active issue tab; falls back to the day's latest issue when
            ``None``.
        canonical_is_today: When ``True``, advertise the form-2
            "today" canonical URL; when ``False``, advertise the
            form-3 dated canonical URL.

    Returns:
        The rendered bulletin page (or empty-state page when no issue
        covers the target day).

    """
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

    # Collect every issue that forecasts the target day and pick the one
    # the caller asked for; otherwise fall back to the day's latest
    # issue (SNOW-1207). Multi-issue days render the requested (or default) issue
    # as the page body.
    issues = _issues_for_date(region, target_date)
    selected, skipped = resolve_selected_pick(issues, target_date, requested_issue_id)

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
                # SNOW-1165: the empty state needs it as much as a populated
                # page does — another day's empty page still links back.
                "is_today": target_date == today,
                "prev_date": prev_date,
                "next_date": next_date,
                "year": datetime.date.today().year,
                "season_calendar": season_header(region, today),
                "canonical_url": canonical_url,
                "map_url": map_url,
            },
            bulletin=None,
        )
        # Empty-state: cache briefly so a freshly-ingested bulletin surfaces
        # within a minute without re-running the view on every pageview.
        patch_cache_control(response, public=True, max_age=60)
        return response

    # No ``viewer`` (SNOW-1480): this page is publicly cached, so it marks
    # nobody's report "You" — only the map's no-store region sheet does.
    body = build_bulletin_body(request, region, page_date, selected, skipped)
    panel = body["panel"]
    next_update_time = body["next_update_time"]
    season_calendar = season_header(region, today)

    # Emit bulletin_viewed (no-ops silently on /examples/* paths). The page
    # only: the map's region sheet renders the same body without counting
    # a view (SNOW-1164).
    _track_bulletin_viewed(request, region, selected, panel)

    # SNOW-555: one derivation feeding both the JSON-LD Report below and the
    # article:* OG properties on the page.
    article_published_time, article_modified_time = _build_article_times(selected)

    context = {
        # The body (SNOW-1164): everything public/partials/_region_body.html
        # reads, from the one builder the map's region sheet also uses.
        **body,
        "region": region,
        "region_name": region_name,
        "region_id": region.region_id.lower(),
        "slug": slugify(region.name),
        "prev_date": prev_date,
        "next_date": next_date,
        "year": today.year,
        # Season heatmap — surfaced as a slide-down sheet (SNOW-117).
        "season_calendar": season_calendar,
        "subregion_name": subregion_name,
        # Canonical form-3 URL — see SNOW-99.
        "canonical_url": canonical_url,
        # Context-aware back-link for the nav bar (SNOW-183) and, since
        # SNOW-806, the page's one tail link: the bulletin is a document,
        # and everything that used to follow it — adjoining regions, resorts,
        # the reader's pins, the subscribe form — is a map object.
        "map_url": map_url,
        # OG description — plain-text summary for og:description / twitter:description
        # (SNOW-218).  Built from the panel's danger rating and key message.
        "og_description": _build_og_description(panel),
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
    region = resolve_region_for_bulletin(region_id)
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
    region = resolve_region_for_bulletin(region_id)
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


def _build_location_canonical_url(
    location: Location, observed_on: datetime.date
) -> str:
    """Build the absolute canonical URL for a location's weather page.

    The bare page for today (or no date), the ``?date=`` form for any other
    day — see ``location_weather``. Absolute from ``settings.SITE_BASE_URL``
    for the same reason ``_build_canonical_url`` is: a crawler arriving on
    a hosting alias must not be told that alias is canonical.

    Args:
        location: The location the page is for.
        observed_on: The day the page is showing.

    Returns:
        An absolute URL.

    """
    base = settings.SITE_BASE_URL.rstrip("/")
    path = location.get_absolute_url()
    if observed_on != timezone.localdate():
        path = f"{path}?date={observed_on.isoformat()}"
    return f"{base}{path}"


def location_weather_legacy_redirect(
    request: HttpRequest, location_id: int
) -> HttpResponse:
    """301 the pre-SNOW-797 ``/weather/<id>/`` shape to ``/weather/<short_id>/``.

    The integer form was the page's URL for its first weeks and was shared
    from the map's weather card, so it keeps a permanent redirect
    (``docs/decisions/no-integer-pks-in-urls.md``). Same visibility rule as
    the page — the public estate plus the reader's own pins — so a guessed
    pk still cannot confirm a stranger's private pin exists. ``?date=``
    rides along, so a shared dated link still opens on its day.

    Args:
        request: The incoming HTTP request.
        location_id: The Location's primary key, from the legacy URL.

    Returns:
        A 301 to the canonical page, or 404 when the pk is not visible.

    """
    location = get_object_or_404(
        Location.objects.visible_to(request.user), pk=location_id
    )
    query = request.META.get("QUERY_STRING", "")
    url = location.get_absolute_url()
    return redirect(f"{url}?{query}" if query else url, permanent=True)


def location_weather(request: HttpRequest, short_id: str) -> HttpResponse:
    """Render the full forecast for one location.

    THE PAGE THE MAP CARD HANDS OFF TO. Tapping a weather symbol opens a
    card with today's conditions and a "View forecast" link; this is what
    that link opens. It carries what the card deliberately does not — the
    week, and the hourly detail at full resolution.

    **The page is built around one question: which day are you planning
    for** (SNOW-789). Five regions, top to bottom — masthead, day picker,
    selected-day line, meteogram, provenance — where the picker is
    navigation and everything under it describes the one selected day. It
    replaced a layout that stated the same high and low three times over:
    once in the "Today" panel, once in the day cells, once in the outlook
    chart's shape.

    **The page is the map's weather sheet** (Hugo, 25 Oct 2026). It renders
    the sheet's content from the sheet's own context
    (:func:`weather_point_context`): the header, the day's summary line,
    the week, each day's linked strips (SNOW-1219) or figures, the
    provenance and the "Where this is" rows. Only the frame is its own —
    the ``<h1>``, the meta tags, the canonical link and, off today, the
    "Showing … · Back to today" strip.

    Three page shapes, discriminated by ``show_day_picker`` and never by
    the date:

      * a day with **rows after it** is a week: up to seven cells, each
        with its strips behind it (SNOW-1337);
      * a day with **no row after it** is one day: no picker, and its day
        line and meteogram render plainly;
      * **no row at all** is the "nothing recorded" message.

    It exists because most weather symbols had nowhere to point. A location
    linked to a resort could hand off to the resort page, but 461 of the
    estate's 540 public locations are ``MicroRegion`` centroids with no page
    of their own, so the link would have been absent on the large majority
    of taps.

    **Public estate, plus the reader's own pins.**
    ``Location.objects.visible_to(request.user)`` is ``public()`` — the
    same filter the map feed and the card use — widened by the locations
    this user's own ``Favourite`` rows reach. ``public()`` excludes
    favourite locations so a public feed cannot leak one, and that holds:
    a guessed id still cannot expose a *stranger's* pin, its coordinates
    or its elevation. But the favourite card links here for the week
    (SNOW-783), and refusing the owner their own pin's forecast would be
    reading the privacy contract backwards.

    ``date`` is an optional ISO-8601 query parameter so the page can follow
    the day the map was showing. A malformed value falls back to today
    rather than 400-ing.

    **Canonical URL** (SNOW-799). ``?date=`` picks which row the page
    shows, not which page it is, so the canonical is the bare
    ``/weather/<short_id>/`` when no date is given or the date is today,
    and the dated URL when another day is being viewed — that page is a
    real, citable thing, but it is not what the evergreen URL should point
    at. The sitemap lists only the undated form.

    Args:
        request: The incoming HTTP request.
        short_id: The Location's opaque short id (SNOW-797).

    Returns:
        The rendered page.

    Raises:
        Http404: The id is unknown, or belongs to neither the public
            estate nor this user's own favourites.

    """
    location = get_object_or_404(
        Location.objects.visible_to(request.user), short_id=short_id
    )

    observed_on = timezone.localdate()
    raw_date = request.GET.get("date", "")
    if raw_date:
        try:
            observed_on = datetime.date.fromisoformat(raw_date)
        except ValueError:
            logger.debug("location_weather: ignoring unparseable date %r", raw_date)

    # The sheet's own context (the sheet is the page, Hugo, 24 Oct 2026):
    # the same header, summary line, week, strips and "Where this is" rows.
    # The page adds only its frame — meta, canonical, the another-day strip.
    context: dict[str, Any] = {
        **weather_point_context(location, observed_on, timezone.now()),
        "canonical_url": _build_location_canonical_url(location, observed_on),
        "today_url": location.get_absolute_url(),
    }
    return render(request, "public/location_weather.html", context)


def resort_legacy_redirect(
    request: HttpRequest, resort_id: int, slug: str
) -> HttpResponse:
    """301 the pre-SNOW-796 ``/resorts/<id>/<slug>/`` shape to ``/resorts/<slug>/``.

    The integer form was indexed and bookmarked for months, so it keeps a
    permanent redirect (``docs/decisions/no-integer-pks-in-urls.md``). The
    inbound ``slug`` is ignored: the row's stored slug is the canonical one,
    and this route is the last place a resort's primary key is accepted.

    Args:
        request: The incoming HTTP request.
        resort_id: The Resort's primary key, from the legacy URL.
        slug: The legacy name-derived suffix — not checked.

    Returns:
        A 301 to the resort's canonical page, or 404 for an unknown pk.

    """
    # all_objects: a retired resort still redirects, and its page answers
    # 410 (SNOW-1404) — the old address learns the same as the new one.
    resort = get_object_or_404(Resort.all_objects, pk=resort_id)
    return redirect(resort.get_absolute_url(), permanent=True)


# SNOW-1150: the levels the resorts board lists, highest first. A rating key
# outside this tuple (``no_rating``) is not a level, so its resort goes in the
# board's "No bulletin" row.
_RESORT_BOARD_LEVELS: tuple[str, ...] = (
    RegionDayRating.Rating.VERY_HIGH,
    RegionDayRating.Rating.HIGH,
    RegionDayRating.Rating.CONSIDERABLE,
    RegionDayRating.Rating.MODERATE,
    RegionDayRating.Rating.LOW,
)


def _alphabetical_key(name: str) -> str:
    """Return a sort key that orders names A–Z the way a reader expects.

    The database's ordering is by code point, which puts "östliche
    Silvretta" after "Zermatt" and lower case after upper. Folding accents
    and case puts every name where a reader looks for it.

    Args:
        name: A resort name.

    Returns:
        The name, accent-stripped and case-folded.

    """
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _resort_board(core: list[Resort], ratings: dict[int, str]) -> list[dict[str, Any]]:
    """Group the CORE resorts by today's danger level, highest first.

    Each level with at least one resort is one row; a level nobody is at is
    omitted. A resort whose region has no rated row today goes in a final
    row with an empty ``level_key`` — the template's "No bulletin" row.
    Resorts keep the order they arrive in (A–Z) within a row.

    Args:
        core: The CORE-tier ski resorts, A–Z.
        ratings: Today's ``max_rating`` keyed by ``MicroRegion`` pk, for the
            regions that have a bulletin today.

    Returns:
        Ordered ``[{"level_key": str, "resorts": list[Resort]}]``.

    """
    by_level: dict[str, list[Resort]] = {key: [] for key in _RESORT_BOARD_LEVELS}
    unrated: list[Resort] = []
    for resort in core:
        key = ratings.get(resort.region_id, "")
        if key in by_level:
            by_level[key].append(resort)
        else:
            unrated.append(resort)
    board = [
        {"level_key": str(key), "resorts": by_level[key]}
        for key in _RESORT_BOARD_LEVELS
        if by_level[key]
    ]
    if unrated:
        board.append({"level_key": "", "resorts": unrated})
    return board


def _resort_index(resorts: list[Resort]) -> list[dict[str, Any]]:
    """Group ski resorts by canton for the full index.

    Cantons are ordered by how many resorts they hold, most first, ties by
    name; a compound code (``"BE/VS"``) is its own group, labelled with both
    names. Resorts keep the order they arrive in (A–Z) within a canton.

    Args:
        resorts: The ``RESORT``-kind rows, A–Z.

    Returns:
        Ordered ``[{"canton_label": str, "resorts": list[Resort]}]``.

    """
    by_canton: dict[str, list[Resort]] = {}
    for resort in resorts:
        by_canton.setdefault(canton_label(resort.canton), []).append(resort)
    ordered = sorted(
        by_canton.items(),
        key=lambda item: (-len(item[1]), _alphabetical_key(item[0])),
    )
    return [{"canton_label": label, "resorts": group} for label, group in ordered]


def _resorts_index_description(board: list[dict[str, Any]], total: int) -> str:
    """Build the ``/resorts/`` meta description from today's board.

    In season it names today's spread — "Today's avalanche danger at 46
    Swiss ski resorts: 2 High, 11 Considerable, …" — reading the same rows
    the board renders, so the two cannot disagree. Out of season (no rated
    row) it describes the index instead. Both stay under the ~155-character
    snippet budget ``_page_meta.html`` works to.

    Args:
        board: The rows from ``_resort_board``.
        total: How many resorts and touring areas the index lists.

    Returns:
        The translated description.

    """
    rated = [row for row in board if row["level_key"]]
    if not rated:
        return ngettext(
            "%(count)d Swiss ski resort or touring area: a link to its "
            "avalanche bulletin and mountain weather.",
            "%(count)d Swiss ski resorts and touring areas: links to each "
            "resort's avalanche bulletin and mountain weather.",
            total,
        ) % {"count": total}
    rated_count = sum(len(row["resorts"]) for row in rated)
    spread = ", ".join(
        f"{len(row['resorts'])} "
        f"{danger_level_label(int(danger_level_digit(row['level_key'])))}"
        for row in rated
    )
    return ngettext(
        "Today's avalanche danger at %(count)d Swiss ski resort: %(spread)s.",
        "Today's avalanche danger at %(count)d Swiss ski resorts: %(spread)s.",
        rated_count,
    ) % {"count": rated_count, "spread": spread}


def _build_resorts_index_structured_data(
    resorts: list[Resort], canonical_url: str
) -> str:
    """Build the ``/resorts/`` schema.org ``CollectionPage`` JSON-LD.

    ``mainEntity`` is an ``ItemList`` with one ``ListItem`` per resort page
    the index links to, each with an absolute URL — the machine-readable
    form of the hub the page exists to be (SNOW-1150).

    Args:
        resorts: Every resort and touring area, in the order to list them.
        canonical_url: The absolute URL of the ``/resorts/`` page.

    Returns:
        A JSON string ready for ``{{ structured_data_json|safe }}``.

    """
    base = settings.SITE_BASE_URL.rstrip("/")
    payload: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": canonical_url,
        "url": canonical_url,
        "name": gettext("Swiss ski resorts — avalanche conditions today"),
        "inLanguage": get_language() or "en-gb",
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(resorts),
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": position,
                    "url": f"{base}{resort.get_absolute_url()}",
                    "name": resort.name,
                }
                for position, resort in enumerate(resorts, start=1)
            ],
        },
    }
    return _serialise_json_ld(payload)


def resorts_index(request: HttpRequest) -> HttpResponse:
    """Render ``/resorts/`` — today's conditions board and the full index.

    The page is the hub every resort page hangs off (SNOW-1150): a crawler
    otherwise reaches a resort only through the sitemap, a bulletin page or a
    map pin, and the map has no text a crawler can follow. It is a public
    reference index for a search term, the same for every reader — not a
    saved-thing list (``docs/decisions/two-documents-and-a-map.md``).

    Three parts:

    * **The board** — the CORE ski resorts grouped by today's
      ``RegionDayRating.max_rating``, highest level first, with any CORE
      resort whose region has no bulletin today in a final "No bulletin"
      row. Read in ONE query, with the groupings endpoint's
      ``source_bulletin__isnull=False`` filter, so the board matches the map
      choropleth. The resort page renders from the bulletin's render model
      instead, which costs several queries per resort; around issue time
      the two can briefly disagree, exactly as the map and the resort page
      can.
    * **Out of season** — when no CORE region has a rated row today, the
      board is replaced by a plain statement and the CORE resorts A–Z with
      their elevation ranges. The state is decided by the data, not the
      calendar, and no season aggregate is shown.
    * **The index** — every ski resort grouped by canton, then the touring
      areas in their own section.

    Two queries whatever the number of resorts: the resorts, then today's
    ratings for the CORE regions.

    Args:
        request: The incoming HTTP request.

    Returns:
        The rendered page.

    """
    today = timezone.localdate()
    resorts = sorted(
        Resort.objects.select_related("region").order_by("name"),
        key=lambda resort: _alphabetical_key(resort.name),
    )
    ski_resorts = [r for r in resorts if r.kind == Resort.Kind.RESORT]
    touring = [r for r in resorts if r.kind == Resort.Kind.TOURING_TERRAIN]
    core = [r for r in ski_resorts if r.tier == Resort.Tier.CORE]

    ratings: dict[int, str] = dict(
        RegionDayRating.objects.filter(
            date=today,
            region_id__in={resort.region_id for resort in core},
            source_bulletin__isnull=False,
        ).values_list("region_id", "max_rating")
    )
    board = _resort_board(core, ratings)
    in_season = any(row["level_key"] for row in board)

    canonical_url = f"{settings.SITE_BASE_URL.rstrip('/')}{reverse('public:resorts')}"
    context = {
        "target_date": today,
        "in_season": in_season,
        "board": board,
        "core_resorts": core,
        "canton_groups": _resort_index(ski_resorts),
        "touring": [
            {"resort": resort, "canton_label": canton_label(resort.canton)}
            for resort in touring
        ],
        "page_description": _resorts_index_description(board, len(resorts)),
        "structured_data_json": _build_resorts_index_structured_data(
            resorts, canonical_url
        ),
    }
    return render(request, "public/resorts_index.html", context)


def resort_detail(
    request: HttpRequest, slug: str, date_str: str | None = None
) -> HttpResponse:
    """
    Render the public resort detail page.

    Gives a Resort its own indexable URL (``/resorts/<slug>/``, SNOW-796),
    cross-linking with the bulletin page (which lists "Resorts in this
    region" — see ``_bulletin_detail_response``) and the map's place sheet
    (``apps.map.place_sheet.resort_sheet_partial``), whose "View resort →"
    is the one link from the map to this page (SNOW-1161).

    The slug is stored and never regenerated, so there is no canonical-slug
    redirect to perform here: the URL either names a resort or it 404s.
    The pre-slug ``/resorts/<id>/<slug>/`` form is ``resort_legacy_redirect``.

    A resort has no avalanche danger of its own (Hugo, 25 Oct 2026,
    ``docs/decisions/a-resort-has-no-avalanche-danger.md``, which supersedes
    SNOW-1124's ``a-resort-page-answers-the-query.md``). The page is the
    resort's information and its weather: the seven-day strip for each of
    its Base, Mid and Summit points (SNOW-1157, ``_resort_forecast_points``),
    the curated facts, and one row naming the avalanche region the resort
    sits in, with no level, which links to that region's bulletin. The
    bulletin card, the day's problem cards and the season's danger calendar
    it carried until then are the region's, on the region's page.

    SNOW-1155 built the frame: a header with the eyebrow
    (``_resort_eyebrow``), the name, the tagline and two actions — save the
    resort, and see it on the map (``/map/?resort=<slug>``) — then the
    sections in a main column with the curated facts in a sidebar.

    Builds the same favourite context the place sheet does (``favourited`` /
    ``favourite_uuid`` / ``can_favourite``). The signed-out link is built by
    ``{% sign_in_href %}`` in the favourite partial, which names this page as
    ``next`` (SNOW-903), so no sign-in url rides in the context.

    SNOW-1164 split the page into a frame and a body: the body — the
    forecast, the facts and the region row — is ``build_resort_body``
    and ``public/partials/_resort_body.html``, which the map's place sheet
    renders too, so on a phone the sheet at full height IS this page. The
    page adds only the frame: the header with its eyebrow, title, tagline
    and actions, the metadata and the structured data.

    The page takes a day (``/resorts/<slug>/<YYYY-MM-DD>/``, SNOW-1164), the
    way the bulletin page does, so the map's sheet and this page can show
    the same day. The bare URL is today; the dated URL for today redirects
    to it, so today has one address, and any other day is ``noindex`` —
    it is reached from the map, never from search.

    Args:
        request: The incoming HTTP request.
        slug: The Resort's slug, from the URL.
        date_str: The ``YYYY-MM-DD`` segment of the dated URL, or None.

    Returns:
        The rendered resort page; a redirect for the dated URL of today;
        404 for an unknown resort or a malformed date; 410 for a retired
        one (SNOW-1404).

    """
    today = timezone.localdate()
    target_date = today
    if date_str is not None:
        try:
            target_date = datetime.date.fromisoformat(date_str)
        except ValueError as exc:
            raise Http404("Not a date") from exc
    resort = get_object_or_404(
        Resort.all_objects.select_related("region__subregion__major", "location"),
        slug=slug,
    )
    if resort.is_retired:
        # SNOW-1404: the page existed, so Gone rather than Not Found.
        return error_response(request, "410")
    if date_str is not None and target_date == today:
        return redirect(resort.get_absolute_url())

    can_favourite = False
    favourite = None
    if request.user.is_authenticated:
        can_favourite = True
        favourite = Favourite.objects.filter(user=request.user, resort=resort).first()

    context = {
        **build_resort_body(resort, target_date, is_staff=request.user.is_staff),
        "favourited": favourite is not None,
        "favourite_uuid": str(favourite.uuid) if favourite else "",
        "can_favourite": can_favourite,
        # SNOW-1155: the header's mono eyebrow — alternative name, canton,
        # country — each part omitted when blank.
        "resort_eyebrow": _resort_eyebrow(resort),
        # SNOW-1123: schema.org SkiResort, emitted in extra_head.
        "structured_data_json": _build_resort_structured_data(
            resort,
            f"{settings.SITE_BASE_URL.rstrip('/')}{resort.get_absolute_url()}",
        ),
    }
    return render(request, "public/resort.html", context)


def _resort_eyebrow(resort: Resort) -> str:
    """Compose the resort page header's eyebrow line (SNOW-1155).

    ``"Les 4 Vallées · Valais · Switzerland"``: the resort's alternative or
    marketing name, its canton (the English name of the region's EAWS major
    region — ``MajorRegion.name_en``, "Valais" for CH-4) and its country's
    English name. Each part is omitted when blank, so an uncurated resort
    in a major region SLF publishes no English name for reads just its
    country, and a resort with nothing to say renders an empty string.

    The country name comes from ``bulletin_panels.COUNTRY_NAMES``, the one
    code → name mapping the map tooltip and llms-full.txt already use; a
    code it does not know is omitted rather than shown raw.

    Args:
        resort: The resort, with ``region__subregion__major`` selected.

    Returns:
        The parts joined with a middle dot, or ``""`` when every part is
        blank.

    """
    subregion = resort.region.subregion if resort.region.subregion_id else None
    major = subregion.major if subregion is not None else None
    parts = [
        resort.name_alt.strip(),
        major.name_en.strip() if major is not None else "",
        COUNTRY_NAMES.get(major.country.upper(), "") if major is not None else "",
    ]
    return " · ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Share redirect (SNOW-217)
# ---------------------------------------------------------------------------


def _record_share_click(request: HttpRequest, share: BulletinShare) -> None:
    """Record one real share-link click: RequestLog, click row, fivebar goal.

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
    analytics.track(
        "share_link_clicked", distinct_id, share_props, page=analytics.page_of(request)
    )


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
    a ``RequestLog``, a ``BulletinShareClick``, and a fivebar goal event — for
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
    cache_key = season_calendar_cache_key(canonical_id, today_iso, get_language())
    cached_body: bytes | None = cache.get(cache_key)
    if cached_body is not None:
        return HttpResponse(cached_body)

    region = get_object_or_404(
        MicroRegion.objects.select_related("subregion"), region_id__iexact=region_id
    )
    grid = build_season_grid(region, today)
    # SNOW-1158: the shared month-shaped calendar, linked. No server ring —
    # the selected day is ringed client-side, so this body is one per day.
    response = render(
        request,
        "public/partials/_season_calendar.html",
        {
            "region": region,
            "season_months": build_season_months(grid, today),
            "season_summary": summarise_season(grid),
            "linked": True,
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
