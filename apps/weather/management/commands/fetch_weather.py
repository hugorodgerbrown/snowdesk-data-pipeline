"""
apps/weather/management/commands/fetch_weather.py — Management command: fetch_weather.

Fetches **today's** weather from the Open-Meteo **forecast** endpoint. That is
the whole job: there are no date arguments, and no logic anywhere in this
command that picks an upstream URL based on a date.

Two passes, both against ``OPEN_METEO_API_BASE_URL``:

  1. Region pass — one ``WeatherSnapshot`` per ``MicroRegion`` with a centre,
     for today.
  2. Active-ForecastCell pass — a ``POINT_FORECAST_DAYS``-day window of
     ``ForecastCellWeather`` per point referenced by a favourite, a resort or
     a location. Pass ``--skip-points`` to run the region pass alone.

**Historical days are not this command's business.** A day is written once, on
the day it is current, and that record then stands. Filling a day this command
missed — an outage, a new region, a fresh environment — is
``backfill_weather``, the only caller of the Open-Meteo archive endpoint in the
codebase. Splitting the two was deliberate: when a single command chose its
endpoint from the date, the day it had just written fell into the next run's
archive sub-range and was silently rewritten with the reanalysis value.

``--add-history`` additionally retains a ``ForecastCellWeatherHistory``
row per stored day (SNOW-575) — this issue's view of each forecast day,
kept for convergence analysis. Off by default, because nothing
user-facing reads it. The scheduled run passes the flag when
``settings.FETCH_WEATHER_ADD_HISTORY`` is set, so retention is an
environment change rather than a deploy (SNOW-629).

Usage:
    # Read-only — calls the API, writes nothing.
    python manage.py fetch_weather

    # Persist today's region snapshots and point forecasts.
    python manage.py fetch_weather --commit

    # Region weather only — skip the active-ForecastCell pass.
    python manage.py fetch_weather --commit --skip-points

    # Also retain the per-issue point-forecast history (SNOW-575).
    python manage.py fetch_weather --commit --add-history
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.regions.models import MicroRegion
from apps.weather.models import ForecastCell
from apps.weather.services.weather_fetcher import (
    fetch_all_points,
    fetch_all_regions,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Fetch today's Open-Meteo forecast for every region and active point.

    Read-only by default; pass --commit to persist. Forecast endpoint only —
    the archive endpoint belongs to ``backfill_weather``.

    SNOW-602 exempt: iterates provider API results (a fetched page/response),
    not a queryset over a growable local table.
    """

    help = (
        "Fetch today's Open-Meteo forecast — one WeatherSnapshot per region, "
        "plus a 7-day ForecastCellWeather window per active point (pass "
        "--skip-points to skip it). Forecast endpoint only; historical days "
        "are filled by backfill_weather. Read-only by default; pass --commit "
        "to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist WeatherSnapshot and ForecastCellWeather rows. "
                "Without this flag the command is read-only (API is still called)."
            ),
        )
        parser.add_argument(
            "--skip-points",
            action="store_true",
            help=(
                "Skip the active-ForecastCell forecast pass; fetch region weather only."
            ),
        )
        parser.add_argument(
            "--add-history",
            action="store_true",
            help=(
                "Also retain a ForecastCellWeatherHistory row per stored day "
                "(SNOW-575) — this issue's view of each forecast day, for "
                "convergence analysis. Off by default: nothing user-facing "
                "reads it. The scheduled run passes this flag when "
                "settings.FETCH_WEATHER_ADD_HISTORY is set, so retention can be "
                "toggled by environment variable without a deploy. No effect "
                "with --skip-points, or without --commit."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        skip_points: bool = options["skip_points"]
        add_history: bool = options["add_history"]
        verbosity: int = options["verbosity"]

        today = timezone.localdate()
        region_count = MicroRegion.objects.count()
        point_count = ForecastCell.objects.active().count()

        self._announce(
            today,
            region_count,
            point_count,
            commit=commit,
            skip_points=skip_points,
            add_history=add_history,
        )

        counts = fetch_all_regions(today, commit=commit)

        if not skip_points:
            point_counts = fetch_all_points(
                today,
                commit=commit,
                add_history=add_history,
            )
            for key in counts:
                counts[key] += point_counts[key]

        self._report_outcome(counts, today, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"fetch_weather completed with {counts['failed']} region/point "
                f"failure(s) for {today}. Check logs for details."
            )

    def _announce(
        self,
        today: date,
        region_count: int,
        point_count: int,
        *,
        commit: bool,
        skip_points: bool,
        add_history: bool,
    ) -> None:
        """Write the start-of-run banner and matching log line."""
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if skip_points:
            flags.append("SKIP-POINTS")
        if add_history:
            flags.append("ADD-HISTORY")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        points_label = "" if skip_points else f", {point_count} active point(s)"

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching today's forecast {today} "
                f"({region_count} region(s){points_label}){flag_label}"
            )
        )
        logger.info(
            "fetch_weather started: date=%s regions=%d points=%d commit=%s "
            "skip_points=%s add_history=%s",
            today,
            region_count,
            point_count,
            commit,
            skip_points,
            add_history,
        )

    def _report_outcome(
        self,
        counts: dict[str, int],
        today: date,
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
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
            "fetch_weather finished: date=%s created=%d updated=%d skipped=%d "
            "failed=%d commit=%s",
            today,
            counts["created"],
            counts["updated"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
