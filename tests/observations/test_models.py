"""
tests/observations/test_models.py — Tests for observations.models.

Covers:
  FieldObservation field values, to_string, Meta.ordering.
  FieldObservationQuerySet.counts_for_region_day — single-type rows,
    multi-row aggregation, empty case, cross-region isolation,
    cross-day isolation.
  FieldObservationQuerySet.user_located_exists_for_region_day — returns True
    only for MANUAL/GPS_REFINED rows, False for GPS and empty.
  LOCATION_SOURCE choices — values are UPPER_CASE.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest
from django.utils import timezone

from observations.models import FieldObservation
from tests.factories import (
    FieldObservationFactory,
    MicroRegionFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestFieldObservationFields:
    """Verify field values and Meta settings."""

    def test_observation_type_round_trips(self) -> None:
        """observation_type is stored and retrieved correctly."""
        obs = FieldObservation.objects.create(
            user=UserFactory.create(),
            latitude=46.10,
            longitude=7.10,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        obs.refresh_from_db()
        assert obs.observation_type == FieldObservation.OBSERVATION_TYPE.WHUMPFING

    def test_observed_at_defaults_to_now(self) -> None:
        """observed_at defaults to the current time (tz-aware)."""
        before = timezone.now()
        obs = FieldObservationFactory.create()
        after = timezone.now()
        assert before <= obs.observed_at <= after
        assert obs.observed_at.tzinfo is not None

    def test_region_is_nullable(self) -> None:
        """region FK may be null (best-effort)."""
        obs = FieldObservation.objects.create(
            user=UserFactory.create(),
            latitude=46.10,
            longitude=7.10,
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
            region=None,
        )
        assert obs.region is None

    def test_accuracy_radius_km_is_nullable(self) -> None:
        """accuracy_radius_km may be null."""
        obs = FieldObservationFactory.create(accuracy_radius_km=None)
        assert obs.accuracy_radius_km is None

    def test_ordering_is_newest_first(self) -> None:
        """Queryset is ordered -observed_at (newest first)."""
        region = MicroRegionFactory.create()
        user = UserFactory.create()
        early = FieldObservationFactory.create(
            user=user,
            region=region,
            observed_at=timezone.now() - datetime.timedelta(hours=2),
        )
        late = FieldObservationFactory.create(
            user=user,
            region=region,
            observed_at=timezone.now(),
        )
        qs = FieldObservation.objects.all()
        assert list(qs[:2]) == [late, early]


@pytest.mark.django_db
class TestFieldObservationToString:
    """to_string and __str__ coverage."""

    def test_to_string_with_region(self) -> None:
        """to_string includes region name and display label when region is set."""
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        obs = FieldObservationFactory.create(
            region=region,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        result = obs.to_string()
        assert "Martigny-Verbier" in result
        assert "Whumpfing" in result

    def test_to_string_without_region(self) -> None:
        """to_string says 'unknown region' when region is null."""
        obs = FieldObservationFactory.create(
            region=None,
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        result = obs.to_string()
        assert "unknown region" in result

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string."""
        obs = FieldObservationFactory.create()
        assert str(obs) == obs.to_string()

    def test_to_string_uses_display_label(self) -> None:
        """to_string shows the human-readable label, not the raw value."""
        obs = FieldObservationFactory.create(
            observation_type=FieldObservation.OBSERVATION_TYPE.WIND_STRIATIONS,
        )
        assert "Wind striations" in obs.to_string()
        # Raw value should not appear in isolation (it appears as part of the label too,
        # but the key check is that the human-readable label is present).
        assert "WIND_STRIATIONS" not in obs.to_string()


