"""
tests/trips/test_share_views.py — Tests for the SNOW-821 sharing endpoints.

trip_share_create / trip_share_revoke (POST, JSON):
  the organiser gets an absolute share URL back;
  minting twice rotates, so the first URL stops working;
  403 anonymous, 404 for another user's trip, 429 past the rate limit;
  revoke nulls the token and is idempotent.

trip_share_page (GET/HEAD /trips/s/<token>/):
  200 for a live token, for an ANONYMOUS visitor — that is the point;
  404 for an unknown, revoked or expired token, all three identically;
  no organiser controls on it, whoever is looking;
  the organiser's note is escaped (invariant 1);
  Cache-Control: no-store;
  429 past the (token, IP) rate limit.

The rate-limit tests patch ``is_ratelimited`` rather than spending a real
budget: ``RATELIMIT_ENABLE`` is False under the development settings the
suite runs on, so a loop of requests would never trip anything. What is
worth asserting is the VIEW's branch — that a limited request answers 429
and does no work — and that is what the patch reaches. The same technique
``tests/routes/test_share_views.py`` uses.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from apps.trips.services.shares import mint_trip_share, revoke_trip_share
from tests.factories import TripFactory, UserFactory

# Well before TripFactory's default date, so a minted link is live.
_NOW = "2026-01-10T09:00:00+00:00"


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTripShareCreate:
    """POST /trips/<uuid>/share/."""

    def test_returns_an_absolute_url_for_the_organiser(self, client: Client) -> None:
        """The body goes straight to the native share sheet."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.post(reverse("trips:share_create", args=[trip.uuid]))

        assert response.status_code == 200
        trip.refresh_from_db()
        assert trip.share_token is not None
        assert response.json()["url"].endswith(f"/trips/s/{trip.share_token}/")

    def test_minting_twice_stops_the_first_link_working(self, client: Client) -> None:
        """Rotation is the organiser's only revoke-and-reshare."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        first = client.post(reverse("trips:share_create", args=[trip.uuid])).json()[
            "url"
        ]
        client.post(reverse("trips:share_create", args=[trip.uuid]))

        client.logout()
        assert client.get(first).status_code == 404

    def test_403_for_an_anonymous_request(self, client: Client) -> None:
        """Minting a link is the organiser's, and needs an account."""
        trip = TripFactory.create()
        response = client.post(reverse("trips:share_create", args=[trip.uuid]))
        assert response.status_code == 403

    def test_404_for_another_users_trip(self, client: Client) -> None:
        """Never 403 — no existence oracle."""
        trip = TripFactory.create()
        client.force_login(UserFactory.create())
        response = client.post(reverse("trips:share_create", args=[trip.uuid]))
        assert response.status_code == 404

    def test_the_rate_limited_branch_returns_429(self, client: Client) -> None:
        """django-ratelimit sets request.limited; the view answers 429."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        with (
            patch("apps.trips.views.mint_trip_share") as mint,
            patch("django_ratelimit.decorators.is_ratelimited", return_value=True),
        ):
            response = client.post(reverse("trips:share_create", args=[trip.uuid]))

        assert response.status_code == 429
        mint.assert_not_called()


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTripShareRevoke:
    """POST /trips/<uuid>/share/revoke/."""

    def test_nulls_the_token_and_kills_the_link(self, client: Client) -> None:
        """The link stops working immediately, for everyone holding it."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        url = client.post(reverse("trips:share_create", args=[trip.uuid])).json()["url"]

        response = client.post(reverse("trips:share_revoke", args=[trip.uuid]))

        assert response.status_code == 200
        assert response.json() == {"revoked": True}
        client.logout()
        assert client.get(url).status_code == 404

    def test_is_idempotent(self, client: Client) -> None:
        """Revoking an unshared trip is a 200, not an error."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        url = reverse("trips:share_revoke", args=[trip.uuid])

        assert client.post(url).status_code == 200
        assert client.post(url).status_code == 200

    def test_403_for_an_anonymous_request(self, client: Client) -> None:
        """A stranger cannot kill somebody's link."""
        trip = TripFactory.create()
        response = client.post(reverse("trips:share_revoke", args=[trip.uuid]))
        assert response.status_code == 403

    def test_404_for_another_users_trip(self, client: Client) -> None:
        """Organiser-scoped through the service's own lookup."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        client.force_login(UserFactory.create())

        assert (
            client.post(reverse("trips:share_revoke", args=[trip.uuid])).status_code
            == 404
        )
        trip.refresh_from_db()
        assert trip.share_is_live


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTripSharePage:
    """GET /trips/s/<token>/ — the page a recipient opens."""

    def test_200_for_an_anonymous_visitor_holding_a_live_link(
        self, client: Client
    ) -> None:
        """The whole point: they see what they were sent, before signing in."""
        trip = TripFactory.create(name="Rosablanche")
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        response = client.get(reverse("trips:share_page", args=[trip.share_token]))

        assert response.status_code == 200
        html = response.content.decode()
        assert "Rosablanche" in html
        assert 'data-testid="trip-summary"' in html
        assert 'id="trip-map-payload"' in html

    def test_404_for_unknown_revoked_and_expired_alike(self, client: Client) -> None:
        """One answer, so a guesser learns nothing from the difference.

        All three dead states, each reached the way it really is: a token
        that never existed, one whose trip has since been revoked, and one
        whose window has closed against the frozen clock.
        """
        revoked = TripFactory.create(
            share_token="revoked0001",
            share_expires_at=datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC),
        )
        revoke_trip_share(revoked.created_by, revoked.uuid)
        TripFactory.create(
            share_token="expired0001",
            share_expires_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        )

        for token in ("neverexisted", "revoked0001", "expired0001"):
            url = reverse("trips:share_page", args=[token])
            assert client.get(url).status_code == 404, token

    def test_carries_no_organiser_controls(self, client: Client) -> None:
        """Even for the organiser: this is what their friends see."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()
        client.force_login(trip.created_by)

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert 'data-testid="trip-organiser-controls"' not in html
        assert 'data-testid="trip-share-controls"' not in html

    def test_the_organisers_note_is_escaped(self, client: Client) -> None:
        """Invariant 1 — no mark_safe on user-supplied content."""
        trip = TripFactory.create(description="<script>alert(1)</script>")
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        html = client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_is_never_stored_by_an_intermediate_cache(self, client: Client) -> None:
        """The page varies with who is signed in, so it must not be shared."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        response = client.get(reverse("trips:share_page", args=[trip.share_token]))

        assert response["Cache-Control"] == "no-store"

    def test_the_rate_limited_branch_returns_429(self, client: Client) -> None:
        """30/h per (token, IP) — the token-guessing surface's bound.

        Answered BEFORE the lookup, so a scanner past its budget cannot
        even learn whether the token it guessed exists.
        """
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
            response = client.get(reverse("trips:share_page", args=[trip.share_token]))

        assert response.status_code == 429

    def test_head_is_allowed(self, client: Client) -> None:
        """Unfurlers HEAD a link before they GET it."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        response = client.head(reverse("trips:share_page", args=[trip.share_token]))

        assert response.status_code == 200


