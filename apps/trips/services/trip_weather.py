"""
apps/trips/services/trip_weather.py — the forecast for a trip's day.

SNOW-1217. Hugo, 2026-10-24: the forecast is a better guide than the
avalanche danger level on the Plan and trip pages, because the forecast
covers the week and a bulletin covers only today and tomorrow. The two
pages read the forecast from two different places, and that was chosen on
purpose:

- **The Plan page reads the NEAREST forecast point.** A route has no
  ``Location`` of its own and nothing fetches its start, so the Plan page
  borrows the forecast of the closest public location (a resort point or
  a region centroid) within ``NEAREST_RADIUS_KM`` of the start, and says
  which one it is and how far away. No upstream call is made for a route
  being planned.
- **The trip page reads the MEETING POINT itself.** A trip's meeting point
  is a ``Location`` already, and ``Location.objects.active()`` includes it
  while the trip is ahead, so ``fetch_weather`` keeps it current; saving a
  trip fetches it once straight away (``meeting_weather.py``).

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
import math
from dataclasses import dataclass, field

from django.utils import timezone

from apps.core.geo import haversine_km
from apps.locations.models import Location
from apps.trips.models import Trip
from apps.weather.models import Weather
from apps.weather.services.weather_display import (
    ForecastPanelDay,
    build_point_forecast_panel,
)

logger = logging.getLogger(__name__)

# How far from a route's start a forecast point may be and still stand for
# it. Far enough to reach the resort village or region centre a tour
# usually starts near; near enough that it is the same valley's weather.
NEAREST_RADIUS_KM = 15.0

# Kilometres per degree of latitude, for the search box around the start.
_KM_PER_DEGREE = 111.0


@dataclass(frozen=True)
class TripWeather:
    """A forecast for a plan, and where it is from.

    Attributes:
        location: The forecast point.
        label: How the point is named — its own name, its resort's or its
            region's; "" for the meeting point, which the page names.
        distance_km: How far the point is from what it stands for; None
            when it IS the place (the meeting point).
        days: Each forecast day by its date.

    """

    location: Location
    label: str
    distance_km: float | None
    days: dict[datetime.date, ForecastPanelDay] = field(default_factory=dict)

    @property
    def elevation_m(self) -> int | None:
        """Return the point's height, rounded, or None when unknown."""
        height = self.location.elevation_m
        return round(height) if height is not None else None

    def on(self, day: datetime.date) -> ForecastPanelDay | None:
        """Return the forecast for one day, or None outside the window."""
        return self.days.get(day)

    def to_string(self) -> str:
        """Return the point and its window, for logs and test failures."""
        where = self.label or "meeting point"
        return f"{where} ({len(self.days)} day(s))"

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


def _label(location: Location) -> str:
    """Name a public location the way its forecast page heads it.

    Its own name, else its resort's (the resort it pins, or one it is
    linked to), else its region's — the precedence
    ``apps.public.views.location_weather`` uses, because most public
    locations are unnamed region centroids. Three small queries at most.
    """
    if location.name:
        return location.name
    pinned = location.pinned_resorts.first()
    if pinned is not None:
        return pinned.name
    link = location.resort_locations.select_related("resort").first()
    if link is not None:
        return link.resort.name
    region = location.micro_regions.first()
    return region.name if region is not None else ""


def nearest_weather(
    latitude: float, longitude: float, today: datetime.date
) -> TripWeather | None:
    """Return the nearest public forecast point to a place, with its week.

    Reads only locations with a row for ``today`` — a point with no
    forecast is no help, however close — and only ``public()`` ones: a
    favourite is somebody's private pin, and naming it on a stranger's
    Plan page would leak it. A search box around the place keeps the
    candidates to a handful before the exact distances are taken.

    Args:
        latitude: The place's latitude, e.g. a route's start.
        longitude: Its longitude.
        today: The site's current day, the row read.

    Returns:
        The nearest point within ``NEAREST_RADIUS_KM`` and its forecast,
        or None when there is none.

    """
    lat_span = NEAREST_RADIUS_KM / _KM_PER_DEGREE
    lon_span = lat_span / max(math.cos(math.radians(latitude)), 0.01)
    rows = (
        Weather.objects.on_date(today)
        .filter(
            location__in=Location.objects.public(),
            location__latitude__range=(latitude - lat_span, latitude + lat_span),
            location__longitude__range=(longitude - lon_span, longitude + lon_span),
        )
        .select_related("location")
    )
    best: tuple[float, Weather] | None = None
    for row in rows:
        distance = haversine_km(
            latitude, longitude, row.location.latitude, row.location.longitude
        )
        if distance <= NEAREST_RADIUS_KM and (best is None or distance < best[0]):
            best = (distance, row)
    if best is None:
        return None
    distance, row = best
    return TripWeather(
        location=row.location,
        label=_label(row.location),
        distance_km=distance,
        days=_days(row, _at_the_point(row, today, datetime.time(12))),
    )


def meeting_point_weather(trip: Trip, today: datetime.date) -> TripWeather | None:
    """Return the forecast at a trip's meeting point, as last known.

    The newest row on or before the trip's day: today's while the trip is
    ahead, and once it has passed, the row of the day itself — the record
    of what the forecast said that morning, which is never rewritten.

    Args:
        trip: The trip, with ``meeting_point`` loaded.
        today: The site's current day.

    Returns:
        The meeting point's forecast, or None when nothing has been
        fetched for it yet.

    """
    row = (
        Weather.objects.for_location(trip.meeting_point)
        .filter(observed_on__lte=min(today, trip.date))
        .order_by("-observed_on")
        .first()
    )
    if row is None:
        return None
    meeting = _at_the_point(row, trip.date, trip.start_time)
    return TripWeather(
        location=trip.meeting_point,
        label="",
        distance_km=None,
        days=_days(row, meeting),
    )
