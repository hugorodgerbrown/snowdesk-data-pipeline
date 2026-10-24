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
from freezegun import freeze_time
from pytest_django.fixtures import Settings
from waffle.testutils import override_flag

from apps.locations.models import Location
from apps.trips.models import Trip
from tests.factories import (
    DRIFTING_TRACK,
    LocationFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    RouteFactory,
    TripFactory,
    UserFactory,
    drifting_track_record,
)

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


def _trip_day() -> datetime.date:
    """Return the day the valid submission plans for.

    Relative to today rather than a fixed date since SNOW-1211, which made
    the create form refuse a day already gone: a literal date in this file
    would start failing the day the calendar passed it.

    Returns:
        A date thirty days from today.

    """
    return timezone.localdate() + datetime.timedelta(days=30)


def _valid_post(route_uuid: str = "") -> dict[str, str]:
    """Return a valid trip form submission.

    Args:
        route_uuid: The source route's uuid, for the create path. Omitted
            on the edit path, whose URL already names the trip.

    Returns:
        A POST payload dict.

    """
    payload = {
        "date": _trip_day().isoformat(),
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

    @freeze_time("2026-10-24T09:00:00+00:00")
    def test_prefills_tomorrow_at_eight(self, client: Client) -> None:
        """SNOW-1211: no empty day or time box for Safari to draw blank.

        An iPad renders an empty native date or time input as a blank box
        with no placeholder, so the form opened looking broken.
        """
        route = RouteFactory.create()
        client.force_login(route.user)
        response = client.get(f"{reverse('trips:new')}?route={route.uuid}")
        html = response.content.decode()
        assert 'value="2026-10-25"' in html
        assert 'value="08:00"' in html

    def test_the_time_field_is_sized_to_fit(self, client: Client) -> None:
        """SNOW-1211 / SNOW-1215: the time field no longer spans the card.

        The native time input overflowed half an iPad card in SNOW-1211;
        since SNOW-1215 it sits alone under the day tiles, capped at the
        width a time needs.
        """
        route = RouteFactory.create()
        client.force_login(route.user)
        response = client.get(f"{reverse('trips:new')}?route={route.uuid}")
        assert "class='mt-4 max-w-44'" in response.content.decode()

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

    def test_the_address_line_is_absent_with_the_flag_off(self, client: Client) -> None:
        """With no conversion happening, there is no address to look up."""
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert "data-what3words-url" not in html
        assert "data-meeting-address" not in html

    @override_flag("what3words", active=True)
    def test_the_address_line_appears_with_the_flag_on(self, client: Client) -> None:
        """The picker is told where to look the pin's address up (SNOW-1214)."""
        route = RouteFactory.create()
        client.force_login(route.user)

        html = client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

        assert 'data-what3words-url="/trips/what3words/"' in html
        assert "data-meeting-address" in html

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

    def test_the_slope_seams_index_the_payloads_own_geometry(
        self, client: Client
    ) -> None:
        """SNOW-1053: one seam per boundary, into the coordinates sent."""
        trip = TripFactory.create(
            points=DRIFTING_TRACK, slope_samples=drifting_track_record()
        )
        client.force_login(trip.created_by)
        response = client.get(reverse("trips:detail", args=[trip.uuid]))
        route = response.context["map_payload"]["route"]
        slope = route["properties"]["slope"]
        coordinates = route["geometry"]["coordinates"]

        assert len(slope["seams"]) == len(slope["points"])
        assert slope["seams"][0] == 0
        assert slope["seams"][-1] == len(coordinates) - 1

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

        # A day outside the week arrives on the "Other" tile (SNOW-1215).
        assert re.search(
            r'name="date"\s+value="2026-09-12"[^>]*data-day-other\s+checked', html
        )
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

    Three states, and the one that matters is the third: the route
    sentence and a maps link are the fallback for every failure, so there
    is never a blank where the meeting point was — and, since SNOW-1214,
    never its latitude and longitude either.
    """

    def test_the_route_sentence_renders_with_the_flag_off(self, client: Client) -> None:
        """Flag off: the route sentence, no coordinate, and no call."""
        trip = TripFactory.create(meeting_point=_meeting_point())
        client.force_login(trip.created_by)

        with patch("apps.locations.services.what3words.requests.get") as mock_get:
            html = client.get(
                reverse("trips:detail", args=[trip.uuid])
            ).content.decode()

        dd = _meeting_point_element(html)
        assert "6.7 km west of the route&#x27;s start" in dd
        # Only in the maps link's href, as %2C-encoded query, never shown.
        assert "46.080012, 7.318197" not in dd
        assert "///" not in dd
        mock_get.assert_not_called()

    @override_flag("what3words", active=True)
    def test_the_address_renders_with_the_flag_on(self, client: Client) -> None:
        """A cached address replaces the pair, prefixed with ///."""
        trip = TripFactory.create(meeting_point=_meeting_point(cached=True))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert "///filled.count.soap" in _meeting_point_element(html)

    @override_flag("what3words", active=True)
    def test_the_coordinate_is_not_even_a_title(self, client: Client) -> None:
        """A meeting point never shows its coordinate, hover included."""
        trip = TripFactory.create(meeting_point=_meeting_point(cached=True))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert "46.080012" not in _meeting_point_element(html)

    @override_flag("what3words", active=True)
    def test_the_edit_form_looks_the_address_up_too(self, client: Client) -> None:
        """The organiser's edit picker is the fourth render path.

        ``_trip_context`` builds it, so the flag has to reach it from
        there rather than from ``_what3words_context`` — a trip page whose
        edit form said nothing while the new-trip form did would be two
        answers to one question.
        """
        trip = TripFactory.create(meeting_point=_meeting_point())
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-what3words-url="/trips/what3words/"' in html

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

    def test_the_fallback_links_to_a_maps_app_not_to_what3words(
        self, client: Client
    ) -> None:
        """No address means a maps link, and never a what3words link.

        ``meeting_point_w3w_url`` is None exactly when the address is, so
        no what3words href is built — a template that built it itself would
        emit ``.../None`` here. The coordinate travels in the maps link's
        href, which is how the group navigates to the spot (SNOW-1214).
        """
        trip = TripFactory.create(meeting_point=_meeting_point())
        client.force_login(trip.created_by)

        dd = _meeting_point_element(
            client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        )

        assert "what3words" not in dd
        assert "None" not in dd
        assert "query=46.080012%2C7.318197" in dd
        assert "Open in maps" in dd

    @override_flag("what3words", active=True)
    def test_a_failing_conversion_falls_back_to_the_route_sentence(
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
        dd = _meeting_point_element(response.content.decode())
        assert "6.7 km west of the route&#x27;s start" in dd
        # Only in the maps link's href, as %2C-encoded query, never shown.
        assert "46.080012, 7.318197" not in dd


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
        assert trip.date == _trip_day()

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
    def test_the_re_rendered_form_still_looks_the_address_up(
        self, client: Client
    ) -> None:
        """The flag reaches the error re-render, not just the first paint.

        This is the render path a missing ``_what3words_context`` would
        silently lose: the form comes back whole after a validation error,
        so an organiser who mistyped the date would watch the address
        line under their pin disappear.
        """
        route = RouteFactory.create()
        client.force_login(route.user)
        payload = _valid_post(str(route.uuid))
        payload["date"] = ""

        response = client.post(reverse("trips:create"), payload, **_HTMX)

        assert response.status_code == 400
        assert 'data-what3words-url="/trips/what3words/"' in response.content.decode()

    @freeze_time("2026-10-24T09:00:00+00:00")
    def test_a_day_already_gone_is_refused(self, client: Client) -> None:
        """SNOW-1211: a trip is planned for a day out, not for yesterday."""
        route = RouteFactory.create()
        client.force_login(route.user)
        payload = _valid_post(str(route.uuid))
        payload["date"] = "2026-10-23"
        response = client.post(reverse("trips:create"), payload, **_HTMX)
        assert response.status_code == 400
        assert "Pick today or a day after it." in response.content.decode()
        assert not Trip.objects.exists()

    @freeze_time("2026-10-24T09:00:00+00:00")
    def test_today_is_accepted(self, client: Client) -> None:
        """The floor is today itself — a same-day plan is a real plan."""
        route = RouteFactory.create()
        client.force_login(route.user)
        payload = _valid_post(str(route.uuid))
        payload["date"] = "2026-10-24"
        response = client.post(reverse("trips:create"), payload, **_HTMX)
        assert response.status_code == 200
        assert Trip.objects.get().date == datetime.date(2026, 10, 24)

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
    def test_the_re_rendered_form_still_looks_the_address_up(
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
        assert 'data-what3words-url="/trips/what3words/"' in response.content.decode()

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

    @freeze_time("2026-10-24T09:00:00+00:00")
    def test_a_past_trip_keeps_its_own_day(self, client: Client) -> None:
        """SNOW-1211: correcting the note on a trip already done is allowed.

        The floor on an edit is the earlier of today and the trip's own
        day, so a past trip is not forced into the future to save a change.
        """
        trip = TripFactory.create(date=datetime.date(2026, 10, 1))
        client.force_login(trip.created_by)
        payload = _valid_post()
        payload["date"] = "2026-10-01"
        payload["description"] = "It was great."
        response = client.post(
            reverse("trips:edit", args=[trip.uuid]), payload, **_HTMX
        )
        assert response.status_code == 200
        trip.refresh_from_db()
        assert trip.description == "It was great."

    @freeze_time("2026-10-24T09:00:00+00:00")
    def test_a_past_trip_cannot_move_earlier(self, client: Client) -> None:
        """The floor is the trip's own day, not no floor at all."""
        trip = TripFactory.create(date=datetime.date(2026, 10, 1))
        client.force_login(trip.created_by)
        payload = _valid_post()
        payload["date"] = "2026-09-30"
        response = client.post(
            reverse("trips:edit", args=[trip.uuid]), payload, **_HTMX
        )
        assert response.status_code == 400
        assert "Pick today or a day after it." in response.content.decode()

    @freeze_time("2026-10-24T09:00:00+00:00")
    def test_a_future_trip_cannot_move_into_the_past(self, client: Client) -> None:
        """For a trip still ahead, the floor on an edit is today."""
        trip = TripFactory.create(date=datetime.date(2026, 11, 1))
        client.force_login(trip.created_by)
        payload = _valid_post()
        payload["date"] = "2026-10-23"
        response = client.post(
            reverse("trips:edit", args=[trip.uuid]), payload, **_HTMX
        )
        assert response.status_code == 400

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

    def test_the_model_figures_are_shown_when_heights_exist(
        self, client: Client
    ) -> None:
        """SNOW-1043: the figures line and the stats row quote the model.

        The drifting track records 200 m of climb and the terrain model
        40 m; the page's map payload sends 40, so both text surfaces on
        the page must say 40 as well.
        """
        trip = TripFactory.create(
            points=DRIFTING_TRACK,
            distance_m=111.2,
            ascent_m=200.0,
            descent_m=0.0,
            slope_samples=drifting_track_record(),
        )
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        stat = re.search(r'data-testid="trip-stat-ascent"[^>]*>(.*?)</p>', html, re.S)

        assert "0.1km · 40m ↑ · 0m ↓" in html
        assert stat is not None
        assert stat.group(1).strip() == "40m"
        assert "200m" not in html

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


@pytest.mark.django_db
class TestMeetingPointAddressLookup:
    """The live address under the Plan page's pin (SNOW-1214)."""

    url = "/trips/what3words/"

    def test_the_url_is_where_the_picker_is_told_to_look(self) -> None:
        """The picker reads this path from the page, so it must not drift."""
        assert reverse("trips:what3words") == self.url

    @override_flag("what3words", active=True)
    @override_settings(WHAT3WORDS_MAP_BASE_URL="https://w3w.example")
    def test_it_answers_the_words_and_their_link(self, client: Client) -> None:
        """A settled pin gets its address, and nothing is stored."""
        client.force_login(UserFactory.create())

        with patch(
            "apps.trips.views.convert_to_3wa", return_value="filled.count.soap"
        ) as convert:
            response = client.get(self.url, {"lat": "46.08", "lon": "7.31"})

        assert response.status_code == 200
        assert response.json() == {
            "words": "filled.count.soap",
            "url": "https://w3w.example/filled.count.soap",
        }
        convert.assert_called_once_with(46.08, 7.31)
        assert not Location.objects.filter(what3words="filled.count.soap").exists()

    @override_flag("what3words", active=True)
    def test_a_failed_conversion_answers_nulls(self, client: Client) -> None:
        """The page says the address comes on save; it never shows numbers."""
        client.force_login(UserFactory.create())

        with patch("apps.trips.views.convert_to_3wa", return_value=None):
            response = client.get(self.url, {"lat": "46.08", "lon": "7.31"})

        assert response.status_code == 200
        assert response.json() == {"words": None, "url": None}

    @override_flag("what3words", active=True)
    def test_anonymous_is_refused(self, client: Client) -> None:
        """Only an organiser places a pin, and an organiser is signed in."""
        response = client.get(self.url, {"lat": "46.08", "lon": "7.31"})

        assert response.status_code == 403

    def test_the_flag_off_is_a_404(self, client: Client) -> None:
        """With the flag off the page asks nothing, so nothing answers."""
        client.force_login(UserFactory.create())

        with patch("apps.trips.views.convert_to_3wa") as convert:
            response = client.get(self.url, {"lat": "46.08", "lon": "7.31"})

        assert response.status_code == 404
        convert.assert_not_called()

    @override_flag("what3words", active=True)
    @pytest.mark.parametrize(
        "query",
        [
            {},
            {"lat": "46.08"},
            {"lat": "north", "lon": "7.31"},
            {"lat": "nan", "lon": "7.31"},
            {"lat": "inf", "lon": "7.31"},
            {"lat": "91", "lon": "7.31"},
            {"lat": "46.08", "lon": "-181"},
        ],
    )
    def test_a_bad_coordinate_is_a_400(
        self, client: Client, query: dict[str, str]
    ) -> None:
        """Junk never reaches the upstream."""
        client.force_login(UserFactory.create())

        with patch("apps.trips.views.convert_to_3wa") as convert:
            response = client.get(self.url, query)

        assert response.status_code == 400
        convert.assert_not_called()

    @override_flag("what3words", active=True)
    def test_it_is_rate_limited(self, client: Client) -> None:
        """A script cannot walk the endpoint as a free geocoder."""
        client.force_login(UserFactory.create())

        with (
            patch("apps.trips.views.convert_to_3wa", return_value="a.b.c"),
            patch("django_ratelimit.decorators.is_ratelimited", return_value=True),
        ):
            response = client.get(self.url, {"lat": "46.08", "lon": "7.31"})

        assert response.status_code == 429


@pytest.mark.django_db
class TestMeetingPointPicker:
    """The picker's markup: a fixed pin, two quick picks, no coordinate."""

    def _html(self, client: Client) -> str:
        """Render the Plan page for a fresh route's owner."""
        route = RouteFactory.create()
        client.force_login(route.user)
        return client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

    def test_the_pin_is_fixed_over_the_map(self, client: Client) -> None:
        """The pin is an element over the frame's centre, not a marker."""
        html = self._html(client)

        assert "data-meeting-pin" in html
        assert 'id="trip-meeting-start"' in html
        assert 'id="trip-meeting-end"' in html

    def test_no_coordinate_readout_and_no_reset(self, client: Client) -> None:
        """The readout and the reset link went with the draggable marker."""
        html = self._html(client)

        assert "data-meeting-readout" not in html
        assert "data-meeting-reset" not in html
        assert "Reset to the route" not in html

    def test_the_place_sentences_reach_the_browser_translated(
        self, client: Client
    ) -> None:
        """The browser words the sentence from {% trans %}, not literals."""
        html = self._html(client)

        assert 'data-string="on_route"' in html
        assert ">On the route, {distance} in<" in html
        assert 'data-string="octant-NE">north-east<' in html
        assert "meeting_place_core.js" in html


_REGION_SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]],
    ],
}


