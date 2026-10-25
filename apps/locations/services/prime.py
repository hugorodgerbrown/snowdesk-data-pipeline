"""
apps/locations/services/prime.py — a curated location's height and forecast, on save.

SNOW-1226. A location placed in the in-map editor (``/map/?edit=locations``)
or the admin used to wait for two unrelated things before a resort panel
could show it: ``fill_location_elevations``, which is run by hand, for its
height, and the next ``fetch_weather`` run — up to six hours away — for its
forecast. A summit added at 14:00 sat at "No forecast yet" with no height.

So saving one enqueues a single task that does both, in this order:

1. **Height first.** ``elevation_m`` is resolved from the coordinate when the
   row has none — a new pin, or a moved one, which the editor has just
   cleared. Order matters: ``fetch_weather_for_location`` downscales the
   forecast to ``elevation_m`` when it is set and falls back to the model
   cell's mean terrain when it is not, which for a summit is a forecast
   hundreds of metres too low and too warm.
2. **Then today's forecast**, through the same ``fetch_weather_for_location``
   the scheduled run uses — but only for a location in
   ``Location.objects.active()``, the set the scheduled run would pay for
   anyway, and only when it has no row for today or has just moved (today's
   row then describes where the pin used to be, and is rewritten in place,
   as today's row may be).

The same shape as ``apps.trips.services.meeting_weather`` (SNOW-1217): the
decisions are the worker's, so a task that waits in the queue sees the row
as it is when it runs; a failure is logged, never raised, and the scheduled
runs are the retry.

**Enqueued on commit.** Under ``ImmediateBackend`` (dev, test and staging)
the worker runs inline, so enqueueing inside a transaction would hold it
open across two upstream round trips — and the admin saves inside one.
``transaction.on_commit`` runs the enqueue straight away when no
transaction is open, so every caller can use it without knowing which case
it is in.
"""

from __future__ import annotations

import logging

import requests
from django.db import transaction
from django.utils import timezone
from django_tasks import task

from apps.locations.models import Location
from apps.locations.services.elevation import fetch_elevation
from apps.weather.models import Weather
from apps.weather.services.fetch import fetch_weather_for_location

logger = logging.getLogger(__name__)


def _resolve_elevation(location: Location) -> None:
    """Fill ``location.elevation_m`` from its coordinate, when it has none.

    Args:
        location: The location, updated in place and saved on success.

    """
    if location.elevation_m is not None:
        return
    try:
        elevation = fetch_elevation(location.latitude, location.longitude)
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        logger.warning(
            "location elevation lookup failed: location=%s: %s", location.pk, exc
        )
        return
    location.elevation_m = elevation
    location.save(update_fields=["elevation_m", "updated_at"])
    logger.info("location elevation resolved: location=%s", location.pk)


def _fetch_today(location: Location, *, moved: bool) -> None:
    """Fetch today's forecast for ``location``, when the scheduled run would.

    Args:
        location: The location to fetch for.
        moved: Whether the pin has just moved, which makes today's row, if
            any, a forecast for somewhere else.

    """
    if not Location.objects.active().filter(pk=location.pk).exists():
        return
    today = timezone.localdate()
    if not moved and Weather.objects.for_location(location).on_date(today).exists():
        return
    try:
        fetch_weather_for_location(location, today, commit=True)
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning(
            "location weather fetch failed: location=%s: %s", location.pk, exc
        )
        return
    logger.info("location weather fetched: location=%s", location.pk)


@task()
def _worker_prime_location(location_pk: int, moved: bool = False) -> None:
    """Background worker: resolve one location's height, then its forecast.

    A primary key rather than the row, re-loaded inside, so the task body is
    JSON-serialisable and a location deleted in the meantime is the expected
    race rather than a failure.

    Args:
        location_pk: Primary key of the ``Location``.
        moved: Whether the pin has just moved.

    """
    location = Location.objects.filter(pk=location_pk).first()
    if location is None:
        logger.info(
            "location pk=%s no longer exists — skipping elevation and weather",
            location_pk,
        )
        return
    _resolve_elevation(location)
    _fetch_today(location, moved=moved)


def enqueue_location_prime(location: Location, *, moved: bool = False) -> None:
    """Enqueue the height-then-forecast fill for ``location``, on commit.

    Safe to call on every save: a location that already has its height and
    today's row costs the worker two queries and no upstream call.

    Args:
        location: The location just created, moved or linked to a resort.
        moved: Whether its coordinate has just changed.

    """
    location_pk = location.pk
    transaction.on_commit(lambda: _worker_prime_location.enqueue(location_pk, moved))
