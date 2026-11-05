"""
apps/public/bulletin_panels.py — The bulletin render layer, as a public API.

Turns a bulletin's stored render model into the panel context the bulletin
page, the map's region and place sheets, favourites and the MCP server all
render: ``build_bulletin_body`` (a region's day), ``build_resort_body`` (a
resort's day), ``problem_cards_for_bulletin`` (one bulletin's problem cards)
and the label tables, day-window builders and ``ElevationBounds`` behind
them.

Moved out of ``apps/public/views.py`` by SNOW-1426 so other surfaces import
named functions instead of a view module's privates
(docs/decisions/public-splits-by-surface.md). Nothing here takes a request
except ``build_bulletin_body`` / ``build_resort_body``, which read it for the
reader's language and alert state.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, cast

import waffle
from django.http import (
    HttpRequest,
)
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.functional import Promise
from django.utils.html import strip_tags
from django.utils.translation import (
    gettext,
    gettext_lazy as _,
)

from apps.bulletins.models import (
    Bulletin,
    RegionDayRating,
)
from apps.bulletins.schema import ValidTimePeriod
from apps.bulletins.services import alert_wording, alerts as alerts_service, day_summary
from apps.bulletins.services.bulletin_change import summarise_change
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    DayCharacter,
    PeriodTransition,
    band_label_for_elevation,
    compute_day_character,
    detect_prose_spatial,
    problem_types_for,
)
from apps.bulletins.services.selection import (
    IssuePick,
    select_default_pick,
)
from apps.ingest.public import (
    derived_of,
    period_transition_of,
    state_of,
)
from apps.locations.models import Location, ResortLocation
from apps.observations.services.ground import ground_for_region
from apps.regions.models import MicroRegion, Resort
from apps.weather.models import Weather
from apps.weather.services.fetch import FORECAST_DAYS
from apps.weather.services.weather_display import build_point_forecast_panel

from .guidance import load_field_guidance
from .headlines import headline_for

logger = logging.getLogger(__name__)

# ISO 3166-1 alpha-2 → English country name mapping. Used by the region
# tooltip breadcrumb and the ?country= filter on the GeoJSON endpoints.
COUNTRY_NAMES: dict[str, str] = {
    "CH": "Switzerland",
    "FR": "France",
    "AT": "Austria",
    "IT": "Italy",
}

# ISO 3166-1 alpha-2 → avalanche provider label mapping. Used by the region
# tooltip to identify the upstream data publisher for permanently-uncovered
# regions. Météo-France retains its accent — it is a proper noun.
PROVIDER_BY_COUNTRY: dict[str, str] = {
    "CH": "SLF",
    "AT": "ALBINA",
    "IT": "ALBINA",
    "FR": "Météo-France",
}


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


def plain_text(html: str | None) -> str:
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


def resolve_selected_pick(
    issues: list[Bulletin],
    target_date: datetime.date,
    requested_id: str | None,
) -> IssuePick:
    """
    Resolve which issue should render given a user-requested override.

    When ``?issue=<uuid>`` names one of the day's issues, return that
    bulletin, built or not, with nothing skipped: the reader asked for it.
    Otherwise fall back to the shared pick, which prefers the latest issue
    that built (SNOW-1368, ``select_default_pick``). Silently ignores
    unknown / malformed IDs so stale bookmarks degrade to the default view
    rather than 404ing.

    Args:
        issues: The bulletins forecasting ``target_date``.
        target_date: Calendar date identifying the day being displayed.
        requested_id: The ``?issue`` query param value, or ``None``.

    Returns:
        The issue to render (``None`` when ``issues`` is empty) and the
        newer unbuilt issue passed over for it, if any.

    """
    if requested_id:
        for b in issues:
            if str(b.bulletin_id) == requested_id:
                return IssuePick(b, None)
    return select_default_pick(issues, target_date)


def _has_later_bulletin(region: MicroRegion, page_date: datetime.date) -> bool:
    """
    Return True when a bulletin exists that forecasts a day after *page_date*.

    Used on the today branch as a proxy for "a later bulletin has already
    been published for this region", replacing the old ``next_date is None``
    check.  Now that next_date always points to the adjacent calendar day,
    its presence no longer signals the absence of a later bulletin.

    It reads ``target_date``, the day a bulletin forecasts, not
    ``valid_to``: an issue whose validity runs past midnight but which
    forecasts today is not a later bulletin, and counting it hid the
    "next update" notice (SNOW-1344). A row with no ``target_date`` is
    ignored; ingest sets one on every bulletin.

    The check is a cheap EXISTS query; callers should short-circuit behind
    ``is_today`` so it only fires on the current-day page.

    Args:
        region: The MicroRegion whose bulletins are searched.
        page_date: The calendar day of the page being rendered.

    Returns:
        True if any bulletin for the region has ``target_date`` strictly
        after *page_date*.

    """
    return Bulletin.objects.filter(regions=region, target_date__gt=page_date).exists()


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
        if level not in DANGER_ORDER:
            continue
        by_period.setdefault(period, []).append(r)
    return by_period


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


def rm_elevation_bounds(rm_elev: dict[str, Any] | None) -> ElevationBounds:
    """Build :class:`ElevationBounds` for a projected elevation dict.

    Reconstructs the CAAML bounds via :func:`_rm_caaml_elevation` then defers
    to :func:`_format_elevation`, so the caption wording matches the
    problem-card aspect/elevation row. The returned object also carries a
    ``bound_type`` (``LOWER`` = "above", ``UPPER`` = "below", ``BOTH``), which
    the day-windows panel feeds to the ``elevation_icon`` filter to pick the
    mountain glyph for a banded row. Empty (falsey) when no bounds survive.
    """
    return _format_elevation(_rm_caaml_elevation(rm_elev))


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
    if level not in DANGER_PANEL_META:
        level = "low"
    suffix: str = rm_rating.get("subdivision") or ""
    number = DANGER_PANEL_META[level]["number"]
    return {
        "type": period,
        "level_key": level,
        "level_css": level.replace("_", "-"),
        "level_label": DANGER_PANEL_META[level]["label"],
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
        bounds = rm_elevation_bounds(r.get("elevation"))
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


def _build_day_windows(
    bulletin: Bulletin,
    render_model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Return the list[Window] consumed by the day-windows panel partial.

    Reads the projected ``danger.ratings`` list in the render model — never
    the raw CAAML ``dangerRatings`` (SNOW-1342).

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
        render_model: The render model dict (enriched or raw), whose
            ``danger.ratings`` list the rows are built from.

    """
    rm_ratings: list[dict[str, Any]] = []
    if render_model:
        rm_ratings = (render_model.get("danger") or {}).get("ratings") or []

    # Every model at the current version carries danger.ratings; a stale
    # row reads as the error sentinel and has none (SNOW-1342).
    return _day_windows_from_rm_ratings(rm_ratings) if rm_ratings else []


