"""
tests/trips/test_models.py — Tests for apps.trips.models.

apps.trips.models.Trip:
  to_string() names the trip, falling back to the source route's label;
  display_name prefers the trip's own name;
  distance_km converts;
  Meta.ordering is by the day the trip happens, not when it was planned;
  for_user() returns trips the user is ON, not trips they created —
    a joined trip is present and an unrelated one is absent;
  upcoming()/past() split on the trip's own date, and a trip dated today
    counts as upcoming.

apps.trips.models.TripParticipant:
  to_string();
  the (trip, user) uniqueness constraint;
  the roster reads in join order.
"""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError

from apps.trips.models import Trip, TripParticipant
from tests.factories import (
    TripFactory,
    TripParticipantFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestTripToString:
    """Trip.to_string() and the two display helpers."""

    def test_names_the_trip_and_its_day(self) -> None:
        """The label, the date and the meeting time, in that order."""
        trip = TripFactory.create(
            name="Rosablanche",
            date=datetime.date(2026, 3, 14),
            start_time=datetime.time(7, 30),
        )
        assert trip.to_string() == "Rosablanche — 2026-03-14 07:30"
        assert str(trip) == trip.to_string()

    def test_falls_back_to_the_source_routes_name(self) -> None:
        """A trip created without a label reads as the route it uses."""
        trip = TripFactory.create(name="", route_name="Mont Fort traverse")
        assert trip.display_name == "Mont Fort traverse"
        assert "Mont Fort traverse" in trip.to_string()

    def test_a_trip_with_no_label_at_all_is_still_readable(self) -> None:
        """Neither name nor route_name still yields a usable string."""
        trip = TripFactory.create(name="", route_name="")
        assert trip.to_string().startswith("Untitled trip")

    def test_distance_km_converts_from_metres(self) -> None:
        """The stored unit is metres; a route is read in kilometres."""
        trip = TripFactory.create(distance_m=2500.0)
        assert trip.distance_km == 2.5


@pytest.mark.django_db
class TestTripOrdering:
    """Meta.ordering — an agenda, not an audit log."""

    def test_orders_by_the_day_the_trip_happens(self) -> None:
        """Created last but dated first still comes first."""
        later = TripFactory.create(date=datetime.date(2026, 3, 20))
        earlier = TripFactory.create(date=datetime.date(2026, 3, 14))
        assert list(Trip.objects.all()) == [earlier, later]

    def test_orders_by_start_time_within_a_day(self) -> None:
        """Two trips on one day read in the order they start."""
        afternoon = TripFactory.create(
            date=datetime.date(2026, 3, 14), start_time=datetime.time(13, 0)
        )
        dawn = TripFactory.create(
            date=datetime.date(2026, 3, 14), start_time=datetime.time(5, 30)
        )
        assert list(Trip.objects.all()) == [dawn, afternoon]


@pytest.mark.django_db
class TestTripForUser:
    """for_user() is scoped by PARTICIPATION, not by created_by."""

    def test_returns_a_trip_the_user_joined_but_did_not_create(self) -> None:
        """The whole reason the roster is one relation."""
        joiner = UserFactory.create()
        trip = TripFactory.create()
        TripParticipantFactory.create(trip=trip, user=joiner)
        assert list(Trip.objects.for_user(joiner)) == [trip]

    def test_omits_a_trip_the_user_created_but_has_no_row_on(self) -> None:
        """created_by alone does not put a trip on the list.

        A factory-built trip has no participant rows — ``create_trip``
        writes the organiser's, and this asserts the scoping is genuinely
        the relation rather than a union that happens to include it.
        """
        organiser = UserFactory.create()
        TripFactory.create(created_by=organiser)
        assert list(Trip.objects.for_user(organiser)) == []

    def test_omits_an_unrelated_trip(self) -> None:
        """Somebody else's trip is not on my list."""
        viewer = UserFactory.create()
        other = TripFactory.create()
        TripParticipantFactory.create(trip=other)
        assert list(Trip.objects.for_user(viewer)) == []


@pytest.mark.django_db
class TestTripUpcomingAndPast:
    """The upcoming / past split, against an explicit date."""

    def test_a_trip_dated_today_is_upcoming(self) -> None:
        """The day it exists for has not finished."""
        today = datetime.date(2026, 3, 14)
        trip = TripFactory.create(date=today)
        assert list(Trip.objects.upcoming(today)) == [trip]
        assert list(Trip.objects.past(today)) == []

    def test_yesterday_is_past(self) -> None:
        """The boundary belongs to upcoming and nothing either side."""
        today = datetime.date(2026, 3, 14)
        trip = TripFactory.create(date=datetime.date(2026, 3, 13))
        assert list(Trip.objects.past(today)) == [trip]
        assert list(Trip.objects.upcoming(today)) == []

    def test_upcoming_is_soonest_first(self) -> None:
        """An agenda reads forwards."""
        today = datetime.date(2026, 3, 1)
        far = TripFactory.create(date=datetime.date(2026, 4, 1))
        near = TripFactory.create(date=datetime.date(2026, 3, 5))
        assert list(Trip.objects.upcoming(today)) == [near, far]

    def test_past_is_most_recent_first(self) -> None:
        """A history reads backwards."""
        today = datetime.date(2026, 3, 20)
        old = TripFactory.create(date=datetime.date(2026, 1, 5))
        recent = TripFactory.create(date=datetime.date(2026, 3, 19))
        assert list(Trip.objects.past(today)) == [recent, old]


@pytest.mark.django_db
class TestTripParticipant:
    """The roster row."""

    def test_to_string_names_the_person_and_the_trip(self) -> None:
        """Format: ``{user} on {trip}``."""
        participant = TripParticipantFactory.create()
        rendered = participant.to_string()
        assert str(participant.user) in rendered
        assert participant.trip.to_string() in rendered
        assert str(participant) == rendered

    def test_one_user_cannot_join_one_trip_twice(self) -> None:
        """The (trip, user) uniqueness constraint holds at the database."""
        participant = TripParticipantFactory.create()
        with pytest.raises(IntegrityError):
            TripParticipant.objects.create(trip=participant.trip, user=participant.user)

    def test_the_roster_reads_in_join_order(self) -> None:
        """Meta.ordering is joined_at, so the list reads as it filled up."""
        trip = TripFactory.create()
        second = TripParticipantFactory.create(
            trip=trip,
            joined_at=datetime.datetime(2026, 3, 2, 9, 0, tzinfo=datetime.UTC),
        )
        first = TripParticipantFactory.create(
            trip=trip,
            joined_at=datetime.datetime(2026, 3, 1, 9, 0, tzinfo=datetime.UTC),
        )
        assert list(TripParticipant.objects.for_trip(trip)) == [first, second]
