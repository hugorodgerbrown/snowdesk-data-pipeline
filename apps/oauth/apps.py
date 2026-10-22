"""
apps/oauth/apps.py — AppConfig for the oauth application.

The oauth app is Snowdesk's side of the MCP endpoint's authorization server
(SNOW-1193). The server itself is mcp-auth on django-oauth-toolkit; this app
holds what is Snowdesk's own: who may connect (``policy.verified_account``),
the verify-first gate in front of the consent page, the connected-apps
disconnect endpoint, and the token purge command. It has no models of its
own any more — migration 0003 dropped the hand-written server's tables, and
the migrations are kept so existing databases can apply it. See
``docs/oauth.md``.
"""

from django.apps import AppConfig


class OAuthConfig(AppConfig):
    """AppConfig for the oauth application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.oauth"
    verbose_name = "OAuth"
