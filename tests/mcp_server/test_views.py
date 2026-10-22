"""
tests/mcp_server/test_views.py — Tests for apps.mcp_server.views.

Two layers. mcp-auth's ``MCPAuthContract`` runs Claude's connector
requirements against both endpoints — discovery, registration, consent,
the code and refresh exchange, audience binding, the connect rule — so a
settings or URL change that would break a connector fails here (SNOW-1193).
The rest covers what Snowdesk adds on top: the JSON-RPC answer itself (the
parse error, the 202 for a notification, no-store on every response), the
slash-less aliases, the per-IP limit, the staff gate on the ops server and
its catalogue (SNOW-1191), and (SNOW-1169) that an ``Idempotency-Key`` never
replays a cached 200 past the bearer check.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from freezegun import freeze_time
from mcp_auth.connected import disconnect
from mcp_auth.testing import MCPAuthContract
from oauth2_provider.models import AccessToken

from apps.core.models import IdempotencyRecord
from apps.mcp_server import protocol
from apps.mcp_server.ops_tools import OPS_TOOLS
from tests.factories import AccountFactory, OAuthAccessTokenFactory

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as _Response

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
PUBLIC = "http://testserver/api/mcp/"
OPS = "http://testserver/api/ops/mcp/"


class TestPublicEndpointMeetsTheConnectorContract(MCPAuthContract):
    """Claude's connector requirements against ``/api/mcp/``."""

    mcp_path = "/api/mcp/"

    def make_allowed_user(self, django_user_model: type[User]) -> User:
        """A verified account — the connect rule's yes."""
        return AccountFactory.create().user

    def make_refused_user(self, django_user_model: type[User]) -> User:
        """An unverified account — signed in, but the connect rule's no."""
        return AccountFactory.create(is_verified=False).user


class TestOpsEndpointMeetsTheConnectorContract(MCPAuthContract):
    """The same requirements against ``/api/ops/mcp/``, for a staff account."""

    mcp_path = "/api/ops/mcp/"

    def make_allowed_user(self, django_user_model: type[User]) -> User:
        """A verified staff account."""
        return AccountFactory.create(user__is_staff=True).user

    def make_refused_user(self, django_user_model: type[User]) -> User:
        """An unverified staff account — refused before staff is asked."""
        return AccountFactory.create(is_verified=False, user__is_staff=True).user


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    """Clear the default cache, where the rate-limit counters live."""
    cache.clear()


def _token(resource: str = PUBLIC, **user: Any) -> str:
    """Return a live access token for a verified account, bound to ``resource``."""
    account = AccountFactory.create(**{f"user__{k}": v for k, v in user.items()})
    raw: str = OAuthAccessTokenFactory.create(
        user=account.user, resource=[resource]
    ).raw_token
    return raw


@pytest.fixture
def token(db: None) -> str:
    """Return a live access token for ``/api/mcp/``."""
    return _token()


def _post(
    client: Client,
    body: dict | bytes,
    token: str | None = None,
    url: str = "/api/mcp/",
    **headers: str,
) -> _Response:
    """POST a JSON-RPC envelope to ``url``, with ``token`` if given."""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        url,
        data=body if isinstance(body, bytes) else json.dumps(body),
        content_type="application/json",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# The JSON-RPC answer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_post_happy_path_returns_json_rpc_result(client: Client, token: str) -> None:
    """A well-formed POST returns a JSON-RPC result envelope."""
    response = _post(client, PING, token)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert json.loads(response.content) == {"jsonrpc": "2.0", "id": 1, "result": {}}


