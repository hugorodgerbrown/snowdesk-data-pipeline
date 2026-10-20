"""
tests/public/test_season_calendar.py — Tests for build_season_grid, season_header,
and build_season_ribbon.

Covers build_season_grid:
  - Empty grid when ``today + 1`` precedes ``SEASON_START_DATE``.
  - Column count and weeks-as-columns layout.
  - Leading ``None`` padding when the season starts mid-week.
  - Trailing ``None`` padding after ``today + 1``.
  - Inclusion of ``today`` and ``today + 1``.
  - Missing-row dates render as inert ``no_rating`` cells.
  - Rows with a ``source_bulletin`` render as interactive (``has_bulletin``).
  - ``is_today`` flag set only on the today cell.
  - ``is_selected`` always ``False`` from the builder (selection is client-side only).
  - Month-label boundaries align with the column where the month flips.
  - ``season_header`` returns the label dict or None.

SNOW-252 — peak semantics:
  - Calendar cell max_rating_key reflects the day's peak on split days.
  - Two-period escalating day (morning=2, afternoon=3) → max_rating_key=considerable.

SNOW-314 — build_season_ribbon:
  - Empty ribbon when today precedes SEASON_START_DATE.
  - Flat ordered day list from start through today (inclusive).
  - No-data days → max_rating_key='no_rating', has_bulletin=False.
  - Days with a source_bulletin → has_bulletin=True, correct rating key.
  - Days without a source_bulletin → has_bulletin=False.
  - season_label matches the SLF two-year format.
  - Cross-region isolation (other region's rows not included).

SNOW-1126 — the resort page's season strip:
  - season_window boundaries: 31 Oct, 1 Nov, April, an October date that
    answers with the previous season, and the SEASON_START_DATE clamp.
  - build_season_grid(window=…) runs from the window start to the earlier
    of its end and today + 1, carrying the window's label.
  - summarise_season counts days with a bulletin, days by level, and days
    at Considerable or higher.

SNOW-1153 — the season summary carries no season-wide peak.

SNOW-1128 — month labels come from Django's translated month names, in the
active language.

SNOW-1158 — the month-shaped view (build_season_months, season_ring_date):
  - Every month in the span drawn in full: 30/31-day months, February's 28
    and a leap year's 29. The span runs from the season start through
    April, past it only to the last month with a bulletin, never beyond
    tomorrow's month.
  - An in-progress season draws its days after today as "future".
  - The ring: today in season, the first High day of a finished season, none
    when the season never reached High, and only where asked.
  - Two-level days (min/max, AM/PM, ALBINA bands) draw as "split", lower
    level left and the peak right; data-level stays the peak.
  - season_header's four-digit name and level keys; SeasonSummary.key_counts.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any

import pytest
from django.test import override_settings
from django.utils import translation

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.public.season_calendar import (
    SEASON_AXIS,
    SEASON_LEVEL_KEYS,
    SeasonCell,
    SeasonGrid,
    SeasonSummary,
    SeasonWindow,
    build_season_grid,
    build_season_months,
    build_season_ribbon,
    season_header,
    season_ring_date,
    season_window,
    summarise_season,
)
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
)


@pytest.mark.django_db
class TestBuildSeasonGrid:
    """Tests for the build_season_grid helper."""

    @override_settings(SEASON_START_DATE=datetime.date(2026, 1, 5))
    def test_empty_when_today_before_season_start(self) -> None:
        """Returns an empty (falsy) grid when end < SEASON_START_DATE."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        # today + 1 = 2026-01-04, season starts 2026-01-05.
        today = datetime.date(2026, 1, 3)
        grid = build_season_grid(region, today=today)
        assert grid.columns == []
        assert grid.month_labels == []
        assert not grid

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_no_leading_pad_when_start_is_monday(self) -> None:
        """The leading column has no None padding when the season starts on Monday."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        # 2025-11-03 is a Monday; today = same Monday → end = Tue 2025-11-04.
        today = datetime.date(2025, 11, 3)
        grid = build_season_grid(region, today=today)

        # Two days fit in a single column with 5 trailing Nones.
        assert len(grid.columns) == 1
        column = grid.columns[0]
        assert column[0] is not None and column[0].date == datetime.date(2025, 11, 3)
        assert column[1] is not None and column[1].date == datetime.date(2025, 11, 4)
        assert all(c is None for c in column[2:])

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 5))
    def test_leading_pad_when_start_is_midweek(self) -> None:
        """Leading None cells fill the column when SEASON_START_DATE is not a Monday."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        # 2025-11-05 is a Wednesday → 2 leading Nones.
        today = datetime.date(2025, 11, 5)
        grid = build_season_grid(region, today=today)

        column = grid.columns[0]
        assert column[0] is None
        assert column[1] is None
        assert column[2] is not None
        assert column[2].date == datetime.date(2025, 11, 5)

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_today_and_tomorrow_present(self) -> None:
        """Both today and today + 1 appear in the grid."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 10)
        grid = build_season_grid(region, today=today)

        all_cells = [c for col in grid.columns for c in col if c is not None]
        dates = {c.date for c in all_cells}
        assert today in dates
        assert today + datetime.timedelta(days=1) in dates

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_missing_row_renders_as_no_rating(self) -> None:
        """Days without a RegionDayRating render as no_rating, has_bulletin=False."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        # No factory rows created.
        grid = build_season_grid(region, today=today)

        cells = [c for col in grid.columns for c in col if c is not None]
        assert len(cells) > 0
        for cell in cells:
            assert cell.has_bulletin is False
            assert cell.min_rating_key == RegionDayRating.Rating.NO_RATING
            assert cell.max_rating_key == RegionDayRating.Rating.NO_RATING

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_row_with_source_bulletin_is_interactive(self) -> None:
        """Days with a RegionDayRating row + source_bulletin set has_bulletin=True."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2025, 11, 4),
            min_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_subdivision="+",
            source_bulletin=bulletin,
        )

        grid = build_season_grid(region, today=today)
        target = next(
            c
            for col in grid.columns
            for c in col
            if c is not None and c.date == datetime.date(2025, 11, 4)
        )
        assert target.has_bulletin is True
        assert target.min_rating_key == RegionDayRating.Rating.CONSIDERABLE
        assert target.max_rating_key == RegionDayRating.Rating.CONSIDERABLE
        assert target.subdivision == "+"

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_row_without_source_bulletin_is_inert(self) -> None:
        """A RegionDayRating row with source_bulletin=None still renders as inert."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2025, 11, 4),
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.LOW,
            source_bulletin=None,
        )

        grid = build_season_grid(region, today=today)
        target = next(
            c
            for col in grid.columns
            for c in col
            if c is not None and c.date == datetime.date(2025, 11, 4)
        )
        assert target.has_bulletin is False

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_is_today_flag_set_only_on_today(self) -> None:
        """Only the cell whose date equals today carries is_today."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        grid = build_season_grid(region, today=today)

        today_cells = [
            c for col in grid.columns for c in col if c is not None and c.is_today
        ]
        assert len(today_cells) == 1
        assert today_cells[0].date == today

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_is_selected_always_false_from_builder(self) -> None:
        """is_selected is always False from the builder — selection is client-side."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 10)
        grid = build_season_grid(region, today=today)

        for col in grid.columns:
            for c in col:
                if c is not None:
                    assert c.is_selected is False

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_month_labels_parallel_to_columns(self) -> None:
        """month_labels has one entry per column, with month abbreviations on flips."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        # Span Nov → Dec → Jan to exercise three month boundaries.
        today = datetime.date(2026, 1, 12)
        grid = build_season_grid(region, today=today)

        assert len(grid.month_labels) == len(grid.columns)
        # First labelled column = Nov.
        labels = [(idx, lbl) for idx, lbl in enumerate(grid.month_labels) if lbl]
        assert labels[0] == (0, "Nov")
        seen = [lbl for _idx, lbl in labels]
        assert "Dec" in seen
        assert "Jan" in seen
        # Each label corresponds to a column whose first dated cell is in that month.
        for idx, label in labels:
            first_dated = next((c for c in grid.columns[idx] if c is not None), None)
            assert first_dated is not None
            month_int = [
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
            ].index(label) + 1
            assert first_dated.date.month == month_int

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_month_labels_follow_the_active_language(self) -> None:
        """SNOW-1128: the labels are Django's month names in the active language."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2026, 1, 12)
        with translation.override("de"):
            grid = build_season_grid(region, today=today)
        labels = [lbl for lbl in grid.month_labels if lbl]
        assert labels == ["Nov", "Dez", "Jan"]

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_excludes_other_regions(self) -> None:
        """Rows from other regions do not contaminate the focal region's grid."""
        region_a = MicroRegionFactory.create(region_id="CH-4115")
        region_b = MicroRegionFactory.create(region_id="CH-9999")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region_b,
            date=datetime.date(2025, 11, 4),
            min_rating=RegionDayRating.Rating.HIGH,
            max_rating=RegionDayRating.Rating.HIGH,
            source_bulletin=bulletin,
        )

        grid = build_season_grid(region_a, today=today)
        target = next(
            c
            for col in grid.columns
            for c in col
            if c is not None and c.date == datetime.date(2025, 11, 4)
        )
        assert target.has_bulletin is False
        assert target.max_rating_key == RegionDayRating.Rating.NO_RATING


