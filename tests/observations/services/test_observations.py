"""
tests/observations/services/test_observations.py — the observation delete service.

Covers ``delete_observation_instance``, ``delete_observation`` and
``delete_observations_for_user``: each removes the report and erases the
anonymous ``Location`` it minted, except where that location is curated or
still referenced, and the two halves commit together (SNOW-1170).
"""

from __future__ import annotations

import uuid as uuid_module
from typing import Any
from unittest import mock

import pytest

from apps.locations.models import Location
from apps.observations.models import FieldObservation
from apps.observations.services.observations import (
    delete_observation,
    delete_observation_instance,
    delete_observations_for_user,
)
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
    TripFactory,
    UserFactory,
)


def _report(**kwargs: Any) -> tuple[FieldObservation, Location]:
    """Create a report with its own anonymous Location and return both.

    Handing the location back typed as ``Location`` (not ``Location | None``)
    keeps the assertions free of None-narrowing.
    """
    location = LocationFactory.create(anonymous=True)
    return FieldObservationFactory.create(location=location, **kwargs), location


@pytest.mark.django_db
class TestDeleteObservation:
    """Deleting one of the user's own reports by uuid.

    ``Model.delete()`` clears the in-memory pk, so every test that asserts a
    deleted row is gone reads the pk first — filtering on ``pk=None`` would
    pass vacuously.
    """

    def test_the_report_and_its_location_go(self) -> None:
        """The anonymous location is erased with the report."""
        observation, location = _report()
        observation_pk = observation.pk
        location_pk = location.pk

        delete_observation(observation.user, observation.uuid)

        assert not FieldObservation.objects.filter(pk=observation_pk).exists()
        assert not Location.objects.filter(pk=location_pk).exists()

    def test_another_users_uuid_raises_and_deletes_nothing(self) -> None:
        """Ownership is enforced by the query."""
        theirs, location = _report()

        with pytest.raises(FieldObservation.DoesNotExist):
            delete_observation(UserFactory.create(), theirs.uuid)

        assert FieldObservation.objects.filter(pk=theirs.pk).exists()
        assert Location.objects.filter(pk=location.pk).exists()

    def test_an_unknown_uuid_raises(self) -> None:
        """A uuid that matches nothing is DoesNotExist, not a silent no-op."""
        with pytest.raises(FieldObservation.DoesNotExist):
            delete_observation(UserFactory.create(), uuid_module.uuid4())


@pytest.mark.django_db
class TestDeleteObservationInstance:
    """Which locations survive a report's deletion."""

    def test_a_named_location_survives(self) -> None:
        """Curated data is not the reporter's to delete."""
        mont_fort = LocationFactory.create(name="Mont Fort")
        observation = FieldObservationFactory.create(location=mont_fort)
        observation_pk = observation.pk

        delete_observation_instance(observation)

        assert not FieldObservation.objects.filter(pk=observation_pk).exists()
        assert Location.objects.filter(pk=mont_fort.pk).exists()

    def test_a_shared_location_goes_with_the_last_report(self) -> None:
        """Two reports at one location: it survives the first, not the second."""
        shared = LocationFactory.create(anonymous=True)
        first = FieldObservationFactory.create(location=shared)
        second = FieldObservationFactory.create(location=shared)

        delete_observation_instance(first)
        assert Location.objects.filter(pk=shared.pk).exists()

        delete_observation_instance(second)
        assert not Location.objects.filter(pk=shared.pk).exists()

    def test_a_location_a_favourite_uses_survives(self) -> None:
        """Another app's referent keeps the location."""
        shared = LocationFactory.create(anonymous=True)
        FavouriteFactory.create(location=shared)
        observation = FieldObservationFactory.create(location=shared)

        delete_observation_instance(observation)

        assert Location.objects.filter(pk=shared.pk).exists()

    def test_a_location_a_trip_meets_at_survives(self) -> None:
        """A trip's meeting point keeps the location."""
        trip = TripFactory.create()
        observation = FieldObservationFactory.create(location=trip.meeting_point)

        delete_observation_instance(observation)

        assert Location.objects.filter(pk=trip.meeting_point.pk).exists()

    def test_a_report_with_no_location_deletes_cleanly(self) -> None:
        """A pre-SNOW-709 row has nothing to sweep."""
        observation = FieldObservationFactory.create(location=None)
        observation_pk = observation.pk

        delete_observation_instance(observation)

        assert not FieldObservation.objects.filter(pk=observation_pk).exists()


@pytest.mark.django_db
class TestDeleteObservationsForUser:
    """The whole set, as account erasure deletes it."""

    def test_every_report_and_location_goes_and_the_count_is_returned(self) -> None:
        """Three reports, three locations erased, a bystander untouched."""
        user = UserFactory.create()
        location_pks = [_report(user=user)[1].pk for _ in range(3)]
        theirs, their_location = _report()

        assert delete_observations_for_user(user) == 3

        assert not FieldObservation.objects.filter(user=user).exists()
        assert not Location.objects.filter(pk__in=location_pks).exists()
        assert FieldObservation.objects.filter(pk=theirs.pk).exists()
        assert Location.objects.filter(pk=their_location.pk).exists()

    def test_a_user_with_no_reports_returns_zero(self) -> None:
        """Nothing to delete is not an error."""
        assert delete_observations_for_user(UserFactory.create()) == 0


@pytest.mark.django_db(transaction=True)
class TestDeletionIsAllOrNothing:
    """A failed sweep must not leave the coordinate stranded."""

    def test_a_failed_sweep_rolls_back_the_report_delete(self) -> None:
        """The row and its location are both still there."""
        observation, location = _report()
        observation_pk = observation.pk
        location_pk = location.pk

        with (
            mock.patch(
                "apps.observations.services.observations.delete_location_if_orphaned",
                side_effect=RuntimeError("database went away"),
            ),
            pytest.raises(RuntimeError),
        ):
            delete_observation_instance(observation)

        assert FieldObservation.objects.filter(pk=observation_pk).exists()
        assert Location.objects.filter(pk=location_pk).exists()
