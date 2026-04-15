"""
pipeline/management/commands/fetch_data.py — Management command: fetch_data.

Fetches SLF bulletins for today (or a specified date) from the CAAML API
and persists them to the database. Intended to be run on a schedule (e.g.
via cron or a task scheduler).

Usage:
    python manage.py fetch_data
    python manage.py fetch_data --date 2024-06-15
    python manage.py fetch_data --force
    python manage.py fetch_data --dry-run
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from pipeline.services.data_fetcher import run_pipeline

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Fetch SLF bulletins for a single date and persist to the database."""

    help = "Fetch SLF bulletins for a single date (default: today) and store them."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=date.today(),
            metavar="YYYY-MM-DD",
            help="Date to fetch bulletins for (default: today).",
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
        target_date: date = options["date"]
        force: bool = options["force"]
        dry_run: bool = options["dry_run"]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching bulletins for {target_date}"
                + (" [FORCE]" if force else "")
                + (" [DRY RUN]" if dry_run else "")
            )
        )
        logger.info(
            "fetch_data started for %s (force=%s, dry_run=%s)",
            target_date,
            force,
            dry_run,
        )

        try:
            run = run_pipeline(
                start=target_date,
                end=target_date,
                triggered_by="fetch_data command",
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
                    f"Done. Run #{run.pk}: {run.records_created} created, "
                    f"{run.records_updated} updated."
                )
            )

        if run.records_failed > 0:
            raise CommandError(
                f"Run #{run.pk} completed with {run.records_failed} render-model "
                f"failure(s). Bulletins were stored with version=0 error sentinels. "
                f"Run 'rebuild_render_models' after fixing the issue."
            )