@pytest.mark.django_db
class TestSeasonHeader:
    """Tests for the season_header helper."""

    @override_settings(SEASON_START_DATE=datetime.date(2026, 1, 5))
    def test_returns_none_before_season_start(self) -> None:
        """Returns None when today + 1 < SEASON_START_DATE."""
        today = datetime.date(2026, 1, 3)  # today + 1 = Jan 4, start = Jan 5
        assert season_header(today) is None

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_returns_dict_when_season_active(self) -> None:
        """Returns a dict with season_label when today + 1 >= SEASON_START_DATE."""
        today = datetime.date(2025, 11, 3)
        result = season_header(today)
        assert result is not None
        assert result["season_label"] == "25/26"

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_season_label_format(self) -> None:
        """season_label is two-digit years separated by a slash."""
        today = datetime.date(2026, 3, 15)
        result = season_header(today)
        assert result is not None
        assert result["season_label"] == "25/26"

    @override_settings(SEASON_START_DATE=datetime.date(2026, 1, 4))
    def test_returns_dict_on_season_start_day(self) -> None:
        """Returns a dict when today + 1 == SEASON_START_DATE (boundary)."""
        today = datetime.date(2026, 1, 3)  # today + 1 = Jan 4 = start
        result = season_header(today)
        assert result is not None
        assert "season_label" in result


