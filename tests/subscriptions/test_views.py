"""
tests/subscriptions/test_views.py — Tests for subscriptions views.

Covers all view functions in subscriptions/views.py:
  - enter_email: GET renders form, POST sends magic link or re-renders errors.
  - email_sent: GET renders confirmation page.
  - verify_token: valid token creates/fetches subscriber and redirects;
    expired/missing token renders link_expired.
  - pick_regions: requires session; GET shows form, POST saves subscriptions.
  - manage_regions: requires session; GET shows current selections, POST updates.
  - confirmed: GET renders success page.
  - region_search_partial: HTMX-only; filters by ?q=.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from subscriptions.models import Subscriber, Subscription
from subscriptions.services.token import generate_magic_link_token
from tests.factories import RegionFactory, SubscriberFactory, SubscriptionFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_client(subscriber: Subscriber) -> Client:
    """Return a test client with subscriber_uuid set in the session."""
    client = Client()
    session = client.session
    session["subscriber_uuid"] = str(subscriber.uuid)
    session.save()
    return client


# ---------------------------------------------------------------------------
# enter_email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnterEmail:
    """Tests for the enter_email view."""

    def test_get_returns_200(self):
        """GET /subscribe/ renders the email form."""
        client = Client()
        response = client.get(reverse("subscriptions:enter_email"))
        assert response.status_code == 200
        assert b"Subscribe" in response.content

    def test_post_valid_email_redirects_to_sent(self, mailoutbox):
        """POST with a valid email sends a magic link and redirects."""
        client = Client()
        response = client.post(
            reverse("subscriptions:enter_email"),
            data={"email": "alice@example.com"},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:email_sent")
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["alice@example.com"]

    def test_post_invalid_email_rerenders_form_with_errors(self):
        """POST with an invalid email re-renders the form with validation errors."""
        client = Client()
        response = client.post(
            reverse("subscriptions:enter_email"),
            data={"email": "not-an-email"},
        )
        assert response.status_code == 200
        assert b"Enter a valid email address" in response.content

    def test_post_empty_email_rerenders_form_with_errors(self):
        """POST with no email shows required-field error."""
        client = Client()
        response = client.post(
            reverse("subscriptions:enter_email"),
            data={"email": ""},
        )
        assert response.status_code == 200
        assert b"This field is required" in response.content


# ---------------------------------------------------------------------------
# email_sent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmailSent:
    """Tests for the email_sent view."""

    def test_get_returns_200(self):
        """GET /subscribe/sent/ renders the inbox-check page."""
        client = Client()
        response = client.get(reverse("subscriptions:email_sent"))
        assert response.status_code == 200
        assert b"Check your inbox" in response.content


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVerifyToken:
    """Tests for the verify_token view."""

    def test_valid_token_new_subscriber_redirects_to_pick_regions(self):
        """A valid token for an unknown email creates a Subscriber and goes to pick_regions."""
        token = generate_magic_link_token(email="new@example.com")
        client = Client()
        response = client.get(
            reverse("subscriptions:verify_token"), data={"token": token}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:pick_regions")
        assert Subscriber.objects.filter(email="new@example.com").exists()

    def test_valid_token_returning_subscriber_with_subscriptions_redirects_to_manage(
        self,
    ):
        """A valid token for a subscriber who has regions goes to manage_regions."""
        subscriber = SubscriberFactory.create(email="returning@example.com")
        SubscriptionFactory.create(subscriber=subscriber, region=RegionFactory.create())
        token = generate_magic_link_token(email="returning@example.com")
        client = Client()
        response = client.get(
            reverse("subscriptions:verify_token"), data={"token": token}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:manage_regions")

    def test_valid_token_sets_session(self):
        """verify_token stores subscriber_uuid in the session."""
        token = generate_magic_link_token(email="session@example.com")
        client = Client()
        client.get(reverse("subscriptions:verify_token"), data={"token": token})
        subscriber = Subscriber.objects.get(email="session@example.com")
        assert client.session.get("subscriber_uuid") == str(subscriber.uuid)

    def test_valid_token_updates_last_authenticated_at(self):
        """verify_token sets last_authenticated_at on the subscriber."""
        token = generate_magic_link_token(email="ts@example.com")
        client = Client()
        client.get(reverse("subscriptions:verify_token"), data={"token": token})
        subscriber = Subscriber.objects.get(email="ts@example.com")
        assert subscriber.last_authenticated_at is not None
        assert subscriber.last_authenticated_at.tzinfo is not None

    def test_expired_token_renders_link_expired(self):
        """An expired token renders link_expired.html with status 400."""
        expired_time = datetime.now(tz=UTC) - timedelta(hours=1)
        with freeze_time(expired_time):
            token = generate_magic_link_token(email="expired@example.com")
        client = Client()
        response = client.get(
            reverse("subscriptions:verify_token"), data={"token": token}
        )
        assert response.status_code == 400
        assert b"expired" in response.content.lower()

    def test_missing_token_renders_link_expired(self):
        """No token query param renders link_expired.html with status 400."""
        client = Client()
        response = client.get(reverse("subscriptions:verify_token"))
        assert response.status_code == 400
        assert b"expired" in response.content.lower()

    def test_malformed_token_renders_link_expired(self):
        """A garbage token string renders link_expired.html with status 400."""
        client = Client()
        response = client.get(
            reverse("subscriptions:verify_token"), data={"token": "garbage.token.here"}
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# pick_regions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPickRegions:
    """Tests for the pick_regions view."""

    def test_get_without_session_redirects_to_enter_email(self):
        """GET without a session redirects to enter_email."""
        client = Client()
        response = client.get(reverse("subscriptions:pick_regions"))
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:enter_email")

    def test_get_with_session_returns_200(self):
        """GET with a valid session renders the region selection form."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:pick_regions"))
        assert response.status_code == 200
        assert b"Pick your regions" in response.content

    def test_post_with_regions_creates_subscriptions_and_redirects(self):
        """POST with selected regions creates Subscription records and redirects to confirmed."""
        subscriber = SubscriberFactory.create()
        region1 = RegionFactory.create()
        region2 = RegionFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(
            reverse("subscriptions:pick_regions"),
            data={"regions": [region1.pk, region2.pk]},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:confirmed")
        assert Subscription.objects.filter(subscriber=subscriber).count() == 2

    def test_post_with_no_regions_redirects_to_confirmed(self):
        """POST with no regions selected (empty form) redirects to confirmed."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(
            reverse("subscriptions:pick_regions"),
            data={},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:confirmed")


# ---------------------------------------------------------------------------
# manage_regions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManageRegions:
    """Tests for the manage_regions view."""

    def test_get_without_session_redirects_to_enter_email(self):
        """GET without a session redirects to enter_email."""
        client = Client()
        response = client.get(reverse("subscriptions:manage_regions"))
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:enter_email")

    def test_get_with_session_returns_200(self):
        """GET with a valid session renders the management form."""
        subscriber = SubscriberFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=RegionFactory.create())
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage_regions"))
        assert response.status_code == 200
        assert b"Manage your subscriptions" in response.content

    def test_post_updates_subscriptions_and_redirects(self):
        """POST replaces subscriptions with the new selection and redirects."""
        subscriber = SubscriberFactory.create()
        old_region = RegionFactory.create()
        new_region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=old_region)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse("subscriptions:manage_regions"),
            data={"regions": [new_region.pk]},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:confirmed")
        assert Subscription.objects.filter(
            subscriber=subscriber, region=new_region
        ).exists()
        assert not Subscription.objects.filter(
            subscriber=subscriber, region=old_region
        ).exists()

    def test_post_empty_clears_subscriptions(self):
        """POST with no regions removes all existing subscriptions."""
        subscriber = SubscriberFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=RegionFactory.create())
        client = _make_session_client(subscriber)
        response = client.post(
            reverse("subscriptions:manage_regions"),
            data={},
        )
        assert response.status_code == 302
        assert Subscription.objects.filter(subscriber=subscriber).count() == 0


# ---------------------------------------------------------------------------
# confirmed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConfirmed:
    """Tests for the confirmed view."""

    def test_get_returns_200(self):
        """GET /subscribe/confirmed/ renders the success page."""
        client = Client()
        response = client.get(reverse("subscriptions:confirmed"))
        assert response.status_code == 200
        assert b"all set" in response.content.lower()


# ---------------------------------------------------------------------------
# region_search_partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegionSearchPartial:
    """Tests for the region_search_partial HTMX view."""

    def test_htmx_get_with_query_returns_matching_regions(self):
        """HTMX GET with ?q= returns checkbox items for matching regions."""
        RegionFactory.create(region_id="CH-4115", name="Davos")
        RegionFactory.create(region_id="CH-4200", name="St. Moritz")
        client = Client()
        response = client.get(
            reverse("subscriptions:region_search_partial"),
            data={"q": "CH"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"CH-4115" in response.content
        assert b"CH-4200" in response.content

    def test_htmx_get_filters_by_name(self):
        """HTMX GET with ?q= filters by region name."""
        RegionFactory.create(region_id="CH-4115", name="Davos")
        RegionFactory.create(region_id="CH-4200", name="St. Moritz")
        client = Client()
        response = client.get(
            reverse("subscriptions:region_search_partial"),
            data={"q": "Davos"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"Davos" in response.content
        assert b"St. Moritz" not in response.content

    def test_htmx_get_empty_query_returns_all_regions(self):
        """HTMX GET with no ?q= returns all regions."""
        RegionFactory.create(region_id="CH-4115", name="Davos")
        RegionFactory.create(region_id="CH-4200", name="St. Moritz")
        client = Client()
        response = client.get(
            reverse("subscriptions:region_search_partial"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"CH-4115" in response.content
        assert b"CH-4200" in response.content

    def test_non_htmx_get_returns_400(self):
        """Non-HTMX GET to the partial endpoint returns 400."""
        client = Client()
        response = client.get(reverse("subscriptions:region_search_partial"))
        assert response.status_code == 400
