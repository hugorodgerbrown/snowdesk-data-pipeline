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

SNOW-1126 adds the resort page's season strip: ``season_window`` names the
1 Nov – 30 Apr season a date belongs to (the previous one from May to
October), ``build_season_grid`` accepts it as an optional ``window``, and
``summarise_season`` counts the grid into the strip's text summary. The
strip renders server-side in ``resort_detail`` — no view-layer cache, one
indexed range query.

SNOW-1158 redraws every season calendar as one shared, month-shaped
component (``public/partials/_season_calendar.html``). ``build_season_months``
reshapes a ``SeasonGrid`` into months × days — a cell state per day (a level,
"split" for a two-level day, "none", or "future" after today) — and rings
the day the page is showing (SNOW-1163 retired the "first High day" ring of
a finished season). The week-column grid stays the data source;
nothing renders its columns directly any more.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from django.conf import settings
from django.utils.dates import MONTHS_3

from apps.bulletins.models import RegionDayRating

if TYPE_CHECKING:
    from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

_DAYS_PER_WEEK = 7

# SNOW-1126: the avalanche season, as (month, day) bounds. A season opens
# on 1 November and closes on 30 April of the following year; a date from
# 1 May to 31 October belongs to no season, and ``season_window`` answers
# with the one that has just ended.
SEASON_OPENS: tuple[int, int] = (11, 1)
SEASON_CLOSES: tuple[int, int] = (4, 30)

# EAWS levels low → very high. The index is the rank ``summarise_season``
# slices to count the days at Considerable or higher; ``no_rating`` is
# deliberately absent.
_RATED_LEVELS: tuple[str, ...] = (
    RegionDayRating.Rating.LOW,
    RegionDayRating.Rating.MODERATE,
    RegionDayRating.Rating.CONSIDERABLE,
    RegionDayRating.Rating.HIGH,
    RegionDayRating.Rating.VERY_HIGH,
)


class SeasonWindow(NamedTuple):
    """
    The date range one season covers, from :func:`season_window`.

    ``start`` is 1 November (clamped to ``settings.SEASON_START_DATE``),
    ``end`` is 30 April of the following year, and ``label`` is the SLF-style
    two-year identifier (``"25/26"``) of the nominal season — computed before
    the clamp, so a clamped start never shifts the label.
    """

    start: datetime.date
    end: datetime.date
    label: str


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


@dataclasses.dataclass(frozen=True)
class SeasonSummary:
    """
    Output of :func:`summarise_season` — the season strip's text summary.

    ``days_with_bulletin`` counts the cells with ``has_bulletin``;
    ``level_counts`` is one ``(rating_key, count)`` pair per EAWS level that
    occurred, low → very high; ``considerable_or_higher`` is the count at
    level 3 and above. There is deliberately no season-wide peak (SNOW-1153):
    nearly every resort reaches 3 or 4 in a winter, so it carries no
    information.
    """

    season_label: str
    days_with_bulletin: int
    level_counts: list[tuple[str, int]]
    considerable_or_higher: int

    def __bool__(self) -> bool:
        """Return ``False`` when no day had a bulletin — the strip is hidden."""
        return self.days_with_bulletin > 0

    @property
    def key_counts(self) -> list[tuple[str, int]]:
        """
        Return the season calendar key's levels, each with its day count.

        Low to High always (SNOW-1158 — the key names the four levels a
        season commonly holds, a zero included); Very high only when a day
        reached it, so the key never explains a colour the calendar lacks
        nor leaves one it has unexplained.
        """
        counts = dict(self.level_counts)
        keys = list(_RATED_LEVELS[:-1])
        if counts.get(RegionDayRating.Rating.VERY_HIGH):
            keys.append(RegionDayRating.Rating.VERY_HIGH)
        return [(key, counts.get(key, 0)) for key in keys]