# ---------------------------------------------------------------------------
# SNOW-252 — peak semantics regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonCalendarPeakSemantics:
    """
    Regression tests asserting that the season calendar always reflects the
    day's peak (max_rating) rather than the morning (min_rating) level.

    See docs/compressed-views-rating-rule.md for the canonical convention.
    """

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_split_day_cell_max_rating_key_is_peak(self) -> None:
        """
        SNOW-252 canonical fixture: morning=moderate (2), afternoon=considerable (3).

        The SeasonCell for that day must carry max_rating_key="considerable"
        (the afternoon peak), not "moderate" (the morning level).
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
        )

        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )

        # max_rating_key must be the peak (considerable).
        assert cell.max_rating_key == RegionDayRating.Rating.CONSIDERABLE
        # min_rating_key carries the morning level.
        assert cell.min_rating_key == RegionDayRating.Rating.MODERATE

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_split_day_cell_min_rating_key_is_not_peak(self) -> None:
        """
        Regression: min_rating_key and max_rating_key are distinct on split days.

        If the implementation accidentally used min_rating as the displayed
        value, the calendar would show the morning level (lower) instead of
        the peak. Both must be present and differ.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.HIGH,
            source_bulletin=bulletin,
        )

        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )

        assert cell.max_rating_key == RegionDayRating.Rating.HIGH
        assert cell.min_rating_key == RegionDayRating.Rating.LOW
        # They must differ on a split day.
        assert cell.max_rating_key != cell.min_rating_key


# ---------------------------------------------------------------------------
# SNOW-292 — source and bands on SeasonCell
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonCellSourceAndBands:
    """Tests for source and bands fields added to SeasonCell in SNOW-292."""

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_slf_cell_source_is_slf(self) -> None:
        """SLF RegionDayRating row propagates source='SLF' to SeasonCell."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.LOW,
            source_bulletin=bulletin,
            source=Bulletin.Source.SLF,
        )
        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )
        assert cell.source == Bulletin.Source.SLF

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_slf_cell_bands_is_none(self) -> None:
        """SLF cell has bands=None."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.LOW,
            source_bulletin=bulletin,
            source=Bulletin.Source.SLF,
            bands=None,
        )
        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )
        assert cell.bands is None

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_albina_cell_band_mode_elevation_only(self) -> None:
        """ALBINA cell with 2 same-period bands has band_mode='elevation-only'."""
        region = MicroRegionFactory.create(region_id="AT-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        bands = [
            {
                "band_id": "above-2200",
                "label": "Above 2200 m",
                "rating_key": "considerable",
                "time_period": "all_day",
            },
            {
                "band_id": "below-2200",
                "label": "Below 2200 m",
                "rating_key": "low",
                "time_period": "all_day",
            },
        ]
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
            source=Bulletin.Source.ALBINA,
            bands=bands,
        )
        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )
        assert cell.source == Bulletin.Source.ALBINA
        assert cell.band_mode == "elevation-only"
        assert cell.bands is not None
        assert len(cell.bands) == 2

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_albina_cell_band_mode_elevation_time(self) -> None:
        """ALBINA cell with 4 bands across 2 periods has band_mode='elevation-time'."""
        region = MicroRegionFactory.create(region_id="AT-4116")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        bands = [
            {
                "band_id": "above-2500",
                "label": "Above 2500 m",
                "rating_key": "considerable",
                "time_period": "earlier",
            },
            {
                "band_id": "below-2500",
                "label": "Below 2500 m",
                "rating_key": "low",
                "time_period": "earlier",
            },
            {
                "band_id": "above-2800",
                "label": "Above 2800 m",
                "rating_key": "high",
                "time_period": "later",
            },
            {
                "band_id": "below-2800",
                "label": "Below 2800 m",
                "rating_key": "moderate",
                "time_period": "later",
            },
        ]
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.LOW,
            max_rating=RegionDayRating.Rating.HIGH,
            source_bulletin=bulletin,
            source=Bulletin.Source.ALBINA,
            bands=bands,
        )
        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )
        assert cell.band_mode == "elevation-time"

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_no_rating_cell_has_empty_source(self) -> None:
        """No-rating cell (no RegionDayRating row) has source='' and bands=None."""
        region = MicroRegionFactory.create(region_id="CH-4117")
        today = datetime.date(2025, 11, 5)
        grid = build_season_grid(region, today=today)
        any_cell = next(c for col in grid.columns for c in col if c is not None)
        assert any_cell.source == ""
        assert any_cell.bands is None

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_slf_cell_band_mode_is_empty(self) -> None:
        """SLF cell never carries a band_mode (returns empty string)."""
        region = MicroRegionFactory.create(region_id="CH-4118")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.MODERATE,
            source_bulletin=bulletin,
            source=Bulletin.Source.SLF,
            bands=None,
        )
        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )
        assert cell.band_mode == ""


