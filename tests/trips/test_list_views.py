"""
tests/trips/test_list_views.py — the trips list (SNOW-823).

trip_list (GET /trips/):
  scoped by PARTICIPATION — a joined trip is on it, an unrelated one is not,
    and a trip the reader organised but has no roster row on is not either;
  split against a FROZEN date: a trip dated today counts as upcoming,
    yesterday's is past;
  upcoming reads soonest-first and past most-recent-first;
  organising is neither a label nor a section since SNOW-848 — it is a
    Share control in the card's footer and nothing else;
  the empty state;
  anonymous redirects to sign-in;
  title and description are set (the page opts out of a share card);
  the create landing (SNOW-834) — ?created=<uuid> confirms the write and
    marks its row, and is ignored for a trip the reader is not on;
  the Share control on organised cards only, labelled for what the press
    will do;
  the card's own contents (SNOW-848) — the meeting point, the stat trio,
    the note, and the past section's rows.

Every date assertion runs under a frozen clock. "A trip dated today is
upcoming" is a claim about a boundary, and a suite that crossed midnight
mid-run would test the wrong side of it.
"""

from __future__ import annotations

import datetime
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from waffle.testutils import override_flag

from apps.trips.services.participants import join_trip
from apps.trips.services.shares import mint_trip_share
from tests.factories import (
    LocationFactory,
    RouteFactory,
    TripFactory,
    UserFactory,
)

