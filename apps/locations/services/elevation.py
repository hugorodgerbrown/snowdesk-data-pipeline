"""
apps/locations/services/elevation.py — one Location's elevation, as a task (SNOW-1398).

``fill_location_elevations --commit`` enqueues ``fill_elevation_task`` once
per candidate through ``apps.core.command_tasks.enqueue_each``. Each task
re-reads its row and returns when it no longer needs an elevation (unless
the run was ``--force``), takes its turn on the ``OPEN_METEO`` slot, asks
Open-Meteo's elevation endpoint for the coordinate, and writes the one
column.

A failed lookup opens the ``OPEN_METEO`` breaker for
``OPEN_METEO_BACK_OFF_S`` and fails the task: ``fetch_elevation`` raises on
an HTTP error, which is the upstream rather than the coordinate, so the
tasks behind it leave their rows as candidates for the next run.
"""

from __future__ import annotations

import logging

from django_tasks import task

from apps.core.command_tasks import (
    BULK_TASK_PRIORITY,
    back_off,
    backing_off,
    calls_outside,
    wait_for_slot,
)
from apps.ingest.public import fetch_elevation
from apps.locations.models import Location

logger = logging.getLogger(__name__)

# The slot and breaker name for Open-Meteo, and how long a failed lookup
# keeps every later task off it.
OPEN_METEO = "open-meteo"
OPEN_METEO_BACK_OFF_S = 300.0


@task(priority=BULK_TASK_PRIORITY)
def fill_elevation_task(
    location_pk: int, delay_s: float = 0.0, force: bool = False
) -> float | None:
    """Background worker: resolve one location's elevation.

    Args:
        location_pk: Primary key of the ``Location`` to resolve.
        delay_s: Seconds between one lookup's start and the next.
        force: Re-resolve a location that already carries an elevation.

    Returns:
        The elevation in metres, kept on the task result, or None when the
        location is gone or resolved already, or the breaker is open.

    Raises:
        Exception: Whatever ``fetch_elevation`` raised, after opening the
            breaker.

    """
    if backing_off(OPEN_METEO):
        logger.info(
            "elevation: backing off Open-Meteo; location id=%s left for the next run",
            location_pk,
        )
        return None
    candidates = Location.objects.all() if force else Location.objects.unresolved()
    location = candidates.filter(pk=location_pk).first()
    if location is None:
        return None
    wait_for_slot(OPEN_METEO, delay_s)
    try:
        elevation = fetch_elevation(location.latitude, location.longitude)
    except Exception:
        logger.exception("elevation: lookup failed for location id=%s", location_pk)
        back_off(OPEN_METEO, OPEN_METEO_BACK_OFF_S)
        raise
    location.elevation_m = elevation
    location.save(update_fields=["elevation_m"])
    return elevation


# Each task calls Open-Meteo, so enqueue_each refuses it unpaced.
calls_outside(fill_elevation_task)