# ---------------------------------------------------------------------------
# SNOW-291 — AM/PM time-split cell
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonCalendarTimeSplit:
    """Tests for the AM/PM time-split SeasonCell behaviour (SNOW-291)."""

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_time_split_cell_is_time_split_true(self) -> None:
        """
        A RegionDayRating row with both am_rating and pm_rating populated
        produces a SeasonCell where is_time_split == True.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.MODERATE,
            am_rating=RegionDayRating.Rating.MODERATE,
            pm_rating=RegionDayRating.Rating.MODERATE,
            source_bulletin=bulletin,
        )

        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )

        assert cell.is_time_split is True
        assert cell.am_rating_key == RegionDayRating.Rating.MODERATE
        assert cell.pm_rating_key == RegionDayRating.Rating.MODERATE

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_uniform_cell_is_time_split_false(self) -> None:
        """A RegionDayRating row without am/pm ratings produces is_time_split == False."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            # No am_rating / pm_rating set — defaults to None.
            source_bulletin=bulletin,
        )

        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )

        assert cell.is_time_split is False
        assert cell.am_rating_key == ""
        assert cell.pm_rating_key == ""

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_time_split_escalating_carries_correct_keys(self) -> None:
        """
        Escalating split: am=moderate, pm=considerable → keys are set correctly
        and is_time_split is True.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            am_rating=RegionDayRating.Rating.MODERATE,
            pm_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=bulletin,
        )

        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )

        assert cell.is_time_split is True
        assert cell.am_rating_key == RegionDayRating.Rating.MODERATE
        assert cell.pm_rating_key == RegionDayRating.Rating.CONSIDERABLE


# ---------------------------------------------------------------------------
# SNOW-314 — build_season_ribbon
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildSeasonRibbon:
    """Tests for the build_season_ribbon helper (SNOW-314)."""

    @override_settings(SEASON_START_DATE=datetime.date(2026, 1, 5))
    def test_empty_when_today_before_season_start(self) -> None:
        """Returns an empty (falsy) ribbon when today < SEASON_START_DATE."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2026, 1, 3)  # before Jan 5 start
        ribbon = build_season_ribbon(region, today=today)
        assert not ribbon
        assert ribbon.days == []

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_days_ordered_oldest_first(self) -> None:
        """Ribbon days run chronologically from season start through today."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 7)
        ribbon = build_season_ribbon(region, today=today)
        assert ribbon
        dates = [d.date for d in ribbon.days]
        assert dates == sorted(dates)
        assert dates[0] == datetime.date(2025, 11, 3)
        assert dates[-1] == today

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_today_included_tomorrow_excluded(self) -> None:
        """Ribbon runs through today inclusive; tomorrow is not in the strip."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        ribbon = build_season_ribbon(region, today=today)
        dates = {d.date for d in ribbon.days}
        assert today in dates
        assert today + datetime.timedelta(days=1) not in dates

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_missing_row_renders_as_no_rating(self) -> None:
        """Days without a RegionDayRating row appear as no_rating, has_bulletin=False."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        ribbon = build_season_ribbon(region, today=today)
        for day in ribbon.days:
            assert day.has_bulletin is False
            assert day.max_rating_key == RegionDayRating.Rating.NO_RATING

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_row_with_source_bulletin_is_interactive(self) -> None:
        """A day with a source_bulletin row has has_bulletin=True and the correct key."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            max_rating=RegionDayRating.Rating.HIGH,
            source_bulletin=bulletin,
        )
        ribbon = build_season_ribbon(region, today=today)
        day = next(d for d in ribbon.days if d.date == target)
        assert day.has_bulletin is True
        assert day.max_rating_key == RegionDayRating.Rating.HIGH

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_row_without_source_bulletin_is_inert(self) -> None:
        """A day with a RegionDayRating row but no source_bulletin has has_bulletin=False."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        target = datetime.date(2025, 11, 4)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            source_bulletin=None,
        )
        ribbon = build_season_ribbon(region, today=today)
        day = next(d for d in ribbon.days if d.date == target)
        assert day.has_bulletin is False

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_season_label_format(self) -> None:
        """season_label uses the SLF two-digit-year format."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        today = datetime.date(2025, 11, 5)
        ribbon = build_season_ribbon(region, today=today)
        assert ribbon.season_label == "25/26"

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 3))
    def test_cross_region_isolation(self) -> None:
        """Ratings from a different region do not contaminate the focal region's ribbon."""
        region_a = MicroRegionFactory.create(region_id="CH-4115")
        region_b = MicroRegionFactory.create(region_id="CH-9999")
        today = datetime.date(2025, 11, 5)
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region_b,
            date=datetime.date(2025, 11, 4),
            max_rating=RegionDayRating.Rating.HIGH,
            source_bulletin=bulletin,
        )
        ribbon = build_season_ribbon(region_a, today=today)
        day = next(d for d in ribbon.days if d.date == datetime.date(2025, 11, 4))
        assert day.has_bulletin is False
        assert day.max_rating_key == RegionDayRating.Rating.NO_RATING


