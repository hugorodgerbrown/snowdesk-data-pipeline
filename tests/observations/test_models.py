"""
tests/observations/test_models.py — Tests for apps.observations.models.

Covers:
  FieldObservation field values, to_string, Meta.ordering.
  FieldObservationQuerySet.counts_for_region_day — single-type rows,
    multi-row aggregation, empty case, cross-region isolation,
    cross-day isolation.
  FieldObservationQuerySet.near_point_for_day /
    counts_near_point_for_day (SNOW-508) — inside/outside radius, inclusive
    boundary, empty result, day scoping.
  FieldObservationQuerySet.user_located_exists_for_region_day — returns True
    only for MANUAL/GPS_REFINED rows, False for GPS and empty.
  FieldObservationQuerySet.recent — rows at/inside vs outside a cutoff.
  LOCATION_SOURCE choices — values are UPPER_CASE.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.observations.models import FieldObservation, _haversine_km
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
class TestNearPointForDay:
    """near_point_for_day / counts_near_point_for_day (SNOW-508)."""

    CENTRE_LAT = 46.10
    CENTRE_LON = 7.10
    RADIUS_KM = 10.0

    def _day(self) -> datetime.date:
        """Return a fixed test date."""
        return datetime.date(2026, 2, 1)

    def _at(self, d: datetime.date) -> datetime.datetime:
        """Return a tz-aware datetime for noon on date d."""
        return datetime.datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC)

    def test_returns_empty_queryset_when_no_observations(self) -> None:
        """near_point_for_day returns an empty queryset when nothing exists."""
        result = FieldObservation.objects.get_queryset().near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert list(result) == []

    def test_counts_returns_empty_dict_when_no_observations(self) -> None:
        """counts_near_point_for_day returns {} when nothing exists."""
        counts = FieldObservation.objects.counts_near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert counts == {}

    def test_includes_observation_inside_radius(self) -> None:
        """An observation well inside the radius is returned and counted."""
        inside = FieldObservationFactory.create(
            latitude=self.CENTRE_LAT,
            longitude=self.CENTRE_LON + 0.01,  # ~ 0.8 km east, well inside 10 km
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        result = FieldObservation.objects.get_queryset().near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert inside in result
        counts = FieldObservation.objects.counts_near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert counts == {"WHUMPFING": 1}

    def test_excludes_observation_outside_radius(self) -> None:
        """An observation well outside the radius is excluded."""
        outside = FieldObservationFactory.create(
            latitude=self.CENTRE_LAT + 1.0,  # ~ 111 km north, well outside 10 km
            longitude=self.CENTRE_LON,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        result = FieldObservation.objects.get_queryset().near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert outside not in result
        counts = FieldObservation.objects.counts_near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert counts == {}

    def test_boundary_point_is_included(self) -> None:
        """A point at exactly the radius edge is included (inclusive boundary).

        The radius passed in is derived from ``_haversine_km`` applied to
        the same two points the queryset method will compare against, so
        the comparison is an exact float equality (``distance <= radius``
        with ``distance == radius``) rather than a fragile near-miss.
        """
        edge_lat, edge_lon = 46.19, self.CENTRE_LON
        exact_radius_km = _haversine_km(
            self.CENTRE_LAT, self.CENTRE_LON, edge_lat, edge_lon
        )
        edge = FieldObservationFactory.create(
            latitude=edge_lat,
            longitude=edge_lon,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.FRACTURES,
        )
        result = FieldObservation.objects.get_queryset().near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, exact_radius_km, self._day()
        )
        assert edge in result

    def test_isolates_by_day(self) -> None:
        """An in-radius observation on a different day is excluded."""
        other_day = self._day() + datetime.timedelta(days=1)
        FieldObservationFactory.create(
            latitude=self.CENTRE_LAT,
            longitude=self.CENTRE_LON,
            observed_at=self._at(other_day),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        result = FieldObservation.objects.get_queryset().near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert list(result) == []
        counts = FieldObservation.objects.counts_near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert counts == {}

    def test_counts_multiple_types_near_point(self) -> None:
        """Multiple in-radius reports of different types tally independently."""
        FieldObservationFactory.create(
            latitude=self.CENTRE_LAT,
            longitude=self.CENTRE_LON + 0.01,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        FieldObservationFactory.create(
            latitude=self.CENTRE_LAT + 0.01,
            longitude=self.CENTRE_LON,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        FieldObservationFactory.create(
            latitude=self.CENTRE_LAT,
            longitude=self.CENTRE_LON - 0.01,
            observed_at=self._at(self._day()),
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        counts = FieldObservation.objects.counts_near_point_for_day(
            self.CENTRE_LAT, self.CENTRE_LON, self.RADIUS_KM, self._day()
        )
        assert counts == {"WHUMPFING": 2, "PINWHEELS": 1}


@pytest.mark.django_db
class TestRecent:
    """recent(since) — filter on observed_at >= since (SNOW-419)."""

    def test_includes_row_at_cutoff(self) -> None:
        """A row observed exactly at the cutoff is included (>=, not >)."""
        cutoff = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        obs = FieldObservationFactory.create(observed_at=cutoff)
        result = FieldObservation.objects.recent(cutoff)
        assert obs in result

    def test_includes_row_just_inside_window(self) -> None:
        """A row one second after the cutoff is included."""
        cutoff = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        obs = FieldObservationFactory.create(
            observed_at=cutoff + datetime.timedelta(seconds=1),
        )
        result = FieldObservation.objects.recent(cutoff)
        assert obs in result

    def test_excludes_row_just_outside_window(self) -> None:
        """A row one second before the cutoff is excluded."""
        cutoff = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        obs = FieldObservationFactory.create(
            observed_at=cutoff - datetime.timedelta(seconds=1),
        )
        result = FieldObservation.objects.recent(cutoff)
        assert obs not in result

    def test_48h_window_boundary(self) -> None:
        """The 48h window used by the map overlay includes/excludes correctly."""
        now = datetime.datetime(2026, 1, 17, 12, 0, tzinfo=UTC)
        since = now - datetime.timedelta(hours=48)
        just_inside = FieldObservationFactory.create(
            observed_at=since + datetime.timedelta(minutes=1),
        )
        just_outside = FieldObservationFactory.create(
            observed_at=since - datetime.timedelta(minutes=1),
        )
        result = FieldObservation.objects.recent(since)
        assert just_inside in result
        assert just_outside not in result


@pytest.mark.django_db
class TestForUser:
    """for_user(user) — the owner scope behind the map panel's list (SNOW-658)."""

    def test_returns_only_that_users_rows(self) -> None:
        """One user's reports never include another's."""
        mine = FieldObservationFactory.create()
        theirs = FieldObservationFactory.create()

        result = FieldObservation.objects.for_user(mine.user)

        assert mine in result
        assert theirs not in result

    def test_is_empty_for_a_user_with_no_reports(self) -> None:
        """A user who has reported nothing gets an empty queryset, not an error."""
        FieldObservationFactory.create()
        user = UserFactory.create()

        assert list(FieldObservation.objects.for_user(user)) == []

    def test_is_chainable(self) -> None:
        """It returns a queryset, so it composes with the other filters."""
        user = UserFactory.create()
        cutoff = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        recent = FieldObservationFactory.create(user=user, observed_at=cutoff)
        FieldObservationFactory.create(
            user=user, observed_at=cutoff - datetime.timedelta(days=1)
        )

        result = FieldObservation.objects.for_user(user).recent(cutoff)

        assert list(result) == [recent]


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


