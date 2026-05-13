"""
tests/public/test_season_calendar.py — Tests for build_season_grid and season_header.

Covers:
  - Empty grid when ``today + 1`` precedes ``SEASON_START_DATE``.
  - Column count and weeks-as-columns layout.
  - Leading ``None`` padding when the season starts mid-week.
  - Trailing ``None`` padding after ``today + 1``.
  - Inclusion of ``today`` and ``today + 1``.
  - Missing-row dates render as inert ``no_rating`` cells.
  - Rows with a ``source_bulletin`` render as interactive (``has_bulletin``).
  - ``is_today`` flag set only on the today cell.
  - ``is_selected`` always ``False`` from the builder (selection is client-side).
  - Month-label boundaries align with the column where the month flips.
  - ``season_header`` returns the label dict or None.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import override_settings

from bulletins.models import RegionDayRating
from public.season_calendar import build_season_grid, season_header
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
