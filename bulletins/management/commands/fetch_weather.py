"""
bulletins/management/commands/fetch_weather.py — Management command: fetch_weather.

Fetches Open-Meteo weather data for all regions on a given date and (optionally)
persists WeatherSnapshot rows to the database. Read-only by default; pass
--commit to write.

The Open-Meteo forecast endpoint is always called (real API probe) regardless of
--commit, so the command serves both as a data-write and a connectivity check.
Regions without a centre coordinate are silently skipped (counted in the banner).

``--source`` selects the upstream:

- ``live`` (default) — the real Open-Meteo forecast API.
- ``local-mirror`` — the development-only view at
  ``/dev/openmeteo-mirror/v1/forecast`` that replays
  ``sample_data/openmeteo_archive.ndjson``. Requires
  ``settings.WEATHER_API_LOCAL_MIRROR_BASE_URL`` (only defined in
  ``development.py``); raises ``CommandError`` otherwise.

``--stash`` captures every fetched ``(region, date)`` record into
``sample_data/openmeteo_archive.ndjson`` (deduped by ``(region_id, date)``,
sorted by ``(region_id, date)``). Independent of ``--commit`` — combine them
for a full-fidelity capture, or use ``--stash`` alone for a read-only archive
refresh.

Usage:
    # Read-only probe for today — no DB writes.
    python manage.py fetch_weather

    # Persist today's weather.
    python manage.py fetch_weather --commit

    # Persist weather for a specific date.
    python manage.py fetch_weather --date 2026-05-01 --commit

    # Replay from the local mirror (dev server must be running).
    python manage.py fetch_weather --source local-mirror --commit

    # Capture today's weather to the archive without DB writes.
    python manage.py fetch_weather --stash

    # Full-fidelity: persist and stash.
    python manage.py fetch_weather --commit --stash
"""

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bulletins.services.openmeteo_archive import merge, read_archive, write_archive
from bulletins.services.weather_fetcher import (
    _SOURCE_LIVE,
    _SOURCE_LOCAL_MIRROR,
    _resolve_weather_source,
    fetch_all_regions,
)
from regions.models import MicroRegion

logger = logging.getLogger(__name__)

_SOURCE_CHOICES = (_SOURCE_LIVE, _SOURCE_LOCAL_MIRROR)


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
        parser.add_argument(
            "--source",
            choices=_SOURCE_CHOICES,
            default=_SOURCE_LIVE,
            help=(
                "Where to fetch from. 'live' (default) hits the real Open-Meteo "
                "forecast API; 'local-mirror' hits the development-only view that "
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
        target: date = options["date"] or timezone.localdate()
        commit: bool = options["commit"]
        source: str = options["source"]
        stash: bool = options["stash"]
        verbosity: int = options["verbosity"]

        try:
            base_url = _resolve_weather_source(source)
        except CommandError:
            raise

        collected: list[dict[str, Any]] = []
        on_fetched = collected.append if stash else None

        region_count = MicroRegion.objects.count()
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if stash:
            flags.append("STASH")
        if source != _SOURCE_LIVE:
            flags.append(f"SOURCE={source.upper()}")
        flag_label = " [" + ", ".join(flags) + "]" if flags else ""

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching weather for {target} ({region_count} region(s)){flag_label}"
            )
        )
        logger.info(
            "fetch_weather started: date=%s regions=%d commit=%s source=%s stash=%s",
            target,
            region_count,
            commit,
            source,
            stash,
        )

        counts = fetch_all_regions(
            target,
            commit=commit,
            base_url=base_url,
            on_fetched=on_fetched,
        )

        if stash:
            self._flush_stash(collected)

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
            "fetch_weather stash flush: collected=%d archive_total=%d path=%s",
            len(collected),
            len(merged),
            path,
        )