class TestSeasonWindow:
    """Tests for season_window (SNOW-1126)."""

    @pytest.fixture(autouse=True)
    def _early_archive(self, settings: Any) -> None:
        """Put SEASON_START_DATE well before every window under test."""
        settings.SEASON_START_DATE = datetime.date(2020, 11, 1)

    def test_31_october_is_the_previous_season(self) -> None:
        """The last day before the season opens answers with the one just ended."""
        assert season_window(datetime.date(2026, 10, 31)) == SeasonWindow(
            datetime.date(2025, 11, 1), datetime.date(2026, 4, 30), "25/26"
        )

    def test_1_november_opens_the_new_season(self) -> None:
        """1 November is the first day of the new season."""
        assert season_window(datetime.date(2026, 11, 1)) == SeasonWindow(
            datetime.date(2026, 11, 1), datetime.date(2027, 4, 30), "26/27"
        )

    @pytest.mark.parametrize("day", [1, 30])
    def test_april_is_inside_the_running_season(self, day: int) -> None:
        """April, through the 30th, belongs to the season that opened in November."""
        assert season_window(datetime.date(2026, 4, day)) == SeasonWindow(
            datetime.date(2025, 11, 1), datetime.date(2026, 4, 30), "25/26"
        )

    @pytest.mark.parametrize(
        "today", [datetime.date(2026, 5, 1), datetime.date(2026, 10, 19)]
    )
    def test_off_season_dates_return_the_previous_season(
        self, today: datetime.date
    ) -> None:
        """May to October answers with the season that has just closed."""
        assert season_window(today) == SeasonWindow(
            datetime.date(2025, 11, 1), datetime.date(2026, 4, 30), "25/26"
        )

    def test_start_is_clamped_to_season_start_date(self, settings: Any) -> None:
        """The start never precedes SEASON_START_DATE; the label is the nominal season's."""
        settings.SEASON_START_DATE = datetime.date(2026, 1, 15)
        assert season_window(datetime.date(2026, 2, 1)) == SeasonWindow(
            datetime.date(2026, 1, 15), datetime.date(2026, 4, 30), "25/26"
        )


@pytest.mark.django_db
class TestWindowedSeasonGrid:
    """build_season_grid with a window, and summarise_season (SNOW-1126)."""

    @pytest.fixture(autouse=True)
    def _early_archive(self, settings: Any) -> None:
        """Put SEASON_START_DATE well before every window under test."""
        settings.SEASON_START_DATE = datetime.date(2020, 11, 1)

    def _rate(
        self,
        region: object,
        day: datetime.date,
        rating: str,
        bulletin: Bulletin | None,
    ) -> None:
        """Create one RegionDayRating row."""
        RegionDayRatingFactory.create(
            region=region,
            date=day,
            min_rating=rating,
            max_rating=rating,
            source_bulletin=bulletin,
        )

    def test_past_season_window_spans_november_to_april(self) -> None:
        """An October today draws the whole previous season, end to end."""
        region = MicroRegionFactory.create()
        today = datetime.date(2026, 10, 19)
        grid = build_season_grid(region, today, window=season_window(today))
        dated = [c for col in grid.columns for c in col if c is not None]
        assert dated[0].date == datetime.date(2025, 11, 1)
        assert dated[-1].date == datetime.date(2026, 4, 30)
        assert grid.season_label == "25/26"

    def test_running_season_window_stops_at_tomorrow(self) -> None:
        """Mid-season the grid ends at today + 1, not at 30 April."""
        region = MicroRegionFactory.create()
        today = datetime.date(2026, 1, 10)
        grid = build_season_grid(region, today, window=season_window(today))
        dated = [c for col in grid.columns for c in col if c is not None]
        assert dated[-1].date == datetime.date(2026, 1, 11)

    def test_window_after_its_end_is_empty(self) -> None:
        """A window whose clamped start passes its end builds an empty grid."""
        region = MicroRegionFactory.create()
        window = SeasonWindow(
            datetime.date(2026, 5, 1), datetime.date(2026, 4, 30), "25/26"
        )
        grid = build_season_grid(region, datetime.date(2026, 6, 1), window=window)
        assert not grid
        assert not summarise_season(grid)

    def test_summary_counts_days_by_level(self) -> None:
        """Counts only days with a bulletin, each at its day's level."""
        region = MicroRegionFactory.create()
        bulletin = BulletinFactory.create()
        self._rate(region, datetime.date(2025, 12, 1), "low", bulletin)
        self._rate(region, datetime.date(2025, 12, 2), "considerable", bulletin)
        self._rate(region, datetime.date(2025, 12, 3), "high", bulletin)
        self._rate(region, datetime.date(2025, 12, 4), "high", bulletin)
        # No source bulletin — not a day with a bulletin, even rated very high.
        self._rate(region, datetime.date(2025, 12, 5), "very_high", None)
        today = datetime.date(2026, 10, 19)
        summary = summarise_season(
            build_season_grid(region, today, window=season_window(today))
        )
        assert summary
        assert summary.season_label == "25/26"
        assert summary.days_with_bulletin == 4
        assert summary.level_counts == [("low", 1), ("considerable", 1), ("high", 2)]
        assert summary.considerable_or_higher == 3

    def test_summary_is_falsy_without_bulletins(self) -> None:
        """No day with a bulletin → a falsy summary with nothing counted."""
        region = MicroRegionFactory.create()
        today = datetime.date(2026, 10, 19)
        summary = summarise_season(
            build_season_grid(region, today, window=season_window(today))
        )
        assert not summary
        assert summary.considerable_or_higher == 0
        assert summary.level_counts == []

    def test_summary_has_no_season_peak(self) -> None:
        """
        No field names a season-wide peak (SNOW-1153).

        Nearly every resort reaches 3 or 4 at some point in a winter, so a
        season peak carries no information; the summary keeps the counts.
        """
        names = [field.name for field in dataclasses.fields(SeasonSummary)]
        assert not [name for name in names if name.startswith("peak")]


