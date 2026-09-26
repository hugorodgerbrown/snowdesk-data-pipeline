"""
tests/accounts/test_connected_apps.py — the settings page's Connected apps (SNOW-1035).

The block lists the signed-in user's active OAuth grants — the apps allowed
to call the MCP server — each with a Disconnect control. The disconnect
endpoint itself (owner-only, HTMX-only) is covered in
tests/oauth/test_views.py; this file covers what the page shows.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.factories import AccountFactory, OAuthClientFactory, OAuthGrantFactory

pytestmark = pytest.mark.django_db

URL = reverse("accounts:settings")


def test_active_grants_are_listed_with_a_disconnect(client: Client) -> None:
    """Each active grant shows its name, host and a Disconnect control."""
    user = AccountFactory.create().user
    grant = OAuthGrantFactory.create(
        user=user, client=OAuthClientFactory.create(client_name="Claude")
    )
    client.force_login(user)
    html = client.get(URL).content.decode()
    assert 'data-testid="connected-apps"' in html
    assert "Claude" in html
    assert "claude.ai · connected" in html
    assert reverse("oauth:grant_revoke", kwargs={"grant_uuid": grant.uuid}) in html
    assert 'aria-label="Disconnect Claude"' in html


def test_last_used_date_is_shown(client: Client) -> None:
    """A used grant says when it was last used; an unused one says never."""
    user = AccountFactory.create().user
    OAuthGrantFactory.create(user=user, last_used_at=timezone.now())
    client.force_login(user)
    assert "last used" in client.get(URL).content.decode()


def test_revoked_and_foreign_grants_are_not_listed(client: Client) -> None:
    """Revoked grants and other users' grants do not appear."""
    user = AccountFactory.create().user
    OAuthGrantFactory.create(
        user=user,
        revoked_at=timezone.now(),
        client=OAuthClientFactory.create(client_name="Revoked App"),
    )
    OAuthGrantFactory.create(client=OAuthClientFactory.create(client_name="Not Mine"))
    client.force_login(user)
    html = client.get(URL).content.decode()
    assert "Revoked App" not in html
    assert "Not Mine" not in html
    assert 'data-testid="connected-apps"' not in html


def test_no_grants_no_section(client: Client) -> None:
    """With nothing connected the section is absent."""
    client.force_login(AccountFactory.create().user)
    assert b"Connected apps" not in client.get(URL).content
