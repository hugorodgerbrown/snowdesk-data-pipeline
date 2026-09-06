"""
tests/trips/test_sign_in_next.py — the share page's sign-in CTA carries a
return trip (SNOW-825).

A recipient opens a trip link, finds they must sign in to save it, and —
before this ticket — came back from signing in on the map, with the trip
only reachable by going back to their messages and reopening the link. The
CTA now sends ``?next=`` pointing at the page the visitor is on, which
every sign-in path honours after validating it.

**IT WAS TWO CTAs UNTIL SNOW-848** — "Sign in to join this trip" and "Sign
in to save this route" — and the page now asks one question, so there is
one. ``test_the_page_asks_once`` is what was
``test_both_ctas_ask_and_neither_asks_twice``, and it still counts rather
than asserting no bare sign-in link exists: the nav carries one of its
own.

These tests assert the LINK, not the redirect: the redirect is
``tests/accounts/test_sign_in_next.py``'s subject, and asserting it here
would test the same view twice while leaving the thing that broke — a CTA
that forgets to ask — uncovered.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from apps.trips.services.shares import mint_trip_share
from tests.factories import TripFactory

# Well before TripFactory's default date, so a minted link is live.
_NOW = "2026-01-10T09:00:00+00:00"


def _shared_trip() -> Any:
    """Return a trip with a live share link, refreshed from the database."""
    trip = TripFactory.create()
    mint_trip_share(trip.created_by, trip.uuid)
    trip.refresh_from_db()
    return trip


@freeze_time(_NOW)
@pytest.mark.django_db
class TestShareCtasCarryNext:
    """GET /trips/s/<token>/ as an anonymous visitor."""

    def _page(self, client: Client) -> tuple[str, str]:
        """Return the rendered share page and the URL it was fetched from."""
        trip = _shared_trip()
        url = reverse("trips:share_page", args=[trip.share_token])
        return client.get(url).content.decode(), url

    def test_the_save_cta_points_back_at_the_trip(self, client: Client) -> None:
        html, url = self._page(client)
        expected = f'href="{reverse("accounts:sign_in")}?next={url}"'
        assert "Save this trip" in html
        assert expected in html

    def test_the_page_asks_once(self, client: Client) -> None:
        """One CTA, one return trip — and the nav's own bare sign-in link
        is left alone, which is why this counts the parameter rather than
        asserting no bare link exists on the page.
        """
        html, url = self._page(client)
        assert html.count(f"?next={url}") == 1
