"""
pipeline/management/commands/fetch_bulletins.py — Management command: fetch_bulletins.

Fetches SLF bulletins from the CAAML API across a date range and (optionally)
persists them to the database. Supersedes the previous fetch_data and
backfill_data commands.

Defaults are tuned for the unattended scheduled-run case:
  * --start-date defaults to settings.SEASON_START_DATE (snowpack build-up).
  * --end-date defaults to today (UTC).
  * No writes happen unless --commit is passed (read-only by default).

Usage:
    # Read-only walk over the whole season so far — the "what would happen?" probe.
    python manage.py fetch_bulletins

    # Persist a specific day (typical scheduled-run shape).
    python manage.py fetch_bulletins --date 2026-01-15 --commit

    # Backfill a window.
    python manage.py fetch_bulletins \
        --start-date 2026-01-01 --end-date 2026-01-31 --commit

    # Re-pull existing rows.
    python manage.py fetch_bulletins --commit --force
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pipeline.models import PipelineRun
from pipeline.services.data_fetcher import run_pipeline

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Fetch SLF bulletins for a date range; read-only unless --commit."""

    help = (
        "Fetch SLF bulletins for a date range. Defaults: "
        "--start-date=settings.SEASON_START_DATE, --end-date=today. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--start-date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "First date to fetch (inclusive). Default: settings.SEASON_START_DATE."
            ),
        )
        parser.add_argument(
            "--end-date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help="Last date to fetch (inclusive). Default: today (UTC).",
        )
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Single-day shortcut — sets both start and end to this date. "
                "Mutually exclusive with --start-date / --end-date."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist changes to the database. "
                "Without this flag the command is read-only."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Upsert existing bulletins instead of skipping them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        start, end = self._resolve_dates(options)
        commit: bool = options["commit"]
        force: bool = options["force"]
        days = (end - start).days + 1

        self._announce(start, end, days, commit=commit, force=force)

        try:
            run = run_pipeline(
                start=start,
                end=end,
                triggered_by="fetch_bulletins command",
                dry_run=not commit,
                force=force,
            )
        except Exception as exc:
            raise CommandError(f"Pipeline failed: {exc}") from exc

        if run.status == "failed":
            raise CommandError(f"Pipeline run {run.pk} failed: {run.error_message}")

        self._report_outcome(run, days, commit=commit)

        if run.records_failed > 0:
            raise CommandError(
                f"Run #{run.pk} completed with {run.records_failed} render-model "
                f"failure(s). Bulletins were stored with version=0 error sentinels. "
                f"Run 'rebuild_render_models' after fixing the issue."
            )

    def _resolve_dates(self, options: dict[str, Any]) -> tuple[date, date]:
        """
        Collapse the --date / --start-date / --end-date options into a range.

        Raises:
            CommandError: if --date is combined with the range flags, or if
                the resolved end precedes the resolved start.

        """
        single: date | None = options["date"]
        start_arg: date | None = options["start_date"]
        end_arg: date | None = options["end_date"]

        if single is not None and (start_arg is not None or end_arg is not None):
            raise CommandError(
                "--date is mutually exclusive with --start-date / --end-date."
            )

        if single is not None:
            start = end = single
        else:
            start = start_arg or settings.SEASON_START_DATE
            end = end_arg or timezone.localdate()

        if end < start:
            raise CommandError("--end-date must be on or after --start-date.")
        return start, end

    def _announce(
        self,
        start: date,
        end: date,
        days: int,
        *,
        commit: bool,
        force: bool,
    ) -> None:
        """Write the start-of-run banner and matching log line."""
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if force:
            flags.append("FORCE")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching bulletins from {start} to {end} ({days} day(s)){flag_label}"
            )
        )
        logger.info(
            "fetch_bulletins started: %s to %s, %d day(s), commit=%s, force=%s",
            start,
            end,
            days,
            commit,
            force,
        )

    def _report_outcome(self, run: PipelineRun, days: int, *, commit: bool) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    "Read-only run complete — no data written. "
                    "Pass --commit to persist."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. Run #{run.pk}: {run.records_created} created, "
                    f"{run.records_updated} updated across {days} day(s)."
                )
            )

        logger.info(
            "fetch_bulletins finished: run=%s status=%s "
            "created=%s updated=%s failed=%s",
            run.pk,
            run.status,
            run.records_created,
            run.records_updated,
            run.records_failed,
        )
