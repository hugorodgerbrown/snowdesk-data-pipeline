"""
tests/public/test_area_items_api.py — Tests for /api/area-items/ (SNOW-965).

Covers:
  trips          — kept when the snapshot bounds touch the box, on all four
                   edges; organised and joined trips alike; never another
                   user's.
  pins           — kept when the point sits in the box, on all four edges;
                   region pins never; never another user's.
  names          — each trip and pin carries the label it is listed under
                   on the /offline/ panels (SNOW-1308).
  forecasts      — each pin names the curated point it borrows, even one
                   outside the box; ``null`` when no point has a row today.
  anonymous      — 200, empty, zero queries.
  bbox validation — a bad box is a 400 that is still ``no-store``.
  response policy — ``private``, ``no-store``, ``Vary: Cookie``, no ETag.
  attestation    — names its viewer in ``X-Snowdesk-Principal`` (SNOW-1308);
                   anonymous sends none.
  query cost     — flat: one pin and five pins cost the same.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.identity import PRINCIPAL_RESPONSE_HEADER
from apps.trips.services.participants import join_trip
from apps.trips.services.shares import mint_trip_share
from tests.factories import (
    AccountFactory,
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    ResortLocationFactory,
    TripFactory,
    UserFactory,
    WeatherFactory,
)

# The box every test asks about: 7.0–8.0 E, 46.0–47.0 N.
BOX = "7.0,46.0,8.0,47.0"


def _url(bbox: str | None = BOX) -> str:
    """Return the endpoint URL, optionally with a ``?bbox=`` parameter.

    Args:
        bbox: The raw query-parameter value, or None for a bare request.

    Returns:
        The URL to GET.

    """
    base = reverse("api:area_items")
    return base if bbox is None else f"{base}?bbox={bbox}"


def _forecast_point(latitude: float, longitude: float) -> str:
    """Create a public point with a row for today and return its short id.

    Args:
        latitude: The point's latitude.
        longitude: The point's longitude.

    Returns:
        The point's ``short_id``.

    """
    point = LocationFactory.create(latitude=latitude, longitude=longitude)
    ResortLocationFactory.create(location=point)
    WeatherFactory.create(location=point, observed_on=timezone.localdate())
    return str(point.short_id)


def _signed_in(client: Client) -> User:
    """Sign a fresh user in and return them.

    Args:
        client: The test client.

    Returns:
        The signed-in user.

    """
    user = UserFactory.create()
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestTrips:
    """A trip is kept when its snapshot bounds touch the box."""

    @pytest.mark.parametrize(
        "bounds",
        [
            [6.5, 46.4, 7.0, 46.6],  # touches the west edge
            [8.0, 46.4, 8.5, 46.6],  # touches the east edge
            [7.4, 45.5, 7.6, 46.0],  # touches the south edge
            [7.4, 47.0, 7.6, 47.5],  # touches the north edge
            [7.4, 46.4, 7.6, 46.6],  # wholly inside
        ],
    )
    def test_a_trip_touching_the_box_is_kept(
        self, client: Client, bounds: list[float]
    ) -> None:
        """Edge-inclusive, the rule area-content applies to regions."""
        user = _signed_in(client)
        trip = TripFactory.create(created_by=user, bounds=bounds)

        response = client.get(_url())

        assert response.status_code == 200
        assert response.json()["trips"] == [
            {"uuid": str(trip.uuid), "name": trip.display_name}
        ]

    def test_a_trip_clear_of_the_box_is_left_out(self, client: Client) -> None:
        """A trip a millidegree past the edge is not in the area."""
        user = _signed_in(client)
        TripFactory.create(created_by=user, bounds=[8.001, 46.4, 8.5, 46.6])

        assert client.get(_url()).json()["trips"] == []

    def test_organised_and_joined_trips_are_both_returned(self, client: Client) -> None:
        """Participation, not authorship — the rule ``for_user`` states."""
        user = _signed_in(client)
        organised = TripFactory.create(created_by=user)
        joined = TripFactory.create()
        mint_trip_share(joined.created_by, joined.uuid)
        joined.refresh_from_db()
        join_trip(user, joined)

        uuids = {row["uuid"] for row in client.get(_url()).json()["trips"]}

        assert uuids == {str(organised.uuid), str(joined.uuid)}

    def test_another_users_trip_is_never_returned(self, client: Client) -> None:
        """Somebody else's trip inside the box is not yours to carry."""
        _signed_in(client)
        TripFactory.create()

        assert client.get(_url()).json()["trips"] == []


