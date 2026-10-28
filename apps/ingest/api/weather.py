"""
apps/ingest/api/weather.py — The v2 ``weather`` data feed, read from SourceWeather.

``serialise_source_weather`` turns one row into one wire item, the same
shape as v1's (``apps/data_feed/feeds.py``) with one difference that is the
reason for the new version: ``location`` is the weather service's id for
the place (``SourceLocation.uuid``, which a client holds as
``Location.source_uuid``), not the uuid of a row in production's own
``Location`` table (SNOW-1253). A client keys its apply on that, so it no
longer needs production's location ids at all.

``WEATHER_FEED`` is what ``/api/data/v2/weather/`` pages through: every row
of every place, tombstoned places included — a tombstone stops the fetch,
not the record of what was fetched.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.db.models import Prefetch, QuerySet

from apps.ingest.api.paging import Feed
from apps.ingest.models import WEATHER_COLUMNS, SourceLocation, SourceWeather

# The version this feed is served under — the /v2/ in its URL.
WEATHER_FEED_VERSION = 2


def _wire(value: Any) -> Any:
    """Return a column value as JSON: dates and datetimes as ISO 8601.

    Args:
        value: A column's value.

    Returns:
        The value, a date or datetime rendered as its ISO string.

    """
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def serialise_source_weather(row: SourceWeather) -> dict[str, Any]:
    """Return one place-day of weather, keyed by the place's service uuid.

    Args:
        row: A row with its ``location`` loaded (``uuid`` at least).

    Returns:
        The wire item.

    """
    return {
        "location": str(row.location.uuid),
        "observed_on": row.observed_on.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "fields": {name: _wire(getattr(row, name)) for name in WEATHER_COLUMNS},
    }


def _source_weather() -> QuerySet[SourceWeather]:
    """Return every stored row, with each place loaded slim."""
    return SourceWeather.objects.prefetch_related(
        Prefetch("location", queryset=SourceLocation.objects.only("pk", "uuid"))
    )


WEATHER_FEED = Feed(
    "weather",
    _source_weather,
    serialise_source_weather,
    version=WEATHER_FEED_VERSION,
    day_field="observed_on",
)
