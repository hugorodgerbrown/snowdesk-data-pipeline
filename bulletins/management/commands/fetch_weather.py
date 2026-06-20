"""
bulletins/management/commands/fetch_weather.py — Management command: fetch_weather.

Unified batch weather command that covers both today's forecast fetch and
historical backfills. Used by the scheduled cron, historical catch-up runs, and
the bootstrap script. The HTMX ``public:weather_snippet`` lazy-load view remains
the per-request live path for individual bulletin pages.

The command derives a sensible default window when no date flags are given:

  start = first non-null of:
    1. The latest ``WeatherSnapshot.valid_for_date`` already in the DB.
    2. The earliest ``Bulletin.valid_from`` date in the DB.
    3. ``settings.SEASON_START_DATE`` (hard backstop for an empty DB).
  end   = today (local timezone).

Dates strictly before today are fetched via the Open-Meteo archive endpoint;
today (and any future date) is fetched via the forecast endpoint. The command
splits the resolved window at the day boundary automatically.

``--local-mirror`` replaces the old ``--source local-mirror`` flag. It switches
all fetches to the development-only view that replays
``bulletins/local_mirrors/openmeteo_archive.ndjson``. Requires
``settings.WEATHER_API_LOCAL_MIRROR_BASE_URL`` (only defined in
``development.py``); raises ``CommandError`` otherwise.

``--stash`` captures every fetched ``(region, date)`` record into
``bulletins/local_mirrors/openmeteo_archive.ndjson`` (deduped by
``(region_id, date)``, sorted by ``(region_id, date)``). Independent of
``--commit`` — combine them for a full-fidelity capture, or use ``--stash``
alone for a read-only archive refresh.

Usage:
    # Read-only default window — no DB writes.
    python manage.py fetch_weather

    # Persist weather over the default window.
    python manage.py fetch_weather --commit

    # Persist weather for a specific date.
    python manage.py fetch_weather --date 2026-05-01 --commit

    # Persist weather for an explicit range.
    python manage.py fetch_weather --start 2026-01-01 --end 2026-04-30 --commit

    # Replay from the local mirror (dev server must be running).
    python manage.py fetch_weather --local-mirror --commit

    # Capture the default window to the archive without DB writes.
    python manage.py fetch_weather --stash

    # Full-fidelity: persist and stash.
    python manage.py fetch_weather --commit --stash

    # Tighten pacing (archive calls only; default 1.0 s between regions).
    python manage.py fetch_weather \
        --start 2024-11-01 --end 2025-04-30 --delay 2 --commit
"""

import logging
from argparse import ArgumentParser, ArgumentTypeError
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bulletins.models import Bulletin, WeatherSnapshot
from bulletins.services.openmeteo_archive import flush_stash
from bulletins.services.weather_fetcher import (
    SOURCE_LIVE,
    SOURCE_LOCAL_MIRROR,
    backfill_all_regions,
    fetch_all_regions,
    resolve_weather_source,
)
from regions.models import MicroRegion

logger = logging.getLogger(__name__)


def _non_negative_float(raw: str) -> float:
    """
    Argparse ``type=`` helper for non-negative float arguments.

    Raises:
        ArgumentTypeError: if the value is unparseable or negative.

    """
    try:
        value = float(raw)
    except ValueError as exc:
        raise ArgumentTypeError(f"invalid float value: {raw!r}") from exc
    if value < 0:
        raise ArgumentTypeError(f"delay must be non-negative (got {value})")
    return value


