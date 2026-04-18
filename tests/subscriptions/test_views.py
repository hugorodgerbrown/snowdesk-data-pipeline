"""
tests/subscriptions/test_views.py — Tests for subscriptions views.

Covers:
  subscribe_partial   — new/pending/active branches; byte-equal response;
                        rate-limit 429; HTMX-only.
  account_view        — valid token activates pending subscriber; idempotent
                        on re-click; bad/expired token → 400.
  manage_view         — unauthenticated GET/POST; authenticated GET/POST;
                        hard-delete on empty selection; rate-limit 429.
  unsubscribe_view    — valid token GET/POST; idempotent; bad token → 400;
                        last-subscription hard-delete; rate-limit 429.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client, RequestFactory
from django.urls import reverse
from freezegun import freeze_time

from subscriptions.models import Subscriber, Subscription
from subscriptions.services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_token,
    generate_unsubscribe_token,
)
from tests.factories import RegionFactory, SubscriberFactory, SubscriptionFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTMX_HEADERS = {"HTTP_HX_REQUEST": "true"}


def _make_session_client(subscriber: Subscriber) -> Client:
    """Return a test client with subscriber_uuid set in the session."""
    client = Client()
    session = client.session
    session["subscriber_uuid"] = str(subscriber.uuid)
    session.save()
    return client


def _valid_account_token(email: str) -> str:
    """Generate a fresh, valid account-access token."""
    return generate_token(email, salt=SALT_ACCOUNT_ACCESS)


# ---------------------------------------------------------------------------
# subscribe_partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscribePartial:
    """Tests for the subscribe_partial HTMX view."""

    @pytest.fixture(autouse=True)
    def patch_email(self):
        """Prevent real email dispatch during all subscribe_partial tests."""
        import subscriptions.views  # ensure the module is imported before patching  # noqa: F401

        with (
            patch("subscriptions.views.send_account_access_email") as mock_send,
            patch("subscriptions.views.send_noop_email") as mock_noop,
        ):
            self.mock_send = mock_send
            self.mock_noop = mock_noop
            yield

    def test_non_htmx_post_returns_400(self):
        client = Client()
        region = RegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "alice@example.com", "region_id": region.region_id},
        )
        assert response.status_code == 400

    def test_get_returns_405(self):
        client = Client()
        response = client.get(reverse("subscriptions:subscribe"), **_HTMX_HEADERS)
        assert response.status_code == 405

    def test_new_subscriber_creates_pending_record(self):
        client = Client()
        region = RegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert Subscriber.objects.filter(email="newuser@example.com").exists()
        sub = Subscriber.objects.get(email="newuser@example.com")
        assert sub.status == Subscriber.Status.PENDING

    def test_new_subscriber_sends_account_access_email(self):
        client = Client()
        region = RegionFactory.create()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        self.mock_send.assert_called_once()

    def test_pending_subscriber_sends_account_access_email(self):
        SubscriberFactory.create(
            email="pending@example.com", status=Subscriber.Status.PENDING
        )
        client = Client()
        region = RegionFactory.create()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        self.mock_send.assert_called_once()

    def test_active_subscriber_sends_noop_email(self):
        SubscriberFactory.create(
            email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        client = Client()
        region = RegionFactory.create()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        self.mock_noop.assert_called_once()
        self.mock_send.assert_not_called()

    def test_response_body_byte_equal_across_all_three_branches(self):
        """All three subscribe branches must return the same HTML fragment."""
        region = RegionFactory.create()
        SubscriberFactory.create(
            email="pending@example.com", status=Subscriber.Status.PENDING
        )
        SubscriberFactory.create(
            email="active@example.com", status=Subscriber.Status.ACTIVE
        )

        client = Client()

        resp_new = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "brand-new@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        resp_pending = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        resp_active = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )

        assert resp_new.content == resp_pending.content == resp_active.content

    def test_invalid_email_returns_form_with_errors(self):
        client = Client()
        region = RegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "not-an-email", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert b"valid email" in response.content.lower()

    def test_rate_limit_returns_429(self):
        # Directly test by faking the `limited` flag that django-ratelimit sets.
        rf = RequestFactory()
        request = rf.post(
            reverse("subscriptions:subscribe"),
            data={"email": "x@example.com", "region_id": "CH-0001"},
        )
        request.htmx = True  # noqa: B010 — set on test request object
        request.limited = True  # noqa: B010 — set on test request object

        import subscriptions.views  # noqa: F401
        from subscriptions.views import subscribe_partial

        response = subscribe_partial(request)
        assert response.status_code == 429

    def test_subscribe_from_landing_creates_subscriber_without_regions(self):
        """POST without region_id (landing page) creates a Subscriber with no Subscription rows."""
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "landing@example.com"},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        sub = Subscriber.objects.get(email="landing@example.com")
        assert sub.status == Subscriber.Status.PENDING
        assert not sub.subscriptions.exists()

    def test_subscribe_from_landing_success_fragment_byte_equal_to_bulletin(self):
        """Success fragment from the landing page is byte-equal to the bulletin-page success."""
        region = RegionFactory.create()
        client = Client()

        resp_landing = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "landing2@example.com"},
            **_HTMX_HEADERS,
        )
        resp_bulletin = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "bulletin2@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert resp_landing.content == resp_bulletin.content


# ---------------------------------------------------------------------------
# account_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountView:
    """Tests for the account_view."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_valid_token_activates_pending_subscriber(self):
        SubscriberFactory.create(
            email="pending@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("pending@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 200
        sub = Subscriber.objects.get(email="pending@example.com")
        assert sub.status == Subscriber.Status.ACTIVE
        assert sub.confirmed_at is not None

    def test_valid_token_sets_confirmed_at_with_timezone(self):
        SubscriberFactory.create(
            email="tz@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("tz@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))
        sub = Subscriber.objects.get(email="tz@example.com")
        assert sub.confirmed_at is not None
        assert sub.confirmed_at.tzinfo is not None

    def test_valid_token_sets_session(self):
        SubscriberFactory.create(
            email="session@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("session@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))
        sub = Subscriber.objects.get(email="session@example.com")
        assert client.session.get("subscriber_uuid") == str(sub.uuid)

    def test_idempotent_on_re_click_does_not_re_stamp_confirmed_at(self):
        """Re-clicking the same link must not update confirmed_at."""
        sub = SubscriberFactory.create(
            email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        sub.confirmed_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        sub.save(update_fields=["confirmed_at"])

        token = _valid_account_token("active@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))

        sub.refresh_from_db()
        assert sub.confirmed_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_expired_token_returns_400(self):
        with freeze_time("2026-01-01T00:00:00Z"):
            token = _valid_account_token("expired@example.com")
        future = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(
            seconds=settings.ACCOUNT_TOKEN_MAX_AGE + 1
        )
        with freeze_time(future):
            client = Client()
            response = client.get(
                reverse("subscriptions:account", kwargs={"token": token})
            )
        assert response.status_code == 400
        assert b"expired" in response.content.lower()

    def test_garbage_token_returns_400(self):
        client = Client()
        response = client.get(
            reverse("subscriptions:account", kwargs={"token": "garbage-token"})
        )
        assert response.status_code == 400

    def test_valid_token_unknown_email_returns_400(self):
        """Token is valid but subscriber does not exist — treat as expired."""
        token = _valid_account_token("ghost@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 400

    def test_renders_account_template(self):
        SubscriberFactory.create(
            email="render@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("render@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 200
        assert b"account" in response.content.lower()

    def test_unsubscribe_token_at_account_endpoint_returns_400(self):
        """An unsubscribe token must not be accepted by the account endpoint."""
        token = generate_unsubscribe_token("ghost@example.com", "CH-4115")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# manage_view (unauthenticated)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManageViewUnauthenticated:
    """Tests for manage_view without a session."""

    @pytest.fixture(autouse=True)
    def patch_email(self):
        import subscriptions.views  # ensure the module is imported before patching  # noqa: F401

        with (
            patch("subscriptions.views.send_account_access_email") as mock_send,
            patch("subscriptions.views.send_noop_email") as mock_noop,
        ):
            self.mock_send = mock_send
            self.mock_noop = mock_noop
            yield

    def test_get_returns_200_with_email_form(self):
        client = Client()
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert b"email" in response.content.lower()

    def test_post_known_email_sends_account_access_email(self):
        SubscriberFactory.create(email="known@example.com")
        client = Client()
        response = client.post(
            reverse("subscriptions:manage"),
            data={"email": "known@example.com"},
        )
        assert response.status_code == 200
        self.mock_send.assert_called_once()

    def test_post_unknown_email_sends_noop_email(self):
        client = Client()
        response = client.post(
            reverse("subscriptions:manage"),
            data={"email": "unknown@example.com"},
        )
        assert response.status_code == 200
        self.mock_noop.assert_called_once()
        self.mock_send.assert_not_called()

    def test_post_known_email_response_identical_to_unknown(self):
        """Responses must be byte-equal to prevent account enumeration."""
        SubscriberFactory.create(email="exists@example.com")
        client = Client()
        resp_known = client.post(
            reverse("subscriptions:manage"),
            data={"email": "exists@example.com"},
        )
        resp_unknown = client.post(
            reverse("subscriptions:manage"),
            data={"email": "nosuchuser@example.com"},
        )
        assert resp_known.content == resp_unknown.content

    def test_post_invalid_email_rerenders_form(self):
        client = Client()
        response = client.post(
            reverse("subscriptions:manage"),
            data={"email": "not-valid"},
        )
        assert response.status_code == 200
        assert b"valid email" in response.content.lower()

    def test_rate_limit_returns_429(self):
        """Exceeding rate limit on the unauthenticated manage POST returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("subscriptions:manage"),
            data={"email": "rl@example.com"},
        )

        from subscriptions.views import _manage_unauthenticated

        with patch(
            "subscriptions.views.get_usage",
            return_value={"should_limit": True},
        ):
            response = _manage_unauthenticated(request)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# manage_view (authenticated)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManageViewAuthenticated:
    """Tests for manage_view with a valid session."""

    def test_get_with_session_returns_region_form(self):
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert region.region_id.encode() in response.content

    def test_post_updates_subscriptions(self):
        subscriber = SubscriberFactory.create()
        old_region = RegionFactory.create()
        new_region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=old_region)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse("subscriptions:manage"),
            data={"regions": [new_region.pk]},
        )
        assert response.status_code == 200
        assert Subscription.objects.filter(
            subscriber=subscriber, region=new_region
        ).exists()
        assert not Subscription.objects.filter(
            subscriber=subscriber, region=old_region
        ).exists()

    def test_post_empty_selection_hard_deletes_subscriber(self):
        subscriber = SubscriberFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=RegionFactory.create())
        subscriber_pk = subscriber.pk
        client = _make_session_client(subscriber)
        response = client.post(
            reverse("subscriptions:manage"),
            data={},
        )
        assert response.status_code == 200
        assert not Subscriber.objects.filter(pk=subscriber_pk).exists()

    def test_post_empty_clears_session(self):
        subscriber = SubscriberFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=RegionFactory.create())
        client = _make_session_client(subscriber)
        client.post(reverse("subscriptions:manage"), data={})
        assert "subscriber_uuid" not in client.session

    def test_stale_session_uuid_returns_unauthenticated_view(self):
        """A session with a deleted subscriber UUID should show email entry form."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        subscriber.delete()
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        # Should render the unauthenticated form
        assert b"Send account link" in response.content


# ---------------------------------------------------------------------------
# unsubscribe_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsubscribeView:
    """Tests for the unsubscribe_view."""

    def test_get_valid_token_renders_confirmation(self):
        subscriber = SubscriberFactory.create(email="unsub@example.com")
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        token = generate_unsubscribe_token("unsub@example.com", region.region_id)
        client = Client()
        response = client.get(
            reverse("subscriptions:unsubscribe", kwargs={"token": token})
        )
        assert response.status_code == 200
        assert b"unsubscribe" in response.content.lower()

    def test_post_valid_token_removes_subscription(self):
        subscriber = SubscriberFactory.create(email="unsub2@example.com")
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        token = generate_unsubscribe_token("unsub2@example.com", region.region_id)
        client = Client()
        response = client.post(
            reverse("subscriptions:unsubscribe", kwargs={"token": token})
        )
        assert response.status_code == 200
        assert not Subscription.objects.filter(
            subscriber=subscriber, region=region
        ).exists()

    def test_post_last_subscription_hard_deletes_subscriber(self):
        subscriber = SubscriberFactory.create(email="lastregion@example.com")
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        token = generate_unsubscribe_token("lastregion@example.com", region.region_id)
        client = Client()
        client.post(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        assert not Subscriber.objects.filter(pk=sub_pk).exists()

    def test_post_not_last_subscription_keeps_subscriber(self):
        subscriber = SubscriberFactory.create(email="keep@example.com")
        region1 = RegionFactory.create()
        region2 = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region1)
        SubscriptionFactory.create(subscriber=subscriber, region=region2)
        token = generate_unsubscribe_token("keep@example.com", region1.region_id)
        client = Client()
        client.post(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        assert Subscriber.objects.filter(email="keep@example.com").exists()
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region2
        ).exists()

    def test_post_idempotent_when_already_deleted(self):
        """Re-submitting after subscriber is deleted renders done page, not error."""
        subscriber = SubscriberFactory.create(email="gone@example.com")
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        token = generate_unsubscribe_token("gone@example.com", region.region_id)
        subscriber.delete()
        client = Client()
        response = client.post(
            reverse("subscriptions:unsubscribe", kwargs={"token": token})
        )
        assert response.status_code == 200
        assert b"unsubscribed" in response.content.lower()

    def test_bad_token_returns_400(self):
        client = Client()
        response = client.get(
            reverse("subscriptions:unsubscribe", kwargs={"token": "garbage"})
        )
        assert response.status_code == 400

    def test_unsubscribe_token_does_not_expire(self):
        """Unsubscribe tokens must remain valid regardless of age."""
        subscriber = SubscriberFactory.create(email="old@example.com")
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)

        with freeze_time("2020-01-01T00:00:00Z"):
            token = generate_unsubscribe_token("old@example.com", region.region_id)

        with freeze_time("2025-06-01T00:00:00Z"):
            client = Client()
            response = client.get(
                reverse("subscriptions:unsubscribe", kwargs={"token": token})
            )
        assert response.status_code == 200

    def test_rate_limit_returns_429(self):
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        region = RegionFactory.create()
        token = generate_unsubscribe_token("rl@example.com", region.region_id)
        request = rf.get(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        request.limited = True  # noqa: B010 — set on test request object

        from subscriptions.views import unsubscribe_view

        response = unsubscribe_view(request, token=token)
        assert response.status_code == 429

    def test_cross_salt_token_returns_400(self):
        """An account-access token cannot be used as an unsubscribe token."""
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        client = Client()
        response = client.get(
            reverse("subscriptions:unsubscribe", kwargs={"token": token})
        )
        assert response.status_code == 400
