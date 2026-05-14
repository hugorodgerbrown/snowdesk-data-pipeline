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
  ``bulletins/local_mirrors/openmeteo_archive.ndjson``. Requires
  ``settings.WEATHER_API_LOCAL_MIRROR_BASE_URL`` (only defined in
  ``development.py``); raises ``CommandError`` otherwise.

``--stash`` captures every fetched ``(region, date)`` record into
``bulletins/local_mirrors/openmeteo_archive.ndjson`` (deduped by
``(region_id, date)``, sorted by ``(region_id, date)``). Independent of
``--commit`` — combine them for a full-fidelity capture, or use ``--stash``
alone for a read-only archive refresh.

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
import warnings
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bulletins.services.openmeteo_archive import flush_stash
from bulletins.services.weather_fetcher import (
    SOURCE_LIVE,
    SOURCE_LOCAL_MIRROR,
    fetch_all_regions,
    resolve_weather_source,
)
from regions.models import MicroRegion

logger = logging.getLogger(__name__)

_SOURCE_CHOICES = (SOURCE_LIVE, SOURCE_LOCAL_MIRROR)


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
            default=SOURCE_LIVE,
            help=(
                "Where to fetch from. 'live' (default) hits the real Open-Meteo "
                "forecast API; 'local-mirror' hits the development-only view that "
                "replays bulletins/local_mirrors/openmeteo_archive.ndjson. "
                "The mirror is only available when "
                "settings.WEATHER_API_LOCAL_MIRROR_BASE_URL is configured "
                "(development.py)."
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
        warnings.warn(
            "fetch_weather is the legacy batch path. The primary live path for "
            "fetching weather data is now the HTMX-triggered public:weather_snippet "
            "view, which fetches just-in-time when a bulletin page renders without "
            "a WeatherSnapshot. Use backfill_weather for historical catch-up runs.",
            PendingDeprecationWarning,
            stacklevel=2,
        )
        target: date = options["date"] or timezone.localdate()
        commit: bool = options["commit"]
        source: str = options["source"]
        stash: bool = options["stash"]
        verbosity: int = options["verbosity"]

        base_url = resolve_weather_source(source)

        collected: list[dict[str, Any]] = []
        on_fetched = collected.append if stash else None

        region_count = MicroRegion.objects.count()
        self._announce(target, region_count, commit=commit, stash=stash, source=source)

        counts = fetch_all_regions(
            target,
            commit=commit,
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

        self._report_outcome(counts, target, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"fetch_weather completed with {counts['failed']} region failure(s) "
                f"on {target}. Check logs for details."
            )

    def _announce(
        self,
        target: date,
        region_count: int,
        *,
        commit: bool,
        stash: bool,
        source: str,
    ) -> None:
        """Write the start-of-run banner and matching log line."""
        flags: list[str] = []
        if not commit:
            flags.append("READ-ONLY")
        if stash:
            flags.append("STASH")
        if source != SOURCE_LIVE:
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

    def _report_outcome(
        self,
        counts: dict[str, int],
        target: date,
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
            "fetch_weather finished: date=%s created=%d updated=%d "
            "skipped=%d failed=%d commit=%s",
            target,
            counts["created"],
            counts["updated"],
            counts["skipped"],
            counts["failed"],
            commit,
        )
