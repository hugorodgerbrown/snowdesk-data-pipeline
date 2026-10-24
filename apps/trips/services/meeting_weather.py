"""
apps/trips/services/meeting_weather.py — fetching a new meeting point's forecast.

SNOW-1217. A trip's meeting point joins ``Location.objects.active()`` while
the trip is ahead, so ``fetch_weather`` refreshes it four times a day. That
alone would leave a trip saved at 09:00 with no forecast until the next
scheduled run, so saving a trip — or moving its meeting point — fetches the
point once, off the request, through the same
``fetch_weather_for_location`` the scheduled run uses.

**One call, and only when it buys something.** No call when the point
already has today's row (a trip edited twice in a morning) — unless the
pin moved, when today's row describes where the point used to be and is
rewritten in place, as today's row may be — and none when
the trip's day is outside the forecast window: a past day has nothing to
forecast, and a day more than ``FORECAST_DAYS`` out is the scheduled run's
to reach once it comes into range.

**A failure is logged, never raised.** The trip is saved either way, and
the scheduled run is the retry.

Like ``slope.py`` beside it, the enqueue must be made OUTSIDE any open
transaction: under ``ImmediateBackend`` (dev, test and staging) the worker
runs inline, and an upstream round trip must not hold a row lock.
"""

from __future__ import annotations

import datetime
import logging

import requests
from django.utils import timezone
from django_tasks import task

from apps.trips.models import Trip
from apps.weather.models import Weather
from apps.weather.services.fetch import FORECAST_DAYS, fetch_weather_for_location

logger = logging.getLogger(__name__)


def _in_window(day: datetime.date, today: datetime.date) -> bool:
    """Return whether ``day`` falls inside a forecast fetched today."""
    return today <= day < today + datetime.timedelta(days=FORECAST_DAYS)


@task()
def _worker_fetch_meeting_weather(trip_pk: int, moved: bool = False) -> None:
    """Background worker: fetch one trip's meeting point forecast.

    A primary key rather than the row, re-loaded inside, so the task body
    is JSON-serialisable and a trip deleted in the meantime is the expected
    race rather than a failure.

    Args:
        trip_pk: Primary key of the ``Trip``.
        moved: Whether the meeting point has just moved, which makes
            today's row, if any, a forecast for somewhere else.

    """
    trip = Trip.objects.select_related("meeting_point").filter(pk=trip_pk).first()
    if trip is None:
        logger.info("trip pk=%s no longer exists — skipping weather fetch", trip_pk)
        return
    today = timezone.localdate()
    location = trip.meeting_point
    if not _in_window(trip.date, today):
        return
    if not moved and Weather.objects.for_location(location).on_date(today).exists():
        return
    try:
        fetch_weather_for_location(location, today, commit=True)
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning(
            "meeting point weather fetch failed: trip=%s location=%s: %s",
            trip.uuid,
            location.pk,
            exc,
        )
        return
    logger.info(
        "meeting point weather fetched: trip=%s location=%s", trip.uuid, location.pk
    )


def enqueue_meeting_weather(trip: Trip, *, moved: bool = False) -> None:
    """Enqueue a forecast fetch for ``trip``'s meeting point, when it is needed.

    **Call this outside any open transaction** — see the module note.
    The checks that decide whether a call is made are the worker's, so a
    task that waits in the queue still sees the trip as it is when it runs.

    Args:
        trip: The trip just saved.
        moved: Whether its meeting point has just moved.

    """
    if not _in_window(trip.date, timezone.localdate()):
        return
    _worker_fetch_meeting_weather.enqueue(trip.pk, moved)
