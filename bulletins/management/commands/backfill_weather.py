r"""
bulletins/management/commands/backfill_weather.py — backfill_weather command.

Backfills Open-Meteo historical weather data (via the archive endpoint) for all
regions across a date range. Read-only by default; pass --commit to write.

Requires --start and --end (both YYYY-MM-DD). --end must be on or after --start.

The Open-Meteo archive endpoint enforces a tight free-tier rate limit, so the
command paces calls by default: ``--delay`` defaults to ``1.0`` seconds between
successive per-region archive calls (~60 calls/minute, comfortably under the
limit). Pass ``--delay 0`` to disable pacing if you have a paid Open-Meteo plan
or are running a tiny region count.

``--source`` selects the upstream:

- ``live`` (default) — the real Open-Meteo archive API.
- ``local-mirror`` — the development-only view at
  ``/dev/openmeteo-mirror/v1/archive`` that replays
  ``sample_data/openmeteo_archive.ndjson``. Requires
  ``settings.WEATHER_API_LOCAL_MIRROR_BASE_URL`` (only defined in
  ``development.py``); raises ``CommandError`` otherwise.

``--stash`` captures every fetched ``(region, date)`` record into
``sample_data/openmeteo_archive.ndjson`` (deduped by ``(region_id, date)``,
sorted by ``(region_id, date)``). Independent of ``--commit`` — combine them
for a full-fidelity capture, or use ``--stash`` alone for a read-only archive
refresh.

Usage:
    # Dry-run probe for a winter season window (paced at 1 s/region by default).
    python manage.py backfill_weather --start 2025-12-01 --end 2026-04-30

    # Persist historical weather for the full season.
    python manage.py backfill_weather --start 2025-12-01 --end 2026-04-30 --commit

    # Replay from the local mirror (instant; dev server must be running).
    python manage.py backfill_weather \
        --start 2026-01-01 --end 2026-01-31 --source local-mirror --commit

    # Capture a range to the archive without DB writes.
    python manage.py backfill_weather --start 2025-12-01 --end 2026-04-30 --stash

    # Tighten or disable pacing.
    python manage.py backfill_weather \
        --start 2024-11-01 --end 2025-04-30 --delay 2 --commit
    python manage.py backfill_weather \
        --start 2024-11-01 --end 2025-04-30 --delay 0 --commit
"""

import argparse
import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bulletins.services.openmeteo_archive import merge, read_archive, write_archive
from bulletins.services.weather_fetcher import (
    _SOURCE_LIVE,
    _SOURCE_LOCAL_MIRROR,
    _resolve_weather_source,
    backfill_all_regions,
)
from regions.models import MicroRegion

logger = logging.getLogger(__name__)

_SOURCE_CHOICES = (_SOURCE_LIVE, _SOURCE_LOCAL_MIRROR)


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
            "--source",
            choices=_SOURCE_CHOICES,
            default=_SOURCE_LIVE,
            help=(
                "Where to fetch from. 'live' (default) hits the real Open-Meteo "
                "archive API; 'local-mirror' hits the development-only view that "
                "replays sample_data/openmeteo_archive.ndjson. The mirror is only "
                "available when settings.WEATHER_API_LOCAL_MIRROR_BASE_URL is "
                "configured (development.py)."
            ),
        )
        parser.add_argument(
            "--stash",
            action="store_true",
            help=(
                "Append every fetched weather record to "
                "sample_data/openmeteo_archive.ndjson (deduped by (region_id, date), "
                "sorted by (region_id, date)). Independent of --commit — combine them "
                "for a full-fidelity capture, or use --stash alone for a read-only "
                "archive refresh."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        start: date = options["start"]
        end: date = options["end"]
        commit: bool = options["commit"]
        delay: float = options["delay"]
        source: str = options["source"]
        stash: bool = options["stash"]
        verbosity: int = options["verbosity"]

        if end < start:
            raise CommandError("--end must be on or after --start.")

        try:
            base_url = _resolve_weather_source(source)
        except CommandError:
            raise

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
            source=source,
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
            self._flush_stash(collected)

        self._report_outcome(
            counts, days, start, end, commit=commit, verbosity=verbosity
        )

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_weather completed with {counts['failed']} region failure(s) "
                f"for range {start}–{end}. Check logs for details."
            )

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
        source: str,
    ) -> None:
        """Write the start-of-run banner and matching log line."""
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if delay > 0:
            flags.append(f"DELAY={delay:g}s")
        if stash:
            flags.append("STASH")
        if source != _SOURCE_LIVE:
            flags.append(f"SOURCE={source.upper()}")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Backfilling weather from {start} to {end} "
                f"({days} day(s), {region_count} region(s)){flag_label}"
            )
        )
        logger.info(
            "backfill_weather started: start=%s end=%s days=%d regions=%d "
            "commit=%s delay=%s source=%s stash=%s",
            start,
            end,
            days,
            region_count,
            commit,
            delay,
            source,
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

    def _flush_stash(self, collected: list[dict[str, Any]]) -> None:
        """
        Merge collected weather records into the on-disk Open-Meteo archive.

        Reads the existing archive, overlays the freshly-collected records
        (later ``captured_at`` wins per ``(region_id, date)`` key), sorts by
        ``(region_id, date)``, and atomically writes the result back to
        ``settings.OPENMETEO_ARCHIVE_PATH``.
        """
        path = settings.OPENMETEO_ARCHIVE_PATH
        existing = list(read_archive(path))
        merged = merge(existing, collected)
        write_archive(path, merged)
        self.stdout.write(
            self.style.SUCCESS(
                f"Stashed {len(collected)} fetched record(s) to {path}; "
                f"archive now contains {len(merged)} record(s)."
            )
        )
        logger.info(
            "backfill_weather stash flush: collected=%d archive_total=%d path=%s",
            len(collected),
            len(merged),
            path,
        )
