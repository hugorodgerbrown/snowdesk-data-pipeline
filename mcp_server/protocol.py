"""
mcp_server/protocol.py — JSON-RPC 2.0 envelope + MCP method router.

Implements just enough of JSON-RPC 2.0 (https://www.jsonrpc.org/specification)
and the MCP method set (https://modelcontextprotocol.io) for a stateless,
POST-only tool server: ``initialize``, ``notifications/initialized``,
``ping``, ``tools/list``, and ``tools/call``. No session state, no
server-initiated messages, no batching — every call is a single request in,
a single response (or, for a notification, no response at all) out.

``dispatch()`` is the only entry point ``mcp_server/views.py`` calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

from django.conf import settings

from mcp_server.tools import TOOLS, ToolError

logger = logging.getLogger(__name__)

#: Advertised in the ``initialize`` response. A permissive server that
#: accepts any client-requested version and states its own preferred
#: version is expected to stay compatible with Claude Desktop / claude.ai
#: for the foreseeable future — see docs/mcp-server.md.
PROTOCOL_VERSION = "2025-11-05"

#: JSON-RPC 2.0 reserved error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ProtocolError(Exception):
    """A method handler's request to fail the call with a specific JSON-RPC code.

    Raised by handlers (in this module or in ``mcp_server.tools``) for
    conditions that are the *caller's* fault — an unknown tool name, a
    malformed argument — as opposed to an unexpected server bug, which
    propagates as a plain exception and is reported as ``INTERNAL_ERROR``.
    """

    def __init__(self, code: int, message: str) -> None:
        """Store the JSON-RPC error code and message to report.

        Args:
            code: One of the module-level JSON-RPC error code constants.
            message: Short, client-safe description of the failure.

        """
        super().__init__(message)
        self.code = code
        self.message = message


def parse_error_response() -> dict[str, Any]:
    """Return the JSON-RPC envelope for a request body that isn't valid JSON.

    Used directly by ``mcp_server.views.mcp_endpoint`` — a body that fails
    ``json.loads`` never reaches :func:`dispatch`, since there is nothing
    for ``dispatch`` to inspect.

    Returns:
        A JSON-RPC 2.0 error envelope with code ``-32700`` and a null id
        (the request could not be parsed far enough to recover one).

    """
    return _error_envelope(None, PARSE_ERROR, "Parse error")


def _error_envelope(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error response envelope.

    Args:
        request_id: The originating request's ``id`` (may be ``None``).
        code: A JSON-RPC error code.
        message: Short, client-safe description of the failure.

    Returns:
        A dict ready to serialise as the HTTP response body.

    """
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result_envelope(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 success response envelope.

    Args:
        request_id: The originating request's ``id``.
        result: The method's return value.

    Returns:
        A dict ready to serialise as the HTTP response body.

    """
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class _ParsedRequest(NamedTuple):
    """A validated JSON-RPC request, ready to route to a method handler."""

    method: str
    params: dict[str, Any]
    request_id: Any
    is_notification: bool


def _parse_request(payload: Any) -> _ParsedRequest | dict[str, Any]:
    """Validate one JSON-RPC request envelope.

    Args:
        payload: The parsed JSON request body (whatever ``json.loads``
            returned — not necessarily a dict).

    Returns:
        A :class:`_ParsedRequest` on success, or an error envelope dict
        (``-32600``/``-32602``) the caller should return as-is.

    """
    if not isinstance(payload, dict):
        return _error_envelope(None, INVALID_REQUEST, "Request must be a JSON object")
    if payload.get("jsonrpc") != "2.0":
        return _error_envelope(
            payload.get("id"), INVALID_REQUEST, "'jsonrpc' must be \"2.0\""
        )
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        return _error_envelope(
            payload.get("id"), INVALID_REQUEST, "Missing or invalid 'method'"
        )
    params = payload.get("params", {})
    if not isinstance(params, dict):
        return _error_envelope(
            payload.get("id"), INVALID_PARAMS, "'params' must be an object"
        )
    return _ParsedRequest(
        method=method,
        params=params,
        request_id=payload.get("id"),
        is_notification="id" not in payload,
    )


def _invoke_handler(handler: Any, parsed: _ParsedRequest) -> dict[str, Any]:
    """Call a method handler and turn any failure into an error envelope.

    Split out of :func:`dispatch` purely to keep that function's cyclomatic
    complexity within the project's lint threshold — no behavioural change
    from the inline version (mirrors ``analytics.views._parse_events``).

    Args:
        handler: The resolved method handler.
        parsed: The validated request being dispatched.

    Returns:
        Either ``{"result": ...}`` or ``{"error": {"code": ..., "message":
        ...}}`` — never both.

    """
    try:
        return {"result": handler(parsed.params)}
    except ProtocolError as exc:
        return {"error": {"code": exc.code, "message": exc.message}}
    except Exception:
        # Any bug in a handler is ours, not the caller's — never leak the
        # exception's text to the client (docs/mcp-server.md error codes).
        logger.exception("mcp_server: unhandled error in method %s", parsed.method)
        return {"error": {"code": INTERNAL_ERROR, "message": "Internal error"}}


def dispatch(payload: Any) -> dict[str, Any] | None:
    """Route one parsed JSON-RPC request to its MCP method handler.

    Args:
        payload: The parsed JSON request body.

    Returns:
        A JSON-RPC response envelope, or ``None`` for a notification (a
        request with no ``id`` key) or a malformed envelope missing an
        ``id`` — either way, JSON-RPC forbids a response body.

    """
    parsed = _parse_request(payload)
    if isinstance(parsed, dict):
        return parsed

    handler = _METHOD_HANDLERS.get(parsed.method)
    if handler is None:
        if parsed.is_notification:
            return None
        return _error_envelope(
            parsed.request_id, METHOD_NOT_FOUND, f"Unknown method: {parsed.method}"
        )

    outcome = _invoke_handler(handler, parsed)
    if parsed.is_notification:
        return None
    if "error" in outcome:
        return _error_envelope(
            parsed.request_id, outcome["error"]["code"], outcome["error"]["message"]
        )
    return _result_envelope(parsed.request_id, outcome["result"] or {})


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001 — MCP handshake carries no inputs we need
    """Handle the ``initialize`` method — the MCP handshake.

    Args:
        params: The client's ``initialize`` params (``protocolVersion``,
            ``capabilities``, ``clientInfo``). Unused: this server is
            permissive about client version and capabilities — it always
            advertises its own preferred version and its fixed tool set.

    Returns:
        The ``InitializeResult`` dict: ``protocolVersion``,
        ``capabilities``, and ``serverInfo``.

    """
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "snowdesk", "version": settings.APP_VERSION},
    }


def _handle_ping(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001 — ping carries no params
    """Handle the ``ping`` method — a liveness check with an empty result."""
    return {}


def _handle_notifications_initialized(
    params: dict[str, Any],  # noqa: ARG001 — notification carries no params we use
) -> dict[str, Any]:
    """Acknowledge the client's ``notifications/initialized`` notification.

    A notification never receives a response body — :func:`dispatch`
    discards this return value — so the function exists purely to occupy
    a slot in ``_METHOD_HANDLERS`` and complete the handshake without
    falling through to "unknown method".
    """
    return {}


def _handle_tools_list(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001 — tools/list carries no params
    """Handle ``tools/list`` — advertise the fixed v1 tool set.

    Returns:
        ``{"tools": [...]}`` — one entry per registered tool, each with
        ``name``, ``description``, and ``inputSchema``.

    """
    return {
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.input_schema,
            }
            for spec in TOOLS.values()
        ]
    }


def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ``tools/call`` — invoke one registered tool by name.

    Args:
        params: ``{"name": <tool name>, "arguments": {...}}``.

    Returns:
        An MCP ``CallToolResult`` dict: ``content`` (always present),
        ``structuredContent`` (the tool's raw return value, on success),
        and ``isError``.

    Raises:
        ProtocolError: ``INVALID_PARAMS`` when ``name`` is missing, not a
            string, unregistered, or when ``arguments`` isn't an object —
            these are faults in the ``tools/call`` request itself, not in
            the tool's own domain logic.

    """
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ProtocolError(INVALID_PARAMS, "Missing or invalid 'name'")
    spec = TOOLS.get(name)
    if spec is None:
        raise ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ProtocolError(INVALID_PARAMS, "'arguments' must be an object")

    try:
        result = spec.handler(arguments)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}

    text = result.get("summary")
    if not isinstance(text, str):
        text = json.dumps(result, default=str)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
        "isError": False,
    }


_METHOD_HANDLERS: dict[str, Any] = {
    "initialize": _handle_initialize,
    "notifications/initialized": _handle_notifications_initialized,
    "ping": _handle_ping,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}