def _day_risk_profile_adds_to_cards(
    day_windows: list[dict[str, Any]],
    problem_cards: list[dict[str, Any]],
) -> bool:
    """
    Whether the Day Risk Profile says anything the problem cards do not.

    SNOW-1234: every problem card's title bar leads with its own level and
    names its window — "Considerable (3-) Wet-snow avalanches, as the day
    progresses" — which is how SLF and WhiteRisk give the level, with no
    day rating of their own. A profile row for a time window therefore
    repeats a card, and on a one-row day it also repeats the map sheet's
    peek tile, which is what Hugo found on 25 Oct 2026. The profile stays
    in two cases:

    - a row carries an elevation caption: ALBINA and Météo-France rate the
      danger by height rather than by problem ("Moderate below 2400 m,
      Considerable above"), which no single card's title can say — 63% and
      56% of their archived days;
    - there are no problem cards, so nothing else on the body states the
      level at all.

    Args:
        day_windows: Rows from :func:`_build_day_windows`.
        problem_cards: The panel's problem cards.

    Returns:
        True when the profile should render.

    """
    if not day_windows:
        return False
    if not problem_cards:
        return True
    return any(w.get("caption") for w in day_windows)


def resolve_region_for_bulletin(region_id: str) -> MicroRegion:
    """
    Look up a MicroRegion with the prefetches the bulletin page needs.

    ``select_related("subregion__major")`` pre-loads the parent EAWS L2 row
    (which the masthead's H2 reads) and its parent MajorRegion (which
    ``_build_structured_data`` uses for the JSON-LD ``spatialCoverage``
    ``containedInPlace`` field). Without the full chain, every bulletin
    pageview fires an extra SELECT on ``regions_majorregion`` (SNOW-13
    query-count monitor catches regressions). ``neighbours`` was prefetched
    here for the "Adjoining regions" tail section until SNOW-806 removed
    it — the page's tail is one link back to the map now, so the M2M is
    not read on this path at all.

    ``centroid_location`` is in the chain for ``MicroRegion.centre_point()``,
    which ``_build_structured_data`` calls for the JSON-LD ``geo`` block on
    every pageview; without it that FK is a second SELECT. SNOW-761 added it
    for the masthead's weather panel instead, and SNOW-784 removed that
    panel — the JOIN is still earned, just by a different reader.
    """
    return get_object_or_404(
        MicroRegion.objects.select_related("subregion__major", "centroid_location"),
        region_id__iexact=region_id,
    )