@dataclasses.dataclass(frozen=True)
class MonthDay:
    """
    One day of the month-shaped season calendar (SNOW-1158).

    ``state`` is what the cell draws: ``"none"`` (no bulletin — outlined),
    ``"future"`` (not yet — a day after today, faint), an EAWS rating key
    (``"low"`` … ``"very_high"``), or ``"split"`` for a two-level day, whose
    halves are ``low_key`` (left) and ``high_key`` (right).

    ``level`` is the day's peak, ``SeasonCell.max_rating_key`` — the
    compressed-views rule (docs/compressed-views-rating-rule.md): a split
    day's peak is its ``high_key``, never the lower half. ``"no_rating"`` on
    a day without a bulletin.

    ``has_bulletin`` is the cell's own flag — a linked calendar links only
    these days. ``is_ring`` marks the one ringed day, if any.
    """

    date: datetime.date
    state: str
    level: str
    has_bulletin: bool
    low_key: str = ""
    high_key: str = ""
    is_ring: bool = False


@dataclasses.dataclass(frozen=True)
class SeasonMonth:
    """
    One month row (a column on a phone) of the season calendar.

    ``label`` is Django's translated three-letter month name, title-cased
    (``"Nov"``); ``days`` holds exactly one :class:`MonthDay` per day of
    the month — 28 to 31 — so a short month simply ends early.
    """

    first: datetime.date
    label: str
    days: list[MonthDay]


@dataclasses.dataclass(frozen=True)
class SeasonMonths:
    """
    Output of :func:`build_season_months` — the month-shaped calendar.

    ``months`` runs from the month of the span's first day to the month of
    its last, inclusive. ``ring_date`` is the ringed day (or ``None``).
    ``season_label`` is the four-digit form (``"2025/26"``) the calendar's
    eyebrow and summary name. ``axis`` is the 31 day-axis labels, a number
    on 1, 5, 10, … 30 and ``""`` elsewhere.
    """

    months: list[SeasonMonth]
    ring_date: datetime.date | None
    season_label: str
    axis: tuple[str, ...]

    def __bool__(self) -> bool:
        """Return ``False`` when there is no month to draw."""
        return bool(self.months)


# The day-axis labels above (beside, on a phone) the month rows: the first
# day of each group of five — 1, 6, 11, 16, 21, 26 — so the 13th reads as
# the third day under 11 (SNOW-1163; CSS draws the gap before each group).
# Fixed — every month row is drawn against 31 slots.
SEASON_AXIS: tuple[str, ...] = tuple(
    str(day) if day % 5 == 1 and day < 31 else "" for day in range(1, 32)
)

# Level ranks for the split rule. ``no_rating`` is absent.
_LEVEL_RANK: dict[str, int] = {key: rank for rank, key in enumerate(_RATED_LEVELS)}


def season_window(today: datetime.date) -> SeasonWindow:
    """
    Return the season ``today`` belongs to, or the one that has just ended.

    From 1 November to 30 April that is the running season; from 1 May to
    31 October it is the previous one (``SEASON_OPENS`` / ``SEASON_CLOSES``).
    The start is clamped to ``settings.SEASON_START_DATE``, the earliest day
    the pipeline holds bulletins for, so a window never reaches back past the
    archive. A clamp past the end leaves ``start > end``, which
    :func:`build_season_grid` renders as an empty grid.

    Args:
        today: The current calendar date.

    Returns:
        The :class:`SeasonWindow` ``(start, end, label)``.

    """
    open_month, open_day = SEASON_OPENS
    close_month, close_day = SEASON_CLOSES
    start_year = (
        today.year if (today.month, today.day) >= SEASON_OPENS else today.year - 1
    )
    nominal_start = datetime.date(start_year, open_month, open_day)
    end = datetime.date(start_year + 1, close_month, close_day)
    earliest: datetime.date = settings.SEASON_START_DATE
    return SeasonWindow(
        start=max(nominal_start, earliest),
        end=end,
        label=_season_label(nominal_start),
    )


def summarise_season(grid: SeasonGrid) -> SeasonSummary:
    """
    Count a season grid into the resort strip's text summary.

    Pure: reads only the already-built cells. Only cells with
    ``has_bulletin`` count — a ``no_rating`` placeholder is not a day with a
    bulletin. Each day counts once, at its ``max_rating_key``.

    Args:
        grid: A grid from :func:`build_season_grid`.

    Returns:
        The :class:`SeasonSummary`; falsy when no day had a bulletin.

    """
    counts: dict[str, int] = dict.fromkeys(_RATED_LEVELS, 0)
    days = 0
    for column in grid.columns:
        for cell in column:
            if cell is None or not cell.has_bulletin:
                continue
            if cell.max_rating_key not in counts:
                continue
            days += 1
            counts[cell.max_rating_key] += 1
    considerable_rank = _RATED_LEVELS.index(RegionDayRating.Rating.CONSIDERABLE)
    return SeasonSummary(
        season_label=grid.season_label,
        days_with_bulletin=days,
        level_counts=[(key, n) for key, n in counts.items() if n],
        considerable_or_higher=sum(
            counts[key] for key in _RATED_LEVELS[considerable_rank:]
        ),
    )


