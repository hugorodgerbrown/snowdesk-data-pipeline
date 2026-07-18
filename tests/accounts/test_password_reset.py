"""
tests/accounts/test_password_reset.py — Tests for password reset (SNOW-432).

Covers:
  token layer — round-trips to the user; auto-invalidates once the password
                changes (single-use, option a).
  request view — GET renders the form; POST for unknown / passwordless / known
                emails all return the byte-identical "check your inbox" page;
                only a known account with a usable password gets an email;
                rate-limited 3/m.
  confirm view — GET renders the set-password form for a valid token; POST sets
                the new password, logs in and redirects; mismatch re-renders;
                a replayed (already-used) or bad token → link-expired 400;
                rate-limited 10/m.
  round trip — after reset the new password signs in and the old one fails.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import SettingsWrapper

from accounts.services.token import (
    generate_password_reset_token,
    verify_password_reset_token,
)
from tests.factories import UserFactory

_OLD = "Old-Passw0rd!"
_NEW = "New-Passw0rd!"
_MAX_AGE = 86400


@pytest.fixture(autouse=True)
def _locmem_email(settings: SettingsWrapper) -> None:
    """Use the locmem email backend so mail.outbox is populated inline."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


def _user_with_password(email: str = "u@example.com", password: str = _OLD) -> User:
    """Create a non-staff user with a usable password."""
    user = UserFactory.create(email=email, is_staff=False)
    user.set_password(password)
    user.save()
    return user


def _reset_token_from_outbox() -> str:
    """Extract the reset token from the last email in the outbox."""
    body = mail.outbox[-1].body
    line = next(ln for ln in body.splitlines() if "/account/reset-password/" in ln)
    return line.strip().rstrip("/").split("/account/reset-password/")[-1]


# ---------------------------------------------------------------------------
# Token layer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResetToken:
    """Single-use password-reset token behaviour."""

    def test_round_trips_to_user(self) -> None:
        user = _user_with_password()
        token = generate_password_reset_token(user)
        assert verify_password_reset_token(token, max_age=_MAX_AGE) == user

    def test_invalidated_after_password_change(self) -> None:
        """Changing the password invalidates an outstanding token (single-use)."""
        user = _user_with_password()
        token = generate_password_reset_token(user)
        user.set_password(_NEW)
        user.save()
        assert verify_password_reset_token(token, max_age=_MAX_AGE) is None

    def test_bad_token_returns_none(self) -> None:
        assert verify_password_reset_token("not-a-token", max_age=_MAX_AGE) is None


# ---------------------------------------------------------------------------
# Request view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResetRequestView:
    """GET/POST /account/reset-password/."""

    URL = "/account/reset-password/"

    def test_get_renders_form(self, client: Client) -> None:
        response = client.get(self.URL)
        assert response.status_code == 200
        assert 'name="email"' in response.content.decode()

    def test_known_password_account_gets_email(self, client: Client) -> None:
        _user_with_password(email="has@example.com")
        response = client.post(self.URL, {"email": "has@example.com"})
        assert response.status_code == 200
        assert len(mail.outbox) == 1
        assert "/account/reset-password/" in mail.outbox[0].body

    def test_unknown_email_sends_nothing(self, client: Client) -> None:
        response = client.post(self.URL, {"email": "ghost@example.com"})
        assert response.status_code == 200
        assert len(mail.outbox) == 0

    def test_passwordless_account_sends_nothing(self, client: Client) -> None:
        user = UserFactory.create(email="nopw@example.com", is_staff=False)
        user.set_unusable_password()
        user.save()
        response = client.post(self.URL, {"email": "nopw@example.com"})
        assert response.status_code == 200
        assert len(mail.outbox) == 0

    def test_enumeration_parity(self, client: Client) -> None:
        """Unknown, passwordless, and has-password all return identical bodies.

        The per-request CSP nonce (random regardless of email, so not a signal)
        is normalised out before comparing.
        """
        import re

        _user_with_password(email="has@example.com")
        nopw = UserFactory.create(email="nopw@example.com", is_staff=False)
        nopw.set_unusable_password()
        nopw.save()

        def _norm(content: bytes) -> bytes:
            return re.sub(rb'nonce="[^"]*"', b'nonce=""', content)

        bodies = {
            email: _norm(client.post(self.URL, {"email": email}).content)
            for email in ("ghost@example.com", "nopw@example.com", "has@example.com")
        }
        assert len(set(bodies.values())) == 1

    def test_rate_limited_returns_429(self) -> None:
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from accounts.views import reset_password_request_view

        request = RequestFactory().post(self.URL, {"email": "rl@example.com"})
        request.user = AnonymousUser()
        with patch("accounts.views.get_usage", return_value={"should_limit": True}):
            assert reset_password_request_view(request).status_code == 429


# ---------------------------------------------------------------------------
# Confirm view + round trip
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResetConfirmView:
    """GET/POST /account/reset-password/<token>/."""

    def _confirm_url(self, user: User) -> str:
        return reverse(
            "accounts:reset_password_confirm",
            args=[generate_password_reset_token(user)],
        )

    def test_get_renders_set_password_form(self, client: Client) -> None:
        user = _user_with_password()
        response = client.get(self._confirm_url(user))
        assert response.status_code == 200
        assert 'name="new_password1"' in response.content.decode()

    def test_post_sets_new_password_and_logs_in(self, client: Client) -> None:
        user = _user_with_password()
        response = client.post(
            self._confirm_url(user),
            {"new_password1": _NEW, "new_password2": _NEW},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:manage")
        user.refresh_from_db()
        assert user.check_password(_NEW) is True
        assert "_auth_user_id" in client.session

    def test_mismatch_rerenders(self, client: Client) -> None:
        user = _user_with_password()
        response = client.post(
            self._confirm_url(user),
            {"new_password1": _NEW, "new_password2": "different!"},
        )
        assert response.status_code == 200
        assert "Choose a new password" in response.content.decode()
        user.refresh_from_db()
        assert user.check_password(_OLD) is True  # unchanged

    def test_bad_token_returns_400(self, client: Client) -> None:
        url = reverse("accounts:reset_password_confirm", args=["not-a-token"])
        assert client.get(url).status_code == 400

    def test_replay_after_reset_is_expired(self, client: Client) -> None:
        """A token cannot be reused once the password has been changed."""
        user = _user_with_password()
        url = self._confirm_url(user)
        # First use succeeds.
        client.post(url, {"new_password1": _NEW, "new_password2": _NEW})
        # Second use of the same token now hits the expired-link page.
        assert client.get(url).status_code == 400

    def test_full_flow_new_password_works_old_fails(self, client: Client) -> None:
        """End to end: request → email → confirm → new works, old fails."""
        _user_with_password(email="flow@example.com")
        client.post("/account/reset-password/", {"email": "flow@example.com"})
        token = _reset_token_from_outbox()
        confirm_url = reverse("accounts:reset_password_confirm", args=[token])
        client.post(confirm_url, {"new_password1": _NEW, "new_password2": _NEW})

        fresh = Client()
        # New password signs in.
        ok = fresh.post(
            "/account/sign-in/", {"email": "flow@example.com", "password": _NEW}
        )
        assert ok.status_code == 302
        # Old password no longer works.
        bad_client = Client()
        bad = bad_client.post(
            "/account/sign-in/", {"email": "flow@example.com", "password": _OLD}
        )
        assert bad.status_code == 200
        assert "_auth_user_id" not in bad_client.session