@pytest.mark.django_db
def test_every_response_carries_cache_control_no_store(
    client: Client, token: str
) -> None:
    """Every response — success, error, notification or challenge — is no-store."""
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    for response in (
        _post(client, PING, token),
        _post(client, b"not json", token),
        _post(client, notification, token),
        _post(client, PING),
    ):
        assert response["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_malformed_json_body_returns_parse_error(client: Client, token: str) -> None:
    """A body that isn't valid JSON gets a -32700 Parse error envelope."""
    response = _post(client, b"{not valid json", token)
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["error"]["code"] == -32700
    assert body["id"] is None


@pytest.mark.django_db
def test_unknown_method_returns_method_not_found(client: Client, token: str) -> None:
    """An unrecognised JSON-RPC method gets a -32601 error envelope."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}
    assert json.loads(_post(client, body, token).content)["error"]["code"] == -32601


@pytest.mark.django_db
def test_notification_returns_202_with_no_body(client: Client, token: str) -> None:
    """A JSON-RPC notification (no 'id') gets a bare 202, as MCP requires."""
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    response = _post(client, notification, token)
    assert response.status_code == 202
    assert response.content == b""


@pytest.mark.django_db
def test_post_succeeds_without_a_csrf_token(token: str) -> None:
    """The endpoint is CSRF-exempt — a client that enforces CSRF still succeeds."""
    assert _post(Client(enforce_csrf_checks=True), PING, token).status_code == 200


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def test_each_endpoint_has_a_slashless_alias() -> None:
    """Both catalogues answer with and without the trailing slash."""
    assert reverse("api:mcp:endpoint") == "/api/mcp/"
    assert reverse("api:mcp:endpoint_noslash") == "/api/mcp"
    assert reverse("api:mcp:ops") == "/api/ops/mcp/"
    assert reverse("api:mcp:ops_noslash") == "/api/ops/mcp"


@pytest.mark.django_db
def test_slashless_url_reaches_the_endpoint_without_a_redirect(
    client: Client, token: str
) -> None:
    """POST /api/mcp hits the view directly, no 301.

    Remote MCP connectors POST ``initialize`` to exactly the URL the user
    typed; ``APPEND_SLASH`` would turn that POST into a 301 the client
    cannot replay. A token bound to ``/api/mcp/`` is good for both.
    """
    response = _post(client, PING, token, url="/api/mcp")
    assert response.status_code == 200
    assert json.loads(response.content) == {"jsonrpc": "2.0", "id": 1, "result": {}}


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/mcp", "/api/ops/mcp/", "/api/ops/mcp"])
def test_missing_token_names_the_metadata_for_the_path_called(
    client: Client, path: str
) -> None:
    """No token: a 401 whose challenge names this exact path's metadata."""
    response = _post(client, PING, url=path)
    assert response.status_code == 401
    assert (
        f"http://testserver/.well-known/oauth-protected-resource{path}"
        in response["WWW-Authenticate"]
    )


@pytest.mark.django_db
def test_ip_rate_limit_exceeded_returns_403(client: Client) -> None:
    """The 121st request within a minute from one IP is blocked.

    The requests carry no token, so each is a 401 — the IP limit counts them
    all, which is what throttles token guessing from one address. The clock
    is frozen because django_ratelimit keys its counter on the end of the
    current window (SNOW-603).
    """
    with override_settings(RATELIMIT_ENABLE=True), freeze_time():
        for _ in range(120):
            assert _post(client, PING).status_code == 401
        assert _post(client, PING).status_code == 403


# ---------------------------------------------------------------------------
# The ops server (SNOW-1191)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ops_server_is_403_for_a_non_staff_account(client: Client) -> None:
    """A good token for a non-staff account is refused, not challenged."""
    response = _post(client, PING, _token(OPS, is_staff=False), url="/api/ops/mcp/")
    assert response.status_code == 403
    assert json.loads(response.content)["error"] == "forbidden"
    assert "WWW-Authenticate" not in response


@pytest.mark.django_db
def test_ops_server_lists_the_ops_catalogue_to_staff(client: Client) -> None:
    """Staff see the ops tools, under the ops server's own name."""
    token = _token(OPS, is_staff=True)
    init = _post(
        client,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        token,
        url="/api/ops/mcp/",
    )
    assert json.loads(init.content)["result"]["serverInfo"]["name"] == "snowdesk-ops"
    listed = _post(
        client,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        token,
        url="/api/ops/mcp",
    )
    names = {tool["name"] for tool in json.loads(listed.content)["result"]["tools"]}
    assert names == {tool.name for tool in OPS_TOOLS.values()}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("bound_to", "called"),
    [(PUBLIC, "/api/ops/mcp/"), (OPS, "/api/mcp/")],
)
def test_a_token_is_good_only_for_its_own_endpoint(
    client: Client, bound_to: str, called: str
) -> None:
    """Neither endpoint's token opens the other: they are siblings, not nested."""
    token = _token(bound_to, is_staff=True)
    response = _post(client, PING, token, url=called)
    assert response.status_code == 401
    assert 'error="invalid_token"' in response["WWW-Authenticate"]


