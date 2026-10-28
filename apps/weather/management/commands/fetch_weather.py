"""
apps/weather/management/commands/fetch_weather.py — Management command: fetch_weather.

Fetches **today's** weather for every active location from the Open-Meteo
forecast endpoint. That is the whole job: no date arguments, and nothing
that picks an upstream URL from a date.

**The fetch is the weather service's since SNOW-1253.** Every active
location is a curated one (SNOW-1265), and the curated estate is the
service's station list. With ``--commit`` the estate is mirrored into
ingest's ``SourceLocation`` list, each station is fetched into
``SourceWeather``, and each row is applied into ``Weather`` through
``Location.source_uuid`` — the same apply a pull from the data feed runs, so
production and staging write ``Weather`` from the same rows. A read-only run
mirrors nothing: it probes the same coordinates with the same request and
writes nothing anywhere.

One pass over one anchor. The two passes this replaces — a region pass over
``MicroRegion.centre`` and a point pass over a quantised ``ForecastCell``
grid — resolved to the same places by two different routes; both anchors
are now ``Location``, so ``Location.objects.active()`` is the whole walk.
That is also why ``--skip-points`` is gone: there is no second pass to skip.
``--add-history`` is gone with the history table, whose job the ``forecast``
column now does inside the row.

**Historical days are not this command's business.** A day is written once,
on the day it is current, and that record then stands —
``apps.weather.services.upsert`` refuses to rewrite it. Filling a day this
command missed is a backfill against the archive endpoint (SNOW-731), which
is a different job with a different upstream.

Scheduled 4×/day (00:00/06:00/12:00/18:00 UTC) in ``schedule.py``. Bulletin
regions have a live on-demand fetch behind the page render; locations have
no equivalent, so the scheduled batch is their only freshness mechanism and
the cadence is what keeps today's row current.

Usage:
    # Preview — calls the API, writes nothing.
    uv run python manage.py fetch_weather

    # Persist today's row for every active location.
    uv run python manage.py fetch_weather --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from django.db.models import QuerySet
from django.utils import timezone

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand
from apps.ingest.public import SourceLocation, fetch_source_weather
from apps.locations.models import Location
from apps.weather.services.fetch import fetch_all_locations
from apps.weather.services.reference import (
    apply_source_weather,
    mirror_reference_locations,
)

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Fetch today's Open-Meteo forecast for every active location.

    Read-only by default; pass --commit to persist. Forecast endpoint only.
    Per-location failures are caught, logged and counted — they never abort
    the batch — and the command exits non-zero when any failed.
    """

    help = (
        "Fetch today's Open-Meteo forecast — one Weather row per active "
        "location (one reachable from a resort or a region centroid — the "
        "curated estate). Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the Weather rows. Without this flag the command "
                "calls the API (a real probe) but writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Fetch and optionally persist today's weather for every active location."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        today = timezone.localdate()

        if commit:
            mirrored = mirror_reference_locations()
            self.tally("stations_linked", mirrored["linked"])
            self.tally("stations_created", mirrored["created"])
            self.tally("stations_updated", mirrored["updated"])
            self.tally("stations_deleted", mirrored["deleted"])
            if verbosity >= 1:
                self.stdout.write(
                    f"Stations: {mirrored['created']} created, "
                    f"{mirrored['updated']} updated, "
                    f"{mirrored['deleted']} deleted, "
                    f"{mirrored['linked']} location(s) linked."
                )
            candidates: QuerySet[Any] = SourceLocation.objects.live()
        else:
            candidates = Location.objects.active()
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching {today} weather for {total} station(s){flag_label}"
            )
        )
        logger.info(
            "fetch_weather started: date=%s stations=%d commit=%s",
            today,
            total,
            commit,
        )

        # Streamed, not materialised: the estate grows with every curated
        # point and every region EAWS adds. iterate_rows orders -id and prints a
        # countdown line per row, so an unattended run reads as a countdown
        # to 1 (SNOW-602).
        rows = iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: row.to_string(),
        )
        if commit:
            # Each station's row is applied into Weather as it is written.
            counts = fetch_source_weather(
                today, commit=True, locations=rows, on_row=apply_source_weather
            )
        else:
            counts = fetch_all_locations(today, commit=False, locations=rows)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"fetch_weather completed with {counts['failed']} location "
                f"failure(s). Check logs for details."
            )

    def _report_outcome(
        self,
        counts: dict[str, int],
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Emit the post-run summary to stdout and the structured log.

        Args:
            counts: The counters returned by ``fetch_all_locations``.
            commit: Whether --commit was passed.
            verbosity: Django's ``--verbosity`` level.

        """
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['created']} row(s) created, "
                        f"{counts['updated']} updated, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['failed']} "
                        "location(s) failed. No data written. Pass --commit "
                        "to persist."
                    )
                )
        # Counts for the command run register (SNOW-1190).
        self.tally("created", counts["created"])
        self.tally("updated", counts["updated"])
        self.tally("failed", counts["failed"])
        logger.info(
            "fetch_weather finished: created=%d updated=%d failed=%d commit=%s",
            counts["created"],
            counts["updated"],
            counts["failed"],
            commit,
        )