# ---------------------------------------------------------------------------
# SNOW-1158 — the month-shaped season calendar
# ---------------------------------------------------------------------------


def _cell(
    date: datetime.date,
    level: str | None,
    low: str | None = None,
    **extra: Any,
) -> SeasonCell:
    """Build one synthetic cell: ``level`` None is a day with no bulletin."""
    if level is None:
        return SeasonCell(
            date=date,
            min_rating_key="no_rating",
            max_rating_key="no_rating",
            subdivision="",
            has_bulletin=False,
        )
    return SeasonCell(
        date=date,
        min_rating_key=low or level,
        max_rating_key=level,
        subdivision="",
        has_bulletin=True,
        **extra,
    )


def _grid(cells: list[SeasonCell]) -> SeasonGrid:
    """Wrap cells in a grid; build_season_months reads only the cells."""
    return SeasonGrid(
        columns=[(cell,) for cell in cells],
        month_labels=[""] * len(cells),
        season_label="25/26",
    )


_WINDOW = SeasonWindow(datetime.date(2025, 11, 1), datetime.date(2026, 4, 30), "25/26")


class TestSeasonMonths:
    """build_season_months reshapes a grid into months × days (SNOW-1158)."""

    def test_window_draws_every_month_in_full(self) -> None:
        """Nov–Apr, each with its own number of days."""
        grid = _grid([_cell(datetime.date(2025, 12, 1), "low")])
        months = build_season_months(grid, datetime.date(2026, 6, 1), window=_WINDOW)
        assert [m.label for m in months.months] == [
            "Nov",
            "Dec",
            "Jan",
            "Feb",
            "Mar",
            "Apr",
        ]
        assert [len(m.days) for m in months.months] == [30, 31, 31, 28, 31, 30]
        assert months.months[3].days[-1].date == datetime.date(2026, 2, 28)
        assert months.season_label == "2025/26"
        assert months.axis == SEASON_AXIS

    def test_leap_year_february_has_29_days(self) -> None:
        """February 2028 is drawn with its 29th."""
        window = SeasonWindow(
            datetime.date(2027, 11, 1), datetime.date(2028, 4, 30), "27/28"
        )
        grid = _grid([_cell(datetime.date(2028, 2, 29), "high")])
        months = build_season_months(grid, datetime.date(2028, 6, 1), window=window)
        february = months.months[3]
        assert len(february.days) == 29
        assert february.days[28].state == "high"
        assert months.season_label == "2027/28"

    def test_axis_labels_one_and_every_fifth_day(self) -> None:
        """1, 5, 10 … 30 over 31 slots."""
        assert len(SEASON_AXIS) == 31
        assert [label for label in SEASON_AXIS if label] == [
            "1",
            "5",
            "10",
            "15",
            "20",
            "25",
            "30",
        ]

    def test_in_progress_season_draws_future_days_faint(self) -> None:
        """After today: "future"; on or before it, a missing bulletin is "none"."""
        today = datetime.date(2026, 1, 14)
        grid = _grid(
            [
                _cell(datetime.date(2026, 1, 13), None),
                _cell(datetime.date(2026, 1, 14), "considerable"),
                # Tomorrow, not yet published.
                _cell(datetime.date(2026, 1, 15), None),
            ]
        )
        months = build_season_months(grid, today, window=_WINDOW)
        january = {d.date.day: d for d in months.months[2].days}
        assert january[13].state == "none"
        assert january[14].state == "considerable"
        assert january[15].state == "future"
        assert months.months[5].days[-1].state == "future"
        # A day of the window before the grid's data is "none", not "future".
        assert months.months[0].days[0].state == "none"

    def test_published_tomorrow_draws_its_level(self) -> None:
        """A bulletin already out for tomorrow draws, not "future"."""
        today = datetime.date(2026, 1, 14)
        grid = _grid([_cell(datetime.date(2026, 1, 15), "moderate")])
        months = build_season_months(grid, today, window=_WINDOW)
        assert months.months[2].days[14].state == "moderate"

    def test_without_a_window_the_span_is_the_season(self) -> None:
        """The bulletin page's grid: its first month through April."""
        grid = _grid(
            [
                _cell(datetime.date(2025, 11, 20), "low"),
                _cell(datetime.date(2026, 1, 3), "low"),
            ]
        )
        months = build_season_months(grid, datetime.date(2026, 1, 2))
        assert [m.first.month for m in months.months] == [11, 12, 1, 2, 3, 4]
        assert months.months[0].days[0].state == "none"
        assert months.months[-1].days[-1].state == "future"
        assert months.ring_date is None
        assert not any(d.is_ring for m in months.months for d in m.days)

    def test_out_of_season_the_summer_is_not_drawn(self) -> None:
        """October, grid running to tomorrow: Nov–Apr only, six rows."""
        today = datetime.date(2026, 10, 20)
        cells = [_cell(datetime.date(2025, 11, 1), "low")]
        cells += [
            _cell(datetime.date(2026, 5, 1) + datetime.timedelta(days=n), None)
            for n in range(174)
        ]
        months = build_season_months(_grid(cells), today)
        assert [m.first.month for m in months.months] == [11, 12, 1, 2, 3, 4]

    def test_a_may_bulletin_extends_to_may_only(self) -> None:
        """A bulletin after April adds its month and stops there."""
        today = datetime.date(2026, 10, 20)
        cells = [
            _cell(datetime.date(2025, 11, 1), "low"),
            _cell(datetime.date(2026, 5, 4), "moderate"),
            _cell(datetime.date(2026, 7, 1), None),
        ]
        months = build_season_months(_grid(cells), today)
        assert [m.first.month for m in months.months][-2:] == [4, 5]
        assert months.months[-1].days[3].state == "moderate"
        # The window gives the same months on the resort page.
        windowed = build_season_months(_grid(cells), today, window=_WINDOW)
        assert [m.first for m in windowed.months] == [m.first for m in months.months]

    def test_extension_never_passes_tomorrow(self) -> None:
        """Past April the span stops at tomorrow's month."""
        today = datetime.date(2026, 5, 10)
        cells = [
            _cell(datetime.date(2025, 11, 1), "low"),
            # Not something ingest produces; the cap holds regardless.
            _cell(datetime.date(2026, 6, 20), "low"),
        ]
        months = build_season_months(_grid(cells), today)
        assert months.months[-1].first == datetime.date(2026, 5, 1)

    def test_empty_grid_without_window_is_falsy(self) -> None:
        """Nothing to draw → a falsy view."""
        empty = SeasonGrid(columns=[], month_labels=[], season_label="25/26")
        assert not build_season_months(empty, datetime.date(2026, 1, 1))

    def test_ring_marks_the_given_day_only(self) -> None:
        """Exactly one day carries is_ring."""
        today = datetime.date(2026, 1, 14)
        grid = _grid([_cell(today, "considerable")])
        months = build_season_months(grid, today, window=_WINDOW, ring_date=today)
        ringed = [d.date for m in months.months for d in m.days if d.is_ring]
        assert ringed == [today]
        assert months.ring_date == today