def build_season_months(
    grid: SeasonGrid,
    today: datetime.date,
    window: SeasonWindow | None = None,
    ring_date: datetime.date | None = None,
) -> SeasonMonths:
    """
    Reshape a season grid into months × days (SNOW-1158).

    The one data shape behind ``public/partials/_season_calendar.html`` on
    both the resort page and the bulletin page: one row per month (a column
    on a phone) and one cell per day of that month. Pure — it reads only the
    already-built grid, no query.

    The months drawn run from the season's start — ``window.start``, or,
    without a window, the grid's first day (the bulletin page's
    ``SEASON_START_DATE``) — through that season's April, so a running
    season shows its remaining days faint. They extend past April only to
    the last month that holds a bulletin, and never beyond tomorrow's month
    (:func:`_last_drawn_day`). Every month drawn is drawn in full.

    Each day's state: a day with a bulletin draws its level, or ``"split"``
    when the day held two levels (see :func:`_split_keys`); any other day
    after ``today`` is ``"future"`` (not yet); everything else is
    ``"none"`` (no bulletin).

    Args:
        grid: A grid from :func:`build_season_grid`.
        today: The current calendar date — days after it with no bulletin
            are "not yet", not "no bulletin".
        window: Optional :class:`SeasonWindow` naming the span to draw.
        ring_date: The day to ring, or ``None`` (the bulletin page rings the
            selected day client-side, so its cached grid stays one body).

    Returns:
        A :class:`SeasonMonths`; falsy when the grid is empty and no window
        was given.

    """
    by_date: dict[datetime.date, SeasonCell] = {c.date: c for c in _grid_cells(grid)}
    if window is not None:
        span_start = window.start
    elif by_date:
        span_start = min(by_date)
    else:
        return SeasonMonths(
            months=[], ring_date=None, season_label="", axis=SEASON_AXIS
        )
    span_end = _last_drawn_day(by_date, span_start, today)

    months: list[SeasonMonth] = []
    first = span_start.replace(day=1)
    while first <= span_end:
        days: list[MonthDay] = []
        cursor = first
        while cursor.month == first.month:
            days.append(
                _month_day(by_date.get(cursor), cursor, today, cursor == ring_date)
            )
            cursor += datetime.timedelta(days=1)
        months.append(
            SeasonMonth(
                first=first,
                label=str(MONTHS_3[first.month]).title(),
                days=days,
            )
        )
        first = cursor
    return SeasonMonths(
        months=months,
        ring_date=ring_date,
        season_label=_season_long_label(span_start),
        axis=SEASON_AXIS,
    )


def _last_drawn_day(
    by_date: dict[datetime.date, SeasonCell],
    span_start: datetime.date,
    today: datetime.date,
) -> datetime.date:
    """
    Return the last day of the calendar's span (SNOW-1158).

    The season's 30 April, unless a bulletin was published after it — then
    the last bulletin's day, capped at tomorrow (the latest a bulletin can
    target), so a May bulletin adds May and nothing adds an empty summer.
    """
    close_month, close_day = SEASON_CLOSES
    april = datetime.date(_season_start_year(span_start) + 1, close_month, close_day)
    published = [day for day, cell in by_date.items() if cell.has_bulletin]
    last = max(published, default=april)
    if last <= april:
        return april
    return min(last, today + datetime.timedelta(days=1))


def _season_start_year(day: datetime.date) -> int:
    """Return the year the season holding ``day`` opened (1 November)."""
    return day.year if (day.month, day.day) >= SEASON_OPENS else day.year - 1


