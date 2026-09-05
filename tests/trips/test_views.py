"""
tests/trips/test_views.py — Tests for apps.trips.views.

trip_new (GET /trips/new/):
  200 for the owner of the named route, with the meeting point prefilled;
  404 for a missing, malformed or someone else's route;
  redirects an anonymous visitor to sign-in.

trip_detail (GET /trips/<uuid>/):
  200 for the organiser; 404 for anyone else and for an anonymous
    visitor's uuid guess;
  the organiser's description is ESCAPED, never marked safe (invariant 1);
  the map payload is emitted inline;
  the edit form prefills the day and time in the ISO shapes an HTML date /
    time input accepts, not the active locale's (SNOW-834).

The three fragments (trips:create / trips:edit / trips:delete):
  400 for a plain non-HTMX request (invariant 4);
  403 for an anonymous HTMX request;
  create writes a trip and answers HX-Redirect to the LIST, carrying the
    new trip's uuid (SNOW-834);
  create re-renders the form at 400 for an invalid submission;
  create answers 409 at the cap;
  edit and delete are organiser-scoped and 404 otherwise.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.trips.models import Trip
from tests.factories import RouteFactory, TripFactory, UserFactory

# Annotated ``dict[str, Any]`` so ``**_HTMX`` unpacks into ``Client.post``
# without mypy matching it against that method's typed keyword parameters.
# The same annotation tests/routes/test_views.py carries, for the same reason.
_HTMX: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


def _valid_post(route_uuid: str = "") -> dict[str, str]:
    """Return a valid trip form submission.

    Args:
        route_uuid: The source route's uuid, for the create path. Omitted
            on the edit path, whose URL already names the trip.

    Returns:
        A POST payload dict.

    """
    payload = {
        "date": "2026-03-14",
        "start_time": "07:30",
        "name": "Rosablanche",
        "description": "Bring skins.",
        "latitude": "46.1",
        "longitude": "7.4",
    }
    if route_uuid:
        payload["route"] = route_uuid
    return payload


@pytest.mark.django_db
class TestTripNewPage:
    """The authoring page."""

    def test_renders_for_the_routes_owner(self, client: Client) -> None:
        """200, with the route named on the page."""
        route = RouteFactory.create(name="Rosablanche")
        client.force_login(route.user)
        response = client.get(f"{reverse('trips:new')}?route={route.uuid}")
        assert response.status_code == 200
        assert "Rosablanche" in response.content.decode()

    def test_prefills_the_meeting_point_from_the_first_coordinate(
        self, client: Client
    ) -> None:
        """The default that makes the coordinate fields workable."""
        route = RouteFactory.create()
        client.force_login(route.user)
        response = client.get(f"{reverse('trips:new')}?route={route.uuid}")
        html = response.content.decode()
        assert 'value="46.1"' in html
        assert 'value="7.4"' in html

    def test_the_meeting_point_is_a_map_with_the_route_on_it(
        self, client: Client
    ) -> None:
        """The pin picker renders, with the track to place the pin against.

        Asserted from the page rather than from the template, on SNOW-668's
        rule: a control that is merely rendered somewhere is not a control
        a user can reach. The payload carries the route so the organiser
        places the pin against the track rather than against blank tiles.
        """
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert "data-trip-meeting-picker" in html
        assert 'id="trip-meeting-payload"' in html
        assert "trip_meeting_picker.js" in html
        # The track itself, not just an empty payload envelope.
        assert "LineString" in html

    def test_the_coordinate_fields_survive_as_the_manual_escape_hatch(
        self, client: Client
    ) -> None:
        """A keyboard visitor, and anyone with no JavaScript, still has them.

        The pin WRITES to these; it does not replace them. If they ever
        stop rendering, the no-JavaScript path submits nothing for the
        meeting point and the form 400s for a field the visitor was never
        shown.
        """
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert "data-meeting-latitude" in html
        assert "data-meeting-longitude" in html

    def test_404_for_another_users_route(self, client: Client) -> None:
        """Never 403 — no existence oracle."""
        route = RouteFactory.create()
        client.force_login(UserFactory.create())
        response = client.get(f"{reverse('trips:new')}?route={route.uuid}")
        assert response.status_code == 404

    def test_404_for_a_malformed_uuid(self, client: Client) -> None:
        """A junk parameter is not a 500."""
        client.force_login(UserFactory.create())
        response = client.get(f"{reverse('trips:new')}?route=not-a-uuid")
        assert response.status_code == 404

    def test_anonymous_is_sent_to_sign_in(self, client: Client) -> None:
        """A page, so a redirect rather than a 403."""
        response = client.get(reverse("trips:new"))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")


@pytest.mark.django_db
class TestTripDetailPage:
    """The trip's own page — organiser-only in SNOW-820."""

    def test_renders_for_the_organiser(self, client: Client) -> None:
        """200, with the plan on the page."""
        trip = TripFactory.create(name="Rosablanche")
        client.force_login(trip.created_by)
        response = client.get(reverse("trips:detail", args=[trip.uuid]))
        assert response.status_code == 200
        assert "Rosablanche" in response.content.decode()

    def test_404_for_anybody_else(self, client: Client) -> None:
        """Organiser-only until SNOW-821/822 widen it."""
        trip = TripFactory.create()
        client.force_login(UserFactory.create())
        response = client.get(reverse("trips:detail", args=[trip.uuid]))
        assert response.status_code == 404

    def test_anonymous_is_sent_to_sign_in(self, client: Client) -> None:
        """Sign-in is the honest answer while there is no public trip surface."""
        trip = TripFactory.create()
        response = client.get(reverse("trips:detail", args=[trip.uuid]))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")

    def test_emits_the_map_payload_inline(self, client: Client) -> None:
        """No second request to draw the page's own map."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        response = client.get(reverse("trips:detail", args=[trip.uuid]))
        assert 'id="trip-map-payload"' in response.content.decode()

    def test_the_organisers_note_is_escaped(self, client: Client) -> None:
        """Invariant 1 — no mark_safe on user-supplied content."""
        trip = TripFactory.create(description="<script>alert(1)</script>")
        client.force_login(trip.created_by)
        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_shows_the_organiser_controls(self, client: Client) -> None:
        """Edit and delete are the organiser's, and they are on the page."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        assert 'data-testid="trip-organiser-controls"' in html

    @override_settings(LANGUAGE_CODE="en-gb")
    def test_the_edit_form_prefills_the_day_a_browser_can_read(
        self, client: Client
    ) -> None:
        """The date input's value is ISO, not the active locale's spelling.

        This project runs ``en-gb``, whose localised date is "12/09/2026" —
        not a value ``<input type="date">`` accepts, so the browser rendered
        an EMPTY field and every edit silently blanked the trip's day. The
        locale is pinned on this test because that is the condition under
        which it fails.
        """
        trip = TripFactory.create(date=datetime.date(2026, 9, 12))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'name="date" value="2026-09-12"' in html
        assert 'value="12/09/2026"' not in html

    @override_settings(LANGUAGE_CODE="en-gb")
    def test_the_edit_form_prefills_the_meeting_time(self, client: Client) -> None:
        """``HH:MM``, the one shape every browser normalises to.

        The seconds en-gb renders are legal in the HTML value and survive,
        but a field that comes back spelled differently from how it went in
        is a field the next round-trip can lose.
        """
        trip = TripFactory.create(start_time=datetime.time(7, 30))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'name="start_time" value="07:30"' in html


