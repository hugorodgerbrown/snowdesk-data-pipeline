r"""
bulletins/management/commands/backfill_weather.py — backfill_weather command.

Backfills Open-Meteo historical weather data (via the archive endpoint) for all
regions across a date range. Read-only by default; pass --commit to write.

Requires --start and --end (both YYYY-MM-DD). --end must be on or after --start.
An optional --delay flag introduces a sleep between successive region calls (in
seconds) to act as a good citizen on the Open-Meteo API.

Usage:
    # Dry-run probe for a winter season window.
    python manage.py backfill_weather --start 2025-12-01 --end 2026-04-30

    # Persist historical weather for the full season.
    python manage.py backfill_weather --start 2025-12-01 --end 2026-04-30 --commit

    # Pace the calls (0.5 s between regions) for long backfills.
    python manage.py backfill_weather \
        --start 2024-11-01 --end 2025-04-30 --delay 0.5 --commit
"""

import argparse
import logging
import time
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from bulletins.services.weather_fetcher import (
    backfill_all_regions,
    fetch_archive_for_region,
)
from pipeline.models import Region

logger = logging.getLogger(__name__)


def _non_negative_float(raw: str) -> float:
    """
    Argparse ``type=`` helper for non-negative float arguments.

    Raises:
        argparse.ArgumentTypeError: if the value is unparseable or negative.

    """
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid float value: {raw!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"delay must be non-negative (got {value})")
    return value


class Command(BaseCommand):
    """Backfill Open-Meteo historical weather for all regions.

    Read-only by default; pass --commit to persist WeatherSnapshot rows.
    """

    help = (
        "Backfill Open-Meteo weather snapshots for all regions over a date range. "
        "Requires --start and --end. Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--start",
            type=date.fromisoformat,
            required=True,
            metavar="YYYY-MM-DD",
            help="First date in the backfill range (inclusive).",
        )
        parser.add_argument(
            "--end",
            type=date.fromisoformat,
            required=True,
            metavar="YYYY-MM-DD",
            help="Last date in the backfill range (inclusive).",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist WeatherSnapshot rows to the database. "
                "Without this flag the command is read-only (API is still called)."
            ),
        )
        parser.add_argument(
            "--delay",
            type=_non_negative_float,
            default=0.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive region archive calls. "
                "Default 0 (no delay). Use for long backfills to be a considerate "
                "Open-Meteo API consumer."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        start: date = options["start"]
        end: date = options["end"]
        commit: bool = options["commit"]
        delay: float = options["delay"]
        verbosity: int = options["verbosity"]

        if end < start:
            raise CommandError("--end must be on or after --start.")

        days = (end - start).days + 1
        region_count = Region.objects.count()

        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if delay > 0:
            flags.append(f"DELAY={delay:g}s")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Backfilling weather from {start} to {end} "
                f"({days} day(s), {region_count} region(s)){flag_label}"
            )
        )
        logger.info(
            "backfill_weather started: start=%s end=%s days=%d regions=%d "
            "commit=%s delay=%s",
            start,
            end,
            days,
            region_count,
            commit,
            delay,
        )

        if delay > 0:
            counts = _backfill_with_delay(start, end, commit=commit, delay=delay)
        else:
            counts = backfill_all_regions(start, end, commit=commit)

        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['created']} created, "
                        f"{counts['updated']} updated, "
                        f"{counts['skipped']} skipped, "
                        f"{counts['failed']} failed "
                        f"across {days} day(s)."
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
            "backfill_weather finished: start=%s end=%s created=%d updated=%d "
            "skipped=%d failed=%d commit=%s",
            start,
            end,
            counts["created"],
            counts["updated"],
            counts["skipped"],
            counts["failed"],
            commit,
        )

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_weather completed with {counts['failed']} region failure(s) "
                f"for range {start}–{end}. Check logs for details."
            )


def _backfill_with_delay(
    start: date,
    end: date,
    *,
    commit: bool,
    delay: float,
) -> dict[str, int]:
    """
    Backfill all regions, sleeping ``delay`` seconds between each region.

    Mirrors the logic of ``backfill_all_regions`` but injects a sleep
    between per-region archive calls so the command is a good citizen on
    the Open-Meteo API during long historical backfills.

    Args:
        start: First date in the range (inclusive).
        end: Last date in the range (inclusive).
        commit: If True, persist snapshots to the database.
        delay: Seconds to sleep between successive region calls.

    Returns:
        Aggregated counters (created, updated, failed, skipped).

    """
    counts: dict[str, int] = {
        "created": 0,
        "updated": 0,
        "failed": 0,
        "skipped": 0,
    }

    regions = list(Region.objects.all().order_by("region_id"))
    for idx, region in enumerate(regions):
        if not region.centre:
            counts["skipped"] += 1
            continue

        try:
            results = fetch_archive_for_region(region, start, end, commit=commit)

            if commit:
                for _snapshot, created in results:
                    if created:
                        counts["created"] += 1
                    else:
                        counts["updated"] += 1

        except Exception:  # noqa: BLE001 — broad catch intentional: per-region failure must not abort the batch
            logger.warning(
                "Failed to backfill region=%s", region.region_id, exc_info=True
            )
            counts["failed"] += 1

        # Sleep between regions, but not after the last one.
        if idx < len(regions) - 1:
            time.sleep(delay)

    return counts