def _month_day(
    cell: SeasonCell | None,
    date: datetime.date,
    today: datetime.date,
    is_ring: bool,
) -> MonthDay:
    """Build one :class:`MonthDay` from the grid's cell for ``date``."""
    if cell is not None and cell.has_bulletin:
        split = _split_keys(cell)
        if split is not None:
            return MonthDay(
                date=date,
                state="split",
                level=cell.max_rating_key,
                has_bulletin=True,
                low_key=split[0],
                high_key=split[1],
                is_ring=is_ring,
            )
        return MonthDay(
            date=date,
            state=cell.max_rating_key,
            level=cell.max_rating_key,
            has_bulletin=True,
            is_ring=is_ring,
        )
    return MonthDay(
        date=date,
        state="future" if date > today else "none",
        level=RegionDayRating.Rating.NO_RATING,
        has_bulletin=False,
        is_ring=is_ring,
    )


def _split_keys(cell: SeasonCell) -> tuple[str, str] | None:
    """
    Return ``(lower, peak)`` when ``cell`` held two levels in one day.

    The levels a day held are its ``min_rating_key`` (an afternoon rise or
    an elevation split, docs/calendar.md), its AM/PM peaks (SNOW-291) and
    its ALBINA band ratings (SNOW-292). The upper half is always the day's
    peak, ``max_rating_key`` — the compressed-views rule — and the lower
    half the lowest level that day below it. ``None`` for a one-level day.
    """
    peak = cell.max_rating_key
    peak_rank = _LEVEL_RANK.get(peak)
    if peak_rank is None:
        return None
    held = [cell.min_rating_key, cell.am_rating_key, cell.pm_rating_key]
    held += [band.get("rating_key", "") for band in cell.bands or []]
    lower = [
        _LEVEL_RANK[key] for key in held if _LEVEL_RANK.get(key, peak_rank) < peak_rank
    ]
    if not lower:
        return None
    return _RATED_LEVELS[min(lower)], peak


def _grid_cells(grid: SeasonGrid) -> list[SeasonCell]:
    """Return the grid's dated cells in date order, padding dropped."""
    return [cell for column in grid.columns for cell in column if cell is not None]


def _season_long_label(start: datetime.date) -> str:
    """Name the season ``start`` falls in, four-digit form (``"2025/26"``).

    A date before ``SEASON_OPENS`` in its year belongs to the season that
    opened the year before.
    """
    start_year = _season_start_year(start)
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def build_season_grid(
    region: "MicroRegion",
    today: datetime.date,
    window: SeasonWindow | None = None,
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
        window: Optional :class:`SeasonWindow` (SNOW-1126, the resort
            page's strip). When given, the grid runs from ``window.start``
            to the earlier of ``window.end`` and ``today + 1``, and carries
            ``window.label``. When omitted — the bulletin page's heatmap —
            the grid runs from ``SEASON_START_DATE`` to ``today + 1``,
            unchanged.

    Returns:
        A :class:`SeasonGrid` ready to render. Empty when the computed
        end date precedes the start.

    """
    start: datetime.date
    if window is None:
        start = settings.SEASON_START_DATE
        end = today + datetime.timedelta(days=1)
        season_label = _season_label(start)
    else:
        start = window.start
        end = min(window.end, today + datetime.timedelta(days=1))
        season_label = window.label
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


def season_header(today: datetime.date) -> dict[str, Any] | None:
    """
    Return a minimal context dict for the season trigger/shell, or ``None``.

    Returns ``{"season_label": "<NN/NN>", "season_name": "<NNNN/NN>"}``
    when the season has started (i.e. ``today + 1 >= SEASON_START_DATE``),
    so the bulletin view can decide whether to render the trigger and shell
    without building the full grid. ``season_name`` heads the sheet.
    Returns ``None`` before the season start.

    Args:
        today: Current calendar date.

    Returns:
        The header dict, or ``None``.

    """
    start: datetime.date = settings.SEASON_START_DATE
    end = today + datetime.timedelta(days=1)
    if end < start:
        return None
    return {
        "season_label": _season_label(start),
        "season_name": _season_long_label(start),
    }


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
            # SNOW-1128: Django's own translated three-letter month name,
            # resolved in the request's active language (``MONTHS_3`` is
            # lower-case — "jan" — so title-case it as the ``M`` date format
            # does).
            labels[idx] = str(MONTHS_3[month]).title()
            last_month = month
    return labels
