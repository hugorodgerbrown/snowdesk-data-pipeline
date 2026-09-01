"""
apps/weather/management/commands/backfill_weather.py — The backfill_weather command.

Fills the **missing** days for every active location, from
``settings.WEATHER_BACKFILL_FLOOR`` up to yesterday, against the Open-Meteo
historical forecast endpoint (SNOW-731).

The complement of ``fetch_weather``, which writes today and only today. A
day is recorded once, on the day it is current, and that record then stands
— so this command is not a re-fetch. It asks only for the days a location
has no row for, and a location with no gaps costs no request at all.

**The same service the admin action uses.** ``LocationAdmin``'s "Backfill
missing weather" action is the surface a curator reaches for when they spot
a gap in the changelist; this command is the same operation without a
request timeout over it, for filling the estate rather than a handful of
rows. Neither owns the logic — ``apps.weather.services.backfill`` does.

Not scheduled. History does not accumulate gaps on its own: a gap appears
when a location is minted, or when ``fetch_weather`` failed on a day, and
both are things an operator notices in the changelist's coverage column.

Usage:
    # Preview — calls the API for every gap, writes nothing.
    uv run python manage.py backfill_weather

    # Persist, for the whole active estate.
    uv run python manage.py backfill_weather --commit

    # A cautious first batch, with a slower throttle.
    uv run python manage.py backfill_weather --commit --limit 10 --delay 5

    # A narrower window than the configured floor.
    uv run python manage.py backfill_weather --commit --floor 2026-01-01
"""

from __future__ import annotations

import itertools
import logging
from argparse import ArgumentParser
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.command_iteration import iterate_rows, non_negative_float
from apps.locations.models import Location
from apps.weather.services import backfill

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Backfill the missing Weather days for every active location.

    Read-only by default; pass --commit to persist. Per-location failures
    are caught, logged and counted — they never abort the walk — and the
    command exits non-zero when any failed.
    """

    help = (
        "Fill the missing Weather days for every active location, from the "
        "configured floor up to yesterday. Today is fetch_weather's row and "
        "is never touched. Read-only unless --commit is passed."
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
                "Earliest day to request (ISO date). Defaults to "
                "settings.WEATHER_BACKFILL_FLOOR."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Stop after this many locations, for staging the estate in "
                "batches. Defaults to no limit."
            ),
        )
        parser.add_argument(
            "--delay",
            type=non_negative_float,
            default=backfill.INTER_LOCATION_DELAY,
            help=(
                "Seconds to wait between locations, so a long walk does not "
                "read as abuse from one IP. 0 disables the throttle."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Backfill the missing days for every active location."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        floor: date = options["floor"] or backfill.backfill_floor()
        limit: int | None = options["limit"]
        delay: float = options["delay"]

        until = backfill.backfill_until()
        candidates = Location.objects.active()
        total = candidates.count()
        if limit is not None:
            total = min(total, limit)

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Backfilling {floor} → {until} for {total} active "
                f"location(s){flag_label}"
            )
        )
        logger.info(
            "backfill_weather started: floor=%s until=%s locations=%d commit=%s",
            floor,
            until,
            total,
            commit,
        )

        # Streamed, not materialised: the estate grows with every favourite
        # and every region EAWS adds. iterate_rows orders -id and prints a
        # countdown line per location (SNOW-602).
        rows = iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row}",
        )
        if limit is not None:
            # islice, not a queryset slice: iterate_rows re-orders by -id,
            # and Django refuses to re-order a sliced queryset.
            rows = itertools.islice(rows, limit)

        counts = backfill.backfill_locations(
            rows,
            floor=floor,
            until=until,
            commit=commit,
            delay=delay,
        )

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_weather completed with {counts['failed']} location "
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
            counts: The counters returned by ``backfill_locations``.
            commit: Whether --commit was passed.
            verbosity: Django's ``--verbosity`` level.

        """
        if verbosity >= 1:
            summary = (
                f"{counts['requests']} request(s) over "
                f"{counts['locations']} location(s); "
                f"{counts['already_present']} day(s) already present, "
                f"{counts['unresolved']} unresolved, "
                f"{counts['failed']} location(s) failed."
            )
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['filled']} day(s) written — {summary}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {summary} No data written. "
                        "Pass --commit to persist."
                    )
                )
        logger.info(
            "backfill_weather finished: locations=%d filled=%d already_present=%d "
            "unresolved=%d requests=%d failed=%d commit=%s",
            counts["locations"],
            counts["filled"],
            counts["already_present"],
            counts["unresolved"],
            counts["requests"],
            counts["failed"],
            commit,
        )
