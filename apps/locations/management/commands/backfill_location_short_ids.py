"""backfill_location_short_ids — mint ``Location.short_id`` where it is null.

One-shot backfill for SNOW-797. ``Location.short_id`` is the location's URL
identifier (``/weather/<short_id>/``) and the id ``weather.geojson`` emits,
replacing the integer primary key those surfaces used to expose
(``docs/decisions/no-integer-pks-in-urls.md``). The field's ``default=``
mints one for every row created after the column landed; this command
reaches the rows that pre-date it.

**Not a data migration.** CLAUDE.md forbids bulk dataset updates in
migrations, and a migration cannot mint a per-row token anyway — Django
evaluates a callable default ONCE and stamps every existing row with the
same value, which the unique constraint would then reject. So the schema
migration adds the column with no database-side default and writes
nothing; this ``--commit``-gated command fills it, and a later migration
tightens the column to ``null=False`` once every environment has run it.

Idempotent: the candidate queryset is the rows with no short id, so a
second run selects zero rows. A short id is never regenerated — it is a
public URL.

Usage:
    # Preview — reports what would be written, writes nothing.
    uv run python manage.py backfill_location_short_ids

    # Apply it.
    uv run python manage.py backfill_location_short_ids --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.command_iteration import iterate_rows
from apps.locations.models import Location, generate_short_id

logger = logging.getLogger(__name__)

# 64 bits of entropy against a few hundred rows: a second draw is a
# runaway guard, not a realistic path.
_MINT_ATTEMPTS = 5


class Command(BaseCommand):
    """Mint a short id for every Location that has none.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    row could not be minted, so a partial run is detectable.
    """

    help = (
        "Mint Location.short_id for every row that has none (SNOW-797). "
        "Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the minted short ids. Without this flag the command "
                "reports and writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Mint a short id for every candidate location."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        candidates = Location.objects.filter(short_id__isnull=True)
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Minting a short id for {total} location(s){flag_label}"
            )
        )
        logger.info(
            "backfill_location_short_ids started: candidates=%d commit=%s",
            total,
            commit,
        )

        taken: set[str] = {
            short_id
            for short_id in Location.objects.exclude(short_id__isnull=True).values_list(
                "short_id", flat=True
            )
            if short_id
        }
        counts = {"minted": 0, "failed": 0}
        for location in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.to_string()}",
        ):
            self._backfill_one(location, taken, counts, commit=commit)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_location_short_ids completed with "
                f"{counts['failed']} failure(s). Check logs for details."
            )

    def _backfill_one(
        self,
        location: Location,
        taken: set[str],
        counts: dict[str, int],
        *,
        commit: bool,
    ) -> None:
        """Mint one location's short id and, when committing, persist it.

        Args:
            location: The location to back-fill.
            taken: Every short id held or planned so far; mutated in place.
            counts: The running tally, mutated in place.
            commit: Whether to persist.

        """
        short_id = next(
            (
                candidate
                for candidate in (generate_short_id() for _ in range(_MINT_ATTEMPTS))
                if candidate not in taken
            ),
            None,
        )
        if short_id is None:
            logger.error(
                "backfill_location_short_ids: location id=%s drew %d colliding "
                "ids in a row; left unchanged",
                location.pk,
                _MINT_ATTEMPTS,
            )
            counts["failed"] += 1
            return
        taken.add(short_id)
        counts["minted"] += 1
        if not commit:
            return
        location.short_id = short_id
        location.save(update_fields=["short_id", "updated_at"])

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['minted']} short id(s) minted, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['minted']} short "
                        "id(s) would be minted. No data written. Pass "
                        "--commit to persist."
                    )
                )
        logger.info(
            "backfill_location_short_ids finished: minted=%d failed=%d commit=%s",
            counts["minted"],
            counts["failed"],
            commit,
        )
