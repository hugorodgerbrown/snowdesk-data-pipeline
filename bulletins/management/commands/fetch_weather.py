"""
bulletins/management/commands/fetch_weather.py — Management command: fetch_weather.

Fetches Open-Meteo weather data for all regions on a given date and (optionally)
persists WeatherSnapshot rows to the database. Read-only by default; pass
--commit to write.

The Open-Meteo forecast endpoint is always called (real API probe) regardless of
--commit, so the command serves both as a data-write and a connectivity check.
Regions without a centre coordinate are silently skipped (counted in the banner).

Usage:
    # Read-only probe for today — no DB writes.
    python manage.py fetch_weather

    # Persist today's weather.
    python manage.py fetch_weather --commit

    # Persist weather for a specific date.
    python manage.py fetch_weather --date 2026-05-01 --commit
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bulletins.services.weather_fetcher import fetch_all_regions
from regions.models import Region

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Fetch Open-Meteo weather for all regions; read-only unless --commit."""

    help = (
        "Fetch Open-Meteo weather snapshots for all regions on the given date "
        "(default: today). The API is always called; pass --commit to persist "
        "results to the database."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help="Date to fetch weather for. Default: today (local timezone).",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist WeatherSnapshot rows to the database. "
                "Without this flag the command is read-only (API is still called)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        target: date = options["date"] or timezone.localdate()
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        region_count = Region.objects.count()
        flag_label = "" if commit else " [READ-ONLY]"

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching weather for {target} ({region_count} region(s)){flag_label}"
            )
        )
        logger.info(
            "fetch_weather started: date=%s regions=%d commit=%s",
            target,
            region_count,
            commit,
        )

        counts = fetch_all_regions(target, commit=commit)

        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['created']} created, "
                        f"{counts['updated']} updated, "
                        f"{counts['skipped']} skipped, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Read-only run complete — no data written. "
                        "Pass --commit to persist."
                    )
                )

        logger.info(
            "fetch_weather finished: date=%s created=%d updated=%d "
            "skipped=%d failed=%d commit=%s",
            target,
            counts["created"],
            counts["updated"],
            counts["skipped"],
            counts["failed"],
            commit,
        )

        if counts["failed"] > 0:
            raise CommandError(
                f"fetch_weather completed with {counts['failed']} region failure(s) "
                f"on {target}. Check logs for details."
            )
