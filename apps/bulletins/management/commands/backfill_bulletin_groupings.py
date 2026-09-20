"""
apps/bulletins/management/commands/backfill_bulletin_groupings.py — Backfill command.

Backfills ``BulletinGrouping`` rows for all bulletins that do not yet have one.
A grouping is normally computed at ingest time by ``upsert_bulletin``; this
command is used once after the SNOW-323 migration to populate historical rows
that were ingested before the ingest hook existed.

Read-only by default — the command iterates the bulletins that lack a
grouping *and could have one*, and reports what would be created without
writing anything to the database.  Pass ``--commit`` to persist.

That second condition is load-bearing since SNOW-1001: a bulletin covering
fewer than ``MIN_GROUPED_REGIONS`` boundaried micro-regions is never given a
grouping (why:
docs/decisions/a-grouping-outline-asserts-an-aggregation.md), so it matches
``grouping__isnull=True`` for ever. Selecting on the null alone meant every
such bulletin — the whole Météo-France archive today, and SLF's too once
SNOW-998 lands — was re-attempted on every run and then reported as
"skipped", which made the summary line describe a backlog that does not
exist. ``candidate_bulletins`` below carries the same boundaried-region count
the writer and ``purge_degenerate_bulletin_groupings`` use, so a second run
selects only what a first run genuinely failed to write.

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
from django.db.models import Count, Q, QuerySet

from apps.bulletins.models import MIN_GROUPED_REGIONS, Bulletin
from apps.bulletins.services.grouping import compute_bulletin_grouping_boundary
from apps.core.command_iteration import iterate_rows

logger = logging.getLogger(__name__)


def candidate_bulletins() -> QuerySet[Bulletin]:
    """
    Return the bulletins that lack a grouping and could be given one.

    The exact complement, over the same boundaried-region count, of
    ``BulletinGroupingQuerySet.degenerate()``: that selects the rows the
    writer would now refuse to write, this selects the bulletins it would
    write for. The two share ``MIN_GROUPED_REGIONS`` but not the
    ``Count(..., filter=...)`` expression, so
    ``tests/bulletins/test_bulletin_grouping_model.py`` pins them as a
    partition rather than trusting the shared constant alone.

    A module-level function rather than a method on the command so that test
    is asserting against the query the command actually runs.

    Returns:
        Bulletins with no ``BulletinGrouping`` row linking at least
        ``MIN_GROUPED_REGIONS`` boundaried micro-regions.

    """
    return (
        Bulletin.objects.filter(grouping__isnull=True)
        .alias(
            boundaried_region_count=Count(
                "regions", filter=Q(regions__boundary__isnull=False)
            )
        )
        .filter(boundaried_region_count__gte=MIN_GROUPED_REGIONS)
    )


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
            the service declined to write one (compute returns None — its
            region links changed between the walk and the call), or
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
                    "Skipped bulletin %s — too few boundaried regions",
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

        Queries the bulletins whose ``grouping`` reverse accessor is null and
        which link at least ``MIN_GROUPED_REGIONS`` boundaried micro-regions
        — a bulletin below that threshold is never given a grouping, so
        including it would re-attempt it on every future run (SNOW-1001).
        Delegates per-bulletin work to ``_process_one``.  Failures are
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

        qs = candidate_bulletins()
        total = qs.count()

        self.stdout.write(f"Bulletins missing a grouping they could have: {total}")

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

        for bulletin in iterate_rows(self, qs, verbosity=verbosity):
            outcome = self._process_one(bulletin, verbosity=verbosity)
            if outcome == "created":
                processed += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — created {processed}, skipped {skipped} "
                f"(region links changed since the walk), {failed} failed."
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