class Command(BaseCommand):
    """Fetch Open-Meteo weather for all regions across a resolved date window.

    Read-only by default; pass --commit to persist WeatherSnapshot rows.
    Automatically routes past dates to the archive endpoint and today to the
    forecast endpoint.
    """

    help = (
        "Fetch Open-Meteo weather snapshots for all regions over a resolved date "
        "window (default: latest snapshot → today). Splits the window at today: "
        "past dates use the archive endpoint; today uses the forecast endpoint. "
        "Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Fetch weather for a single date only. "
                "Mutually exclusive with --start/--end."
            ),
        )
        parser.add_argument(
            "--start",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "First date of the fetch window (inclusive). "
                "If omitted without --date, the default window start is derived "
                "from the DB (latest snapshot, earliest bulletin, or "
                "SEASON_START_DATE)."
            ),
        )
        parser.add_argument(
            "--end",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Last date of the fetch window (inclusive). "
                "If omitted without --date, defaults to today (local timezone)."
            ),
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
            "--local-mirror",
            action="store_true",
            help=(
                "Fetch from the development-only local mirror instead of the live "
                "Open-Meteo API. Replays "
                "bulletins/local_mirrors/openmeteo_archive.ndjson "
                "via the dev-only view. Requires "
                "settings.WEATHER_API_LOCAL_MIRROR_BASE_URL (development.py); "
                "raises CommandError otherwise."
            ),
        )
        parser.add_argument(
            "--delay",
            type=_non_negative_float,
            default=1.0,
            metavar="SECONDS",
            help=(
                "Sleep this many seconds between successive per-region archive "
                "calls. Default 1.0 — paces the run inside Open-Meteo's free-tier "
                "rate limit (~60 calls/minute). Pass 0 to disable pacing if you "
                "have a paid plan or a tiny region count; raise it for very long "
                "backfills if you start to see 429 responses. Has no effect on "
                "the forecast endpoint (today only)."
            ),
        )
        parser.add_argument(
            "--stash",
            action="store_true",
            help=(
                "Append every fetched weather record to "
                "bulletins/local_mirrors/openmeteo_archive.ndjson "
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
            today=today,
        )

        total_counts = self._fetch(
            start,
            end,
            today,
            commit=commit,
            delay=delay,
            base_url=base_url,
            on_fetched=on_fetched,
        )

        if stash:
            flush_stash(
                settings.OPENMETEO_ARCHIVE_PATH,
                collected,
                "fetch_weather",
                stdout=self.stdout,
                style=self.style,
            )

        self._report_outcome(
            total_counts, days, start, end, commit=commit, verbosity=verbosity
        )

        if total_counts["failed"] > 0:
            raise CommandError(
                f"fetch_weather completed with {total_counts['failed']} region "
                f"failure(s) for range {start}–{end}. Check logs for details."
            )

    def _resolve_window(
        self,
        target_date: date | None,
        start_opt: date | None,
        end_opt: date | None,
        today: date,
    ) -> tuple[date, date]:
        """
        Resolve the [start, end] fetch window from command-line options.

        Args:
            target_date: Value of --date (single day shorthand), or None.
            start_opt:   Value of --start, or None.
            end_opt:     Value of --end, or None.
            today:       Today's local date.

        Returns:
            A ``(start, end)`` pair of dates (both inclusive).

        Raises:
            CommandError: --date combined with --start or --end; or end < start.

        """
        if target_date is not None and (start_opt is not None or end_opt is not None):
            raise CommandError(
                "--date cannot be combined with --start or --end. "
                "Use --date for a single day, or --start/--end for a range."
            )

        if target_date is not None:
            return target_date, target_date

        end = end_opt if end_opt is not None else today
        start = (
            start_opt if start_opt is not None else self._derive_default_start(today)
        )

        if end < start:
            raise CommandError("--end must be on or after --start.")
        return start, end

    def _fetch(
        self,
        start: date,
        end: date,
        today: date,
        *,
        commit: bool,
        delay: float,
        base_url: str | None,
        on_fetched: Any,
    ) -> dict[str, int]:
        """
        Route the [start, end] window to archive and/or forecast endpoints.

        Dates strictly before today are sent to ``backfill_all_regions``
        (Open-Meteo archive endpoint). Today and any future date are sent to
        ``fetch_all_regions`` (Open-Meteo forecast endpoint). The two sets of
        counts are merged into a single dict.

        Args:
            start:      First date of the window (inclusive).
            end:        Last date of the window (inclusive).
            today:      Today's local date (used as the split boundary).
            commit:     Whether to persist rows.
            delay:      Seconds between per-region archive calls.
            base_url:   Override for the Open-Meteo base URL (mirror).
            on_fetched: Callback invoked once per fetched record (for --stash).

        Returns:
            Merged ``{created, updated, skipped, failed}`` counts.

        """
        total: dict[str, int] = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
        }

        # Past dates (strictly before today) → archive endpoint.
        if start < today:
            archive_end = min(end, today - timedelta(days=1))
            counts = backfill_all_regions(
                start,
                archive_end,
                commit=commit,
                delay=delay,
                base_url=base_url,
                on_fetched=on_fetched,
            )
            for key in total:
                total[key] += counts[key]

        # Today (and any future date) → forecast endpoint.
        if end >= today:
            counts = fetch_all_regions(
                today,
                commit=commit,
                base_url=base_url,
                on_fetched=on_fetched,
            )
            for key in total:
                total[key] += counts[key]

        return total

    def _derive_default_start(self, today: date) -> date:
        """
        Derive the default start date from the DB state.

        Tries, in order:
          1. The latest ``WeatherSnapshot.valid_for_date`` already stored.
          2. The earliest ``Bulletin.valid_from`` date in the DB.
          3. ``settings.SEASON_START_DATE`` as the hard backstop.

        Args:
            today: Today's local date (passed in so tests can pin it).

        Returns:
            The resolved start date.

        """
        latest_snapshot = WeatherSnapshot.objects.latest_date()
        if latest_snapshot is not None:
            logger.debug(
                "fetch_weather: default start derived from latest snapshot: %s",
                latest_snapshot,
            )
            return latest_snapshot

        earliest_bulletin = Bulletin.objects.earliest_valid_from_date()
        if earliest_bulletin is not None:
            logger.debug(
                "fetch_weather: default start derived from earliest bulletin: %s",
                earliest_bulletin,
            )
            return earliest_bulletin

        season_start: date = settings.SEASON_START_DATE
        logger.debug(
            "fetch_weather: default start from SEASON_START_DATE: %s",
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
        today: date,
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

        if start == end:
            window_label = str(start)
        else:
            window_label = f"{start} to {end}"

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching weather {window_label} "
                f"({days} day(s), {region_count} region(s)){flag_label}"
            )
        )
        logger.info(
            "fetch_weather started: start=%s end=%s days=%d regions=%d "
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
            "fetch_weather finished: start=%s end=%s created=%d updated=%d "
            "skipped=%d failed=%d commit=%s",
            start,
            end,
            counts["created"],
            counts["updated"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
