"""
tests/trips/test_save_route_views.py — the Save route endpoints (SNOW-824).

trips:save_route_shared (POST /trips/partials/s/<token>/save-route/) and
trips:save_route (POST /trips/partials/<uuid>/save-route/):
  400 for a plain request, 403 anonymous, 404 for a dead token / a trip the
    caller is not on;
  a save writes a route owned by the caller and answers the saved-state
    control;
  a second save writes no second route;
  409 at the routes cap.

On the rendered pages:
  the control is offered to a link-holder who has NOT joined — saving does
    not join and joining does not save;
  it renders in its saved state once the geometry is on the account;
  an anonymous visitor gets a sign-in link, not a button that posts to 403;
  the share page's control is TOKEN-addressed, so no uuid reaches a
    link-holder's DOM.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from freezegun import freeze_time

from apps.routes.models import Route
from apps.trips.services.participants import join_trip
from apps.trips.services.shares import mint_trip_share
from tests.factories import RouteFactory, TripFactory, UserFactory

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
class TestSaveRouteFromTheShareLink:
    """POST /trips/partials/s/<token>/save-route/."""

    def test_writes_a_route_owned_by_the_caller(self, client: Client) -> None:
        """The recipient keeps the track, whether or not they join."""
        trip = _shared_trip()
        viewer = UserFactory.create()
        client.force_login(viewer)

        response = client.post(
            reverse("trips:save_route_shared", args=[trip.share_token]), **_HTMX
        )

        assert response.status_code == 200
        route = Route.objects.for_user(viewer).get()
        assert route.points == trip.points
        assert 'data-testid="trip-route-saved"' in response.content.decode()

    def test_saving_does_not_join(self, client: Client) -> None:
        """Two different acts: one puts a track on your map, one puts you
        in a group.
        """
        trip = _shared_trip()
        viewer = UserFactory.create()
        client.force_login(viewer)

        client.post(
            reverse("trips:save_route_shared", args=[trip.share_token]), **_HTMX
        )

        assert not trip.participants.filter(user=viewer).exists()

    def test_a_second_save_writes_no_second_route(self, client: Client) -> None:
        """A double tap must not spend a slot of the caller's cap."""
        trip = _shared_trip()
        viewer = UserFactory.create()
        client.force_login(viewer)
        url = reverse("trips:save_route_shared", args=[trip.share_token])

        assert client.post(url, **_HTMX).status_code == 200
        assert client.post(url, **_HTMX).status_code == 200
        assert Route.objects.for_user(viewer).count() == 1

    def test_400_without_the_htmx_header(self, client: Client) -> None:
        """Invariant 4."""
        trip = _shared_trip()
        client.force_login(UserFactory.create())
        url = reverse("trips:save_route_shared", args=[trip.share_token])
        assert client.post(url).status_code == 400

    def test_403_for_an_anonymous_request(self, client: Client) -> None:
        """A saved route needs an account to sit on."""
        trip = _shared_trip()
        url = reverse("trips:save_route_shared", args=[trip.share_token])
        assert client.post(url, **_HTMX).status_code == 403

    def test_404_for_a_dead_token(self, client: Client) -> None:
        """One answer for unknown, revoked and expired alike."""
        client.force_login(UserFactory.create())
        url = reverse("trips:save_route_shared", args=["neverexisted"])
        assert client.post(url, **_HTMX).status_code == 404

    @override_settings(ROUTES_MAX_PER_USER=1)
    def test_409_at_the_routes_cap(self, client: Client) -> None:
        """A cap is a permanent failure, so 409 and not the transient 429."""
        trip = _shared_trip()
        viewer = UserFactory.create()
        # Different geometry from the trip's snapshot, or ``already_saved``
        # short-circuits to the saved state before the cap is ever reached —
        # RouteFactory and TripFactory share a default track.
        RouteFactory.create(user=viewer, points=[[1.0, 1.0, 0.0], [1.1, 1.1, 0.0]])
        client.force_login(viewer)

        response = client.post(
            reverse("trips:save_route_shared", args=[trip.share_token]), **_HTMX
        )

        assert response.status_code == 409
        assert Route.objects.for_user(viewer).count() == 1


@freeze_time(_NOW)
@pytest.mark.django_db
class TestSaveRouteFromTheObjectPage:
    """POST /trips/partials/<uuid>/save-route/."""

    def test_a_participant_can_save(self, client: Client) -> None:
        """The same act, on the surface a participant reads the trip on."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        response = client.post(reverse("trips:save_route", args=[trip.uuid]), **_HTMX)

        assert response.status_code == 200
        assert Route.objects.for_user(joiner).count() == 1

    def test_the_organiser_can_save_their_own_trips_route(self, client: Client) -> None:
        """They already own the source route, but this is a copy of the
        SNAPSHOT — which may differ, and which is what the page shows.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.post(reverse("trips:save_route", args=[trip.uuid]), **_HTMX)

        assert response.status_code == 200

    def test_404_for_somebody_not_on_the_trip(self, client: Client) -> None:
        """Participation-scoped by the lookup — never an existence oracle."""
        trip = TripFactory.create()
        client.force_login(UserFactory.create())
        url = reverse("trips:save_route", args=[trip.uuid])
        assert client.post(url, **_HTMX).status_code == 404

    def test_400_without_the_htmx_header(self, client: Client) -> None:
        """Invariant 4."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        assert (
            client.post(reverse("trips:save_route", args=[trip.uuid])).status_code
            == 400
        )

    def test_403_for_an_anonymous_request(self, client: Client) -> None:
        """No account to save onto."""
        trip = TripFactory.create()
        url = reverse("trips:save_route", args=[trip.uuid])
        assert client.post(url, **_HTMX).status_code == 403


@freeze_time(_NOW)
@pytest.mark.django_db
class TestSaveControlOnThePage:
    """The three states of the control, as rendered."""

    def test_offered_to_a_link_holder_who_has_not_joined(self, client: Client) -> None:
        """Saving does not join and joining does not save."""
        trip = _shared_trip()
        client.force_login(UserFactory.create())

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-save-route-form"' in html

    def test_the_share_pages_control_is_token_addressed(self, client: Client) -> None:
        """A link-holder must never be handed the trip's uuid."""
        trip = _shared_trip()
        client.force_login(UserFactory.create())

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert trip.share_token in html
        assert str(trip.uuid) not in html

    def test_renders_saved_once_the_route_is_on_the_account(
        self, client: Client
    ) -> None:
        """The user asked for it and got it; a second press has nothing
        left to do.
        """
        trip = _shared_trip()
        viewer = UserFactory.create()
        client.force_login(viewer)
        client.post(
            reverse("trips:save_route_shared", args=[trip.share_token]), **_HTMX
        )

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-route-saved"' in html
        assert 'data-testid="trip-save-route-form"' not in html

    def test_an_anonymous_visitor_gets_the_sign_in_link(self, client: Client) -> None:
        """Never a button whose only outcome is a swallowed 403."""
        trip = _shared_trip()

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-save-route"' in html
        assert 'data-testid="trip-save-route-form"' not in html
        assert reverse("accounts:sign_in") in html