@freeze_time(_NOW)
@pytest.mark.django_db
class TestOrganiserShareControls:
    """The organiser's own page carries the Share pair."""

    def test_the_controls_are_on_the_object_page(self, client: Client) -> None:
        """Show controls, don't hide them: Share is always rendered."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-testid="trip-share-controls"' in html
        assert f'data-trip-share="{trip.uuid}"' in html

    def test_revoke_is_disabled_until_a_link_exists(self, client: Client) -> None:
        """Disabled rather than absent — a vanishing control reads as a bug."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert "disabled" in html

        mint_trip_share(trip.created_by, trip.uuid)
        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()

        assert 'data-trip-share-revoke="' in html


@freeze_time(_NOW)
@pytest.mark.django_db
class TestSharePageMetadata:
    """The card the link unfurls as, and the robots directive beside it."""

    def _html(self, client: Client) -> str:
        """Return the rendered share page for a freshly shared trip."""
        trip = TripFactory.create(name="Rosablanche", date=datetime.date(2026, 3, 14))
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()
        return client.get(
            reverse("trips:share_page", args=[trip.share_token])
        ).content.decode()

    def test_emits_the_full_sharing_set(self, client: Client) -> None:
        """Unfurling as a card in a message is the point of the link."""
        html = self._html(client)
        for tag in (
            '<meta property="og:title"',
            '<meta property="og:description"',
            '<meta name="twitter:title"',
            '<meta name="twitter:description"',
            '<meta property="og:url"',
        ):
            assert tag in html, tag

    def test_the_card_names_the_trip_and_its_day(self, client: Client) -> None:
        """A card reading "Snowdesk" tells a recipient nothing."""
        html = self._html(client)
        assert "Rosablanche" in html
        assert "14 March 2026" in html

    def test_is_noindex(self, client: Client) -> None:
        """An unguessable URL in a search result would defeat the token."""
        assert '<meta name="robots" content="noindex, nofollow">' in self._html(client)

    def test_the_object_page_is_not_noindex_by_accident(self, client: Client) -> None:
        """`noindex` is opt-in — an omitted parameter must not set it.

        The reason the partial tests ``noindex is True`` rather than
        truthiness: an unpassed variable resolves to the empty string, and
        a page that acquired a robots directive by accident is a page that
        silently left the index.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)
        html = client.get(reverse("trips:detail", args=[trip.uuid])).content.decode()
        assert 'name="robots"' not in html


@pytest.mark.django_db
def test_the_sitemap_lists_no_trip_url(client: Client) -> None:
    """Per-user and token-bearing pages stay out of the sitemap.

    Asserted rather than assumed because the exclusion is by omission —
    ``SITEMAPS`` lists sections rather than excluding routes — so nothing
    else would notice a trips section being added.
    """
    TripFactory.create()
    body = client.get(reverse("sitemap")).content.decode()
    assert "/trips/" not in body
