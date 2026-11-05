"""
tests/bulletins/test_day_rating_model.py — Tests for the RegionDayRating model.

Covers:
  - to_string() format for uniform days (min == max)
  - to_string() format for variable days (min != max)
  - Queryset ordering
  - for_region_month() returns only in-range rows
  - for_region_range() returns only in-range rows (inclusive bounds)
  - rated() keeps rows with a source bulletin and a real rating
  - latest_run(): one run; a gap over 60 empty days
    splits; exactly 60 does not; unrated rows ignored; region filter; empty
  - first_rated_date() skips no_rating rows that earliest_date() counts
  - earliest_date() returns the region's earliest rated day, or None
  - unique_together raises IntegrityError on duplicate (region, date)
  - Admin is registered for the model
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib import admin
from django.db import IntegrityError

from apps.bulletins.models import RegionDayRating
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
)


@pytest.mark.django_db
class TestRegionDayRatingToString:
    """Tests for the to_string() method."""

    def test_to_string_no_subdivision_uniform(self) -> None:
        """to_string returns '<region_id> <date> <rating>' for a uniform day."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        rdr = RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 1, 15),
            min_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_subdivision="",
        )
        assert rdr.to_string() == "CH-4115 2026-01-15 considerable"

    def test_to_string_with_subdivision_uniform(self) -> None:
        """to_string appends the subdivision suffix on a uniform day."""
        region = MicroRegionFactory.create(region_id="CH-7111")
        rdr = RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 1),
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.MODERATE,
            max_subdivision="+",
        )
        assert rdr.to_string() == "CH-7111 2026-02-01 moderate+"

    def test_to_string_variable_day(self) -> None:
        """to_string uses '<min>..<max>' format when ratings differ."""
        region = MicroRegionFactory.create(region_id="CH-5000")
        rdr = RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 4, 16),
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_subdivision="+",
        )
        assert rdr.to_string() == "CH-5000 2026-04-16 moderate..considerable"

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ and to_string() return the same value."""
        rdr = RegionDayRatingFactory.create()
        assert str(rdr) == rdr.to_string()

    def test_to_string_no_rating(self) -> None:
        """to_string handles no_rating correctly."""
        region = MicroRegionFactory.create(region_id="CH-0001")
        rdr = RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 3, 5),
            min_rating=RegionDayRating.Rating.NO_RATING,
            max_rating=RegionDayRating.Rating.NO_RATING,
            max_subdivision="",
        )
        assert rdr.to_string() == "CH-0001 2026-03-05 no_rating"


@pytest.mark.django_db
class TestRegionDayRatingOrdering:
    """Tests for queryset ordering."""

    def test_ordering_by_date_desc_then_region(self) -> None:
        """Rows are ordered by -date, then region__region_id ascending."""
        region_a = MicroRegionFactory.create(region_id="CH-1111")
        region_b = MicroRegionFactory.create(region_id="CH-2222")

        rdr_old_a = RegionDayRatingFactory.create(
            region=region_a, date=datetime.date(2026, 1, 1)
        )
        rdr_old_b = RegionDayRatingFactory.create(
            region=region_b, date=datetime.date(2026, 1, 1)
        )
        rdr_new = RegionDayRatingFactory.create(
            region=region_a, date=datetime.date(2026, 2, 1)
        )

        rows = list(RegionDayRating.objects.all())
        assert rows[0].pk == rdr_new.pk
        assert rows[1].pk == rdr_old_a.pk
        assert rows[2].pk == rdr_old_b.pk


@pytest.mark.django_db
class TestForRegionMonth:
    """Tests for RegionDayRatingQuerySet.for_region_month()."""

    def test_returns_rows_within_month(self) -> None:
        """for_region_month returns all rows in the specified month."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        jan = RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 1, 15)
        )
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 2, 1))

        qs = RegionDayRating.objects.for_region_month(region, 2026, 1)
        pks = list(qs.values_list("pk", flat=True))
        assert jan.pk in pks
        assert len(pks) == 1

    def test_excludes_other_regions(self) -> None:
        """for_region_month excludes rows for different regions."""
        region_a = MicroRegionFactory.create(region_id="CH-4115")
        region_b = MicroRegionFactory.create(region_id="CH-9999")
        RegionDayRatingFactory.create(region=region_a, date=datetime.date(2026, 3, 10))
        RegionDayRatingFactory.create(region=region_b, date=datetime.date(2026, 3, 10))

        qs = RegionDayRating.objects.for_region_month(region_a, 2026, 3)
        assert qs.count() == 1
        row = qs.first()
        assert row is not None
        assert row.region_id == region_a.pk

    def test_covers_full_month_boundaries(self) -> None:
        """for_region_month includes rows on the 1st and last day of the month."""
        region = MicroRegionFactory.create(region_id="CH-5555")
        first = RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 4, 1)
        )
        last = RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 4, 30)
        )

        qs = RegionDayRating.objects.for_region_month(region, 2026, 4)
        pks = set(qs.values_list("pk", flat=True))
        assert first.pk in pks
        assert last.pk in pks


