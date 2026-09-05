"""
tests/trips/test_route_geojson_views.py — the SNOW-828 map endpoints.

The two endpoints behind "View on the map", split by who may ask:

trip_route_geojson (GET /trips/<uuid>/route.geojson):
  200 for the organiser and for a participant who joined;
  403 anonymous, 404 for a trip the requester is not on.

trip_share_route_geojson (GET /trips/s/<token>/route.geojson):
  200 for an ANONYMOUS link-holder who is not a participant — that is the
  whole point of the ticket, and the case that was unreachable before it;
  404 for an unknown, revoked or expired token, all three identically,
  matching ``trip_share_page``;
  429 past the (token, IP) rate limit.

Plus what both answer with: geometry from the SNAPSHOT, surviving the
source route's deletion; a ``page_url`` addressed the way its own caller
may address the trip (uuid for a participant, token for a link-holder);
and ``Cache-Control: no-store``.

The rate-limit test patches ``is_ratelimited`` rather than spending a real
budget, for the reason ``test_share_views.py`` states: ``RATELIMIT_ENABLE``
is False under the development settings this suite runs on.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from apps.trips.services.participants import join_trip
from apps.trips.services.shares import mint_trip_share, revoke_trip_share
from tests.factories import TripFactory, UserFactory

# Well before TripFactory's default date, so a minted link is live.
_NOW = "2026-01-10T09:00:00+00:00"


def _route_feature(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the LineString Feature from a FeatureCollection payload."""
    return next(f for f in payload["features"] if f["properties"]["kind"] == "route")


def _meeting_feature(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the Point Feature from a FeatureCollection payload."""
    return next(f for f in payload["features"] if f["properties"]["kind"] == "meeting")


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTripRouteGeojson:
    """GET /trips/<uuid>/route.geojson — for somebody on the trip."""

    def test_the_organiser_gets_the_route(self, client: Client) -> None:
        """The organiser holds a participant row from creation."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:route_geojson", args=[trip.uuid]))

        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "FeatureCollection"
        assert _route_feature(payload)["geometry"]["coordinates"] == trip.points

    def test_a_participant_who_joined_gets_the_route(self, client: Client) -> None:
        """Scoped by MEMBERSHIP, not authorship."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        response = client.get(reverse("trips:route_geojson", args=[trip.uuid]))

        assert response.status_code == 200

    def test_anonymous_is_refused(self, client: Client) -> None:
        """403 rather than a sign-in redirect: a script reads this, not a person."""
        trip = TripFactory.create()

        response = client.get(reverse("trips:route_geojson", args=[trip.uuid]))

        assert response.status_code == 403

    def test_somebody_not_on_the_trip_gets_404(self, client: Client) -> None:
        """Not-yours and doesn't-exist answer identically."""
        trip = TripFactory.create()
        client.force_login(UserFactory.create())

        response = client.get(reverse("trips:route_geojson", args=[trip.uuid]))

        assert response.status_code == 404

    def test_the_page_url_is_uuid_addressed(self, client: Client) -> None:
        """A participant has the uuid, and the banner links them to it."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:route_geojson", args=[trip.uuid]))

        assert _route_feature(response.json())["properties"]["page_url"] == reverse(
            "trips:detail", args=[trip.uuid]
        )

    def test_it_is_never_cached(self, client: Client) -> None:
        """Per-recipient in the sense that matters — see trip_share_page."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:route_geojson", args=[trip.uuid]))

        assert response["Cache-Control"] == "no-store"


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTripShareRouteGeojson:
    """GET /trips/s/<token>/route.geojson — for somebody holding the link."""

    def test_an_anonymous_link_holder_gets_the_route(self, client: Client) -> None:
        """The case SNOW-828 exists for: a recipient who has not joined.

        ``routes:geojson`` is owner-scoped, so before this endpoint there
        was no route on the map for this person to focus at all.
        """
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        response = client.get(
            reverse("trips:share_route_geojson", args=[trip.share_token])
        )

        assert response.status_code == 200
        payload = response.json()
        assert _route_feature(payload)["geometry"]["coordinates"] == trip.points
        assert _meeting_feature(payload)["geometry"]["coordinates"] == [
            trip.meeting_point.longitude,
            trip.meeting_point.latitude,
        ]

    def test_somebody_without_the_link_cannot_guess_it(self, client: Client) -> None:
        """An unknown token is a 404, like every other dead link."""
        TripFactory.create()

        response = client.get(
            reverse("trips:share_route_geojson", args=["not-a-real-token"])
        )

        assert response.status_code == 404

    def test_a_revoked_token_draws_nothing(self, client: Client) -> None:
        """Revoking stops the map link, not just the page."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()
        token = trip.share_token
        revoke_trip_share(trip.created_by, trip.uuid)

        response = client.get(reverse("trips:share_route_geojson", args=[token]))

        assert response.status_code == 404

    def test_an_expired_token_draws_nothing(self, client: Client) -> None:
        """The window is measured from the trip's date — see share_expiry_for."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()
        token = trip.share_token
        trip.share_expires_at = datetime.datetime(
            2026, 1, 9, tzinfo=datetime.timezone.utc
        )
        trip.save(update_fields=["share_expires_at"])

        response = client.get(reverse("trips:share_route_geojson", args=[token]))

        assert response.status_code == 404

    def test_the_page_url_is_token_addressed(self, client: Client) -> None:
        """A link-holder must never be handed the uuid."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        response = client.get(
            reverse("trips:share_route_geojson", args=[trip.share_token])
        )

        page_url = _route_feature(response.json())["properties"]["page_url"]
        assert page_url == reverse("trips:share_page", args=[trip.share_token])
        assert str(trip.uuid) not in response.content.decode()

    def test_it_answers_429_past_the_rate_limit(self, client: Client) -> None:
        """The same token-guessing surface as the share page, so the same key."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
            response = client.get(
                reverse("trips:share_route_geojson", args=[trip.share_token])
            )

        assert response.status_code == 429


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTheGeometryIsTheSnapshot:
    """The snapshot is the trip — never the ``route`` FK."""

    def test_it_survives_the_organiser_deleting_the_source_route(
        self, client: Client
    ) -> None:
        """A trip outlives the route it was planned from (SNOW-820)."""
        trip = TripFactory.create()
        expected = list(trip.points)
        assert trip.route is not None
        trip.route.delete()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:route_geojson", args=[trip.uuid]))

        assert response.status_code == 200
        assert _route_feature(response.json())["geometry"]["coordinates"] == expected


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTheControlOnThePage:
    """The map control ships to both trip surfaces, addressed differently."""

    def test_the_object_page_links_by_uuid(self, client: Client) -> None:
        """A participant has the uuid, so the participant endpoint serves it."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:detail", args=[trip.uuid]))

        assert f"?trip={trip.uuid}" in response.content.decode()

    def test_the_share_page_links_by_token(self, client: Client) -> None:
        """And a link-holder gets the token, never the uuid."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        response = client.get(reverse("trips:share_page", args=[trip.share_token]))

        body = response.content.decode()
        assert f"?trip_share={trip.share_token}" in body
        assert str(trip.uuid) not in body
