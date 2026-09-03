"""
tests/accounts/test_views.py — Tests for accounts views.

Covers:
  account_view        — valid token verifies an unverified account; redirects
                        to manage with ?just_confirmed=1; idempotent on
                        re-click; bad/expired token → 400.
  delete_account      — hard-deletes account; clears session; HX-Redirect to done;
                        no session → 403; non-HTMX → 400; sweeps the Location
                        a deleted favourite was the last referent of, while
                        leaving shared and curated locations alone.
  unsubscribe_view    — valid token GET/POST removes the region pin
                        (SNOW-802); idempotent; bad token → 400; rate-limit
                        429.
  unsubscribe_done_view — GET renders done page.
  caplog regression   — plaintext emails never appear in log output; pk=/masked
                        forms appear instead; covers account_view,
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
from pytest_django.fixtures import Settings
from waffle.testutils import override_flag

from apps.accounts.models import Account
from apps.accounts.services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_token,
    generate_unsubscribe_token,
)
from apps.favourites.models import Favourite
from apps.locations.models import Location
from tests.factories import (
    AccountFactory,
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"


def _make_session_client(account: Account) -> Client:
    """Return a test client logged in as the account's User via Django auth."""
    client = Client()
    client.force_login(account.user, backend=_TOKEN_BACKEND)
    return client


def _region_pins(account: Account, region: Any) -> Any:
    """The account's region pin(s) for ``region`` — what an unsubscribe removes."""
    return Favourite.objects.for_user(account.user).region_pins().filter(region=region)


def _valid_account_token(email: str) -> str:
    """Generate a fresh, valid account-access token."""
    return generate_token(email, salt=SALT_ACCOUNT_ACCESS)