@pytest.mark.django_db
class TestCountsForRegionDay:
    """counts_for_region_day — tally per observation type."""

    def _day(self) -> datetime.date:
        """Return a fixed test date."""
        return datetime.date(2026, 1, 15)

    def _at(self, d: datetime.date) -> datetime.datetime:
        """Return a tz-aware datetime for noon on date d."""
        return datetime.datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC)

    def test_returns_empty_dict_when_no_observations(self) -> None:
        """Returns empty dict when no observations exist for the region/day."""
        region = MicroRegionFactory.create()
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {}

    def test_counts_single_observation(self) -> None:
        """A single row with one type produces count of 1."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {"WHUMPFING": 1}

    def test_counts_multiple_observations_same_type(self) -> None:
        """Multiple reports with the same type sum correctly."""
        region = MicroRegionFactory.create()
        for _ in range(3):
            FieldObservationFactory.create(
                region=region,
                observed_at=self._at(self._day()),
                observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
            )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {"PINWHEELS": 3}

    def test_counts_multiple_reports_different_types(self) -> None:
        """Two reports with different types each contribute to their own count."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.FRACTURES,
        )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {"WHUMPFING": 1, "FRACTURES": 1}

    def test_isolates_by_region(self) -> None:
        """Reports for a different region are not counted."""
        region = MicroRegionFactory.create()
        other_region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=other_region,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {}

    def test_isolates_by_day(self) -> None:
        """Reports on a different day are not counted."""
        region = MicroRegionFactory.create()
        other_day = self._day() + datetime.timedelta(days=1)
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(other_day),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {}

    def test_multiple_reporters_same_day(self) -> None:
        """Two reporters submitting different types produce correct totals."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts["WHUMPFING"] == 2
        assert counts["PINWHEELS"] == 1
        assert "FRACTURES" not in counts


@pytest.mark.django_db
class TestObservationTypeChoices:
    """OBSERVATION_TYPE TextChoices sanity checks."""

    def test_all_expected_types_present(self) -> None:
        """All five canonical types are present in OBSERVATION_TYPE."""
        values = FieldObservation.OBSERVATION_TYPE.values
        assert "WHUMPFING" in values
        assert "PINWHEELS" in values
        assert "WIND_STRIATIONS" in values
        assert "FRACTURES" in values
        assert "SHOOTING_CRACKS" in values

    def test_values_are_upper_case(self) -> None:
        """All OBSERVATION_TYPE values are UPPER_CASE strings."""
        for value in FieldObservation.OBSERVATION_TYPE.values:
            assert value == value.upper(), f"{value!r} is not UPPER_CASE"


@pytest.mark.django_db
class TestLocationSourceChoices:
    """LOCATION_SOURCE TextChoices sanity checks (SNOW-330)."""

    def test_all_three_sources_present(self) -> None:
        """GPS, GPS_REFINED, and MANUAL are present in LOCATION_SOURCE."""
        values = FieldObservation.LOCATION_SOURCE.values
        assert "GPS" in values
        assert "GPS_REFINED" in values
        assert "MANUAL" in values

    def test_values_are_upper_case(self) -> None:
        """All LOCATION_SOURCE values are UPPER_CASE strings."""
        for value in FieldObservation.LOCATION_SOURCE.values:
            assert value == value.upper(), f"{value!r} is not UPPER_CASE"

    def test_location_source_round_trips(self) -> None:
        """location_source is stored and retrieved correctly."""
        obs = FieldObservation.objects.create(
            user=UserFactory.create(),
            latitude=46.10,
            longitude=7.10,
            location_source=FieldObservation.LOCATION_SOURCE.MANUAL,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        obs.refresh_from_db()
        assert obs.location_source == FieldObservation.LOCATION_SOURCE.MANUAL

    def test_gps_coords_round_trip(self) -> None:
        """gps_latitude and gps_longitude are stored and retrieved correctly."""
        obs = FieldObservation.objects.create(
            user=UserFactory.create(),
            latitude=46.20,
            longitude=7.20,
            gps_latitude=46.10,
            gps_longitude=7.10,
            location_source=FieldObservation.LOCATION_SOURCE.GPS_REFINED,
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        obs.refresh_from_db()
        assert obs.gps_latitude == pytest.approx(46.10)
        assert obs.gps_longitude == pytest.approx(7.10)

    def test_gps_coords_nullable(self) -> None:
        """gps_latitude and gps_longitude may be null (MANUAL path)."""
        obs = FieldObservation.objects.create(
            user=UserFactory.create(),
            latitude=46.10,
            longitude=7.10,
            gps_latitude=None,
            gps_longitude=None,
            location_source=FieldObservation.LOCATION_SOURCE.MANUAL,
            observation_type=FieldObservation.OBSERVATION_TYPE.FRACTURES,
        )
        assert obs.gps_latitude is None
        assert obs.gps_longitude is None


@pytest.mark.django_db
class TestUserLocatedExistsForRegionDay:
    """user_located_exists_for_region_day queryset method (SNOW-330)."""

    def _day(self) -> datetime.date:
        """Return a fixed test date."""
        return datetime.date(2026, 1, 20)

    def _at(self, d: datetime.date) -> datetime.datetime:
        """Return a tz-aware datetime for noon on date d."""
        return datetime.datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC)

    def test_returns_false_when_no_observations(self) -> None:
        """Returns False when no observations exist for the region/day."""
        region = MicroRegionFactory.create()
        result = FieldObservation.objects.user_located_exists_for_region_day(
            region, self._day()
        )
        assert result is False

    def test_returns_false_for_gps_only_observations(self) -> None:
        """GPS observations (not user-located) do not trigger True."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            location_source=FieldObservation.LOCATION_SOURCE.GPS,
        )
        result = FieldObservation.objects.user_located_exists_for_region_day(
            region, self._day()
        )
        assert result is False

    def test_returns_true_for_manual_observation(self) -> None:
        """A MANUAL observation makes the method return True."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            location_source=FieldObservation.LOCATION_SOURCE.MANUAL,
        )
        result = FieldObservation.objects.user_located_exists_for_region_day(
            region, self._day()
        )
        assert result is True

    def test_returns_true_for_gps_refined_observation(self) -> None:
        """A GPS_REFINED observation makes the method return True."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            location_source=FieldObservation.LOCATION_SOURCE.GPS_REFINED,
        )
        result = FieldObservation.objects.user_located_exists_for_region_day(
            region, self._day()
        )
        assert result is True

    def test_isolates_by_region(self) -> None:
        """MANUAL observation in another region does not return True."""
        region = MicroRegionFactory.create()
        other_region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=other_region,
            observed_at=self._at(self._day()),
            location_source=FieldObservation.LOCATION_SOURCE.MANUAL,
        )
        result = FieldObservation.objects.user_located_exists_for_region_day(
            region, self._day()
        )
        assert result is False

    def test_isolates_by_day(self) -> None:
        """MANUAL observation on a different day does not return True."""
        region = MicroRegionFactory.create()
        other_day = self._day() + datetime.timedelta(days=1)
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(other_day),
            location_source=FieldObservation.LOCATION_SOURCE.MANUAL,
        )
        result = FieldObservation.objects.user_located_exists_for_region_day(
            region, self._day()
        )
        assert result is False
