"""
apps/oauth/apps.py — AppConfig for the oauth application.

The oauth app makes Snowdesk its own OAuth 2.1 authorization server for the
MCP endpoint at ``/api/mcp/`` (SNOW-1035). It owns the client registry
(``OAuthClient``), the per-user approvals (``OAuthGrant`` — a "connected
app" on the settings page), and the short-lived authorization codes and
access / refresh tokens issued under them. See ``docs/oauth.md``.
"""

from django.apps import AppConfig


class OAuthConfig(AppConfig):
    """AppConfig for the oauth application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.oauth"
    verbose_name = "OAuth"
