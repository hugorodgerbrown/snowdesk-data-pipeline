"""
apps/trips/services/trip_weather.py — the forecast for a trip's day.

SNOW-1217. Hugo, 2026-10-24: the forecast is a better guide than the
avalanche danger level on the Plan and trip pages, because the forecast
covers the week and a bulletin covers only today and tomorrow.

**Both pages borrow the nearest curated forecast** (Hugo, 2026-10-28).
Snowdesk decides where a forecast is fetched, because Snowdesk pays for it,
so neither a route's start nor a trip's meeting point buys a fetch of its
own. The Plan page reads the curated point nearest the route's start; the
trip page reads the one nearest the meeting point. Either is found at any
distance and named with its height and how far away it is
(``apps.weather.services.nearest``). Until then the trip page read its
meeting point's own forecast, fetched on save and four times a day while
the trip was ahead, and the Plan page gave up past 15 km.

Both answer in ``ForecastPanelDay`` — the weather pages' own per-day shape
(``apps.weather.services.weather_display``) — so a tile and a card show
the icon, the high, the low and the new snow the forecast page shows.

**The icon is the daytime one.** ``build_point_forecast_panel`` chooses a
day or night icon by projecting a wall-clock time onto each day. A plan is
for a day out, so the Plan page projects noon, and the trip page projects
the meeting time — both on the forecast point's own clock.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from django.utils import timezone

from apps.locations.models import Location
from apps.trips.models import Trip
from apps.weather.models import Weather
from apps.weather.services.nearest import (
    SAME_PLACE_KM,
    NearestForecast,
    nearest_forecast,
)
from apps.weather.services.weather_display import (
    ForecastPanelDay,
    build_point_forecast_panel,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TripWeather:
    """A forecast for a plan, and the curated point it is borrowed from.

    Attributes:
        location: The forecast point.
        label: How the point is named — its own name, its resort's or its
            region's.
        distance_km: How far the point is from what it stands for — the
            route's start, or the meeting point.
        days: Each forecast day by its date.

    """

    location: Location
    label: str
    distance_km: float
    days: dict[datetime.date, ForecastPanelDay] = field(default_factory=dict)

    @property
    def elevation_m(self) -> int | None:
        """Return the point's height, rounded, or None when unknown."""
        height = self.location.elevation_m
        return round(height) if height is not None else None

    @property
    def is_same_place(self) -> bool:
        """Return whether the point is, near enough, the place itself."""
        return self.distance_km < SAME_PLACE_KM

    def on(self, day: datetime.date) -> ForecastPanelDay | None:
        """Return the forecast for one day, or None outside the window."""
        return self.days.get(day)

    def to_string(self) -> str:
        """Return the point and its window, for logs and test failures."""
        return f"{self.label} ({len(self.days)} day(s))"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def _at_the_point(
    row: Weather, day: datetime.date, at: datetime.time
) -> datetime.datetime:
    """Return ``at`` on ``day`` as a wall-clock time AT the forecast point.

    A meeting time is the time on the meeting point's clock, and the
    day/night decision compares it with sunrise there. The forecast days
    keep the provider's sunrise strings with the point's own offset
    (``timezone=auto``), so the reference instant is built in that offset;
    the site's zone, UTC, would put a 06:00 Alpine meeting at 08:00 local.
    Falls back to the site's zone when no forecast day carries an offset.
    """
    zone: datetime.tzinfo = timezone.get_current_timezone()
    for entry in row.forecast or []:
        try:
            offset = datetime.datetime.fromisoformat(entry["sunrise"]).tzinfo
        except KeyError, TypeError, ValueError:
            continue
        if offset is not None:
            zone = offset
            break
    return datetime.datetime.combine(day, at, tzinfo=zone)


def _days(
    row: Weather, now: datetime.datetime
) -> dict[datetime.date, ForecastPanelDay]:
    """Return a row's forecast window keyed by day."""
    panel = build_point_forecast_panel(row, now)
    return {day["date"]: day for day in panel["days"]} if panel else {}


def _borrowed(
    nearest: NearestForecast, day: datetime.date, at: datetime.time
) -> TripWeather:
    """Wrap a borrowed row as a plan's forecast, its icons projected at ``at``."""
    row = nearest.row
    return TripWeather(
        location=nearest.location,
        label=nearest.label,
        distance_km=nearest.distance_km,
        days=_days(row, _at_the_point(row, day, at)),
    )


def nearest_weather(
    latitude: float, longitude: float, today: datetime.date
) -> TripWeather | None:
    """Return the nearest curated forecast to a route's start, with its week.

    Reads only points with a row for ``today`` — a point with no forecast
    is no help, however close — and only ``public()`` ones: a favourite is
    somebody's private pin, and naming it on a stranger's Plan page would
    leak it. No distance cap (Hugo, 2026-10-28): the page names the point
    and how far away it is, and that is the honesty the cap stood in for.

    Args:
        latitude: The route start's latitude.
        longitude: Its longitude.
        today: The site's current day, the row read.

    Returns:
        The nearest point and its forecast, or None when no curated point
        has a row today.

    """
    nearest = nearest_forecast(latitude, longitude, today)
    if nearest is None:
        return None
    return _borrowed(nearest, today, datetime.time(12))


def meeting_point_weather(trip: Trip, today: datetime.date) -> TripWeather | None:
    """Return the nearest curated forecast to a trip's meeting point.

    The meeting point buys no fetch of its own (Hugo, 2026-10-28), so the
    trip page borrows, as the Plan page does. The row read is today's while
    the trip is ahead, and once it has passed, the row of the trip's own
    day — the record of what the forecast said that morning, which is
    never rewritten.

    Args:
        trip: The trip, with ``meeting_point`` loaded.
        today: The site's current day.

    Returns:
        The borrowed forecast, or None when no curated point has a row for
        that day.

    """
    point = trip.meeting_point
    nearest = nearest_forecast(point.latitude, point.longitude, min(today, trip.date))
    if nearest is None:
        return None
    return _borrowed(nearest, trip.date, trip.start_time)
