"""
mcp_server/protocol.py — JSON-RPC 2.0 envelope + MCP method router.

Stub — routing is wired end to end (SNOW-391 build order step 2) but every
method currently returns "not implemented". Filled in once ``normalise.py``,
``season.py``, ``resolvers.py``, and ``tools.py`` land.
"""

from __future__ import annotations

from typing import Any

#: JSON-RPC 2.0 reserved error codes (https://www.jsonrpc.org/specification).
METHOD_NOT_FOUND = -32601


def parse_error_response() -> dict[str, Any]:
    """Return the JSON-RPC envelope for an unparsable request body.

    Returns:
        A JSON-RPC 2.0 error envelope with code ``-32700`` and a null id
        (the request could not be parsed far enough to recover one).

    """
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }


def dispatch(payload: Any) -> dict[str, Any] | None:
    """Route one parsed JSON-RPC request to its MCP method handler.

    Args:
        payload: The parsed JSON request body.

    Returns:
        A JSON-RPC response envelope, or ``None`` for a notification
        (a request with no ``id`` key), which must get no response body.

    """
    request_id = payload.get("id") if isinstance(payload, dict) else None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": METHOD_NOT_FOUND, "message": "not implemented"},
    }
