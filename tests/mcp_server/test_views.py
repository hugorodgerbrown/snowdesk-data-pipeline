"""
tests/mcp_server/test_views.py — Tests for apps.mcp_server.views.mcp_endpoint.

Covers the HTTP-layer contract: POST-only JSON-RPC dispatch, the 405
method guard, CSRF exemption, ``Cache-Control: no-store`` on every
response, and the ``block=True`` rate limit.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from django.core.cache import cache
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as _Response


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    """Clear the default cache before every test.

    Both the resolvers candidate-pool cache and the rate-limit counters
    live in the default cache — clearing it keeps tests independent of
    execution order.
    """
    cache.clear()


def _post(client: Client, body: dict) -> _Response:
    """POST a JSON-RPC envelope to the mcp endpoint."""
    return client.post(
        reverse("api:mcp:endpoint"),
        data=json.dumps(body),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_post_happy_path_returns_json_rpc_result(client: Client) -> None:
    """A well-formed POST returns a JSON-RPC result envelope."""
    response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    body = json.loads(response.content)
    assert body == {"jsonrpc": "2.0", "id": 1, "result": {}}


@pytest.mark.django_db
def test_every_response_carries_cache_control_no_store(client: Client) -> None:
    """Every response — success, error, or notification — is no-store."""
    ok_response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert ok_response["Cache-Control"] == "no-store"

    error_response = client.post(
        reverse("api:mcp:endpoint"), data=b"not json", content_type="application/json"
    )
    assert error_response["Cache-Control"] == "no-store"

    notification_response = _post(
        client, {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert notification_response["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_slashless_url_reaches_the_endpoint_without_a_redirect(client: Client) -> None:
    """POST /api/mcp (no trailing slash) hits the view directly, no 301.

    Remote MCP connectors POST ``initialize`` to exactly the URL the user
    typed. Without the slash-less alias, ``APPEND_SLASH`` would turn that
    POST into a 301 the client cannot replay, breaking the handshake. The
    alias must resolve straight to the view and return the JSON-RPC result.
    """
    response = client.post(
        "/api/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert json.loads(response.content) == {"jsonrpc": "2.0", "id": 1, "result": {}}


@pytest.mark.django_db
def test_canonical_and_slashless_urls_share_one_view(client: Client) -> None:
    """Both spellings reverse/route to ``apps.mcp_server.views.mcp_endpoint``."""
    assert reverse("api:mcp:endpoint") == "/api/mcp/"
    assert reverse("api:mcp:endpoint_noslash") == "/api/mcp"


@pytest.mark.django_db
def test_get_returns_405_with_allow_post_header(client: Client) -> None:
    """GET is rejected with 405 and an Allow: POST header."""
    response = client.get(reverse("api:mcp:endpoint"))
    assert response.status_code == 405
    assert response["Allow"] == "POST"


@pytest.mark.django_db
def test_malformed_json_body_returns_parse_error(client: Client) -> None:
    """A body that isn't valid JSON gets a -32700 Parse error envelope."""
    response = client.post(
        reverse("api:mcp:endpoint"),
        data=b"{not valid json",
        content_type="application/json",
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["error"]["code"] == -32700
    assert body["id"] is None


@pytest.mark.django_db
def test_unknown_method_returns_method_not_found(client: Client) -> None:
    """An unrecognised JSON-RPC method gets a -32601 error envelope."""
    response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    body = json.loads(response.content)
    assert body["error"]["code"] == -32601


@pytest.mark.django_db
def test_notification_returns_204_with_no_body(client: Client) -> None:
    """A JSON-RPC notification (no 'id') gets a bare 204, no JSON body."""
    response = _post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.django_db
def test_post_succeeds_without_a_csrf_token(client: Client) -> None:
    """The endpoint is CSRF-exempt — a client that enforces CSRF still succeeds."""
    strict_client = Client(enforce_csrf_checks=True)
    response = _post(strict_client, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 200


@pytest.mark.django_db
def test_rate_limit_exceeded_returns_403(client: Client) -> None:
    """The 61st request within a minute from one IP is blocked (block=True)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    with override_settings(RATELIMIT_ENABLE=True):
        for _ in range(60):
            response = _post(client, body)
            assert response.status_code == 200
        blocked = _post(client, body)
        assert blocked.status_code == 403