def _build_bulletin_headline(headline_inputs: dict[str, str]) -> str:
    """
    Return the data-driven headline for a bulletin's render model (SNOW-249).

    The five inputs of the variant matrix — source, partition type, peak
    rating, direction and problem family — are projected by the service
    and stored in the model's ``derived.headline_inputs`` (SNOW-1341); only
    the copy, which is localised, is looked up here with
    :func:`apps.public.headlines.headline_for`. The resort page read it too
    (SNOW-1124) until SNOW-1203 gave its card the Day Risk Profile rows.

    Args:
        headline_inputs: ``derived_of(render_model)["headline_inputs"]``.

    Returns:
        The translated headline string.

    """
    return headline_for(
        headline_inputs["source"],
        headline_inputs["partition_type"],
        headline_inputs["peak_rating"],
        headline_inputs["direction"],
        headline_inputs["family"],
    )


def build_bulletin_body(
    request: HttpRequest,
    region: MicroRegion,
    page_date: datetime.date,
    selected: Bulletin | None,
    skipped: Bulletin | None = None,
) -> dict[str, Any]:
    """
    Build what ``public/partials/_region_body.html`` reads for one bulletin.

    SNOW-1164: the region's body is rendered in two containers, the bulletin
    page and the map's region sheet, and this is the one place either gets
    its context, so the two cannot describe the same day differently. The
    page adds its chrome (title, canonical URL, structured data, the season
    sheet) around it; the sheet adds its peek row.

    Args:
        request: The incoming request.
        region: The region the body describes.
        page_date: The calendar day the body describes.
        selected: The bulletin chosen for that day, or ``None`` when no
            issue covers it.
        skipped: A newer issue for the same day that could not be shown
            because it did not build (SNOW-1368); the body names both
            issues' times in a notice above the bulletin.

    Returns:
        ``bulletin``, ``page_date``, ``is_today`` and ``ground`` (the
        region's field reports, ``ground_for_region``) always; with a
        bulletin, also ``panel``, ``day_windows``, ``day_movement``, the
        source label and URL, ``next_update_time`` and the
        ``headline``.

    """
    # SNOW-1291: what people found on the ground, in both shapes of the
    # body — a region with no bulletin today (off season, say) can still
    # have reports, and they are worth more then, not less.
    ground = ground_for_region(region, page_date)
    if selected is None:
        return {
            "bulletin": None,
            "page_date": page_date,
            "is_today": page_date == timezone.now().date(),
            "ground": ground,
        }

    today = timezone.now().date()
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

    # Before _build_panel_context, which decorates the render model's
    # problems in place for the templates.
    since_strip = _since_strip(request, region, page_date, selected.render_model)

    panel = _build_panel_context(selected)

    day_windows: list[dict[str, Any]] = _build_day_windows(
        selected, render_model=panel.get("render_model")
    )
    show_day_risk_profile: bool = _day_risk_profile_adds_to_cards(
        day_windows, panel.get("problem_cards") or []
    )

    # Derive the source label and URL for the metadata strip Source cell
    # (SNOW-211) from the row's own ``source`` column, so it reads the same
    # whether or not the render model is current (SNOW-1342).
    bulletin_source_label, bulletin_source_url = BULLETIN_SOURCE_LINKS.get(
        selected.source, ("", "")
    )

    # Period transition — rise/fall/flat-but-split (SNOW-248), read by the
    # day movement and the headline below. The service derives it from the
    # render model's danger.ratings list and stores it in ``derived``
    # (SNOW-1341). ``None`` on all-day bulletins.
    raw_render_model: dict[str, Any] = panel.get("render_model") or {}
    # Read off the stored (unenriched) model: a model with no ``derived``
    # is computed from, and the enriched copy's problems carry display
    # objects rather than the stored shape.
    derived = derived_of(get_render_model(selected))
    period_transition: PeriodTransition | None = period_transition_of(derived)

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

    # Bulletin headline (SNOW-249).
    headline: str = _build_bulletin_headline(derived["headline_inputs"])

    return {
        "since_strip": since_strip,
        "alert_prompt": _alert_prompt(request, region),
        "bulletin": selected,
        "panel": panel,
        "page_date": page_date,
        "is_today": is_today,
        "next_update_time": next_update_time,
        "day_windows": day_windows,
        "show_day_risk_profile": show_day_risk_profile,
        "day_movement": day_movement,
        "bulletin_source_label": bulletin_source_label,
        "bulletin_source_url": bulletin_source_url,
        "ground": ground,
        "headline": headline,
        "skipped_issue": skipped,
    }