# 14 March 2026 is a Saturday, which is the day a trip is normally on.
_TODAY = "2026-03-14T09:00:00+00:00"


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestTripListScope:
    """for_user, on the page."""

    def test_lists_a_trip_the_reader_organised(self, client: Client) -> None:
        """Their own roster row is written at creation."""
        trip = TripFactory.create(name="Rosablanche")
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert "Rosablanche" in html

    def test_lists_a_trip_the_reader_joined(self, client: Client) -> None:
        """A joined trip belongs on their agenda as much as an organised one."""
        trip = TripFactory.create(name="Mont Fort")
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        html = client.get(reverse("trips:list")).content.decode()

        assert "Mont Fort" in html

    def test_no_card_states_how_many_people_hold_the_trip(self, client: Client) -> None:
        """SNOW-848: nobody learns who else saved a trip, or how many did.

        This was ``test_the_row_counts_everyone_on_the_trip_not_just_the_
        reader``, guarding a real bug — ``for_user`` filters on
        ``participants__user`` and the row's count annotated over the SAME
        relation, so every trip read "1 person going" to everyone on it.
        The count is gone rather than fixed, so the test is inverted: the
        annotation must not come back with it.
        """
        trip = TripFactory.create(name="Rosablanche")
        join_trip(UserFactory.create(), trip)
        join_trip(UserFactory.create(), trip)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert "people going" not in html
        assert "person going" not in html

    def test_omits_an_unrelated_trip(self, client: Client) -> None:
        """Somebody else's trip is not on my agenda."""
        TripFactory.create(name="Not mine")
        client.force_login(UserFactory.create())

        html = client.get(reverse("trips:list")).content.decode()

        assert "Not mine" not in html
        assert 'data-testid="trips-empty"' in html

    def test_anonymous_is_sent_to_sign_in(self, client: Client) -> None:
        """A page, so a redirect rather than a 403."""
        response = client.get(reverse("trips:list"))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestTripListSplit:
    """Upcoming and past, against the trip's own date."""

    def test_a_trip_dated_today_is_upcoming(self, client: Client) -> None:
        """The day it exists for has not finished."""
        trip = TripFactory.create(
            name="This afternoon", date=datetime.date(2026, 3, 14)
        )
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trips-upcoming"' in html
        assert 'data-testid="trips-past"' not in html

    def test_yesterdays_trip_is_past(self, client: Client) -> None:
        """The boundary belongs to upcoming and nothing either side."""
        trip = TripFactory.create(name="Yesterday", date=datetime.date(2026, 3, 13))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trips-past"' in html
        assert 'data-testid="trips-upcoming"' not in html

    def test_upcoming_reads_soonest_first(self, client: Client) -> None:
        """An agenda reads forwards."""
        reader = UserFactory.create()
        TripFactory.create(
            created_by=reader, name="Far", date=datetime.date(2026, 4, 1)
        )
        TripFactory.create(
            created_by=reader, name="Near", date=datetime.date(2026, 3, 16)
        )
        client.force_login(reader)

        html = client.get(reverse("trips:list")).content.decode()

        assert html.index("Near") < html.index("Far")

    def test_past_reads_most_recent_first(self, client: Client) -> None:
        """A history reads backwards."""
        reader = UserFactory.create()
        TripFactory.create(
            created_by=reader, name="Long ago", date=datetime.date(2026, 1, 5)
        )
        TripFactory.create(
            created_by=reader, name="Last week", date=datetime.date(2026, 3, 7)
        )
        client.force_login(reader)

        html = client.get(reverse("trips:list")).content.decode()

        assert html.index("Last week") < html.index("Long ago")

    def test_both_sections_render_together(self, client: Client) -> None:
        """A reader with history and plans sees both."""
        reader = UserFactory.create()
        TripFactory.create(created_by=reader, date=datetime.date(2026, 3, 20))
        TripFactory.create(created_by=reader, date=datetime.date(2026, 3, 1))
        client.force_login(reader)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trips-upcoming"' in html
        assert 'data-testid="trips-past"' in html


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestOrganisingIsNotLabelled:
    """SNOW-848 removed the chip, and did not put a section in its place.

    "You organised this" was authorship stated on a card whose reader is
    the only person who sees it, on a page listing only their own trips —
    it told them something they already knew, and it was the last piece of
    the social framing the ticket removed. What organising still changes
    is which CONTROL the card carries: Share, in the footer's right slot.

    Splitting the list by authorship instead would file two trips on the
    same morning under different headings; the reader's question is "what
    am I doing", not "what did I write", which is why
    ``test_both_kinds_sit_in_one_date_ordered_list`` survives unchanged.
    """

    def test_an_organised_trip_carries_no_chip_but_does_carry_share(
        self, client: Client
    ) -> None:
        """Authorship shows up as a control, not as a label."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trip-list-organiser-chip"' not in html
        assert 'data-testid="trip-list-share"' in html

    def test_a_saved_trip_carries_neither(self, client: Client) -> None:
        """Minting is organiser-scoped, so a card for somebody else's trip
        gets no Share button rather than a disabled one.
        """
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trip-list-organiser-chip"' not in html
        assert 'data-testid="trip-list-share"' not in html

    def test_both_kinds_sit_in_one_date_ordered_list(self, client: Client) -> None:
        """Two trips on consecutive days read in date order, not by author."""
        reader = UserFactory.create()
        joined = TripFactory.create(name="Joined", date=datetime.date(2026, 3, 16))
        join_trip(reader, joined)
        TripFactory.create(
            created_by=reader, name="Organised", date=datetime.date(2026, 3, 18)
        )
        client.force_login(reader)

        html = client.get(reverse("trips:list")).content.decode()

        assert html.index("Joined") < html.index("Organised")


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestTripListMetadata:
    """The page sets its own title and description, and no share card."""

    def test_sets_a_title_and_description(self, client: Client) -> None:
        """It is not in tests/public/test_page_meta.py's dicts — that
        module's client is anonymous and this page redirects — so its two
        states are asserted here instead.
        """
        client.force_login(UserFactory.create())

        html = client.get(reverse("trips:list")).content.decode()

        assert "<title>Your trips" in html
        assert '<meta name="description" content="Every trip' in html

    def test_emits_no_share_card(self, client: Client) -> None:
        """One reader's own agenda; a preview of it means nothing."""
        client.force_login(UserFactory.create())

        html = client.get(reverse("trips:list")).content.decode()

        assert 'property="og:title"' not in html
        assert 'name="twitter:title"' not in html


