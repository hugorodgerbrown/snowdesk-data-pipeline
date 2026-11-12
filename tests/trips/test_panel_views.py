"""
tests/trips/test_panel_views.py — the Saved tab's Trips list (SNOW-1479).

Covers ``trips:panel_list``, ``trips:rename`` and ``trips:delete``'s panel
mode:
  - panel_list: 400 without HTMX, 403 for a visitor; scoped by
    participation; upcoming soonest first (today included), then past, most
    recent first;
  - an organiser's row has the "…" menu of Share, Rename, Delete in that
    order; a participant's row has no menu and links to the trip;
  - rename: 400 without HTMX, 403 for a visitor, 404 for another user's
    trip, and the row re-rendered with the new name;
  - delete with ``?panel=1``: an empty 200 with no ``HX-Redirect``.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.trips.models import Trip
from apps.trips.services.participants import join_trip
from tests.factories import TripFactory, UserFactory

_HTMX: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}
LIST_URL = "/trips/partials/list/"


def _menu_words(content: str, uuid: object) -> list[str]:
    """Return the visible words of a trip row's "…" menu items, in order."""
    menu = re.search(
        r'<ul\s+id="trip-actions-menu-%s"\s+role="menu".*?</ul>' % uuid, content, re.S
    )
    assert menu
    return [
        " ".join(re.sub(r"<[^>]+>", " ", item).split())
        for item in re.findall(
            r'<button\b[^>]*role="menuitem"[^>]*>(.*?)</button>', menu.group(0), re.S
        )
    ]


@pytest.mark.django_db
class TestTripPanelList:
    """GET /trips/partials/list/."""

    def test_htmx_only(self, client: Client) -> None:
        client.force_login(UserFactory.create())
        assert client.get(LIST_URL).status_code == 400

    def test_a_visitor_is_refused(self, client: Client) -> None:
        assert client.get(LIST_URL, **_HTMX).status_code == 403

    def test_reverses_to_its_url(self) -> None:
        assert reverse("trips:panel_list") == LIST_URL

    def test_scoped_by_participation(self, client: Client) -> None:
        reader = UserFactory.create()
        TripFactory.create(created_by=reader, name="Mine")
        joined = TripFactory.create(name="Joined")
        join_trip(reader, joined)
        TripFactory.create(name="Someone else's")
        client.force_login(reader)

        content = client.get(LIST_URL, **_HTMX).content.decode()

        assert "Mine" in content
        assert "Joined" in content
        assert "Someone else" not in content

    def test_upcoming_soonest_first_then_past_most_recent_first(
        self, client: Client
    ) -> None:
        reader = UserFactory.create()
        today = timezone.localdate()
        for name, days in (
            ("Later", 5),
            ("Yesterday", -1),
            ("Today", 0),
            ("Long ago", -30),
            ("Tomorrow", 1),
        ):
            TripFactory.create(
                created_by=reader,
                name=name,
                date=today + datetime.timedelta(days=days),
            )
        client.force_login(reader)

        content = client.get(LIST_URL, **_HTMX).content.decode()

        labels = [
            m.strip()
            for m in re.findall(
                r'data-testid="trip-panel-row-label"[^>]*>([^<]*)<', content
            )
        ]
        assert labels == ["Today", "Tomorrow", "Later", "Yesterday", "Long ago"]

    def test_an_organisers_row_has_share_rename_delete(self, client: Client) -> None:
        trip = TripFactory.create(name="Rosablanche")
        client.force_login(trip.created_by)

        content = client.get(LIST_URL, **_HTMX).content.decode()

        assert _menu_words(content, trip.uuid) == ["Share", "Rename", "Delete"]
        assert f'data-trip-panel-share="{trip.uuid}"' in content
        assert f'data-trip-rename="{trip.uuid}"' in content
        assert f'hx-post="/trips/partials/{trip.uuid}/delete/?panel=1"' in content
        assert "data-row-renameable" in content

    def test_a_participants_row_has_no_menu_and_opens_the_trip(
        self, client: Client
    ) -> None:
        trip = TripFactory.create(name="Mont Fort")
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        content = client.get(LIST_URL, **_HTMX).content.decode()

        assert "trip-actions-" not in content
        assert "data-row-renameable" not in content
        assert re.search(
            r'<a\s+href="%s"\s+data-row-label'
            % re.escape(reverse("trips:detail", kwargs={"uuid": trip.uuid})),
            content,
        )

    def test_no_trips_says_how_to_plan_one(self, client: Client) -> None:
        client.force_login(UserFactory.create())
        content = client.get(LIST_URL, **_HTMX).content.decode()
        assert 'data-testid="trip-panel-empty"' in content


@pytest.mark.django_db
class TestTripRename:
    """POST /trips/partials/<uuid>/rename/."""

    def test_htmx_only(self, client: Client) -> None:
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        response = client.post(
            reverse("trips:rename", args=[trip.uuid]), {"name": "New"}
        )
        assert response.status_code == 400

    def test_a_visitor_is_refused(self, client: Client) -> None:
        trip = TripFactory.create()
        response = client.post(
            reverse("trips:rename", args=[trip.uuid]), {"name": "New"}, **_HTMX
        )
        assert response.status_code == 403

    def test_404_for_another_users_trip(self, client: Client) -> None:
        trip = TripFactory.create(name="Theirs")
        joiner = UserFactory.create()
        join_trip(joiner, trip)
        client.force_login(joiner)

        response = client.post(
            reverse("trips:rename", args=[trip.uuid]), {"name": "Mine now"}, **_HTMX
        )

        assert response.status_code == 404
        trip.refresh_from_db()
        assert trip.name == "Theirs"

    def test_renames_and_answers_the_row(self, client: Client) -> None:
        trip = TripFactory.create(name="Old")
        client.force_login(trip.created_by)

        response = client.post(
            reverse("trips:rename", args=[trip.uuid]),
            {"name": "  Rosablanche  "},
            **_HTMX,
        )

        assert response.status_code == 200
        trip.refresh_from_db()
        assert trip.name == "Rosablanche"
        assert f'id="trip-{trip.uuid}"' in response.content.decode()

    def test_a_blank_name_falls_back_to_the_route(self, client: Client) -> None:
        trip = TripFactory.create(name="Old")
        client.force_login(trip.created_by)
        client.post(reverse("trips:rename", args=[trip.uuid]), {"name": ""}, **_HTMX)
        trip.refresh_from_db()
        assert trip.name == ""
        assert trip.display_name == trip.route_name


@pytest.mark.django_db
class TestTripDeletePanelMode:
    """POST /trips/partials/<uuid>/delete/?panel=1."""

    def test_empty_200_with_no_redirect(self, client: Client) -> None:
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.post(
            reverse("trips:delete", args=[trip.uuid]) + "?panel=1", **_HTMX
        )

        assert response.status_code == 200
        assert response.content == b""
        assert "HX-Redirect" not in response
        assert not Trip.objects.filter(pk=trip.pk).exists()

    def test_without_it_still_redirects(self, client: Client) -> None:
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        response = client.post(reverse("trips:delete", args=[trip.uuid]), **_HTMX)
        assert response["HX-Redirect"] == reverse("trips:list")
