"""
tests/mcp_server/test_protocol.py — Tests for apps.mcp_server.protocol.

Covers the JSON-RPC 2.0 envelope (every reserved error code), the MCP
handshake methods (``initialize``, ``notifications/initialized``,
``ping``), and the tool-invocation methods (``tools/list``,
``tools/call``).

The ``tools/call`` section also exercises every ``_handle_*`` adapter in
``apps.mcp_server.tools`` end-to-end (the dispatcher entry point a real MCP
client actually calls), plus every argument-validation error branch in
``apps.mcp_server.tools``'s ``_require_str`` / ``_required_iso_date`` /
``_optional_iso_date`` helpers — the direct-call tests in
``tests/mcp_server/test_tools.py`` only cover the business functions, not
the adapters that unpack a JSON-RPC ``arguments`` dict into them.
"""

from __future__ import annotations

import datetime
from datetime import UTC
from typing import Any

import pytest
from django.core.cache import cache
from freezegun import freeze_time

from apps.bulletins.models import RegionDayRating
from apps.mcp_server import protocol
from apps.mcp_server.tools import TOOLS
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
)


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


def test_initialize_echoes_supported_client_version() -> None:
    """initialize() echoes the client's protocolVersion when we support it."""
    response = protocol.dispatch(
        _request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0.0.1"},
            },
        )
    )
    assert response is not None
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "snowdesk"
    assert "tools" in result["capabilities"]


