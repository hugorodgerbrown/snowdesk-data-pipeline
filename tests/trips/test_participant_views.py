"""
tests/trips/test_participant_views.py — join, leave and the roster surfaces
(SNOW-822).

trips:join (POST /trips/partials/s/<token>/join/):
  400 for a plain request, 403 anonymous, 404 for a dead token;
  a join puts the caller on the roster and answers the roster fragment;
  a second join is a 200 with one row.

trips:leave (POST /trips/partials/<uuid>/leave/):
  400/403/404 the same way; participation-scoped by the lookup;
  409 for the organiser, with a message pointing at delete.

The disclosure rule, on the rendered pages:
  a non-participant with the link sees the COUNT but not the NAMES;
  a participant sees the names;
  the ORGANISER IS NAMED IN BOTH.

Plus: trips:detail widened to participants, and still 404 for a
link-holder who has not joined.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from apps.trips.models import TripParticipant
from apps.trips.services.participants import join_trip
from apps.trips.services.shares import mint_trip_share
from tests.factories import TripFactory, UserFactory

_HTMX: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}

# Well before TripFactory's default date, so a minted link is live.
_NOW = "2026-01-10T09:00:00+00:00"


def _shared_trip(**kwargs: Any) -> Any:
    """Return a trip with a live share link, refreshed from the database."""
    trip = TripFactory.create(**kwargs)
    mint_trip_share(trip.created_by, trip.uuid)
    trip.refresh_from_db()
    return trip


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTripJoin:
    """POST /trips/partials/s/<token>/join/."""

    def test_puts_the_caller_on_the_roster(self, client: Client) -> None:
        """The verb the link was sent for."""
        trip = _shared_trip()
        joiner = UserFactory.create()
        client.force_login(joiner)

        response = client.post(reverse("trips:join", args=[trip.share_token]), **_HTMX)

        assert response.status_code == 200
        assert TripParticipant.objects.filter(trip=trip, user=joiner).exists()
        assert 'id="trip-roster"' in response.content.decode()

    def test_a_second_join_is_a_200_with_one_row(self, client: Client) -> None:
        """Idempotent, so a double tap is not an IntegrityError."""
        trip = _shared_trip()
        joiner = UserFactory.create()
        client.force_login(joiner)
        url = reverse("trips:join", args=[trip.share_token])

        assert client.post(url, **_HTMX).status_code == 200
        assert client.post(url, **_HTMX).status_code == 200
        assert TripParticipant.objects.filter(trip=trip, user=joiner).count() == 1

    def test_400_without_the_htmx_header(self, client: Client) -> None:
        """Invariant 4."""
        trip = _shared_trip()
        client.force_login(UserFactory.create())
        assert (
            client.post(reverse("trips:join", args=[trip.share_token])).status_code
            == 400
        )

    def test_403_for_an_anonymous_request(self, client: Client) -> None:
        """Joining needs an account to join WITH."""
        trip = _shared_trip()
        assert (
            client.post(
                reverse("trips:join", args=[trip.share_token]), **_HTMX
            ).status_code
            == 403
        )

    def test_404_for_a_dead_token(self, client: Client) -> None:
        """One answer for unknown, revoked and expired alike."""
        client.force_login(UserFactory.create())
        assert (
            client.post(
                reverse("trips:join", args=["neverexisted"]), **_HTMX
            ).status_code
            == 404
        )


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTripLeave:
    """POST /trips/partials/<uuid>/leave/."""

    def test_takes_the_caller_off_the_roster(self, client: Client) -> None:
        """And answers the roster, repainted from one read."""
        trip = TripFactory.create()
        leaver = UserFactory.create()
        join_trip(leaver, trip)
        client.force_login(leaver)

        response = client.post(reverse("trips:leave", args=[trip.uuid]), **_HTMX)

        assert response.status_code == 200
        assert not TripParticipant.objects.filter(trip=trip, user=leaver).exists()
        assert 'id="trip-roster"' in response.content.decode()

    def test_409_for_the_organiser(self, client: Client) -> None:
        """A permanent conflict with the object's state, not a permission.

        The message points at Delete, which is the real exit and which
        says out loud that it removes the trip for everyone.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.post(reverse("trips:leave", args=[trip.uuid]), **_HTMX)

        assert response.status_code == 409
        assert "Delete it" in response.content.decode()
        assert TripParticipant.objects.filter(trip=trip, user=trip.created_by).exists()

    def test_404_for_somebody_not_on_the_trip(self, client: Client) -> None:
        """Participation-scoped by the lookup — never an existence oracle."""
        trip = TripFactory.create()
        client.force_login(UserFactory.create())
        assert (
            client.post(reverse("trips:leave", args=[trip.uuid]), **_HTMX).status_code
            == 404
        )

    def test_400_without_the_htmx_header(self, client: Client) -> None:
        """Invariant 4."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        assert client.post(reverse("trips:leave", args=[trip.uuid])).status_code == 400

    def test_403_for_an_anonymous_request(self, client: Client) -> None:
        """There is no roster row to remove for a visitor with no account."""
        trip = TripFactory.create()
        assert (
            client.post(reverse("trips:leave", args=[trip.uuid]), **_HTMX).status_code
            == 403
        )


@freeze_time(_NOW)
@pytest.mark.django_db
class TestDisclosureOnThePage:
    """The count-vs-names rule, as rendered."""

    def test_a_link_holder_sees_the_count_but_not_the_names(
        self, client: Client
    ) -> None:
        """A trip link travels; the people on it did not agree to be listed."""
        organiser = UserFactory.create(email="olga@example.com")
        trip = _shared_trip(created_by=organiser)
        join_trip(UserFactory.create(email="anna@example.com"), trip)

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-roster-count"' in html
        assert "2 people are going" in html
        assert 'data-testid="trip-roster-names"' not in html
        assert "anna" not in html

    def test_the_organiser_is_named_to_everyone(self, client: Client) -> None:
        """Their name already answers "who sent me this"."""
        organiser = UserFactory.create(email="olga@example.com")
        trip = _shared_trip(created_by=organiser)

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-roster-organiser"' in html
        assert "olga" in html

    def test_a_participant_sees_the_names(self, client: Client) -> None:
        """Joining is the act that earns the group's names."""
        organiser = UserFactory.create(email="olga@example.com")
        trip = _shared_trip(created_by=organiser)
        joiner = UserFactory.create(email="anna@example.com")
        other = UserFactory.create(email="bruno@example.com")
        join_trip(joiner, trip)
        join_trip(other, trip)
        client.force_login(joiner)

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-roster-names"' in html
        assert "bruno" in html
        assert "olga" in html

    def test_an_anonymous_visitor_gets_the_sign_in_cta(self, client: Client) -> None:
        """Never a Join button that posts to a 403."""
        trip = _shared_trip()

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-join-form"' not in html
        assert reverse("accounts:sign_in") in html

    def test_a_signed_in_non_participant_gets_the_join_control(
        self, client: Client
    ) -> None:
        """Addressed by token — a link-holder never sees the uuid."""
        trip = _shared_trip()
        client.force_login(UserFactory.create())

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-join-form"' in html
        assert str(trip.uuid) not in html

    def test_the_organiser_gets_no_leave_control(self, client: Client) -> None:
        """They are always on it; Delete is the exit, and it says so."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-testid="trip-leave-form"' not in html
        assert 'data-testid="trip-roster-organiser-note"' in html

    def test_a_participant_gets_the_leave_control_on_the_object_page(
        self, client: Client
    ) -> None:
        """One exit, on the surface that also carries the plan being left."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-testid="trip-leave-form"' in html


@freeze_time(_NOW)
@pytest.mark.django_db
class TestObjectPageScope:
    """trips:detail is scoped by participation, not by authorship."""

    def test_a_participant_gets_the_object_page(self, client: Client) -> None:
        """Everybody on the trip gets it; the controls are what differ."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        response = client.get(reverse("trips:detail", args=[trip.uuid]))

        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-testid="trip-organiser-controls"' not in html

    def test_a_link_holder_who_has_not_joined_still_gets_404(
        self, client: Client
    ) -> None:
        """The uuid is the participants' identifier, not the link's."""
        trip = _shared_trip()
        client.force_login(UserFactory.create())

        assert client.get(reverse("trips:detail", args=[trip.uuid])).status_code == 404
