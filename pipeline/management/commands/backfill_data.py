"""
pipeline/management/commands/backfill_data.py — Management command: backfill_data.

Backfills historical SLF bulletins for a given date range. Pages through
the CAAML API in reverse chronological order and stops once it passes the
start date boundary. Supports --force and --dry-run.

Usage:
    python manage.py backfill_data \
        --start-date 2024-01-01 --end-date 2024-12-31
    python manage.py backfill_data \
        --start-date 2024-01-01 --end-date 2024-12-31 --force
    python manage.py backfill_data \
        --start-date 2024-01-01 --end-date 2024-12-31 --dry-run
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from pipeline.services.data_fetcher import run_pipeline

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Backfill SLF bulletins for a specified date range."""

    help = "Backfill SLF bulletins for a date range and store them."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--start-date",
            required=True,
            type=date.fromisoformat,
            metavar="YYYY-MM-DD",
            help="First date to backfill (inclusive).",
        )
        parser.add_argument(
            "--end-date",
            required=True,
            type=date.fromisoformat,
            metavar="YYYY-MM-DD",
            help="Last date to backfill (inclusive).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Upsert existing bulletins instead of skipping them.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch data but do not write anything to the database.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        start: date = options["start_date"]
        end: date = options["end_date"]
        force: bool = options["force"]
        dry_run: bool = options["dry_run"]

        if end < start:
            raise CommandError("--end-date must be on or after --start-date.")

        days = (end - start).days + 1
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Backfilling {days} day(s) from {start} to {end}"
                + (" [FORCE]" if force else "")
                + (" [DRY RUN]" if dry_run else "")
            )
        )
        logger.info(
            "backfill_data started: %s to %s, %d day(s), force=%s, dry_run=%s",
            start,
            end,
            days,
            force,
            dry_run,
        )

        try:
            run = run_pipeline(
                start=start,
                end=end,
                triggered_by="backfill_data command",
                dry_run=dry_run,
                force=force,
            )
        except Exception as exc:
            raise CommandError(f"Pipeline failed: {exc}") from exc

        if run.status == "failed":
            raise CommandError(f"Pipeline run {run.pk} failed: {run.error_message}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete — no data written."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Backfill complete. Run #{run.pk}: "
                    f"{run.records_created} created, {run.records_updated} updated "
                    f"across {days} day(s)."
                )
            )
        logger.info(
            "backfill_data finished: run=%s status=%s created=%s updated=%s",
            run.pk,
            run.status,
            run.records_created,
            run.records_updated,
        )