@pytest.mark.django_db
class TestFieldObservationCoordinateConstraints:
    """SNOW-464: DB check constraints reject out-of-range / negative values.

    Belt-and-braces for a write path that bypasses the view-layer validators
    (admin, shell, a future API).
    """

    def test_latitude_out_of_range_raises_integrity_error(self) -> None:
        """A latitude beyond WGS-84 bounds is rejected at the DB layer."""
        with pytest.raises(IntegrityError), transaction.atomic():
            FieldObservationFactory.create(latitude=999.0)

    def test_longitude_out_of_range_raises_integrity_error(self) -> None:
        """A longitude beyond WGS-84 bounds is rejected at the DB layer."""
        with pytest.raises(IntegrityError), transaction.atomic():
            FieldObservationFactory.create(longitude=999.0)

    def test_gps_latitude_out_of_range_raises_integrity_error(self) -> None:
        """An out-of-range raw GPS latitude is rejected at the DB layer."""
        with pytest.raises(IntegrityError), transaction.atomic():
            FieldObservationFactory.create(gps_latitude=999.0)

    def test_negative_accuracy_raises_integrity_error(self) -> None:
        """A negative accuracy radius is rejected at the DB layer."""
        with pytest.raises(IntegrityError), transaction.atomic():
            FieldObservationFactory.create(accuracy_radius_km=-1.0)