@pytest.mark.django_db
class TestFragmentsRejectPlainRequests:
    """Invariant 4 — every partials/ endpoint is @require_htmx."""

    def test_every_fragment_400s_without_the_htmx_header(self, client: Client) -> None:
        """A plain POST to any of the three is a 400."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        for url in (
            reverse("trips:create"),
            reverse("trips:edit", args=[trip.uuid]),
            reverse("trips:delete", args=[trip.uuid]),
        ):
            assert client.post(url).status_code == 400, url

    def test_every_fragment_403s_for_an_anonymous_htmx_request(
        self, client: Client
    ) -> None:
        """Authentication is checked after the HTMX gate, before the work."""
        trip = TripFactory.create()
        for url in (
            reverse("trips:create"),
            reverse("trips:edit", args=[trip.uuid]),
            reverse("trips:delete", args=[trip.uuid]),
        ):
            assert client.post(url, **_HTMX).status_code == 403, url


@pytest.mark.django_db
class TestTripCreate:
    """POST /trips/partials/create/."""

    def test_writes_a_trip_and_redirects_to_the_list(self, client: Client) -> None:
        """HX-Redirect to the LIST, carrying the new trip's uuid.

        Not to the trip's own page (SNOW-834): the list is what says what
        the organiser now has, and the uuid is how it confirms this write
        and marks the row it made.
        """
        route = RouteFactory.create()
        client.force_login(route.user)
        response = client.post(
            reverse("trips:create"), _valid_post(str(route.uuid)), **_HTMX
        )
        assert response.status_code == 200
        trip = Trip.objects.get()
        assert response["HX-Redirect"] == f"{reverse('trips:list')}?created={trip.uuid}"
        assert trip.name == "Rosablanche"
        assert trip.date == datetime.date(2026, 3, 14)

    def test_an_invalid_submission_comes_back_as_the_form(self, client: Client) -> None:
        """400 with the errors, swapped in place — the reason it is a fragment."""
        route = RouteFactory.create()
        client.force_login(route.user)
        payload = _valid_post(str(route.uuid))
        payload["date"] = ""
        response = client.post(reverse("trips:create"), payload, **_HTMX)
        assert response.status_code == 400
        assert 'id="trip-form"' in response.content.decode()
        assert Trip.objects.count() == 0

    def test_a_missing_route_is_a_400(self, client: Client) -> None:
        """No route means nothing to plan from."""
        client.force_login(UserFactory.create())
        response = client.post(reverse("trips:create"), _valid_post(), **_HTMX)
        assert response.status_code == 400

    def test_another_users_route_is_a_404(self, client: Client) -> None:
        """Owner-scoped through the service's own lookup."""
        route = RouteFactory.create()
        client.force_login(UserFactory.create())
        response = client.post(
            reverse("trips:create"), _valid_post(str(route.uuid)), **_HTMX
        )
        assert response.status_code == 404

    @override_settings(TRIPS_MAX_PER_USER=0)
    def test_the_cap_answers_409_not_429(self, client: Client) -> None:
        """A cap is a permanent failure until a trip is deleted."""
        route = RouteFactory.create()
        client.force_login(route.user)
        response = client.post(
            reverse("trips:create"), _valid_post(str(route.uuid)), **_HTMX
        )
        assert response.status_code == 409
        assert 'data-testid="trip-limit"' in response.content.decode()


