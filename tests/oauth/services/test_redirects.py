"""
tests/oauth/services/test_redirects.py — Tests for apps.oauth.services.redirects.

Covers what may be registered, exact matching for a web redirect, and
port-agnostic matching for a loopback one.
"""

from __future__ import annotations

import pytest

from apps.oauth.services.redirects import (
    is_loopback,
    is_registrable,
    only_loopback,
    redirect_uri_allowed,
    registered_redirect_uri,
)
from tests.factories import OAuthClientFactory

CLAUDE = "https://claude.ai/api/mcp/auth_callback"


@pytest.mark.parametrize(
    ("uri", "ok"),
    [
        (CLAUDE, True),
        ("http://localhost:33418/callback", True),
        ("http://127.0.0.1/callback", True),
        ("http://claude.ai/callback", False),
        ("https://claude.ai/cb#frag", False),
        ("https://user:pw@claude.ai/cb", False),
        ("javascript:alert(1)", False),
        ("https:///nohost", False),
        ("http://localhost:notaport/cb", False),
        (42, False),
        ("", False),
    ],
)
def test_is_registrable(uri: object, ok: bool) -> None:
    """https with a host, or http loopback; nothing else."""
    assert is_registrable(uri) is ok


def test_is_loopback() -> None:
    """Only http on localhost or 127.0.0.1 is loopback."""
    assert is_loopback("http://localhost:1/cb")
    assert is_loopback("http://127.0.0.1:2/cb")
    assert not is_loopback("https://localhost/cb")
    assert not is_loopback("http://localhost.evil.test/cb")


@pytest.mark.django_db
def test_web_redirect_matches_exactly() -> None:
    """A non-loopback redirect must match a registered URI exactly."""
    client = OAuthClientFactory.create(redirect_uris=[CLAUDE])
    assert redirect_uri_allowed(client, CLAUDE)
    assert not redirect_uri_allowed(client, CLAUDE + "/")
    assert not redirect_uri_allowed(
        client, "https://claude.ai:8443/api/mcp/auth_callback"
    )
    assert not redirect_uri_allowed(client, "")


@pytest.mark.django_db
def test_loopback_redirect_matches_on_any_port() -> None:
    """Claude Code binds a fresh port each time; the port is not compared."""
    client = OAuthClientFactory.create(redirect_uris=["http://localhost:1234/callback"])
    assert redirect_uri_allowed(client, "http://localhost:55555/callback")
    assert redirect_uri_allowed(client, "http://localhost/callback")
    assert not redirect_uri_allowed(client, "http://localhost:55555/other")
    assert not redirect_uri_allowed(client, "http://127.0.0.1:55555/callback")


@pytest.mark.django_db
def test_loopback_request_does_not_match_web_registration() -> None:
    """A loopback URI never matches a client registered for the web only."""
    client = OAuthClientFactory.create(redirect_uris=[CLAUDE])
    assert not redirect_uri_allowed(client, "http://localhost:1/api/mcp/auth_callback")


@pytest.mark.django_db
def test_only_loopback() -> None:
    """The consent warning fires for a loopback-only client."""
    assert only_loopback(
        OAuthClientFactory.create(redirect_uris=["http://127.0.0.1/cb"])
    )
    assert not only_loopback(
        OAuthClientFactory.create(redirect_uris=["http://127.0.0.1/cb", CLAUDE])
    )
    assert not only_loopback(OAuthClientFactory.create(redirect_uris=[]))


@pytest.mark.django_db
def test_registered_redirect_uri_returns_the_stored_value() -> None:
    """An exact match returns the client's registered string."""
    client = OAuthClientFactory.create(redirect_uris=[CLAUDE])
    assert registered_redirect_uri(client, CLAUDE) == CLAUDE
    assert registered_redirect_uri(client, CLAUDE + "/") is None
    assert registered_redirect_uri(client, "") is None


@pytest.mark.django_db
def test_registered_redirect_uri_takes_only_the_port_from_a_loopback_request() -> None:
    """A loopback match is rebuilt from the registered URI plus the requested port."""
    client = OAuthClientFactory.create(redirect_uris=["http://localhost/callback"])
    assert (
        registered_redirect_uri(client, "http://localhost:55555/callback")
        == "http://localhost:55555/callback"
    )
    assert (
        registered_redirect_uri(client, "http://localhost/callback")
        == "http://localhost/callback"
    )
    assert registered_redirect_uri(client, "http://localhost:55555/other") is None
    assert registered_redirect_uri(client, "http://localhost:99999/callback") is None
