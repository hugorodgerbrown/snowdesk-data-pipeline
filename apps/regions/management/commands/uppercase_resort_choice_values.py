"""
apps/regions/management/commands/uppercase_resort_choice_values.py — One-time command.

``Resort.geocode_source`` predates the project's UPPER CASE storage
convention and holds rows written with the old lower-case values
(``"manual"``, ``"import"``) before ``Resort.GeocodeSource`` was promoted to
a proper ``TextChoices`` class (SNOW-582). This command rewrites those rows
to the new upper-case members.

Read-only by default — the detection and counting still run so the dry-run
reports a real breakdown of what would change. Pass ``--commit`` to persist.

The queryset (rows whose ``geocode_source`` is a lower-case form of a
current choice) is itself the idempotency mechanism — a second run after a
successful commit selects nothing, because no lower-case values remain.

Usage::

    # Read-only probe — how many resorts would be converted, and to what?
    python manage.py uppercase_resort_choice_values

    # Persist the uppercased values.
    python manage.py uppercase_resort_choice_values --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from apps.core.uppercase_choices import uppercase_field_values
from apps.regions.models import Resort

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Uppercase legacy lower-case Resort.geocode_source values."""

    help = (
        "Rewrite Resort.geocode_source from its legacy lower-case stored "
        "values ('manual', 'import') to the upper-case GeocodeSource "
        "choices. Read-only by default; pass --commit to persist."
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
        """Execute the one-time uppercase conversion.

        Flags:
            --commit: Persist the uppercased values to the database.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Uppercasing Resort.geocode_source" + ("" if commit else " [READ-ONLY]")
            )
        )

        converted = uppercase_field_values(
            self, Resort, "geocode_source", commit=commit, verbosity=verbosity
        )
        total = sum(converted.values())

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        for value, count in sorted(converted.items()):
            self.stdout.write(f"  {value}: {count}")

        if commit:
            self.stdout.write(
                self.style.SUCCESS(f"Done — uppercased {total} resort(s).")
            )
        else:
            self.stdout.write(
                f"Read-only run — would uppercase {total} resort(s). "
                "Pass --commit to persist."
            )

        logger.info(
            "uppercase_resort_choice_values finished: converted=%d commit=%s",
            total,
            commit,
        )
