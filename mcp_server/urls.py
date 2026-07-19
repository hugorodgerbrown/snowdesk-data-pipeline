"""
mcp_server/urls.py — URL routing for the mcp_server application.

Included from ``public/api_urls.py`` at the ``/api/`` root, so this module
owns the ``mcp`` path segment and registers the endpoint at both
``POST /api/mcp/`` (canonical, reverses as ``api:mcp:endpoint``) and
``POST /api/mcp`` (slash-less alias).

The slash-less alias exists because remote MCP clients (Claude Desktop /
claude.ai connectors) send the JSON-RPC ``initialize`` as a POST to exactly
the URL the user typed. If that URL omits the trailing slash, Django's
``APPEND_SLASH`` cannot redirect it to the canonical ``/api/mcp/`` — a POST
body cannot survive the resulting 301 (the client replays it as a GET, which
this POST-only view answers with 405), so the handshake fails before it
starts. Serving both spellings from the same view removes that footgun; the
canonical trailing-slash form stays the documented URL and the reverse
target.
"""

from django.urls import path

from . import views

app_name = "mcp"

urlpatterns = [
    path("mcp/", views.mcp_endpoint, name="endpoint"),
    path("mcp", views.mcp_endpoint, name="endpoint_noslash"),
]