@pytest.mark.django_db
class TestPins:
    """A pin is kept when its point sits in the box."""

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [(46.5, 7.0), (46.5, 8.0), (46.0, 7.5), (47.0, 7.5), (46.5, 7.5)],
    )
    def test_a_pin_on_or_inside_the_box_is_kept(
        self, client: Client, latitude: float, longitude: float
    ) -> None:
        """Edge-inclusive on all four sides."""
        user = _signed_in(client)
        pin = FavouriteFactory.create(user=user, latitude=latitude, longitude=longitude)

        pins = client.get(_url()).json()["pins"]

        assert [row["uuid"] for row in pins] == [str(pin.uuid)]

    def test_a_pin_outside_the_box_is_left_out(self, client: Client) -> None:
        """Past the edge is out."""
        user = _signed_in(client)
        FavouriteFactory.create(user=user, latitude=46.5, longitude=8.001)

        assert client.get(_url()).json()["pins"] == []

    def test_a_region_pin_is_never_returned(self, client: Client) -> None:
        """A region pin has no point, so no place in a rectangle."""
        user = _signed_in(client)
        FavouriteFactory.create(
            user=user, region_pin=True, region=MicroRegionFactory.create()
        )

        assert client.get(_url()).json()["pins"] == []

    def test_another_users_pin_is_never_returned(self, client: Client) -> None:
        """Somebody else's pin inside the box is not yours."""
        _signed_in(client)
        FavouriteFactory.create(latitude=46.5, longitude=7.5)

        assert client.get(_url()).json()["pins"] == []


@pytest.mark.django_db
class TestBorrowedForecasts:
    """Each pin names the curated point whose forecast it borrows."""

    def test_a_borrowed_point_outside_the_box_is_returned(self, client: Client) -> None:
        """The point is nearest at any distance — that is why it is here."""
        user = _signed_in(client)
        FavouriteFactory.create(user=user, latitude=46.9, longitude=7.9)
        outside = _forecast_point(47.2, 8.2)
        _forecast_point(45.0, 6.0)

        pins = client.get(_url()).json()["pins"]

        assert pins[0]["forecast"] == {"short_id": outside}

    def test_no_row_today_borrows_nothing(self, client: Client) -> None:
        """A point with only yesterday's row is no forecast for today."""
        user = _signed_in(client)
        FavouriteFactory.create(user=user, latitude=46.5, longitude=7.5)
        point = LocationFactory.create(latitude=46.5, longitude=7.5)
        ResortLocationFactory.create(location=point)
        WeatherFactory.create(
            location=point,
            observed_on=timezone.localdate() - datetime.timedelta(days=1),
        )

        pins = client.get(_url()).json()["pins"]

        assert pins[0]["forecast"] is None


@pytest.mark.django_db
class TestNames:
    """Each trip and pin carries the name the /offline/ panels list it by."""

    def test_a_trip_is_named_by_its_own_label(self, client: Client) -> None:
        """A trip's own name wins over its route's."""
        user = _signed_in(client)
        TripFactory.create(created_by=user, name="Saturday tour", route_name="Route")

        trips = client.get(_url()).json()["trips"]

        assert trips[0]["name"] == "Saturday tour"

    def test_an_unnamed_trip_falls_back_to_its_route(self, client: Client) -> None:
        """``Trip.display_name``'s fallback, read in the same query."""
        user = _signed_in(client)
        TripFactory.create(created_by=user, name="", route_name="Haute Route")

        trips = client.get(_url()).json()["trips"]

        assert trips[0]["name"] == "Haute Route"

    def test_a_pin_is_named_by_its_own_label(self, client: Client) -> None:
        """The label the reader gave the pin."""
        user = _signed_in(client)
        FavouriteFactory.create(user=user, latitude=46.5, longitude=7.5, name="Hut")

        pins = client.get(_url()).json()["pins"]

        assert pins[0]["name"] == "Hut"

    def test_a_saved_resort_is_named_by_its_resort(self, client: Client) -> None:
        """An unnamed resort favourite reads as the resort."""
        user = _signed_in(client)
        resort = ResortFactory.create(name="Verbier")
        FavouriteFactory.create(
            user=user, latitude=46.5, longitude=7.5, name="", resort=resort
        )

        pins = client.get(_url()).json()["pins"]

        assert pins[0]["name"] == "Verbier"

    def test_an_unnamed_pin_has_an_empty_name(self, client: Client) -> None:
        """The client words an unnamed pin itself."""
        user = _signed_in(client)
        FavouriteFactory.create(user=user, latitude=46.5, longitude=7.5, name="")

        pins = client.get(_url()).json()["pins"]

        assert pins[0]["name"] == ""


