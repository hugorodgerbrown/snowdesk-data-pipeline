"""
tests/subscriptions/test_views.py — Tests for subscriptions views.

Covers:
  subscribe_partial   — four-case matrix (A=new, B=pending, C=active+new-region,
  analytics events    — subscription_started, subscription_confirmed, region_added,
                        region_removed, unsubscribed (two sites).
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
  caplog regression   — plaintext emails never appear in log output; pk=/masked
                        forms appear instead; covers subscribe_partial, account_view,
                        sign_in_view POST, delete_account, and unsubscribe_view
                        hard-delete (SNOW-311).
"""

import time
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from freezegun import freeze_time
from pytest_django.fixtures import SettingsWrapper

from subscriptions.models import Subscriber, Subscription
from subscriptions.services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_token,
    generate_unsubscribe_token,
)
from tests.factories import (
    MicroRegionFactory,
    ResortFactory,
    SubscriberFactory,
    SubscriptionFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


_TOKEN_BACKEND = "subscriptions.backends.TokenBackend"


def _make_session_client(subscriber: Subscriber) -> Client:
    """Return a test client logged in as the subscriber's User via Django auth."""
    client = Client()
    client.force_login(subscriber.user, backend=_TOKEN_BACKEND)
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
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_non_htmx_post_returns_400(self) -> None:
        """Non-HTMX POST is rejected with 400."""
        client = Client()
        region = MicroRegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "alice@example.com", "region_id": region.region_id},
        )
        assert response.status_code == 400

    def test_get_returns_405(self) -> None:
        """GET on subscribe_partial is method-not-allowed."""
        client = Client()
        response = client.get(reverse("subscriptions:subscribe"), **_HTMX_HEADERS)
        assert response.status_code == 405

    def test_missing_region_id_returns_form_with_errors(self) -> None:
        """POST without region_id returns the form with validation errors."""
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "noregion@example.com"},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        # Form is re-rendered — no subscriber created
        assert not Subscriber.objects.filter(
            user__email="noregion@example.com"
        ).exists()

    def test_unknown_region_id_returns_400_error_fragment(self) -> None:
        """POST with a region_id that does not exist in the DB returns 400."""
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "alice@example.com", "region_id": "CH-NOTEXIST"},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 400
        assert b"went wrong" in response.content.lower()

    def test_invalid_email_returns_form_with_errors(self) -> None:
        """Invalid email address → form re-rendered with validation errors."""
        client = Client()
        region = MicroRegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "not-an-email", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert b"valid email" in response.content.lower()

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("subscriptions:subscribe"),
            data={"email": "x@example.com", "region_id": "CH-0001"},
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr added by middleware
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr added by middleware

        import subscriptions.views  # noqa: F401
        from subscriptions.views import subscribe_partial

        response = subscribe_partial(request)
        assert response.status_code == 429

    # ---- Case A: new subscriber ----

    def test_case_a_new_subscriber_creates_pending_record(self) -> None:
        """Case A: new email → Subscriber created with status=pending."""
        client = Client()
        region = MicroRegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        sub = Subscriber.objects.get(user__email="newuser@example.com")
        assert sub.status == Subscriber.Status.PENDING

    def test_case_a_new_subscriber_creates_subscription_row(self) -> None:
        """Case A: new email + region → Subscription row created."""
        client = Client()
        region = MicroRegionFactory.create()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newwithregion@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        sub = Subscriber.objects.get(user__email="newwithregion@example.com")
        assert Subscription.objects.filter(subscriber=sub, region=region).exists()

    def test_case_a_new_subscriber_sends_account_access_email(self) -> None:
        """Case A: new email → account-access email sent (subject contains 'Snowdesk')."""
        client = Client()
        region = MicroRegionFactory.create()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "Snowdesk" in mail.outbox[0].subject
        assert "account" in mail.outbox[0].subject.lower()

    def test_case_a_response_contains_check_your_inbox(self) -> None:
        """Case A: response fragment contains 'Check your inbox'."""
        client = Client()
        region = MicroRegionFactory.create()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Check your inbox" in response.content

    # ---- Case B: existing pending subscriber ----

    def test_case_b_pending_creates_subscription_row(self) -> None:
        """Case B: existing pending + new region → Subscription row created."""
        subscriber = SubscriberFactory.create(
            user__email="pending@example.com", status=Subscriber.Status.PENDING
        )
        region = MicroRegionFactory.create()
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region
        ).exists()

    def test_case_b_pending_sends_account_access_email(self) -> None:
        """Case B: existing pending subscriber → account-access email resent."""
        SubscriberFactory.create(
            user__email="pending@example.com", status=Subscriber.Status.PENDING
        )
        region = MicroRegionFactory.create()
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "account" in mail.outbox[0].subject.lower()

    def test_case_b_response_contains_check_your_inbox(self) -> None:
        """Case B: response fragment contains 'Check your inbox'."""
        SubscriberFactory.create(
            user__email="pending@example.com", status=Subscriber.Status.PENDING
        )
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Check your inbox" in response.content

    # ---- Case C: existing active subscriber, new region ----

    def test_case_c_active_new_region_creates_subscription_row(self) -> None:
        """Case C: active subscriber + new region → Subscription row created."""
        subscriber = SubscriberFactory.create(
            user__email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create()
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region
        ).exists()

    def test_case_c_active_new_region_sends_confirmation_email(self) -> None:
        """Case C: active subscriber + new region → subscription confirmation email sent."""
        SubscriberFactory.create(
            user__email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create(name="Davos Region")
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "Davos Region" in mail.outbox[0].subject

    def test_case_c_response_contains_added_and_region_name(self) -> None:
        """Case C: response fragment contains 'Added' and the region name."""
        SubscriberFactory.create(
            user__email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create(name="Davos Region")
        client = Client()
        response = client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Added" in response.content
        assert b"Davos Region" in response.content

    # ---- Case D: existing active subscriber, already subscribed ----

    def test_case_d_already_subscribed_is_idempotent(self) -> None:
        """Case D: active subscriber already subscribed → no duplicate Subscription row."""
        subscriber = SubscriberFactory.create(
            user__email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create()
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

    def test_case_d_already_subscribed_sends_no_email(self) -> None:
        """Case D: active subscriber already subscribed → no email sent."""
        subscriber = SubscriberFactory.create(
            user__email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = Client()
        client.post(
            reverse("subscriptions:subscribe"),
            data={"email": "active2@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 0

    def test_case_d_response_contains_already_subscribed_and_region_name(self) -> None:
        """Case D: response fragment contains 'already subscribed' and the region name."""
        subscriber = SubscriberFactory.create(
            user__email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create(name="Zermatt Region")
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
# subscribe_partial — RequestLog wiring (SNOW-277)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscribePartialRequestLog:
    """Tests for RequestLog capture and FK wiring in subscribe_partial."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_case_a_subscriber_gets_acquisition_request(self) -> None:
        """New subscriber (Case A) has acquisition_request populated."""
        from unittest.mock import patch

        from bulletins.services.geoip import GeoLookup

        fake_geo = GeoLookup(
            country="CH",
            subdivision="VS",
            city="Sion",
            latitude=46.0,
            longitude=7.0,
            accuracy_radius_km=50,
        )
        region = MicroRegionFactory.create()
        with patch("bulletins.services.geoip.geo_lookup", return_value=fake_geo):
            Client().post(
                reverse("subscriptions:subscribe"),
                data={"email": "newuser@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        subscriber = Subscriber.objects.get(user__email="newuser@example.com")
        assert subscriber.acquisition_request is not None
        assert subscriber.acquisition_request.country_code == "CH"

    def test_case_a_subscription_gets_subscribed_via(self) -> None:
        """New subscription (Case A) has subscribed_via populated."""
        from unittest.mock import patch

        from bulletins.services.geoip import GeoLookup

        fake_geo = GeoLookup(
            country="DE",
            subdivision="",
            city="Berlin",
            latitude=52.5,
            longitude=13.4,
            accuracy_radius_km=100,
        )
        region = MicroRegionFactory.create()
        with patch("bulletins.services.geoip.geo_lookup", return_value=fake_geo):
            Client().post(
                reverse("subscriptions:subscribe"),
                data={"email": "newuser2@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        subscriber = Subscriber.objects.get(user__email="newuser2@example.com")
        subscription = Subscription.objects.get(subscriber=subscriber, region=region)
        assert subscription.subscribed_via is not None
        assert subscription.subscribed_via.country_code == "DE"

    def test_acquisition_request_first_observation_wins(self) -> None:
        """Re-submitting does not overwrite acquisition_request on Subscriber."""
        from unittest.mock import patch

        from bulletins.services.geoip import GeoLookup

        region = MicroRegionFactory.create()
        email = "returning@example.com"

        # First call (Case A: new subscriber).
        geo_first = GeoLookup(
            country="CH",
            subdivision="",
            city="",
            latitude=None,
            longitude=None,
            accuracy_radius_km=None,
        )
        with patch("bulletins.services.geoip.geo_lookup", return_value=geo_first):
            Client().post(
                reverse("subscriptions:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        original_request_id = Subscriber.objects.get(
            user__email=email
        ).acquisition_request_id

        # Second call from a different IP / country (Case B: pending re-send).
        geo_second = GeoLookup(
            country="FR",
            subdivision="",
            city="",
            latitude=None,
            longitude=None,
            accuracy_radius_km=None,
        )
        with patch("bulletins.services.geoip.geo_lookup", return_value=geo_second):
            Client().post(
                reverse("subscriptions:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        # acquisition_request unchanged.
        sub = Subscriber.objects.get(user__email=email)
        assert sub.acquisition_request_id == original_request_id

    def test_subscription_started_event_includes_country_code(self) -> None:
        """subscription_started props include country_code when non-empty (Case A)."""
        from unittest.mock import patch

        from bulletins.services.geoip import GeoLookup

        fake_geo = GeoLookup(
            country="AT",
            subdivision="",
            city="",
            latitude=None,
            longitude=None,
            accuracy_radius_km=None,
        )
        region = MicroRegionFactory.create()
        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=fake_geo),
            patch("subscriptions.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("subscriptions:subscribe"),
                data={"email": "austria@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        assert "subscription_started" in calls
        props = calls["subscription_started"].args[2]
        assert props.get("country_code") == "AT"

    def test_subscription_started_omits_country_code_when_empty(self) -> None:
        """subscription_started omits country_code when geo lookup returns None."""
        from unittest.mock import patch

        region = MicroRegionFactory.create()
        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=None),
            patch("subscriptions.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("subscriptions:subscribe"),
                data={"email": "noip@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        props = calls["subscription_started"].args[2]
        assert "country_code" not in props


# ---------------------------------------------------------------------------
# sign_in_view — RequestLog wiring (SNOW-277)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignInViewRequestLog:
    """Tests for RequestLog capture in sign_in_view."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sign_in_requested_includes_country_code(self) -> None:
        """sign_in_requested event includes country_code when non-empty."""
        from unittest.mock import patch

        from bulletins.services.geoip import GeoLookup

        SubscriberFactory.create(user__email="signin@example.com")
        fake_geo = GeoLookup(
            country="IT",
            subdivision="",
            city="",
            latitude=None,
            longitude=None,
            accuracy_radius_km=None,
        )
        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=fake_geo),
            patch("subscriptions.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("subscriptions:sign_in"),
                data={"email": "signin@example.com"},
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        assert "sign_in_requested" in calls
        props = calls["sign_in_requested"].args[2]
        assert props.get("country_code") == "IT"

    def test_sign_in_requested_omits_country_code_when_empty(self) -> None:
        """sign_in_requested omits country_code when geo lookup returns None."""
        from unittest.mock import patch

        SubscriberFactory.create(user__email="signin2@example.com")
        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=None),
            patch("subscriptions.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("subscriptions:sign_in"),
                data={"email": "signin2@example.com"},
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        props = calls["sign_in_requested"].args[2]
        assert "country_code" not in props


# ---------------------------------------------------------------------------
# account_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountView:
    """Tests for the account_view."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend to avoid real dispatch."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_valid_token_activates_pending_subscriber(self) -> None:
        """Pending subscriber is activated when a valid token is presented."""
        SubscriberFactory.create(
            user__email="pending@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("pending@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))
        sub = Subscriber.objects.get(user__email="pending@example.com")
        assert sub.status == Subscriber.Status.ACTIVE
        assert sub.confirmed_at is not None

    def test_valid_token_redirects_to_manage_with_just_confirmed(self) -> None:
        """Successful token click redirects to /subscribe/manage/?just_confirmed=1."""
        SubscriberFactory.create(
            user__email="redirect@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("redirect@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert response["Location"] == "/subscribe/manage/?just_confirmed=1"

    def test_valid_token_sets_confirmed_at_with_timezone(self) -> None:
        """confirmed_at timestamp has tzinfo set."""
        SubscriberFactory.create(
            user__email="tz@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("tz@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))
        sub = Subscriber.objects.get(user__email="tz@example.com")
        assert sub.confirmed_at is not None
        assert sub.confirmed_at.tzinfo is not None

    def test_valid_token_sets_session(self) -> None:
        """Django auth session is established after successful token click."""
        SubscriberFactory.create(
            user__email="session@example.com", status=Subscriber.Status.PENDING
        )
        token = _valid_account_token("session@example.com")
        client = Client()
        client.get(reverse("subscriptions:account", kwargs={"token": token}))
        sub = Subscriber.objects.get(user__email="session@example.com")
        assert client.session.get("_auth_user_id") == str(sub.user_id)

    def test_idempotent_on_re_click_does_not_re_stamp_confirmed_at(self) -> None:
        """Re-clicking the same link for an already-active subscriber does not re-stamp confirmed_at."""
        sub = SubscriberFactory.create(
            user__email="active@example.com", status=Subscriber.Status.ACTIVE
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

    def test_active_subscriber_re_click_also_redirects(self) -> None:
        """Active subscriber clicking the link again still gets redirected to manage."""
        sub = SubscriberFactory.create(
            user__email="active2@example.com", status=Subscriber.Status.ACTIVE
        )
        sub.confirmed_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        sub.save(update_fields=["confirmed_at"])
        token = _valid_account_token("active2@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert "/subscribe/manage/" in response["Location"]

    def test_expired_token_returns_400(self) -> None:
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

    def test_garbage_token_returns_400(self) -> None:
        """Garbage token string returns 400."""
        client = Client()
        response = client.get(
            reverse("subscriptions:account", kwargs={"token": "garbage-token"})
        )
        assert response.status_code == 400

    def test_valid_token_unknown_email_returns_400(self) -> None:
        """Valid token for a deleted subscriber returns 400."""
        token = _valid_account_token("ghost@example.com")
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        assert response.status_code == 400

    def test_unsubscribe_token_at_account_endpoint_returns_400(self) -> None:
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
    """Unauthenticated GET on manage_view redirects to sign_in."""

    def test_get_redirects_to_sign_in(self) -> None:
        """Unauthenticated GET on manage redirects to the sign-in page."""
        client = Client()
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:sign_in")


@pytest.mark.django_db
class TestSignInView:
    """Tests for the dedicated sign_in_view."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_get_returns_200_with_email_form(self) -> None:
        """GET renders the email entry form."""
        client = Client()
        response = client.get(reverse("subscriptions:sign_in"))
        assert response.status_code == 200
        assert b"email" in response.content.lower()

    def test_authenticated_get_redirects_to_manage(self) -> None:
        """Authenticated subscriber hitting sign-in is redirected to manage."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:sign_in"))
        assert response.status_code == 302
        assert "/subscribe/manage/" in response["Location"]

    def test_post_known_email_sends_account_access_email(self) -> None:
        """Known email on POST → account access email sent."""
        SubscriberFactory.create(user__email="known@example.com")
        client = Client()
        response = client.post(
            reverse("subscriptions:sign_in"),
            data={"email": "known@example.com"},
        )
        assert response.status_code == 200
        assert len(mail.outbox) == 1
        assert "Snowdesk" in mail.outbox[0].subject

    def test_post_unknown_email_creates_subscriber_and_sends_email(self) -> None:
        """Unknown email on POST → subscriber created, email sent."""
        client = Client()
        response = client.post(
            reverse("subscriptions:sign_in"),
            data={"email": "brandnew@example.com"},
        )
        assert response.status_code == 200
        assert len(mail.outbox) == 1
        assert Subscriber.objects.filter(user__email="brandnew@example.com").exists()

    def test_post_known_email_response_identical_to_unknown(self) -> None:
        """Responses for known and unknown emails must be byte-equal (anti-enumeration)."""
        SubscriberFactory.create(user__email="exists@example.com")
        client = Client()
        resp_known = client.post(
            reverse("subscriptions:sign_in"),
            data={"email": "exists@example.com"},
        )
        resp_unknown = client.post(
            reverse("subscriptions:sign_in"),
            data={"email": "nosuchuser@example.com"},
        )
        import re

        nonce_re = re.compile(rb'\s?nonce="[^"]+"')
        assert nonce_re.sub(b"", resp_known.content) == nonce_re.sub(
            b"", resp_unknown.content
        )

    def test_post_invalid_email_rerenders_form(self) -> None:
        """Invalid email on POST re-renders the form with validation errors."""
        client = Client()
        response = client.post(
            reverse("subscriptions:sign_in"),
            data={"email": "not-valid"},
        )
        assert response.status_code == 200
        assert b"valid email" in response.content.lower()

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit on sign-in POST returns 429."""
        from django.contrib.auth.models import AnonymousUser

        from subscriptions.views import sign_in_view

        rf = RequestFactory()
        request = rf.post(
            reverse("subscriptions:sign_in"),
            data={"email": "rl@example.com"},
        )
        request.user = AnonymousUser()  # noqa: B010 — set on test request object

        with patch(
            "subscriptions.views.get_usage",
            return_value={"should_limit": True},
        ):
            response = sign_in_view(request)
        assert response.status_code == 429


@pytest.mark.django_db
class TestSignInPostTimingSideChannel:
    """SNOW-26: known vs unknown email POST on sign_in must not leak via response time."""

    @override_settings(
        TASKS={
            "default": {
                "BACKEND": "django_tasks_db.DatabaseBackend",
                "QUEUES": ["default"],
            }
        },
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_known_and_unknown_response_time_within_bound(self) -> None:
        """With DatabaseBackend, both branches enqueue a task and return immediately.

        This validates that the production primitive (DB insert + return)
        maintains the timing-indistinguishability guarantee from SNOW-26.
        No worker is running so tasks accumulate in the DB without executing —
        the test only measures the enqueue-side timing, which is what the
        request handler observes.
        """
        SubscriberFactory.create(user__email="known@example.com")
        client = Client()
        # Warm-up — first request pays template-cache and DB-connection cost.
        client.post(
            reverse("subscriptions:sign_in"),
            data={"email": "warm@example.com"},
        )

        n = 5
        known_times: list[float] = []
        unknown_times: list[float] = []
        for i in range(n):
            t0 = time.perf_counter()
            client.post(
                reverse("subscriptions:sign_in"),
                data={"email": "known@example.com"},
            )
            known_times.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            client.post(
                reverse("subscriptions:sign_in"),
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

    def test_get_shows_subscribed_region_name(self) -> None:
        """Authenticated GET shows the subscribed region's name."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create(name="Zermatt Region")
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert b"Zermatt Region" in response.content

    def test_get_shows_subscribed_region_id(self) -> None:
        """Authenticated GET shows the subscribed region's region_id."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        assert region.region_id.encode() in response.content

    def test_get_shows_resort_names_for_subscribed_region(self) -> None:
        """Authenticated GET lists resort names for subscribed regions."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        ResortFactory.create(region=region, name="Verbier")
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert b"Verbier" in response.content

    def test_get_does_not_show_non_subscribed_region(self) -> None:
        """Non-subscribed regions must not appear in the manage page."""
        subscriber = SubscriberFactory.create()
        subscribed_region = MicroRegionFactory.create(name="Subscribed Region")
        MicroRegionFactory.create(name="Other Region Zephyr")
        SubscriptionFactory.create(subscriber=subscriber, region=subscribed_region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert b"Other Region Zephyr" not in response.content

    def test_get_shows_welcome_banner_when_just_confirmed(self) -> None:
        """?just_confirmed=1 querystring renders the welcome banner."""
        subscriber = SubscriberFactory.create()
        MicroRegionFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage") + "?just_confirmed=1")
        assert response.status_code == 200
        assert b"confirmed" in response.content.lower()

    def test_get_no_welcome_banner_without_just_confirmed(self) -> None:
        """Without ?just_confirmed the welcome banner is absent."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 200
        # The banner contains a specific phrase; assert it's absent
        assert b"Your subscription is confirmed" not in response.content

    def test_stale_session_redirects_to_sign_in(self) -> None:
        """A session whose subscriber was deleted redirects to sign-in."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        subscriber.delete()
        response = client.get(reverse("subscriptions:manage"))
        assert response.status_code == 302
        assert reverse("subscriptions:sign_in") in response["Location"]

    def test_get_shows_map_cta_link(self) -> None:
        """Authenticated manage page contains the 'Choose more regions on the map' link."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))
        assert b"map" in response.content.lower()
        assert b"/map/" in response.content

    def test_card_shows_bulletin_link(self) -> None:
        """Each card links to the region's evergreen bulletin URL with today's date label."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)

        with freeze_time("2026-05-18"):
            response = client.get(reverse("subscriptions:manage"))

        assert response.status_code == 200
        bulletin_url = region.get_absolute_url().encode()
        assert bulletin_url in response.content
        # Date formatted as j N Y (day month year, no leading zero)
        assert b"18 May 2026" in response.content
        assert b"Open bulletin for" in response.content

    def test_card_shows_map_link(self) -> None:
        """Each card contains a direct link to /map/#<region_id> using the raw (uppercase) region_id."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create(region_id="CH-1234")
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))

        assert response.status_code == 200
        assert b"/map/#CH-1234" in response.content

    def test_card_shows_breadcrumb(self) -> None:
        """Each card renders the L1 (MajorRegion) and L2 (SubRegion) names in the breadcrumb."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))

        assert response.status_code == 200
        # SubFactory chain: MicroRegion → SubRegion → MajorRegion
        subregion_name = region.subregion.name_en or region.subregion.name_native
        major_name = (
            region.subregion.major.name_en or region.subregion.major.name_native
        )
        assert subregion_name.encode() in response.content
        assert major_name.encode() in response.content

    def test_card_shows_country_flag_and_region_id(self) -> None:
        """Each card renders a flag <use> reference and the case-preserved region_id."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create(region_id="CH-4115")
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.get(reverse("subscriptions:manage"))

        assert response.status_code == 200
        # Flag sprite use reference for CH
        assert b'href="#flag-ch"' in response.content
        # Case-preserved region_id appears in the badge
        assert b"CH-4115" in response.content


# ---------------------------------------------------------------------------
# remove_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveRegion:
    """Tests for the remove_region HTMX view."""

    def test_removes_subscription_row(self) -> None:
        """Session-authenticated POST removes the Subscription row."""
        subscriber = SubscriberFactory.create()
        region1 = MicroRegionFactory.create()
        region2 = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region1)
        SubscriptionFactory.create(subscriber=subscriber, region=region2)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region",
                kwargs={"region_id": region1.region_id.lower()},
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

    def test_last_region_hard_deletes_subscriber(self) -> None:
        """Removing the last region hard-deletes the subscriber."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        client = _make_session_client(subscriber)
        client.post(
            reverse(
                "subscriptions:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert not Subscriber.objects.filter(pk=sub_pk).exists()

    def test_last_region_responds_with_hx_redirect(self) -> None:
        """Removing the last region responds with HX-Redirect header."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert "HX-Redirect" in response
        assert "unsubscribe" in response["HX-Redirect"]

    def test_no_session_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse(
                "subscriptions:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
        )
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("subscriptions:remove_region", kwargs={"region_id": "ch-0001"}),
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from subscriptions.views import remove_region

        response = remove_region(request, region_id="ch-0001")
        assert response.status_code == 429

    def test_region_not_held_returns_200_and_does_not_delete_subscriber(self) -> None:
        """POST for a region the subscriber never held returns benign 200; no data changed.

        The manage-page card list never showed the region, so a benign empty 200
        is the correct response — matching the "card removed via outerHTML swap"
        semantics — while ensuring the subscriber is not hard-deleted.
        """
        subscriber = SubscriberFactory.create()
        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        region_c = MicroRegionFactory.create()  # subscriber does NOT hold region_c
        SubscriptionFactory.create(subscriber=subscriber, region=region_a)
        SubscriptionFactory.create(subscriber=subscriber, region=region_b)
        sub_pk = subscriber.pk
        client = _make_session_client(subscriber)

        response = client.post(
            reverse(
                "subscriptions:remove_region",
                kwargs={"region_id": region_c.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )

        assert response.status_code == 200
        # Existing subscriptions must be untouched.
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region_a
        ).exists()
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region_b
        ).exists()
        # Subscriber must not have been hard-deleted.
        assert Subscriber.objects.filter(pk=sub_pk).exists()


# ---------------------------------------------------------------------------
# delete_account
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteAccount:
    """Tests for the delete_account HTMX view."""

    def test_hard_deletes_subscriber(self) -> None:
        """Session-authenticated POST hard-deletes the subscriber."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        client = _make_session_client(subscriber)
        client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert not Subscriber.objects.filter(pk=sub_pk).exists()

    def test_cascades_subscription_rows(self) -> None:
        """Subscriber deletion cascades to Subscription rows."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        sub = SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = sub.pk
        client = _make_session_client(subscriber)
        client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert not Subscription.objects.filter(pk=sub_pk).exists()

    def test_clears_session(self) -> None:
        """Session is cleared after account deletion."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert "_auth_user_id" not in client.session

    def test_responds_with_hx_redirect(self) -> None:
        """Response includes HX-Redirect header pointing to unsubscribe-done."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 200
        assert "HX-Redirect" in response
        assert "unsubscribe" in response["HX-Redirect"]

    def test_no_session_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        client = Client()
        response = client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(reverse("subscriptions:delete_account"))
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(reverse("subscriptions:delete_account"))
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from subscriptions.views import delete_account

        response = delete_account(request)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# sign_out
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignOut:
    """Tests for the sign_out view."""

    def test_clears_session_and_redirects(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(reverse("subscriptions:sign_out"))
        assert response.status_code == 302
        assert response["Location"] == reverse("subscriptions:sign_in")
        assert "_auth_user_id" not in client.session

    def test_get_not_allowed(self) -> None:
        client = Client()
        response = client.get(reverse("subscriptions:sign_out"))
        assert response.status_code == 405

    def test_works_when_not_signed_in(self) -> None:
        client = Client()
        response = client.post(reverse("subscriptions:sign_out"))
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# unsubscribe_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsubscribeView:
    """Tests for the unsubscribe_view."""

    def test_get_valid_token_renders_confirmation(self) -> None:
        """Valid token GET renders the unsubscribe confirmation page."""
        subscriber = SubscriberFactory.create(user__email="unsub@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        token = generate_unsubscribe_token("unsub@example.com", region.region_id)
        client = Client()
        response = client.get(
            reverse("subscriptions:unsubscribe", kwargs={"token": token})
        )
        assert response.status_code == 200
        assert b"unsubscribe" in response.content.lower()

    def test_post_valid_token_removes_subscription(self) -> None:
        """Valid token POST deletes the matching Subscription row."""
        subscriber = SubscriberFactory.create(user__email="unsub2@example.com")
        region = MicroRegionFactory.create()
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

    def test_post_last_subscription_hard_deletes_subscriber(self) -> None:
        """Removing last subscription hard-deletes the Subscriber."""
        subscriber = SubscriberFactory.create(user__email="lastregion@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        token = generate_unsubscribe_token("lastregion@example.com", region.region_id)
        client = Client()
        client.post(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        assert not Subscriber.objects.filter(pk=sub_pk).exists()

    def test_post_not_last_subscription_keeps_subscriber(self) -> None:
        """Removing one of multiple subscriptions keeps the subscriber."""
        subscriber = SubscriberFactory.create(user__email="keep@example.com")
        region1 = MicroRegionFactory.create()
        region2 = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region1)
        SubscriptionFactory.create(subscriber=subscriber, region=region2)
        token = generate_unsubscribe_token("keep@example.com", region1.region_id)
        client = Client()
        client.post(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        assert Subscriber.objects.filter(user__email="keep@example.com").exists()
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region2
        ).exists()

    def test_post_idempotent_when_already_deleted(self) -> None:
        """Re-submitting after subscriber deletion renders done page without error."""
        subscriber = SubscriberFactory.create(user__email="gone@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        token = generate_unsubscribe_token("gone@example.com", region.region_id)
        subscriber.delete()
        client = Client()
        response = client.post(
            reverse("subscriptions:unsubscribe", kwargs={"token": token})
        )
        assert response.status_code == 200
        assert b"unsubscribed" in response.content.lower()

    def test_bad_token_returns_400(self) -> None:
        """Garbage token returns 400."""
        client = Client()
        response = client.get(
            reverse("subscriptions:unsubscribe", kwargs={"token": "garbage"})
        )
        assert response.status_code == 400

    def test_unsubscribe_token_does_not_expire(self) -> None:
        """Unsubscribe tokens must remain valid regardless of age."""
        subscriber = SubscriberFactory.create(user__email="old@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)

        with freeze_time("2020-01-01T00:00:00Z"):
            token = generate_unsubscribe_token("old@example.com", region.region_id)

        with freeze_time("2025-06-01T00:00:00Z"):
            client = Client()
            response = client.get(
                reverse("subscriptions:unsubscribe", kwargs={"token": token})
            )
        assert response.status_code == 200

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        region = MicroRegionFactory.create()
        token = generate_unsubscribe_token("rl@example.com", region.region_id)
        request = rf.get(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from subscriptions.views import unsubscribe_view

        response = unsubscribe_view(request, token=token)
        assert response.status_code == 429

    def test_cross_salt_token_returns_400(self) -> None:
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

    def test_get_renders_done_page(self) -> None:
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
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
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

    def test_uppercase_and_lowercase_same_address_creates_one_subscriber(self) -> None:
        """Two POSTs for the same address in different case create one Subscriber."""
        region = MicroRegionFactory.create()
        self._subscribe("User@Example.com", region.region_id)
        self._subscribe("user@example.com", region.region_id)
        assert Subscriber.objects.filter(user__email="user@example.com").count() == 1
        assert Subscriber.objects.count() == 1

    def test_mixed_case_address_is_stored_lowercase(self) -> None:
        """The stored email address is the lowercase-normalised form."""
        region = MicroRegionFactory.create()
        self._subscribe("ALICE@EXAMPLE.COM", region.region_id)
        assert Subscriber.objects.filter(user__email="alice@example.com").exists()

    def test_sign_in_post_looks_up_normalised_email(self) -> None:
        """sign_in_view POST for a mixed-case address finds the lowercase subscriber."""
        subscriber = SubscriberFactory.create(
            user__email="bob@example.com", status=Subscriber.Status.ACTIVE
        )
        client = Client()
        with patch("subscriptions.views.send_account_access_email") as mock_send:
            client.post(
                reverse("subscriptions:sign_in"),
                data={"email": "BOB@EXAMPLE.COM"},
            )
        mock_send.assert_called_once_with(
            subscriber.user.email, request=mock_send.call_args[1]["request"]
        )

    def test_account_view_resolves_mixed_case_token_to_lowercase_subscriber(
        self,
    ) -> None:
        """A token generated for a mixed-case email activates the lowercase subscriber.

        verify_token returns the raw value embedded in the token, which may
        come from a mixed-case email.  account_view must normalise it before
        the database lookup so the stored lowercase record is found.
        """
        subscriber = SubscriberFactory.create(
            user__email="foo@bar.com", status=Subscriber.Status.PENDING
        )
        token = generate_token("FOO@BAR.com", salt=SALT_ACCOUNT_ACCESS)
        client = Client()
        response = client.get(reverse("subscriptions:account", kwargs={"token": token}))
        # Should redirect to manage page, not render a link-expired 400.
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("subscriptions:manage"))
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE

    def test_unsubscribe_post_resolves_mixed_case_token(self) -> None:
        """An unsubscribe token generated for a mixed-case email removes the subscription.

        verify_unsubscribe_token now lowercases the email component, and
        unsubscribe_view POSTs against email.lower(), so the subscription
        for the lowercase-stored subscriber is deleted correctly.
        """
        subscriber = SubscriberFactory.create(
            user__email="foo@bar.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        token = generate_unsubscribe_token("FOO@BAR.com", region.region_id)
        client = Client()
        response = client.post(
            reverse("subscriptions:unsubscribe", kwargs={"token": token}),
        )
        assert response.status_code == 200
        assert not Subscription.objects.filter(
            subscriber=subscriber, region=region
        ).exists()


class TestEmailFormNormalisation:
    """Unit tests for SubscribeForm and EmailForm clean_email."""

    def test_subscribe_form_lowercases_email(self) -> None:
        """SubscribeForm.clean_email returns a lowercased address."""
        from subscriptions.forms import SubscribeForm

        form = SubscribeForm(data={"email": "TEST@EXAMPLE.COM", "region_id": "CH-0001"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_subscribe_form_strips_whitespace(self) -> None:
        """SubscribeForm.clean_email strips leading and trailing whitespace."""
        from subscriptions.forms import SubscribeForm

        form = SubscribeForm(
            data={"email": "  user@example.com  ", "region_id": "CH-0001"}
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "user@example.com"

    def test_email_form_lowercases_email(self) -> None:
        """EmailForm.clean_email returns a lowercased address."""
        from subscriptions.forms import EmailForm

        form = EmailForm(data={"email": "TEST@EXAMPLE.COM"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_email_form_strips_whitespace(self) -> None:
        """EmailForm.clean_email strips leading and trailing whitespace."""
        from subscriptions.forms import EmailForm

        form = EmailForm(data={"email": "  user@example.com  "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "user@example.com"


# ---------------------------------------------------------------------------
# add_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddRegion:
    """Tests for the add_region HTMX view."""

    def test_authenticated_creates_subscription(self) -> None:
        """Session-authenticated POST creates a Subscription row and returns 200."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region
        ).exists()

    def test_authenticated_returns_success_added_fragment(self) -> None:
        """Response contains the subscribe_success_added fragment content."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create(name="Davos Region")
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert b"Davos Region" in response.content
        assert b"Added" in response.content

    def test_idempotent_second_post_returns_success(self) -> None:
        """POSTing twice does not raise IntegrityError — returns success fragment."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        client = _make_session_client(subscriber)
        url = reverse(
            "subscriptions:add_region", kwargs={"region_id": region.region_id.lower()}
        )
        client.post(url, **_HTMX_HEADERS)
        response = client.post(url, **_HTMX_HEADERS)
        assert response.status_code == 200
        # Exactly one Subscription row (idempotent).
        assert (
            Subscription.objects.filter(subscriber=subscriber, region=region).count()
            == 1
        )

    def test_unauthenticated_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse(
                "subscriptions:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
        )
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("subscriptions:add_region", kwargs={"region_id": "ch-0001"}),
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from subscriptions.views import add_region

        response = add_region(request, region_id="ch-0001")
        assert response.status_code == 429

    def test_unknown_region_returns_400(self) -> None:
        """POST with a region_id that does not exist returns 400 error fragment."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(
            reverse("subscriptions:add_region", kwargs={"region_id": "xx-9999"}),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 400
        assert b"went wrong" in response.content.lower()


# ---------------------------------------------------------------------------
# remove_region_from_bulletin
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveRegionFromBulletin:
    """Tests for the remove_region_from_bulletin HTMX view."""

    def test_removes_subscription_row_and_returns_confirmation(self) -> None:
        """Multi-region subscriber: removes target subscription, returns confirmation fragment."""
        subscriber = SubscriberFactory.create()
        region1 = MicroRegionFactory.create(name="Davos Region")
        region2 = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region1)
        SubscriptionFactory.create(subscriber=subscriber, region=region2)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region_from_bulletin",
                kwargs={"region_id": region1.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert not Subscription.objects.filter(
            subscriber=subscriber, region=region1
        ).exists()
        # Other subscription retained.
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region2
        ).exists()
        assert b"unsubscribed" in response.content.lower()
        assert b"Davos Region" in response.content

    def test_last_region_hard_deletes_subscriber_and_redirects(self) -> None:
        """Last-region removal hard-deletes the subscriber and sends HX-Redirect."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        sub_pk = subscriber.pk
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region_from_bulletin",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert not Subscriber.objects.filter(pk=sub_pk).exists()
        assert "HX-Redirect" in response
        assert "unsubscribe" in response["HX-Redirect"]

    def test_unauthenticated_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse(
                "subscriptions:remove_region_from_bulletin",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region_from_bulletin",
                kwargs={"region_id": region.region_id.lower()},
            ),
        )
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse(
                "subscriptions:remove_region_from_bulletin",
                kwargs={"region_id": "ch-0001"},
            ),
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from subscriptions.views import remove_region_from_bulletin

        response = remove_region_from_bulletin(request, region_id="ch-0001")
        assert response.status_code == 429

    def test_unknown_region_returns_400(self) -> None:
        """POST with a region_id that does not exist returns 400 error fragment."""
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        response = client.post(
            reverse(
                "subscriptions:remove_region_from_bulletin",
                kwargs={"region_id": "xx-9999"},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 400
        assert b"went wrong" in response.content.lower()

    def test_region_not_held_returns_400_and_does_not_delete_subscriber(self) -> None:
        """POST for a region the subscriber never held returns 400; no data is changed.

        Protects against the cascade bug where a zero-row delete could trigger
        a hard-delete of the subscriber when they happened to have no subscriptions
        for unrelated reasons (e.g. a stale or forged POST to a region_id the
        subscriber never held).
        """
        subscriber = SubscriberFactory.create()
        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        region_c = MicroRegionFactory.create()  # subscriber does NOT hold region_c
        SubscriptionFactory.create(subscriber=subscriber, region=region_a)
        SubscriptionFactory.create(subscriber=subscriber, region=region_b)
        sub_pk = subscriber.pk
        client = _make_session_client(subscriber)

        response = client.post(
            reverse(
                "subscriptions:remove_region_from_bulletin",
                kwargs={"region_id": region_c.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )

        assert response.status_code == 400
        assert b"went wrong" in response.content.lower()
        # Existing subscriptions must be untouched.
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region_a
        ).exists()
        assert Subscription.objects.filter(
            subscriber=subscriber, region=region_b
        ).exists()
        # Subscriber must not have been hard-deleted.
        assert Subscriber.objects.filter(pk=sub_pk).exists()


# ---------------------------------------------------------------------------
# Analytics event firing — subscription flow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalyticsSubscriptionStarted:
    """analytics.track('subscription_started') fires on Case A only."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_case_a_fires_subscription_started(self) -> None:
        region = MicroRegionFactory.create()
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:subscribe"),
                data={"email": "new@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )
        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "subscription_started"
        ]
        assert len(calls) == 1

    def test_case_a_distinct_id_is_anon_uuid(self) -> None:
        import re

        region = MicroRegionFactory.create()
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:subscribe"),
                data={"email": "new2@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )
        started_calls = [
            c for c in mock_track.call_args_list if c.args[0] == "subscription_started"
        ]
        assert len(started_calls) == 1
        distinct_id = started_calls[0].args[1]
        # Distinct ID should be a UUID string — not an email or numeric PK.
        assert re.match(r"^[0-9a-f-]{36}$", distinct_id), (
            f"Expected UUID, got {distinct_id!r}"
        )

    def test_case_b_does_not_fire_subscription_started(self) -> None:
        region = MicroRegionFactory.create()
        SubscriberFactory.create(
            user__email="pending@example.com", status=Subscriber.Status.PENDING
        )
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:subscribe"),
                data={"email": "pending@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )
        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "subscription_started"
        ]
        assert len(calls) == 0

    def test_utm_params_included_from_session(self) -> None:
        region = MicroRegionFactory.create()
        client = Client()
        session = client.session
        session["analytics_utm"] = {
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "winter-2026",
        }
        session.save()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:subscribe"),
                data={"email": "utm@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )
        started_calls = [
            c for c in mock_track.call_args_list if c.args[0] == "subscription_started"
        ]
        assert len(started_calls) == 1
        props = started_calls[0].args[2]
        assert props.get("source") == "newsletter"
        assert props.get("utm_medium") == "email"
        assert props.get("utm_campaign") == "winter-2026"


@pytest.mark.django_db
class TestAnalyticsSubscriptionConfirmed:
    """analytics.track('subscription_confirmed') fires when PENDING subscriber confirms."""

    def test_fires_on_pending_confirmation(self) -> None:
        subscriber = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        token = _valid_account_token(subscriber.user.email)
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.get(reverse("subscriptions:account", kwargs={"token": token}))
        calls = [
            c
            for c in mock_track.call_args_list
            if c.args[0] == "subscription_confirmed"
        ]
        assert len(calls) == 1
        assert calls[0].args[1] == str(subscriber.user_id)
        props = calls[0].args[2]
        assert "hours_since_started" in props

    def test_does_not_fire_on_already_active(self) -> None:
        subscriber = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        token = _valid_account_token(subscriber.user.email)
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.get(reverse("subscriptions:account", kwargs={"token": token}))
        calls = [
            c
            for c in mock_track.call_args_list
            if c.args[0] == "subscription_confirmed"
        ]
        assert len(calls) == 0

    def test_alias_called_when_anon_id_in_session(self) -> None:
        subscriber = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        token = _valid_account_token(subscriber.user.email)
        client = Client()
        session = client.session
        session["analytics_anon_id"] = "anon-uuid-111"
        session.save()
        with patch("subscriptions.views.analytics.alias") as mock_alias:
            client.get(reverse("subscriptions:account", kwargs={"token": token}))
        mock_alias.assert_called_once_with(
            distinct_id=str(subscriber.user_id),
            alias_id="anon-uuid-111",
        )

    def test_alias_not_called_without_anon_id(self) -> None:
        subscriber = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        token = _valid_account_token(subscriber.user.email)
        client = Client()
        with patch("subscriptions.views.analytics.alias") as mock_alias:
            client.get(reverse("subscriptions:account", kwargs={"token": token}))
        mock_alias.assert_not_called()


@pytest.mark.django_db
class TestAnalyticsRegionAdded:
    """analytics.track('region_added') fires in add_region and subscribe Case C."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_fires_in_add_region(self) -> None:
        subscriber = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        region = MicroRegionFactory.create()
        client = _make_session_client(subscriber)
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "subscriptions:add_region",
                    kwargs={"region_id": region.region_id.lower()},
                ),
                **_HTMX_HEADERS,
            )
        calls = [c for c in mock_track.call_args_list if c.args[0] == "region_added"]
        assert len(calls) == 1
        props = calls[0].args[2]
        assert props["region_id"] == region.region_id
        assert props["source"] == "bulletin"

    def test_fires_in_subscribe_case_c(self) -> None:
        subscriber = SubscriberFactory.create(
            user__email="active@example.com", status=Subscriber.Status.ACTIVE
        )
        region = MicroRegionFactory.create()
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:subscribe"),
                data={"email": subscriber.user.email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )
        calls = [c for c in mock_track.call_args_list if c.args[0] == "region_added"]
        assert len(calls) == 1
        props = calls[0].args[2]
        assert props["region_id"] == region.region_id

    def test_not_fired_on_duplicate_add(self) -> None:
        subscriber = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "subscriptions:add_region",
                    kwargs={"region_id": region.region_id.lower()},
                ),
                **_HTMX_HEADERS,
            )
        calls = [c for c in mock_track.call_args_list if c.args[0] == "region_added"]
        assert len(calls) == 0


@pytest.mark.django_db
class TestAnalyticsRegionRemoved:
    """analytics.track('region_removed') fires in _delete_subscription_with_cascade."""

    def test_fires_on_remove_region(self) -> None:
        subscriber = SubscriberFactory.create()
        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region_a)
        SubscriptionFactory.create(subscriber=subscriber, region=region_b)
        client = _make_session_client(subscriber)
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "subscriptions:remove_region",
                    kwargs={"region_id": region_a.region_id.lower()},
                ),
                **_HTMX_HEADERS,
            )
        calls = [c for c in mock_track.call_args_list if c.args[0] == "region_removed"]
        assert len(calls) == 1
        props = calls[0].args[2]
        assert props["region_id"] == region_a.region_id
        assert props["region_count_after"] == 1

    def test_region_count_after_zero_on_last_region(self) -> None:
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        client = _make_session_client(subscriber)
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "subscriptions:remove_region",
                    kwargs={"region_id": region.region_id.lower()},
                ),
                **_HTMX_HEADERS,
            )
        calls = [c for c in mock_track.call_args_list if c.args[0] == "region_removed"]
        assert len(calls) == 1
        props = calls[0].args[2]
        assert props["region_count_after"] == 0


@pytest.mark.django_db
class TestAnalyticsUnsubscribed:
    """analytics.track('unsubscribed') fires in delete_account and unsubscribe_view."""

    def test_fires_in_delete_account(self) -> None:
        subscriber = SubscriberFactory.create()
        pk = str(subscriber.pk)
        client = _make_session_client(subscriber)
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "unsubscribed"]
        assert len(calls) == 1
        assert calls[0].args[1] == pk
        props = calls[0].args[2]
        assert props["reason"] == "account_deleted"
        assert "account_age_days" in props

    def test_fires_in_unsubscribe_view(self) -> None:
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        pk = str(subscriber.user_id)
        token = generate_unsubscribe_token(subscriber.user.email, region.region_id)
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(reverse("subscriptions:unsubscribe", kwargs={"token": token}))
        calls = [c for c in mock_track.call_args_list if c.args[0] == "unsubscribed"]
        assert len(calls) == 1
        assert calls[0].args[1] == pk
        props = calls[0].args[2]
        assert props["reason"] == "unsubscribe_link"
        assert "account_age_days" in props


@pytest.mark.django_db
class TestAnalyticsSignInRequested:
    """analytics.track('sign_in_requested') fires when sign_in_view POST succeeds."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_fires_for_known_email(self) -> None:
        """POST with a known email fires sign_in_requested with the existing PK."""
        subscriber = SubscriberFactory.create(user__email="known@example.com")
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:sign_in"),
                data={"email": "known@example.com"},
            )
        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "sign_in_requested"
        ]
        assert len(calls) == 1
        assert calls[0].args[1] == str(subscriber.user_id)

    def test_fires_for_unknown_email_after_subscriber_created(self) -> None:
        """POST with a fresh email creates a Subscriber and fires sign_in_requested with the new PK."""
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:sign_in"),
                data={"email": "brandnew@example.com"},
            )
        new_subscriber = Subscriber.objects.get(user__email="brandnew@example.com")
        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "sign_in_requested"
        ]
        assert len(calls) == 1
        assert calls[0].args[1] == str(new_subscriber.user_id)

    def test_does_not_fire_on_invalid_email(self) -> None:
        """POST with an invalid email re-renders the form and does not fire the event."""
        client = Client()
        with patch("subscriptions.views.analytics.track") as mock_track:
            client.post(
                reverse("subscriptions:sign_in"),
                data={"email": "not-valid"},
            )
        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "sign_in_requested"
        ]
        assert calls == []


# ---------------------------------------------------------------------------
# SNOW-311 — caplog regression: no plaintext emails in log output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscribePartialLogging:
    """SNOW-311: subscribe_partial logs pk, never the plaintext email address."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_new_subscriber_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Case A (new subscriber): log record contains pk=, not the full email address.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        email = "caplog-new@example.com"
        region = MicroRegionFactory.create()

        with caplog.at_level(logging.INFO, logger="subscriptions.views"):
            Client().post(
                reverse("subscriptions:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        subscriber = Subscriber.objects.get(user__email=email)
        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email address must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # At least one record must mention the subscriber's pk.
        assert any(str(subscriber.pk) in msg for msg in all_messages), (
            f"No log record contains pk={subscriber.pk}; records: {all_messages}"
        )


@pytest.mark.django_db
class TestAccountViewLogging:
    """SNOW-311: account_view logs masked email for unknown-email path."""

    def test_unknown_email_path_logs_masked_not_plaintext(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A valid token for an email with no subscriber row logs the masked form.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        email = "unknown-caplog@example.com"
        token = _valid_account_token(email)

        with caplog.at_level(logging.WARNING, logger="subscriptions.views"):
            Client().get(f"/subscribe/account/{token}/")

        all_messages = [r.getMessage() for r in caplog.records]

        # Plaintext email must not appear.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # The masked form u***@example.com must appear in at least one record.
        assert any("u***@example.com" in msg for msg in all_messages), (
            f"Masked email not found in any log record; records: {all_messages}"
        )


@pytest.mark.django_db
class TestSignInViewLogging:
    """SNOW-311: sign_in_view POST logs pk=, never the plaintext email address."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_post_success_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """sign_in_view POST logs subscriber pk=, not the full email address.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        email = "signin-caplog@example.com"

        with caplog.at_level(logging.INFO, logger="subscriptions.views"):
            Client().post(
                reverse("subscriptions:sign_in"),
                data={"email": email},
            )

        subscriber = Subscriber.objects.get(user__email=email)
        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # At least one record must mention the subscriber's pk.
        assert any(str(subscriber.pk) in msg for msg in all_messages), (
            f"No log record contains pk={subscriber.pk}; records: {all_messages}"
        )


@pytest.mark.django_db
class TestDeleteAccountLogging:
    """SNOW-311: delete_account logs masked email, never the plaintext address."""

    def test_delete_account_logs_masked_not_plaintext(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """delete_account logs the masked email after hard-delete, not the plaintext.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        email = "delete-caplog@example.com"
        subscriber = SubscriberFactory.create(user__email=email)
        client = _make_session_client(subscriber)

        with caplog.at_level(logging.INFO, logger="subscriptions.views"):
            client.post(reverse("subscriptions:delete_account"), **_HTMX_HEADERS)

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # The masked form d***@example.com must appear in at least one record.
        assert any("d***@example.com" in msg for msg in all_messages), (
            f"Masked email not found in any log record; records: {all_messages}"
        )


@pytest.mark.django_db
class TestUnsubscribeViewLogging:
    """SNOW-311: unsubscribe_view hard-delete path logs masked email, never plaintext."""

    def test_last_subscription_hard_delete_logs_masked_not_plaintext(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Removing the last subscription (hard-delete) logs the masked email.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        email = "unsub-caplog@example.com"
        subscriber = SubscriberFactory.create(user__email=email)
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(subscriber=subscriber, region=region)
        token = generate_unsubscribe_token(email, region.region_id)

        with caplog.at_level(logging.INFO, logger="subscriptions.views"):
            Client().post(reverse("subscriptions:unsubscribe", kwargs={"token": token}))

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # The masked form u***@example.com must appear in at least one record.
        assert any("u***@example.com" in msg for msg in all_messages), (
            f"Masked email not found in any log record; records: {all_messages}"
        )
