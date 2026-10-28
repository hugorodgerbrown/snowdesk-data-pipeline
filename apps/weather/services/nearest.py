"""
apps/weather/services/nearest.py — the curated forecast a place borrows.

Hugo, 2026-10-28: Snowdesk decides where a forecast is fetched, because
Snowdesk pays for it. Only the curated estate (``Location.objects.public()``:
resort points and region centroids) is fetched. Everything else — a saved
pin, a trip's meeting point, a route's start — borrows the nearest curated
point that has a row for the day, **at any distance**, and says which point
it is, how high and how far away. "As long as we are open about where the
nearest point is, the distance doesn't matter. If it turns out it does
matter, we add more points."

The point is chosen by distance alone; its height is stated, not used to
choose. A resort's peak and its village can sit a thousand metres apart, so
the caption names the height and the reader judges.

**One small scan, then one row.** The candidates are every public location
with a row for the day — the estate is a few hundred points — read as bare
coordinates, so the heavy ``hourly`` and ``forecast`` columns are loaded for
the winner only. ``public()`` and never ``active()``: they are the same set
today, but the borrow is a VISIBILITY question (a stranger's private pin
must never be named on someone else's page), so it reads the method that
answers it.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from apps.core.geo import haversine_km
from apps.locations.models import Location
from apps.weather.models import Weather

logger = logging.getLogger(__name__)

# Under this a place IS the forecast point, near enough — a saved resort's
# own pin — and the caption drops the distance rather than print "0 km".
SAME_PLACE_KM = 0.1


@dataclass(frozen=True)
class NearestForecast:
    """A curated forecast point, the row read from it and how far away it is.

    Attributes:
        row: The point's ``Weather`` row for the day asked for.
        label: How the point is named — its own name, its resort's or its
            region's (``forecast_label``).
        distance_km: How far the point is from the place that borrows it.

    """

    row: Weather
    label: str
    distance_km: float

    @property
    def location(self) -> Location:
        """Return the forecast point."""
        return self.row.location

    @property
    def elevation_m(self) -> int | None:
        """Return the point's height, rounded, or None when unknown."""
        height = self.row.location.elevation_m
        return round(height) if height is not None else None

    @property
    def is_same_place(self) -> bool:
        """Return whether the point is, near enough, the place itself."""
        return self.distance_km < SAME_PLACE_KM

    def to_string(self) -> str:
        """Return the point and its distance, for logs and test failures."""
        return f"{self.label or self.location} ({self.distance_km:.1f} km)"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def forecast_label(location: Location) -> str:
    """Name a public location the way its forecast page heads it.

    Its own name, else its resort's (the resort it pins, or one it is
    linked to), else its region's — the precedence
    ``apps.public.views.location_weather`` uses, because most public
    locations are unnamed region centroids. Three small queries at most.

    Args:
        location: The forecast point.

    Returns:
        The point's name, or "" when nothing names it.

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


def nearest_forecast(
    latitude: float, longitude: float, observed_on: datetime.date
) -> NearestForecast | None:
    """Return the nearest curated forecast point to a place, for one day.

    Args:
        latitude: The place's latitude — a pin, a meeting point, a route's
            start.
        longitude: Its longitude.
        observed_on: The day whose row is read: today for a live forecast,
            a past day for the record of what was known then.

    Returns:
        The nearest public point with a row for ``observed_on``, or None
        when no public point has one (an empty estate, or a day nothing
        was fetched).

    """
    candidates = (
        Weather.objects.on_date(observed_on)
        .filter(location__in=Location.objects.public())
        .values_list("pk", "location__latitude", "location__longitude")
    )
    best: tuple[float, int] | None = None
    for pk, lat, lon in candidates:
        distance = haversine_km(latitude, longitude, lat, lon)
        if best is None or distance < best[0]:
            best = (distance, pk)
    if best is None:
        return None
    distance, pk = best
    row = Weather.objects.select_related("location").get(pk=pk)
    return NearestForecast(
        row=row, label=forecast_label(row.location), distance_km=distance
    )
