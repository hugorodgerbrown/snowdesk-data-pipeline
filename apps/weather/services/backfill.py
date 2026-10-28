"""
apps/weather/services/backfill.py — The app's side of the weather backfill (SNOW-1275).

The backfill itself — the window, the fortnight sweep, the budget, the 429
stop, "never today" — is the weather service's since SNOW-1275
(``apps.ingest.public``): it walks ``SourceLocation`` rows and writes
``SourceWeather``, so the history is the service's and
``/api/data/v2/weather/`` serves it. This module is what the app does
around it, the backfill's counterpart of ``fetch_weather``'s mirror and
apply:

  stations_for(locations)
      The live stations of the given ``Location`` rows, through
      ``source_uuid``. A location the service does not hold — not yet
      mirrored, or a user's own point — has none, and is not backfilled.

  apply_backfilled_row(row)
      Copy one backfilled row into ``Weather``. A past day this instance
      already holds is kept: its account of the day stands, as
      ``upsert_weather`` insists, and the service's copy simply joins it.

  replay_held(locations, ...)
      Apply the service's rows for days this instance still lacks. The
      service skips a day it already holds, so a row whose apply failed
      once (or that landed before the app's mirror) is never re-fetched;
      this copies it across without spending an upstream call.

  backfill_locations(locations, ...) / sweep(locations, ...)
      The service's walks over ``stations_for(locations)``, each written
      row applied as it is written. Both replay the held rows first.

The window helpers the admin reads (``backfill_floor``, ``backfill_until``,
``expected_days``) and the constants are re-exported, so callers keep one
import.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from django.db.models import Exists, OuterRef, QuerySet

from apps.ingest.public import (
    ADMIN_MAX_LOCATIONS,
    INTER_LOCATION_DELAY,
    BackfillResult,
    SourceLocation,
    SourceWeather,
    SweepResult,
    backfill_floor,
    backfill_locations as service_backfill_locations,
    backfill_until,
    expected_days,
    request_weight,
    sweep as service_sweep,
    sweep_budget,
)
from apps.locations.models import Location
from apps.weather.exceptions import ImmutableWeatherRowError
from apps.weather.models import Weather
from apps.weather.services.reference import apply_source_weather

logger = logging.getLogger(__name__)

__all__ = [
    "ADMIN_MAX_LOCATIONS",
    "INTER_LOCATION_DELAY",
    "BackfillResult",
    "SweepResult",
    "apply_backfilled_row",
    "backfill_floor",
    "backfill_locations",
    "backfill_until",
    "expected_days",
    "replay_held",
    "request_weight",
    "stations_for",
    "sweep",
    "sweep_budget",
]


def stations_for(locations: QuerySet[Location]) -> QuerySet[SourceLocation]:
    """Return the live stations of ``locations``, matched on ``source_uuid``.

    Args:
        locations: The locations to fill.

    Returns:
        A queryset of ``SourceLocation`` — a subquery, not a list, so the
        sweep can re-filter it per block.

    """
    return SourceLocation.objects.live().filter(
        uuid__in=locations.filter(source_uuid__isnull=False).values("source_uuid")
    )


def apply_backfilled_row(row: SourceWeather) -> None:
    """Copy one backfilled row into ``Weather``, keeping a day already held.

    Args:
        row: The row the service just wrote.

    """
    try:
        apply_source_weather(row)
    except ImmutableWeatherRowError:
        # This instance recorded the day before the service did — history
        # from before SNOW-1275, or a row copy_weather_to_service has not
        # reached. Its own account stands.
        logger.debug(
            "apply_backfilled_row: %s %s already held; kept",
            row.location.uuid,
            row.observed_on,
        )


def replay_held(
    locations: QuerySet[Location],
    *,
    floor: date | None = None,
    until: date | None = None,
    commit: bool,
) -> int:
    """Apply the service's rows for days the app's ``Weather`` lacks.

    The service's walk skips a day ``SourceWeather`` already holds, so it
    never re-fetches one whose apply into ``Weather`` failed, or that it
    wrote before this location was mirrored here. Such a day is copied
    across from the service's row — no upstream call, no budget spent.

    Args:
        locations: The locations to fill.
        floor: Window start; defaults to ``backfill_floor()``.
        until: Window end; defaults to yesterday.
        commit: When False the rows are counted, not applied.

    Returns:
        How many held rows the app lacked (applied, when ``commit``).

    """
    floor = floor or backfill_floor()
    until = until or backfill_until()
    missing = (
        SourceWeather.objects.filter(
            location__in=stations_for(locations),
            observed_on__gte=floor,
            observed_on__lte=until,
        )
        .exclude(
            Exists(
                Weather.objects.filter(
                    location__source_uuid=OuterRef("location__uuid"),
                    observed_on=OuterRef("observed_on"),
                )
            )
        )
        .select_related("location")
    )
    if not commit:
        return missing.count()
    replayed = 0
    for row in missing.iterator():
        apply_backfilled_row(row)
        replayed += 1
    return replayed


def backfill_locations(
    locations: Iterable[Location],
    *,
    floor: date | None = None,
    until: date | None = None,
    commit: bool,
    base_url: str | None = None,
    delay: float | None = None,
    on_location: Callable[[SourceLocation], None] | None = None,
    on_result: Callable[[SourceLocation, BackfillResult], None] | None = None,
) -> dict[str, int]:
    """Fill each location's whole window through the service, then apply it.

    Args:
        locations: The locations to fill.
        floor: Window start; defaults to ``backfill_floor()``.
        until: Window end; defaults to yesterday.
        commit: When False the requests still execute but nothing is written.
        base_url: Overrides the configured historical host.
        delay: Seconds between stations; defaults to ``INTER_LOCATION_DELAY``.
        on_location: Called once per station before its requests.
        on_result: Called once per station with its result.

    Returns:
        The service walk's counters, plus ``unheld``: locations the service
        holds no live station for, which were not backfilled; and
        ``replayed``: held days copied across without a request.

    """
    locations = list(locations)
    replayed = replay_held(
        Location.objects.filter(pk__in=[location.pk for location in locations]),
        floor=floor,
        until=until,
        commit=commit,
    )
    wanted = [location.source_uuid for location in locations]
    by_uuid = {
        station.uuid: station
        for station in SourceLocation.objects.live().filter(
            uuid__in=[u for u in wanted if u is not None]
        )
    }
    # In the caller's order, so a capped admin batch walks what it shows.
    stations = [by_uuid[u] for u in wanted if u in by_uuid]
    counts = service_backfill_locations(
        stations,
        floor=floor,
        until=until,
        commit=commit,
        base_url=base_url,
        delay=delay,
        on_location=on_location,
        on_result=on_result,
        on_row=apply_backfilled_row,
    )
    counts["unheld"] = len(wanted) - len(stations)
    counts["replayed"] = replayed
    return counts


def sweep(
    locations: QuerySet[Location],
    *,
    floor: date | None = None,
    until: date | None = None,
    commit: bool,
    budget: float | None,
    base_url: str | None = None,
    delay: float | None = None,
    stream: Callable[[QuerySet[SourceLocation]], Iterable[SourceLocation]]
    | None = None,
    on_block: Callable[[date, date, int], None] | None = None,
    **kwargs: Any,
) -> SweepResult:
    """Run one budgeted sweep over the stations of ``locations``.

    Args:
        locations: The locations to fill.
        floor: Window start; defaults to ``backfill_floor()``.
        until: Window end; defaults to yesterday.
        commit: When False the requests still execute but nothing is written.
        budget: Weighted calls the run may spend; None for no limit.
        base_url: Overrides the configured historical host.
        delay: Seconds between requests.
        stream: Turns a block's stations into an iterable — the command's
            countdown.
        on_block: Called as each block with a gap begins.
        **kwargs: Passed through to the service's ``sweep`` (``sleep``).

    Returns:
        The run's counters.

    """
    return service_sweep(
        stations_for(locations),
        floor=floor,
        until=until,
        commit=commit,
        budget=budget,
        base_url=base_url,
        delay=delay,
        stream=stream,
        on_block=on_block,
        on_row=apply_backfilled_row,
        **kwargs,
    )
