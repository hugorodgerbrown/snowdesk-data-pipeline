"""
apps/weather/management/commands/backfill_weather.py — the backfill_weather command.

Fills gaps in ``WeatherSnapshot`` from the Open-Meteo **archive** endpoint.
This is the only caller of that endpoint in the codebase — no view, no
background thread, and no other command chooses an upstream URL from a date.

A day is normally written once, by ``fetch_weather``, on the day it is
current. This command exists for the days that never happened: a scheduler
outage, a region added mid-season, a fresh environment with an empty table.
It is additive by construction — writes are create-only, so a day already
stored is left exactly as the forecast pass wrote it and re-running is safe.

Gap detection happens before any HTTP call
(``apps.weather.services.weather_fetcher.backfill_all_regions``): a region
with no missing days in the window costs nothing, and a region with holes is
requested only across the span that contains them. A bare re-run on a fully
populated season therefore makes zero API calls.

The default window is the whole bulletin archive up to yesterday:

  start = the earliest ``Bulletin.valid_from`` date in the DB, or
          ``settings.SEASON_START_DATE`` when there are no bulletins.
  end   = yesterday (local timezone).

Today is never included — today belongs to ``fetch_weather``, and the archive
has no entry for a day that has not finished.

Points (``ForecastCell`` / ``ForecastCellWeather``) have no archive path at
all and are untouched here: Open-Meteo cannot say what the forecast for a
point would have been in the past (SNOW-416, SNOW-417).

Usage:
    # Read-only — reports the gaps it would fill, writes nothing.
    python manage.py backfill_weather

    # Fill every gap from the start of the bulletin archive to yesterday.
    python manage.py backfill_weather --commit

    # A single missed day.
    python manage.py backfill_weather --date 2026-08-26 --commit

    # An explicit range.
    python manage.py backfill_weather --start 2026-01-01 --end 2026-04-30 --commit

    # Replay from the local mirror (dev server must be running).
    python manage.py backfill_weather --local-mirror --commit

    # Capture the window to the on-disk archive without DB writes.
    python manage.py backfill_weather --stash

    # Tighten pacing between per-region archive calls (default 1.0 s).
    python manage.py backfill_weather --start 2024-11-01 --end 2025-04-30 \
        --delay 2 --commit
"""

