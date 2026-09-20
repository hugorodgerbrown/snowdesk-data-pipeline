"""
apps/bulletins/management/commands/purge_degenerate_bulletin_groupings.py.

Management command.

Deletes ``BulletinGrouping`` rows that draw nothing the micro-region layer
does not already draw — those whose bulletin links fewer than
``apps.bulletins.models.MIN_GROUPED_REGIONS`` boundaried micro-regions
(SNOW-1001). Why such a row is worse than no row at all:
docs/decisions/a-grouping-outline-asserts-an-aggregation.md.

``compute_bulletin_grouping_boundary`` now refuses to write these rows and
deletes any it finds on re-ingest, so this command exists for the rows
already in a database that will not be re-ingested. That is Météo-France in
bulk: it is 1:1 across its whole archive — 4,671 of 4,671 bulletins cover one
massif — so every grouping it has ever written is degenerate. SLF is
aggregated today and will join that population once SNOW-998 lands; ALBINA
stays 99% multi-region and is largely untouched.

Selection is ``BulletinGrouping.objects.degenerate()``, the same predicate
the ingest-time guard reads via ``MIN_GROUPED_REGIONS``, so the writer and
the purge cannot drift apart.

Deliberately a command rather than a data migration: a bulk delete inside a
migration locks the table for the length of a Render deploy.

Read-only by default — pass ``--commit`` to delete (per the project-wide
management command convention; see
docs/decisions/dry-run-default-commands.md).

Typical use::

    # Read-only walk — how many degenerate rows are there?
    python manage.py purge_degenerate_bulletin_groupings

    # Persist the deletions.
    python manage.py purge_degenerate_bulletin_groupings --commit

Exit codes:

- ``0`` — the walk completed (or there was nothing to purge).
- Non-zero — at least one row failed to delete (``CommandError``); the
  remaining chunks are still attempted.
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.models import BulletinGrouping
from apps.core.command_iteration import iterate_rows, positive_int

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500


class Command(BaseCommand):
    """Delete BulletinGrouping rows whose bulletin covers fewer than two regions."""

    help = (
        "Delete BulletinGrouping rows whose bulletin links fewer than "
        "MIN_GROUPED_REGIONS boundaried micro-regions (SNOW-1001) — their "
        "outline duplicates regions-line. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments.

        Args:
            parser: The argument parser to configure.

        """
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Delete the degenerate groupings. Without this flag the "
                "command is read-only and only reports how many rows would "
                "be deleted."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=positive_int,
            default=_DEFAULT_BATCH_SIZE,
            metavar="N",
            help=(
                "Rows per DELETE statement once the walk has collected the "
                f"candidates (default: {_DEFAULT_BATCH_SIZE})."
            ),
        )

    def _collect(self, *, verbosity: int) -> list[int]:
        """Stream the degenerate groupings newest-id-first, collecting their pks.

        Only the primary keys are retained — never the model instances — so a
        purge of a full historical estate stays flat in memory. The delete
        step runs after the walk rather than inside it so no row is removed
        from under the streaming cursor.

        Args:
            verbosity: Django's ``--verbosity`` level, forwarded to
                ``iterate_rows`` so the countdown honours ``-v 0``.

        Returns:
            The primary keys of every degenerate BulletinGrouping row.

        """
        return [
            grouping.pk
            for grouping in iterate_rows(
                self,
                BulletinGrouping.objects.degenerate(),
                verbosity=verbosity,
            )
        ]

    def _delete(self, pks: list[int], *, batch_size: int) -> tuple[int, int]:
        """Delete the groupings named by ``pks`` in chunks of ``batch_size``.

        A chunk that raises is logged and counted as failed; the remaining
        chunks are still attempted, so one bad row cannot strand the rest.

        Args:
            pks: Primary keys of the rows to delete.
            batch_size: Rows per DELETE statement.

        Returns:
            A ``(deleted, failed)`` pair of row counts.

        """
        deleted = 0
        failed = 0
        for start in range(0, len(pks), batch_size):
            chunk = pks[start : start + batch_size]
            try:
                # The first element of delete()'s return counts CASCADED rows
                # too. Nothing references BulletinGrouping today, so the two
                # agree — but the per-label map stays right the day something
                # does.
                _, per_label = BulletinGrouping.objects.filter(pk__in=chunk).delete()
            except Exception:
                logger.exception(
                    "Failed to delete %d degenerate BulletinGrouping row(s)",
                    len(chunk),
                )
                failed += len(chunk)
                continue
            deleted += per_label.get("bulletins.BulletinGrouping", 0)
        return deleted, failed

    def _announce(self, *, commit: bool, verbosity: int) -> None:
        """Write the start-of-run banner.

        Args:
            commit: Whether ``--commit`` was passed.
            verbosity: Django's ``--verbosity`` level.

        """
        if not verbosity:
            return
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Purging degenerate BulletinGrouping rows"
                + ("" if commit else " [READ-ONLY]")
            )
        )

    def _should_delete(self, pks: list[int], *, commit: bool, verbosity: int) -> bool:
        """Report what the walk found and answer whether to go on and delete.

        Args:
            pks: Primary keys collected by ``_collect``.
            commit: Whether ``--commit`` was passed.
            verbosity: Django's ``--verbosity`` level.

        Returns:
            ``True`` when there is something to delete and ``--commit`` was
            passed; ``False`` otherwise.

        """
        if not verbosity:
            return bool(pks) and commit

        self.stdout.write(f"Degenerate groupings: {len(pks)}")
        if not pks:
            self.stdout.write(self.style.SUCCESS("Nothing to purge."))
            return False
        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    f"Read-only run — {len(pks)} grouping(s) would be deleted. "
                    "Pass --commit to persist."
                )
            )
            return False
        return True

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the purge command.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command-line options.

        Raises:
            CommandError: At least one row failed to delete, so cron/CI see a
                non-zero exit.

        """
        commit: bool = options["commit"]
        batch_size: int = options["batch_size"]
        verbosity: int = options["verbosity"]

        self._announce(commit=commit, verbosity=verbosity)

        pks = self._collect(verbosity=verbosity)
        if not self._should_delete(pks, commit=commit, verbosity=verbosity):
            return

        deleted, failed = self._delete(pks, batch_size=batch_size)

        if verbosity:
            self.stdout.write(
                self.style.SUCCESS(f"Done — deleted {deleted}, {failed} failed.")
            )

        logger.info(
            "purge_degenerate_bulletin_groupings finished: "
            "candidates=%d deleted=%d failed=%d commit=%s",
            len(pks),
            deleted,
            failed,
            commit,
        )

        if failed:
            raise CommandError(
                f"{failed} grouping(s) failed to delete. Check logs for details."
            )
