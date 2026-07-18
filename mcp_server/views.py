"""
mcp_server/views.py — HTTP view for the hosted MCP JSON-RPC endpoint.

Exposes a single view, ``mcp_endpoint``, mounted at ``POST /api/mcp/``
(``public/api_urls.py``). Mirrors the shape of
``analytics/views.py::telemetry_receive`` — the other stateless,
CSRF-exempt, rate-limited JSON POST endpoint in this codebase — but returns
a JSON-RPC 2.0 envelope rather than a bare 204.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from mcp_server import protocol

logger = logging.getLogger(__name__)


def _no_store(response: HttpResponse) -> HttpResponse:
    """Attach ``Cache-Control: no-store`` to a response.

    Every response from this endpoint is per-request JSON-RPC state; none
    of it is safe to cache at any layer (browser, CDN, or intermediate
    proxy).

    Args:
        response: The response to annotate.

    Returns:
        The same response, mutated in place, for chaining.

    """
    response["Cache-Control"] = "no-store"
    return response


# JSON-RPC clients (Claude Desktop, MCP Inspector, curl) cannot mint CSRF
# tokens — the same rationale as analytics.views.telemetry_receive. The
# endpoint is read-only from the caller's point of view (every tool is a
# query, nothing mutates Snowdesk state), so the CSRF risk surface is empty.
# Rationale: docs/mcp-server.md.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="60/m", block=True)
def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    """Handle one JSON-RPC 2.0 request against the MCP tool server.

    Contract:

    * Only ``POST`` is accepted; ``@require_POST`` returns 405 with
      ``Allow: POST`` for any other verb.
    * CSRF-exempt — see the module-level comment above.
    * Rate-limited to 60 requests / minute per source IP
      (``django-ratelimit``, ``block=True`` — over-limit requests are
      rejected automatically rather than falling through to the view).
    * The request body must be a single JSON object (batched JSON-RPC
      requests are not supported in v1). A malformed body yields a
      JSON-RPC ``-32700 Parse error`` envelope rather than a raw 400, so
      MCP clients can handle it via their normal error path.
    * Every response — success or error — carries
      ``Cache-Control: no-store`` and ``Content-Type: application/json``.
    * A JSON-RPC *notification* (a request object with no ``id`` key,
      e.g. ``notifications/initialized``) is accepted and processed but
      gets no response body — returns ``204 No Content``.

    Args:
        request: The incoming POST request.

    Returns:
        A JSON HttpResponse carrying the JSON-RPC result or error
        envelope, or a bare 204 for notifications.

    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.info("mcp_server: invalid JSON body: %s", exc)
        return _no_store(JsonResponse(protocol.parse_error_response(), status=200))

    response_body = protocol.dispatch(payload)
    if response_body is None:
        # Notification — MCP/JSON-RPC forbids a response body.
        return _no_store(HttpResponse(status=204))

    return _no_store(JsonResponse(response_body))
