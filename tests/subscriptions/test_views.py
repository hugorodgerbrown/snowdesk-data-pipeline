"""
tests/subscriptions/test_views.py — Tests for subscriptions views.

Covers:
  subscribe_partial   — four-case matrix (A=new, B=pending, C=active+new-region,
                        D=active+already-subscribed); rate-limit 429; HTMX-only;
                        missing region_id rejected (400 form error);
                        unknown region_id returns 400 error fragment.
  account_view        — valid token activates pending subscriber; redirects to
                        manage with ?just_confirmed=1; idempotent on re-click;
                        bad/expired token → 400.
  manage_view         — unauthenticated GET/POST (byte-equal response for known
                        and unknown emails); authenticated GET shows region cards;
                        non-subscribed regions absent; just_confirmed banner.
  remove_region       — removes one region; last region → hard-delete + HX-Redirect;
                        no session → 403; non-HTMX → 400; rate-limit 429.
  delete_account      — hard-deletes subscriber; clears session; HX-Redirect to done;
                        no session → 403; non-HTMX → 400.
  unsubscribe_view    — valid token GET/POST; idempotent; bad token → 400;
                        last-subscription hard-delete; rate-limit 429.
  unsubscribe_done_view — GET renders done page.
"""

import time
from datetime import UTC, datetime, timedelta
from statistics import median
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from freezegun import freeze_time

