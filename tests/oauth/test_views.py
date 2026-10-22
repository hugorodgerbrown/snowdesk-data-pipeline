"""
tests/oauth/test_views.py — Snowdesk's views around mcp-auth (SNOW-1193).

Covers the verify-first gate in front of the consent page, Snowdesk's
consent and returning templates (the overrides of mcp-auth's), the
protected-resource document keeping the URL's trailing slash, and the
settings page's HTMX disconnect endpoint. The OAuth flow itself is
mcp-auth's, and its connector contract runs in tests/mcp_server/test_views.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from mcp_auth.testing import CALLBACK, pkce_pair
from oauth2_provider.models import AccessToken, Application

from tests.factories import (
    AccountFactory,
    OAuthAccessTokenFactory,
    OAuthApplicationFactory,
)

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    """Rate-limit counters live in the cache; start each test with none."""
    cache.clear()


def _params(application: Application, redirect_uri: str = CALLBACK) -> dict[str, Any]:
    """Return a valid authorize query for ``application``."""
    _, challenge = pkce_pair()
    return {
        "response_type": "code",
        "client_id": application.client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "scope": "mcp offline_access",
        "resource": "http://testserver/api/mcp/",
    }


# ---------------------------------------------------------------------------
# The consent page
# ---------------------------------------------------------------------------


def test_an_unverified_account_is_told_to_verify_first(client: Client) -> None:
    """Signed in but unverified: the verify-first page, as a 403."""
    client.force_login(AccountFactory.create(is_verified=False).user)
    response = client.get(
        "/oauth/authorize/", _params(OAuthApplicationFactory.create())
    )
    assert response.status_code == 403
    assert [t.name for t in response.templates][0] == "oauth/verify_first.html"
    assert b"Verify your email first" in response.content
    assert reverse("accounts:settings").encode() in response.content


def test_a_verified_account_sees_snowdesks_consent_page(client: Client) -> None:
    """The consent page names the app, the account, the host and the switch."""
    account = AccountFactory.create()
    client.force_login(account.user)
    response = client.get(
        "/oauth/authorize/", _params(OAuthApplicationFactory.create())
    )
    assert response.status_code == 200
    html = " ".join(response.content.decode().split())
    assert "Connect Claude to Snowdesk?" in html
    assert account.user.email in html
    assert 'data-testid="oauth-redirect-host"' in html
    assert "claude.ai" in html
    assert 'action="/oauth/switch-account/"' in html
    assert 'name="allow"' in html
    assert 'data-testid="oauth-loopback-warning"' not in html


def test_a_loopback_callback_carries_the_warning(client: Client) -> None:
    """A code bound for this computer says so."""
    loopback = "http://localhost:33418/callback"
    app = OAuthApplicationFactory.create(redirect_uris=loopback)
    client.force_login(AccountFactory.create().user)
    response = client.get("/oauth/authorize/", _params(app, loopback))
    assert b'data-testid="oauth-loopback-warning"' in response.content


def test_a_bad_request_shows_the_error_state(client: Client) -> None:
    """An unknown client has no form to show, only the reason."""
    client.force_login(AccountFactory.create().user)
    params = _params(OAuthApplicationFactory.create())
    params["client_id"] = "nobody"
    response = client.get("/oauth/authorize/", params)
    assert b"This connection can't go ahead" in response.content
    assert b'name="allow"' not in response.content


def test_allow_returns_through_snowdesks_returning_page(client: Client) -> None:
    """Allow answers with the meta-refresh page, not a 302 the CSP would block."""
    client.force_login(AccountFactory.create().user)
    page = client.get("/oauth/authorize/", _params(OAuthApplicationFactory.create()))
    fields = {
        bound.name: bound.value() or ""
        for bound in page.context["form"]
        if bound.is_hidden
    }
    response = client.post("/oauth/authorize/", {**fields, "allow": "Authorize"})
    assert response.status_code == 200
    assert [t.name for t in response.templates][0] == "mcp_auth/returning.html"
    target = response.context["target"]
    assert target.startswith(f"{CALLBACK}?")
    assert "code=" in target
    assert b'http-equiv="refresh"' in response.content
    assert b"Returning to claude.ai" in response.content


# ---------------------------------------------------------------------------
# The protected-resource document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/mcp/", "/api/mcp", "/api/ops/mcp/"])
def test_resource_is_the_path_called_slash_and_all(client: Client, path: str) -> None:
    """Claude requires ``resource`` to equal the URL the user typed."""
    document = client.get(f"/.well-known/oauth-protected-resource{path}").json()
    assert document["resource"] == f"http://testserver{path}"
    assert document["authorization_servers"] == ["http://testserver"]


def test_the_root_document_is_mcp_auths(client: Client) -> None:
    """Without a path the resource is the origin, as mcp-auth answers."""
    document = client.get("/.well-known/oauth-protected-resource").json()
    assert document["resource"] == "http://testserver"


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def _url(application: Application) -> str:
    """Return the disconnect URL for ``application``."""
    return reverse("oauth:disconnect", kwargs={"application_id": application.pk})


def test_disconnect_revokes_and_removes_the_row(client: Client) -> None:
    """An empty 200 (the row is the swap target) and no live token left."""
    token = OAuthAccessTokenFactory.create(user=AccountFactory.create().user)
    client.force_login(token.user)
    response = client.post(_url(token.application), headers=HTMX)
    assert response.status_code == 200
    assert response.content == b""
    assert not AccessToken.objects.filter(pk=token.pk).exists()


def test_disconnect_is_404_for_an_app_the_user_is_not_connected_to(
    client: Client,
) -> None:
    """Someone else's connection is not this user's to end."""
    theirs = OAuthAccessTokenFactory.create()
    client.force_login(AccountFactory.create().user)
    assert client.post(_url(theirs.application), headers=HTMX).status_code == 404
    assert AccessToken.objects.filter(pk=theirs.pk).exists()


def test_disconnect_is_403_signed_out(client: Client) -> None:
    """No session, no disconnect."""
    token = OAuthAccessTokenFactory.create()
    assert client.post(_url(token.application), headers=HTMX).status_code == 403


def test_disconnect_needs_htmx_and_post(client: Client) -> None:
    """A plain POST is a 400 (require_htmx); a GET is a 405."""
    token = OAuthAccessTokenFactory.create(user=AccountFactory.create().user)
    client.force_login(token.user)
    assert client.post(_url(token.application)).status_code == 400
    assert client.get(_url(token.application), headers=HTMX).status_code == 405


def test_mcp_auths_own_connected_page_renders_in_the_site_layout(
    client: Client,
) -> None:
    """The package page nothing links to still wears Snowdesk's chrome."""
    token = OAuthAccessTokenFactory.create(user=AccountFactory.create().user)
    client.force_login(token.user)
    response = client.get("/oauth/connected/")
    assert response.status_code == 200
    assert "mcp_auth/base.html" in [t.name for t in response.templates]
    assert b"Connect an app" in response.content
