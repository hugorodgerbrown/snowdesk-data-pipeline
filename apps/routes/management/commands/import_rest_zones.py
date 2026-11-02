"""
apps/routes/management/commands/import_rest_zones.py — import_rest_zones.

Imports FOEN's wildlife rest zones (Wildruhezonen) into the reference-data
service and applies them to this instance (SNOW-1263):

1. reads every canton's zones from geo.admin's MapServer ``find``
   (``ch.bafu.wrz-wildruhezonen_portal``), all of them before anything is
   written;
2. makes the service's ``SourceRestZone`` equal them
   (``import_rest_zones``): an unchanged zone is not rewritten and one FOEN
   dropped is tombstoned, so the v2 ``rest-zones`` feed serves only what
   changed;
3. applies what changed to this instance's ``RestZone``
   (``apply_from_service``), the same apply ``pull_reference_data`` runs on
   every other instance.

**A failed canton writes nothing.** The sync makes the stored zones equal
the whole list it is given, so a list missing a canton would tombstone
every zone in it. If any canton (or any zone in one) cannot be read, the
run reports which, leaves every stored zone as it was, and exits non-zero.

Run on the service's host, production: scheduled weekly (Mondays at 02:50
UTC) in ``schedule.py``, since FOEN edits the zones a few times a year.
Another instance gets the zones from ``pull_reference_data``.

Read-only by default: the import and the apply run inside a transaction
that is rolled back, so a preview reports exactly what a commit would
change. Not a row walk: each step reconciles the whole layer against the
stored one, so there is no per-row countdown (``docs/management-commands.md``,
the derived-unit exemption). Each step's counts are printed and tallied.

Usage:
    uv run python manage.py import_rest_zones                # preview
    uv run python manage.py import_rest_zones --commit       # write
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from collections import Counter
from typing import Any

from django.core.management.base import CommandError
from django.db import transaction

from apps.core.command_register import RegisteredCommand
from apps.ingest.public import RestZoneImportError, import_rest_zones
from apps.routes.services.ski_network import (
    FAILED,
    OUTCOMES,
    REST_ZONE_KINDS,
    apply_from_service,
)

logger = logging.getLogger(__name__)

# What the service's sync reports.
SYNC_OUTCOMES: tuple[str, ...] = ("created", "updated", "deleted", "unchanged")


class _Rollback(Exception):
    """Raised inside a preview's transaction so that nothing it wrote stays."""


def _any_failed(applied: dict[str, Counter[str]]) -> bool:
    """Return whether any kind's apply counted a failed zone."""
    return any(outcomes[FAILED] for outcomes in applied.values())


class Command(RegisteredCommand):
    """Import FOEN's wildlife rest zones, and apply them here."""

    help = (
        "Import FOEN's wildlife rest zones from geo.admin into the "
        "reference-data service and apply them to this instance. Read-only "
        "unless --commit is passed. A canton that cannot be read writes "
        "nothing and exits non-zero."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write. Without it the import runs and is rolled back.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Fetch every canton, import, apply, keeping it only with --commit."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Importing FOEN's wildlife rest zones{label}")
        )
        synced, applied = self._import(commit=commit)
        failed = self._report(synced, applied, verbosity=verbosity)
        logger.info("import_rest_zones: commit=%s %s %s", commit, synced, applied)
        if failed:
            raise CommandError(
                f"import_rest_zones: {failed} zone(s) failed to apply. "
                "Nothing was written."
            )

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

    def _import(
        self, *, commit: bool
    ) -> tuple[dict[str, dict[str, int]], dict[str, Counter[str]]]:
        """Import and apply in one transaction, rolled back unless committing.

        Args:
            commit: Keep the writes.

        Returns:
            The service sync's counts and this instance's apply outcomes.

        Raises:
            CommandError: A canton or a zone could not be read; nothing was
                written.

        """
        synced: dict[str, dict[str, int]] = {}
        applied: dict[str, Counter[str]] = {}
        try:
            with transaction.atomic():
                synced = import_rest_zones()
                applied = apply_from_service(commit=True, kinds=REST_ZONE_KINDS)
                # A zone that fails to apply rolls the sync back too, so
                # the feed never carries a document no instance can hold.
                if not commit or _any_failed(applied):
                    raise _Rollback
        except _Rollback:
            pass
        except RestZoneImportError as exc:
            self.tally("failed", 1)
            raise CommandError(
                f"import_rest_zones: {exc}. Nothing was written."
            ) from exc
        return synced, applied

    def _report(
        self,
        synced: dict[str, dict[str, int]],
        applied: dict[str, Counter[str]],
        *,
        verbosity: int,
    ) -> int:
        """Tally and print both steps' counts; return how many zones failed.

        Args:
            synced: The service sync's counts, by kind.
            applied: This instance's apply outcomes, by kind.
            verbosity: The run's verbosity.

        Returns:
            The failed zones.

        """
        lines: list[str] = []
        for kind, counts in synced.items():
            for outcome in SYNC_OUTCOMES:
                self.tally(f"service_{kind}_{outcome}", counts.get(outcome, 0))
            summary = ", ".join(f"{counts.get(o, 0)} {o}" for o in SYNC_OUTCOMES)
            lines.append(f"  service {kind}: {summary}")
        for kind, outcomes in applied.items():
            for outcome in OUTCOMES:
                self.tally(f"{kind}_{outcome}", outcomes[outcome])
            summary = ", ".join(f"{outcomes[o]} {o}" for o in OUTCOMES)
            lines.append(f"  here {kind}: {summary}")
        failed = sum(outcomes[FAILED] for outcomes in applied.values())
        self.tally("failed", failed)
        if verbosity >= 1:
            self.stdout.write("\n".join(lines))
        return failed