@pytest.mark.django_db
@freeze_time("2026-10-24 10:00:00")
class TestPlanPage:
    """SNOW-1215: the Plan page — route card, When, Where, Details, footer."""

    def _page(self, client: Client, **route_kwargs: Any) -> str:
        route = RouteFactory.create(**route_kwargs)
        client.force_login(route.user)
        return client.get(f"{reverse('trips:new')}?route={route.uuid}").content.decode()

    def test_the_route_card_comes_first_with_its_figures(self, client: Client) -> None:
        """Name, meta line, the shared profile and Change, before the form."""
        html = self._page(client, name="Mont Fort", ascent_m=305.0, descent_m=1873.0)

        assert html.index('data-testid="trip-new-route"') < html.index('id="trip-form"')
        meta = re.search(
            r'data-testid="trip-new-route-meta">(.*?)</p>', html, re.DOTALL
        )
        assert meta is not None
        assert " ".join(meta.group(1).split()) == "2.5km · 305m ↑ · 1873m ↓ · 2h00m"
        assert "data-trip-route-profile" in html
        assert "data-route-rail-lane" in html
        assert f"{reverse('public:map')}?panel=routes" in html
        assert "Using your route" not in html

    def test_three_numbered_sections_in_order(self, client: Client) -> None:
        """When, Where you meet, Details — then the footer."""
        html = self._page(client)

        order = [
            html.index(f'data-testid="{testid}"')
            for testid in (
                "trip-form-when",
                "trip-form-where",
                "trip-form-details",
                "trip-form-footer",
            )
        ]
        assert order == sorted(order)
        assert "data-trip-readback-when" in html

    def test_seven_day_tiles_and_other_with_tomorrow_checked(
        self, client: Client
    ) -> None:
        """Each tile is a date radio; tomorrow is the default."""
        html = self._page(client)

        assert html.count('data-testid="trip-day"') == 7
        assert 'data-testid="trip-day-other"' in html
        assert re.search(r'name="date"\s+value="2026-10-25"\s+id="trip-day-1"', html)
        checked = re.findall(r'<input[^>]*name="date"[^>]*checked', html, re.DOTALL)
        assert len(checked) == 1
        assert 'value="2026-10-25"' in checked[0]
        # The native date field is the Other tile's picker, unnamed.
        assert 'type="date"' in html
        assert 'name="date" type="date"' not in html

    def test_a_rated_day_shows_its_level_and_an_unrated_one_nothing(
        self, client: Client
    ) -> None:
        """Only the days a bulletin rated carry a level; the caption names it."""
        region = MicroRegionFactory.create(
            name="Martigny-Verbier", boundary=_REGION_SQUARE
        )
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 10, 25),
            max_rating="considerable",
        )

        html = self._page(client)

        assert html.count('data-testid="trip-day-rating"') == 1
        assert 'data-level="considerable"' in html
        assert 'data-rating="Considerable (3)"' in html
        assert "Martigny-Verbier" in html

    def test_the_trip_name_is_a_placeholder_not_a_value(self, client: Client) -> None:
        """A trip left unnamed takes its route's name; the field says so."""
        html = self._page(client, name="Mont Fort")

        field = re.search(r'<input[^>]*name="name"[^>]*>', html, re.DOTALL)
        assert field is not None
        assert 'placeholder="Mont Fort"' in field.group(0)
        assert "value=" not in field.group(0)
        assert "e.g. Bring skins and a transceiver." in html

    def test_an_invalid_submission_keeps_the_week_and_the_placeholder(
        self, client: Client
    ) -> None:
        """The swapped-in form is the same form, tiles and all."""
        route = RouteFactory.create(name="Mont Fort")
        client.force_login(route.user)

        response = client.post(
            reverse("trips:create"),
            {
                "route": str(route.uuid),
                "date": "2026-11-12",
                "start_time": "",
                "latitude": "46.1",
                "longitude": "7.4",
            },
            HTTP_HX_REQUEST="true",
        )
        html = response.content.decode()

        assert response.status_code == 400
        assert html.count('data-testid="trip-day"') == 7
        assert re.search(r'value="2026-11-12"[^>]*data-day-other\s+checked', html)
        assert "12 Nov" in html
        assert 'placeholder="Mont Fort"' in html
