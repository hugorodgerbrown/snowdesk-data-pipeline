"""
tests/accounts/test_views.py — Tests for accounts views.

Covers:
  subscribe_partial   — four-case matrix (A=new, B=pending, C=active+new-region,
  analytics events    — subscription_started, subscription_confirmed, region_added,
                        region_removed, unsubscribed (two sites).
                        D=active+already-subscribed); rate-limit 429; HTMX-only;
                        missing region_id rejected (400 form error);
                        unknown region_id returns 400 error fragment.
  account_view        — valid token verifies an unverified account; redirects
                        to manage with ?just_confirmed=1; idempotent on
                        re-click; bad/expired token → 400.
  manage_view         — unauthenticated GET/POST (byte-equal response for known
                        and unknown emails); authenticated GET shows region cards;
                        non-subscribed regions absent; just_confirmed banner;
                        telemetry toggle renders with role="switch", explainer
                        copy, and a link to the privacy policy (SNOW-387);
                        the flag-gated "My favourites" section lazy-loads
                        favourites:list when active, absent when inactive
                        (SNOW-415).
  remove_region       — removes one region; last region → account and session
                        survive, no redirect; no session → 403; non-HTMX →
                        400; rate-limit 429.
  delete_account      — hard-deletes account; clears session; HX-Redirect to done;
                        no session → 403; non-HTMX → 400.
  unsubscribe_view    — valid token GET/POST; idempotent; bad token → 400;
                        last-subscription hard-delete; rate-limit 429.
  unsubscribe_done_view — GET renders done page.
  caplog regression   — plaintext emails never appear in log output; pk=/masked
                        forms appear instead; covers subscribe_partial, account_view,
                        sign_in_view POST, delete_account, and unsubscribe_view
                        hard-delete (SNOW-311).
"""

import re
import time
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from freezegun import freeze_time
from pytest_django.fixtures import SettingsWrapper
from waffle.testutils import override_flag