def _since_strip(
    request: HttpRequest,
    region: MicroRegion,
    page_date: datetime.date,
    render_model: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Return the "since the day before" strip for a region body (SNOW-1328).

    Behind the ``bulletin_alerts`` flag while alerts roll out. Compares the
    bulletin on the page with the one authoritative the day before, in the
    alert's own words, so tapping an alert lands on the sentence that sent
    it.

    Args:
        request: The incoming request (for the flag).
        region: The region.
        page_date: The day on the page.
        render_model: The page bulletin's render model.

    Returns:
        ``heading``, ``sentences`` and ``quiet_text``, or None when the flag
        is off or the day before has no bulletin.

    """
    # Anonymous first: the flag is superusers-only while alerts roll out, so
    # an anonymous page (every audited and cached one) pays no flag lookup.
    if not request.user.is_authenticated or not render_model:
        return None
    if not waffle.flag_is_active(request, "bulletin_alerts"):
        return None
    previous_date = page_date - datetime.timedelta(days=1)
    previous = alerts_service.day_state(region, previous_date)
    if previous is None:
        return None
    change = summarise_change(previous, state_of(render_model)[0])
    return {
        "heading": _("Since %(day)s’s bulletin")
        % {"day": date_format(previous_date, "l")},
        "sentences": alert_wording.change_sentences(change),
        "quiet_text": _("No change to the level or the problems."),
    }


def _alert_prompt(request: HttpRequest, region: MicroRegion) -> str | None:
    """
    Return the one-line alerts prompt under the region body (SNOW-1325).

    Args:
        request: The incoming request.
        region: The region.

    Returns:
        ``"on"`` when the signed-in user gets alerts for this region,
        ``"offer"`` when their alerts are off, None otherwise (flag off,
        anonymous, alerts on but this region not followed).

    """
    user = request.user
    if not user.is_authenticated or not waffle.flag_is_active(
        request, "bulletin_alerts"
    ):
        return None
    account = getattr(user, "account", None)
    if account is None:
        return None
    if account.notify_when == account.NotifyWhen.OFF:
        return "offer"
    places = alerts_service.account_places(user)
    return "on" if any(p.region.pk == region.pk for p in places) else None


def build_resort_body(
    resort: Resort, target_date: datetime.date, *, is_staff: bool = False
) -> dict[str, Any]:
    """Build the context of a resort's body, shared by its page and sheet.

    SNOW-1164: one body, two containers. ``public/partials/_resort_body.html``
    renders this on the resort page (``resort_detail``) and in the map's
    place sheet (``apps.public.place_sheet.resort_sheet_partial``), so a
    fact about a resort is added here and reaches both.

    A resort has no avalanche danger of its own (Hugo, 25 Oct 2026,
    ``docs/decisions/a-resort-has-no-avalanche-danger.md``): the body is the
    resort's forecast and facts, and one row naming the region it sits in.
    So nothing here selects a bulletin; the row links to the region's page
    for the day, which states the danger.

    SNOW-1318: it takes the reader's staff flag rather than the request,
    so the sheet — which a download keeps unstamped and serves to whoever
    opens it offline — reads nothing about the reader. The page passes it;
    the sheet leaves it ``False``, the hint being the page's alone.

    Args:
        resort: The resort, with ``region`` and ``location`` loaded.
        target_date: The day to show — today on the bare page, the map's
            day in the sheet.
        is_staff: Whether the reader is staff (the page's curation hint).

    Returns:
        The body's context; see ``_resort_body.html`` for each key.

    """
    region = resort.region
    today = timezone.localdate()
    # SNOW-1157: the forecast strip's Base / Mid / Summit points. A forward
    # day has no row of its own — today's row carries it in its forecast —
    # so it reads today's.
    forecast_points = _resort_forecast_points(
        resort,
        min(target_date, today),
        timezone.now(),
        link_date=None if target_date == today else target_date,
    )
    bulletin = _resort_bulletin_context(region, target_date)

    return {
        **bulletin,
        "resort": resort,
        "region": region,
        "region_id": region.region_id,
        "target_date": target_date,
        "is_today": target_date == today,
        # The region row's href: its evergreen page today, the dated one
        # on another day, so the page and the map's day agree.
        "bulletin_url": region.get_absolute_url()
        if target_date == today
        else region.get_absolute_url(target_date=target_date),
        # SNOW-542: the why-it-matters partial shows a curation hint instead
        # of the public blank state for staff, exactly as the popup's
        # metadata rows already do.
        "is_staff": is_staff,
        "forecast_points": forecast_points,
        # SNOW-1205: no point with a forecast for the day draws one line in
        # place of the card (Hugo, 23 Oct).
        "forecast_available": any(point["panel"] for point in forecast_points),
        # "Weather • Open-Meteo HH:MM" (Hugo, 27 Oct 2026): the points are
        # fetched in one walk, so the latest of their fetch times stands for
        # the section; None when no point has a row.
        "forecast_updated_at": max(
            (p["weather"].fetched_at for p in forecast_points if p["weather"]),
            default=None,
        ),
    }


def _resort_bulletin_context(
    region: MicroRegion, target_date: datetime.date
) -> dict[str, Any]:
    """Build the resort body's bulletin callout: the region's level, or none.

    Hugo, 27 Oct 2026: the callout shows the region's rating for the day
    when a bulletin produced one, and "No bulletin available on this date"
    when none did. The level is the REGION's — the callout names the region
    under it and opens it — so this amends, rather than undoes,
    docs/decisions/a-resort-has-no-avalanche-danger.md.

    The source names the provider in the section's eyebrow ("Avalanche
    bulletin • SLF"). With no bulletin on the day it is the provider of the
    region's latest rated day, so the eyebrow still says whose bulletin is
    missing; a region never rated has none.

    Args:
        region: The resort's MicroRegion.
        target_date: The day shown.

    Returns:
        ``day_rating`` (the RegionDayRating, or None with no bulletin),
        ``level_label`` (the level's name, or "") and
        ``bulletin_source_label`` (the provider's short name, or "").

    """
    day_rating = (
        RegionDayRating.objects.filter(
            region=region, date=target_date, source_bulletin__isnull=False
        )
        .exclude(max_rating=RegionDayRating.Rating.NO_RATING)
        .first()
    )
    if day_rating is not None:
        source = day_rating.source
    else:
        source = (
            RegionDayRating.objects.filter(region=region)
            .exclude(source="")
            .order_by("-date")
            .values_list("source", flat=True)
            .first()
            or ""
        )
    level_meta = DANGER_PANEL_META.get(day_rating.max_rating) if day_rating else None
    return {
        "day_rating": day_rating,
        "level_label": level_meta["label"] if level_meta else "",
        "bulletin_source_label": BULLETIN_SOURCE_LINKS.get(source, ("", ""))[0],
    }


def _dated_weather_url(location: Location, day: datetime.date | None) -> str:
    """Return a location's weather page URL, opened on ``day`` when given.

    Args:
        location: The forecast point's Location.
        day: The day to open, or None for the bare page.

    Returns:
        ``/weather/<short_id>/`` with ``?date=<day>`` when a day is given,
        or "" for a location with no page yet.

    """
    url = location.get_absolute_url()
    if url and day is not None:
        return f"{url}?date={day.isoformat()}"
    return url


# SNOW-1157: the resort page's forecast points, in chip order. The role a
# ``ResortLocation`` plays and the chip's short label — "Mid" and "Summit"
# rather than the model's "Mid-mountain" and "Top", which name the
# relationship rather than the place a reader picks.
_RESORT_FORECAST_ROLES: tuple[tuple[str, Any], ...] = (
    (ResortLocation.ROLE.BASE, _("Base")),
    (ResortLocation.ROLE.MID, _("Mid")),
    (ResortLocation.ROLE.TOP, _("Summit")),
)


def _resort_forecast_points(
    resort: Resort,
    today: datetime.date,
    now: datetime.datetime,
    *,
    link_date: datetime.date | None = None,
) -> list[dict[str, Any]]:
    """Return the resort page's forecast points: Base, Mid and Summit (SNOW-1157).

    One point per role, in that order, each the resort's ``ResortLocation``
    in that role — the primary link first within a role, then by name. The
    resort's pin (``resort.location``) stands in for Base when no link has
    that role: since SNOW-1144 every pin writer goes through
    ``set_resort_pin``, so the pin is the one location every pinned resort
    has, and it is where the resort is. A role with no location gets no
    point, and a link with a blank role is not a point at all — it names no
    altitude the chips could offer.

    Each point carries today's ``Weather`` row and the week built by
    ``build_point_forecast_panel`` — today's row, then the rows after it
    (SNOW-1337), ``FORECAST_DAYS`` in all. The rows are read in ONE
    batched query keyed by ``location_id``, so the page costs the same
    whatever the number of points (the pin rides on the caller's
    ``select_related("location")``). A point with no row today keeps its
    chip and its link with ``panel=None``.

    Args:
        resort: The resort whose links to walk, with ``location`` loaded.
        today: The server's local date — the ``Weather.observed_on`` read.
        now: The reference instant for each day's day/night icon.
        link_date: The day the body shows when it is not today (SNOW-1164):
            each point's link then opens its weather page on that day
            (``?date=``), as the map's place sheet always did. None links
            the bare page.

    Returns:
        One dict per point: ``role`` (``"base"``/``"mid"``/``"top"``),
        ``label``, ``elevation_m`` (a rounded ``int`` or ``None``), ``url``
        (the location's ``/weather/<short_id>/`` page, ``""`` without a
        short id), ``weather`` (the row or ``None``) and ``panel`` (a
        ``ForecastPanel`` or ``None``). Empty when no role has a location.

    """
    by_role: dict[str, Location] = {}
    links = resort.resort_locations.filter(
        role__in=[role for role, _label in _RESORT_FORECAST_ROLES]
    ).select_related("location")
    for link in links.order_by("-is_primary", "location__name"):
        by_role.setdefault(link.role, link.location)
    if ResortLocation.ROLE.BASE not in by_role and resort.location is not None:
        by_role[ResortLocation.ROLE.BASE] = resort.location
    if not by_role:
        return []
    week_by_location: dict[int, list[Weather]] = defaultdict(list)
    for row in Weather.objects.filter(
        location_id__in=[location.pk for location in by_role.values()]
    ).window(today, FORECAST_DAYS):
        week_by_location[row.location_id].append(row)
    points: list[dict[str, Any]] = []
    for role, label in _RESORT_FORECAST_ROLES:
        location = by_role.get(role)
        if location is None:
            continue
        week = week_by_location.get(location.pk, [])
        weather = week[0] if week and week[0].observed_on == today else None
        panel = build_point_forecast_panel(weather, week[1:])
        points.append(
            {
                "role": str(role).lower(),
                "label": str(label),
                "elevation_m": (
                    round(location.elevation_m)
                    if location.elevation_m is not None
                    else None
                ),
                "url": _dated_weather_url(location, link_date),
                # The map opens the point's weather panel by it (the
                # pane's link, map_panel_trail.js).
                "short_id": location.short_id or "",
                "weather": weather,
                "panel": panel,
            }
        )
    return points


# Per-level display metadata used by the compact panel card. Keys match the
# CAAML ``mainValue`` strings; ``icon`` is the filename inside
# ``static/icons/eaws/danger_levels/``.
DANGER_PANEL_META: dict[str, dict[str, Any]] = {
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
    # ``DANGER_PANEL_META[key]`` lookups safe when they don't.
    "no_rating": {
        "number": "—",
        "label": _("No rating"),
        "sub": _("No rating available"),
        "icon": "No-Rating.svg",
    },
}

# Human labels for the CAAML ``problemType`` enum used on the panel tags.
PROBLEM_LABELS: dict[str, Any] = {
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

DANGER_ORDER: tuple[str, ...] = (
    "low",
    "moderate",
    "considerable",
    "high",
    "very_high",
)

_DANGER_RATING_INT: dict[str, int] = {
    "low": 1,
    "moderate": 2,
    "considerable": 3,
    "high": 4,
    "very_high": 5,
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
        if rk not in DANGER_ORDER:
            continue
        if best_key is None or DANGER_ORDER.index(rk) >= DANGER_ORDER.index(best_key):
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
    populate only part of a ``render_model`` rendering correctly.

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
    # directly and often leave its ratings empty — without this fallback the
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
    return DANGER_ORDER[max(levels) - 1], ""


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
        return gettext("above %(bound)s") % {"bound": lower_fmt}
    if upper_fmt:
        return gettext("below %(bound)s") % {"bound": upper_fmt}
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
        return gettext("Affects %(aspect_str)s aspects %(elevation)s") % {
            "aspect_str": aspect_str,
            "elevation": elevation.display,
        }
    return gettext("Affects all aspects and elevations")


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
        return gettext("treeline")
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
    return gettext("Wet line at %(earlier)s earlier, %(later)s later") % {
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
            PROBLEM_LABELS.get(
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
                    PROBLEM_LABELS.get(ptype, ptype.replace("_", " ").capitalize())
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
    danger_key: str = DANGER_ORDER[max(max_danger_level, 1) - 1]
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

    Every source's problem cards come from here (SNOW-1342 removed the
    raw-CAAML aggregation path). The traits list comes from the **enriched**
    render model (already processed by ``enrich_render_model``), so
    elevation and label fields are already in the presentation-ready shape
    the ``_rating_block.html`` partial expects.

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
    guidance: Mapping[str, str],
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
    label = PROBLEM_LABELS.get(
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
        # SNOW-1148: put the token back on the bound that carried it.
        # ``treeline_side`` is "upper" for a problem BELOW the treeline; it
        # used to be dropped, so every treeline band read "above treeline".
        # A render model stored before the side was recorded has no
        # ``treeline_side``, and keeps the old lower-bound reading.
        treeline_side = rm_elevation.get("treeline_side")
        caaml_lower: Any = lower_raw
        caaml_upper: Any = upper_raw
        if is_treeline and treeline_side == "upper":
            if upper_raw is None:
                caaml_upper = "treeline"
        elif is_treeline and lower_raw is None:
            caaml_lower = "treeline"
        elevation_bounds = _format_elevation(
            {"lowerBound": caaml_lower, "upperBound": caaml_upper}
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
        plain = plain_text(comment_html)
        later_plains = [
            plain_text(p.get("comment_html") or "")
            for p in trait_problems[problem_index + 1 :]
        ]
        if plain in later_plains:
            hide_comment = True

    # Map the problem's own danger rating to its CSS data-level value.
    # Uses the same key set as the danger-band data-level attribute so the
    # same CSS token rules apply. Falls back to empty string (neutral/grey).
    danger_rating_value: str = rm_problem.get("danger_rating_value") or ""
    danger_level_css = (
        danger_rating_value if danger_rating_value in DANGER_ORDER else ""
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


def get_render_model(bulletin: Bulletin) -> dict[str, Any]:
    """
    Return a bulletin's stored render model, or an error sentinel if stale.

    **A request never rebuilds a render model** (SNOW-1342). A row whose
    ``render_model_version`` is older than ``RENDER_MODEL_VERSION`` is not
    rebuilt from its CAAML here — that would load every bulletin's CAAML
    on every read path and hide a missed rebuild behind a per-request
    cost. A version bump ships with ``Deploy-Step: rebuild_render_models
    --commit`` instead (docs/render-model.md), and a row the rebuild has
    not reached reads as the same ``{"version": 0, "error": …}`` sentinel
    a failed build stores, so the page renders its error card. A stored
    failure keeps its own message; a stale row says which versions differ.

    Args:
        bulletin: The Bulletin whose render model is needed.

    Returns:
        The stored render model dict, or a version-0 error sentinel.

    """
    if bulletin.render_model_version >= RENDER_MODEL_VERSION:
        return cast("dict[str, Any]", bulletin.render_model)

    logger.warning(
        "Bulletin %s has a stale render_model (stored version=%d, current=%d);"
        " run rebuild_render_models --commit",
        bulletin.bulletin_id,
        bulletin.render_model_version,
        RENDER_MODEL_VERSION,
    )
    stored: dict[str, Any] = bulletin.render_model or {}
    return {
        "version": 0,
        "error": stored.get("error")
        or (
            f"Render model is stale (stored version "
            f"{bulletin.render_model_version}, current {RENDER_MODEL_VERSION}); "
            "run rebuild_render_models --commit."
        ),
        "error_type": stored.get("error_type") or "StaleRenderModel",
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
    raw_rm = get_render_model(bulletin)
    render_model = enrich_render_model(raw_rm)
    traits = render_model.get("traits") or []
    rm_danger = raw_rm.get("danger") or {}
    return _resolve_problem_cards(
        traits, raw_rm.get("danger_patterns") or [], rm_danger.get("ratings") or []
    )


def _key_message(render_model: dict[str, Any]) -> str:
    """
    Return the one-paragraph key message a share card quotes, as plain text.

    The first problem comment across the traits, else the snowpack
    structure, else the weather review — read from the render model
    rather than the CAAML (SNOW-1342), in the order the fallback always
    took.

    Args:
        render_model: The stored (unenriched) render model.

    Returns:
        Plain text with whitespace collapsed, or ``""``.

    """
    for trait in render_model.get("traits") or []:
        for problem in trait.get("problems") or []:
            comment = plain_text(problem.get("comment_html"))
            if comment:
                return comment
    prose: dict[str, Any] = render_model.get("prose") or {}
    return plain_text(prose.get("snowpack_structure")) or plain_text(
        prose.get("weather_review")
    )


def _build_panel_context(bulletin: Bulletin) -> dict[str, Any]:
    """
    Build the template context for a single compact bulletin panel.

    Reads ``bulletin.render_model`` and the row's own columns only — never
    CAAML (SNOW-1342). A row whose ``render_model_version`` is older
    than ``RENDER_MODEL_VERSION`` reads as the error sentinel
    :func:`get_render_model` returns; it is not rebuilt per request.

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
    # The stored render model, or the error sentinel for a stale row.
    raw_render_model = get_render_model(bulletin)

    # Enrich the render model with presentation-ready fields (labels,
    # ElevationBounds, field_guidance, hide_comment per trait).
    render_model = enrich_render_model(raw_render_model)

    # Danger key and subdivision come from the render model projection.
    # The render model reads customData.CH.subdivision for SLF; ALBINA
    # and METEOFRANCE carry None. This avoids re-reading raw customData here.
    rm_danger: dict[str, Any] = raw_render_model.get("danger") or {}
    danger_key: str = rm_danger.get("key") or "low"
    danger_subdivision: str = rm_danger.get("subdivision") or ""
    if not danger_key or danger_key not in DANGER_PANEL_META:
        logger.error(
            "_build_panel_context: %s has no usable danger key in render model",
            bulletin.pk,
        )
        danger_key = "low"
    danger_meta = DANGER_PANEL_META[danger_key]

    # The key message feeds only the OG / meta description
    # (_build_og_description): the first problem's comment, else the
    # snowpack structure, else the weather review — as plain text.
    key_message = _key_message(raw_render_model)

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
    morning_meta = DANGER_PANEL_META[morning_key]
    afternoon_meta = DANGER_PANEL_META[afternoon_key]

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
    panel["show_day_character"] = SHOW_DAY_CHARACTER
    panel["tendency_outlook"] = _resolve_tendency_outlook(raw_render_model)
    return panel


# The day-character callout ("Hard-to-read day. …") is hidden for now: the
# copy is not good enough and it distracts from the bulletin itself. The
# label and explainer are still computed and kept on the panel context, so
# turning this back on is this one line.
SHOW_DAY_CHARACTER: bool = False


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