@pytest.mark.django_db
class TestAnonymous:
    """Someone signed out has nothing of their own to carry."""

    def test_anonymous_gets_an_empty_answer_without_a_query(
        self, client: Client, django_assert_num_queries: Callable[..., Any]
    ) -> None:
        """200 rather than 403, so an existing area's refresh does not fail."""
        TripFactory.create()
        FavouriteFactory.create(latitude=46.5, longitude=7.5)

        with django_assert_num_queries(0):
            response = client.get(_url())

        assert response.status_code == 200
        assert response.json() == {"trips": [], "pins": []}


@pytest.mark.django_db
class TestValidationAndPolicy:
    """The parser area-content uses, and a policy that never caches."""

    @pytest.mark.parametrize("bbox", [None, "", "a,b,c,d", "7,46,8", "8,46,7,47"])
    def test_a_bad_box_is_a_400_that_is_not_stored(
        self, client: Client, bbox: str | None
    ) -> None:
        """Missing, non-numeric, short and inverted boxes all 400."""
        _signed_in(client)

        response = client.get(_url(bbox))

        assert response.status_code == 400
        assert response.json() == {"error": "invalid_bbox"}
        assert "no-store" in response["Cache-Control"]

    def test_the_answer_is_private_and_never_stored(self, client: Client) -> None:
        """Per-user, so no shared or worker cache may hold it."""
        _signed_in(client)

        response = client.get(_url())

        directives = {d.strip() for d in response["Cache-Control"].split(",")}
        assert {"private", "no-store"} <= directives
        assert "Cookie" in response["Vary"]
        assert not response.has_header("ETag")

    def test_the_answer_names_its_viewer(self, client: Client) -> None:
        """SNOW-1308: the client stamps names with the account that answered.

        Not the page's render-time ``pwa-user-id``, which a sign-in in
        another tab can leave stale — so the answer itself says whose it is.
        """
        account = AccountFactory.create()
        client.force_login(account.user)

        response = client.get(_url())

        assert response[PRINCIPAL_RESPONSE_HEADER] == str(account.uuid)

    def test_an_anonymous_answer_names_nobody(self, client: Client) -> None:
        """No viewer, no header — read as "do not attribute"."""
        response = client.get(_url())

        assert not response.has_header(PRINCIPAL_RESPONSE_HEADER)

    def test_a_post_is_refused(self, client: Client) -> None:
        """Read-only."""
        _signed_in(client)

        assert client.post(_url()).status_code == 405


@pytest.mark.django_db
class TestQueryCost:
    """Flat however many pins sit in the box."""

    @staticmethod
    def _queries_for(client: Client, pins: int) -> int:
        """Return how many queries one request costs with ``pins`` pins.

        Args:
            client: The test client.
            pins: How many of a fresh user's pins to place in the box.

        Returns:
            The number of queries the request ran.

        """
        user = _signed_in(client)
        TripFactory.create(created_by=user)
        for index in range(pins):
            FavouriteFactory.create(
                user=user, latitude=46.1 + index * 0.1, longitude=7.5
            )
        with CaptureQueriesContext(connection) as context:
            client.get(_url())
        return len(context.captured_queries)

    def test_one_pin_and_five_pins_cost_the_same(self, client: Client) -> None:
        """Trips, pins and one candidate scan — never a query per pin."""
        _forecast_point(46.5, 7.5)
        one = self._queries_for(client, 1)
        client.logout()
        five = self._queries_for(client, 5)

        assert one == five
