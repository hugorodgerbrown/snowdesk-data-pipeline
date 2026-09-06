"""
tests/trips/test_participant_views.py — saving and removing a trip
(SNOW-822's two endpoints, relabelled by SNOW-848).

trips:join (POST /trips/partials/s/<token>/join/) — "Save this trip":
  400 for a plain request, 403 anonymous, 404 for a dead token;
  a save puts the caller on the roster and answers the SAVE CARD in its
  saved state; a second save is a 200 with one row.

trips:leave (POST /trips/partials/<uuid>/leave/) — "Remove from my trips":
  400/403/404 the same way; participation-scoped by the lookup;
  409 for the organiser, with a message pointing at delete;
  a successful remove answers ``HX-Redirect`` to the agenda.

**SNOW-848 REPLACED THE DISCLOSURE RULE WITH A STRONGER ONE.** Until it,
the roster published a COUNT to anybody holding the link and the NAMES to
participants. Now it publishes neither, to anyone: a trip is a shareable
item rather than a social event, and nobody learns who else holds one.
``TestNobodyLearnsWhoElseIsOnIt`` is what enforces that, and it is the
same tests inverted — the surfaces it checks are the ones that used to
name people.

Plus: trips:detail widened to participants, and still 404 for a
link-holder who has not saved it.
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
        assert 'id="trip-save"' in response.content.decode()

    def test_answers_the_saved_card_and_the_way_on_to_the_trip(
        self, client: Client
    ) -> None:
        """The pressed control repaints as the confirmation.

        Remove is NOT in the answer: this fragment is the share page's,
        and removing is uuid-addressed. What a saver gets instead is the
        link to the trip's own page, which is where removing lives.
        """
        trip = _shared_trip()
        client.force_login(UserFactory.create())

        html = client.post(
            reverse("trips:join", args=[trip.share_token]), **_HTMX
        ).content.decode()

        assert 'data-testid="trip-saved"' in html
        assert 'data-testid="trip-open-saved"' in html
        assert 'data-testid="trip-remove-form"' not in html

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
        """And sends them back to the agenda.

        ``HX-Redirect`` and not a fragment (SNOW-848): the only surface
        that offers Remove is the trip's own page, which is
        participation-scoped, so the moment the row is gone the caller is
        standing on a page they can no longer reload. Swapping a control
        into it would leave them there.
        """
        trip = TripFactory.create()
        leaver = UserFactory.create()
        join_trip(leaver, trip)
        client.force_login(leaver)

        response = client.post(reverse("trips:leave", args=[trip.uuid]), **_HTMX)

        assert response.status_code == 200
        assert not TripParticipant.objects.filter(trip=trip, user=leaver).exists()
        assert response["HX-Redirect"] == reverse("trips:list")

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
class TestNobodyLearnsWhoElseIsOnIt:
    """SNOW-848's disclosure rule, which is that there is nothing to disclose.

    Until SNOW-848 these surfaces published a COUNT to anybody holding the
    link and the NAMES to participants, and the tests here asserted both.
    They now assert the opposite, because the product changed: someone
    plans a trip and sends the link, whoever opens it saves it, and nobody
    sees who else did or how many.

    The rows are still written — ``TripParticipant`` is how
    ``Trip.objects.for_user`` knows whose agenda a trip belongs on — so
    "the roster is empty" would be a false test. What these check is that
    the PAGES say nothing about it.
    """

    def test_a_link_holder_learns_nothing_about_the_other_savers(
        self, client: Client
    ) -> None:
        """Not the names, and not the count either."""
        organiser = UserFactory.create(email="olga@example.com")
        trip = _shared_trip(created_by=organiser)
        join_trip(UserFactory.create(email="anna@example.com"), trip)

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert "anna" not in html
        assert "2 people are going" not in html
        assert 'data-testid="trip-roster"' not in html

    def test_a_saver_learns_nothing_either(self, client: Client) -> None:
        """Saving is not the act that earns the group's names any more.

        There is no act that earns them. This is the test that used to be
        ``test_a_participant_sees_the_names``.
        """
        organiser = UserFactory.create(email="olga@example.com")
        trip = _shared_trip(created_by=organiser)
        joiner = UserFactory.create(email="anna@example.com")
        join_trip(joiner, trip)
        join_trip(UserFactory.create(email="bruno@example.com"), trip)
        client.force_login(joiner)

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert "bruno" not in html
        assert 'data-testid="trip-roster-names"' not in html

    def test_the_organiser_is_still_named(self, client: Client) -> None:
        """The one piece of social information a trip surface keeps.

        Their name already answers "who sent me this", which the message
        carrying the link answered too; withholding it would make the page
        more anonymous than the group chat it arrived in.
        """
        organiser = UserFactory.create(email="olga@example.com")
        trip = _shared_trip(created_by=organiser)

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-attribution"' in html
        assert "olga" in html

    def test_the_organiser_reads_their_own_trip_as_theirs(self, client: Client) -> None:
        """Naming the reader back to themselves reads as a page not paying
        attention to who is looking at it.
        """
        organiser = UserFactory.create(email="olga@example.com")
        trip = TripFactory.create(created_by=organiser)
        client.force_login(organiser)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert "Your trip" in html
        assert "olga&#x27;s trip" not in html

    def test_an_anonymous_visitor_gets_the_sign_in_cta(self, client: Client) -> None:
        """Never a Save button that posts to a 403."""
        trip = _shared_trip()

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-save-form"' not in html
        assert reverse("accounts:sign_in") in html

    def test_a_signed_in_non_saver_gets_the_save_control(self, client: Client) -> None:
        """Addressed by token — a link-holder never sees the uuid."""
        trip = _shared_trip()
        client.force_login(UserFactory.create())

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-save-form"' in html
        assert str(trip.uuid) not in html

    def test_the_organiser_gets_no_remove_control(self, client: Client) -> None:
        """They are always on it; Delete is the exit, and it says so.

        No save card at all, rather than a disabled one: reaching this
        page means the trip is already theirs, and the only thing the card
        could offer them is an action the service refuses.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-testid="trip-remove-form"' not in html
        assert 'data-testid="trip-save"' not in html
        assert 'data-testid="trip-organiser-controls"' in html

    def test_a_saver_gets_the_remove_control_on_the_object_page(
        self, client: Client
    ) -> None:
        """One exit, on the surface that also carries the plan being removed."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-testid="trip-remove-form"' in html
        assert 'data-testid="trip-saved"' in html


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
