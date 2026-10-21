"""
tests/locations/services/test_orphans.py — the shared minted-Location sweep.

Covers ``apps.locations.services.orphans.delete_location_if_orphaned``: an
anonymous, unreferenced location goes (with its Weather rows); a named one,
or one still referenced by an observation, favourite or trip, stays — and
the probe that finds a referent does not poison an enclosing transaction.
"""

from __future__ import annotations

import pytest
from django.db import transaction

from apps.locations.models import Location
from apps.locations.services.orphans import delete_location_if_orphaned
from apps.weather.models import Weather
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
    TripFactory,
    WeatherFactory,
)


@pytest.mark.django_db
class TestDeleteLocationIfOrphaned:
    """Which locations the sweep removes and which it leaves."""

    def test_an_unreferenced_anonymous_location_goes_with_its_weather(self) -> None:
        """Nothing points at it, so the coordinate is erased."""
        location = LocationFactory.create(anonymous=True)
        weather = WeatherFactory.create(location=location)

        delete_location_if_orphaned(location)

        assert not Location.objects.filter(pk=location.pk).exists()
        assert not Weather.objects.filter(pk=weather.pk).exists()

    def test_a_named_location_is_left_alone(self) -> None:
        """Curated data is never swept, referenced or not."""
        location = LocationFactory.create(name="Mont Fort")

        delete_location_if_orphaned(location)

        assert Location.objects.filter(pk=location.pk).exists()

    def test_a_location_an_observation_references_stays(self) -> None:
        """A PROTECT referent answers "still in use" without an error."""
        observation = FieldObservationFactory.create()
        assert observation.location is not None

        delete_location_if_orphaned(observation.location)

        assert Location.objects.filter(pk=observation.location.pk).exists()

    def test_a_location_a_favourite_references_stays(self) -> None:
        """A favourite's pin keeps its location."""
        favourite = FavouriteFactory.create()
        assert favourite.location is not None

        delete_location_if_orphaned(favourite.location)

        assert Location.objects.filter(pk=favourite.location.pk).exists()

    def test_a_location_a_trip_meets_at_stays(self) -> None:
        """A trip's meeting point keeps its location."""
        trip = TripFactory.create()

        delete_location_if_orphaned(trip.meeting_point)

        assert Location.objects.filter(pk=trip.meeting_point.pk).exists()

    def test_the_outer_transaction_stays_usable(self) -> None:
        """The failed probe is confined to its savepoint."""
        observation = FieldObservationFactory.create()
        assert observation.location is not None

        with transaction.atomic():
            delete_location_if_orphaned(observation.location)
            # A broken transaction would raise TransactionManagementError here.
            assert Location.objects.filter(pk=observation.location.pk).exists()