class TestSeasonRingDate:
    """season_ring_date — today in season, the first High day after it."""

    grid = _grid(
        [
            _cell(datetime.date(2026, 1, 3), "considerable"),
            _cell(datetime.date(2026, 1, 9), "high"),
            _cell(datetime.date(2026, 2, 1), "very_high"),
        ]
    )

    def test_in_season_rings_today(self) -> None:
        """A running season rings today, High days or not."""
        today = datetime.date(2026, 3, 2)
        assert season_ring_date(self.grid, _WINDOW, today) == today

    def test_finished_season_rings_the_first_high_day(self) -> None:
        """After 30 April: the first day at High or above."""
        assert season_ring_date(
            self.grid, _WINDOW, datetime.date(2026, 10, 19)
        ) == datetime.date(2026, 1, 9)

    def test_very_high_counts_as_high(self) -> None:
        """A season whose first day above Considerable was Very high rings it."""
        grid = _grid([_cell(datetime.date(2026, 2, 1), "very_high")])
        assert season_ring_date(
            grid, _WINDOW, datetime.date(2026, 10, 19)
        ) == datetime.date(2026, 2, 1)

    def test_finished_season_without_high_rings_nothing(self) -> None:
        """No High day → no ring."""
        grid = _grid([_cell(datetime.date(2026, 1, 3), "considerable")])
        assert season_ring_date(grid, _WINDOW, datetime.date(2026, 10, 19)) is None


