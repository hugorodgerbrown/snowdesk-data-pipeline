"""
apps/weather/management/commands/fetch_weather.py — Management command: fetch_weather.

Fetches **today's** weather for every active location from the Open-Meteo
forecast endpoint. That is the whole job: no date arguments, and nothing
that picks an upstream URL from a date.

**The fetch is the weather service's since SNOW-1253.** Every active
location is a curated one (SNOW-1265), and the curated estate is the
service's station list. With ``--commit`` the estate is mirrored into
ingest's ``SourceLocation`` list, then one ``fetch_station_task`` per
station is enqueued (SNOW-1399): each fetches its station into
``SourceWeather`` and applies the rows into ``Weather`` through
``Location.source_uuid`` — the same apply a pull from the data feed runs, so
production and staging write ``Weather`` from the same rows. A read-only run
mirrors nothing, asks Open-Meteo nothing and writes nothing: it counts.

**Paced, with a breaker.** ``--delay`` staggers the tasks and is the gap
each waits for on the shared ``open-meteo`` slot before it calls out, so a
run never bursts past Open-Meteo's per-minute limit. A 429 opens the
``open-meteo`` breaker for five minutes; the tasks behind it return
without asking, and the next scheduled run fetches their stations.

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
    # Preview — counts the stations, asks and writes nothing.
    uv run python manage.py fetch_weather

    # Mirror the estate and enqueue one fetch per station.
    uv run python manage.py fetch_weather --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from django.db.models import QuerySet
from django.utils import timezone

from apps.core.command_iteration import non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.ingest.public import SourceLocation
from apps.locations.models import Location
from apps.weather.services.reference import mirror_reference_locations
from apps.weather.services.station_tasks import fetch_station_task

logger = logging.getLogger(__name__)

# Seconds between one station's request and the next. A station costs 2.4
# weighted calls (24 variables over a week), so half a second keeps a run
# near 290 a minute, under the free tier's 600, with the backfill's units
# sharing the same slot.
DEFAULT_DELAY_S = 0.5


class Command(RegisteredCommand):
    """Enqueue today's Open-Meteo forecast for every active station.

    Read-only by default; pass --commit to mirror the estate and enqueue.
    Exits non-zero when a task run inline failed.
    """

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

    help = (
        "Enqueue today's Open-Meteo forecast — one task per station of the "
        "curated estate, each writing a Weather row per day of its week. "
        "Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Mirror the estate and enqueue one fetch per station. Without "
                "this flag the command counts the stations and asks nothing."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=DEFAULT_DELAY_S,
            help=(
                f"Seconds between one station's request and the next "
                f"(default {DEFAULT_DELAY_S}). 0 disables the pacing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Mirror the estate and enqueue a fetch for every station."""
        commit: bool = options["commit"]
        delay: float = options["delay"]
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
            # Read-only mirrors nothing, so the stations it would fetch are
            # the curated estate the mirror would make them from.
            candidates = Location.objects.active()
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Fetching {today} weather for {total} station(s){flag_label}"
            )
        )
        counts = enqueue_each(
            self,
            candidates,
            fetch_station_task,
            args=lambda row: (row.pk, delay),
            describe=lambda row: row.to_string(),
            commit=commit,
            verbosity=verbosity,
            pace=delay,
        )
        logger.info(
            "fetch_weather finished: date=%s enqueued=%d already_queued=%d "
            "failed=%d commit=%s",
            today,
            counts.enqueued,
            counts.already_queued,
            counts.failed,
            commit,
        )
        if verbosity >= 1:
            verb = "enqueued" if commit else "would be enqueued"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {counts.enqueued} station(s) {verb}, "
                    f"{counts.already_queued} already queued, "
                    f"{counts.failed} failed."
                )
            )
        if counts.failed:
            raise CommandError(
                f"fetch_weather completed with {counts.failed} station "
                f"failure(s). Check logs for details."
            )
