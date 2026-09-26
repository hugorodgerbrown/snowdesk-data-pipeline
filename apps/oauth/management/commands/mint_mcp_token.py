"""
apps/oauth/management/commands/mint_mcp_token.py.

Management command.

Mints a one-hour MCP access token for one Snowdesk account, for local
testing with curl or an MCP inspector without running the browser OAuth
flow (SNOW-1035). The token is issued under a ``LOCAL`` client and a grant
for that account, so it appears on the account's settings page as a
connected app and Disconnect revokes it like any other.

The token is printed once, and only its hash is stored.

``--email`` is required, which the project's command rules call a smell.
The exception is deliberate: the command's whole purpose is a token that
acts as one named account, and there is no account a bare invocation could
sensibly pick.

Read-only by default — pass ``--commit`` to create the client, grant and
token (docs/decisions/dry-run-default-commands.md).

Typical use::

    python manage.py mint_mcp_token --email you@example.com
    python manage.py mint_mcp_token --email you@example.com --commit

    # Behind a tunnel, the token's audience must be the tunnel's MCP URL.
    python manage.py mint_mcp_token --email you@example.com --commit --resource https://abc.ngrok.app/api/mcp/
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from apps.oauth.models import OAuthClient, OAuthGrant
from apps.oauth.services.resource import canonical_resource
from apps.oauth.services.tokens import DEFAULT_SCOPE, issue_token_pair

logger = logging.getLogger(__name__)

LOCAL_CLIENT_ID = "sd_local_mint_mcp_token"
LOCAL_CLIENT_NAME = "Local token (mint_mcp_token)"


class Command(BaseCommand):
    """Mint an MCP access token for one account."""

    help = (
        "Mints a one-hour MCP access token for the account with --email. "
        "Read-only without --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        # Required on purpose — see the module docstring.
        parser.add_argument(
            "--email",
            required=True,
            help="Email address of the account the token acts as.",
        )
        parser.add_argument(
            "--resource",
            default=None,
            help=(
                "The MCP URL the token is for. Defaults to SITE_BASE_URL + "
                "/api/mcp/; it must match the host the token is sent to."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Create the token. Without this flag nothing is written.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Mint the token, or report what would be minted.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: For an unknown email or an unusable --resource.

        """
        email: str = options["email"].strip().lower()
        verbosity: int = options["verbosity"]
        resource: str = options["resource"] or (
            settings.SITE_BASE_URL.rstrip("/") + reverse("api:mcp:endpoint")
        )
        if not canonical_resource(resource):
            raise CommandError(f"--resource is not an absolute URL: {resource!r}")

        user = User.objects.filter(email=email).first()
        if user is None:
            raise CommandError(f"No user with email {email!r}.")

        if not options["commit"]:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING(
                        f"Would mint an access token for user pk={user.pk} "
                        f"with audience {resource}. Re-run with --commit."
                    )
                )
            return

        with transaction.atomic():
            client, _ = OAuthClient.objects.get_or_create(
                client_id=LOCAL_CLIENT_ID,
                defaults={
                    "kind": OAuthClient.KIND.LOCAL,
                    "client_name": LOCAL_CLIENT_NAME,
                    "redirect_uris": [],
                },
            )
            grant, _ = OAuthGrant.objects.get_or_create(user=user, client=client)
            grant.scope = DEFAULT_SCOPE
            grant.resource = resource
            grant.revoked_at = None
            grant.save(update_fields=["scope", "resource", "revoked_at", "updated_at"])
            pair, _ = issue_token_pair(grant, resource=resource, scope=DEFAULT_SCOPE)

        logger.info("mint_mcp_token: minted an access token for user pk=%s", user.pk)
        # The token is this command's output artefact, so it prints at every
        # verbosity — a --verbosity 0 run that printed nothing would have
        # minted a token nobody can use.
        self.stdout.write(pair.access_token)
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Access token for user pk={user.pk}, audience {resource}, "
                    f"expires in {pair.expires_in // 60} minutes. Send it as "
                    "'Authorization: Bearer <token>'."
                )
            )
