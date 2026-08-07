"""
apps/bulletins/management/commands/uppercase_bulletin_choice_values.py — one-time.

``PipelineRun.status`` predates the project's UPPER CASE storage convention
and holds rows written with the old lower-case values (``"pending"``,
``"running"``, ``"success"``, ``"failed"``) before ``PipelineRun.Status``
was upper-cased (SNOW-582). This command rewrites those rows to the new
upper-case members.

Read-only by default — the detection and counting still run so the dry-run
reports a real breakdown of what would change. Pass ``--commit`` to persist.

The queryset (rows whose ``status`` is a lower-case form of a current
choice) is itself the idempotency mechanism — a second run after a
successful commit selects nothing.

Usage::

    # Read-only probe — how many pipeline runs would be converted, and to what?
    python manage.py uppercase_bulletin_choice_values

    # Persist the uppercased values.
    python manage.py uppercase_bulletin_choice_values --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from apps.bulletins.models import PipelineRun
from apps.core.uppercase_choices import uppercase_field_values

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Uppercase legacy lower-case PipelineRun.status values."""

    help = (
        "Rewrite PipelineRun.status from its legacy lower-case stored "
        "values to the upper-case Status choices. Read-only by default; "
        "pass --commit to persist."
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
                "Uppercasing bulletins choice values"
                + ("" if commit else " [READ-ONLY]")
            )
        )

        total = 0
        for label, model, field in (("PipelineRun.status", PipelineRun, "status"),):
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
            "uppercase_bulletin_choice_values finished: converted=%d commit=%s",
            total,
            commit,
        )
