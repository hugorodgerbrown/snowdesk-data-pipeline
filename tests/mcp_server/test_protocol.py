"""
tests/mcp_server/test_protocol.py — Tests for mcp_server.protocol.

Covers the JSON-RPC 2.0 envelope (every reserved error code), the MCP
handshake methods (``initialize``, ``notifications/initialized``,
``ping``), and the tool-invocation methods (``tools/list``,
``tools/call``).
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from mcp_server import protocol
from mcp_server.tools import TOOLS
from tests.factories import MicroRegionFactory


@pytest.fixture(autouse=True)
def clear_mcp_candidate_cache() -> None:
    """Ensure the resolvers candidate-pool cache is clear before every test."""
    cache.clear()


def _request(method: str, params: dict | None = None, request_id: object = 1) -> dict:
    """Build a well-formed JSON-RPC 2.0 request envelope."""
    envelope: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        envelope["params"] = params
    if request_id is not None:
        envelope["id"] = request_id
    return envelope


# ---------------------------------------------------------------------------
# Envelope-level errors
# ---------------------------------------------------------------------------


def test_non_dict_payload_is_invalid_request() -> None:
    """A non-object payload (e.g. a bare list) is -32600 Invalid Request."""
    response = protocol.dispatch(["not", "an", "object"])
    assert response is not None
    assert response["error"]["code"] == protocol.INVALID_REQUEST


def test_wrong_jsonrpc_version_is_invalid_request() -> None:
    """A payload missing/mismatching 'jsonrpc': '2.0' is Invalid Request."""
    response = protocol.dispatch({"jsonrpc": "1.0", "method": "ping", "id": 1})
    assert response is not None
    assert response["error"]["code"] == protocol.INVALID_REQUEST


def test_missing_method_is_invalid_request() -> None:
    """A payload with no 'method' key is Invalid Request."""
    response = protocol.dispatch({"jsonrpc": "2.0", "id": 1})
    assert response is not None
    assert response["error"]["code"] == protocol.INVALID_REQUEST


def test_non_object_params_is_invalid_params() -> None:
    """A non-object 'params' value is -32602 Invalid Params."""
    response = protocol.dispatch(
        {"jsonrpc": "2.0", "method": "ping", "params": "bad", "id": 1}
    )
    assert response is not None
    assert response["error"]["code"] == protocol.INVALID_PARAMS


def test_unknown_method_is_method_not_found() -> None:
    """An unrecognised method is -32601 Method Not Found."""
    response = protocol.dispatch(_request("resources/list"))
    assert response is not None
    assert response["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_response_echoes_the_request_id() -> None:
    """The response envelope's 'id' matches the request's 'id'."""
    response = protocol.dispatch(_request("ping", request_id="abc-123"))
    assert response is not None
    assert response["id"] == "abc-123"


def test_parse_error_response_has_null_id() -> None:
    """parse_error_response() (used for unparsable bodies) has a null id."""
    response = protocol.parse_error_response()
    assert response["id"] is None
    assert response["error"]["code"] == protocol.PARSE_ERROR


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def test_notification_gets_no_response() -> None:
    """A request with no 'id' key (a notification) gets no response body."""
    response = protocol.dispatch(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert response is None


def test_notification_for_unknown_method_still_gets_no_response() -> None:
    """An unknown-method notification is silently dropped, not an error."""
    response = protocol.dispatch({"jsonrpc": "2.0", "method": "notifications/bogus"})
    assert response is None


# ---------------------------------------------------------------------------
# initialize / ping
# ---------------------------------------------------------------------------


def test_initialize_advertises_protocol_version_and_server_info() -> None:
    """initialize() returns the advertised protocol version and server info."""
    response = protocol.dispatch(
        _request(
            "initialize",
            {
                "protocolVersion": "2025-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0.0.1"},
            },
        )
    )
    assert response is not None
    result = response["result"]
    assert result["protocolVersion"] == protocol.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "snowdesk"
    assert "tools" in result["capabilities"]


def test_ping_returns_empty_result() -> None:
    """ping() returns an empty result object."""
    response = protocol.dispatch(_request("ping"))
    assert response is not None
    assert response["result"] == {}


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


def test_tools_list_returns_all_four_registered_tools() -> None:
    """tools/list advertises all four tools with name/description/inputSchema."""
    response = protocol.dispatch(_request("tools/list"))
    assert response is not None
    names = {t["name"] for t in response["result"]["tools"]}
    assert names == set(TOOLS.keys())
    assert names == {
        "search_regions",
        "get_current_conditions",
        "get_danger_history",
        "list_resorts_in_region",
    }
    for tool in response["result"]["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


# ---------------------------------------------------------------------------
# tools/call
# ---------------------------------------------------------------------------


def test_tools_call_unknown_tool_is_invalid_params() -> None:
    """tools/call with an unregistered tool name is -32602 Invalid Params."""
    response = protocol.dispatch(
        _request("tools/call", {"name": "not_a_real_tool", "arguments": {}})
    )
    assert response is not None
    assert response["error"]["code"] == protocol.INVALID_PARAMS


def test_tools_call_missing_name_is_invalid_params() -> None:
    """tools/call with no 'name' key is -32602 Invalid Params."""
    response = protocol.dispatch(_request("tools/call", {"arguments": {}}))
    assert response is not None
    assert response["error"]["code"] == protocol.INVALID_PARAMS


@pytest.mark.django_db
def test_tools_call_happy_path_returns_structured_content() -> None:
    """A successful tool call returns content, structuredContent, isError=False."""
    MicroRegionFactory.create(region_id="CH-4115", name="Bas-Valais")
    response = protocol.dispatch(
        _request(
            "tools/call", {"name": "search_regions", "arguments": {"query": "CH-4115"}}
        )
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert result["structuredContent"]["query"] == "CH-4115"


@pytest.mark.django_db
def test_tools_call_domain_error_is_reported_as_tool_error_not_protocol_error() -> None:
    """A domain-level failure (unknown region_id) is isError=True, not a JSON-RPC error."""
    response = protocol.dispatch(
        _request(
            "tools/call",
            {
                "name": "get_current_conditions",
                "arguments": {"region_id": "XX-0000"},
            },
        )
    )
    assert response is not None
    assert "error" not in response
    result = response["result"]
    assert result["isError"] is True
    assert "XX-0000" in result["content"][0]["text"]
