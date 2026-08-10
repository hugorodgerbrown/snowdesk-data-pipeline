"""
tests/accounts/test_email_change.py — Tests for change-of-email (SNOW-433).

Covers:
  token — round-trips to (user, new_email); bad token → None.
  request view — auth-gated; GET renders; POST sets the pending slot, mails the
                 new address a confirmation and the old address an FYI, and
                 leaves the account email unchanged; a taken new address is a
                 silent no-op (same response, no confirmation, no pending);
                 rate-limited 3/m.
  confirm view — GET renders confirm without swapping; POST swaps username +
                 email atomically, verifies, clears pending, logs in, FYIs the
                 old address; bad/stale/used token → 400; a taken-at-confirm
                 address → 400.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_django.fixtures import Settings

from apps.accounts.models import Account
from apps.accounts.services.token import (
    generate_email_change_token,
    verify_email_change_token,
)
from tests.factories import AccountFactory, UserFactory

_MAX_AGE = 86400


@pytest.fixture(autouse=True)
def _locmem_email(settings: Settings) -> None:
    """Use the locmem email backend so mail.outbox is populated inline."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


def _to(email: str) -> list:
    """Return the outbox messages addressed to ``email``."""
    return [m for m in mail.outbox if m.to == [email]]


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmailChangeToken:
    """Email-change token round-trips and binds user + address."""

    def test_round_trips(self) -> None:
        account = AccountFactory.create(user__email="old@example.com")
        token = generate_email_change_token(account.user, "new@example.com")
        result = verify_email_change_token(token, max_age=_MAX_AGE)
        assert result is not None
        user, new_email = result
        assert user == account.user
        assert new_email == "new@example.com"

    def test_bad_token(self) -> None:
        assert verify_email_change_token("nope", max_age=_MAX_AGE) is None


# ---------------------------------------------------------------------------
# Request view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChangeEmailRequestView:
    """GET/POST /account/change-email/."""

    URL = "/account/change-email/"

    def test_unauthenticated_redirects(self, client: Client) -> None:
        response = client.get(self.URL)
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")

    def test_get_renders_form(self, client: Client) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.get(self.URL)
        assert response.status_code == 200
        assert 'name="email"' in response.content.decode()

    def test_get_is_no_store(self, client: Client) -> None:
        """The form renders the account's current address, so it is never cached.

        C1, docs/code-reviews/2026-08-03-js-review.md — same argument as
        ``manage_view``: ``no-store`` is what keeps authenticated HTML out
        of the PWA shell cache (``_networkFirst`` in ``static/js/sw.js``).
        """
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.get(self.URL)
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]

    def test_post_sets_pending_and_mails_both(self, client: Client) -> None:
        account = AccountFactory.create(user__email="old@example.com")
        client.force_login(account.user)
        response = client.post(self.URL, {"email": "new@example.com"})
        assert response.status_code == 200
        assert "Check your inbox" in response.content.decode()

        account.refresh_from_db()
        assert account.pending_email == "new@example.com"
        # Account email is unchanged until confirmation.
        account.user.refresh_from_db()
        assert account.user.email == "old@example.com"

        # Confirmation to the new address + FYI to the old address.
        assert len(_to("new@example.com")) == 1
        assert "/account/change-email/" in _to("new@example.com")[0].body
        assert len(_to("old@example.com")) == 1
        assert "/account/change-email/" not in _to("old@example.com")[0].body

    def test_taken_address_is_silent_noop(self, client: Client) -> None:
        """A new address owned by another account: same response, no pending,
        no confirmation email (only the old-address FYI).
        """
        AccountFactory.create(user__email="taken@example.com", is_verified=True)
        account = AccountFactory.create(user__email="me@example.com")
        client.force_login(account.user)
        response = client.post(self.URL, {"email": "taken@example.com"})
        assert response.status_code == 200
        assert "Check your inbox" in response.content.decode()

        account.refresh_from_db()
        assert account.pending_email is None
        assert len(_to("taken@example.com")) == 0  # no confirmation leaked
        assert len(_to("me@example.com")) == 1  # only the FYI

    def test_same_address_is_noop_no_email(self, client: Client) -> None:
        """Submitting the current address sends nothing (no misleading FYI)."""
        account = AccountFactory.create(user__email="me@example.com")
        client.force_login(account.user)
        response = client.post(self.URL, {"email": "me@example.com"})
        assert response.status_code == 200
        account.refresh_from_db()
        assert account.pending_email is None
        assert len(mail.outbox) == 0

    def test_rate_limited_returns_429(self) -> None:
        from django.test import RequestFactory

        from apps.accounts.views import change_email_view

        account = AccountFactory.create()
        request = RequestFactory().post(self.URL, {"email": "new@example.com"})
        request.user = account.user
        with patch(
            "apps.accounts.views.get_usage", return_value={"should_limit": True}
        ):
            assert change_email_view(request).status_code == 429


# ---------------------------------------------------------------------------
# Confirm view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChangeEmailConfirmView:
    """GET/POST /account/change-email/<token>/."""

    def _pending_account(
        self, current: str = "old@example.com", new: str = "new@example.com"
    ) -> Account:
        account = AccountFactory.create(user__email=current, is_verified=True)
        account.request_email_change(new, timezone.now())
        account.save()
        return account

    def _url(self, account: Account, new: str = "new@example.com") -> str:
        token = generate_email_change_token(account.user, new)
        return reverse("accounts:change_email_confirm", args=[token])

    def test_get_renders_without_swapping(self, client: Client) -> None:
        account = self._pending_account()
        response = client.get(self._url(account))
        assert response.status_code == 200
        assert "Confirm new email" in response.content.decode()
        account.user.refresh_from_db()
        assert account.user.email == "old@example.com"  # unchanged on GET

    def test_post_swaps_and_logs_in(self, client: Client) -> None:
        account = self._pending_account()
        response = client.post(self._url(account))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:manage")

        account.user.refresh_from_db()
        account.refresh_from_db()
        assert account.user.email == "new@example.com"
        assert account.user.username == "new@example.com"
        assert account.is_verified is True
        assert account.pending_email is None
        assert "_auth_user_id" in client.session
        # Old address gets the completion FYI.
        assert len(_to("old@example.com")) >= 1

    def test_old_username_gone_after_swap(self, client: Client) -> None:
        account = self._pending_account()
        client.post(self._url(account))
        assert not User.objects.filter(username="old@example.com").exists()
        assert User.objects.filter(username="new@example.com").exists()

    def test_bad_token_returns_400(self, client: Client) -> None:
        url = reverse("accounts:change_email_confirm", args=["nope"])
        assert client.get(url).status_code == 400

    def test_stale_token_when_pending_cleared(self, client: Client) -> None:
        """A token whose pending slot no longer matches is rejected."""
        account = self._pending_account()
        url = self._url(account)
        account.clear_pending_email()
        account.save()
        assert client.get(url).status_code == 400

    def test_replay_after_completion_is_rejected(self, client: Client) -> None:
        account = self._pending_account()
        url = self._url(account)
        client.post(url)  # completes the change, clears pending
        assert Client().get(url).status_code == 400

    def test_taken_at_confirm_is_rejected(self, client: Client) -> None:
        """If the new address is registered between request and confirm, reject."""
        account = self._pending_account()
        url = self._url(account)
        # Someone else grabs the address in the meantime.
        UserFactory.create(email="new@example.com", is_staff=False)
        assert client.get(url).status_code == 400
