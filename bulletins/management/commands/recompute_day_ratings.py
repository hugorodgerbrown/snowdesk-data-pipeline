r"""
bulletins/management/commands/recompute_day_ratings.py — Management command.

Re-derives every ``RegionDayRating`` row under the current v5 headline-only
policy: both ``min_rating`` and ``max_rating`` are set to the bulletin's
headline ``render_model["danger"]["key"]``.

Intended as a post-deployment step after a day-rating policy change.  Iterates
every distinct (region, date) pair present in the ``RegionDayRating`` table and
calls ``recompute_region_day`` for each, optionally filtered to a date window.

Read-only by default — pass ``--commit`` to persist changes (per the
project-wide management command convention).

Typical use::

    # Read-only walk (counts what would change).
    python manage.py recompute_day_ratings

    # Persist.
    python manage.py recompute_day_ratings --commit

    # Narrow to a specific date window.
    python manage.py recompute_day_ratings \\
        --start-date 2026-01-01 --end-date 2026-04-30 --commit
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from pipeline.models import Region

from bulletins.models import RegionDayRating
from bulletins.services.day_rating import recompute_region_day

logger = logging.getLogger(__name__)

_LOG_INTERVAL = 100


class Command(BaseCommand):
    """Re-derive RegionDayRating rows under the current day-rating policy."""

    help = (
        "Re-derive every RegionDayRating row under the current v5 headline-only "
        "policy. Read-only unless --commit is passed. "
        "Use --start-date / --end-date to restrict to a date window."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist recomputed day ratings to the database. "
                "Without this flag the command is read-only."
            ),
        )
        parser.add_argument(
            "--start-date",
            metavar="YYYY-MM-DD",
            help="Only recompute pairs on or after this date (inclusive).",
        )
        parser.add_argument(
            "--end-date",
            metavar="YYYY-MM-DD",
            help="Only recompute pairs on or before this date (inclusive).",
        )

    def _collect_pairs(
        self,
        start_date: date | None,
        end_date: date | None,
    ) -> list[tuple[Any, date]]:
        """Return distinct (region_id, date) pairs, optionally filtered."""
        qs = RegionDayRating.objects.values_list("region_id", "date")
        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)
        return list(qs.distinct())

    def _process_pairs(
        self,
        pairs: list[tuple[Any, date]],
        *,
        commit: bool,
        verbosity: int,
    ) -> tuple[int, int]:
        """Recompute each pair; return (processed, failed) counts."""
        region_cache: dict[Any, Region] = {}
        total = len(pairs)
        processed = 0
        failed = 0

        for region_id, day in pairs:
            if region_id not in region_cache:
                try:
                    region_cache[region_id] = Region.objects.get(pk=region_id)
                except Region.DoesNotExist:
                    logger.error(
                        "Region pk=%s not found — skipping pair(s) for this region",
                        region_id,
                    )
                    failed += 1
                    processed += 1
                    continue

            region = region_cache[region_id]
            try:
                recompute_region_day(region, day, commit=commit)
            except Exception:
                logger.exception(
                    "Failed to recompute day rating for region=%s day=%s",
                    region.region_id,
                    day,
                )
                failed += 1

            processed += 1
            if verbosity >= 1 and processed % _LOG_INTERVAL == 0:
                self.stdout.write(f"  Processed {processed}/{total} pairs …")

        return processed, failed

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the recompute command.

        Collects all distinct (region_id, date) pairs from ``RegionDayRating``,
        applies any date filters, caches ``Region`` objects to avoid N+1 queries,
        then calls ``recompute_region_day`` for each pair.  Logs progress every
        ``_LOG_INTERVAL`` pairs and raises ``CommandError`` if any pair fails.

        Flags:
            --commit: Persist recomputed ratings to the database.
            --start-date: Filter pairs to dates on or after this value.
            --end-date: Filter pairs to dates on or before this value.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        start_date_str: str | None = options["start_date"]
        end_date_str: str | None = options["end_date"]
        start_date = date.fromisoformat(start_date_str) if start_date_str else None
        end_date = date.fromisoformat(end_date_str) if end_date_str else None

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Recomputing day ratings"
                + (f" from {start_date}" if start_date else "")
                + (f" to {end_date}" if end_date else "")
                + ("" if commit else " [READ-ONLY]")
            )
        )

        pairs = self._collect_pairs(start_date, end_date)
        total = len(pairs)
        self.stdout.write(f"(region, date) pairs to process: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        processed, failed = self._process_pairs(
            pairs, commit=commit, verbosity=verbosity
        )

        succeeded = processed - failed
        if commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done — recomputed {succeeded}/{total} pair(s) ({failed} failed)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Read-only run complete — would recompute {succeeded}/{total} "
                    f"pair(s) ({failed} would fail). Pass --commit to persist."
                )
            )

        logger.info(
            "recompute_day_ratings finished: processed=%d failed=%d commit=%s",
            processed,
            failed,
            commit,
        )

        if failed > 0:
            raise CommandError(
                f"{failed} pair(s) failed during recompute. Check logs for details."
            )
