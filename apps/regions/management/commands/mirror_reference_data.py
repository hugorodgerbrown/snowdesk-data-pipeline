"""
apps/regions/management/commands/mirror_reference_data.py — mirror_reference_data.

Copies the curated estate — reference places, season passes and resorts —
into the reference-data service's tables (``SourceLocation``,
``SourcePass``, ``SourceResort``), which the v2 data feeds serve
(SNOW-1271). Until the service has its own editor, production's resort
sheet, ``import_resorts`` and the map-admin overlays are where the estate
is curated; this command is how each change reaches the service.

Read-only by default: the mirror runs inside a transaction that is rolled
back, so a preview reports exactly what a commit would change. Scheduled
hourly at :40 in ``schedule.py``; ``fetch_weather --commit`` also mirrors
the places, four times a day, before it fetches them.

Not a row walk: each step reconciles a whole list against another, so
there is no per-row countdown (``docs/management-commands.md``, the
derived-unit exemption). Each step's counts are printed and tallied.

Usage:
    uv run python manage.py mirror_reference_data           # preview
    uv run python manage.py mirror_reference_data --commit  # write
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.db import transaction

from apps.core.command_register import RegisteredCommand
from apps.regions.services.reference import mirror_reference_data

logger = logging.getLogger(__name__)

# The counts each step reports, in the order they are printed.
OUTCOMES = ("created", "updated", "deleted", "unchanged", "linked")


class _Rollback(Exception):
    """Raised inside a preview's transaction so that nothing it wrote stays."""


class Command(RegisteredCommand):
    """Mirror places, passes and resorts into the reference-data service."""

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

    help = (
        "Mirror the curated places, passes and resorts into the reference-data "
        "service's tables. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write. Without it the mirror runs and is rolled back.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the mirror, keeping it only with --commit."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Mirroring reference data{label}")
        )

        result: dict[str, dict[str, int]] = {}
        try:
            with transaction.atomic():
                result = mirror_reference_data()
                if not commit:
                    raise _Rollback
        except _Rollback:
            pass

        for kind, counts in result.items():
            for outcome in OUTCOMES:
                self.tally(f"{kind}_{outcome}", counts.get(outcome, 0))
            if verbosity >= 1:
                summary = ", ".join(f"{counts.get(o, 0)} {o}" for o in OUTCOMES)
                self.stdout.write(f"  {kind}: {summary}")

        if verbosity >= 1:
            if commit:
                self.stdout.write(self.style.SUCCESS("Done."))
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Read-only run complete. Nothing written. Pass --commit "
                        "to persist."
                    )
                )
        logger.info("mirror_reference_data: commit=%s %s", commit, result)
