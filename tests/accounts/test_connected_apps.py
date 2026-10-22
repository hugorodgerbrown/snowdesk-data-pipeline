"""
tests/accounts/test_connected_apps.py — the settings page's Connected apps (SNOW-1035, SNOW-1193).

The block lists the apps the signed-in user holds a live token for — the
apps allowed to call the MCP server, by mcp-auth's ``connected_apps`` — each
with a Disconnect control. The disconnect endpoint itself is covered in
tests/oauth/test_views.py; this file covers what the page shows.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    AccountFactory,
    OAuthAccessTokenFactory,
    OAuthApplicationFactory,
    OAuthRefreshTokenFactory,
)

pytestmark = pytest.mark.django_db

URL = reverse("accounts:settings")


def test_connected_apps_are_listed_with_a_disconnect(client: Client) -> None:
    """Each connected app shows its name, its callback's host and a Disconnect."""
    user = AccountFactory.create().user
    token = OAuthAccessTokenFactory.create(user=user)
    client.force_login(user)
    html = client.get(URL).content.decode()
    text = " ".join(html.split())
    assert 'data-testid="connected-apps"' in html
    assert "> Claude </p>" in text
    assert ">claude.ai</p>" in text
    disconnect = reverse(
        "oauth:disconnect", kwargs={"application_id": token.application.pk}
    )
    assert disconnect in html
    assert 'aria-label="Disconnect Claude"' in html


def test_a_refresh_token_alone_keeps_an_app_connected(client: Client) -> None:
    """An app whose hour-long access token lapsed is still connected."""
    user = AccountFactory.create().user
    OAuthRefreshTokenFactory.create(
        user=user, application=OAuthApplicationFactory.create(name="Refreshing")
    )
    client.force_login(user)
    assert b"Refreshing" in client.get(URL).content


def test_a_local_token_row_has_no_host_line(client: Client) -> None:
    """A mint_mcp_token app has no redirect URI, so no host is shown."""
    user = AccountFactory.create().user
    OAuthAccessTokenFactory.create(
        user=user,
        application=OAuthApplicationFactory.create(
            name="Local token", redirect_uris=""
        ),
    )
    client.force_login(user)
    html = client.get(URL).content.decode()
    assert "Local token" in html
    assert "font-mono text-xs text-text-3 mt-0.5" not in html


def test_expired_and_foreign_connections_are_not_listed(client: Client) -> None:
    """A lapsed token and another user's connection do not appear."""
    user = AccountFactory.create().user
    OAuthAccessTokenFactory.create(
        user=user,
        expires=timezone.now() - timedelta(minutes=1),
        application=OAuthApplicationFactory.create(name="Lapsed App"),
    )
    OAuthAccessTokenFactory.create(
        application=OAuthApplicationFactory.create(name="Not Mine")
    )
    client.force_login(user)
    html = client.get(URL).content.decode()
    assert "Lapsed App" not in html
    assert "Not Mine" not in html
    assert 'data-testid="connected-apps"' not in html


def test_nothing_connected_no_section(client: Client) -> None:
    """With nothing connected the section is absent."""
    client.force_login(AccountFactory.create().user)
    assert b"Connected apps" not in client.get(URL).content
