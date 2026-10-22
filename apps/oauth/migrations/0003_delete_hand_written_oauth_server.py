"""
apps/oauth/migrations/0003_delete_hand_written_oauth_server.py.

Drops the four tables of the hand-written OAuth server (SNOW-1193). The MCP
endpoint's authorization server is now mcp-auth on django-oauth-toolkit,
whose own tables replace these; every existing connection reconnects once.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Delete OAuthToken, AuthorizationCode, OAuthGrant and OAuthClient."""

    dependencies = [
        ("oauth", "0002_token_consumed_at"),
    ]

    operations = [
        migrations.DeleteModel(name="OAuthToken"),
        migrations.DeleteModel(name="AuthorizationCode"),
        migrations.DeleteModel(name="OAuthGrant"),
        migrations.DeleteModel(name="OAuthClient"),
    ]
