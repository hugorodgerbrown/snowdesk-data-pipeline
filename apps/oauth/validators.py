"""
apps/oauth/validators.py — Snowdesk's OAuth validator: who may use client credentials.

``OAUTH2_PROVIDER["OAUTH2_VALIDATOR_CLASS"]`` names :class:`Validator`, which
is mcp-auth's validator plus one rule (SNOW-1197): the **client-credentials
grant** is issued only to an application a member of staff created by hand
in the admin (``registration_source == MANUAL``).

The rule exists because registration is open. Dynamic Client Registration
(RFC 7591) lets anyone with an allowlisted callback register a client, and
django-oauth-toolkit maps a registration that asks for
``grant_types: ["client_credentials"]`` straight onto a confidential
client-credentials application with a secret. Without this check, anyone on
the internet could mint a machine token with no user behind it. The data
feed (``apps.data_feed``) is the only consumer of such a token, and it
re-checks the same rule on every request — this is the gate at issuance,
that is the gate at use.

``registration_source`` is read-only in the toolkit's admin and is set to
``dcr`` or ``cimd`` by those two registration paths, so it cannot be
flipped by the client that a registration created.
"""

from __future__ import annotations

from typing import Any

from mcp_auth.validators import Validator as MCPAuthValidator
from oauth2_provider.models import AbstractApplication


def is_service_client(application: Any) -> bool:
    """Return True for a staff-provisioned, confidential client-credentials app.

    The definition of a service client — the data feed's clients — shared by
    the token endpoint (below) and the feed's own check
    (``apps.data_feed.auth``), so the two can never disagree.

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


class Validator(MCPAuthValidator):
    """mcp-auth's validator, issuing client credentials only to service clients."""

    def validate_grant_type(
        self,
        client_id: str,
        grant_type: str,
        client: Any,
        request: Any,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Refuse the client-credentials grant to anything but a service client.

        Every other grant is decided exactly as mcp-auth and the toolkit
        decide it.

        Args:
            client_id: The client's id.
            grant_type: The grant asked for, e.g. ``client_credentials``.
            client: The authenticated application.
            request: oauthlib's request.
            *args: Passed through.
            **kwargs: Passed through.

        Returns:
            Whether the client may use the grant.

        """
        if grant_type == "client_credentials" and not is_service_client(request.client):
            return False
        allowed: bool = super().validate_grant_type(
            client_id, grant_type, client, request, *args, **kwargs
        )
        return allowed
