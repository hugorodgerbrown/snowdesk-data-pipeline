"""
apps/public/season_calendar.py — Build a season-long heatmap grid for the bulletin page.

The bulletin page already shows the current day's rating block and the
month-grid nav-glyph calendar. This module backs a third, complementary
surface: a GitHub-contributions-style heatmap covering the whole season at
a glance, one tile per day from ``settings.SEASON_START_DATE`` through
``today + 1``.

The grid is laid out as weeks-as-columns (Mon..Sun rows, European
convention). The leading column is padded with ``None`` cells when the
season starts mid-week; the trailing column is padded after the end date
so all seven rows align.

Tiles for days that have a ``RegionDayRating`` row link to the day's
bulletin. Tiles for days without a row render as inert ``no_rating``
placeholders. This is a pure presentation reshape of the already
pre-computed ``RegionDayRating`` rows — no Python-level caching in this
module; the rendered grid is cached at the view layer in
``season_calendar_partial`` (keyed on ``(canonical_region_id, today_iso)``)
and invalidated by ``apply_bulletin_day_ratings`` after ingest.

``season_header`` is a cheap helper that returns ``{"season_label": "<NN/NN>"}``
when the season has started (used by the bulletin view to decide whether
to render the shell + trigger without building the full grid).
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from typing import TYPE_CHECKING

from django.conf import settings

from apps.bulletins.models import RegionDayRating

if TYPE_CHECKING:
    from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

_DAYS_PER_WEEK = 7
_MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclasses.dataclass(frozen=True)
class SeasonCell:
    """
    A single tile in the season-long heatmap.

    ``has_bulletin=True`` means a ``RegionDayRating`` row exists for the
    date and the tile should render as an interactive link. Otherwise the
    tile renders as an inert ``no_rating`` placeholder.

    ``is_today`` is set only when the cell date equals today. ``is_selected``
    is always ``False`` from the builder — highlighting of the currently
    displayed date is applied client-side after the HTMX swap, keyed off
    ``data-selected-date`` on the grid container and ``data-date`` on each
    cell anchor/div.

    ``month_parity`` alternates 0/1 across calendar months (the first
    dated month is 0). The template paints a subtle backdrop on cells
    where ``month_parity == 1`` so the month boundary is visible at
    the exact day, even when it falls mid-column.

    ``source`` is the bulletin source string (e.g. ``"ALBINA"``, ``"SLF"``)
    copied from ``RegionDayRating.source``.  Empty string when no rating row
    exists.

    ``bands`` is the ALBINA elevation-band list from ``RegionDayRating.bands``,
    or ``None`` for SLF, MeteoFrance, and no-rating cells.  When present, each
    entry is ``{"band_id": str, "label": str, "rating_key": str,
    "time_period": str}``.  The template uses ``bands`` to select the tile
    rendering mode:

    * 2 entries sharing the same ``time_period`` → ``"elevation-only"``
      (horizontal split, low band bottom, high band top).
    * 4 entries with 2 distinct ``time_period`` values →
      ``"elevation-time"`` (2×2 grid).

    SNOW-291 AM/PM split: ``am_rating_key`` and ``pm_rating_key`` are
    non-empty only on days where the bulletin carries both a morning and an
    afternoon period.  The ``is_time_split`` property returns ``True`` when
    both are present, letting the template render a vertical left/right split
    instead of the existing diagonal min/max fill.  Uniform days (no later
    period) always have both fields as empty strings.
    """

    date: datetime.date
    min_rating_key: str
    max_rating_key: str
    subdivision: str
    has_bulletin: bool
    is_today: bool = False
    is_selected: bool = False
    month_parity: int = 0
    source: str = ""
    bands: list[dict] | None = None
    am_rating_key: str = ""
    am_subdivision: str = ""
    pm_rating_key: str = ""
    pm_subdivision: str = ""

    @property
    def band_mode(self) -> str:
        """
        Return the tile rendering mode derived from the bands list.

        Returns ``"elevation-only"`` when bands has 2 entries sharing a single
        time_period, ``"elevation-time"`` when bands has 4 entries across 2
        distinct periods, and ``""`` for all other cases (SLF, MF, no-rating).
        """
        if not self.bands:
            return ""
        periods = {b.get("time_period", "all_day") for b in self.bands}
        if len(self.bands) == 2 and len(periods) == 1:
            return "elevation-only"
        if len(self.bands) == 4 and len(periods) == 2:  # noqa: PLR2004 — 4 bands / 2 periods are the fixed shape of the ALBINA 2×2 grid; these are domain constants, not magic numbers
            return "elevation-time"
        return ""

    @property
    def is_time_split(self) -> bool:
        """Return True when both AM and PM rating keys are non-empty."""
        return bool(self.am_rating_key and self.pm_rating_key)


@dataclasses.dataclass(frozen=True)
class RibbonDay:
    """
    A single day entry in the season ribbon strip.

    The ribbon is a flat chronological list of coloured cells — one per day
    from ``SEASON_START_DATE`` through today — docked beside the map scrubber.
    Cells are coloured by ``max_rating_key`` so the strip gives an at-a-glance
    view of how danger peaked each day for the selected region.

    ``has_bulletin=True`` means a ``RegionDayRating`` row exists and the cell
    is interactive (clicking drives the scrubber). ``False`` cells still appear
    in the strip as inert placeholders (``no_rating`` colour).
    """

    date: datetime.date
    max_rating_key: str
    has_bulletin: bool


@dataclasses.dataclass(frozen=True)
class SeasonRibbon:
    """
    Output of :func:`build_season_ribbon`.

    ``days`` is a flat chronological list of :class:`RibbonDay` objects, one
    per calendar day from ``SEASON_START_DATE`` through today.

    ``season_label`` is the SLF-style two-year season identifier (e.g.
    ``"25/26"``), used by the ribbon header.

    """

    days: list[RibbonDay]
    season_label: str

    def __bool__(self) -> bool:
        """Return ``False`` when the ribbon is empty (e.g. before season start)."""
        return bool(self.days)


@dataclasses.dataclass(frozen=True)
class SeasonGrid:
    """
    Output of :func:`build_season_grid`.

    ``columns`` is a list of 7-tuples (Mon..Sun rows). Each entry is
    either a :class:`SeasonCell` or ``None`` for padding cells outside the
    season.

    ``month_labels`` is a list parallel to ``columns``: each entry holds
    an abbreviated month name (``"Nov"``) only on the first column of
    that calendar month, and an empty string otherwise. The template
    iterates it zipped with ``columns`` to draw the labels row.

    ``season_label`` is the two-year season identifier in SLF style
    (e.g. ``"25/26"`` for the season starting in autumn 2025), used by
    the page-nav trigger to make the current season explicit.
    """

    columns: list[tuple[SeasonCell | None, ...]]
    month_labels: list[str]
    season_label: str = ""

    def __bool__(self) -> bool:
        """Return ``False`` when the grid is empty (e.g. before season start)."""
        return bool(self.columns)


def build_season_grid(
    region: "MicroRegion",
    today: datetime.date,
) -> SeasonGrid:
    """
    Build the season-long heatmap grid for ``region``.

    All cells have ``is_selected=False`` — client-side highlighting applies
    after the HTMX swap (keyed off ``data-selected-date`` on the grid
    container and ``data-date`` on each cell anchor/div).

    Args:
        region: The region whose ratings to render.
        today: Current date — the day after this is the last column of
            the grid (the SLF afternoon bulletin targets ``today + 1``).

    Returns:
        A :class:`SeasonGrid` ready to render. Empty when the computed
        end date precedes ``SEASON_START_DATE``.

    """
    start: datetime.date = settings.SEASON_START_DATE
    end = today + datetime.timedelta(days=1)
    season_label = _season_label(start)
    if end < start:
        return SeasonGrid(columns=[], month_labels=[], season_label=season_label)

    rows = RegionDayRating.objects.for_region_range(region, start, end)
    by_date: dict[datetime.date, RegionDayRating] = {r.date: r for r in rows}

    cells: list[SeasonCell] = []
    cursor = start
    month_parity = 0
    prev_month: int | None = None
    while cursor <= end:
        if prev_month is not None and cursor.month != prev_month:
            month_parity = 1 - month_parity
        rdr = by_date.get(cursor)
        min_key: str
        max_key: str
        if rdr is None:
            min_key = RegionDayRating.Rating.NO_RATING
            max_key = RegionDayRating.Rating.NO_RATING
            subdivision = ""
            am_rating_key = ""
            am_subdivision = ""
            pm_rating_key = ""
            pm_subdivision = ""
            has_bulletin = False
            source = ""
            bands: list[dict] | None = None
        else:
            min_key = rdr.min_rating
            max_key = rdr.max_rating
            subdivision = rdr.max_subdivision
            am_rating_key = rdr.am_rating or ""
            am_subdivision = rdr.am_subdivision or ""
            pm_rating_key = rdr.pm_rating or ""
            pm_subdivision = rdr.pm_subdivision or ""
            has_bulletin = (
                rdr.source_bulletin_id is not None
                and max_key != RegionDayRating.Rating.NO_RATING
            )
            source = rdr.source or ""
            bands = rdr.bands
        is_today = cursor == today
        cells.append(
            SeasonCell(
                date=cursor,
                min_rating_key=min_key,
                max_rating_key=max_key,
                subdivision=subdivision,
                has_bulletin=has_bulletin,
                is_today=is_today,
                month_parity=month_parity,
                source=source,
                bands=bands,
                am_rating_key=am_rating_key,
                am_subdivision=am_subdivision,
                pm_rating_key=pm_rating_key,
                pm_subdivision=pm_subdivision,
            )
        )
        prev_month = cursor.month
        cursor += datetime.timedelta(days=1)

    columns = _pack_into_columns(cells, start)
    month_labels = _month_label_indices(columns)
    return SeasonGrid(
        columns=columns,
        month_labels=month_labels,
        season_label=season_label,
    )


def season_header(today: datetime.date) -> dict[str, str] | None:
    """
    Return a minimal context dict for the season trigger/shell, or ``None``.

    Returns ``{"season_label": "<NN/NN>"}`` when the season has started
    (i.e. ``today + 1 >= SEASON_START_DATE``), so the bulletin view can
    decide whether to render the trigger and shell without building the full
    grid. Returns ``None`` before the season start.

    Args:
        today: Current calendar date.

    Returns:
        ``{"season_label": "<NN/NN>"}`` or ``None``.

    """
    start: datetime.date = settings.SEASON_START_DATE
    end = today + datetime.timedelta(days=1)
    if end < start:
        return None
    return {"season_label": _season_label(start)}


def build_season_ribbon(
    region: "MicroRegion",
    today: datetime.date,
) -> SeasonRibbon:
    """
    Build the flat chronological season ribbon for ``region``.

    The ribbon is a simpler counterpart to :func:`build_season_grid`: a flat
    list of one :class:`RibbonDay` per calendar day from ``SEASON_START_DATE``
    through ``today`` (inclusive), ordered oldest-first. Days after ``today``
    are excluded — the ribbon is a retrospective view of the season so far.

    Reuses the same ``RegionDayRating`` queryset as ``build_season_grid``; no
    additional DB queries. Rendering is cached at the view layer.

    Args:
        region: The region whose ratings to render (e.g. CH-4115).
        today: Current date — the last day included in the ribbon.

    Returns:
        A :class:`SeasonRibbon` ready to render. Empty (falsy) when today
        precedes ``SEASON_START_DATE``.

    """
    start: datetime.date = settings.SEASON_START_DATE
    season_label = _season_label(start)
    if today < start:
        return SeasonRibbon(days=[], season_label=season_label)

    rows = RegionDayRating.objects.for_region_range(region, start, today)
    by_date: dict[datetime.date, RegionDayRating] = {r.date: r for r in rows}

    days: list[RibbonDay] = []
    cursor = start
    while cursor <= today:
        rdr = by_date.get(cursor)
        max_key: str
        if rdr is None:
            max_key = RegionDayRating.Rating.NO_RATING
            has_bulletin = False
        else:
            max_key = rdr.max_rating
            has_bulletin = (
                rdr.source_bulletin_id is not None
                and max_key != RegionDayRating.Rating.NO_RATING
            )
        days.append(
            RibbonDay(date=cursor, max_rating_key=max_key, has_bulletin=has_bulletin)
        )
        cursor += datetime.timedelta(days=1)

    return SeasonRibbon(days=days, season_label=season_label)


def _season_label(start: datetime.date) -> str:
    """Build the SLF-style two-year season identifier (e.g. ``"25/26"``).

    The Northern-hemisphere avalanche season runs from autumn through to
    late spring of the following year. The label is always two two-digit
    years separated by a slash.
    """
    return f"{start.year % 100:02d}/{(start.year + 1) % 100:02d}"


def _pack_into_columns(
    cells: list[SeasonCell],
    start: datetime.date,
) -> list[tuple[SeasonCell | None, ...]]:
    """Pack ``cells`` into 7-row columns, padding the leading column."""
    if not cells:
        return []
    leading_pad = start.weekday()
    flat: list[SeasonCell | None] = [None] * leading_pad + list(cells)
    while len(flat) % _DAYS_PER_WEEK != 0:
        flat.append(None)
    return [
        tuple(flat[i : i + _DAYS_PER_WEEK]) for i in range(0, len(flat), _DAYS_PER_WEEK)
    ]


def _month_label_indices(
    columns: list[tuple[SeasonCell | None, ...]],
) -> list[str]:
    """Build a parallel labels list, marking each column where the month flips."""
    labels: list[str] = ["" for _ in columns]
    last_month: int | None = None
    for idx, column in enumerate(columns):
        first_dated = next((c for c in column if c is not None), None)
        if first_dated is None:
            continue
        month = first_dated.date.month
        if month != last_month:
            labels[idx] = _MONTH_ABBREVIATIONS[month - 1]
            last_month = month
    return labels
