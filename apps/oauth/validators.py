"""
apps/oauth/validators.py — Who counts as a service client (SNOW-1197).

A **service client** is the data feed's kind of client: a confidential
client-credentials application a member of staff created by hand in the
admin (``registration_source == MANUAL``). ``apps.data_feed.auth`` checks
the rule on every feed request.

The token endpoint needs no rule of Snowdesk's own. Since v0.1.8 mcp-auth
refuses every grant but the authorization code at registration (DCR and
CIMD alike), and its validator refuses those grants at ``/oauth/token/`` to
any client not made by hand — so open registration can never mint a
userless token. A public client cannot use client credentials either: the
grant always authenticates the client, and a public client has no secret.

``registration_source`` is read-only in the toolkit's admin and is set to
``dcr`` or ``cimd`` by those two registration paths, so it cannot be
flipped by the client that a registration created.
"""

from __future__ import annotations

from typing import Any

from oauth2_provider.models import AbstractApplication


def is_service_client(application: Any) -> bool:
    """Return True for a staff-provisioned, confidential client-credentials app.

    The definition of a service client — the data feed's clients — read by
    the feed's own check (``apps.data_feed.auth``).

    Args:
        application: A django-oauth-toolkit ``Application``, or None.

    Returns:
        Whether the application was created by hand, is confidential, and
        holds the client-credentials grant.

    """
    return bool(
        application is not None
        and application.registration_source
        == AbstractApplication.RegistrationSource.MANUAL
        and application.client_type == AbstractApplication.CLIENT_CONFIDENTIAL
        and application.authorization_grant_type
        == AbstractApplication.GRANT_CLIENT_CREDENTIALS
    )
