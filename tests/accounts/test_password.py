"""
tests/accounts/test_password.py — Tests for password setup + sign-in (SNOW-431).

Covers:
  set_password_view — authed user sets a valid password (redirect to manage,
                      usable password, session preserved); mismatch and
                      validator-rejected passwords re-render setup with errors
                      and store nothing; unauthenticated → redirect to sign-in.
  sign_in_view (password branch) — email+password authenticates and redirects
                      to manage; wrong password / unknown email / no-password
                      account all return the same generic error and no session;
                      email-only POST keeps the magic-link flow.
  setup page — renders the "Set a password" card.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings

from tests.factories import AccountFactory, UserFactory

_STRONG = "Str0ngPassw0rd!"


@pytest.fixture(autouse=True)
def _locmem_email(settings: Settings) -> None:
    """Use the locmem email backend so the magic-link path does not hit SMTP."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


# ---------------------------------------------------------------------------
# set_password_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSetPasswordView:
    """Tests for set_password_view (POST /account/setup/password/)."""

    URL = "/account/setup/password/"

    def test_valid_password_is_set_and_redirects(self, client: Client) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.post(
            self.URL, {"new_password1": _STRONG, "new_password2": _STRONG}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:hub")
        account.user.refresh_from_db()
        assert account.user.check_password(_STRONG) is True
        assert account.user.has_usable_password() is True

    def test_session_preserved_after_set(self, client: Client) -> None:
        """update_session_auth_hash keeps the user logged in after the change."""
        account = AccountFactory.create()
        client.force_login(account.user)
        client.post(self.URL, {"new_password1": _STRONG, "new_password2": _STRONG})
        # Still authenticated — setup page renders instead of redirecting away.
        assert client.get(reverse("accounts:setup")).status_code == 200

    def test_mismatch_rerenders_and_stores_nothing(self, client: Client) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        original = account.user.password
        response = client.post(
            self.URL, {"new_password1": _STRONG, "new_password2": "different!"}
        )
        assert response.status_code == 200
        assert "Set a password" in response.content.decode()
        account.user.refresh_from_db()
        assert account.user.password == original

    def test_weak_password_rejected(self, client: Client) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        original = account.user.password
        response = client.post(
            self.URL, {"new_password1": "12345678", "new_password2": "12345678"}
        )
        assert response.status_code == 200
        account.user.refresh_from_db()
        assert account.user.password == original  # validator rejected → unchanged

    def test_unauthenticated_redirects_to_sign_in(self, client: Client) -> None:
        response = client.post(
            self.URL, {"new_password1": _STRONG, "new_password2": _STRONG}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")


# ---------------------------------------------------------------------------
# sign_in_view — password branch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasswordSignIn:
    """Tests for the password sign-in branch on /account/sign-in/."""

    URL = "/account/sign-in/"

    def _user_with_password(self, email: str = "signin@example.com") -> User:
        user = UserFactory.create(email=email, is_staff=False)
        user.set_password(_STRONG)
        user.save()
        return user

    def test_correct_password_signs_in(self, client: Client) -> None:
        self._user_with_password()
        response = client.post(
            self.URL, {"email": "signin@example.com", "password": _STRONG}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:hub")
        assert "_auth_user_id" in client.session

    def test_wrong_password_generic_error_no_session(self, client: Client) -> None:
        self._user_with_password()
        response = client.post(
            self.URL, {"email": "signin@example.com", "password": "wrong-password"}
        )
        assert response.status_code == 200
        assert "Invalid email or password" in response.content.decode()
        assert "_auth_user_id" not in client.session

    def test_no_password_account_generic_error(self, client: Client) -> None:
        """An account with no usable password gets the same generic error."""
        user = UserFactory.create(email="nopw@example.com", is_staff=False)
        user.set_unusable_password()
        user.save()
        response = client.post(
            self.URL, {"email": "nopw@example.com", "password": _STRONG}
        )
        assert response.status_code == 200
        assert "Invalid email or password" in response.content.decode()
        assert "_auth_user_id" not in client.session

    def test_unknown_email_generic_error(self, client: Client) -> None:
        response = client.post(
            self.URL, {"email": "ghost@example.com", "password": _STRONG}
        )
        assert response.status_code == 200
        assert "Invalid email or password" in response.content.decode()
        assert "_auth_user_id" not in client.session

    def test_email_only_keeps_magic_link_flow(self, client: Client) -> None:
        """Submitting email with no password still sends the magic link."""
        response = client.post(self.URL, {"email": "someone@example.com"})
        assert response.status_code == 200
        assert "Check your inbox" in response.content.decode()
        assert len(mail.outbox) == 1


# ---------------------------------------------------------------------------
# setup page renders the password card
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupPasswordCard:
    """The setup page exposes the set-password card."""

    def test_setup_renders_password_fields(self, client: Client) -> None:
        account = AccountFactory.create()
        client.force_login(account.user)
        response = client.get(reverse("accounts:setup"))
        content = response.content.decode()
        assert "Set a password" in content
        assert 'name="new_password1"' in content
        assert 'name="new_password2"' in content
