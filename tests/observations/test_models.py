"""
tests/observations/test_models.py — Tests for observations.models.

Covers:
  FieldObservation defaults, to_string, Meta.ordering.
  FieldObservationQuerySet.counts_for_region_day — multi-type rows, empty case,
    cross-region isolation, cross-day isolation.
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
    SubscriberFactory,
)


@pytest.mark.django_db
class TestFieldObservationDefaults:
    """Verify field defaults and Meta settings."""

    def test_observation_types_defaults_to_empty_list(self) -> None:
        """observation_types defaults to an empty list when not provided."""
        obs = FieldObservation.objects.create(
            subscriber=SubscriberFactory.create(),
            latitude=46.10,
            longitude=7.10,
        )
        assert obs.observation_types == []

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
            subscriber=SubscriberFactory.create(),
            latitude=46.10,
            longitude=7.10,
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
        subscriber = SubscriberFactory.create()
        early = FieldObservationFactory.create(
            subscriber=subscriber,
            region=region,
            observed_at=timezone.now() - datetime.timedelta(hours=2),
        )
        late = FieldObservationFactory.create(
            subscriber=subscriber,
            region=region,
            observed_at=timezone.now(),
        )
        qs = FieldObservation.objects.all()
        assert list(qs[:2]) == [late, early]


@pytest.mark.django_db
class TestFieldObservationToString:
    """to_string and __str__ coverage."""

    def test_to_string_with_region(self) -> None:
        """to_string includes region name when region is set."""
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        obs = FieldObservationFactory.create(
            region=region,
            observation_types=[FieldObservation.OBSERVATION_TYPE.WHUMPFING],
        )
        result = obs.to_string()
        assert "Martigny-Verbier" in result
        assert "WHUMPFING" in result

    def test_to_string_without_region(self) -> None:
        """to_string says 'unknown region' when region is null."""
        obs = FieldObservationFactory.create(
            region=None,
            observation_types=[FieldObservation.OBSERVATION_TYPE.PINWHEELS],
        )
        result = obs.to_string()
        assert "unknown region" in result

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string."""
        obs = FieldObservationFactory.create()
        assert str(obs) == obs.to_string()

    def test_to_string_with_no_types(self) -> None:
        """to_string says 'no types' when observation_types is empty."""
        obs = FieldObservationFactory.create(observation_types=[])
        assert "no types" in obs.to_string()


@pytest.mark.django_db
class TestCountsForRegionDay:
    """counts_for_region_day — tally per observation type."""

    def _day(self) -> datetime.date:
        """Return a fixed test date."""
        return datetime.date(2026, 1, 15)

    def _at(self, d: datetime.date) -> datetime.datetime:
        """Return a tz-aware datetime for midnight on date d."""
        return datetime.datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC)

    def test_returns_empty_dict_when_no_observations(self) -> None:
        """Returns empty dict when no observations exist for the region/day."""
        region = MicroRegionFactory.create()
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {}

    def test_counts_single_observation_type(self) -> None:
        """Counts a single observation type correctly."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_types=[FieldObservation.OBSERVATION_TYPE.WHUMPFING],
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
                observation_types=[FieldObservation.OBSERVATION_TYPE.PINWHEELS],
            )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {"PINWHEELS": 3}

    def test_counts_multiple_types_in_one_row(self) -> None:
        """A single row with multiple types contributes to each type's count."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_types=[
                FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                FieldObservation.OBSERVATION_TYPE.FRACTURES,
            ],
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
            observation_types=[FieldObservation.OBSERVATION_TYPE.WHUMPFING],
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
            observation_types=[FieldObservation.OBSERVATION_TYPE.WHUMPFING],
        )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts == {}

    def test_multiple_reports_same_day_multiple_types(self) -> None:
        """Two reporters each with a mix of types produce correct totals."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_types=[
                FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                FieldObservation.OBSERVATION_TYPE.PINWHEELS,
            ],
        )
        FieldObservationFactory.create(
            region=region,
            observed_at=self._at(self._day()),
            observation_types=[
                FieldObservation.OBSERVATION_TYPE.WHUMPFING,
                FieldObservation.OBSERVATION_TYPE.SHOOTING_CRACKS,
            ],
        )
        counts = FieldObservation.objects.counts_for_region_day(region, self._day())
        assert counts["WHUMPFING"] == 2
        assert counts["PINWHEELS"] == 1
        assert counts["SHOOTING_CRACKS"] == 1
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
