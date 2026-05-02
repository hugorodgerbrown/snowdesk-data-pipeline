"""
tests/bulletins/test_weather_snapshot_model.py — Tests for the WeatherSnapshot model.

Covers:
  - Factory produces a valid instance via .create().
  - to_string() / __str__() format.
  - WeatherSnapshotQuerySet.for_date() filter.
  - unique_together constraint raises IntegrityError on duplicate (region, date).
  - ordering: newest date first, then region_id ascending.
  - Deleting the linked Region cascades to WeatherSnapshot.
"""

import datetime

import pytest
from django.db import IntegrityError

from bulletins.models import WeatherSnapshot
from tests.factories import RegionFactory, WeatherSnapshotFactory


@pytest.mark.django_db
class TestWeatherSnapshotFactory:
    """The factory produces valid, well-formed WeatherSnapshot instances."""

    def test_create_returns_weather_snapshot(self) -> None:
        """WeatherSnapshotFactory.create() returns a persisted WeatherSnapshot."""
        snapshot = WeatherSnapshotFactory.create()
        assert isinstance(snapshot, WeatherSnapshot)
        assert snapshot.pk is not None

    def test_defaults_are_sensible(self) -> None:
        """Default factory values satisfy model constraints."""
        snapshot = WeatherSnapshotFactory.create()
        assert snapshot.weather_code == 0
        assert snapshot.sunrise.tzinfo is not None
        assert snapshot.sunset.tzinfo is not None
        assert snapshot.fetched_at is not None
        assert snapshot.region is not None

    def test_override_weather_code(self) -> None:
        """weather_code can be overridden at factory call time."""
        snapshot = WeatherSnapshotFactory.create(weather_code=61)
        assert snapshot.weather_code == 61


@pytest.mark.django_db
class TestWeatherSnapshotStr:
    """__str__ / to_string() format."""

    def test_to_string_format(self) -> None:
        """to_string() returns '<region_id> <date> wmo=<code>'."""
        region = RegionFactory.create(region_id="CH-4115")
        snapshot = WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=datetime.date(2026, 5, 1),
            weather_code=3,
        )
        assert snapshot.to_string() == "CH-4115 2026-05-01 wmo=3"

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        snapshot = WeatherSnapshotFactory.create()
        assert str(snapshot) == snapshot.to_string()


@pytest.mark.django_db
class TestWeatherSnapshotQuerySet:
    """WeatherSnapshotQuerySet.for_date() filters correctly."""

    def test_for_date_returns_matching_rows(self) -> None:
        """for_date() returns only snapshots with matching valid_for_date."""
        target = datetime.date(2026, 5, 1)
        other = datetime.date(2026, 4, 30)
        snap_target = WeatherSnapshotFactory.create(valid_for_date=target)
        WeatherSnapshotFactory.create(valid_for_date=other)

        result = WeatherSnapshot.objects.for_date(target)
        assert list(result) == [snap_target]

    def test_for_date_empty_when_no_match(self) -> None:
        """for_date() returns an empty queryset when no rows match."""
        WeatherSnapshotFactory.create(valid_for_date=datetime.date(2026, 4, 30))
        result = WeatherSnapshot.objects.for_date(datetime.date(2026, 5, 1))
        assert result.count() == 0


@pytest.mark.django_db
class TestWeatherSnapshotConstraints:
    """Model-level integrity constraints."""

    def test_unique_together_region_date(self) -> None:
        """Inserting two snapshots for the same (region, date) raises IntegrityError."""
        region = RegionFactory.create()
        target = datetime.date(2026, 5, 1)
        WeatherSnapshotFactory.create(region=region, valid_for_date=target)

        with pytest.raises(IntegrityError):
            WeatherSnapshotFactory.create(region=region, valid_for_date=target)

    def test_different_regions_same_date_allowed(self) -> None:
        """Two snapshots with different regions but the same date are allowed."""
        region_a = RegionFactory.create()
        region_b = RegionFactory.create()
        target = datetime.date(2026, 5, 1)
        WeatherSnapshotFactory.create(region=region_a, valid_for_date=target)
        snap_b = WeatherSnapshotFactory.create(region=region_b, valid_for_date=target)
        assert snap_b.pk is not None

    def test_same_region_different_dates_allowed(self) -> None:
        """Two snapshots for the same region on different dates are allowed."""
        region = RegionFactory.create()
        snap1 = WeatherSnapshotFactory.create(
            region=region, valid_for_date=datetime.date(2026, 5, 1)
        )
        snap2 = WeatherSnapshotFactory.create(
            region=region, valid_for_date=datetime.date(2026, 5, 2)
        )
        assert snap1.pk != snap2.pk

    def test_cascade_delete_from_region(self) -> None:
        """Deleting the linked Region also deletes the WeatherSnapshot."""
        region = RegionFactory.create()
        snap = WeatherSnapshotFactory.create(region=region)
        snap_pk = snap.pk
        region.delete()
        assert not WeatherSnapshot.objects.filter(pk=snap_pk).exists()


@pytest.mark.django_db
class TestWeatherSnapshotOrdering:
    """Default ordering: newest date first, then region_id ascending."""

    def test_ordering_newest_date_first(self) -> None:
        """Queryset returns most recent valid_for_date first."""
        region = RegionFactory.create()
        snap_old = WeatherSnapshotFactory.create(
            region=region, valid_for_date=datetime.date(2026, 4, 30)
        )
        snap_new = WeatherSnapshotFactory.create(
            region=region, valid_for_date=datetime.date(2026, 5, 1)
        )
        qs = list(WeatherSnapshot.objects.all())
        assert qs[0] == snap_new
        assert qs[1] == snap_old


@pytest.mark.django_db
class TestWeatherSnapshotTzAware:
    """Datetimes are stored and returned tz-aware."""

    def test_sunrise_is_tz_aware(self) -> None:
        """Sunrise returned from the DB carries timezone info."""
        snapshot = WeatherSnapshotFactory.create()
        snapshot.refresh_from_db()
        assert snapshot.sunrise.tzinfo is not None

    def test_sunset_is_tz_aware(self) -> None:
        """Sunset returned from the DB carries timezone info."""
        snapshot = WeatherSnapshotFactory.create()
        snapshot.refresh_from_db()
        assert snapshot.sunset.tzinfo is not None

    def test_fetched_at_is_tz_aware(self) -> None:
        """fetched_at returned from the DB carries timezone info."""
        snapshot = WeatherSnapshotFactory.create()
        snapshot.refresh_from_db()
        assert snapshot.fetched_at.tzinfo is not None
