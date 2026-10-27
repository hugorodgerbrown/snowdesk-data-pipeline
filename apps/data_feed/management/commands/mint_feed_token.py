"""
apps/data_feed/management/commands/mint_feed_token.py.

Management command.

Mints a bearer token for the data feed (SNOW-1254), so a consumer that
cannot run the client-credentials exchange — a cloud dev environment, set up
once through its environment variables — can still read the feed. The token
is an ordinary django-oauth-toolkit access token, issued to a **service
client** with the ``data`` scope and bound (RFC 8707) to the feed's URL, so
``require_feed_token`` admits it without any change. It expires after
``--days`` (90 by default), carries no user, and is revoked by deleting it,
or its application, in the admin.

Give each kind of consumer its own service client ("Cloud dev feed", say),
so revoking the cloud token never touches staging's credentials.

The raw token is printed once, on stdout, and never logged: only its hash is
stored, so it cannot be read back. Read-only without ``--commit``.

Typical use, on production::

    python manage.py mint_feed_token --client "Cloud dev feed"
    python manage.py mint_feed_token --client "Cloud dev feed" --days 30 --commit
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone
from oauth2_provider.models import (
    get_access_token_model,
    get_application_model,
    set_token_value,
)

from apps.core.command_register import RegisteredCommand
from apps.data_feed import contract
from apps.oauth.validators import is_service_client

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 90


def default_resource() -> str:
    """Return the feed URL a minted token is bound to on this site.

    Returns:
        ``SITE_BASE_URL`` plus ``/api/data/v<VERSION>/``.

    """
    return f"{settings.SITE_BASE_URL.rstrip('/')}/api/data/v{contract.VERSION}/"


class Command(RegisteredCommand):
    """Mint a long-lived, read-only data feed token for a service client."""

    help = (
        "Mints a bearer token with the data scope for a service client, bound to "
        "the feed URL and expiring after --days. Read-only without --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--client",
            default="Cloud dev feed",
            help="Name of the service client application (default: %(default)s).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_DAYS,
            help="Days until the token expires (default: %(default)s).",
        )
        parser.add_argument(
            "--resource",
            default="",
            help="Feed URL the token is bound to (default: this site's feed).",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Create the token. Without this flag nothing is written.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Validate the client, then mint (or with no --commit, describe) a token.

        Exempt from row-by-row streaming (CLAUDE.md management-command rule
        5): it writes one row and walks none.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: The client does not exist, is not a service client,
                or ``--days`` is not positive.

        """
        days: int = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1.")
        name: str = options["client"]
        application = get_application_model().objects.filter(name=name).first()
        if application is None:
            raise CommandError(
                f"No OAuth application named {name!r}. Create a service client in "
                "the admin first — see docs/data-feed.md."
            )
        if not is_service_client(application):
            raise CommandError(
                f"{name!r} is not a service client: it must be confidential, "
                "client-credentials and created by hand in the admin."
            )
        resource: str = options["resource"] or default_resource()
        expires = timezone.now() + dt.timedelta(days=days)
        commit: bool = options["commit"]
        if not commit:
            self.tally("tokens_minted", 0)
            self.stdout.write(
                f"Read-only run — would mint a {days}-day data token for {name!r}, "
                f"bound to {resource}, expiring {expires:%Y-%m-%d}. "
                "Pass --commit to mint it."
            )
            return
        raw = secrets.token_urlsafe(32)
        token = get_access_token_model()(
            user=None,
            application=application,
            scope=contract.SCOPE,
            resource=[resource],
            expires=expires,
        )
        set_token_value(token, raw)
        token.save()
        self.tally("tokens_minted", 1)
        logger.info(
            "mint_feed_token: minted token %s for %r, expiring %s",
            token.pk,
            name,
            expires.isoformat(),
        )
        self.stdout.write(
            f"Minted a data token for {name!r}, bound to {resource}, expiring "
            f"{expires:%Y-%m-%d}. It is shown once and cannot be read back:\n\n"
            f"{raw}\n\nSet it as DATA_FEED_TOKEN on the consumer."
        )
