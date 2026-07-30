"""
apps/bulletins/management/commands/backfill_bulletin_target_dates.py — Backfill command.

Back-fills ``Bulletin.target_date`` for all rows where it is currently
``NULL``. The field is normally set at ingest time by ``upsert_bulletin``
(SNOW-560); this command populates historical rows that were ingested before
the field existed.

Read-only by default — the command derives ``target_date`` for every
eligible row via ``target_day_for_valid_from(valid_from)`` and reports what
*would* be written without touching the database. Pass ``--commit`` to
persist.

The queryset (``Bulletin.objects.filter(target_date__isnull=True)``) is
itself the idempotency mechanism: a second run after a successful commit
selects zero rows.

Usage::

    # Read-only probe — how many bulletins are missing a target_date, and
    # what would be written?
    python manage.py backfill_bulletin_target_dates

    # Persist the derived target_date values.
    python manage.py backfill_bulletin_target_dates --commit

Exit codes:

- ``0`` — all eligible bulletins processed without error (or nothing to do).
- Non-zero — at least one bulletin failed to derive a target_date
  (``CommandError``). Failures are logged individually; other bulletins are
  still attempted.
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.models import Bulletin
from apps.bulletins.services.day_rating import target_day_for_valid_from

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_LOG_INTERVAL = 500


class Command(BaseCommand):
    """Back-fill Bulletin.target_date for rows where it is currently NULL."""

    help = (
        "Back-fill Bulletin.target_date for rows where it is currently NULL, "
        "deriving it from valid_from via target_day_for_valid_from. "
        "Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line flags."""
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Persist the derived target_date values. Omit for a dry-run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the backfill command.

        Iterates every ``Bulletin`` row whose ``target_date`` is ``NULL``,
        deriving the value from ``valid_from`` in both dry-run and commit
        mode so the read-only output reports a real count of what would
        change. In commit mode, writes are flushed via ``bulk_update`` every
        ``_BATCH_SIZE`` rows plus a trailing flush.

        Flags:
            --commit: Persist the derived target_date values to the database.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Backfilling Bulletin.target_date" + ("" if commit else " [READ-ONLY]")
            )
        )

        qs = Bulletin.objects.filter(target_date__isnull=True).order_by("id")
        total = qs.count()

        self.stdout.write(f"Bulletins missing a target_date: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        processed, failed = _process_queryset(
            self, qs, total=total, commit=commit, verbosity=verbosity
        )

        if commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done — populated {processed} target_date(s), {failed} failed."
                )
            )
        else:
            self.stdout.write(
                f"Read-only run — would populate {processed} target_date(s), "
                f"{failed} would fail. Pass --commit to persist."
            )

        logger.info(
            "backfill_bulletin_target_dates finished: processed=%d failed=%d commit=%s",
            processed,
            failed,
            commit,
        )

        if failed > 0:
            raise CommandError(
                f"{failed} bulletin(s) failed to derive a target_date. "
                "Check logs for details."
            )


def _process_queryset(
    cmd: BaseCommand,
    qs: Any,
    *,
    total: int,
    commit: bool,
    verbosity: int,
) -> tuple[int, int]:
    """Iterate the queryset, deriving target_date and optionally bulk-updating.

    In read-only mode the derivation still runs — so the summary reports a real
    count of what would change — but nothing is accumulated or written.

    Args:
        cmd: The calling command, used for progress output.
        qs: Queryset of Bulletin rows missing a target_date.
        total: Total number of rows in ``qs``, for progress reporting.
        commit: If True, persist derived values via bulk_update.
        verbosity: Django verbosity level (0–3).

    Returns:
        A ``(processed, failed)`` tuple. ``processed`` counts bulletins whose
        target_date was successfully derived (persisted, in commit mode);
        ``failed`` counts bulletins that raised while deriving.

    """
    processed = 0
    failed = 0
    batch: list[Bulletin] = []

    for bulletin in qs.iterator():
        try:
            bulletin.target_date = target_day_for_valid_from(bulletin.valid_from)
        except Exception:
            failed += 1
            logger.exception(
                "Failed to derive target_date for bulletin %s",
                bulletin.bulletin_id,
            )
            continue

        processed += 1

        # Only accumulate when we intend to write. A dry-run over a large table
        # would otherwise hold every eligible row in memory for nothing.
        if commit:
            batch.append(bulletin)

            if len(batch) >= _BATCH_SIZE:
                Bulletin.objects.bulk_update(
                    batch, ["target_date"], batch_size=_BATCH_SIZE
                )
                batch = []

        if verbosity >= 1 and processed % _LOG_INTERVAL == 0:
            cmd.stdout.write(f"  Processed {processed}/{total} bulletin(s)…")

    if commit and batch:
        Bulletin.objects.bulk_update(batch, ["target_date"], batch_size=_BATCH_SIZE)

    return processed, failed