from accounts.models import Account, Subscription
from accounts.services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_token,
    generate_unsubscribe_token,
)
from tests.factories import (
    AccountFactory,
    MicroRegionFactory,
    ResortFactory,
    SubscriptionFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


_TOKEN_BACKEND = "accounts.backends.TokenBackend"


def _make_session_client(account: Account) -> Client:
    """Return a test client logged in as the account's User via Django auth."""
    client = Client()
    client.force_login(account.user, backend=_TOKEN_BACKEND)
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
            reverse("accounts:subscribe"),
            data={"email": "alice@example.com", "region_id": region.region_id},
        )
        assert response.status_code == 400

    def test_get_returns_405(self) -> None:
        """GET on subscribe_partial is method-not-allowed."""
        client = Client()
        response = client.get(reverse("accounts:subscribe"), **_HTMX_HEADERS)
        assert response.status_code == 405

    def test_missing_region_id_returns_form_with_errors(self) -> None:
        """POST without region_id returns the form with validation errors."""
        client = Client()
        response = client.post(
            reverse("accounts:subscribe"),
            data={"email": "noregion@example.com"},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        # Form is re-rendered — no account created
        assert not Account.objects.filter(user__email="noregion@example.com").exists()

    def test_unknown_region_id_returns_400_error_fragment(self) -> None:
        """POST with a region_id that does not exist in the DB returns 400."""
        client = Client()
        response = client.post(
            reverse("accounts:subscribe"),
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
            reverse("accounts:subscribe"),
            data={"email": "not-an-email", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert b"valid email" in response.content.lower()

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("accounts:subscribe"),
            data={"email": "x@example.com", "region_id": "CH-0001"},
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr added by middleware
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr added by middleware

        import accounts.views  # noqa: F401
        from accounts.views import subscribe_partial

        response = subscribe_partial(request)
        assert response.status_code == 429

    # ---- Case A: new account ----

    def test_case_a_new_subscriber_creates_pending_record(self) -> None:
        """Case A: new email → Account created unverified."""
        client = Client()
        region = MicroRegionFactory.create()
        response = client.post(
            reverse("accounts:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        sub = Account.objects.get(user__email="newuser@example.com")
        assert not sub.is_verified

    def test_case_a_new_subscriber_creates_subscription_row(self) -> None:
        """Case A: new email + region → Subscription row created."""
        client = Client()
        region = MicroRegionFactory.create()
        client.post(
            reverse("accounts:subscribe"),
            data={"email": "newwithregion@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        sub = Account.objects.get(user__email="newwithregion@example.com")
        assert Subscription.objects.filter(account=sub, region=region).exists()

    def test_case_a_new_subscriber_sends_account_access_email(self) -> None:
        """Case A: new email → account-access email sent (subject contains 'Snowdesk')."""
        client = Client()
        region = MicroRegionFactory.create()
        client.post(
            reverse("accounts:subscribe"),
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
            reverse("accounts:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Check your inbox" in response.content

    # ---- Case B: existing pending account ----

    def test_case_b_pending_creates_subscription_row(self) -> None:
        """Case B: existing pending + new region → Subscription row created."""
        account = AccountFactory.create(
            user__email="pending@example.com", is_verified=False
        )
        region = MicroRegionFactory.create()
        client = Client()
        client.post(
            reverse("accounts:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert Subscription.objects.filter(account=account, region=region).exists()

    def test_case_b_pending_sends_account_access_email(self) -> None:
        """Case B: existing pending account → account-access email resent."""
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        region = MicroRegionFactory.create()
        client = Client()
        client.post(
            reverse("accounts:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "account" in mail.outbox[0].subject.lower()

    def test_case_b_response_contains_check_your_inbox(self) -> None:
        """Case B: response fragment contains 'Check your inbox'."""
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse("accounts:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Check your inbox" in response.content

    def test_case_a_and_b_responses_are_byte_equal(self) -> None:
        """Case A (new) and Case B (existing-unverified) must be byte-equal.

        This is the anti-enumeration invariant documented in docs/accounts.md:
        an unauthenticated submitter must not be able to tell whether an address
        is already on the system. Case B seeds an unverified account first; Case
        A subscribes a fresh address. After stripping the per-response CSP nonce,
        the two fragments must be identical.
        """
        region = MicroRegionFactory.create()
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        client = Client()
        resp_b = client.post(  # existing-unverified account
            reverse("accounts:subscribe"),
            data={"email": "pending@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        resp_a = client.post(  # brand-new account
            reverse("accounts:subscribe"),
            data={"email": "newuser@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        nonce_re = re.compile(rb'\s?nonce="[^"]+"')
        assert nonce_re.sub(b"", resp_a.content) == nonce_re.sub(b"", resp_b.content)

    # ---- Case C: existing active account, new region ----

    def test_case_c_active_new_region_creates_subscription_row(self) -> None:
        """Case C: active account + new region → Subscription row created."""
        account = AccountFactory.create(
            user__email="active@example.com", is_verified=True
        )
        region = MicroRegionFactory.create()
        client = Client()
        client.post(
            reverse("accounts:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert Subscription.objects.filter(account=account, region=region).exists()

    def test_case_c_active_new_region_sends_confirmation_email(self) -> None:
        """Case C: active account + new region → subscription confirmation email sent."""
        AccountFactory.create(user__email="active@example.com", is_verified=True)
        region = MicroRegionFactory.create(name="Davos Region")
        client = Client()
        client.post(
            reverse("accounts:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 1
        assert "Davos Region" in mail.outbox[0].subject

    def test_case_c_response_contains_added_and_region_name(self) -> None:
        """Case C: response fragment contains 'Added' and the region name."""
        AccountFactory.create(user__email="active@example.com", is_verified=True)
        region = MicroRegionFactory.create(name="Davos Region")
        client = Client()
        response = client.post(
            reverse("accounts:subscribe"),
            data={"email": "active@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert b"Added" in response.content
        assert b"Davos Region" in response.content

    # ---- Case D: existing active account, already subscribed ----

    def test_case_d_already_subscribed_is_idempotent(self) -> None:
        """Case D: active account already subscribed → no duplicate Subscription row."""
        account = AccountFactory.create(
            user__email="active2@example.com", is_verified=True
        )
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = Client()
        client.post(
            reverse("accounts:subscribe"),
            data={"email": "active2@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert Subscription.objects.filter(account=account, region=region).count() == 1

    def test_case_d_already_subscribed_sends_no_email(self) -> None:
        """Case D: active account already subscribed → no email sent."""
        account = AccountFactory.create(
            user__email="active2@example.com", is_verified=True
        )
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = Client()
        client.post(
            reverse("accounts:subscribe"),
            data={"email": "active2@example.com", "region_id": region.region_id},
            **_HTMX_HEADERS,
        )
        assert len(mail.outbox) == 0

    def test_case_d_response_contains_already_subscribed_and_region_name(self) -> None:
        """Case D: response fragment contains 'already subscribed' and the region name."""
        account = AccountFactory.create(
            user__email="active2@example.com", is_verified=True
        )
        region = MicroRegionFactory.create(name="Zermatt Region")
        SubscriptionFactory.create(account=account, region=region)
        client = Client()
        response = client.post(
            reverse("accounts:subscribe"),
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
        """New account (Case A) has acquisition_request populated."""
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
                reverse("accounts:subscribe"),
                data={"email": "newuser@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        account = Account.objects.get(user__email="newuser@example.com")
        assert account.acquisition_request is not None
        assert account.acquisition_request.country_code == "CH"

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
                reverse("accounts:subscribe"),
                data={"email": "newuser2@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        account = Account.objects.get(user__email="newuser2@example.com")
        subscription = Subscription.objects.get(account=account, region=region)
        assert subscription.subscribed_via is not None
        assert subscription.subscribed_via.country_code == "DE"

    def test_acquisition_request_first_observation_wins(self) -> None:
        """Re-submitting does not overwrite acquisition_request on Account."""
        from unittest.mock import patch

        from bulletins.services.geoip import GeoLookup

        region = MicroRegionFactory.create()
        email = "returning@example.com"

        # First call (Case A: new account).
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
                reverse("accounts:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        original_request_id = Account.objects.get(
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
                reverse("accounts:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        # acquisition_request unchanged.
        sub = Account.objects.get(user__email=email)
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
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:subscribe"),
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
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": "noip@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        props = calls["subscription_started"].args[2]
        assert "country_code" not in props


# ---------------------------------------------------------------------------
# subscribe_partial — geo-match classification (SNOW-278)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscribePartialGeoMatch:
    """Tests for geo_match_kind / geo_matched_region written by subscribe_partial.

    The geo_lookup call is patched at ``bulletins.services.geoip.geo_lookup``
    (the import site in ``core.models.RequestLogManager.from_request``).
    """

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def _square_polygon(self, x0: float, y0: float, x1: float, y1: float) -> dict:
        """Return a GeoJSON Polygon for the rectangle (x0,y0)→(x1,y1)."""
        return {
            "type": "Polygon",
            "coordinates": [
                [
                    [x0, y0],
                    [x1, y0],
                    [x1, y1],
                    [x0, y1],
                    [x0, y0],
                ]
            ],
        }

    def _make_geo_lookup(self, lon: float | None, lat: float | None) -> object:
        """Return a GeoLookup stub with the given coordinates."""
        from bulletins.services.geoip import GeoLookup

        return GeoLookup(
            country="CH",
            subdivision="VS",
            city="Sion",
            latitude=lat,
            longitude=lon,
            accuracy_radius_km=50,
        )

    def test_subscription_written_with_in_region_kind(self) -> None:
        """subscribe_partial sets geo_match_kind=in_region when inside the target."""
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 10, 10))
        geo = self._make_geo_lookup(lon=5.0, lat=5.0)

        with patch("bulletins.services.geoip.geo_lookup", return_value=geo):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": "geo-in@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        sub = Account.objects.get(user__email="geo-in@example.com")
        subscription = Subscription.objects.get(account=sub, region=region)
        assert subscription.geo_match_kind == Subscription.GeoMatchKind.IN_REGION
        assert subscription.geo_matched_region == region

    def test_subscription_written_with_in_neighbour_kind(self) -> None:
        """subscribe_partial sets geo_match_kind=in_neighbour when inside a neighbour."""
        target = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 5, 5))
        neighbour = MicroRegionFactory.create(
            boundary=self._square_polygon(10, 0, 15, 5)
        )
        target.neighbours.add(neighbour)
        geo = self._make_geo_lookup(lon=12.0, lat=2.0)

        with patch("bulletins.services.geoip.geo_lookup", return_value=geo):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": "geo-nb@example.com", "region_id": target.region_id},
                **_HTMX_HEADERS,
            )

        sub = Account.objects.get(user__email="geo-nb@example.com")
        subscription = Subscription.objects.get(account=sub, region=target)
        assert subscription.geo_match_kind == Subscription.GeoMatchKind.IN_NEIGHBOUR
        assert subscription.geo_matched_region == neighbour

    def test_subscription_written_with_elsewhere_kind(self) -> None:
        """subscribe_partial sets geo_match_kind=elsewhere when outside all regions."""
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 5, 5))
        geo = self._make_geo_lookup(lon=50.0, lat=50.0)

        with patch("bulletins.services.geoip.geo_lookup", return_value=geo):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": "geo-el@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        sub = Account.objects.get(user__email="geo-el@example.com")
        subscription = Subscription.objects.get(account=sub, region=region)
        assert subscription.geo_match_kind == Subscription.GeoMatchKind.ELSEWHERE
        assert subscription.geo_matched_region is None

    def test_subscription_written_with_unknown_kind_when_no_geo(self) -> None:
        """subscribe_partial sets geo_match_kind=unknown when geo_lookup returns None."""
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 5, 5))

        with patch("bulletins.services.geoip.geo_lookup", return_value=None):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": "geo-unk@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        sub = Account.objects.get(user__email="geo-unk@example.com")
        subscription = Subscription.objects.get(account=sub, region=region)
        assert subscription.geo_match_kind == Subscription.GeoMatchKind.UNKNOWN
        assert subscription.geo_matched_region is None

    def test_repeat_call_does_not_overwrite_geo_match_fields(self) -> None:
        """A second POST for the same (account, region) pair does not overwrite.

        The first call (Case A) sets geo_match_kind=in_region.  A repeat call
        (Case B — pending re-send) must leave the frozen fields untouched.
        """
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 10, 10))
        email = "geo-repeat@example.com"

        # First call — inside the region.
        geo_inside = self._make_geo_lookup(lon=5.0, lat=5.0)
        with patch("bulletins.services.geoip.geo_lookup", return_value=geo_inside):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        first_subscription = Subscription.objects.get(
            account__user__email=email, region=region
        )
        assert first_subscription.geo_match_kind == Subscription.GeoMatchKind.IN_REGION

        # Second call — outside the region (different geo).
        geo_outside = self._make_geo_lookup(lon=50.0, lat=50.0)
        with patch("bulletins.services.geoip.geo_lookup", return_value=geo_outside):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        # get_or_create with (account, region) uniqueness — existing row unchanged.
        refreshed = Subscription.objects.get(account__user__email=email, region=region)
        assert refreshed.geo_match_kind == Subscription.GeoMatchKind.IN_REGION

    def test_subscription_started_props_include_geo_match_kind(self) -> None:
        """subscription_started event includes geo_match_kind when resolved (Case A)."""
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 10, 10))
        geo = self._make_geo_lookup(lon=5.0, lat=5.0)

        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=geo),
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": "geo-props@example.com", "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        assert "subscription_started" in calls
        props = calls["subscription_started"].args[2]
        assert props.get("geo_match_kind") == "in_region"

    def test_subscription_started_props_include_region_match_true(self) -> None:
        """subscription_started event includes region_match=True for in_region (Case A)."""
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 10, 10))
        geo = self._make_geo_lookup(lon=5.0, lat=5.0)

        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=geo),
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:subscribe"),
                data={
                    "email": "geo-rm-true@example.com",
                    "region_id": region.region_id,
                },
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        props = calls["subscription_started"].args[2]
        assert props.get("region_match") is True

    def test_subscription_started_props_include_region_match_true_for_in_neighbour(
        self,
    ) -> None:
        """subscription_started event includes region_match=True for in_neighbour (Case A)."""
        target = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 5, 5))
        neighbour = MicroRegionFactory.create(
            boundary=self._square_polygon(10, 0, 15, 5)
        )
        target.neighbours.add(neighbour)
        geo = self._make_geo_lookup(lon=12.0, lat=2.0)

        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=geo),
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:subscribe"),
                data={
                    "email": "geo-rm-neighbour@example.com",
                    "region_id": target.region_id,
                },
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        props = calls["subscription_started"].args[2]
        assert props.get("region_match") is True

    def test_subscription_started_props_include_region_match_false_for_elsewhere(
        self,
    ) -> None:
        """subscription_started event includes region_match=False for elsewhere."""
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 5, 5))
        geo = self._make_geo_lookup(lon=50.0, lat=50.0)

        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=geo),
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:subscribe"),
                data={
                    "email": "geo-rm-false@example.com",
                    "region_id": region.region_id,
                },
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        props = calls["subscription_started"].args[2]
        assert props.get("region_match") is False

    def test_subscription_started_props_include_language_primary(self) -> None:
        """subscription_started event includes language_primary when non-empty (Case A)."""
        region = MicroRegionFactory.create(boundary=self._square_polygon(0, 0, 10, 10))
        geo = self._make_geo_lookup(lon=5.0, lat=5.0)

        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=geo),
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": "geo-lang@example.com", "region_id": region.region_id},
                HTTP_ACCEPT_LANGUAGE="de-CH,de;q=0.9,en;q=0.8",
                **_HTMX_HEADERS,
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        props = calls["subscription_started"].args[2]
        assert props.get("language_primary") == "de"


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

        AccountFactory.create(user__email="signin@example.com")
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
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:sign_in"),
                data={"email": "signin@example.com"},
            )

        calls = {c.args[0]: c for c in mock_track.call_args_list}
        assert "sign_in_requested" in calls
        props = calls["sign_in_requested"].args[2]
        assert props.get("country_code") == "IT"

    def test_sign_in_requested_omits_country_code_when_empty(self) -> None:
        """sign_in_requested omits country_code when geo lookup returns None."""
        from unittest.mock import patch

        AccountFactory.create(user__email="signin2@example.com")
        with (
            patch("bulletins.services.geoip.geo_lookup", return_value=None),
            patch("accounts.views.analytics.track") as mock_track,
        ):
            Client().post(
                reverse("accounts:sign_in"),
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

    def test_get_renders_confirm_page_without_activating(self) -> None:
        """SNOW-439: GET renders the confirm page and does NOT verify on its own."""
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        token = _valid_account_token("pending@example.com")
        client = Client()
        response = client.get(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 200
        # A POST form is present (the actual sign-in action).
        assert b"<form" in response.content
        assert b'method="post"' in response.content
        # No state change.
        acc = Account.objects.get(user__email="pending@example.com")
        assert not acc.is_verified
        assert acc.verified_at is None

    def test_get_does_not_log_in(self) -> None:
        """SNOW-439: no session is established on GET (no GET-verb account access)."""
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        token = _valid_account_token("pending@example.com")
        client = Client()
        client.get(reverse("accounts:account", kwargs={"token": token}))
        assert client.session.get("_auth_user_id") is None

    def test_get_confirm_page_sets_same_origin_referrer(self) -> None:
        """SNOW-438: the confirm page carries a same-origin policy so its POST passes CSRF."""
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        token = _valid_account_token("pending@example.com")
        client = Client()
        response = client.get(reverse("accounts:account", kwargs={"token": token}))
        assert response["Referrer-Policy"] == "same-origin"

    def test_post_activates_pending_subscriber(self) -> None:
        """POST from the confirm page verifies the unverified account."""
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        token = _valid_account_token("pending@example.com")
        client = Client()
        client.post(reverse("accounts:account", kwargs={"token": token}))
        acc = Account.objects.get(user__email="pending@example.com")
        assert acc.is_verified
        assert acc.verified_at is not None

    def test_post_redirects_to_manage_with_just_confirmed(self) -> None:
        """Successful POST redirects to /account/manage/?just_confirmed=1."""
        AccountFactory.create(user__email="redirect@example.com", is_verified=False)
        token = _valid_account_token("redirect@example.com")
        client = Client()
        response = client.post(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert response["Location"] == "/account/manage/?just_confirmed=1"

    def test_post_sets_confirmed_at_with_timezone(self) -> None:
        """verified_at timestamp has tzinfo set."""
        AccountFactory.create(user__email="tz@example.com", is_verified=False)
        token = _valid_account_token("tz@example.com")
        client = Client()
        client.post(reverse("accounts:account", kwargs={"token": token}))
        acc = Account.objects.get(user__email="tz@example.com")
        assert acc.verified_at is not None
        assert acc.verified_at.tzinfo is not None

    def test_post_sets_session(self) -> None:
        """Django auth session is established after a successful POST."""
        AccountFactory.create(user__email="session@example.com", is_verified=False)
        token = _valid_account_token("session@example.com")
        client = Client()
        client.post(reverse("accounts:account", kwargs={"token": token}))
        acc = Account.objects.get(user__email="session@example.com")
        assert client.session.get("_auth_user_id") == str(acc.user_id)

    def test_post_idempotent_on_re_click_does_not_re_stamp_confirmed_at(self) -> None:
        """Re-POSTing for an already-verified account does not re-stamp verified_at."""
        acc = AccountFactory.create(user__email="active@example.com", is_verified=True)
        acc.verified_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        acc.save(update_fields=["verified_at"])

        token = _valid_account_token("active@example.com")
        client = Client()
        response = client.post(reverse("accounts:account", kwargs={"token": token}))
        # Still redirects, not an error
        assert response.status_code == 302

        acc.refresh_from_db()
        assert acc.verified_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_post_active_subscriber_re_click_also_redirects(self) -> None:
        """Verified account POSTing the link again still gets redirected to manage."""
        acc = AccountFactory.create(user__email="active2@example.com", is_verified=True)
        acc.verified_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        acc.save(update_fields=["verified_at"])
        token = _valid_account_token("active2@example.com")
        client = Client()
        response = client.post(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert "/account/manage/" in response["Location"]

    def test_get_expired_token_returns_400(self) -> None:
        """Expired token renders link_expired.html with status 400 on GET."""
        with freeze_time("2026-01-01T00:00:00Z"):
            token = _valid_account_token("expired@example.com")
        future = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(
            seconds=settings.ACCOUNT_TOKEN_MAX_AGE + 1
        )
        with freeze_time(future):
            client = Client()
            response = client.get(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 400
        assert b"expired" in response.content.lower()

    def test_get_garbage_token_returns_400(self) -> None:
        """Garbage token string returns 400 on GET."""
        client = Client()
        response = client.get(
            reverse("accounts:account", kwargs={"token": "garbage-token"})
        )
        assert response.status_code == 400

    def test_post_garbage_token_returns_400(self) -> None:
        """Garbage token string returns 400 on POST — no activation path is reached."""
        client = Client()
        response = client.post(
            reverse("accounts:account", kwargs={"token": "garbage-token"})
        )
        assert response.status_code == 400

    def test_valid_token_unknown_email_returns_400(self) -> None:
        """Valid token for a deleted account returns 400."""
        token = _valid_account_token("ghost@example.com")
        client = Client()
        response = client.get(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 400

    def test_post_valid_token_unknown_email_returns_400(self) -> None:
        """A POST with a valid token for a deleted account also returns 400.

        The unknown-account branch runs before the GET/POST dispatch, so
        POST cannot reach an activation path for a token whose account no
        longer exists — this pins that guarantee against a future reordering.
        """
        token = _valid_account_token("ghost@example.com")
        client = Client()
        response = client.post(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 400

    def test_unsubscribe_token_at_account_endpoint_returns_400(self) -> None:
        """An unsubscribe token must not be accepted at the account endpoint."""
        token = generate_unsubscribe_token("ghost@example.com", "CH-4115")
        client = Client()
        response = client.get(reverse("accounts:account", kwargs={"token": token}))
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
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")


@pytest.mark.django_db
class TestManageViewRegisteredOnly:
    """A registered user with an Account but no Subscription rows still
    reaches the dashboard — no redirect loop (SNOW-434 regression).
    """

    def test_account_without_subscriptions_renders_manage(self) -> None:
        account = AccountFactory.create()
        client = Client()
        client.force_login(account.user)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        assert "no active subscriptions" in response.content.decode().lower()


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
        response = client.get(reverse("accounts:sign_in"))
        assert response.status_code == 200
        assert b"email" in response.content.lower()

    def test_authenticated_get_redirects_to_manage(self) -> None:
        """Authenticated account hitting sign-in is redirected to manage."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:sign_in"))
        assert response.status_code == 302
        assert "/account/manage/" in response["Location"]

    def test_post_known_email_sends_account_access_email(self) -> None:
        """Known email on POST → account access email sent."""
        AccountFactory.create(user__email="known@example.com")
        client = Client()
        response = client.post(
            reverse("accounts:sign_in"),
            data={"email": "known@example.com"},
        )
        assert response.status_code == 200
        assert len(mail.outbox) == 1
        assert "Snowdesk" in mail.outbox[0].subject

    def test_post_unknown_email_creates_subscriber_and_sends_email(self) -> None:
        """Unknown email on POST → account created, email sent."""
        client = Client()
        response = client.post(
            reverse("accounts:sign_in"),
            data={"email": "brandnew@example.com"},
        )
        assert response.status_code == 200
        assert len(mail.outbox) == 1
        assert Account.objects.filter(user__email="brandnew@example.com").exists()

    def test_post_known_email_response_identical_to_unknown(self) -> None:
        """Responses for known and unknown emails must be byte-equal (anti-enumeration)."""
        AccountFactory.create(user__email="exists@example.com")
        client = Client()
        resp_known = client.post(
            reverse("accounts:sign_in"),
            data={"email": "exists@example.com"},
        )
        resp_unknown = client.post(
            reverse("accounts:sign_in"),
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
            reverse("accounts:sign_in"),
            data={"email": "not-valid"},
        )
        assert response.status_code == 200
        assert b"valid email" in response.content.lower()

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit on sign-in POST returns 429."""
        from django.contrib.auth.models import AnonymousUser

        from accounts.views import sign_in_view

        rf = RequestFactory()
        request = rf.post(
            reverse("accounts:sign_in"),
            data={"email": "rl@example.com"},
        )
        request.user = AnonymousUser()  # noqa: B010 — set on test request object

        with patch(
            "accounts.views.get_usage",
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
        AccountFactory.create(user__email="known@example.com")
        client = Client()
        # Warm-up — first request pays template-cache and DB-connection cost.
        client.post(
            reverse("accounts:sign_in"),
            data={"email": "warm@example.com"},
        )

        n = 5
        known_times: list[float] = []
        unknown_times: list[float] = []
        for i in range(n):
            t0 = time.perf_counter()
            client.post(
                reverse("accounts:sign_in"),
                data={"email": "known@example.com"},
            )
            known_times.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            client.post(
                reverse("accounts:sign_in"),
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
        account = AccountFactory.create()
        region = MicroRegionFactory.create(name="Zermatt Region")
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        assert b"Zermatt Region" in response.content

    def test_get_shows_subscribed_region_id(self) -> None:
        """Authenticated GET shows the subscribed region's region_id."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        assert region.region_id.encode() in response.content

    def test_get_shows_resort_names_for_subscribed_region(self) -> None:
        """Authenticated GET lists resort names for subscribed regions."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        ResortFactory.create(region=region, name="Verbier")
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert b"Verbier" in response.content

    def test_get_does_not_show_non_subscribed_region(self) -> None:
        """Non-subscribed regions must not appear in the manage page."""
        account = AccountFactory.create()
        subscribed_region = MicroRegionFactory.create(name="Subscribed Region")
        MicroRegionFactory.create(name="Other Region Zephyr")
        SubscriptionFactory.create(account=account, region=subscribed_region)
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert b"Other Region Zephyr" not in response.content

    def test_get_shows_welcome_banner_when_just_confirmed(self) -> None:
        """?just_confirmed=1 querystring renders the welcome banner."""
        account = AccountFactory.create()
        MicroRegionFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage") + "?just_confirmed=1")
        assert response.status_code == 200
        assert b"confirmed" in response.content.lower()

    def test_get_no_welcome_banner_without_just_confirmed(self) -> None:
        """Without ?just_confirmed the welcome banner is absent."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        # The banner contains a specific phrase; assert it's absent
        assert b"Your subscription is confirmed" not in response.content

    def test_stale_session_redirects_to_sign_in(self) -> None:
        """A session whose user was deleted (full account removal) redirects to
        sign-in — the session no longer resolves to an authenticated user.

        (Deleting only the Account leaves an authenticated registered-only
        user, who now correctly sees the dashboard — see
        TestManageViewRegisteredOnly.)
        """
        account = AccountFactory.create()
        client = _make_session_client(account)
        user = account.user
        account.delete()
        user.delete()
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 302
        assert reverse("accounts:sign_in") in response["Location"]

    def test_get_shows_map_cta_link(self) -> None:
        """Authenticated manage page contains the 'Choose more regions on the map' link.

        SNOW-344: link now points at / (the canonical map page).
        """
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert b"map" in response.content.lower()
        assert b'href="/"' in response.content

    def test_card_shows_bulletin_link(self) -> None:
        """Each card links to the region's evergreen bulletin URL with today's date label."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)

        with freeze_time("2026-05-18"):
            response = client.get(reverse("accounts:manage"))

        assert response.status_code == 200
        bulletin_url = region.get_absolute_url().encode()
        assert bulletin_url in response.content
        # Date formatted as j N Y (day month year, no leading zero)
        assert b"18 May 2026" in response.content
        assert b"Open bulletin for" in response.content

    def test_card_shows_map_link(self) -> None:
        """Each card contains a direct link to /#<region_id> using the raw (uppercase) region_id.

        SNOW-344: the map URL is now / not /map/.
        """
        account = AccountFactory.create()
        region = MicroRegionFactory.create(region_id="CH-1234")
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))

        assert response.status_code == 200
        assert b"/#CH-1234" in response.content

    def test_card_shows_breadcrumb(self) -> None:
        """Each card renders the L1 (MajorRegion) and L2 (SubRegion) names in the breadcrumb."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))

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
        account = AccountFactory.create()
        region = MicroRegionFactory.create(region_id="CH-4115")
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))

        assert response.status_code == 200
        # Flag sprite use reference for CH
        assert b'href="#flag-ch"' in response.content
        # Case-preserved region_id appears in the badge
        assert b"CH-4115" in response.content

    def test_shows_telemetry_toggle_with_role_switch(self) -> None:
        """SNOW-387: the Anonymous usage data section renders a role="switch" toggle."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        assert b"data-telemetry-toggle" in response.content
        assert b'role="switch"' in response.content

    def test_telemetry_toggle_explainer_copy_present(self) -> None:
        """SNOW-387: the telemetry section explains what is (and isn't) collected."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert b"Anonymous usage data" in response.content
        assert b"No bulletin content" in response.content

    def test_telemetry_toggle_links_to_privacy_page(self) -> None:
        """SNOW-387: the telemetry copy links to the resolved privacy policy URL."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        privacy_url = reverse("public:privacy")
        assert privacy_url.encode() in response.content


# ---------------------------------------------------------------------------
# manage_view — "My favourites" section (SNOW-415)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManageViewFavouritesSection:
    """The 'My favourites' section lazy-loads favourites:list.

    Asserted purely via the reversed ``favourites:list`` URL appearing in
    the response HTML — this test module carries no import from the
    ``favourites`` app, matching ``manage_view`` itself.
    """

    def test_section_present(self) -> None:
        """The section always lazy-loads favourites:list."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        assert b"My favourites" in response.content
        assert reverse("favourites:list").encode() in response.content


# ---------------------------------------------------------------------------
# manage_view — "Sync log" panel (SNOW-482)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestManageViewSyncLogSection:
    """The flag-gated 'Sync log' panel next to the SNOW-378 reset control."""

    @override_flag("sync_log", active=True)
    def test_panel_present_when_flag_active(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        assert b'data-testid="sync-log-panel"' in response.content

    @override_flag("sync_log", active=False)
    def test_panel_absent_when_flag_inactive(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:manage"))
        assert response.status_code == 200
        assert b'data-testid="sync-log-panel"' not in response.content


# ---------------------------------------------------------------------------
# remove_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveRegion:
    """Tests for the remove_region HTMX view."""

    def test_removes_subscription_row(self) -> None:
        """Session-authenticated POST removes the Subscription row."""
        account = AccountFactory.create()
        region1 = MicroRegionFactory.create()
        region2 = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region1)
        SubscriptionFactory.create(account=account, region=region2)
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:remove_region",
                kwargs={"region_id": region1.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert not Subscription.objects.filter(account=account, region=region1).exists()
        # Other subscription retained
        assert Subscription.objects.filter(account=account, region=region2).exists()

    def test_last_region_keeps_account_and_user(self) -> None:
        """Removing the last region leaves the User and Account intact."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        account_pk = account.pk
        user_pk = account.user_id
        client = _make_session_client(account)
        client.post(
            reverse(
                "accounts:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert Account.objects.filter(pk=account_pk).exists()
        assert User.objects.filter(pk=user_pk).exists()
        assert not Subscription.objects.filter(account_id=account_pk).exists()

    def test_last_region_keeps_session_and_returns_empty_200(self) -> None:
        """Removing the last region stays signed in — no redirect, no HX-Redirect."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert "HX-Redirect" not in response
        assert "_auth_user_id" in client.session

    def test_no_session_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse(
                "accounts:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:remove_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
        )
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("accounts:remove_region", kwargs={"region_id": "ch-0001"}),
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from accounts.views import remove_region

        response = remove_region(request, region_id="ch-0001")
        assert response.status_code == 429

    def test_region_not_held_returns_200_and_does_not_delete_subscriber(self) -> None:
        """POST for a region the account never held returns benign 200; no data changed.

        The manage-page card list never showed the region, so a benign empty 200
        is the correct response — matching the "card removed via outerHTML swap"
        semantics — while ensuring the account is not hard-deleted.
        """
        account = AccountFactory.create()
        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        region_c = MicroRegionFactory.create()  # account does NOT hold region_c
        SubscriptionFactory.create(account=account, region=region_a)
        SubscriptionFactory.create(account=account, region=region_b)
        sub_pk = account.pk
        client = _make_session_client(account)

        response = client.post(
            reverse(
                "accounts:remove_region",
                kwargs={"region_id": region_c.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )

        assert response.status_code == 200
        # Existing subscriptions must be untouched.
        assert Subscription.objects.filter(account=account, region=region_a).exists()
        assert Subscription.objects.filter(account=account, region=region_b).exists()
        # Account must not have been hard-deleted.
        assert Account.objects.filter(pk=sub_pk).exists()


# ---------------------------------------------------------------------------
# delete_account
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteAccount:
    """Tests for the delete_account HTMX view."""

    def test_hard_deletes_account(self) -> None:
        """Session-authenticated POST hard-deletes the account."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        account_pk = account.pk
        client = _make_session_client(account)
        client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert not Account.objects.filter(pk=account_pk).exists()

    def test_works_for_registered_only_account_with_no_subscriptions(self) -> None:
        """delete_account is the sole hard-delete path, available to ANY authenticated
        account — including a registered-only account (SNOW-430) with zero
        Subscription rows.
        """
        account = AccountFactory.create()
        account_pk = account.pk
        user_pk = account.user_id
        client = _make_session_client(account)
        response = client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 200
        assert not Account.objects.filter(pk=account_pk).exists()
        assert not User.objects.filter(pk=user_pk).exists()

    def test_cascades_subscription_rows(self) -> None:
        """Account deletion cascades to Subscription rows."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        sub = SubscriptionFactory.create(account=account, region=region)
        sub_pk = sub.pk
        client = _make_session_client(account)
        client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert not Subscription.objects.filter(pk=sub_pk).exists()

    def test_clears_session(self) -> None:
        """Session is cleared after account deletion."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert "_auth_user_id" not in client.session

    def test_responds_with_hx_redirect(self) -> None:
        """Response includes HX-Redirect header pointing to unsubscribe-done."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 200
        assert "HX-Redirect" in response
        assert "unsubscribe" in response["HX-Redirect"]

    def test_no_session_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        client = Client()
        response = client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.post(reverse("accounts:delete_account"))
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(reverse("accounts:delete_account"))
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from accounts.views import delete_account

        response = delete_account(request)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# sign_out
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignOut:
    """Tests for the sign_out view."""

    def test_clears_session_and_redirects(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.post(reverse("accounts:sign_out"))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")
        assert "_auth_user_id" not in client.session

    def test_get_not_allowed(self) -> None:
        client = Client()
        response = client.get(reverse("accounts:sign_out"))
        assert response.status_code == 405

    def test_works_when_not_signed_in(self) -> None:
        client = Client()
        response = client.post(reverse("accounts:sign_out"))
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# unsubscribe_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsubscribeView:
    """Tests for the unsubscribe_view."""

    def test_get_valid_token_renders_confirmation(self) -> None:
        """Valid token GET renders the unsubscribe confirmation page."""
        account = AccountFactory.create(user__email="unsub@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        token = generate_unsubscribe_token("unsub@example.com", region.region_id)
        client = Client()
        response = client.get(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert response.status_code == 200
        assert b"unsubscribe" in response.content.lower()

    def test_post_valid_token_removes_subscription(self) -> None:
        """Valid token POST deletes the matching Subscription row."""
        account = AccountFactory.create(user__email="unsub2@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        token = generate_unsubscribe_token("unsub2@example.com", region.region_id)
        client = Client()
        response = client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert response.status_code == 200
        assert not Subscription.objects.filter(account=account, region=region).exists()

    def test_post_last_subscription_keeps_account(self) -> None:
        """Removing the last subscription leaves the User and Account intact."""
        account = AccountFactory.create(user__email="lastregion@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        account_pk = account.pk
        user_pk = account.user_id
        token = generate_unsubscribe_token("lastregion@example.com", region.region_id)
        client = Client()
        client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert Account.objects.filter(pk=account_pk).exists()
        assert User.objects.filter(pk=user_pk).exists()
        assert not Subscription.objects.filter(account_id=account_pk).exists()

    def test_post_not_last_subscription_keeps_subscriber(self) -> None:
        """Removing one of multiple subscriptions keeps the account."""
        account = AccountFactory.create(user__email="keep@example.com")
        region1 = MicroRegionFactory.create()
        region2 = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region1)
        SubscriptionFactory.create(account=account, region=region2)
        token = generate_unsubscribe_token("keep@example.com", region1.region_id)
        client = Client()
        client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert Account.objects.filter(user__email="keep@example.com").exists()
        assert Subscription.objects.filter(account=account, region=region2).exists()

    def test_post_idempotent_when_already_deleted(self) -> None:
        """Re-submitting after account deletion renders done page without error."""
        account = AccountFactory.create(user__email="gone@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        token = generate_unsubscribe_token("gone@example.com", region.region_id)
        account.delete()
        client = Client()
        response = client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert response.status_code == 200
        assert b"unsubscribed" in response.content.lower()

    def test_post_last_subscription_does_not_touch_an_existing_session(self) -> None:
        """The unauthenticated token path makes no session change on last-region removal.

        A user who is already signed in (e.g. clicked an old unsubscribe email
        link from their own inbox while logged in elsewhere in the same
        browser) stays signed in — unsubscribe_view never calls login/logout.
        """
        account = AccountFactory.create(user__email="stillin@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        token = generate_unsubscribe_token("stillin@example.com", region.region_id)
        client = _make_session_client(account)
        client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert "_auth_user_id" in client.session

    def test_bad_token_returns_400(self) -> None:
        """Garbage token returns 400."""
        client = Client()
        response = client.get(
            reverse("accounts:unsubscribe", kwargs={"token": "garbage"})
        )
        assert response.status_code == 400

    def test_unsubscribe_token_does_not_expire(self) -> None:
        """Unsubscribe tokens must remain valid regardless of age."""
        account = AccountFactory.create(user__email="old@example.com")
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)

        with freeze_time("2020-01-01T00:00:00Z"):
            token = generate_unsubscribe_token("old@example.com", region.region_id)

        with freeze_time("2025-06-01T00:00:00Z"):
            client = Client()
            response = client.get(
                reverse("accounts:unsubscribe", kwargs={"token": token})
            )
        assert response.status_code == 200

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        region = MicroRegionFactory.create()
        token = generate_unsubscribe_token("rl@example.com", region.region_id)
        request = rf.get(reverse("accounts:unsubscribe", kwargs={"token": token}))
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from accounts.views import unsubscribe_view

        response = unsubscribe_view(request, token=token)
        assert response.status_code == 429

    def test_cross_salt_token_returns_400(self) -> None:
        """An account-access token must not be accepted at the unsubscribe endpoint."""
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        client = Client()
        response = client.get(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# unsubscribe_done_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsubscribeDoneView:
    """Tests for the standalone unsubscribe_done_view."""

    def test_get_renders_done_page(self) -> None:
        """GET /account/unsubscribe-done/ renders the done page."""
        client = Client()
        response = client.get(reverse("accounts:unsubscribe_done"))
        assert response.status_code == 200
        assert b"unsubscribed" in response.content.lower()


# ---------------------------------------------------------------------------
# Email normalisation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmailNormalisation:
    """Tests for email normalisation at the form boundary.

    Verifies that case variants and whitespace are collapsed before the
    account lookup so duplicate accounts cannot be created via case
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
            reverse("accounts:subscribe"),
            data={"email": email, "region_id": region_id},
            HTTP_HX_REQUEST="true",
        )

    def test_uppercase_and_lowercase_same_address_creates_one_subscriber(self) -> None:
        """Two POSTs for the same address in different case create one Account."""
        region = MicroRegionFactory.create()
        self._subscribe("User@Example.com", region.region_id)
        self._subscribe("user@example.com", region.region_id)
        assert Account.objects.filter(user__email="user@example.com").count() == 1
        assert Account.objects.count() == 1

    def test_mixed_case_address_is_stored_lowercase(self) -> None:
        """The stored email address is the lowercase-normalised form."""
        region = MicroRegionFactory.create()
        self._subscribe("ALICE@EXAMPLE.COM", region.region_id)
        assert Account.objects.filter(user__email="alice@example.com").exists()

    def test_sign_in_post_looks_up_normalised_email(self) -> None:
        """sign_in_view POST for a mixed-case address finds the lowercase account."""
        account = AccountFactory.create(user__email="bob@example.com", is_verified=True)
        client = Client()
        with patch("accounts.views.send_account_access_email") as mock_send:
            client.post(
                reverse("accounts:sign_in"),
                data={"email": "BOB@EXAMPLE.COM"},
            )
        mock_send.assert_called_once_with(
            account.user.email, request=mock_send.call_args[1]["request"]
        )

    def test_account_view_resolves_mixed_case_token_to_lowercase_subscriber(
        self,
    ) -> None:
        """A token generated for a mixed-case email verifies the lowercase account.

        verify_token returns the raw value embedded in the token, which may
        come from a mixed-case email.  account_view must normalise it before
        the database lookup so the stored lowercase record is found.
        """
        account = AccountFactory.create(user__email="foo@bar.com", is_verified=False)
        token = generate_token("FOO@BAR.com", salt=SALT_ACCOUNT_ACCESS)
        client = Client()
        response = client.post(reverse("accounts:account", kwargs={"token": token}))
        # Should redirect to manage page, not render a link-expired 400.
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:manage"))
        account.refresh_from_db()
        assert account.is_verified

    def test_unsubscribe_post_resolves_mixed_case_token(self) -> None:
        """An unsubscribe token generated for a mixed-case email removes the subscription.

        verify_unsubscribe_token now lowercases the email component, and
        unsubscribe_view POSTs against email.lower(), so the subscription
        for the lowercase-stored account is deleted correctly.
        """
        account = AccountFactory.create(user__email="foo@bar.com", is_verified=True)
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        token = generate_unsubscribe_token("FOO@BAR.com", region.region_id)
        client = Client()
        response = client.post(
            reverse("accounts:unsubscribe", kwargs={"token": token}),
        )
        assert response.status_code == 200
        assert not Subscription.objects.filter(account=account, region=region).exists()


class TestEmailFormNormalisation:
    """Unit tests for SubscribeForm and EmailForm clean_email."""

    def test_subscribe_form_lowercases_email(self) -> None:
        """SubscribeForm.clean_email returns a lowercased address."""
        from accounts.forms import SubscribeForm

        form = SubscribeForm(data={"email": "TEST@EXAMPLE.COM", "region_id": "CH-0001"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_subscribe_form_strips_whitespace(self) -> None:
        """SubscribeForm.clean_email strips leading and trailing whitespace."""
        from accounts.forms import SubscribeForm

        form = SubscribeForm(
            data={"email": "  user@example.com  ", "region_id": "CH-0001"}
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "user@example.com"

    def test_email_form_lowercases_email(self) -> None:
        """EmailForm.clean_email returns a lowercased address."""
        from accounts.forms import EmailForm

        form = EmailForm(data={"email": "TEST@EXAMPLE.COM"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_email_form_strips_whitespace(self) -> None:
        """EmailForm.clean_email strips leading and trailing whitespace."""
        from accounts.forms import EmailForm

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
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert Subscription.objects.filter(account=account, region=region).exists()

    def test_authenticated_returns_success_added_fragment(self) -> None:
        """Response contains the subscribe_success_added fragment content."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create(name="Davos Region")
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert b"Davos Region" in response.content
        assert b"Added" in response.content

    def test_idempotent_second_post_returns_success(self) -> None:
        """POSTing twice does not raise IntegrityError — returns success fragment."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        client = _make_session_client(account)
        url = reverse(
            "accounts:add_region", kwargs={"region_id": region.region_id.lower()}
        )
        client.post(url, **_HTMX_HEADERS)
        response = client.post(url, **_HTMX_HEADERS)
        assert response.status_code == 200
        # Exactly one Subscription row (idempotent).
        assert Subscription.objects.filter(account=account, region=region).count() == 1

    def test_unauthenticated_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse(
                "accounts:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:add_region",
                kwargs={"region_id": region.region_id.lower()},
            ),
        )
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse("accounts:add_region", kwargs={"region_id": "ch-0001"}),
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from accounts.views import add_region

        response = add_region(request, region_id="ch-0001")
        assert response.status_code == 429

    def test_unknown_region_returns_400(self) -> None:
        """POST with a region_id that does not exist returns 400 error fragment."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.post(
            reverse("accounts:add_region", kwargs={"region_id": "xx-9999"}),
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
        """Multi-region account: removes target subscription, returns confirmation fragment."""
        account = AccountFactory.create()
        region1 = MicroRegionFactory.create(name="Davos Region")
        region2 = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region1)
        SubscriptionFactory.create(account=account, region=region2)
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:remove_region_from_bulletin",
                kwargs={"region_id": region1.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert not Subscription.objects.filter(account=account, region=region1).exists()
        # Other subscription retained.
        assert Subscription.objects.filter(account=account, region=region2).exists()
        assert b"unsubscribed" in response.content.lower()
        assert b"Davos Region" in response.content

    def test_last_region_keeps_account_and_session(self) -> None:
        """Last-region removal leaves the Account and session intact — no redirect."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        account_pk = account.pk
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:remove_region_from_bulletin",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 200
        assert Account.objects.filter(pk=account_pk).exists()
        assert "HX-Redirect" not in response
        assert "_auth_user_id" in client.session
        assert b"unsubscribed" in response.content.lower()

    def test_unauthenticated_returns_403(self) -> None:
        """Unauthenticated POST returns 403."""
        region = MicroRegionFactory.create()
        client = Client()
        response = client.post(
            reverse(
                "accounts:remove_region_from_bulletin",
                kwargs={"region_id": region.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        """Non-HTMX POST returns 400."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:remove_region_from_bulletin",
                kwargs={"region_id": region.region_id.lower()},
            ),
        )
        assert response.status_code == 400

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(
            reverse(
                "accounts:remove_region_from_bulletin",
                kwargs={"region_id": "ch-0001"},
            ),
        )
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from accounts.views import remove_region_from_bulletin

        response = remove_region_from_bulletin(request, region_id="ch-0001")
        assert response.status_code == 429

    def test_unknown_region_returns_400(self) -> None:
        """POST with a region_id that does not exist returns 400 error fragment."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.post(
            reverse(
                "accounts:remove_region_from_bulletin",
                kwargs={"region_id": "xx-9999"},
            ),
            **_HTMX_HEADERS,
        )
        assert response.status_code == 400
        assert b"went wrong" in response.content.lower()

    def test_region_not_held_returns_400_and_does_not_delete_subscriber(self) -> None:
        """POST for a region the account never held returns 400; no data is changed.

        Protects against the cascade bug where a zero-row delete could trigger
        a hard-delete of the account when they happened to have no subscriptions
        for unrelated reasons (e.g. a stale or forged POST to a region_id the
        account never held).
        """
        account = AccountFactory.create()
        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        region_c = MicroRegionFactory.create()  # account does NOT hold region_c
        SubscriptionFactory.create(account=account, region=region_a)
        SubscriptionFactory.create(account=account, region=region_b)
        sub_pk = account.pk
        client = _make_session_client(account)

        response = client.post(
            reverse(
                "accounts:remove_region_from_bulletin",
                kwargs={"region_id": region_c.region_id.lower()},
            ),
            **_HTMX_HEADERS,
        )

        assert response.status_code == 400
        assert b"went wrong" in response.content.lower()
        # Existing subscriptions must be untouched.
        assert Subscription.objects.filter(account=account, region=region_a).exists()
        assert Subscription.objects.filter(account=account, region=region_b).exists()
        # Account must not have been hard-deleted.
        assert Account.objects.filter(pk=sub_pk).exists()


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
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:subscribe"),
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
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:subscribe"),
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
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:subscribe"),
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
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:subscribe"),
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
    """analytics.track('subscription_confirmed') fires when an unverified account verifies."""

    def test_fires_on_pending_confirmation(self) -> None:
        account = AccountFactory.create(is_verified=False)
        token = _valid_account_token(account.user.email)
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(reverse("accounts:account", kwargs={"token": token}))
        calls = [
            c
            for c in mock_track.call_args_list
            if c.args[0] == "subscription_confirmed"
        ]
        assert len(calls) == 1
        assert calls[0].args[1] == str(account.uuid)
        props = calls[0].args[2]
        assert "hours_since_started" in props

    def test_does_not_fire_on_get(self) -> None:
        """SNOW-439: GET renders the confirm page only — no confirmation event."""
        account = AccountFactory.create(is_verified=False)
        token = _valid_account_token(account.user.email)
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.get(reverse("accounts:account", kwargs={"token": token}))
        calls = [
            c
            for c in mock_track.call_args_list
            if c.args[0] == "subscription_confirmed"
        ]
        assert len(calls) == 0

    def test_does_not_fire_on_already_active(self) -> None:
        account = AccountFactory.create(is_verified=True)
        token = _valid_account_token(account.user.email)
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(reverse("accounts:account", kwargs={"token": token}))
        calls = [
            c
            for c in mock_track.call_args_list
            if c.args[0] == "subscription_confirmed"
        ]
        assert len(calls) == 0

    def test_alias_called_when_anon_id_in_session(self) -> None:
        account = AccountFactory.create(is_verified=False)
        token = _valid_account_token(account.user.email)
        client = Client()
        session = client.session
        session["analytics_anon_id"] = "anon-uuid-111"
        session.save()
        with patch("accounts.views.analytics.alias") as mock_alias:
            client.post(reverse("accounts:account", kwargs={"token": token}))
        mock_alias.assert_called_once_with(
            distinct_id=str(account.uuid),
            alias_id="anon-uuid-111",
        )

    def test_alias_not_called_without_anon_id(self) -> None:
        account = AccountFactory.create(is_verified=False)
        token = _valid_account_token(account.user.email)
        client = Client()
        with patch("accounts.views.analytics.alias") as mock_alias:
            client.post(reverse("accounts:account", kwargs={"token": token}))
        mock_alias.assert_not_called()


@pytest.mark.django_db
class TestAnalyticsRegionAdded:
    """analytics.track('region_added') fires in add_region and subscribe Case C."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Use in-memory email backend."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_fires_in_add_region(self) -> None:
        account = AccountFactory.create(is_verified=True)
        region = MicroRegionFactory.create()
        client = _make_session_client(account)
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "accounts:add_region",
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
        account = AccountFactory.create(
            user__email="active@example.com", is_verified=True
        )
        region = MicroRegionFactory.create()
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:subscribe"),
                data={"email": account.user.email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )
        calls = [c for c in mock_track.call_args_list if c.args[0] == "region_added"]
        assert len(calls) == 1
        props = calls[0].args[2]
        assert props["region_id"] == region.region_id

    def test_not_fired_on_duplicate_add(self) -> None:
        account = AccountFactory.create(is_verified=True)
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "accounts:add_region",
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
        account = AccountFactory.create()
        region_a = MicroRegionFactory.create()
        region_b = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region_a)
        SubscriptionFactory.create(account=account, region=region_b)
        client = _make_session_client(account)
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "accounts:remove_region",
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
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse(
                    "accounts:remove_region",
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
        account = AccountFactory.create()
        distinct_id = str(account.uuid)
        client = _make_session_client(account)
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "unsubscribed"]
        assert len(calls) == 1
        assert calls[0].args[1] == distinct_id
        props = calls[0].args[2]
        assert props["reason"] == "account_deleted"
        assert "account_age_days" in props

    def test_fires_in_unsubscribe_view(self) -> None:
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        distinct_id = str(account.uuid)
        token = generate_unsubscribe_token(account.user.email, region.region_id)
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        calls = [c for c in mock_track.call_args_list if c.args[0] == "unsubscribed"]
        assert len(calls) == 1
        assert calls[0].args[1] == distinct_id
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
        account = AccountFactory.create(user__email="known@example.com")
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:sign_in"),
                data={"email": "known@example.com"},
            )
        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "sign_in_requested"
        ]
        assert len(calls) == 1
        assert calls[0].args[1] == str(account.uuid)

    def test_fires_for_unknown_email_after_account_created(self) -> None:
        """POST with a fresh email creates an Account and fires sign_in_requested with the new PK."""
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:sign_in"),
                data={"email": "brandnew@example.com"},
            )
        new_account = Account.objects.get(user__email="brandnew@example.com")
        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "sign_in_requested"
        ]
        assert len(calls) == 1
        assert calls[0].args[1] == str(new_account.uuid)

    def test_does_not_fire_on_invalid_email(self) -> None:
        """POST with an invalid email re-renders the form and does not fire the event."""
        client = Client()
        with patch("accounts.views.analytics.track") as mock_track:
            client.post(
                reverse("accounts:sign_in"),
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
        """Case A (new account): log record contains pk=, not the full email address.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "caplog-new@example.com"
        region = MicroRegionFactory.create()

        with caplog.at_level(logging.INFO, logger="accounts.views"):
            Client().post(
                reverse("accounts:subscribe"),
                data={"email": email, "region_id": region.region_id},
                **_HTMX_HEADERS,
            )

        account = Account.objects.get(user__email=email)
        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email address must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # At least one record must mention the account's pk.
        assert any(str(account.pk) in msg for msg in all_messages), (
            f"No log record contains pk={account.pk}; records: {all_messages}"
        )


@pytest.mark.django_db
class TestAccountViewLogging:
    """SNOW-311: account_view logs masked email for unknown-email path."""

    def test_unknown_email_path_logs_masked_not_plaintext(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A valid token for an email with no account row logs the masked form.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "unknown-caplog@example.com"
        token = _valid_account_token(email)

        with caplog.at_level(logging.WARNING, logger="accounts.views"):
            Client().get(f"/account/access/{token}/")

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
        """sign_in_view POST logs account pk=, not the full email address.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "signin-caplog@example.com"

        with caplog.at_level(logging.INFO, logger="accounts.views"):
            Client().post(
                reverse("accounts:sign_in"),
                data={"email": email},
            )

        account = Account.objects.get(user__email=email)
        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # At least one record must mention the account's pk.
        assert any(str(account.pk) in msg for msg in all_messages), (
            f"No log record contains pk={account.pk}; records: {all_messages}"
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

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "delete-caplog@example.com"
        account = AccountFactory.create(user__email=email)
        client = _make_session_client(account)

        with caplog.at_level(logging.INFO, logger="accounts.views"):
            client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)

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
    """SNOW-311: unsubscribe_view never logs a plaintext email address."""

    def test_last_subscription_removal_logs_no_plaintext_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Removing the last subscription logs the account pk, never the plaintext email.

        The account survives the request (only the Subscription row is
        deleted), so the success log line is pk-based already — this guards
        against a future regression that logs the raw address.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "unsub-caplog@example.com"
        account = AccountFactory.create(user__email=email)
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        token = generate_unsubscribe_token(email, region.region_id)

        with caplog.at_level(logging.INFO, logger="accounts.views"):
            Client().post(reverse("accounts:unsubscribe", kwargs={"token": token}))

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # At least one record must mention the account's pk.
        assert any(str(account.pk) in msg for msg in all_messages), (
            f"No log record contains pk={account.pk}; records: {all_messages}"
        )

    def test_already_removed_logs_masked_not_plaintext(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Re-submitting for a since-deleted Account logs the masked email.

        Hits the idempotent Account.DoesNotExist branch, the only path that
        still logs a masked email (the account itself is gone, so pk is
        unavailable).
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "gone-caplog@example.com"
        account = AccountFactory.create(user__email=email)
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        token = generate_unsubscribe_token(email, region.region_id)
        account.delete()

        with caplog.at_level(logging.INFO, logger="accounts.views"):
            Client().post(reverse("accounts:unsubscribe", kwargs={"token": token}))

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # The masked form g***@example.com must appear in at least one record.
        assert any("g***@example.com" in msg for msg in all_messages), (
            f"Masked email not found in any log record; records: {all_messages}"
        )
