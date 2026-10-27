"""
tests/accounts/test_backends.py — Tests for the TokenBackend auth backend.

Covers ``TokenBackend.get_user`` (SNOW-1167): it returns an active user,
answers ``None`` for an inactive user and for an unknown pk, and — end to
end — a session made by a magic link stops authenticating on the next
request once the user is deactivated.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.backends import TokenBackend
from apps.accounts.services.token import SALT_ACCOUNT_ACCESS, generate_token
from tests.factories import AccountFactory, UserFactory


@pytest.mark.django_db
class TestTokenBackendGetUser:
    """Tests for TokenBackend.get_user."""

    def test_returns_active_user(self) -> None:
        """An active user is returned."""
        user = UserFactory.create(is_active=True)
        assert TokenBackend().get_user(user.pk) == user

    def test_returns_none_for_inactive_user(self) -> None:
        """An inactive user is answered as None, the same as an unknown pk."""
        user = UserFactory.create(is_active=False)
        assert TokenBackend().get_user(user.pk) is None

    def test_returns_none_for_unknown_pk(self) -> None:
        """A pk with no user returns None."""
        assert TokenBackend().get_user(999_999) is None


@pytest.mark.django_db
class TestDeactivationEndsTokenSession:
    """A magic-link session ends on the next request after deactivation."""

    def _signed_in_client(self, email: str) -> Client:
        """Sign in by magic-link POST and return the client holding the session."""
        client = Client()
        token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
        response = client.post(reverse("accounts:account", kwargs={"token": token}))
        assert response.status_code == 302
        assert client.session.get("_auth_user_id") is not None
        return client

    def test_active_user_keeps_the_session(self) -> None:
        """Control: an active user still reaches the settings page."""
        AccountFactory.create(user__email="active@example.com")
        client = self._signed_in_client("active@example.com")
        response = client.get(reverse("accounts:settings"))
        assert response.status_code == 200

    def test_deactivated_user_is_signed_out_on_next_request(self) -> None:
        """Setting is_active=False makes the existing session anonymous."""
        account = AccountFactory.create(user__email="disabled@example.com")
        client = self._signed_in_client("disabled@example.com")
        account.user.is_active = False
        account.user.save(update_fields=["is_active"])

        response = client.get(reverse("accounts:settings"))

        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")
