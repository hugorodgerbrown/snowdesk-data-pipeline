"""
apps/accounts/management/commands/dev_magic_link.py — Dev-only magic-link helper.

Prints a ready-to-open magic-link URL for an account so that the full
subscription / passkey flow can be tested locally without needing a working
SMTP stack.

Refuses to run when ``DEBUG`` is ``False`` so it cannot be accidentally
invoked in production.

Usage::

    uv run python manage.py dev_magic_link --email you@example.com
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import Account
from apps.accounts.services.token import SALT_ACCOUNT_ACCESS, generate_token
from apps.core.command_register import RegisteredCommand

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Print a magic-link URL for an account, creating it if needed.

    SNOW-602 exempt: no queryset loop — operates on a single named account.
    """

    # The run is registered but its output is not (SNOW-1189):
    # it prints a working sign-in link, and the register is readable in the admin
    # and over MCP.
    capture_output = False

    help = "Print a dev magic-link URL for local passkey / subscription testing."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Add --email argument."""
        parser.add_argument(
            "--email",
            required=True,
            help="Account email address.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Generate and print the magic-link URL."""
        if not settings.DEBUG:
            raise CommandError("This command is only available when DEBUG=True.")

        email = options["email"].strip().lower()
        now = timezone.now()

        account, created = Account.objects.get_or_create_for_email(
            email,
            defaults={"is_verified": True, "verified_at": now},
        )

        verbosity = options["verbosity"]

        if not created and not account.is_verified:
            account.mark_verified(now)
            account.save(update_fields=["is_verified", "verified_at", "updated_at"])
            if verbosity >= 1:
                self.stdout.write(f"Verified existing account {email}")
        elif created and verbosity >= 1:
            self.stdout.write(f"Created account {email}")

        token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
        base = str(settings.WEBAUTHN_ORIGIN).rstrip("/")
        url = f"{base}/account/access/{token}/"

        self.stdout.write(self.style.SUCCESS(url))