import logging
from argparse import ArgumentParser
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# ``Bulletin`` is the one cross-app read here: the weather backfill window
# legitimately defaults to the start of the bulletin archive, since weather is
# only useful for days a bulletin exists for.
from apps.bulletins.models import Bulletin
from apps.core.command_iteration import non_negative_float
from apps.regions.models import MicroRegion
from apps.weather.services.openmeteo_archive import flush_stash
from apps.weather.services.weather_fetcher import (
    SOURCE_LIVE,
    SOURCE_LOCAL_MIRROR,
    backfill_all_regions,
    resolve_weather_source,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Fill WeatherSnapshot gaps from the Open-Meteo archive endpoint.

    Read-only by default; pass --commit to persist. Create-only: an existing
    day is never rewritten.

    SNOW-602 exempt: iterates provider API results (a fetched page/response),
    not a queryset over a growable local table.
    """

    help = (
        "Fill gaps in WeatherSnapshot from the Open-Meteo archive endpoint "
        "(default window: the earliest bulletin date, or SEASON_START_DATE, "
        "through yesterday). Create-only — a day already stored is left as "
        "the forecast pass wrote it. Regions with no missing days cost no API "
        "call. Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Backfill a single date only. Mutually exclusive with --start/--end."
            ),
        )
        parser.add_argument(
            "--start",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "First date of the backfill window (inclusive). If omitted "
                "without --date, defaults to the earliest Bulletin.valid_from "
                "date, or SEASON_START_DATE when the DB holds no bulletins."
            ),
        )
        parser.add_argument(
            "--end",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Last date of the backfill window (inclusive). If omitted "
                "without --date, defaults to yesterday (local timezone)."
            ),
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist WeatherSnapshot rows for the missing days. "
                "Without this flag the command is read-only (API is still called)."
            ),
        )
        parser.add_argument(
            "--local-mirror",
            action="store_true",
            help=(
                "Fetch from the development-only local mirror instead of the live "
                "Open-Meteo API. Replays "
                "apps/weather/local_mirrors/openmeteo_archive.ndjson "
                "via the dev-only view. Requires "
                "settings.WEATHER_API_LOCAL_MIRROR_BASE_URL (development.py); "
                "raises CommandError otherwise."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=1.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive per-region archive "
                "calls. Default 1.0 — paces the run inside Open-Meteo's free-tier "
                "rate limit (~60 calls/minute). Pass 0 to disable pacing if you "
                "have a paid plan or a tiny region count; raise it for very long "
                "backfills if you start to see 429 responses."
            ),
        )
        parser.add_argument(
            "--stash",
            action="store_true",
            help=(
                "Append every fetched weather record to "
                "apps/weather/local_mirrors/openmeteo_archive.ndjson "
                "(deduped by (region_id, date), sorted by (region_id, date)). "
                "Independent of --commit — combine them for a full-fidelity "
                "capture, or use --stash alone for a read-only archive refresh."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        target_date: date | None = options["date"]
        start_opt: date | None = options["start"]
        end_opt: date | None = options["end"]
        commit: bool = options["commit"]
        local_mirror: bool = options["local_mirror"]
        delay: float = options["delay"]
        stash: bool = options["stash"]
        verbosity: int = options["verbosity"]

        today = timezone.localdate()
        start, end = self._resolve_window(target_date, start_opt, end_opt, today)

        source = SOURCE_LOCAL_MIRROR if local_mirror else SOURCE_LIVE
        base_url = resolve_weather_source(source)

        collected: list[dict[str, Any]] = []
        on_fetched = collected.append if stash else None

        days = (end - start).days + 1
        region_count = MicroRegion.objects.count()

        self._announce(
            start,
            end,
            days,
            region_count,
            commit=commit,
            delay=delay,
            stash=stash,
            local_mirror=local_mirror,
        )

        counts = backfill_all_regions(
            start,
            end,
            commit=commit,
            delay=delay,
            base_url=base_url,
            on_fetched=on_fetched,
        )

        if stash:
            flush_stash(
                settings.OPENMETEO_ARCHIVE_PATH,
                collected,
                "backfill_weather",
                stdout=self.stdout,
                style=self.style,
            )

        self._report_outcome(
            counts, days, start, end, commit=commit, verbosity=verbosity
        )

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_weather completed with {counts['failed']} region "
                f"failure(s) for range {start}–{end}. Check logs for details."
            )

    def _resolve_window(
        self,
        target_date: date | None,
        start_opt: date | None,
        end_opt: date | None,
        today: date,
    ) -> tuple[date, date]:
        """Resolve the [start, end] backfill window from the given options.

        Args:
            target_date: ``--date``, when given — pins start == end.
            start_opt:   ``--start``, when given.
            end_opt:     ``--end``, when given.
            today:       Today's local date (passed in so tests can pin it).

        Returns:
            The resolved ``(start, end)`` pair, both inclusive.

        Raises:
            CommandError: If ``--date`` is combined with ``--start``/``--end``,
                if ``--end`` precedes ``--start``, or if the window reaches
                today or later — today belongs to ``fetch_weather``, and the
                archive has no entry for an unfinished day.

        """
        if target_date is not None:
            if start_opt is not None or end_opt is not None:
                raise CommandError("--date cannot be combined with --start/--end.")
            start = end = target_date
        else:
            end = end_opt if end_opt is not None else today - timedelta(days=1)
            start = start_opt if start_opt is not None else self._derive_default_start()

        if end < start:
            raise CommandError("--end must be on or after --start.")
        if end >= today:
            raise CommandError(
                f"backfill_weather covers past days only; --end must be before "
                f"{today}. Today's weather is written by fetch_weather."
            )
        return start, end

    def _derive_default_start(self) -> date:
        """Derive the default start date from the DB state.

        Tries, in order:
          1. The earliest ``Bulletin.valid_from`` date in the DB.
          2. ``settings.SEASON_START_DATE`` as the hard backstop.

        Unlike the retired routing command, this never keys off the latest
        stored snapshot: gap detection is what bounds the work now, so the
        window can safely span the whole archive and still make no API call
        for a region that is already complete.

        Returns:
            The resolved start date.

        """
        earliest_bulletin = Bulletin.objects.earliest_valid_from_date()
        if earliest_bulletin is not None:
            logger.debug(
                "backfill_weather: default start derived from earliest bulletin: %s",
                earliest_bulletin,
            )
            return earliest_bulletin

        season_start: date = settings.SEASON_START_DATE
        logger.debug(
            "backfill_weather: default start from SEASON_START_DATE: %s",
            season_start,
        )
        return season_start

    def _announce(
        self,
        start: date,
        end: date,
        days: int,
        region_count: int,
        *,
        commit: bool,
        delay: float,
        stash: bool,
        local_mirror: bool,
    ) -> None:
        """Write the start-of-run banner and matching log line."""
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if local_mirror:
            flags.append("LOCAL-MIRROR")
        if delay > 0:
            flags.append(f"DELAY={delay:g}s")
        if stash:
            flags.append("STASH")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        window_label = str(start) if start == end else f"{start} to {end}"

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Backfilling weather gaps {window_label} "
                f"({days} day(s), {region_count} region(s)){flag_label}"
            )
        )
        logger.info(
            "backfill_weather started: start=%s end=%s days=%d regions=%d "
            "commit=%s local_mirror=%s delay=%s stash=%s",
            start,
            end,
            days,
            region_count,
            commit,
            local_mirror,
            delay,
            stash,
        )

    def _report_outcome(
        self,
        counts: dict[str, int],
        days: int,
        start: date,
        end: date,
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['created']} gap(s) filled, "
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
            "backfill_weather finished: start=%s end=%s created=%d skipped=%d "
            "failed=%d commit=%s",
            start,
            end,
            counts["created"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
