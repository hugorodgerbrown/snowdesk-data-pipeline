"""
apps/accounts/management/commands/uppercase_account_choice_values.py — One-time command.

Two fields predate the project's UPPER CASE storage convention and hold
rows written with the old lower-case values (SNOW-582):

- ``Subscription.geo_match_kind`` — ``"in_region"``, ``"in_neighbour"``,
  ``"elsewhere"``, ``"unknown"``.
- ``PushSubscription.mechanism`` — ``"sw"``, ``"declarative"``.

This command rewrites both to their upper-case ``TextChoices`` members.

Read-only by default — the detection and counting still run so the dry-run
reports a real breakdown of what would change, per field. Pass ``--commit``
to persist.

Each field's queryset (rows whose value is a lower-case form of a current
choice) is itself the idempotency mechanism — a second run after a
successful commit selects nothing.

Usage::

    # Read-only probe — how many rows would be converted, and to what?
    python manage.py uppercase_account_choice_values

    # Persist the uppercased values.
    python manage.py uppercase_account_choice_values --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from apps.accounts.models import PushSubscription, Subscription
from apps.core.uppercase_choices import uppercase_field_values

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Uppercase legacy lower-case Subscription/PushSubscription choice values."""

    help = (
        "Rewrite Subscription.geo_match_kind and PushSubscription.mechanism "
        "from their legacy lower-case stored values to their upper-case "
        "TextChoices members. Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line flags."""
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Persist the uppercased values. Omit for a dry-run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the one-time uppercase conversion for both fields.

        Flags:
            --commit: Persist the uppercased values to the database.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Uppercasing accounts choice values"
                + ("" if commit else " [READ-ONLY]")
            )
        )

        total = 0
        for label, model, field in (
            ("Subscription.geo_match_kind", Subscription, "geo_match_kind"),
            ("PushSubscription.mechanism", PushSubscription, "mechanism"),
        ):
            converted = uppercase_field_values(
                self, model, field, commit=commit, verbosity=verbosity
            )
            field_total = sum(converted.values())
            total += field_total

            if field_total == 0:
                self.stdout.write(f"{label}: nothing to do.")
                continue

            self.stdout.write(f"{label}:")
            for value, count in sorted(converted.items()):
                self.stdout.write(f"  {value}: {count}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        if commit:
            self.stdout.write(self.style.SUCCESS(f"Done — uppercased {total} row(s)."))
        else:
            self.stdout.write(
                f"Read-only run — would uppercase {total} row(s). "
                "Pass --commit to persist."
            )

        logger.info(
            "uppercase_account_choice_values finished: converted=%d commit=%s",
            total,
            commit,
        )