@pytest.mark.django_db
class TestForRegionRange:
    """Tests for RegionDayRatingQuerySet.for_region_range()."""

    def test_includes_lower_bound(self) -> None:
        """for_region_range is inclusive of the start date."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        on_start = RegionDayRatingFactory.create(
            region=region, date=datetime.date(2025, 11, 1)
        )
        RegionDayRatingFactory.create(region=region, date=datetime.date(2025, 10, 31))

        qs = RegionDayRating.objects.for_region_range(
            region,
            datetime.date(2025, 11, 1),
            datetime.date(2025, 11, 30),
        )
        pks = list(qs.values_list("pk", flat=True))
        assert pks == [on_start.pk]

    def test_includes_upper_bound(self) -> None:
        """for_region_range is inclusive of the end date."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        on_end = RegionDayRatingFactory.create(
            region=region, date=datetime.date(2026, 4, 30)
        )
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 5, 1))

        qs = RegionDayRating.objects.for_region_range(
            region,
            datetime.date(2025, 11, 1),
            datetime.date(2026, 4, 30),
        )
        pks = list(qs.values_list("pk", flat=True))
        assert pks == [on_end.pk]

    def test_excludes_other_regions(self) -> None:
        """for_region_range excludes rows for other regions in the same range."""
        region_a = MicroRegionFactory.create(region_id="CH-4115")
        region_b = MicroRegionFactory.create(region_id="CH-9999")
        RegionDayRatingFactory.create(region=region_a, date=datetime.date(2026, 1, 15))
        RegionDayRatingFactory.create(region=region_b, date=datetime.date(2026, 1, 15))

        qs = RegionDayRating.objects.for_region_range(
            region_a,
            datetime.date(2026, 1, 1),
            datetime.date(2026, 1, 31),
        )
        assert qs.count() == 1
        row = qs.first()
        assert row is not None
        assert row.region_id == region_a.pk

    def test_empty_range_returns_no_rows(self) -> None:
        """for_region_range returns nothing when start > end."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 15))

        qs = RegionDayRating.objects.for_region_range(
            region,
            datetime.date(2026, 2, 1),
            datetime.date(2026, 1, 1),
        )
        assert qs.count() == 0


def rated(region: MicroRegion, day: datetime.date) -> RegionDayRating:
    """Create a rated row: a bulletin behind it and a real rating."""
    return RegionDayRatingFactory.create(
        region=region, date=day, source_bulletin=BulletinFactory.create()
    )


def unrated(
    region: MicroRegion, day: datetime.date, *, bulletin: bool = True
) -> RegionDayRating:
    """Create a ``no_rating`` row, with or without a bulletin behind it."""
    return RegionDayRatingFactory.create(
        region=region,
        date=day,
        source_bulletin=BulletinFactory.create() if bulletin else None,
        min_rating=RegionDayRating.Rating.NO_RATING,
        max_rating=RegionDayRating.Rating.NO_RATING,
    )


@pytest.mark.django_db
class TestRated:
    """Tests for ``RegionDayRatingQuerySet.rated()`` (SNOW-1431)."""

    def test_keeps_only_rows_a_bulletin_rated(self) -> None:
        """No source bulletin, or a ``no_rating`` row, is not a rated row."""
        region = MicroRegionFactory.create()
        kept = rated(region, datetime.date(2026, 1, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 2))
        unrated(region, datetime.date(2026, 1, 3))

        assert list(RegionDayRating.objects.rated()) == [kept]


@pytest.mark.django_db
class TestLatestRun:
    """Tests for ``RegionDayRatingQuerySet.latest_run()`` (SNOW-1431)."""

    def test_empty_table_returns_none(self) -> None:
        """No rated row: no run."""
        assert RegionDayRating.objects.latest_run() is None

    def test_one_run_starts_at_its_first_day(self) -> None:
        """A single run, with short pauses, starts at its first rated day."""
        region = MicroRegionFactory.create()
        for day in (
            datetime.date(2025, 11, 20),
            datetime.date(2025, 12, 30),
            datetime.date(2026, 4, 30),
        ):
            rated(region, day)
        # Pauses of 39 and 120 days: the second splits, so start again.
        assert RegionDayRating.objects.latest_run() == (
            datetime.date(2026, 4, 30),
            datetime.date(2026, 4, 30),
        )
        RegionDayRating.objects.filter(date=datetime.date(2026, 4, 30)).delete()
        assert RegionDayRating.objects.latest_run() == (
            datetime.date(2025, 11, 20),
            datetime.date(2025, 12, 30),
        )

    def test_a_gap_over_sixty_days_splits_runs(self) -> None:
        """Sixty-one empty days start a new run; the later run's start wins."""
        region = MicroRegionFactory.create()
        rated(region, datetime.date(2026, 3, 1))
        # 2 March – 1 May is 61 empty days.
        rated(region, datetime.date(2026, 5, 2))
        rated(region, datetime.date(2026, 5, 10))

        assert RegionDayRating.objects.latest_run() == (
            datetime.date(2026, 5, 2),
            datetime.date(2026, 5, 10),
        )

    def test_a_gap_of_exactly_sixty_days_does_not_split(self) -> None:
        """Sixty empty days between two rated days keep them one run."""
        region = MicroRegionFactory.create()
        rated(region, datetime.date(2026, 3, 1))
        # 2 March – 30 April is 60 empty days.
        rated(region, datetime.date(2026, 5, 1))

        assert RegionDayRating.objects.latest_run() == (
            datetime.date(2026, 3, 1),
            datetime.date(2026, 5, 1),
        )

    def test_unrated_rows_are_ignored(self) -> None:
        """``no_rating`` rows and rows with no bulletin neither bridge nor start."""
        region = MicroRegionFactory.create()
        rated(region, datetime.date(2026, 1, 1))
        # These would bridge the gap if they counted.
        unrated(region, datetime.date(2026, 2, 15))
        unrated(region, datetime.date(2026, 3, 20), bulletin=False)
        rated(region, datetime.date(2026, 4, 30))
        # And these would end the run later.
        unrated(region, datetime.date(2026, 5, 10))

        assert RegionDayRating.objects.latest_run() == (
            datetime.date(2026, 4, 30),
            datetime.date(2026, 4, 30),
        )

    def test_runs_span_regions_unless_narrowed(self) -> None:
        """Every region feeds the run; ``region=`` reads one region's alone."""
        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        rated(region_a, datetime.date(2026, 1, 1))
        rated(region_b, datetime.date(2026, 2, 15))
        rated(region_a, datetime.date(2026, 4, 1))

        assert RegionDayRating.objects.latest_run() == (
            datetime.date(2026, 1, 1),
            datetime.date(2026, 4, 1),
        )
        assert RegionDayRating.objects.latest_run(region_a) == (
            datetime.date(2026, 4, 1),
            datetime.date(2026, 4, 1),
        )
        assert RegionDayRating.objects.latest_run(MicroRegionFactory.create()) is None


