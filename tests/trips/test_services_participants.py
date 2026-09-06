"""
tests/trips/test_services_participants.py — Tests for the roster (SNOW-822).

join_trip:
  writes one row, and a second join neither writes another nor raises;
  the first join's joined_at survives the second, since the roster's order
    is read from it.

leave_trip / leave_trip_by_uuid:
  removes only the caller's row;
  is silent when they were not on it;
  REFUSES THE ORGANISER — the choice this ticket had to make, asserted;
  is scoped by participation, so an unrelated uuid is DoesNotExist.

roster_names_visible_to:
  the disclosure rule — a link-holder sees the count, a participant sees
  the names.

Plus: deleting a trip removes every participant row (the CASCADE the delete
confirmation promises).
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.trips.models import Trip, TripParticipant
from apps.trips.services.participants import (
    OrganiserCannotLeave,
    display_label_for,
    is_participant,
    join_trip,
    leave_trip,
    leave_trip_by_uuid,
    roster_for,
    roster_names_visible_to,
)
from apps.trips.services.trips import delete_trip
from tests.factories import AccountFactory, TripFactory, UserFactory


@pytest.mark.django_db
class TestJoinTrip:
    """Joining is idempotent, at the service and not at the caller."""

    def test_writes_one_row(self) -> None:
        """One join, one participant beside the organiser's own row."""
        trip = TripFactory.create()
        joiner = UserFactory.create()

        join_trip(joiner, trip)

        assert TripParticipant.objects.filter(trip=trip).count() == 2
        assert is_participant(joiner, trip)

    def test_a_second_join_neither_writes_nor_raises(self) -> None:
        """A double-tapped Join must not surface an IntegrityError."""
        trip = TripFactory.create()
        joiner = UserFactory.create()

        first = join_trip(joiner, trip)
        second = join_trip(joiner, trip)

        assert first.pk == second.pk
        assert TripParticipant.objects.filter(trip=trip, user=joiner).count() == 1

    def test_the_first_joins_timestamp_survives(self) -> None:
        """The roster's order is read from joined_at, so it must not move."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        original = join_trip(joiner, trip)
        original.joined_at = datetime.datetime(2026, 2, 1, 8, 0, tzinfo=datetime.UTC)
        original.save(update_fields=["joined_at", "updated_at"])

        join_trip(joiner, trip)

        original.refresh_from_db()
        assert original.joined_at == datetime.datetime(
            2026, 2, 1, 8, 0, tzinfo=datetime.UTC
        )

    def test_puts_the_trip_on_the_joiners_own_list(self) -> None:
        """The consequence: for_user is scoped by participation."""
        trip = TripFactory.create()
        joiner = UserFactory.create()

        join_trip(joiner, trip)

        assert list(Trip.objects.for_user(joiner)) == [trip]


@pytest.mark.django_db
class TestLeaveTrip:
    """Leaving takes one row and only one."""

    def test_removes_only_the_callers_row(self) -> None:
        """The organiser and the other joiner stay."""
        trip = TripFactory.create()
        leaver = UserFactory.create()
        stayer = UserFactory.create()
        join_trip(leaver, trip)
        join_trip(stayer, trip)

        leave_trip(leaver, trip)

        remaining = {row.user_id for row in roster_for(trip)}
        assert remaining == {trip.created_by_id, stayer.pk}

    def test_is_silent_when_they_were_not_on_it(self) -> None:
        """A double-submitted Leave reaches here twice."""
        trip = TripFactory.create()
        outsider = UserFactory.create()

        leave_trip(outsider, trip)
        leave_trip(outsider, trip)

        assert not is_participant(outsider, trip)

    def test_the_organiser_cannot_leave(self) -> None:
        """The choice this ticket had to make, asserted rather than implied.

        A trip whose organiser is not on it is incoherent: nobody is
        answerable for the plan, and the roster no longer holds the person
        whose name answers "who sent me this". Delete is the exit.
        """
        trip = TripFactory.create()

        with pytest.raises(OrganiserCannotLeave):
            leave_trip(trip.created_by, trip)

        assert is_participant(trip.created_by, trip)

    def test_leave_by_uuid_is_scoped_by_participation(self) -> None:
        """An unrelated uuid is DoesNotExist, which the view answers 404."""
        trip = TripFactory.create()
        outsider = UserFactory.create()

        with pytest.raises(Trip.DoesNotExist):
            leave_trip_by_uuid(outsider, trip.uuid)


@pytest.mark.django_db
class TestDisclosureRule:
    """A link-holder sees how many; a participant sees who."""

    def test_an_anonymous_visitor_sees_no_names(self) -> None:
        """And costs no query to be told so."""
        trip = TripFactory.create()
        assert roster_names_visible_to(AnonymousUser(), trip) is False

    def test_a_signed_in_non_participant_sees_no_names(self) -> None:
        """Holding the link is not being in the group."""
        trip = TripFactory.create()
        assert roster_names_visible_to(UserFactory.create(), trip) is False

    def test_a_participant_sees_the_names(self) -> None:
        """Joining is the act that earns them."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        assert roster_names_visible_to(joiner, trip) is True

    def test_the_organiser_sees_the_names(self) -> None:
        """They are on the roster like everybody else."""
        trip = TripFactory.create()
        assert roster_names_visible_to(trip.created_by, trip) is True