def test_initialize_falls_back_to_preferred_on_unsupported_client_version() -> None:
    """initialize() returns PROTOCOL_VERSION when the client asks for an unknown version."""
    response = protocol.dispatch(
        _request(
            "initialize",
            {
                "protocolVersion": "2099-01-01",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0.0.1"},
            },
        )
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == protocol.PROTOCOL_VERSION


def test_initialize_returns_preferred_version_when_client_omits_it() -> None:
    """initialize() returns PROTOCOL_VERSION when the client sends no protocolVersion."""
    response = protocol.dispatch(_request("initialize", {}))
    assert response is not None
    assert response["result"]["protocolVersion"] == protocol.PROTOCOL_VERSION


def test_ping_returns_empty_result() -> None:
    """ping() returns an empty result object."""
    response = protocol.dispatch(_request("ping"))
    assert response is not None
    assert response["result"] == {}


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


def test_tools_list_returns_all_registered_tools() -> None:
    """tools/list advertises every registered tool with name/description/inputSchema."""
    response = protocol.dispatch(_request("tools/list"))
    assert response is not None
    names = {t["name"] for t in response["result"]["tools"]}
    assert names == set(TOOLS.keys())
    assert names == {
        "search_regions",
        "get_current_conditions",
        "get_avalanche_problems",
        "get_danger_history",
        "list_resorts_in_region",
        "get_bulletin_metadata",
        "get_bulletin_raw",
        "bulk_current_conditions",
        "find_regions_near",
        "get_danger_trend",
        "get_regional_snapshot",
        "list_regions",
        "region_info",
        "list_locations_in_region",
        "get_location_weather",
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


@pytest.mark.django_db
def test_tools_call_get_avalanche_problems_returns_structured_content() -> None:
    """get_avalanche_problems round-trips through tools/call and returns problem data."""
    region = MicroRegionFactory.create(region_id="CH-4115", name="Bas-Valais")
    target_date = datetime.date(2026, 3, 10)
    bulletin = BulletinFactory.create(
        issued_at=datetime.datetime.combine(
            target_date, datetime.time(17, 0), tzinfo=UTC
        ),
        valid_from=datetime.datetime.combine(
            target_date, datetime.time(8, 0), tzinfo=UTC
        ),
        valid_to=datetime.datetime.combine(
            target_date, datetime.time(17, 0), tzinfo=UTC
        ),
        raw_data={
            "properties": {
                "avalancheProblems": [
                    {
                        "problemType": "wind_slab",
                        "dangerRatingValue": "considerable",
                        "aspects": ["N"],
                    }
                ]
            }
        },
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)

    response = protocol.dispatch(
        _request(
            "tools/call",
            {
                "name": "get_avalanche_problems",
                "arguments": {"region_id": "CH-4115", "date": "2026-03-10"},
            },
        )
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["region_id"] == "CH-4115"
    assert result["structuredContent"]["count"] == 1
    assert result["structuredContent"]["problems"][0]["problem_type"] == "wind_slab"


@pytest.mark.django_db
@freeze_time("2026-03-10")
def test_tools_call_get_danger_history_returns_structured_content() -> None:
    """get_danger_history round-trips through tools/call and returns day data."""
    region = MicroRegionFactory.create(region_id="CH-4115", name="Bas-Valais")
    RegionDayRatingFactory.create(
        region=region,
        date=datetime.date(2026, 1, 10),
        max_rating=RegionDayRating.Rating.HIGH,
    )
    response = protocol.dispatch(
        _request(
            "tools/call",
            {
                "name": "get_danger_history",
                "arguments": {
                    "region_id": "CH-4115",
                    "from_date": "2026-01-01",
                    "to_date": "2026-01-31",
                },
            },
        )
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["region_id"] == "CH-4115"
    assert result["structuredContent"]["count"] == 1
    assert result["structuredContent"]["days"][0]["max_rating"] == "high"


@pytest.mark.django_db
def test_tools_call_list_resorts_in_region_returns_structured_content() -> None:
    """list_resorts_in_region round-trips through tools/call and returns resorts."""
    region = MicroRegionFactory.create(region_id="CH-4115", name="Bas-Valais")
    ResortFactory.create(
        name="Verbier", region=region, latitude=46.0961, longitude=7.2286
    )
    response = protocol.dispatch(
        _request(
            "tools/call",
            {
                "name": "list_resorts_in_region",
                "arguments": {"region_id": "CH-4115"},
            },
        )
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["count"] == 1
    assert result["structuredContent"]["resorts"][0]["name"] == "Verbier"


# ---------------------------------------------------------------------------
# tools/call — argument-validation error branches
# ---------------------------------------------------------------------------
#
# These exercise the _require_str / _required_iso_date / _optional_iso_date
# helpers in apps.mcp_server.tools via the same tools/call round-trip a real MCP
# client uses. A validation failure is a ToolError, so it surfaces as
# isError=True with the message in content[0]["text"] — not a JSON-RPC
# protocol-level error.


def _call_tool(name: str, arguments: dict[str, object]) -> dict[str, Any]:
    """Dispatch a tools/call request and return the unwrapped CallToolResult."""
    response = protocol.dispatch(
        _request("tools/call", {"name": name, "arguments": arguments})
    )
    assert response is not None
    result: dict[str, Any] = response["result"]
    return result


def test_require_str_missing_key_is_a_tool_error() -> None:
    """A missing required string argument (e.g. 'query') is a tool error."""
    result = _call_tool("search_regions", {})
    assert result["isError"] is True
    assert "query" in result["content"][0]["text"]


def test_require_str_blank_value_is_a_tool_error() -> None:
    """A blank (whitespace-only) required string argument is a tool error."""
    result = _call_tool("search_regions", {"query": "   "})
    assert result["isError"] is True
    assert "query" in result["content"][0]["text"]


def test_require_str_non_string_value_is_a_tool_error() -> None:
    """A non-string value for a required string argument is a tool error."""
    result = _call_tool("search_regions", {"query": 42})
    assert result["isError"] is True
    assert "query" in result["content"][0]["text"]


def test_required_iso_date_missing_key_is_a_tool_error() -> None:
    """A missing required date argument (e.g. 'from_date') is a tool error."""
    result = _call_tool(
        "get_danger_history",
        {"region_id": "CH-4115", "to_date": "2026-01-31"},
    )
    assert result["isError"] is True
    assert "from_date" in result["content"][0]["text"]


def test_required_iso_date_non_string_value_is_a_tool_error() -> None:
    """A non-string value for a required date argument is a tool error."""
    result = _call_tool(
        "get_danger_history",
        {"region_id": "CH-4115", "from_date": 20260101, "to_date": "2026-01-31"},
    )
    assert result["isError"] is True
    assert "from_date" in result["content"][0]["text"]


def test_required_iso_date_malformed_string_is_a_tool_error() -> None:
    """A malformed (unparsable) ISO date string is a tool error."""
    result = _call_tool(
        "get_danger_history",
        {
            "region_id": "CH-4115",
            "from_date": "not-a-date",
            "to_date": "2026-01-31",
        },
    )
    assert result["isError"] is True
    assert "from_date" in result["content"][0]["text"]


def test_optional_iso_date_non_string_value_is_a_tool_error() -> None:
    """A non-string value for an optional date argument is a tool error."""
    result = _call_tool(
        "get_current_conditions",
        {"region_id": "CH-4115", "date": 20260101},
    )
    assert result["isError"] is True
    assert "date" in result["content"][0]["text"]


@pytest.mark.django_db
def test_optional_iso_date_valid_string_is_parsed() -> None:
    """A well-formed 'date' string is parsed and used, not just left to default."""
    MicroRegionFactory.create(region_id="CH-4115", name="Bas-Valais")
    result = _call_tool(
        "get_current_conditions",
        {"region_id": "CH-4115", "date": "2026-04-08"},
    )
    assert result["isError"] is False
    assert result["structuredContent"]["date"] == "2026-04-08"


def test_min_rating_non_string_value_is_a_tool_error() -> None:
    """A non-string 'min_rating' value is a tool error."""
    result = _call_tool(
        "get_danger_history",
        {
            "region_id": "CH-4115",
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
            "min_rating": 3,
        },
    )
    assert result["isError"] is True
    assert "min_rating" in result["content"][0]["text"]
