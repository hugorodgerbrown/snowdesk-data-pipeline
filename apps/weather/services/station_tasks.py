"""
apps/weather/services/station_tasks.py — the weather commands' row tasks (SNOW-1399).

``fetch_weather --commit`` and ``backfill_weather --commit`` only enqueue,
through ``apps.core.command_tasks.enqueue_each``, one task per unit:

  fetch_station_task(station_pk, delay_s)
      One station's live forecast, today to six days ahead, written to
      ``SourceWeather`` and applied into ``Weather`` in one transaction.
      Today is read when the task runs, not when it was enqueued, so a
      task that waited past midnight fetches the day it runs on.

  backfill_unit_task(station_pk, start, end, delay_s)
      One station's gap in one fortnight block — one unit of
      ``plan_sweep`` — filled through ``sweep_block`` and applied as it is
      written. A unit filled since it was planned costs no request.

Both call Open-Meteo, so both are registered with ``calls_outside`` and
take their turn on the shared ``OPEN_METEO`` slot (with the elevation
lookups) before they call out. A 429, or an outage — a timeout, a refused
connection, a 5xx (``upstream_unavailable``) — opens the ``OPEN_METEO``
breaker for ``OPEN_METEO_BACK_OFF_S`` and fails the task: every task after
it, of either kind, returns without asking and leaves its unit for the
next run. A failure that is the station's own (a 4xx, a bad payload, a
write) fails its task alone and leaves the breaker shut.
"""

from __future__ import annotations

import logging
from datetime import date

import requests
from django.db import transaction
from django.utils import timezone
from django_tasks import task

from apps.core.command_tasks import (
    BULK_TASK_PRIORITY,
    back_off,
    backing_off,
    calls_outside,
    wait_for_slot,
)
from apps.ingest.public import (
    SourceLocation,
    fetch_source_location,
    sweep_block,
    upstream_unavailable,
)
from apps.locations.services.elevation import OPEN_METEO, OPEN_METEO_BACK_OFF_S
from apps.weather.services.backfill import apply_backfilled_row
from apps.weather.services.reference import apply_source_weather

logger = logging.getLogger(__name__)


class OpenMeteoThrottled(RuntimeError):
    """Open-Meteo refused (429) or is down: the breaker is open, the task fails."""


def _station(station_pk: int, kind: str) -> SourceLocation | None:
    """Return the live station, or None when the breaker is open or it is gone.

    Args:
        station_pk: The ``SourceLocation``'s primary key.
        kind: What the task does, for the log line.

    """
    if backing_off(OPEN_METEO):
        logger.info(
            "%s: backing off Open-Meteo; station pk=%s left for the next run",
            kind,
            station_pk,
        )
        return None
    return SourceLocation.objects.live().filter(pk=station_pk).first()


def _throttled(station_pk: int, kind: str, why: str) -> OpenMeteoThrottled:
    """Open the breaker and return the error the task raises.

    Args:
        station_pk: The station whose request was refused or met the outage.
        kind: What the task does, for the message.
        why: ``"throttled"`` or ``"unavailable"``.

    """
    back_off(OPEN_METEO, OPEN_METEO_BACK_OFF_S)
    return OpenMeteoThrottled(f"{kind}: Open-Meteo {why} at station pk={station_pk}")


@task(priority=BULK_TASK_PRIORITY)
def fetch_station_task(station_pk: int, delay_s: float = 0.0) -> dict[str, int] | None:
    """Background worker: fetch one station's live forecast and apply it.

    Args:
        station_pk: The ``SourceLocation``'s primary key.
        delay_s: Seconds between one Open-Meteo call's start and the next.

    Returns:
        ``created`` and ``updated`` row counts, kept on the task result, or
        None when the station is gone or the breaker is open.

    Raises:
        OpenMeteoThrottled: Open-Meteo answered 429 or is down; the breaker
            is open.

    """
    station = _station(station_pk, "fetch_weather")
    if station is None:
        return None
    wait_for_slot(OPEN_METEO, delay_s)
    counts = {"created": 0, "updated": 0}
    try:
        # The service's week and this host's copy commit together, so a
        # failed apply leaves neither half written.
        with transaction.atomic():
            for row, created in (
                fetch_source_location(station, timezone.localdate(), commit=True) or []
            ):
                apply_source_weather(row)
                counts["created" if created else "updated"] += 1
    except requests.RequestException as exc:
        if (
            isinstance(exc, requests.HTTPError)
            and exc.response is not None
            and exc.response.status_code == 429
        ):
            raise _throttled(station_pk, "fetch_weather", "throttled") from exc
        if upstream_unavailable(exc):
            raise _throttled(station_pk, "fetch_weather", "unavailable") from exc
        raise
    return counts


@task(priority=BULK_TASK_PRIORITY)
def backfill_unit_task(
    station_pk: int, start: str, end: str, delay_s: float = 0.0
) -> int | None:
    """Background worker: fill one station's gap in one fortnight block.

    Args:
        station_pk: The ``SourceLocation``'s primary key.
        start: Block start, as an ISO date (task arguments are JSON).
        end: Block end, inclusive, as an ISO date.
        delay_s: Seconds between one Open-Meteo call's start and the next.

    Returns:
        The days written, kept on the task result, or None when the station
        is gone or the breaker is open.

    Raises:
        OpenMeteoThrottled: Open-Meteo answered 429 or is down; the breaker
            is open.
        RuntimeError: The request or the write failed for another reason.

    """
    station = _station(station_pk, "backfill_weather")
    if station is None:
        return None
    result = sweep_block(
        station,
        date.fromisoformat(start),
        date.fromisoformat(end),
        before_request=lambda: wait_for_slot(OPEN_METEO, delay_s),
        on_row=apply_backfilled_row,
    )
    if result.stopped in ("throttled", "unavailable"):
        raise _throttled(station_pk, "backfill_weather", result.stopped)
    if result.failed:
        raise RuntimeError(
            f"backfill_weather: station pk={station_pk} {start}..{end} failed"
        )
    return result.filled


# Each task calls Open-Meteo, so enqueue_each refuses it unpaced.
calls_outside(fetch_station_task)
calls_outside(backfill_unit_task)
