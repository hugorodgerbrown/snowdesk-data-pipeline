"""
apps/routes/management/commands/import_ski_tours.py — import_ski_tours.

Imports swisstopo's ski-touring map into the reference-data service and
applies it to this instance (SNOW-1276):

1. downloads the GeoPackage zip named by the STAC item
   ``ch.swisstopo-karto.skitouren/skitouren`` (or reads ``--archive``);
2. makes the service's ``SourceSkiSegment`` and ``SourceSkiTour`` equal it
   (``import_ski_archive``): unchanged rows are not rewritten, a line
   swisstopo dropped is tombstoned, so the v2 ``ski-network`` and
   ``ski-tours`` feeds serve only what changed;
3. applies what changed to this instance's ``SkiSegment`` and ``SkiTour``
   (``apply_from_service``), the same apply ``pull_reference_data`` runs
   on every other instance.

Run on the service's host, production: scheduled weekly (Mondays at 02:30
UTC) in ``schedule.py``, since swisstopo releases the data about once a
year. Another instance gets the lines from ``pull_reference_data``, or from
this command with ``--archive`` and a local copy of the zip.

Read-only by default: the import and the apply run inside a transaction
that is rolled back, so a preview reports exactly what a commit would
change. Not a row walk: each step reconciles a whole layer against the
stored one, so there is no per-row countdown (``docs/management-commands.md``,
the derived-unit exemption). Each step's counts are printed and tallied.

Usage:
    uv run python manage.py import_ski_tours                 # preview
    uv run python manage.py import_ski_tours --commit        # write
    uv run python manage.py import_ski_tours --archive skitouren_2056.gpkg.zip --commit
"""

from __future__ import annotations

import logging
import tempfile
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError
from django.db import transaction

from apps.core.command_register import RegisteredCommand
from apps.ingest.public import (
    SkiArchiveError,
    SwisstopoError,
    download_ski_archive,
    import_ski_archive,
)
from apps.routes.services.ski_network import FAILED, OUTCOMES, apply_from_service

logger = logging.getLogger(__name__)

# What the service's sync reports for each kind.
SYNC_OUTCOMES: tuple[str, ...] = ("created", "updated", "deleted", "unchanged")


class _Rollback(Exception):
    """Raised inside a preview's transaction so that nothing it wrote stays."""


class Command(RegisteredCommand):
    """Import swisstopo's ski-touring network and SAC tours, and apply them here."""

    help = (
        "Import swisstopo's ski-touring network and SAC's tours into the "
        "reference-data service and apply them to this instance. Read-only "
        "unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--archive",
            type=Path,
            default=None,
            help="Read this skitouren_2056.gpkg.zip instead of downloading it.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write. Without it the import runs and is rolled back.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Fetch the archive, import it, apply it, keeping it only with --commit."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        archive: Path | None = options["archive"]

        label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Importing swisstopo's ski tours{label}")
        )
        with tempfile.TemporaryDirectory() as scratch:
            source = self._archive(archive, Path(scratch))
            synced, applied = self._import(source, commit=commit)
        failed = self._report(synced, applied, verbosity=verbosity)
        logger.info("import_ski_tours: commit=%s %s %s", commit, synced, applied)
        if failed:
            raise CommandError(f"import_ski_tours: {failed} item(s) failed to apply")

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

    def _archive(self, archive: Path | None, scratch: Path) -> Path:
        """Return the archive to read: ``--archive``, or swisstopo's, downloaded.

        Args:
            archive: The ``--archive`` path, or None to download.
            scratch: A directory the download may be written to.

        Returns:
            The zip's path.

        Raises:
            CommandError: The file is missing or the download failed.

        """
        if archive is not None:
            if not archive.is_file():
                raise CommandError(f"{archive} is not a file")
            return archive
        try:
            return download_ski_archive(scratch / "skitouren.zip")
        except SwisstopoError as exc:
            raise CommandError(str(exc)) from exc

    def _report(
        self,
        synced: dict[str, dict[str, int]],
        applied: dict[str, Counter[str]],
        *,
        verbosity: int,
    ) -> int:
        """Tally and print both steps' counts; return how many items failed.

        Args:
            synced: The service sync's counts, by kind.
            applied: This instance's apply outcomes, by kind.
            verbosity: The run's verbosity.

        Returns:
            The failed items.

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

    def _import(
        self, archive: Path, *, commit: bool
    ) -> tuple[dict[str, dict[str, int]], dict[str, Counter[str]]]:
        """Import and apply in one transaction, rolled back unless committing.

        Args:
            archive: The GeoPackage zip.
            commit: Keep the writes.

        Returns:
            The service sync's counts and this instance's apply outcomes.

        Raises:
            CommandError: The archive is not swisstopo's.

        """
        synced: dict[str, dict[str, int]] = {}
        applied: dict[str, Counter[str]] = {}
        try:
            with transaction.atomic():
                synced = import_ski_archive(archive)
                applied = apply_from_service(commit=True)
                if not commit:
                    raise _Rollback
        except _Rollback:
            pass
        except SkiArchiveError as exc:
            raise CommandError(str(exc)) from exc
        return synced, applied
