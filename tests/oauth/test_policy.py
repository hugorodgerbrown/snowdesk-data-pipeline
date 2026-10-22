"""
tests/oauth/test_policy.py — apps.oauth.policy.verified_account (SNOW-1193).

The connect rule mcp-auth runs at consent and on every MCP call: an active
user with a verified Snowdesk account, and nobody else.
"""

from __future__ import annotations

import pytest

from apps.oauth.policy import verified_account
from tests.factories import AccountFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_a_verified_account_may_connect() -> None:
    """The one yes."""
    assert verified_account(AccountFactory.create().user) is True


def test_an_unverified_account_may_not() -> None:
    """An address nobody has confirmed is not enough."""
    assert verified_account(AccountFactory.create(is_verified=False).user) is False


def test_a_deactivated_account_may_not() -> None:
    """Deactivating a user cuts off its connected apps."""
    user = AccountFactory.create().user
    user.is_active = False
    assert verified_account(user) is False


def test_staff_without_an_account_may_not() -> None:
    """Staff is not a reader's credential; no Account row is no connection."""
    assert verified_account(UserFactory.create(is_staff=True)) is False
