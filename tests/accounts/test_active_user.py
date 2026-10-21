"""
tests/accounts/test_active_user.py — Tests for the token-path sign-in policy.

Covers ``user_may_sign_in`` (SNOW-1167): ``None`` and an inactive user may
not sign in by a token path; an active user may.
"""

import pytest

from apps.accounts.services.active_user import user_may_sign_in
from tests.factories import UserFactory


class TestUserMaySignIn:
    """Tests for user_may_sign_in."""

    def test_none_may_not_sign_in(self) -> None:
        """No resolved user means no sign-in."""
        assert user_may_sign_in(None) is False

    @pytest.mark.django_db
    def test_inactive_user_may_not_sign_in(self) -> None:
        """A user with is_active=False is refused."""
        user = UserFactory.create(is_active=False)
        assert user_may_sign_in(user) is False

    @pytest.mark.django_db
    def test_active_user_may_sign_in(self) -> None:
        """A user with is_active=True is allowed."""
        user = UserFactory.create(is_active=True)
        assert user_may_sign_in(user) is True