@pytest.mark.django_db
class TestFirstRatedDate:
    """Tests for ``RegionDayRatingQuerySet.first_rated_date()`` (SNOW-1431)."""

    def test_skips_unrated_rows_that_earliest_date_counts(self) -> None:
        """A ``no_rating`` row is the earliest date but not the first rated one."""
        region = MicroRegionFactory.create()
        unrated(region, datetime.date(2025, 11, 4), bulletin=False)
        rated(region, datetime.date(2025, 11, 20))
        rated(MicroRegionFactory.create(), datetime.date(2025, 11, 1))

        assert RegionDayRating.objects.first_rated_date(region) == datetime.date(
            2025, 11, 20
        )
        assert RegionDayRating.objects.earliest_date(region) == datetime.date(
            2025, 11, 4
        )

    def test_returns_none_without_a_rated_row(self) -> None:
        """A region with only unrated rows has no first rated day."""
        region = MicroRegionFactory.create()
        unrated(region, datetime.date(2025, 11, 4))

        assert RegionDayRating.objects.first_rated_date(region) is None


@pytest.mark.django_db
class TestUniqueTogether:
    """Tests for the unique_together constraint on (region, date)."""

    def test_duplicate_raises_integrity_error(self) -> None:
        """Creating two rows for the same (region, date) raises IntegrityError."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 1))
        with pytest.raises(IntegrityError):
            RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 1))


class TestAdminRegistration:
    """Tests that RegionDayRating is registered in the Django admin."""

    def test_admin_registered(self) -> None:
        """RegionDayRating appears in admin.site._registry."""
        assert RegionDayRating in admin.site._registry


@pytest.mark.django_db
class TestEarliestDate:
    """Tests for ``RegionDayRatingQuerySet.earliest_date()`` (SNOW-1344)."""

    def test_returns_none_for_a_region_with_no_rows(self) -> None:
        """A region with no rated day gives None rather than raising."""
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(date=datetime.date(2026, 1, 1))

        assert RegionDayRating.objects.earliest_date(region) is None

    def test_returns_the_regions_earliest_day(self) -> None:
        """The minimum date among the region's own rows wins."""
        region = MicroRegionFactory.create()
        for day in (15, 3, 9):
            RegionDayRatingFactory.create(
                region=region, date=datetime.date(2026, 1, day)
            )
        # An older row for another region does not leak in.
        RegionDayRatingFactory.create(date=datetime.date(2025, 12, 1))

        assert RegionDayRating.objects.earliest_date(region) == datetime.date(
            2026, 1, 3
        )