@freeze_time(_TODAY)
@pytest.mark.django_db
def test_deleting_a_trip_returns_the_organiser_to_the_list(
    client: Client,
) -> None:
    """Where the thing they just removed used to be."""
    trip = TripFactory.create()
    client.force_login(trip.created_by)

    response = client.post(
        reverse("trips:delete", args=[trip.uuid]), HTTP_HX_REQUEST="true"
    )

    assert response["HX-Redirect"] == reverse("trips:list")


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestTripCreatedLanding:
    """?created=<uuid>, which trip_create redirects here with (SNOW-834)."""

    def test_confirms_the_write(self, client: Client) -> None:
        """The page says the trip was created, without waiting for JS.

        The whole reported defect was silence: the form appeared to reset
        and nothing anywhere said a trip existed.
        """
        trip = TripFactory.create(name="Rosablanche")
        client.force_login(trip.created_by)

        html = client.get(
            f"{reverse('trips:list')}?created={trip.uuid}"
        ).content.decode()

        assert "Trip created." in html
        # Rendered SHOWN, not hidden behind a class only JS removes.
        assert 'id="trip-toast-success"' in html
        toast = html.split('id="trip-toast-success"')[1].split(">")[0]
        assert "hidden" not in toast

    def test_marks_the_row_it_made(self, client: Client) -> None:
        """So the confirmation has something to point at on a full agenda."""
        trip = TripFactory.create()
        TripFactory.create(created_by=trip.created_by)
        client.force_login(trip.created_by)

        html = client.get(
            f"{reverse('trips:list')}?created={trip.uuid}"
        ).content.decode()

        assert html.count('data-testid="trip-list-row-created"') == 1

    def test_says_nothing_without_the_parameter(self, client: Client) -> None:
        """A plain visit to the list is not a confirmation of anything."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert "Trip created." not in html
        assert 'data-testid="trip-list-row-created"' not in html
        # The toast still RENDERS — trip_share.js writes a share outcome
        # through it — but hidden until something has been said.
        toast = html.split('id="trip-toast-success"')[1].split(">")[0]
        assert "hidden" in toast

    def test_ignores_a_trip_the_reader_is_not_on(self, client: Client) -> None:
        """The parameter is a hint from a URL, and a URL is typed by anyone.

        Confirming a stranger's trip would both lie to the reader and tell
        them that uuid names something real.
        """
        stranger_trip = TripFactory.create()
        reader = UserFactory.create()
        client.force_login(reader)

        html = client.get(
            f"{reverse('trips:list')}?created={stranger_trip.uuid}"
        ).content.decode()

        assert "Trip created." not in html

    def test_ignores_a_malformed_uuid(self, client: Client) -> None:
        """A junk parameter is not a 500."""
        client.force_login(UserFactory.create())

        response = client.get(f"{reverse('trips:list')}?created=not-a-uuid")

        assert response.status_code == 200
        assert "Trip created." not in response.content.decode()


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestTripListShareControl:
    """The Share button on a row — the reason creating lands here."""

    def test_organised_rows_carry_it(self, client: Client) -> None:
        """With the machinery static/js/trip_share.js reads."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert f'data-trip-share="{trip.uuid}"' in html
        assert "data-trip-share-url=" in html
        assert "trip_share.js" in html
        # The mint is a POST, so the page has to carry a token.
        assert 'name="csrfmiddlewaretoken"' in html

    def test_a_joined_trip_carries_none(self, client: Client) -> None:
        """Minting is organiser-scoped, so the control would only 404."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        html = client.get(reverse("trips:list")).content.decode()

        assert f'data-trip-share="{trip.uuid}"' not in html

    def test_the_label_says_which_press_this_is(self, client: Client) -> None:
        """A second press ROTATES, which stops a link already sent working."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        before = client.get(reverse("trips:list")).content.decode()
        mint_trip_share(trip.created_by, trip.uuid)
        after = client.get(reverse("trips:list")).content.decode()

        assert "Share again" not in before
        assert "Share again" in after
        assert "stops working" in after


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestTripCardContents:
    """What SNOW-848's card carries that the row it replaced did not.

    The row gave a day, a name and a distance. A person deciding which of
    three Saturdays to commit to needs the size of the day and where the
    group meets, and had to open each trip to get either.
    """

    def test_the_card_carries_the_meeting_point_and_the_figures(
        self, client: Client
    ) -> None:
        """The whole answer, without opening the trip."""
        trip = TripFactory.create(distance_m=12400.0, ascent_m=850.0, descent_m=1100.0)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trip-card-meeting-point"' in html
        assert 'data-testid="trip-stat-distance"' in html
        assert 'data-testid="trip-stat-ascent"' in html
        assert 'data-testid="trip-stat-descent"' in html

    def test_a_track_with_no_elevation_drops_those_two_cells(
        self, client: Client
    ) -> None:
        """Null is "the source route carried no <ele>", which is not flat.

        Rendering "0 m" for it would be a safety-relevant lie about
        terrain somebody is planning to ski, and an empty labelled cell
        would claim the figure is missing rather than unknown. The cell
        goes; the asymmetry is honest.
        """
        trip = TripFactory.create(ascent_m=None, descent_m=None)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trip-stat-distance"' in html
        assert 'data-testid="trip-stat-ascent"' not in html
        assert 'data-testid="trip-stat-descent"' not in html

    def test_the_note_is_shown_when_there_is_one_and_omitted_when_not(
        self, client: Client
    ) -> None:
        """An empty block is a claim that something is missing."""
        reader = UserFactory.create()
        TripFactory.create(
            created_by=reader,
            name="With a note",
            description="Skins on from the lift.",
        )
        client.force_login(reader)

        with_note = client.get(reverse("trips:list")).content.decode()
        assert with_note.count('data-testid="trip-card-notes"') == 1
        assert "Skins on from the lift." in with_note

        TripFactory.create(created_by=reader, name="Bare", description="")
        both = client.get(reverse("trips:list")).content.decode()
        assert both.count('data-testid="trip-card-notes"') == 1

    def test_the_note_is_escaped_and_never_marked_safe(self, client: Client) -> None:
        """Invariant 1 — the description is user-supplied prose."""
        trip = TripFactory.create(description="<script>alert(1)</script>")
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_a_past_trip_is_a_row_and_not_a_card(self, client: Client) -> None:
        """A history is not a plan: nobody reads a meeting point off a day
        that has been.
        """
        trip = TripFactory.create(date=datetime.date(2026, 3, 1))
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trip-past-row"' in html
        assert 'data-testid="trip-card-meeting-point"' not in html

    def test_the_new_trip_link_points_at_the_routes_panel(self, client: Client) -> None:
        """And not at the authoring form, which 404s without a ``?route=``.

        A trip is planned FROM a route, and the routes panel is the one
        surface listing the reader's own.
        """
        client.force_login(UserFactory.create())

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trips-new-link"' in html
        assert f'href="{reverse("public:map")}?panel=routes"' in html


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestTripListMeetingAddress:
    """The list reads the what3words CACHE and never converts.

    ``convert-to-3wa`` is billed per call against a 1,000-a-month
    allowance and takes an HTTP round trip with a 5-second timeout. A list
    page that converted per card would spend an agenda's worth of the
    allowance on one render — and, worse, would take that timeout once per
    uncached trip while the reader waited.

    The conversion happens at the WRITE instead: ``trip_create`` and
    ``trip_edit`` both call ``_fill_meeting_address``, so the cached case
    is the normal one and the fallback below is what a trip planned more
    than thirty days out falls back TO.
    """

    @override_flag("what3words", active=True)
    def test_a_cached_address_reaches_the_card(self, client: Client) -> None:
        """The common case: ``trip_create`` spends the conversion, and the
        licence lets the answer stand for 30 days — which is longer than
        most of a trip's life.
        """
        trip = TripFactory.create(
            meeting_point=LocationFactory.create(
                anonymous=True,
                latitude=46.080012,
                longitude=7.318197,
                what3words="filled.count.soap",
                what3words_fetched_at=timezone.now(),
            )
        )
        client.force_login(trip.created_by)

        with mock.patch("apps.locations.services.what3words.requests.get") as mock_get:
            html = client.get(reverse("trips:list")).content.decode()

        mock_get.assert_not_called()
        assert "///filled.count.soap" in html

    @override_flag("what3words", active=True)
    def test_an_uncached_meeting_point_falls_back_to_the_coordinate(
        self, client: Client
    ) -> None:
        """There is never a blank where the meeting point was — and, the
        point of the test, never a conversion either.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        with mock.patch("apps.locations.services.what3words.requests.get") as mock_get:
            html = client.get(reverse("trips:list")).content.decode()

        mock_get.assert_not_called()
        assert 'data-testid="trip-card-meeting-point"' in html
        assert "///" not in html


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestCreatingATripSpendsTheConversion:
    """The write is where a three word address is resolved.

    Most of a trip's life happens within thirty days of it being created —
    planned, shared, saved, skied — and thirty days is exactly how long
    what3words' licence lets a converted address be held. Filling the
    cache at creation therefore makes "the list has the address" the
    normal case rather than the lucky one, and costs the same single
    conversion the read path would have spent.
    """

    @override_flag("what3words", active=True)
    def test_the_new_trip_lands_on_a_list_that_already_has_the_address(
        self, client: Client
    ) -> None:
        """One conversion at the write; none at the read after it."""
        route = RouteFactory.create()
        client.force_login(route.user)

        with mock.patch(
            "apps.locations.services.what3words.convert_to_3wa",
            return_value="filled.count.soap",
        ) as convert:
            client.post(
                reverse("trips:create"),
                {
                    "route": str(route.uuid),
                    "date": "2026-03-21",
                    "start_time": "07:30",
                    "name": "Rosablanche",
                    "description": "",
                    "latitude": "46.1",
                    "longitude": "7.4",
                },
                HTTP_HX_REQUEST="true",
            )
            assert convert.call_count == 1
            html = client.get(reverse("trips:list")).content.decode()
            assert convert.call_count == 1

        assert "///filled.count.soap" in html

    def test_the_flag_being_off_makes_no_call_at_all(self, client: Client) -> None:
        """Not a call that 401s — no call. The flag gates the spend."""
        route = RouteFactory.create()
        client.force_login(route.user)

        with mock.patch("apps.locations.services.what3words.convert_to_3wa") as convert:
            client.post(
                reverse("trips:create"),
                {
                    "route": str(route.uuid),
                    "date": "2026-03-21",
                    "start_time": "07:30",
                    "name": "Rosablanche",
                    "description": "",
                    "latitude": "46.1",
                    "longitude": "7.4",
                },
                HTTP_HX_REQUEST="true",
            )

        convert.assert_not_called()

    @override_flag("what3words", active=True)
    def test_moving_the_pin_refills_the_cache_the_move_cleared(
        self, client: Client
    ) -> None:
        """``update_trip`` nulls the address when the coordinate changes —
        it named the square the pin used to stand on — so an edited trip
        would drop back to a coordinate pair on the list for no reason.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        with mock.patch(
            "apps.locations.services.what3words.convert_to_3wa",
            return_value="moved.pin.here",
        ):
            client.post(
                reverse("trips:edit", args=[trip.uuid]),
                {
                    "date": "2026-03-21",
                    "start_time": "08:00",
                    "name": trip.name,
                    "description": "",
                    "latitude": "46.5",
                    "longitude": "7.9",
                },
                HTTP_HX_REQUEST="true",
            )

        trip.meeting_point.refresh_from_db()
        assert trip.meeting_point.what3words == "moved.pin.here"


@freeze_time(_TODAY)
@pytest.mark.django_db
class TestFigureSpelling:
    """One measurement, one spelling, on every surface that states it.

    The unit is closed up to its value and the thousands are not grouped
    (SNOW-830) — the same spelling ``route-distance`` / ``route-ascent`` /
    ``route-descent`` give the routes panel and the map popup, and the
    route card's own figures line gives the trip page. The design draws
    "1,340 m"; adopting that here alone would put two spellings of one
    number on one page.
    """

    def test_the_card_states_the_figures_the_way_every_other_surface_does(
        self, client: Client
    ) -> None:
        trip = TripFactory.create(distance_m=12400.0, ascent_m=1340.0, descent_m=1340.0)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert "12.4km" in html
        assert "1340m" in html
        assert "1,340" not in html
        assert "12.4 km" not in html
