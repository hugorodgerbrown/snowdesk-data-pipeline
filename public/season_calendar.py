"""
public/season_calendar.py — Build a season-long heatmap grid for the bulletin page.

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
pre-computed ``RegionDayRating`` rows — no caching, no pre-compute, no
signals.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from typing import TYPE_CHECKING

from django.conf import settings

from bulletins.models import RegionDayRating

if TYPE_CHECKING:
    from pipeline.models import Region

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

    ``is_today`` and ``is_selected`` are mutually exclusive: when the page
    date matches today, only ``is_today`` is set — otherwise the tile
    would render with two stacked rings.
    """

    date: datetime.date
    min_rating_key: str
    max_rating_key: str
    subdivision: str
    has_bulletin: bool
    is_today: bool = False
    is_selected: bool = False


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

    @property
    def columns_with_labels(
        self,
    ) -> list[tuple[str, tuple[SeasonCell | None, ...]]]:
        """Yield ``(month_label, column)`` pairs for parallel template iteration.

        A non-empty ``month_label`` flags the column as the start of a new
        calendar month — the template uses that to insert a visual gap so
        the heatmap's month boundaries are scannable.
        """
        return list(zip(self.month_labels, self.columns, strict=True))


def build_season_grid(
    region: Region,
    page_date: datetime.date,
    today: datetime.date,
) -> SeasonGrid:
    """
    Build the season-long heatmap grid for ``region``.

    Args:
        region: The region whose ratings to render.
        page_date: The date currently displayed on the bulletin page. When
            this differs from ``today`` the matching tile is flagged
            ``is_selected``.
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
    while cursor <= end:
        rdr = by_date.get(cursor)
        min_key: str
        max_key: str
        if rdr is None:
            min_key = RegionDayRating.Rating.NO_RATING
            max_key = RegionDayRating.Rating.NO_RATING
            subdivision = ""
            has_bulletin = False
        else:
            min_key = rdr.min_rating
            max_key = rdr.max_rating
            subdivision = rdr.max_subdivision
            has_bulletin = (
                rdr.source_bulletin_id is not None
                and max_key != RegionDayRating.Rating.NO_RATING
            )
        is_today = cursor == today
        is_selected = cursor == page_date and not is_today
        cells.append(
            SeasonCell(
                date=cursor,
                min_rating_key=min_key,
                max_rating_key=max_key,
                subdivision=subdivision,
                has_bulletin=has_bulletin,
                is_today=is_today,
                is_selected=is_selected,
            )
        )
        cursor += datetime.timedelta(days=1)

    columns = _pack_into_columns(cells, start)
    month_labels = _month_label_indices(columns)
    return SeasonGrid(
        columns=columns,
        month_labels=month_labels,
        season_label=season_label,
    )


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
