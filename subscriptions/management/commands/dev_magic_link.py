"""
subscriptions/management/commands/dev_magic_link.py — Dev-only magic-link helper.

Prints a ready-to-open magic-link URL for a subscriber so that the full
subscription / passkey flow can be tested locally without needing a working
SMTP stack.

Refuses to run when ``DEBUG`` is ``False`` so it cannot be accidentally
invoked in production.

Usage::

    poetry run python manage.py dev_magic_link --email you@example.com
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from subscriptions.models import Subscriber
from subscriptions.services.token import SALT_ACCOUNT_ACCESS, generate_token


class Command(BaseCommand):
    """Print a magic-link URL for a subscriber, creating them if needed."""

    help = "Print a dev magic-link URL for local passkey / subscription testing."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Add --email argument."""
        parser.add_argument(
            "--email",
            required=True,
            help="Subscriber email address.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Generate and print the magic-link URL."""
        if not settings.DEBUG:
            raise CommandError("This command is only available when DEBUG=True.")

        email = options["email"].strip().lower()

        subscriber, created = Subscriber.objects.get_or_create(
            email=email,
            defaults={
                "status": Subscriber.Status.ACTIVE,
                "confirmed_at": timezone.now(),
            },
        )

        verbosity = options["verbosity"]

        if not created and subscriber.status != Subscriber.Status.ACTIVE:
            subscriber.status = Subscriber.Status.ACTIVE
            if subscriber.confirmed_at is None:
                subscriber.confirmed_at = timezone.now()
            subscriber.save(update_fields=["status", "confirmed_at", "updated_at"])
            if verbosity >= 1:
                self.stdout.write(f"Activated existing subscriber {email}")
        elif created and verbosity >= 1:
            self.stdout.write(f"Created subscriber {email}")

        token = generate_token(email, salt=SALT_ACCOUNT_ACCESS)
        base = str(settings.WEBAUTHN_ORIGIN).rstrip("/")
        url = f"{base}/subscribe/account/{token}/"

        self.stdout.write(self.style.SUCCESS(url))
