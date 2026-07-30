"""
tests/bulletins/test_bulletin_grouping_model.py — Tests for BulletinGrouping.

Covers:
  - to_string() returns the expected human-readable format.
  - __str__ delegates to to_string().
  - Default ordering is by -target_date (most recent first).
  - for_date() queryset helper filters by target_date.
  - Admin class is registered for the model.
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib import admin

from apps.bulletins.models import BulletinGrouping
from tests.factories import BulletinGroupingFactory


@pytest.mark.django_db
class TestBulletinGroupingToString:
    """Tests for BulletinGrouping.to_string()."""

    def test_to_string_format(self) -> None:
        """to_string returns 'BulletinGrouping(<id>, <date>, <countries>)'."""
        grouping = BulletinGroupingFactory.create(
            target_date=datetime.date(2026, 1, 15),
            countries=["CH"],
        )
        s = grouping.to_string()
        assert "BulletinGrouping(" in s
        assert "2026-01-15" in s
        assert "CH" in s

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ is identical to to_string()."""
        grouping = BulletinGroupingFactory.create()
        assert str(grouping) == grouping.to_string()

    def test_to_string_includes_bulletin_id(self) -> None:
        """to_string embeds the bulletin_id from the linked bulletin."""
        grouping = BulletinGroupingFactory.create()
        assert grouping.bulletin.bulletin_id in grouping.to_string()


@pytest.mark.django_db
class TestBulletinGroupingOrdering:
    """Tests for BulletinGrouping default ordering (-target_date)."""

    def test_ordering_most_recent_first(self) -> None:
        """Rows are returned most-recent-date-first by default."""
        older = BulletinGroupingFactory.create(target_date=datetime.date(2026, 1, 14))
        newer = BulletinGroupingFactory.create(target_date=datetime.date(2026, 1, 15))

        rows = list(BulletinGrouping.objects.all())
        assert rows[0].pk == newer.pk
        assert rows[1].pk == older.pk


@pytest.mark.django_db
class TestBulletinGroupingQuerySet:
    """Tests for BulletinGroupingQuerySet helpers."""

    def test_for_date_returns_matching_rows(self) -> None:
        """for_date() returns only groupings whose target_date matches."""
        d1 = datetime.date(2026, 1, 14)
        d2 = datetime.date(2026, 1, 15)
        g1 = BulletinGroupingFactory.create(target_date=d1)
        BulletinGroupingFactory.create(target_date=d2)

        result = list(BulletinGrouping.objects.for_date(d1))

        assert len(result) == 1
        assert result[0].pk == g1.pk

    def test_for_date_returns_empty_when_no_match(self) -> None:
        """for_date() returns an empty queryset when no row matches."""
        BulletinGroupingFactory.create(target_date=datetime.date(2026, 1, 14))

        result = BulletinGrouping.objects.for_date(datetime.date(2026, 1, 16))

        assert result.count() == 0


class TestBulletinGroupingAdmin:
    """Tests for BulletinGrouping admin registration."""

    def test_admin_is_registered(self) -> None:
        """BulletinGrouping has an explicit admin class registered."""
        assert admin.site.is_registered(BulletinGrouping)
