"""
tests/trips/test_services_trips.py — Tests for apps.trips.services.trips.

create_trip:
  copies the snapshot from the source route, nulls included;
  writes the organiser's own participant row;
  mints a per-trip anonymous Location for the meeting point, defaulting to
    the route's FIRST coordinate and honouring an explicit override;
  never reuses another trip's Location for the same coordinates;
  refuses a route belonging to somebody else;
  enforces settings.TRIPS_MAX_PER_USER, and counts organised trips only;
  the snapshot does NOT move when the source route is later renamed,
    edited or deleted.

update_trip:
  edits the plan and moves the trip's own Location in place;
  is organiser-scoped.

delete_trip / delete_trips_for_user:
  sweep the minted Location, leave a curated one alone, and are
    organiser-scoped.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import override_settings

from apps.locations.models import Location
from apps.routes.models import Route
from apps.trips.models import Trip, TripParticipant
from apps.trips.services.trips import (
    TripLimitReached,
    create_trip,
    delete_trip,
    delete_trips_for_user,
    update_trip,
)
from tests.factories import LocationFactory, RouteFactory, TripFactory, UserFactory

_DATE = datetime.date(2026, 3, 14)
_TIME = datetime.time(7, 30)


@pytest.mark.django_db
class TestCreateTripSnapshot:
    """The snapshot is copied at creation and never re-read."""

    def test_copies_the_geometry_and_figures(self) -> None:
        """Every snapshot field matches the source route."""
        route = RouteFactory.create(name="Rosablanche")
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        assert trip.points == route.points
        assert trip.bounds == route.bounds
        assert trip.distance_m == route.distance_m
        assert trip.ascent_m == route.ascent_m
        assert trip.descent_m == route.descent_m
        assert trip.point_count == route.point_count
        assert trip.route_name == "Rosablanche"

    def test_a_null_ascent_is_copied_as_null_never_zero(self) -> None:
        """Unknown and flat are different facts, and only one is zero."""
        route = RouteFactory.create(
            ascent_m=None,
            descent_m=None,
            points=[[7.4, 46.1, None], [7.41, 46.11, None]],
            point_count=2,
        )
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        assert trip.ascent_m is None
        assert trip.descent_m is None

    def test_the_snapshot_survives_the_route_being_renamed(self) -> None:
        """A trip stays what its organiser shared."""
        route = RouteFactory.create(name="Rosablanche")
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        route.name = "Something else entirely"
        route.points = [[0.0, 0.0, 0.0], [0.1, 0.1, 0.0]]
        route.save(update_fields=["name", "points", "updated_at"])

        trip.refresh_from_db()
        assert trip.route_name == "Rosablanche"
        assert trip.points[0] == [7.4, 46.1, 1500.0]

    def test_the_snapshot_survives_the_route_being_deleted(self) -> None:
        """SET_NULL nulls the provenance FK and leaves the trip whole."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        route.delete()

        trip.refresh_from_db()
        assert trip.route_id is None
        assert trip.point_count == 3
        assert trip.distance_m == 2500.0


@pytest.mark.django_db
class TestCreateTripRoster:
    """The organiser is on the trip from the first moment it exists."""

    def test_writes_the_organisers_participant_row(self) -> None:
        """One relation answers "everyone on this trip"."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        rows = list(TripParticipant.objects.for_trip(trip))
        assert [row.user_id for row in rows] == [route.user.pk]

    def test_the_organiser_is_on_their_own_for_user_list(self) -> None:
        """The consequence the participant row exists for."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        assert list(Trip.objects.for_user(route.user)) == [trip]


@pytest.mark.django_db
class TestCreateTripMeetingPoint:
    """The meeting point mints its own anonymous Location."""

    def test_defaults_to_the_routes_first_coordinate(self) -> None:
        """points stores [lon, lat, ele]; the Location reads them named."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        assert trip.meeting_point.latitude == pytest.approx(46.1)
        assert trip.meeting_point.longitude == pytest.approx(7.4)

    def test_an_explicit_pair_wins(self) -> None:
        """Latitude first in the signature, per the repo convention."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user,
            route_uuid=route.uuid,
            date=_DATE,
            start_time=_TIME,
            latitude=46.5,
            longitude=7.9,
        )
        assert trip.meeting_point.latitude == pytest.approx(46.5)
        assert trip.meeting_point.longitude == pytest.approx(7.9)

    def test_the_minted_location_is_anonymous(self) -> None:
        """No name and no kind — naming a place is a curation act."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        assert trip.meeting_point.name == ""
        assert trip.meeting_point.kind == ""

    def test_two_trips_at_one_coordinate_get_two_locations(self) -> None:
        """Never anchor_location() — a meeting point is per-instance."""
        route = RouteFactory.create()
        second_route = RouteFactory.create(user=route.user)
        first = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        second = create_trip(
            route.user,
            route_uuid=second_route.uuid,
            date=_DATE,
            start_time=_TIME,
        )
        assert first.meeting_point_id != second.meeting_point_id


@pytest.mark.django_db
class TestCreateTripOwnership:
    """A trip can only be planned from the caller's own route."""

    def test_refuses_another_users_route(self) -> None:
        """The lookup is owner-scoped, so this is DoesNotExist not 403."""
        route = RouteFactory.create()
        stranger = UserFactory.create()
        with pytest.raises(Route.DoesNotExist):
            create_trip(stranger, route_uuid=route.uuid, date=_DATE, start_time=_TIME)
        assert Trip.objects.count() == 0


