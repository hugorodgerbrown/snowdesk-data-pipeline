"""
apps/mcp_server/views.py — HTTP views for the hosted MCP JSON-RPC endpoints.

Two endpoints, one shape, different tool catalogues:

* ``mcp_endpoint`` at ``POST /api/mcp/`` — the public catalogue (bulletins,
  danger ratings, resorts) for any connected account.
* ``ops_mcp_endpoint`` at ``POST /api/ops/mcp/`` (SNOW-1191) — the staff ops
  catalogue (the command run register, build info, ingest and estate
  health, and the waffle setters). A connected account that is not staff
  gets a 403.

The ops endpoint is a sibling path, not ``/api/mcp/ops/``: django-oauth-
toolkit checks a token's audience by URL prefix, so a token bound to
``/api/mcp/`` would also be accepted beneath it. Each endpoint's tokens are
bound to its own URL.

**Authentication (SNOW-1193).** Both are mcp-auth ``@mcp_endpoint`` views:
every request needs an OAuth access token from the mcp-auth server mounted
in ``config/urls.py``, bound to the endpoint (RFC 8707) and held by an
account the connect rule (``apps.oauth.policy.verified_account``) admits.
Anything else is a ``401`` whose ``WWW-Authenticate`` header names the
protected-resource metadata, which is how an MCP client (Claude) discovers
where to send the user to sign in. mcp-auth also applies the per-user rate
limit, marks every response ``no-store``, refuses any verb but POST with a
405, and exempts the view from CSRF (it reads only the Authorization
header). The per-IP limit here throttles token guessing from one address.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django_ratelimit.decorators import ratelimit
from mcp_auth.resource import mcp_endpoint as require_mcp_token

from apps.mcp_server import protocol
from apps.mcp_server.ops_tools import OPS_TOOLS

logger = logging.getLogger(__name__)

OPS_SERVER = protocol.ToolServer("snowdesk-ops", OPS_TOOLS, {})


def _answer(request: HttpRequest, server: protocol.ToolServer) -> HttpResponse:
    """Dispatch one JSON-RPC request body against ``server``.

    * The body must be a single JSON object (batched JSON-RPC requests are
      not supported). A malformed body yields a JSON-RPC ``-32700 Parse
      error`` envelope rather than a raw 400, so MCP clients can handle it
      via their normal error path.
    * A JSON-RPC *notification* (a request object with no ``id`` key, e.g.
      ``notifications/initialized``) is processed but gets no body —
      ``202 Accepted``, which the MCP Streamable HTTP transport requires.
      A ``204`` here left Claude's connector stuck after the handshake,
      never listing tools.

    Args:
        request: The authenticated POST.
        server: The tool catalogue to answer from.

    Returns:
        The JSON-RPC result or error envelope, or a bare 202.

    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        logger.info("mcp_server: invalid JSON body on %s", request.path)
        return JsonResponse(protocol.parse_error_response(), status=200)

    response_body = protocol.dispatch(payload, server)
    if response_body is None:
        return HttpResponse(status=202)
    return JsonResponse(response_body)


@ratelimit(key="ip", rate="120/m", block=True)
@require_mcp_token
def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    """Handle one JSON-RPC 2.0 request against the public tool catalogue.

    Args:
        request: The incoming POST, authenticated by ``@mcp_endpoint``.

    Returns:
        The JSON-RPC answer (see :func:`_answer`).

    """
    return _answer(request, protocol.PUBLIC_SERVER)


@ratelimit(key="ip", rate="120/m", block=True)
@require_mcp_token
def ops_mcp_endpoint(request: HttpRequest) -> HttpResponse:
    """Handle one JSON-RPC 2.0 request against the staff ops catalogue.

    A 403, not a 401: the token is good, and a 401 would send the client
    round the OAuth dance again for an account that will never be staff.

    Args:
        request: The incoming POST, authenticated by ``@mcp_endpoint``.

    Returns:
        The JSON-RPC answer (see :func:`_answer`), or 403 for a non-staff
        account.

    """
    if not request.user.is_staff:
        return JsonResponse(
            {
                "error": "forbidden",
                "error_description": "The ops server is for Snowdesk staff.",
            },
            status=403,
        )
    # The actor is bound per request so the waffle setters can attribute
    # each change to the staff account that made it.
    return _answer(request, OPS_SERVER._replace(actor=request.user))