from subscriptions.models import Subscriber, Subscription
from subscriptions.services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_token,
    generate_unsubscribe_token,
)
from tests.factories import (
    RegionFactory,
    ResortFactory,
    SubscriberFactory,
    SubscriptionFactory,
)

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
    """Tests for the subscribe_partial HTMX view — four-case matrix."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings):
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_non_htmx_post_returns_400(self):
        """Non-HTMX POST is rejected with 400."""
        client = Client()
        region = RegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "alice@example.com", "region_id": region.region_id},
        )
        assert response.status_code == 400

    def test_get_returns_405(self):
        """GET on subscribe_partial is method-not-allowed."""
        client = Client()
        response = client.get(reverse("subscriptions:subscribe"), **_HTMX_HEADERS)
        assert response.status_code == 405

    def test_missing_region_id_returns_form_with_errors(self):
        """POST without region_id returns the form with validation errors."""
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "noregion@example.com"},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        # Form is re-rendered — no subscriber created
        assert not Subscriber.objects.filter(email="noregion@example.com").exists()

    def test_unknown_region_id_returns_400_error_fragment(self):
        """POST with a region_id that does not exist in the DB returns 400."""
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "alice@example.com", "region_id": "CH-NOTEXIST"},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 400
        assert b"went wrong" in response.content.lower()

    def test_invalid_email_returns_form_with_errors(self):
        """Invalid email address → form re-rendered with validation errors."""
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
        """Exceeding rate limit returns 429."""
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

    # ---- Case A: new subscriber ----

    def test_case_a_new_subscriber_creates_pending_record(self):
        """Case A: new email → Subscriber created with status=pending."""
        client = Client()
        region = RegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        sub = Subscriber.objects.get(email="newuser@example.com")
        assert sub.status == Subscriber.Status.PENDING

    def test_case_a_new_subscriber_creates_subscription_row(self):
        """Case A: new email + region → Subscription row created."""
        client = Client()
        region = RegionFactory.create()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newwithregion@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        sub = Subscriber.objects.get(email="newwithregion@example.com")
        assert Subscription.objects.filter(subscriber=sub, region=region).exists()

    def test_case_a_new_subscriber_sends_account_access_email(self):
        """Case A: new email → account-access email sent (subject contains 'Snowdesk')."""
        client = Client()
        region = RegionFactory.create()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "Snowdesk" in mail.outbox[0].subject
        assert "account" in mail.outbox[0].subject.lower()

    def test_case_a_response_contains_check_your_inbox(self):
        """Case A: response fragment contains 'Check your inbox'."""
        client = Client()
        region = RegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Check your inbox" in response.content

    # ---- Case B: existing pending subscriber ----

    def test_case_b_pending_creates_subscription_row(self):
        """Case B: existing pending + new region → Subscription row created."""
        subscriber = SubscriberFactory.create(
            email="pending@example.com", status=Subscriber.Status.PENDING
        )
        region = RegionFactory.create()
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region
        ).exists()

    def test_case_b_pending_sends_account_access_email(self):
        """Case B: existing pending subscriber → account-access email resent."""
        SubscriberFactory.create(
            email="pending@example.com", status=Subscriber.Status.PENDING
        )
        region = RegionFactory.create()
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "account" in mail.outbox[0].subject.lower()

    def test_case_b_response_contains_check_your_inbox(self):
        """Case B: response fragment contains 'Check your inbox'."""
        SubscriberFactory.create(
            email="pending@example.com", status=Subscriber.Status.PENDING
        )
        region = RegionFactory.create()
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Check your inbox" in response.content

    # ---- Case C: existing active subscriber, new region ----

    def test_case_c_active_new_region_creates_subscription_row(self):
        """Case C: active subscriber + new region → Subscription row created."""
        subscriber = SubscriberFactory.create(
            email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        region = RegionFactory.create()
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region
        ).exists()

    def test_case_c_active_new_region_sends_confirmation_email(self):
        """Case C: active subscriber + new region → subscription confirmation email sent."""
        SubscriberFactory.create(
            email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        region = RegionFactory.create(name="Davos Region")
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "Davos Region" in mail.outbox[0].subject

    def test_case_c_response_contains_added_and_region_name(self):
        """Case C: response fragment contains 'Added' and the region name."""
        SubscriberFactory.create(
            email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        region = RegionFactory.create(name="Davos Region")
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Added" in response.content
        assert b"Davos Region" in response.content

    # ---- Case D: existing active subscriber, already subscribed ----

    def test_case_d_already_subscribed_is_idempotent(self):
        """Case D: active subscriber already subscribed → no duplicate Subscription row."""
        subscriber = SubscriberFactory.create(
            email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active2@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert (
            Subscription.objects.filter(subscriber=subscriber, region=region).count()
            == 1
        )

    def test_case_d_already_subscribed_sends_no_email(self):
        """Case D: active subscriber already subscribed → no email sent."""
        subscriber = SubscriberFactory.create(
            email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active2@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 0

    def test_case_d_response_contains_already_subscribed_and_region_name(self):
        """Case D: response fragment contains 'already subscribed' and the region name."""
        subscriber = SubscriberFactory.create(
            email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        region = RegionFactory.create(name="Zermatt Region")
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active2@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"already subscribed" in response.content.lower()
        assert b"Zermatt Region" in response.content


# ---------------------------------------------------------------------------
# account_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountView:
    """Tests for the account_view."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings):
        """Use in-memory email backend to avoid real dispatch."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_valid_token_activates_pending_subscriber(self):
        """Pending subscriber is activated when a valid token is presented."""
        SubscriberFactory.create(
            email="pending@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("pending@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))
        sub = Subscriber.objects.get(email="pending@example.com")
        assert sub.status == Subscriber.Status.ACTIVE
        assert sub.confirmed_at is not None

    def test_valid_token_redirects_to_manage_with_just_confirmed(self):
        """Successful token click redirects to /subscribe/manage/?just_confirmed=1."""
        SubscriberFactory.create(
            email="redirect@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("redirect@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert response["Location"] == "/subscribe/manage/?just_confirmed=1"

    def test_valid_token_sets_confirmed_at_with_timezone(self):
        """confirmed_at timestamp has tzinfo set."""
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
        """Session is populated with subscriber_uuid after successful token click."""
        SubscriberFactory.create(
            email="session@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("session@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))
        sub = Subscriber.objects.get(email="session@example.com")
        assert client.session.get("subscriber_uuid") == str(sub.uuid)

    def test_idempotent_on_re_click_does_not_re_stamp_confirmed_at(self):
        """Re-clicking the same link for an already-active subscriber does not re-stamp confirmed_at."""
        sub = SubscriberFactory.create(
            email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        sub.confirmed_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        sub.save(update_fields=["confirmed_at"])

        token = _valid_account_token("active@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        # Still redirects, not an error
        assert response.status_code == 302

        sub.refresh_from_db()
        assert sub.confirmed_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_active_subscriber_re_click_also_redirects(self):
        """Active subscriber clicking the link again still gets redirected to manage."""
        sub = SubscriberFactory.create(
            email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        sub.confirmed_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        sub.save(update_fields=["confirmed_at"])
        token = _valid_account_token("active2@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert "/subscribe/manage/" in response["Location"]

    def test_expired_token_returns_400(self):
        """Expired token renders link_expired.html with status 400."""
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
        """Garbage token string returns 400."""
        client = Client()
        response = client.get(
            reverse("subscriptions:account", kwargs={"token": "garbage-token"})
        )
        assert response.status_code == 400

    def test_valid_token_unknown_email_returns_400(self):
        """Valid token for a deleted subscriber returns 400."""
        token = _valid_account_token("ghost@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 400

    def test_unsubscribe_token_at_account_endpoint_returns_400(self):
        """An unsubscribe token must not be accepted at the account endpoint."""
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
    def use_locmem_backend(self, settings):
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_get_returns_200_with_email_form(self):
        """Unauthenticated GET renders the email entry form."""
        client = Client()
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert b"email" in response.content.lower()

    def test_post_known_email_sends_account_access_email(self):
        """Known email on unauthenticated POST → account access email sent."""
        SubscriberFactory.create(email="known@example.com")
        client = Client()
        response = client.post(
            reverse("subscriptions:manage"),
            data={"email": "known@example.com"},
        )
        assert response.status_code == 200
        assert len(mail.outbox) == 1
        assert "Snowdesk" in mail.outbox[0].subject

    def test_post_unknown_email_sends_no_email(self):
        """Unknown email on unauthenticated POST → no email sent (no enumeration)."""
        client = Client()
        response = client.post(
            reverse("subscriptions:manage"),
            data={"email": "unknown@example.com"},
        )
        assert response.status_code == 200
        assert len(mail.outbox) == 0

    def test_post_known_email_response_identical_to_unknown(self):
        """Responses for known and unknown emails must be byte-equal."""
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
        # CSP nonces are request-scoped, so every response embeds a unique
        # random value — strip nonce="…" attributes before comparing, so
        # the email-enumeration check stays tight without falsely tripping
        # on the per-request CSP noise.
        import re

        nonce_re = re.compile(rb'\s?nonce="[^"]+"')
        assert nonce_re.sub(b"", resp_known.content) == nonce_re.sub(
            b"", resp_unknown.content
        )

    def test_post_invalid_email_rerenders_form(self):
        """Invalid email on unauthenticated POST re-renders the form with errors."""
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


@pytest.mark.django_db
class TestManagePostTimingSideChannel:
    """SNOW-26: known vs unknown email POST must not leak via response time."""

    @override_settings(
        SUBSCRIPTIONS_EMAIL_ASYNC=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_known_and_unknown_response_time_within_bound(self):
        """With async dispatch on, the known and unknown branches converge."""
        SubscriberFactory.create(email="known@example.com")
        client = Client()
        # Warm-up — first request pays template-cache and DB-connection cost.
        client.post(
            reverse("subscriptions:manage"),
            data={"email": "warm@example.com"},
        )

        n = 5
        known_times: list[float] = []
        unknown_times: list[float] = []
        for i in range(n):
            t0 = time.perf_counter()
            client.post(
                reverse("subscriptions:manage"),
                data={"email": "known@example.com"},
            )
            known_times.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            client.post(
                reverse("subscriptions:manage"),
                data={"email": f"u{i}@example.com"},
            )
            unknown_times.append(time.perf_counter() - t0)

        delta = abs(median(known_times) - median(unknown_times))
        assert delta < 0.050, (
            f"Timing delta {delta * 1000:.1f}ms exceeds 50ms bound "
            f"(known median {median(known_times) * 1000:.1f}ms, "
            f"unknown median {median(unknown_times) * 1000:.1f}ms)"
        )


# ---------------------------------------------------------------------------
# manage_view (authenticated)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManageViewAuthenticated:
    """Tests for manage_view with a valid session."""

    def test_get_shows_subscribed_region_name(self):
        """Authenticated GET shows the subscribed region's name."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create(name="Zermatt Region")
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert b"Zermatt Region" in response.content

    def test_get_shows_subscribed_region_id(self):
        """Authenticated GET shows the subscribed region's region_id."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert region.region_id.encode() in response.content

    def test_get_shows_resort_names_for_subscribed_region(self):
        """Authenticated GET lists resort names for subscribed regions."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        ResortFactory.create(region=region, name="Verbier")
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert b"Verbier" in response.content

    def test_get_does_not_show_non_subscribed_region(self):
        """Non-subscribed regions must not appear in the manage page."""
        subscriber = SubscriberFactory.create()
        subscribed_region = RegionFactory.create(name="Subscribed Region")
        RegionFactory.create(name="Other Region Zephyr")
        SubscriptionFactory.create(subscriber=subscriber, region=subscribed_region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert b"Other Region Zephyr" not in response.content

    def test_get_shows_welcome_banner_when_just_confirmed(self):
        """?just_confirmed=1 querystring renders the welcome banner."""
        subscriber = SubscriberFactory.create()
        RegionFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage") + "?just_confirmed=1")
        assert response.status_code == 200
        assert b"confirmed" in response.content.lower()

    def test_get_no_welcome_banner_without_just_confirmed(self):
        """Without ?just_confirmed the welcome banner is absent."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        # The banner contains a specific phrase; assert it's absent
        assert b"Your subscription is confirmed" not in response.content

    def test_stale_session_uuid_returns_unauthenticated_view(self):
        """A session with a deleted subscriber UUID should show email entry form."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        subscriber.delete()
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert b"Send account link" in response.content

    def test_get_shows_map_cta_link(self):
        """Authenticated manage page contains the 'Choose more regions on the map' link."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert b"map" in response.content.lower()
        assert b"/map/" in response.content


# ---------------------------------------------------------------------------
# remove_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveRegion:
    """Tests for the remove_region HTMX view."""

    def test_removes_subscription_row(self):
        """Session-authenticated POST removes the Subscription row."""
        subscriber = SubscriberFactory.create()
        region1 = RegionFactory.create()
        region2 = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region1)
        SubscriptionFactory.create(subscriber=subscriber, region=region2)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region", kwargs={"region_id": region1.region_id}
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert not Subscription.objects.filter(
            subscriber=subscriber, region=region1
        ).exists()
        # Other subscription retained
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region2
        ).exists()

    def test_last_region_hard_deletes_subscriber(self):
        """Removing the last region hard-deletes the subscriber."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        client = _make_session_client(subscriber)
        client.post(
            reverse(
                "subscriptions:remove_region", kwargs={"region_id": region.region_id}
            ),
            **_HTMX_HEADERS,
        )
        assert not Subscriber.objects.filter(pk=sub_pk).exists()

    def test_last_region_responds_with_hx_redirect(self):
        """Removing the last region responds with HX-Redirect header."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region", kwargs={"region_id": region.region_id}
            ),
            **_HTMX_HEADERS,
        )
        assert "HX-Redirect" in response
        assert "unsubscribe" in response["HX-Redirect"]

    def test_no_session_returns_403(self):
        """Unauthenticated POST returns 403."""
        region = RegionFactory.create()
        client = Client()
        response = client.post(
            reverse(
                "subscriptions:remove_region", kwargs={"region_id": region.region_id}
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self):
        """Non-HTMX POST returns 400."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region", kwargs={"region_id": region.region_id}
            ),
        )
        assert response.status_code == 400

    def test_rate_limit_returns_429(self):
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("subscriptions:remove_region", kwargs={"region_id": "CH-0001"}),
        )
        request.htmx = True  # noqa: B010
        request.limited = True  # noqa: B010

        from subscriptions.views import remove_region

        response = remove_region(request, region_id="CH-0001")
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# delete_account
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteAccount:
    """Tests for the delete_account HTMX view."""

    def test_hard_deletes_subscriber(self):
        """Session-authenticated POST hard-deletes the subscriber."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        client = _make_session_client(subscriber)
        client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert not Subscriber.objects.filter(pk=sub_pk).exists()

    def test_cascades_subscription_rows(self):
        """Subscriber deletion cascades to Subscription rows."""
        subscriber = SubscriberFactory.create()
        region = RegionFactory.create()
        sub = SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = sub.pk
        client = _make_session_client(subscriber)
        client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert not Subscription.objects.filter(pk=sub_pk).exists()

    def test_clears_session(self):
        """Session is cleared after account deletion."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert "subscriber_uuid" not in client.session

    def test_responds_with_hx_redirect(self):
        """Response includes HX-Redirect header pointing to unsubscribe-done."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 200
        assert "HX-Redirect" in response
        assert "unsubscribe" in response["HX-Redirect"]

    def test_no_session_returns_403(self):
        """Unauthenticated POST returns 403."""
        client = Client()
        response = client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 403

    def test_non_htmx_returns_400(self):
        """Non-HTMX POST returns 400."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(reverse("subscriptions:delete_account"))
        assert response.status_code == 400

    def test_rate_limit_returns_429(self):
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(reverse("subscriptions:delete_account"))
        request.htmx = True  # noqa: B010
        request.limited = True  # noqa: B010

        from subscriptions.views import delete_account

        response = delete_account(request)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# unsubscribe_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsubscribeView:
    """Tests for the unsubscribe_view."""

    def test_get_valid_token_renders_confirmation(self):
        """Valid token GET renders the unsubscribe confirmation page."""
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
        """Valid token POST deletes the matching Subscription row."""
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
        """Removing last subscription hard-deletes the Subscriber."""
        subscriber = SubscriberFactory.create(email="lastregion@example.com")
        region = RegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        token = generate_unsubscribe_token("lastregion@example.com", region.region_id)
        client = Client()
        client.post(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        assert not Subscriber.objects.filter(pk=sub_pk).exists()

    def test_post_not_last_subscription_keeps_subscriber(self):
        """Removing one of multiple subscriptions keeps the subscriber."""
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
        """Re-submitting after subscriber deletion renders done page without error."""
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
        """Garbage token returns 400."""
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
        request.limited = True  # noqa: B010

        from subscriptions.views import unsubscribe_view

        response = unsubscribe_view(request, token=token)
        assert response.status_code == 429

    def test_cross_salt_token_returns_400(self):
        """An account-access token must not be accepted at the unsubscribe endpoint."""
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        client = Client()
        response = client.get(
            reverse("subscriptions:unsubscribe", kwargs={"token": token})
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# unsubscribe_done_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsubscribeDoneView:
    """Tests for the standalone unsubscribe_done_view."""

    def test_get_renders_done_page(self):
        """GET /subscribe/unsubscribe-done/ renders the done page."""
        client = Client()
        response = client.get(reverse("subscriptions:unsubscribe_done"))
        assert response.status_code == 200
        assert b"unsubscribed" in response.content.lower()


# ---------------------------------------------------------------------------
# Email normalisation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmailNormalisation:
    """Tests for email normalisation at the form boundary.

    Verifies that case variants and whitespace are collapsed before the
    subscriber lookup so duplicate accounts cannot be created via case
    differences.
    """

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings):
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def _subscribe(self, email: str, region_id: str) -> None:
        """POST the subscribe_partial endpoint with HTMX headers."""
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": email, "region_id": region_id},
            HTTP_HX_REQUEST="true",
        )

    def test_uppercase_and_lowercase_same_address_creates_one_subscriber(self):
        """Two POSTs for the same address in different case create one Subscriber."""
        region = RegionFactory.create()
        self._subscribe("User@Example.com", region.region_id)
        self._subscribe("user@example.com", region.region_id)
        assert Subscriber.objects.filter(email="user@example.com").count() == 1
        assert Subscriber.objects.count() == 1

    def test_mixed_case_address_is_stored_lowercase(self):
        """The stored email address is the lowercase-normalised form."""
        region = RegionFactory.create()
        self._subscribe("ALICE@EXAMPLE.COM", region.region_id)
        assert Subscriber.objects.filter(email="alice@example.com").exists()

    def test_manage_view_post_looks_up_normalised_email(self):
        """manage_view POST for a mixed-case address finds the lowercase subscriber."""
        subscriber = SubscriberFactory.create(
            email="bob@example.com", status=Subscriber.Status.ACTIVE
        )
        client = Client()
        with patch("subscriptions.views.send_account_access_email") as mock_send:
            client.post(
                reverse("subscriptions:manage"),
                data={"email": "BOB@EXAMPLE.COM"},
            )
        mock_send.assert_called_once_with(
            subscriber.email, request=mock_send.call_args[1]["request"]
        )


class TestEmailFormNormalisation:
    """Unit tests for SubscribeForm and EmailForm clean_email."""

    def test_subscribe_form_lowercases_email(self):
        """SubscribeForm.clean_email returns a lowercased address."""
        from subscriptions.forms import SubscribeForm

        form = SubscribeForm(data={"email": "TEST@EXAMPLE.COM", "region_id": "CH-0001"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_subscribe_form_strips_whitespace(self):
        """SubscribeForm.clean_email strips leading and trailing whitespace."""
        from subscriptions.forms import SubscribeForm

        form = SubscribeForm(
            data={"email": "  user@example.com  ", "region_id": "CH-0001"}
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "user@example.com"

    def test_email_form_lowercases_email(self):
        """EmailForm.clean_email returns a lowercased address."""
        from subscriptions.forms import EmailForm

        form = EmailForm(data={"email": "TEST@EXAMPLE.COM"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_email_form_strips_whitespace(self):
        """EmailForm.clean_email strips leading and trailing whitespace."""
        from subscriptions.forms import EmailForm

        form = EmailForm(data={"email": "  user@example.com  "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "user@example.com"