class TestSeasonMonthsSplitDays:
    """Two levels in one day draw as one split cell (SNOW-1158)."""

    def _day(self, cell: SeasonCell) -> Any:
        """Return the MonthDay for ``cell``'s date."""
        months = build_season_months(
            _grid([cell]), datetime.date(2026, 6, 1), window=_WINDOW
        )
        return next(d for m in months.months for d in m.days if d.date == cell.date)

    def test_afternoon_rise_is_split_with_the_peak_right(self) -> None:
        """min moderate / max considerable → lo moderate, hi considerable."""
        day = self._day(_cell(datetime.date(2026, 1, 5), "considerable", "moderate"))
        assert day.state == "split"
        assert (day.low_key, day.high_key) == ("moderate", "considerable")
        # The compressed-views rule: the day reads as its peak.
        assert day.level == "considerable"

    def test_am_higher_than_pm_is_split(self) -> None:
        """A falling day (am above pm) is still two levels, peak right."""
        day = self._day(
            _cell(
                datetime.date(2026, 1, 5),
                "considerable",
                am_rating_key="considerable",
                pm_rating_key="moderate",
            )
        )
        assert day.state == "split"
        assert (day.low_key, day.high_key) == ("moderate", "considerable")

    def test_albina_bands_split_lowest_to_peak(self) -> None:
        """Four bands draw the lowest and the peak."""
        bands = [
            {"band_id": "a", "rating_key": "considerable", "time_period": "earlier"},
            {"band_id": "b", "rating_key": "low", "time_period": "earlier"},
            {"band_id": "c", "rating_key": "high", "time_period": "later"},
            {"band_id": "d", "rating_key": "moderate", "time_period": "later"},
        ]
        day = self._day(
            _cell(
                datetime.date(2026, 1, 5), "high", "low", source="ALBINA", bands=bands
            )
        )
        assert (day.state, day.low_key, day.high_key) == ("split", "low", "high")

    def test_equal_levels_are_not_split(self) -> None:
        """AM = PM = max is one level."""
        day = self._day(
            _cell(
                datetime.date(2026, 1, 5),
                "moderate",
                am_rating_key="moderate",
                pm_rating_key="moderate",
            )
        )
        assert day.state == "moderate"
        assert (day.low_key, day.high_key) == ("", "")

    def test_a_level_above_the_peak_never_reaches_the_drawing(self) -> None:
        """The peak is max_rating_key; a higher AM never replaces it."""
        day = self._day(
            _cell(
                datetime.date(2026, 1, 5),
                "moderate",
                am_rating_key="high",
                pm_rating_key="moderate",
            )
        )
        assert day.state == "moderate"
        assert day.level == "moderate"

    def test_no_bulletin_day_is_never_split(self) -> None:
        """A day with no bulletin draws "none" with a no_rating level."""
        day = self._day(_cell(datetime.date(2026, 1, 5), None))
        assert (day.state, day.level, day.has_bulletin) == ("none", "no_rating", False)


class TestSeasonCalendarKeyAndHeader:
    """The key's levels and counts, and season_header's additions (SNOW-1158)."""

    def test_key_counts_low_to_high_with_zeros(self) -> None:
        """Low … High always, a zero included; no Very high when none occurred."""
        summary = summarise_season(
            _grid(
                [
                    _cell(datetime.date(2026, 1, 1), "low"),
                    _cell(datetime.date(2026, 1, 2), "considerable"),
                    _cell(datetime.date(2026, 1, 3), "considerable"),
                ]
            )
        )
        assert summary.key_counts == [
            ("low", 1),
            ("moderate", 0),
            ("considerable", 2),
            ("high", 0),
        ]

    def test_key_counts_add_very_high_when_it_occurred(self) -> None:
        """A Very high day puts the fifth level in the key."""
        summary = summarise_season(
            _grid([_cell(datetime.date(2026, 1, 1), "very_high")])
        )
        assert summary.key_counts[-1] == ("very_high", 1)

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    def test_season_header_names_the_season_and_the_levels(self) -> None:
        """The sheet's eyebrow name and the readout's level keys."""
        result = season_header(datetime.date(2026, 3, 15))
        assert result is not None
        assert result["season_name"] == "2025/26"
        assert result["levels"] == SEASON_LEVEL_KEYS
        assert SEASON_LEVEL_KEYS[0] == "no_rating"


@pytest.mark.django_db
class TestSeasonMonthsFromTheDatabase:
    """build_season_months over a real build_season_grid (SNOW-1158)."""

    @override_settings(SEASON_START_DATE=datetime.date(2024, 11, 1))
    def test_resort_window_mid_season(self) -> None:
        """Rated days draw their level, today is ringed, tomorrow on is faint."""
        region = MicroRegionFactory.create()
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 1, 5),
            min_rating="moderate",
            max_rating="considerable",
            source_bulletin=bulletin,
        )
        today = datetime.date(2026, 1, 10)
        window = season_window(today)
        grid = build_season_grid(region, today, window=window)
        months = build_season_months(
            grid, today, window=window, ring_date=season_ring_date(grid, window, today)
        )
        january = months.months[2].days
        assert january[4].state == "split"
        assert january[9].is_ring
        assert january[10].state == "future"
        assert january[8].state == "none"
