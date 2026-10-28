"""
apps/weather/management/commands/backfill_weather.py — The backfill_weather command.

Fills the **missing** days for the active locations, from
``settings.WEATHER_BACKFILL_FLOOR`` up to yesterday, against the Open-Meteo
historical forecast endpoint (SNOW-731).

The complement of ``fetch_weather``, which writes today and only today. A
day is recorded once, on the day it is current, and that record then stands
— so this command is not a re-fetch. It asks only for the days a location
has no row for, and a location with no gaps costs no request at all.

**A budgeted sweep, newest fortnight first (SNOW-1245).** The window is cut
into fourteen-day blocks — the unit Open-Meteo charges one weighted call
for — and walked newest first. Each block is finished across every selected
location before the next one starts, with one request per location per
block. The run stops before the request that would take it past
``--budget`` weighted calls, and at once on a 429. The default budget is
this hour's share of what ``fetch_weather`` leaves of the free tier's daily
quota (``backfill.sweep_budget``), so a bare ``--commit`` run is exactly
what the scheduler fires every hour, and an operator who wants more passes
a bigger number. A run that stops partway through a block resumes it next
time: the locations it finished no longer select.

The first unattended full-estate run asked each location for its whole
season in one request, ~63 weighted calls apiece, and was throttled after
nineteen minutes. That is the failure the budget exists to prevent.

**Scheduled hourly** at :30, as ``backfill_weather`` in ``schedule.py``,
behind the ``schedule_backfill_weather`` switch. It sweeps up a newly
minted location's history and any day a ``fetch_weather`` run missed.

**Through the weather service since SNOW-1275.** The sweep walks the
selected locations' stations (``SourceLocation``, matched on
``source_uuid``) and writes ``SourceWeather``, so the service holds the
history it serves; each row is applied into ``Weather`` as it is written.
A location the service holds no station for is not swept.

**The admin action is a different walk over the same writer.**
``LocationAdmin``'s "Backfill missing weather" fills a handful of selected
locations' whole windows inline; neither owns the logic — the service does
(``apps.ingest.public``), behind ``apps.weather.services.backfill``.

Usage:
    # Preview this hour's sweep — calls the API, writes nothing.
    uv run python manage.py backfill_weather

    # Persist one sweep (what the scheduler runs).
    uv run python manage.py backfill_weather --commit

    # One day, for four resorts, with no budget to speak of.
    uv run python manage.py backfill_weather --commit --floor 2026-04-10
        --until 2026-04-10 --resort verbier --resort zermatt

    # One location's whole window, spending up to 100 weighted calls.
    uv run python manage.py backfill_weather --commit --location 42 --budget 100
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import CommandError
from django.db.models import Q, QuerySet

from apps.core.command_iteration import iterate_rows, non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.ingest.public import SourceLocation
from apps.locations.models import Location
from apps.weather.services import backfill

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Sweep the missing Weather days, newest fortnight first, within a budget.

    Read-only by default; pass --commit to persist. Per-location failures
    are caught, logged and counted — they never abort the walk — and the
    command exits non-zero when any failed or the upstream throttled it.
    """

    help = (
        "Fill the missing Weather days for the active locations, newest "
        "fortnight first, within a budget of weighted Open-Meteo calls. "
        "Today is fetch_weather's row and is never touched. Read-only unless "
        "--commit is passed."
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
        parser.add_argument(
            "--floor",
            type=date.fromisoformat,
            default=None,
            help=(
                "Earliest day to fill (ISO date). Defaults to "
                "settings.WEATHER_BACKFILL_FLOOR."
            ),
        )
        parser.add_argument(
            "--until",
            type=date.fromisoformat,
            default=None,
            help=(
                "Last day to fill (ISO date), inclusive. Defaults to "
                "yesterday, and a later date is clamped to yesterday."
            ),
        )
        parser.add_argument(
            "--location",
            type=int,
            action="append",
            default=None,
            metavar="ID",
            help="Only this active location, by id. Repeat for several.",
        )
        parser.add_argument(
            "--resort",
            action="append",
            default=None,
            metavar="SLUG",
            help=(
                "Only the active locations of this resort — its linked "
                "places and its pin. Repeat for several."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=("Only the N newest of the selected locations. Defaults to no limit."),
        )
        parser.add_argument(
            "--budget",
            type=non_negative_float,
            default=None,
            help=(
                "Weighted Open-Meteo calls this run may spend. Defaults to "
                "one hourly sweep's share of what fetch_weather leaves of "
                "the free tier's daily quota."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=backfill.INTER_LOCATION_DELAY,
            help=(
                "Seconds to wait between requests, so a long walk does not "
                "read as abuse from one IP. 0 disables the throttle."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run one budgeted sweep over the selected locations."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        floor: date = options["floor"] or backfill.backfill_floor()
        yesterday = backfill.backfill_until()
        until: date = min(options["until"] or yesterday, yesterday)
        budget: float | None = options["budget"]
        if budget is None:
            budget = backfill.sweep_budget(SourceLocation.objects.live().count())

        locations = self._select(options)
        total = backfill.stations_for(locations).count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Sweeping {floor} → {until} over {total} station(s), "
                f"budget {budget:.0f} weighted call(s){flag_label}"
            )
        )
        logger.info(
            "backfill_weather started: floor=%s until=%s locations=%d "
            "budget=%.1f commit=%s",
            floor,
            until,
            total,
            budget,
            commit,
        )

        def on_block(start: date, end: date, count: int) -> None:
            """Head each block's countdown with its dates and its size."""
            if verbosity >= 1:
                self.stdout.write(
                    self.style.MIGRATE_LABEL(
                        f"{start} → {end}: {count} location(s) with a gap"
                    )
                )

        result = backfill.sweep(
            locations,
            floor=floor,
            until=until,
            commit=commit,
            budget=budget,
            delay=options["delay"],
            # Streamed, not materialised: iterate_rows orders -id and prints
            # a countdown line per location in the block (SNOW-602).
            stream=lambda queryset: iterate_rows(
                self,
                queryset,
                verbosity=verbosity,
                describe=lambda row: row.to_string(),
            ),
            on_block=on_block,
        )

        self._report_outcome(result, commit=commit, verbosity=verbosity)

        if result.stopped == "throttled":
            raise CommandError(
                "backfill_weather was throttled by Open-Meteo (429) after "
                f"{result.weight:.0f} weighted call(s); the next run resumes."
            )
        if result.failed > 0:
            raise CommandError(
                f"backfill_weather completed with {result.failed} location "
                f"failure(s). Check logs for details."
            )

    def _select(self, options: dict[str, Any]) -> QuerySet[Location]:
        """Return the active locations the options select.

        Args:
            options: The parsed command options.

        Returns:
            ``Location.objects.active()``, narrowed by ``--location``,
            ``--resort`` and ``--limit``.

        Raises:
            CommandError: A ``--resort`` slug matched no active location.

        """
        locations = Location.objects.active()
        if options["location"]:
            locations = locations.filter(pk__in=options["location"])
        if options["resort"]:
            by_resort = locations.filter(
                Q(resort_locations__resort__slug__in=options["resort"])
                | Q(pinned_resorts__slug__in=options["resort"])
            )
            if not by_resort.exists():
                raise CommandError(
                    f"No active location belongs to {', '.join(options['resort'])}."
                )
            locations = by_resort
        if options["limit"] is not None:
            # By id, not a queryset slice: the sweep re-filters this set per
            # block, and Django refuses to filter a sliced queryset.
            newest = list(
                locations.order_by("-id").values_list("id", flat=True)[
                    : options["limit"]
                ]
            )
            locations = Location.objects.filter(pk__in=newest)
        return locations

    def _report_outcome(
        self,
        result: backfill.SweepResult,
        *,
        commit: bool,
        verbosity: int,
    ) -> None:
        """Emit the post-run summary to stdout and the structured log.

        Args:
            result: The counters returned by ``backfill.sweep``.
            commit: Whether --commit was passed.
            verbosity: Django's ``--verbosity`` level.

        """
        if verbosity >= 1:
            if result.stopped == "budget":
                ending = "Stopped at the budget; the next run resumes this block."
            elif result.stopped == "throttled":
                ending = "Stopped by an Open-Meteo throttle (429)."
            elif not result.blocks:
                ending = "Nothing missing in the window."
            else:
                ending = "Every block in the window walked."
            summary = (
                f"{result.requests} request(s), {result.weight:.1f} weighted "
                f"call(s) over {result.locations} location-block(s); "
                f"{result.blocks_finished} of {len(result.blocks)} block(s) "
                f"finished, {result.unresolved} day(s) unresolved, "
                f"{result.failed} failed. {ending}"
            )
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {result.filled} day(s) written — {summary}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {summary} No data written. "
                        "Pass --commit to persist."
                    )
                )
        # Counts for the command run register (SNOW-1190).
        self.tally("locations", result.locations)
        self.tally("filled", result.filled)
        self.tally("unresolved", result.unresolved)
        self.tally("requests", result.requests)
        self.tally("weighted_calls", round(result.weight))
        self.tally("blocks_finished", result.blocks_finished)
        self.tally("failed", result.failed)
        logger.info(
            "backfill_weather finished: locations=%d filled=%d unresolved=%d "
            "requests=%d weight=%.1f blocks=%d finished=%d failed=%d "
            "stopped=%s commit=%s",
            result.locations,
            result.filled,
            result.unresolved,
            result.requests,
            result.weight,
            len(result.blocks),
            result.blocks_finished,
            result.failed,
            result.stopped or "-",
            commit,
        )
