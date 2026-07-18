"""
tests/accounts/test_registration.py — Tests for the registration flow (SNOW-430).

Covers:
  register_view  — GET renders the form; authed GET redirects to manage;
                   POST creates User + unverified Account and sends one
                   verification email; verified re-registration is
                   indistinguishable and sends no email; unverified
                   re-registration resends; name → display_name; invalid email
                   re-renders; email lowercased; rate-limit 429.
  verify_view    — GET renders confirm page without verifying (prefetch-safe);
                   POST verifies + logs in + redirects to setup; bad/expired
                   token → 400; unknown email → 400; Referrer-Policy set.
  setup_view     — unauthenticated → redirect to sign-in; authenticated → 200.
  send_verification_email — one email, correct recipient, /account/verify/ URL,
                   token round-trips under SALT_EMAIL_VERIFICATION.
  /subscribe/ → /account/ permanent redirect (legacy links preserved).
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import SettingsWrapper

from accounts.models import Account
from accounts.services.email import send_verification_email
from accounts.services.token import (
    SALT_EMAIL_VERIFICATION,
    generate_token,
    verify_token,
)
from tests.factories import AccountFactory

User = get_user_model()


@pytest.fixture(autouse=True)
def _locmem_email(settings: SettingsWrapper) -> None:
    """Use the locmem email backend so mail.outbox is populated inline."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


# ---------------------------------------------------------------------------
# register_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegisterView:
    """Tests for register_view (GET/POST /account/register/)."""

    URL = "/account/register/"

    def test_get_renders_form(self, client: Client) -> None:
        response = client.get(self.URL)
        assert response.status_code == 200
        assert 'name="email"' in response.content.decode()

    def test_authenticated_get_redirects_to_manage(self, client: Client) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.get(self.URL)
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:manage")

    def test_post_new_email_creates_user_and_account(self, client: Client) -> None:
        response = client.post(self.URL, {"email": "new@example.com"})
        assert response.status_code == 200
        assert "Check your inbox" in response.content.decode()
        user = User.objects.get(username="new@example.com")
        account = Account.objects.get(user=user)
        assert account.is_verified is False

    def test_post_new_email_sends_one_verification_email(self, client: Client) -> None:
        client.post(self.URL, {"email": "new@example.com"})
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["new@example.com"]
        assert "/account/verify/" in mail.outbox[0].body

    def test_post_lowercases_email(self, client: Client) -> None:
        client.post(self.URL, {"email": "MixedCase@Example.com"})
        assert User.objects.filter(username="mixedcase@example.com").exists()

    def test_post_stores_optional_name(self, client: Client) -> None:
        client.post(self.URL, {"email": "named@example.com", "name": "Ada"})
        account = Account.objects.get(user__username="named@example.com")
        assert account.display_name == "Ada"

    def test_verified_reregistration_sends_no_email(self, client: Client) -> None:
        """A verified email produces the same response but no second email."""
        AccountFactory.create(user__email="known@example.com", is_verified=True)
        response = client.post(self.URL, {"email": "known@example.com"})
        assert response.status_code == 200
        assert "Check your inbox" in response.content.decode()
        assert len(mail.outbox) == 0

    def test_verified_reregistration_creates_no_duplicate(self, client: Client) -> None:
        AccountFactory.create(user__email="known@example.com", is_verified=True)
        client.post(self.URL, {"email": "known@example.com"})
        assert User.objects.filter(username="known@example.com").count() == 1
        assert Account.objects.filter(user__username="known@example.com").count() == 1

    def test_unverified_reregistration_resends(self, client: Client) -> None:
        AccountFactory.create(user__email="pending@example.com", is_verified=False)
        client.post(self.URL, {"email": "pending@example.com"})
        assert len(mail.outbox) == 1

    def test_invalid_email_rerenders_form_and_sends_nothing(
        self, client: Client
    ) -> None:
        response = client.post(self.URL, {"email": "not-an-email"})
        assert response.status_code == 200
        assert 'name="email"' in response.content.decode()
        assert len(mail.outbox) == 0
        assert User.objects.count() == 0

    def test_rate_limited_returns_429(self) -> None:
        """When the per-IP limit is exceeded, the POST returns 429."""
        from unittest.mock import patch

        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from accounts.views import register_view

        rf = RequestFactory()
        request = rf.post(self.URL, data={"email": "rl@example.com"})
        request.user = AnonymousUser()

        with patch("accounts.views.get_usage", return_value={"should_limit": True}):
            response = register_view(request)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# verify_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVerifyView:
    """Tests for verify_view (GET/POST /account/verify/<token>/)."""

    def _url(self, email: str) -> str:
        token = generate_token(email, salt=SALT_EMAIL_VERIFICATION)
        return reverse("accounts:verify", args=[token])

    def test_get_renders_confirm_without_verifying(self, client: Client) -> None:
        account = AccountFactory.create(user__email="v@example.com", is_verified=False)
        response = client.get(self._url("v@example.com"))
        assert response.status_code == 200
        assert "Verify my email" in response.content.decode()
        account.refresh_from_db()
        assert account.is_verified is False  # GET must not verify

    def test_get_sets_referrer_policy(self, client: Client) -> None:
        AccountFactory.create(user__email="v@example.com", is_verified=False)
        response = client.get(self._url("v@example.com"))
        assert response["Referrer-Policy"] == "no-referrer"

    def test_post_verifies_and_logs_in(self, client: Client) -> None:
        account = AccountFactory.create(user__email="v@example.com", is_verified=False)
        response = client.post(self._url("v@example.com"))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:setup")
        account.refresh_from_db()
        assert account.is_verified is True
        assert account.verified_at is not None
        assert "_auth_user_id" in client.session

    def test_post_already_verified_is_idempotent(self, client: Client) -> None:
        """Re-clicking a valid link on a verified account re-logs in without
        overwriting the original verified_at.
        """
        import datetime

        original = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        AccountFactory.create(
            user__email="v@example.com", is_verified=True, verified_at=original
        )
        response = client.post(self._url("v@example.com"))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:setup")
        account = Account.objects.get(user__username="v@example.com")
        assert account.verified_at == original  # untouched
        assert "_auth_user_id" in client.session

    def test_post_bad_token_returns_400(self, client: Client) -> None:
        response = client.post(reverse("accounts:verify", args=["not-a-token"]))
        assert response.status_code == 400

    def test_get_bad_token_returns_400(self, client: Client) -> None:
        response = client.get(reverse("accounts:verify", args=["not-a-token"]))
        assert response.status_code == 400

    def test_valid_token_unknown_email_returns_400(self, client: Client) -> None:
        # A well-signed token for an email with no User row.
        response = client.get(self._url("ghost@example.com"))
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# setup_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupView:
    """Tests for setup_view (GET /account/setup/)."""

    URL = "/account/setup/"

    def test_unauthenticated_redirects_to_sign_in(self, client: Client) -> None:
        response = client.get(self.URL)
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")

    def test_authenticated_renders_page(self, client: Client) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.get(self.URL)
        assert response.status_code == 200
        assert "You're verified" in response.content.decode()