@pytest.mark.django_db
class TestRosterRead:
    """roster_for and how a person is named on it."""

    def test_reads_in_join_order_with_the_organiser_first(self) -> None:
        """Their row is written at creation, so it is always oldest."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)

        assert [row.user_id for row in roster_for(trip)] == [
            trip.created_by_id,
            joiner.pk,
        ]

    def test_a_person_is_named_by_the_local_part_of_their_email(self) -> None:
        """Never the whole address — a roster is read by the whole group."""
        trip = TripFactory.create()
        joiner = UserFactory.create(email="anna@example.com")
        participant = join_trip(joiner, trip)

        assert participant.display_label == "anna"
        assert "@" not in participant.display_label


@pytest.mark.django_db
class TestDeletingATripEmptiesTheRoster:
    """The CASCADE the delete confirmation promises."""

    def test_removes_every_participant_row(self) -> None:
        """Deleting removes the trip for everyone on it."""
        trip = TripFactory.create()
        join_trip(UserFactory.create(), trip)
        join_trip(UserFactory.create(), trip)

        delete_trip(trip.created_by, trip.uuid)

        assert TripParticipant.objects.count() == 0


@pytest.mark.django_db
class TestDisplayLabelFor:
    """``display_label_for`` — how one account is NAMED on a trip surface.

    Extracted from ``TripParticipant.display_label`` by SNOW-848, which
    needs the same answer for ``trip.created_by`` — reached without a
    roster row — for the "Marta's trip" eyebrow.
    """

    def test_a_display_name_wins(self) -> None:
        """What the person chose to be called."""
        user = UserFactory.create(email="marta@example.com")
        AccountFactory.create(user=user, display_name="Marta")

        assert display_label_for(user) == "Marta"

    def test_the_local_part_stands_in_for_a_missing_display_name(self) -> None:
        """Never the whole address.

        A trip link travels — forwarded out of one group chat and into
        another — and the full address is what an account signs in with.
        The local part identifies somebody to people who already know
        them, which is exactly the audience, and is not a deliverable
        address.
        """
        user = UserFactory.create(email="marta@example.com")

        label = display_label_for(user)

        assert label == "marta"
        assert "@" not in label

    def test_an_account_with_neither_still_gets_a_name(self) -> None:
        """A plain staff superuser has no Account profile."""
        assert display_label_for(UserFactory.create(email="")) == "Somebody"

    def test_the_participant_property_gives_the_same_answer(self) -> None:
        """The model delegates rather than keeping a second copy of the
        rule — two copies of "never show the whole address" is one too
        many.
        """
        trip = TripFactory.create()
        joiner = UserFactory.create(email="anna@example.com")
        participant = join_trip(joiner, trip)

        assert participant.display_label == display_label_for(joiner)