@pytest.mark.django_db
class TestTripEdit:
    """POST /trips/partials/<uuid>/edit/."""

    def test_updates_and_redirects_back_to_the_trip(self, client: Client) -> None:
        """A whole-page repaint, because the page draws one context."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        payload = _valid_post()
        payload["name"] = "Moved"
        response = client.post(
            reverse("trips:edit", args=[trip.uuid]), payload, **_HTMX
        )
        assert response.status_code == 200
        assert response["HX-Redirect"] == reverse("trips:detail", args=[trip.uuid])
        trip.refresh_from_db()
        assert trip.name == "Moved"

    def test_404_for_a_non_organiser(self, client: Client) -> None:
        """Organiser-scoped through update_trip's own lookup."""
        trip = TripFactory.create()
        client.force_login(UserFactory.create())
        response = client.post(
            reverse("trips:edit", args=[trip.uuid]), _valid_post(), **_HTMX
        )
        assert response.status_code == 404

    def test_an_invalid_submission_comes_back_as_the_form(self, client: Client) -> None:
        """Same contract as create's."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        payload = _valid_post()
        payload["start_time"] = "not a time"
        response = client.post(
            reverse("trips:edit", args=[trip.uuid]), payload, **_HTMX
        )
        assert response.status_code == 400
        assert 'id="trip-form"' in response.content.decode()


@pytest.mark.django_db
class TestTripDelete:
    """POST /trips/partials/<uuid>/delete/."""

    def test_deletes_and_redirects(self, client: Client) -> None:
        """The trip goes, and the organiser is sent somewhere real."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        response = client.post(reverse("trips:delete", args=[trip.uuid]), **_HTMX)
        assert response.status_code == 200
        assert response["HX-Redirect"]
        assert not Trip.objects.filter(pk=trip.pk).exists()

    def test_404_for_a_non_organiser(self, client: Client) -> None:
        """A participant cannot delete the trip out from under everyone."""
        trip = TripFactory.create()
        client.force_login(UserFactory.create())
        response = client.post(reverse("trips:delete", args=[trip.uuid]), **_HTMX)
        assert response.status_code == 404
        assert Trip.objects.filter(pk=trip.pk).exists()
