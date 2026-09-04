"""
tests/trips/test_list_views.py — the trips list (SNOW-823).

trip_list (GET /trips/):
  scoped by PARTICIPATION — a joined trip is on it, an unrelated one is not,
    and a trip the reader organised but has no roster row on is not either;
  split against a FROZEN date: a trip dated today counts as upcoming,
    yesterday's is past;
  upcoming reads soonest-first and past most-recent-first;
  organising is a LABEL on the row, not a separate section;
  the empty state;
  anonymous redirects to sign-in;
  title and description are set (the page opts out of a share card).

Every date assertion runs under a frozen clock. "A trip dated today is
upcoming" is a claim about a boundary, and a suite that crossed midnight
mid-run would test the wrong side of it.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from apps.trips.services.participants import join_trip
from tests.factories import TripFactory, UserFactory

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

    def test_the_row_counts_everyone_on_the_trip_not_just_the_reader(
        self, client: Client
    ) -> None:
        """The count is of the roster, not of the reader's own row.

        The regression: ``for_user`` used to filter on
        ``participants__user``, and the row's participant count annotates
        over that SAME relation — so the count saw only the rows the filter
        had left, which is always exactly one. A trip with three people on
        it read "1 person going" to every one of them, and nothing about
        the query looked wrong. Asserted from the reader's own page rather
        than from the queryset, because the page is where it was visible
        and a queryset test would have passed either way.
        """
        trip = TripFactory.create(name="Rosablanche")
        join_trip(UserFactory.create(), trip)
        join_trip(UserFactory.create(), trip)
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert "3 people going" in html

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
class TestOrganisingIsALabel:
    """Not a section — the reader's question is "what am I doing"."""

    def test_an_organised_trip_carries_the_chip(self, client: Client) -> None:
        """A label on the row, in date order with everything else."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trip-list-organiser-chip"' in html

    def test_a_joined_trip_does_not(self, client: Client) -> None:
        """The chip means "you wrote this", not "you are on it"."""
        trip = TripFactory.create()
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        html = client.get(reverse("trips:list")).content.decode()

        assert 'data-testid="trip-list-organiser-chip"' not in html

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
