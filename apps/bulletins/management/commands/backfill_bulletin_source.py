"""
apps/bulletins/management/commands/backfill_bulletin_source.py — Backfill command.

Back-fills ``Bulletin.source`` for all rows where it is currently blank. The
field is normally set at ingest time by ``upsert_bulletin`` (SNOW-581); this
command populates historical rows that were ingested before the field existed.

The provider is detected from ``raw_data.properties.customData`` via
``detect_source`` — the same discriminator the render-model builder uses.
Deliberately *not* read from ``render_model["source"]``: that value can be
stale (older pipeline versions wrote the legacy ``"euregio"``), absent on a
row whose render model failed to build, and is rewritten wholesale by
``rebuild_render_models``. Provenance is an ingest fact, so it is derived
from the raw payload every time.

Read-only by default — the command derives the source for every eligible row
and reports what *would* be written without touching the database. Pass
``--commit`` to persist.

The queryset (``Bulletin.objects.filter(source="")``) is itself the
idempotency mechanism: a second run after a successful commit selects only
the rows whose payload carries no recognisable source marker.

Usage::

    # Read-only probe — how many bulletins lack a source, and what would
    # be written, broken down by detected provider?
    python manage.py backfill_bulletin_source

    # Persist the detected source values.
    python manage.py backfill_bulletin_source --commit

Exit codes:

- ``0`` — all eligible bulletins processed without error (or nothing to do).
- Non-zero — at least one bulletin's source could not be detected
  (``CommandError``). Failures are logged individually; other bulletins are
  still attempted, and in commit mode the successful ones are still written.
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.models import Bulletin
from apps.bulletins.services.render_model import detect_source
from apps.core.command_iteration import iterate_rows

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


class Command(BaseCommand):
    """Back-fill Bulletin.source for rows where it is currently blank."""

    help = (
        "Back-fill Bulletin.source for rows where it is currently blank, "
        "detecting the provider from raw_data via detect_source. "
        "Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line flags."""
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Persist the detected source values. Omit for a dry-run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the backfill command.

        Iterates every ``Bulletin`` row whose ``source`` is blank, detecting
        the provider from ``raw_data`` in both dry-run and commit mode so the
        read-only output reports a real breakdown of what would change. In
        commit mode, writes are flushed via ``bulk_update`` every
        ``_BATCH_SIZE`` rows plus a trailing flush.

        Flags:
            --commit: Persist the detected source values to the database.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Backfilling Bulletin.source" + ("" if commit else " [READ-ONLY]")
            )
        )

        qs = Bulletin.objects.filter(source="")
        total = qs.count()

        self.stdout.write(f"Bulletins missing a source: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        detected, failed = _process_queryset(
            self, qs, commit=commit, verbosity=verbosity
        )

        self._report_breakdown(detected)

        processed = sum(detected.values())
        if commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done — populated {processed} source(s), {failed} failed."
                )
            )
        else:
            self.stdout.write(
                f"Read-only run — would populate {processed} source(s), "
                f"{failed} would fail. Pass --commit to persist."
            )

        logger.info(
            "backfill_bulletin_source finished: processed=%d failed=%d commit=%s",
            processed,
            failed,
            commit,
        )

        if failed > 0:
            raise CommandError(
                f"{failed} bulletin(s) had no detectable source. "
                "Check logs for details."
            )

    def _report_breakdown(self, detected: Counter[str]) -> None:
        """Print the per-provider tally of detected sources."""
        for source, count in sorted(detected.items()):
            self.stdout.write(f"  {source}: {count}")


def _process_queryset(
    cmd: BaseCommand,
    qs: Any,
    *,
    commit: bool,
    verbosity: int,
) -> tuple[Counter[str], int]:
    """Iterate the queryset, detecting source and optionally bulk-updating.

    In read-only mode the detection still runs — so the summary reports a real
    breakdown of what would change — but nothing is accumulated or written.
    Streams newest-id-first via ``iterate_rows``, printing each processed
    bulletin's pk as a countdown line.

    Args:
        cmd: The calling command, used for progress output.
        qs: Queryset of Bulletin rows with a blank source.
        commit: If True, persist detected values via bulk_update.
        verbosity: Django verbosity level (0–3).

    Returns:
        A ``(detected, failed)`` tuple. ``detected`` counts successfully
        detected bulletins per provider (persisted, in commit mode);
        ``failed`` counts bulletins whose payload carried no known marker.

    """
    detected: Counter[str] = Counter()
    failed = 0
    batch: list[Bulletin] = []

    for bulletin in iterate_rows(cmd, qs, verbosity=verbosity):
        properties = (bulletin.raw_data or {}).get("properties") or {}
        try:
            bulletin.source = detect_source(properties)
        except Exception:
            failed += 1
            logger.exception(
                "Failed to detect source for bulletin %s",
                bulletin.bulletin_id,
            )
            continue

        detected[bulletin.source] += 1

        # Only accumulate when we intend to write. A dry-run over a large table
        # would otherwise hold every eligible row in memory for nothing.
        if commit:
            batch.append(bulletin)

            if len(batch) >= _BATCH_SIZE:
                Bulletin.objects.bulk_update(batch, ["source"], batch_size=_BATCH_SIZE)
                batch = []

    if commit and batch:
        Bulletin.objects.bulk_update(batch, ["source"], batch_size=_BATCH_SIZE)

    return detected, failed