# ---------------------------------------------------------------------------
# SNOW-1169 — an Idempotency-Key never replays past the bearer check
# ---------------------------------------------------------------------------

_REPLAY_BODY = {"jsonrpc": "2.0", "id": 7, "method": "ping"}
_REPLAY_KEY = "mcp-replay-key"


def _keyed_post(client: Client, path: str, token: str | None) -> _Response:
    """POST the fixed replay body to ``path`` under the fixed Idempotency-Key."""
    return _post(
        client, _REPLAY_BODY, token, url=path, **{"Idempotency-Key": _REPLAY_KEY}
    )


def _prime(client: Client, path: str, token: str) -> None:
    """Send the keyed request once with a valid bearer and check it succeeded."""
    primed = _keyed_post(client, path, token)
    assert primed.status_code == 200
    assert json.loads(primed.content)["id"] == 7


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/mcp/", "/api/mcp"])
def test_replayed_key_without_a_bearer_is_401(
    client: Client, token: str, path: str
) -> None:
    """The same key and body with no Authorization header is challenged, not replayed."""
    _prime(client, path, token)
    replay = _keyed_post(client, path, None)
    assert replay.status_code == 401
    assert "resource_metadata=" in replay["WWW-Authenticate"]
    assert not IdempotencyRecord.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/mcp/", "/api/mcp"])
def test_replayed_key_with_a_garbage_bearer_is_401(
    client: Client, token: str, path: str
) -> None:
    """The same key and body with an unknown bearer is ``invalid_token``."""
    _prime(client, path, token)
    replay = _keyed_post(client, path, "garbage")
    assert replay.status_code == 401
    assert 'error="invalid_token"' in replay["WWW-Authenticate"]


@pytest.mark.django_db
def test_replayed_key_after_disconnect_is_401(client: Client) -> None:
    """Disconnecting the app stops the same key and body from being served."""
    token = _token()
    _prime(client, "/api/mcp/", token)
    row = AccessToken.objects.get()
    disconnect(row.user, row.application)
    assert _keyed_post(client, "/api/mcp/", token).status_code == 401


@pytest.mark.django_db
def test_replayed_key_on_the_ops_server_is_403_for_non_staff(client: Client) -> None:
    """A key primed by staff is never replayed to a non-staff token."""
    _prime(client, "/api/ops/mcp/", _token(OPS, is_staff=True))
    replay = _keyed_post(client, "/api/ops/mcp/", _token(OPS, is_staff=False))
    assert replay.status_code == 403


@pytest.mark.django_db
def test_valid_repeat_of_a_key_re_executes(client: Client, token: str) -> None:
    """A valid repeat of the same key and body runs the dispatcher again."""
    with mock.patch.object(protocol, "dispatch", wraps=protocol.dispatch) as dispatch:
        _prime(client, "/api/mcp/", token)
        _prime(client, "/api/mcp/", token)
    assert dispatch.call_count == 2
    assert not IdempotencyRecord.objects.exists()
