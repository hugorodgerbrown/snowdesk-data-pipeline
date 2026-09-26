"""
tests/mcp_server/test_views.py — Tests for apps.mcp_server.views.mcp_endpoint.

Covers the HTTP-layer contract: POST-only JSON-RPC dispatch, the 405
method guard, CSRF exemption, ``Cache-Control: no-store`` on every
response, the ``block=True`` per-IP rate limit, and (SNOW-1035) bearer
authentication — the 401 challenge on both URL spellings, a rejected
token, and the per-user rate limit.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from django.core.cache import cache
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from freezegun import freeze_time

from apps.oauth.services.tokens import issue_token_pair
from tests.factories import OAuthGrantFactory

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


@pytest.fixture
def token(db: None) -> str:
    """Return a live access token for the test origin's MCP resource."""
    grant = OAuthGrantFactory.create(resource="http://testserver/api/mcp/")
    pair, _ = issue_token_pair(grant, resource=grant.resource, scope="mcp")
    return pair.access_token


def _post(
    client: Client, body: dict, token: str | None = None, url: str | None = None
) -> _Response:
    """POST a JSON-RPC envelope to the mcp endpoint, with ``token`` if given."""
    return client.post(
        url or reverse("api:mcp:endpoint"),
        data=json.dumps(body),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"} if token else None,
    )


@pytest.mark.django_db
def test_post_happy_path_returns_json_rpc_result(client: Client, token: str) -> None:
    """A well-formed POST returns a JSON-RPC result envelope."""
    response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    body = json.loads(response.content)
    assert body == {"jsonrpc": "2.0", "id": 1, "result": {}}


@pytest.mark.django_db
def test_every_response_carries_cache_control_no_store(
    client: Client, token: str
) -> None:
    """Every response — success, error, or notification — is no-store."""
    ok_response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token)
    assert ok_response["Cache-Control"] == "no-store"

    error_response = client.post(
        reverse("api:mcp:endpoint"),
        data=b"not json",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert error_response["Cache-Control"] == "no-store"

    notification_response = _post(
        client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token
    )
    assert notification_response["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_slashless_url_reaches_the_endpoint_without_a_redirect(
    client: Client, token: str
) -> None:
    """POST /api/mcp (no trailing slash) hits the view directly, no 301.

    Remote MCP connectors POST ``initialize`` to exactly the URL the user
    typed. Without the slash-less alias, ``APPEND_SLASH`` would turn that
    POST into a 301 the client cannot replay, breaking the handshake. The
    alias must resolve straight to the view and return the JSON-RPC result.
    """
    response = _post(
        client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token, url="/api/mcp"
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
def test_malformed_json_body_returns_parse_error(client: Client, token: str) -> None:
    """A body that isn't valid JSON gets a -32700 Parse error envelope."""
    response = client.post(
        reverse("api:mcp:endpoint"),
        data=b"{not valid json",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["error"]["code"] == -32700
    assert body["id"] is None


@pytest.mark.django_db
def test_unknown_method_returns_method_not_found(client: Client, token: str) -> None:
    """An unrecognised JSON-RPC method gets a -32601 error envelope."""
    response = _post(
        client, {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}, token
    )
    body = json.loads(response.content)
    assert body["error"]["code"] == -32601


@pytest.mark.django_db
def test_notification_returns_204_with_no_body(client: Client, token: str) -> None:
    """A JSON-RPC notification (no 'id') gets a bare 204, no JSON body."""
    response = _post(
        client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token
    )
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.django_db
def test_post_succeeds_without_a_csrf_token(client: Client, token: str) -> None:
    """The endpoint is CSRF-exempt — a client that enforces CSRF still succeeds."""
    strict_client = Client(enforce_csrf_checks=True)
    response = _post(
        strict_client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_ip_rate_limit_exceeded_returns_403(client: Client) -> None:
    """The 121st request within a minute from one IP is blocked (block=True).

    The requests carry no token, so each is a 401 — the IP limit counts them
    all, which is what throttles token guessing from one address.

    The clock is frozen for the same reason as the share-redirect abuse
    bounds (SNOW-603): django_ratelimit keys its counter on the end of the
    current window, so a loop that straddles that boundary starts counting
    again from 1 and the request that should be refused succeeds.
    """
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    with override_settings(RATELIMIT_ENABLE=True), freeze_time():
        for _ in range(120):
            response = _post(client, body)
            assert response.status_code == 401
        blocked = _post(client, body)
        assert blocked.status_code == 403


@pytest.mark.django_db
def test_per_user_rate_limit_returns_429(client: Client, token: str) -> None:
    """The 61st request within a minute from one account is 429."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    with override_settings(RATELIMIT_ENABLE=True), freeze_time():
        for _ in range(60):
            assert _post(client, body, token).status_code == 200
        limited = _post(client, body, token)
    assert limited.status_code == 429
    assert limited["Cache-Control"] == "no-store"


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/mcp/", "/api/mcp"])
def test_missing_token_is_401_with_resource_metadata(client: Client, path: str) -> None:
    """No token: 401 naming the metadata for the exact path called."""
    response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, url=path)
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == (
        'Bearer resource_metadata="http://testserver/.well-known/'
        f'oauth-protected-resource{path}", scope="mcp"'
    )
    assert response["Cache-Control"] == "no-store"
    assert "jsonrpc" not in json.loads(response.content)


@pytest.mark.django_db
def test_the_challenge_resolves_to_a_matching_resource(client: Client) -> None:
    """Following the challenge's URL yields ``resource`` equal to the path called."""
    for path in ("/api/mcp/", "/api/mcp"):
        response = _post(
            client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, url=path
        )
        header = response["WWW-Authenticate"]
        metadata_url = header.split('resource_metadata="', 1)[1].split('"', 1)[0]
        document = client.get(metadata_url.removeprefix("http://testserver")).json()
        assert document["resource"] == f"http://testserver{path}"


@pytest.mark.django_db
def test_rejected_token_is_401_invalid_token(client: Client) -> None:
    """A token that does not authenticate adds error="invalid_token"."""
    response = _post(
        client, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="sd_at_bogus"
    )
    assert response.status_code == 401
    assert 'error="invalid_token"' in response["WWW-Authenticate"]


@pytest.mark.django_db
def test_non_bearer_scheme_is_treated_as_no_token(client: Client) -> None:
    """Basic credentials are not a bearer token."""
    response = client.post(
        reverse("api:mcp:endpoint"),
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Basic dXNlcjpwYXNz",
    )
    assert response.status_code == 401
    assert "invalid_token" not in response["WWW-Authenticate"]