# ---------------------------------------------------------------------------
# sign_in_view — RequestLog wiring (SNOW-277)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignInViewRequestLog:
    """Tests for RequestLog capture in sign_in_view."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: Settings) -> None:
        """Use in-memory email backend."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sign_in_requested_includes_country_code(self) -> None:
        """sign_in_requested event includes country_code when non-empty."""
        from unittest.mock import patch

        from apps.bulletins.services.geoip import GeoLookup

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
            patch("apps.bulletins.services.geoip.geo_lookup", return_value=fake_geo),
            patch("apps.accounts.views.analytics.track") as mock_track,
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
            patch("apps.bulletins.services.geoip.geo_lookup", return_value=None),
            patch("apps.accounts.views.analytics.track") as mock_track,
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
    def use_locmem_backend(self, settings: Settings) -> None:
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

    def test_post_redirects_to_the_map_with_the_pins_sheet(self) -> None:
        """Successful POST lands on the map, pins sheet open (SNOW-802)."""
        AccountFactory.create(user__email="redirect@example.com", is_verified=False)
        token = _valid_account_token("redirect@example.com")
        client = Client()
        response = client.post(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert response["Location"] == "/?panel=favourites"

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
        assert response["Location"] == "/?panel=favourites"

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
# hub_view (unauthenticated)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignInView:
    """Tests for the dedicated sign_in_view."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: Settings) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_get_returns_200_with_email_form(self) -> None:
        """GET renders the email entry form."""
        client = Client()
        response = client.get(reverse("accounts:sign_in"))
        assert response.status_code == 200
        assert b"email" in response.content.lower()

    def test_authenticated_get_redirects_to_the_map(self) -> None:
        """Authenticated account hitting sign-in is redirected to manage."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:sign_in"))
        assert response.status_code == 302
        assert response["Location"] == reverse("public:home")

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

        from apps.accounts.views import sign_in_view

        rf = RequestFactory()
        request = rf.post(
            reverse("accounts:sign_in"),
            data={"email": "rl@example.com"},
        )
        request.user = AnonymousUser()  # noqa: B010 — set on test request object

        with patch(
            "apps.accounts.views.get_usage",
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
# settings_view — "Sync log" panel (SNOW-482)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSettingsViewSyncLogSection:
    """The flag-gated 'Sync log' panel next to the SNOW-378 reset control."""

    @override_flag("sync_log", active=True)
    def test_panel_present_when_flag_active(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:settings"))
        assert response.status_code == 200
        assert b'data-testid="sync-log-panel"' in response.content

    @override_flag("sync_log", active=False)
    def test_panel_absent_when_flag_inactive(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account)
        response = client.get(reverse("accounts:settings"))
        assert response.status_code == 200
        assert b'data-testid="sync-log-panel"' not in response.content


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
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        account_pk = account.pk
        client = _make_session_client(account)
        client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert not Account.objects.filter(pk=account_pk).exists()

    def test_works_for_registered_only_account_with_no_subscriptions(self) -> None:
        """delete_account is the sole hard-delete path, available to ANY authenticated
        account — including a registered-only account (SNOW-430) with zero
        region pins.
        """
        account = AccountFactory.create()
        account_pk = account.pk
        user_pk = account.user_id
        client = _make_session_client(account)
        response = client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert response.status_code == 200
        assert not Account.objects.filter(pk=account_pk).exists()
        assert not User.objects.filter(pk=user_pk).exists()

    def test_cascades_region_pins(self) -> None:
        """Account deletion takes the user's region pins with it (SNOW-802)."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        pin = FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        pin_pk = pin.pk
        client = _make_session_client(account)
        client.post(reverse("accounts:delete_account"), **_HTMX_HEADERS)
        assert not Favourite.objects.filter(pk=pin_pk).exists()

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

    def test_deletes_the_location_only_the_favourite_referenced(self) -> None:
        """A favourite's minted Location is a saved item and goes with it.

        ``Favourite.user`` is CASCADE, so the bulk cascade removes the
        favourite rows without running any Python — which left the anonymous
        Location behind holding the person's real coordinates and elevation,
        referenced by nothing. Deletion now runs through the favourites
        service so the orphan sweep happens.
        """
        account = AccountFactory.create()
        location = LocationFactory.create(anonymous=True)
        favourite = FavouriteFactory.create(user=account.user, location=location)

        _make_session_client(account).post(
            reverse("accounts:delete_account"), **_HTMX_HEADERS
        )

        assert not Favourite.objects.filter(pk=favourite.pk).exists()
        assert not Location.objects.filter(pk=location.pk).exists()

    def test_keeps_a_location_something_else_still_references(self) -> None:
        """Erasure takes the person's rows, not everybody's.

        A location another account's favourite also pins, and a curated
        named location, both survive.
        """
        account = AccountFactory.create()
        shared = LocationFactory.create(anonymous=True)
        FavouriteFactory.create(user=account.user, location=shared)
        bystander = FavouriteFactory.create(location=shared)
        curated = LocationFactory.create(name="Mont Fort")
        FavouriteFactory.create(user=account.user, location=curated)

        _make_session_client(account).post(
            reverse("accounts:delete_account"), **_HTMX_HEADERS
        )

        assert Location.objects.filter(pk=shared.pk).exists()
        assert Location.objects.filter(pk=curated.pk).exists()
        assert Favourite.objects.filter(pk=bystander.pk).exists()

    def test_rate_limit_returns_429(self) -> None:
        """Exceeding rate limit returns 429."""
        rf = RequestFactory()
        request = rf.post(reverse("accounts:delete_account"))
        request.htmx = True  # type: ignore[attr-defined]  # noqa: B010 — django-htmx attr
        request.limited = True  # type: ignore[attr-defined]  # noqa: B010 — django-ratelimit attr

        from apps.accounts.views import delete_account

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
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        token = generate_unsubscribe_token("unsub@example.com", region.region_id)
        client = Client()
        response = client.get(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert response.status_code == 200
        assert b"unsubscribe" in response.content.lower()

    def test_post_valid_token_removes_subscription(self) -> None:
        """Valid token POST removes the matching region pin (SNOW-802)."""
        account = AccountFactory.create(user__email="unsub2@example.com")
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        token = generate_unsubscribe_token("unsub2@example.com", region.region_id)
        client = Client()
        response = client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert response.status_code == 200
        assert not _region_pins(account, region).exists()

    def test_post_last_subscription_keeps_account(self) -> None:
        """Removing the last subscription leaves the User and Account intact."""
        account = AccountFactory.create(user__email="lastregion@example.com")
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        account_pk = account.pk
        user_pk = account.user_id
        token = generate_unsubscribe_token("lastregion@example.com", region.region_id)
        client = Client()
        client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert Account.objects.filter(pk=account_pk).exists()
        assert User.objects.filter(pk=user_pk).exists()
        assert not Favourite.objects.filter(user_id=user_pk).region_pins().exists()

    def test_post_not_last_subscription_keeps_subscriber(self) -> None:
        """Removing one of multiple subscriptions keeps the account."""
        account = AccountFactory.create(user__email="keep@example.com")
        region1 = MicroRegionFactory.create()
        region2 = MicroRegionFactory.create()
        FavouriteFactory.create(user=account.user, region=region1, region_pin=True)
        FavouriteFactory.create(user=account.user, region=region2, region_pin=True)
        token = generate_unsubscribe_token("keep@example.com", region1.region_id)
        client = Client()
        client.post(reverse("accounts:unsubscribe", kwargs={"token": token}))
        assert Account.objects.filter(user__email="keep@example.com").exists()
        assert _region_pins(account, region2).exists()

    def test_post_idempotent_when_already_deleted(self) -> None:
        """Re-submitting after account deletion renders done page without error."""
        account = AccountFactory.create(user__email="gone@example.com")
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
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
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
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
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)

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

        from apps.accounts.views import unsubscribe_view

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


class TestEmailFormNormalisation:
    """Unit tests for EmailForm clean_email."""

    def test_email_form_lowercases_email(self) -> None:
        """EmailForm.clean_email returns a lowercased address."""
        from apps.accounts.forms import EmailForm

        form = EmailForm(data={"email": "TEST@EXAMPLE.COM"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_email_form_strips_whitespace(self) -> None:
        """EmailForm.clean_email strips leading and trailing whitespace."""
        from apps.accounts.forms import EmailForm

        form = EmailForm(data={"email": "  user@example.com  "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "user@example.com"


# ---------------------------------------------------------------------------
# Analytics event firing — subscription flow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalyticsUnsubscribed:
    """analytics.track('unsubscribed') fires in delete_account and unsubscribe_view."""

    def test_fires_in_delete_account(self) -> None:
        account = AccountFactory.create()
        distinct_id = str(account.uuid)
        client = _make_session_client(account)
        with patch("apps.accounts.views.analytics.track") as mock_track:
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
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        distinct_id = str(account.uuid)
        token = generate_unsubscribe_token(account.user.email, region.region_id)
        client = Client()
        with patch("apps.accounts.views.analytics.track") as mock_track:
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
    def use_locmem_backend(self, settings: Settings) -> None:
        """Use in-memory email backend so mail.outbox is populated."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_fires_for_known_email(self) -> None:
        """POST with a known email fires sign_in_requested with the existing PK."""
        account = AccountFactory.create(user__email="known@example.com")
        client = Client()
        with patch("apps.accounts.views.analytics.track") as mock_track:
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
        with patch("apps.accounts.views.analytics.track") as mock_track:
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
        with patch("apps.accounts.views.analytics.track") as mock_track:
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

        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)

        email = "unknown-caplog@example.com"
        token = _valid_account_token(email)

        with caplog.at_level(logging.WARNING, logger="apps.accounts.views"):
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
    def use_locmem_backend(self, settings: Settings) -> None:
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

        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)

        email = "signin-caplog@example.com"

        with caplog.at_level(logging.INFO, logger="apps.accounts.views"):
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

        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)

        email = "delete-caplog@example.com"
        account = AccountFactory.create(user__email=email)
        client = _make_session_client(account)

        with caplog.at_level(logging.INFO, logger="apps.accounts.views"):
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

        The account survives the request (only the region pin is
        deleted), so the success log line is pk-based already — this guards
        against a future regression that logs the raw address.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)

        email = "unsub-caplog@example.com"
        account = AccountFactory.create(user__email=email)
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        token = generate_unsubscribe_token(email, region.region_id)

        with caplog.at_level(logging.INFO, logger="apps.accounts.views"):
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

        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)

        email = "gone-caplog@example.com"
        account = AccountFactory.create(user__email=email)
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        token = generate_unsubscribe_token(email, region.region_id)
        account.delete()

        with caplog.at_level(logging.INFO, logger="apps.accounts.views"):
            Client().post(reverse("accounts:unsubscribe", kwargs={"token": token}))

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # The masked form g***@example.com must appear in at least one record.
        assert any("g***@example.com" in msg for msg in all_messages), (
            f"Masked email not found in any log record; records: {all_messages}"
        )
