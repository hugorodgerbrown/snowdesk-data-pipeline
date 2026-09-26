"""
apps/mcp_server/views.py — HTTP view for the hosted MCP JSON-RPC endpoint.

Exposes a single view, ``mcp_endpoint``, mounted at ``POST /api/mcp/``
(``apps/public/api_urls.py``). Mirrors the shape of
``apps/analytics/views.py::telemetry_receive`` — the other stateless,
CSRF-exempt, rate-limited JSON POST endpoint in this codebase — but returns
a JSON-RPC 2.0 envelope rather than a bare 204.

**Authentication (SNOW-1035).** Every request needs an OAuth access token
issued by Snowdesk's own authorization server (``apps.oauth``), sent as
``Authorization: Bearer …``. Without one the answer is ``401`` with a
``WWW-Authenticate`` header naming the protected-resource metadata, which
is how an MCP client (Claude) discovers where to send the user to sign in.
Claude ignores that header on any other status, so it must be a 401.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.core import is_ratelimited
from django_ratelimit.decorators import ratelimit

from apps.mcp_server import protocol
from apps.oauth.services.resource import request_origin
from apps.oauth.services.tokens import authenticate_bearer

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


# Per-user ceiling. The per-IP limit on the decorator stays (raised to
# 120/m) to throttle token guessing from one address; this one bounds what a
# single account can spend, whichever address its client calls from.
USER_RATE: str = "60/m"


def _bearer_token(request: HttpRequest) -> str | None:
    """Return the token from ``Authorization: Bearer <token>``, or None.

    Args:
        request: The incoming request.

    Returns:
        The token, or None when the header is absent or another scheme.

    """
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _unauthorised(request: HttpRequest, *, token_sent: bool) -> HttpResponse:
    """Return the 401 that starts an MCP client's OAuth discovery.

    ``resource_metadata`` names the protected-resource document for the
    exact path the client called, so its ``resource`` matches the URL the
    user typed (``/api/mcp/`` or ``/api/mcp``).

    Args:
        request: The incoming request.
        token_sent: Whether a bearer token was presented (and rejected),
            which adds ``error="invalid_token"`` (RFC 6750 §3.1).

    Returns:
        A 401 JSON response with the ``WWW-Authenticate`` challenge.

    """
    metadata = (
        f"{request_origin(request)}/.well-known/oauth-protected-resource{request.path}"
    )
    challenge = f'Bearer resource_metadata="{metadata}", scope="mcp"'
    if token_sent:
        challenge += ', error="invalid_token"'
    response = JsonResponse(
        {
            "error": "invalid_token" if token_sent else "unauthorized",
            "error_description": "A Snowdesk account is required. Connect with OAuth.",
        },
        status=401,
    )
    response["WWW-Authenticate"] = challenge
    return _no_store(response)


# JSON-RPC clients (Claude Desktop, MCP Inspector, curl) cannot mint CSRF
# tokens — the same rationale as apps.analytics.views.telemetry_receive. The
# endpoint is read-only from the caller's point of view (every tool is a
# query, nothing mutates Snowdesk state), so the CSRF risk surface is empty.
# Rationale: docs/mcp-server.md.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="120/m", block=True)
def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    """Handle one JSON-RPC 2.0 request against the MCP tool server.

    Contract:

    * Only ``POST`` is accepted; ``@require_POST`` returns 405 with
      ``Allow: POST`` for any other verb.
    * CSRF-exempt — see the module-level comment above.
    * Requires a Snowdesk OAuth access token (``Authorization: Bearer``).
      No token, or a rejected one, is a ``401`` carrying the
      ``WWW-Authenticate`` challenge — see the module docstring.
    * Rate-limited to 120 requests / minute per source IP
      (``django-ratelimit``, ``block=True`` — over-limit requests are
      rejected automatically rather than falling through to the view),
      and to 60 requests / minute per user (``429``).
    * The request body must be a single JSON object (batched JSON-RPC
      requests are not supported in v1). A malformed body yields a
      JSON-RPC ``-32700 Parse error`` envelope rather than a raw 400, so
      MCP clients can handle it via their normal error path.
    * Every response — success or error — carries
      ``Cache-Control: no-store`` and ``Content-Type: application/json``.
    * A JSON-RPC *notification* (a request object with no ``id`` key,
      e.g. ``notifications/initialized``) is accepted and processed but
      gets no response body — returns ``202 Accepted``, which the MCP
      Streamable HTTP transport requires ("the server MUST return HTTP
      status code 202 Accepted with no body"). A ``204`` here left
      Claude's connector stuck after the handshake, never listing tools.

    Args:
        request: The incoming POST request.

    Returns:
        A JSON HttpResponse carrying the JSON-RPC result or error
        envelope, a bare 202 for notifications, 401 without a valid
        token, or 429 over the per-user limit.

    """
    raw_token = _bearer_token(request)
    user = authenticate_bearer(raw_token, request) if raw_token else None
    if user is None:
        return _unauthorised(request, token_sent=raw_token is not None)

    if is_ratelimited(
        request,
        group="mcp:user",
        key=lambda _group, _request: f"user:{user.pk}",
        rate=USER_RATE,
        increment=True,
    ):
        return _no_store(JsonResponse({"error": "rate_limited"}, status=429))

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.info("mcp_server: invalid JSON body: %s", exc)
        return _no_store(JsonResponse(protocol.parse_error_response(), status=200))

    response_body = protocol.dispatch(payload)
    if response_body is None:
        # Notification — MCP/JSON-RPC forbids a response body, and the
        # Streamable HTTP transport requires 202, not 204 (see docstring).
        return _no_store(HttpResponse(status=202))

    return _no_store(JsonResponse(response_body))
