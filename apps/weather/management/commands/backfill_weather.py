"""
apps/weather/management/commands/backfill_weather.py — The backfill_weather command.

Fills the **missing** days for the active locations, from
``settings.WEATHER_BACKFILL_FLOOR`` up to yesterday, against the Open-Meteo
historical forecast endpoint (SNOW-731).

The complement of ``fetch_weather``, which writes today and only today. A
day is recorded once, on the day it is current, and that record then stands
— so this command is not a re-fetch. It asks only for the days a location
has no row for, and a location with no gaps costs no request at all.

**A budgeted sweep, newest fortnight first (SNOW-1245), planned here and
filled on the task runner (SNOW-1399).** The window is cut into
fourteen-day blocks — the unit Open-Meteo charges one weighted call for —
and planned newest first (``plan_sweep``): each block across every selected
location before the next, one unit (one request) per location per block.
The plan stops before the unit that would take it past ``--budget``
weighted calls, and one ``backfill_unit_task`` per unit is enqueued through
``enqueue_each``. The default budget is this hour's share of what
``fetch_weather`` leaves of the free tier's daily quota
(``backfill.sweep_budget``), so a bare ``--commit`` run is exactly what the
scheduler fires every hour, and an operator who wants more passes a bigger
number. A unit whose task has not run yet is planned again next hour,
found already queued and not enqueued twice — and it counts against that
hour's budget, so a stalled worker never lets the queue grow past one
hour's worth. A 429 opens the ``open-meteo`` breaker for five minutes
rather than stopping a loop: the tasks behind it return without asking,
and their units stay gaps for the next plan.

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
    # Preview this hour's plan — asks the API nothing, writes nothing.
    uv run python manage.py backfill_weather

    # Enqueue one sweep (what the scheduler runs).
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

from apps.core.command_iteration import non_negative_float
from apps.core.command_register import RegisteredCommand
from apps.core.command_tasks import enqueue_each
from apps.ingest.public import SourceLocation
from apps.locations.models import Location
from apps.weather.services import backfill
from apps.weather.services.station_tasks import backfill_unit_task

logger = logging.getLogger(__name__)


class Command(RegisteredCommand):
    """Enqueue the missing Weather days, newest fortnight first, within a budget.

    Read-only by default; pass --commit to enqueue. Exits non-zero when a
    task run inline failed or was throttled.
    """

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

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
                "Enqueue one task per planned station-block. Without this "
                "flag the command plans and counts, and asks nothing."
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
                "Seconds between one request's start and the next, so a long "
                "walk does not read as abuse from one IP. 0 disables it."
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

        # Days the service already holds but this instance lacks cost no
        # request: copy them across before the plan looks for gaps.
        replayed = backfill.replay_held(
            locations, floor=floor, until=until, commit=commit
        )
        if verbosity >= 1 and replayed:
            self.stdout.write(
                f"{replayed} held day(s) copied from the weather service"
                + ("" if commit else " (read-only: counted, not written)")
            )
        self.tally("replayed", replayed)

        # Bounded by the budget, so a list: enqueue_each counts it down.
        units = list(
            backfill.plan_sweep(
                backfill.stations_for(locations),
                floor=floor,
                until=until,
                budget=budget,
            )
        )
        weight = sum(unit.weight for unit in units)
        delay: float = options["delay"]
        counts = enqueue_each(
            self,
            units,
            backfill_unit_task,
            args=lambda unit: (
                unit.location_id,
                unit.start.isoformat(),
                unit.end.isoformat(),
                delay,
            ),
            describe=lambda unit: f"{unit.label} {unit.start} → {unit.end}",
            commit=commit,
            verbosity=verbosity,
            pace=delay,
        )
        # Counts for the command run register (SNOW-1190).
        self.tally("weighted_calls", round(weight))
        logger.info(
            "backfill_weather finished: units=%d weight=%.1f enqueued=%d "
            "already_queued=%d failed=%d commit=%s",
            len(units),
            weight,
            counts.enqueued,
            counts.already_queued,
            counts.failed,
            commit,
        )
        if verbosity >= 1:
            verb = "enqueued" if commit else "would be enqueued"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {len(units)} station-block(s) planned, "
                    f"{weight:.1f} weighted call(s): {counts.enqueued} {verb}, "
                    f"{counts.already_queued} already queued, "
                    f"{counts.failed} failed."
                )
            )
        if counts.failed:
            raise CommandError(
                f"backfill_weather completed with {counts.failed} failed "
                f"station-block(s) (a 429 among them opens the breaker); "
                f"the next run resumes."
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
