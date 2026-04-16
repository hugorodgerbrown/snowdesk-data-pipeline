"""
tests/pipeline/test_day_rating_model.py — Tests for the RegionDayRating model.

Covers:
  - to_string() format for uniform days (min == max)
  - to_string() format for variable days (min != max)
  - Queryset ordering
  - for_region_month() returns only in-range rows
  - unique_together raises IntegrityError on duplicate (region, date)
  - Admin is registered for the model
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib import admin
from django.db import IntegrityError

from pipeline.models import RegionDayRating
from tests.factories import RegionDayRatingFactory, RegionFactory


@pytest.mark.django_db
class TestRegionDayRatingToString:
    """Tests for the to_string() method."""

    def test_to_string_no_subdivision_uniform(self) -> None:
        """to_string returns '<region_id> <date> <rating>' for a uniform day."""
        region = RegionFactory.create(region_id="CH-4115")
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
        region = RegionFactory.create(region_id="CH-7111")
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
        region = RegionFactory.create(region_id="CH-5000")
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
        region = RegionFactory.create(region_id="CH-0001")
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
        region_a = RegionFactory.create(region_id="CH-1111")
        region_b = RegionFactory.create(region_id="CH-2222")

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
        region = RegionFactory.create(region_id="CH-4115")
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
        region_a = RegionFactory.create(region_id="CH-4115")
        region_b = RegionFactory.create(region_id="CH-9999")
        RegionDayRatingFactory.create(region=region_a, date=datetime.date(2026, 3, 10))
        RegionDayRatingFactory.create(region=region_b, date=datetime.date(2026, 3, 10))

        qs = RegionDayRating.objects.for_region_month(region_a, 2026, 3)
        assert qs.count() == 1
        row = qs.first()
        assert row is not None
        assert row.region_id == region_a.pk

    def test_covers_full_month_boundaries(self) -> None:
        """for_region_month includes rows on the 1st and last day of the month."""
        region = RegionFactory.create(region_id="CH-5555")
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
class TestUniqueTogether:
    """Tests for the unique_together constraint on (region, date)."""

    def test_duplicate_raises_integrity_error(self) -> None:
        """Creating two rows for the same (region, date) raises IntegrityError."""
        region = RegionFactory.create(region_id="CH-4115")
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 1))
        with pytest.raises(IntegrityError):
            RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 1, 1))


class TestAdminRegistration:
    """Tests that RegionDayRating is registered in the Django admin."""

    def test_admin_registered(self) -> None:
        """RegionDayRating appears in admin.site._registry."""
        assert RegionDayRating in admin.site._registry
