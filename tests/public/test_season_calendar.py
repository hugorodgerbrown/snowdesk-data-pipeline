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
"""

from __future__ import annotations

import datetime

import pytest
from django.test import override_settings

from apps.bulletins.models import RegionDayRating
from apps.public.season_calendar import (
    build_season_grid,
    build_season_ribbon,
    season_header,
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
        """SLF RegionDayRating row propagates source='slf' to SeasonCell."""
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
            source="slf",
        )
        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )
        assert cell.source == "slf"

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
            source="slf",
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
            source="albina",
            bands=bands,
        )
        grid = build_season_grid(region, today=today)
        cell = next(
            c for col in grid.columns for c in col if c is not None and c.date == target
        )
        assert cell.source == "albina"
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
            source="albina",
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
            source="slf",
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
