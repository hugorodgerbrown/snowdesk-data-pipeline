"""
tests/oauth/management/commands/test_mint_mcp_token.py — mint_mcp_token (SNOW-1035).

Covers the dry run, a committed token that authenticates at the MCP
endpoint, email normalisation, the unknown-email failure and a bad
--resource.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse

from apps.oauth.models import OAuthClient, OAuthGrant, OAuthToken
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

RESOURCE = "http://testserver/api/mcp/"


def test_dry_run_writes_nothing() -> None:
    """Without --commit no client, grant or token is created."""
    user = UserFactory.create(email="walker@example.com")
    out = StringIO()
    call_command("mint_mcp_token", "--email", user.email, stdout=out)
    assert "Would mint" in out.getvalue()
    assert not OAuthClient.objects.exists()
    assert not OAuthToken.objects.exists()


def test_commit_mints_a_working_token(client: Client) -> None:
    """--commit prints a token that the MCP endpoint accepts."""
    user = UserFactory.create(email="walker@example.com")
    out = StringIO()
    call_command(
        "mint_mcp_token",
        "--email",
        "Walker@Example.com",
        "--resource",
        RESOURCE,
        "--commit",
        stdout=out,
    )
    token = out.getvalue().splitlines()[0]
    assert token.startswith("sd_at_")
    grant = OAuthGrant.objects.get(user=user)
    assert grant.client.kind == OAuthClient.KIND.LOCAL
    response = client.post(
        reverse("api:mcp:endpoint"),
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200


def test_second_run_reuses_the_client_and_grant() -> None:
    """Minting twice keeps one LOCAL client and one grant."""
    user = UserFactory.create()
    for _ in range(2):
        call_command(
            "mint_mcp_token", "--email", user.email, "--commit", stdout=StringIO()
        )
    assert OAuthClient.objects.count() == 1
    assert OAuthGrant.objects.count() == 1
    assert OAuthToken.objects.count() == 2


def test_token_prints_at_verbosity_zero() -> None:
    """The token is the output, so it prints even when quiet."""
    user = UserFactory.create()
    out = StringIO()
    call_command(
        "mint_mcp_token", "--email", user.email, "--commit", "-v", "0", stdout=out
    )
    assert out.getvalue().strip().startswith("sd_at_")


def test_unknown_email_fails() -> None:
    """An email with no user exits non-zero."""
    with pytest.raises(CommandError, match="No user"):
        call_command("mint_mcp_token", "--email", "nobody@example.com", "--commit")


def test_bad_resource_fails() -> None:
    """A relative --resource is refused."""
    user = UserFactory.create()
    with pytest.raises(CommandError, match="not an absolute URL"):
        call_command("mint_mcp_token", "--email", user.email, "--resource", "/api/mcp/")
