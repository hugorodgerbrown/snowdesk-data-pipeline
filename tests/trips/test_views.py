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

The meeting point (SNOW-840):
  the coordinate pair with the ``what3words`` flag off; the three word
  address with it on and a cached address on the location; the coordinate
  pair again with the flag on but the conversion failing; and the address
  as a LINK to the configured what3words map host, which the coordinate
  fallback never grows.
  ``tests/trips/test_share_views.py`` asserts the same three on the public
  page, because both surfaces are built by one context builder and the
  point is that they cannot diverge.
"""

from __future__ import annotations

import datetime
import re
from typing import Any
from unittest.mock import patch

import pytest
import requests
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from pytest_django.fixtures import Settings
from waffle.testutils import override_flag

from apps.locations.models import Location
from apps.trips.models import Trip
from tests.factories import LocationFactory, RouteFactory, TripFactory, UserFactory

# The element the meeting point renders into, tag and contents. Matched
# rather than searched for whole-page so "the address replaced the
# coordinates" and "the coordinates are elsewhere on the page" cannot be
# confused: the map payload carries the same numbers.
#
# An ``<a>`` when there is an address and a ``<span>`` when there is only
# a coordinate — SNOW-848 moved the testid off the wrapping ``<dd>`` and
# onto the element itself, because the same partial now renders into the
# list card's footer, where there is no ``<dd>`` to hang it on.
_MEETING_POINT_ELEMENT = re.compile(
    r'<(?P<tag>a|span)[^>]*data-testid="trip-meeting-point".*?</(?P=tag)>',
    re.DOTALL,
)


def _meeting_point(cached: bool = False) -> Location:
    """Return an anonymous Location at a fixed coordinate (SNOW-840).

    Fixed rather than the factory's default so the rendered pair is
    known, and so a test asserting the fallback is asserting a string
    only this location could have produced.

    Args:
        cached: When True, the row carries a FRESH cached three word
            address, which ``fill_what3words`` returns without a call.

    Returns:
        The location, saved.

    """
    return LocationFactory.create(
        anonymous=True,
        latitude=46.080012,
        longitude=7.318197,
        what3words="filled.count.soap" if cached else None,
        what3words_fetched_at=timezone.now() if cached else None,
    )


def _meeting_point_element(html: str) -> str:
    """Return the meeting-point ``<dd>`` from a rendered trip page.

    Args:
        html: The rendered page.

    Returns:
        The element's source, tag and contents.

    """
    match = _MEETING_POINT_ELEMENT.search(html)
    assert match is not None, "the page rendered no meeting point"
    return match.group(0)


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
        """The default the hidden fields carry when nobody moves the pin.

        Since SNOW-840 it is also the only meeting point a visitor with no
        JavaScript can submit, which is why it has to be the route's own
        start rather than nothing.
        """
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

    def test_the_coordinate_fields_are_posted_but_hidden(self, client: Client) -> None:
        """The pin's transport survives SNOW-840; the boxes do not.

        The marker WRITES to these fields and they are what the form
        posts, so if they stop rendering a trip submits nothing for its
        meeting point and 400s on a field nobody was shown. What changed is
        their widget: they are hidden inputs, found by the picker through
        the same ``data-meeting-*`` attributes.
        """
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert "data-meeting-latitude" in html
        assert "data-meeting-longitude" in html
        assert html.count('type="hidden" name="latitude"') == 1
        assert html.count('type="hidden" name="longitude"') == 1

    def test_the_manual_coordinate_panel_is_gone(self, client: Client) -> None:
        """SNOW-840 removed the "Enter coordinates manually" disclosure.

        Hugo: "no one is going to use that." Asserted on the rendered page
        because that is where the cost was — every organiser read the
        panel's title and dismissed it to serve the few who would have
        opened it.
        """
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert "Enter coordinates manually" not in html
        assert "trip-meeting-manual" not in html

    def test_the_conversion_copy_is_absent_with_the_flag_off(
        self, client: Client
    ) -> None:
        """With no conversion happening, the sentence would be a lie."""
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert "three word address" not in html

    @override_flag("what3words", active=True)
    def test_the_conversion_copy_appears_with_the_flag_on(self, client: Client) -> None:
        """The organiser is told what becomes of the pin they just dropped."""
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert 'data-testid="trip-meeting-conversion"' in html
        assert "three word address" in html

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
class TestTripMeetingPointAddress:
    """The meeting point as ///filled.count.soap (SNOW-840).

    Three states, and the one that matters is the third: the coordinate
    pair is the fallback for every failure, so there is never a blank
    where the meeting point was.
    """

    def test_the_coordinates_render_with_the_flag_off(self, client: Client) -> None:
        """Flag off is today's page, unchanged — and makes no call."""
        trip = TripFactory.create(meeting_point=_meeting_point())
        client.force_login(trip.created_by)

        with patch("apps.locations.services.what3words.requests.get") as mock_get:
            html = client.get(
                reverse("trips:detail", args=[trip.uuid])
            ).content.decode()

        assert "46.080012, 7.318197" in html
        assert "///" not in _meeting_point_element(html)
        mock_get.assert_not_called()

    @override_flag("what3words", active=True)
    def test_the_address_renders_with_the_flag_on(self, client: Client) -> None:
        """A cached address replaces the pair, prefixed with ///."""
        trip = TripFactory.create(meeting_point=_meeting_point(cached=True))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert "///filled.count.soap" in _meeting_point_element(html)

    @override_flag("what3words", active=True)
    def test_the_coordinates_stay_available_as_the_titles(self, client: Client) -> None:
        """The paste-into-a-map-app affordance survives the swap."""
        trip = TripFactory.create(meeting_point=_meeting_point(cached=True))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'title="46.080012, 7.318197"' in _meeting_point_element(html)

    @override_flag("what3words", active=True)
    def test_the_edit_form_promises_the_conversion_too(self, client: Client) -> None:
        """The organiser's edit picker is the fourth render path.

        ``_trip_context`` builds it, so the flag has to reach it from
        there rather than from ``_what3words_context`` — a trip page whose
        edit form said nothing while the new-trip form did would be two
        answers to one question.
        """
        trip = TripFactory.create(meeting_point=_meeting_point())
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-testid="trip-meeting-conversion"' in html

    @override_flag("what3words", active=True)
    @override_settings(WHAT3WORDS_MAP_BASE_URL="https://w3w.example")
    def test_the_address_links_to_the_configured_map_host(self, client: Client) -> None:
        """The words link to the square on what3words' own map.

        The host comes from the setting rather than a literal in the
        template, so this asserts the CONFIGURED one — a hardcoded
        what3words.com would pass against the default and hide the fact
        that nothing reads the setting.
        """
        trip = TripFactory.create(meeting_point=_meeting_point(cached=True))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'href="https://w3w.example/filled.count.soap"' in _meeting_point_element(
            html
        )

    @override_flag("what3words", active=True)
    def test_the_address_link_opens_away_and_hands_over_no_handle(
        self, client: Client
    ) -> None:
        """An off-site link gets a new tab and rel="noopener noreferrer"."""
        trip = TripFactory.create(meeting_point=_meeting_point(cached=True))
        client.force_login(trip.created_by)

        dd = _meeting_point_element(
            client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        )

        assert 'target="_blank"' in dd
        assert 'rel="noopener noreferrer"' in dd

    def test_the_coordinate_fallback_carries_no_link(self, client: Client) -> None:
        """No address means no anchor — never a link to nowhere.

        ``meeting_point_w3w_url`` is None exactly when the address is, so
        the coordinate pair renders as the bare text it always has. A
        template that built the href itself would emit ``.../None`` here.
        """
        trip = TripFactory.create(meeting_point=_meeting_point())
        client.force_login(trip.created_by)

        dd = _meeting_point_element(
            client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        )

        assert "<a" not in dd
        assert "46.080012, 7.318197" in dd

    @override_flag("what3words", active=True)
    def test_a_failing_conversion_falls_back_to_the_coordinates(
        self, client: Client, settings: Settings
    ) -> None:
        """A what3words outage must not take the trip page with it."""
        settings.WHAT3WORDS_API_KEY = "test-key"
        trip = TripFactory.create(meeting_point=_meeting_point())
        client.force_login(trip.created_by)

        with patch(
            "apps.locations.services.what3words.requests.get",
            side_effect=requests.Timeout("too slow"),
        ):
            response = client.get(reverse("trips:detail", args=[trip.uuid]))

        assert response.status_code == 200
        assert "46.080012, 7.318197" in response.content.decode()


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

    def test_a_hand_crafted_coordinate_off_the_planet_is_still_rejected(
        self, client: Client
    ) -> None:
        """SNOW-840 hid the fields; the bounds that guard them are unmoved.

        ``min_value``/``max_value`` are FIELD-level, so they never relied
        on a browser validating a number box — and a hand-crafted POST is
        now the only way a value the marker did not write can arrive at
        all.
        """
        route = RouteFactory.create()
        client.force_login(route.user)
        payload = _valid_post(str(route.uuid))
        payload["latitude"] = "91.0"

        response = client.post(reverse("trips:create"), payload, **_HTMX)

        assert response.status_code == 400
        assert Trip.objects.count() == 0

    @override_flag("what3words", active=True)
    def test_the_re_rendered_form_still_carries_the_conversion_copy(
        self, client: Client
    ) -> None:
        """The flag reaches the error re-render, not just the first paint.

        This is the render path a missing ``_what3words_context`` would
        silently lose: the form comes back whole after a validation error,
        so an organiser who mistyped the date would watch the sentence
        about their pin disappear.
        """
        route = RouteFactory.create()
        client.force_login(route.user)
        payload = _valid_post(str(route.uuid))
        payload["date"] = ""

        response = client.post(reverse("trips:create"), payload, **_HTMX)

        assert response.status_code == 400
        assert 'data-testid="trip-meeting-conversion"' in response.content.decode()

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

    @override_flag("what3words", active=True)
    def test_the_re_rendered_form_still_carries_the_conversion_copy(
        self, client: Client
    ) -> None:
        """The edit path's error re-render is the fourth picker render."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        payload = _valid_post()
        payload["start_time"] = ""

        response = client.post(
            reverse("trips:edit", args=[trip.uuid]), payload, **_HTMX
        )

        assert response.status_code == 400
        assert 'data-testid="trip-meeting-conversion"' in response.content.decode()

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


@pytest.mark.django_db
class TestTripSummaryFigures:
    """The figures line on a trip page (trips/partials/_trip_map.html).

    It lived in ``_trip_summary.html`` until SNOW-840 moved it into the
    route-profile card, which is why the class is still named for the
    summary. The assertions below are about the WORDING and are indifferent
    to which partial renders it; the one that is not says so.

    A trip's figures ARE its route's figures — the geometry is a snapshot
    of one — so they are spelled the way the routes panel and the map
    popup spell them (SNOW-830): the unit closed up to its value, and
    ascent and descent as ↑ and ↓. A user meeting the same numbers on
    three surfaces should not meet three spellings.

    Untested until SNOW-830 changed the wording, which is how the page
    came to be the last surface still saying "850 m ascent" — nothing
    failed when the other two moved.
    """

    def test_all_three_figures_are_rendered(self, client: Client) -> None:
        """Distance, ascent and descent, in the shared spelling."""
        trip = TripFactory.create(distance_m=12400.0, ascent_m=850.0, descent_m=1100.0)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert "12.4km · 850m ↑ · 1100m ↓" in html

    def test_a_null_ascent_and_descent_leave_the_distance_alone(
        self, client: Client
    ) -> None:
        """No elevation data shows the distance and nothing else.

        Null is "the source route carried no <ele>", which Trip's own
        docstring separates from flat. Rendering "0m ↑" for it would be a
        safety-relevant lie about terrain somebody is planning to ski, so
        the figures are dropped rather than zeroed.
        """
        trip = TripFactory.create(distance_m=12400.0, ascent_m=None, descent_m=None)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        figures = re.search(r'data-testid="trip-figures"[^>]*>(.*?)</p>', html, re.S)

        assert figures is not None
        assert figures.group(1).strip() == "12.4km"

    def test_a_zero_ascent_is_still_stated(self, client: Client) -> None:
        """A measured zero is a fact, and is not the same as unknown."""
        trip = TripFactory.create(distance_m=5000.0, ascent_m=0.0, descent_m=0.0)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert "5.0km · 0m ↑ · 0m ↓" in html

    def test_the_figures_sit_with_the_profile_they_scale(self, client: Client) -> None:
        """SNOW-840 moved the line into the route-profile card.

        The drawing is a picture of these numbers, so the two are one
        object — the map page's route popup has always framed them
        together. Asserted by ORDER against the profile's own container:
        the figures used to sit in the summary, above the map, which is
        several hundred pixels and a card away from the curve they scale.
        """
        trip = TripFactory.create(distance_m=12400.0, ascent_m=850.0, descent_m=1100.0)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        summary = html.index('data-testid="trip-summary"')
        figures = html.index('data-testid="trip-figures"')
        profile = html.index("data-trip-profile")
        assert summary < figures < profile