# ---------------------------------------------------------------------------
# send_verification_email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendVerificationEmail:
    """Tests for the verification email service."""

    def test_sends_one_email_to_recipient(self) -> None:
        send_verification_email("alice@example.com")
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["alice@example.com"]

    def test_body_contains_verify_path(self) -> None:
        send_verification_email("alice@example.com")
        assert "/account/verify/" in mail.outbox[0].body

    def test_token_round_trips_under_verification_salt(self) -> None:
        send_verification_email("alice@example.com")
        body = mail.outbox[0].body
        url_line = next(
            line for line in body.splitlines() if "/account/verify/" in line
        )
        token = url_line.strip().rstrip("/").split("/account/verify/")[-1]
        result = verify_token(token, salt=SALT_EMAIL_VERIFICATION, max_age=86400)
        assert result == "alice@example.com"


# ---------------------------------------------------------------------------
# Legacy /subscribe/ → /account/ redirect (SNOW-430 remount)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscribeRedirect:
    """The legacy /subscribe/ prefix permanently redirects to /account/."""

    def test_manage_path_redirects(self, client: Client) -> None:
        response = client.get("/subscribe/manage/")
        assert response.status_code == 301
        assert response["Location"] == "/account/manage/"

    def test_sign_in_path_redirects(self, client: Client) -> None:
        response = client.get("/subscribe/sign-in/")
        assert response.status_code == 301
        assert response["Location"] == "/account/sign-in/"

    def test_legacy_account_token_redirects_to_access(self, client: Client) -> None:
        """In-flight /subscribe/account/<token>/ links land on /account/access/."""
        response = client.get("/subscribe/account/some-token/")
        assert response.status_code == 301
        assert response["Location"] == "/account/access/some-token/"
