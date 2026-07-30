"""
apps/mcp_server/apps.py — AppConfig for the mcp_server application.

The app has no models or migrations — it exists only to register the
``POST /api/mcp/`` JSON-RPC endpoint and the pure-Python modules
(``normalise``, ``season``, ``resolvers``, ``tools``, ``protocol``) that
back it.
"""

from django.apps import AppConfig


class McpServerConfig(AppConfig):
    """Django application configuration for the mcp_server app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mcp_server"
    verbose_name = "MCP server"
