"""
bulletins/management/commands/backfill_bulletin_groupings.py — Backfill command.

Backfills ``BulletinGrouping`` rows for all bulletins that do not yet have one.
A grouping is normally computed at ingest time by ``upsert_bulletin``; this
command is used once after the SNOW-323 migration to populate historical rows
that were ingested before the ingest hook existed.

Read-only by default — the command iterates ``Bulletin.objects.filter(
grouping__isnull=True)`` and reports what *would* be created without writing
anything to the database.  Pass ``--commit`` to persist.

Usage::

    # Read-only probe — how many bulletins are missing a grouping?
    python manage.py backfill_bulletin_groupings

    # Persist the missing groupings.
    python manage.py backfill_bulletin_groupings --commit

Exit codes:

- ``0`` — all eligible bulletins processed without error (or nothing to do).
- Non-zero — at least one bulletin failed (``CommandError``).  Failures are
  logged individually; other bulletins are still attempted.
"""

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from bulletins.models import Bulletin
from bulletins.services.grouping import compute_bulletin_grouping_boundary

logger = logging.getLogger(__name__)

_LOG_INTERVAL = 100


class Command(BaseCommand):
    """Backfill BulletinGrouping rows for bulletins that lack one."""

    help = (
        "Compute and persist BulletinGrouping rows for all Bulletin rows that "
        "do not yet have one. Read-only unless --commit is passed. "
        "Calls compute_bulletin_grouping_boundary for each eligible bulletin."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the computed BulletinGrouping rows to the database. "
                "Without this flag the command is read-only and only reports "
                "how many bulletins would be processed."
            ),
        )

    def _process_one(
        self,
        bulletin: Bulletin,
        *,
        verbosity: int,
    ) -> str:
        """
        Compute and persist the grouping for a single bulletin.

        Args:
            bulletin: The Bulletin to compute a grouping for.
            verbosity: Django verbosity level (0–3).

        Returns:
            ``"created"`` when a grouping row was written, ``"skipped"`` when
            the bulletin has no boundaried regions (compute returns None), or
            ``"failed"`` on exception.

        """
        try:
            result = compute_bulletin_grouping_boundary(bulletin)
        except Exception:
            logger.exception(
                "Failed to compute grouping for bulletin %s",
                bulletin.bulletin_id,
            )
            return "failed"

        if result is None:
            if verbosity >= 2:
                logger.debug(
                    "Skipped bulletin %s — no boundaried regions",
                    bulletin.bulletin_id,
                )
            return "skipped"

        if verbosity >= 2:
            logger.debug(
                "Created grouping for bulletin %s → %s",
                bulletin.bulletin_id,
                result.target_date,
            )
        return "created"

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the backfill command.

        Queries all bulletins whose ``grouping`` reverse accessor is null,
        then delegates per-bulletin work to ``_process_one``.  Failures are
        collected and reported; if any fail a ``CommandError`` is raised at
        the end so cron/CI receives a non-zero exit code.

        Flags:
            --commit: Persist computed groupings to the database.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Backfilling BulletinGrouping rows" + ("" if commit else " [READ-ONLY]")
            )
        )

        qs = Bulletin.objects.filter(grouping__isnull=True).order_by("valid_from")
        total = qs.count()

        self.stdout.write(f"Bulletins missing a grouping: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        if not commit:
            self.stdout.write(
                f"Read-only run — {total} bulletin(s) would be processed. "
                "Pass --commit to persist."
            )
            return

        processed = 0
        skipped = 0
        failed = 0

        for bulletin in qs.iterator():
            outcome = self._process_one(bulletin, verbosity=verbosity)
            if outcome == "created":
                processed += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed += 1

            total_done = processed + skipped + failed
            if verbosity >= 1 and total_done % _LOG_INTERVAL == 0:
                self.stdout.write(f"  Processed {total_done}/{total} …")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — created {processed}, skipped {skipped} "
                f"(no boundaried regions), {failed} failed."
            )
        )

        logger.info(
            "backfill_bulletin_groupings finished: "
            "processed=%d skipped=%d failed=%d commit=%s",
            processed,
            skipped,
            failed,
            commit,
        )

        if failed > 0:
            raise CommandError(
                f"{failed} bulletin(s) failed during backfill. Check logs for details."
            )