@pytest.mark.django_db
class TestCreateTripCap:
    """settings.TRIPS_MAX_PER_USER."""

    @override_settings(TRIPS_MAX_PER_USER=1)
    def test_refuses_past_the_cap(self) -> None:
        """A second trip for a one-trip user is refused."""
        route = RouteFactory.create()
        create_trip(route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME)
        with pytest.raises(TripLimitReached):
            create_trip(route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME)
        assert Trip.objects.count() == 1

    @override_settings(TRIPS_MAX_PER_USER=1)
    def test_leaves_no_orphaned_location_when_refused(self) -> None:
        """The mint is inside the transaction the cap re-check guards."""
        route = RouteFactory.create()
        create_trip(route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME)
        before = Location.objects.count()
        with pytest.raises(TripLimitReached):
            create_trip(route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME)
        assert Location.objects.count() == before

    @override_settings(TRIPS_MAX_PER_USER=1)
    def test_counts_organised_trips_only(self) -> None:
        """Joining a friend's trip does not spend my own budget."""
        route = RouteFactory.create()
        joined = TripFactory.create()
        joined.participants.create(user=route.user)
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        assert trip.created_by_id == route.user.pk


@pytest.mark.django_db
class TestUpdateTrip:
    """The plan is editable; the snapshot is not."""

    def test_edits_the_plan(self) -> None:
        """Day, time, label and note all move."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        update_trip(
            route.user,
            trip.uuid,
            date=datetime.date(2026, 3, 21),
            start_time=datetime.time(6, 0),
            name="Moved a week",
            description="Bring skins.",
        )
        trip.refresh_from_db()
        assert trip.date == datetime.date(2026, 3, 21)
        assert trip.start_time == datetime.time(6, 0)
        assert trip.name == "Moved a week"
        assert trip.description == "Bring skins."

    def test_moves_the_meeting_point_in_place(self) -> None:
        """The trip's own Location is edited, not replaced."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        original_location_id = trip.meeting_point_id
        before = Location.objects.count()

        update_trip(
            route.user,
            trip.uuid,
            date=_DATE,
            start_time=_TIME,
            latitude=46.9,
            longitude=8.1,
        )

        trip.refresh_from_db()
        assert trip.meeting_point_id == original_location_id
        assert trip.meeting_point.latitude == pytest.approx(46.9)
        assert Location.objects.count() == before

    def test_leaves_the_snapshot_alone(self) -> None:
        """There is no argument that could change it."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        update_trip(route.user, trip.uuid, date=_DATE, start_time=_TIME, name="Renamed")
        trip.refresh_from_db()
        assert trip.points == route.points
        assert trip.route_name == route.name

    def test_is_organiser_scoped(self) -> None:
        """Somebody else's trip is not editable."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        stranger = UserFactory.create()
        with pytest.raises(Trip.DoesNotExist):
            update_trip(stranger, trip.uuid, date=_DATE, start_time=datetime.time(9, 0))


@pytest.mark.django_db
class TestDeleteTrip:
    """Deleting sweeps the minted Location."""

    def test_sweeps_the_meeting_point(self) -> None:
        """The anonymous row goes with the last thing referencing it."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        location_id = trip.meeting_point_id

        delete_trip(route.user, trip.uuid)

        assert not Location.objects.filter(pk=location_id).exists()

    def test_leaves_a_curated_location_alone(self) -> None:
        """A trip meeting at Mont Fort must not delete Mont Fort."""
        named = LocationFactory.create(name="Mont Fort")
        trip = TripFactory.create(meeting_point=named)

        delete_trip(trip.created_by, trip.uuid)

        assert Location.objects.filter(pk=named.pk).exists()

    def test_is_organiser_scoped(self) -> None:
        """A participant cannot delete the trip out from under everyone."""
        trip = TripFactory.create()
        stranger = UserFactory.create()
        with pytest.raises(Trip.DoesNotExist):
            delete_trip(stranger, trip.uuid)
        assert Trip.objects.filter(pk=trip.pk).exists()

    def test_removes_every_participant_row(self) -> None:
        """CASCADE: deleting a trip removes it for everyone on it."""
        route = RouteFactory.create()
        trip = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        trip.participants.create(user=UserFactory.create())

        delete_trip(route.user, trip.uuid)

        assert TripParticipant.objects.count() == 0


@pytest.mark.django_db
class TestDeleteTripsForUser:
    """The account-erasure counterpart."""

    def test_sweeps_every_organised_trips_location(self) -> None:
        """The gap a bulk CASCADE leaves, closed."""
        route = RouteFactory.create()
        second_route = RouteFactory.create(user=route.user)
        first = create_trip(
            route.user, route_uuid=route.uuid, date=_DATE, start_time=_TIME
        )
        second = create_trip(
            route.user,
            route_uuid=second_route.uuid,
            date=_DATE,
            start_time=_TIME,
        )
        location_ids = [first.meeting_point_id, second.meeting_point_id]

        assert delete_trips_for_user(route.user) == 2

        assert not Location.objects.filter(pk__in=location_ids).exists()

    def test_leaves_trips_the_user_merely_joined(self) -> None:
        """Erasure removes what they organised, not what they attended."""
        joiner = UserFactory.create()
        trip = TripFactory.create()
        trip.participants.create(user=joiner)

        assert delete_trips_for_user(joiner) == 0
        assert Trip.objects.filter(pk=trip.pk).exists()
