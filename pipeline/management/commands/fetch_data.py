"""
pipeline/management/commands/fetch_data.py — Management command: fetch_data.

Fetches data for today (or a specified date) from the external data source
and persists it to the database. Intended to be run on a schedule (e.g. via
cron or a task scheduler).

Usage:
    python manage.py fetch_data
    python manage.py fetch_data --date 2024-06-15
    python manage.py fetch_data --dry-run
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from pipeline.services.data_fetcher import run_pipeline

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Fetch data for a single date and persist it to the database."""

    help = "Fetch data for a single date (default: today) and store it in the database."

    def add_arguments(self, parser):
        """Register command-line arguments."""
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=date.today(),
            metavar="YYYY-MM-DD",
            help="Date to fetch data for (default: today).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch data but do not write anything to the database.",
        )

    def handle(self, *args, **options):
        """Execute the command."""
        target_date: date = options["date"]
        dry_run: bool = options["dry_run"]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching data for {target_date}"
                + (" [DRY RUN]" if dry_run else "")
            )
        )
        logger.info("fetch_data started for %s (dry_run=%s)", target_date, dry_run)

        try:
            run = run_pipeline(
                start=target_date,
                end=target_date,
                triggered_by="fetch_data command",
                dry_run=dry_run,
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
                    f"Done. Run #{run.pk}: {run.records_created} created, "
                    f"{run.records_updated} updated."
                )
            )
