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

**The scan and the choice are separate helpers** (SNOW-965).
``forecast_candidates`` reads the day's candidates once and
``nearest_candidate`` picks from them, so a caller resolving many places —
``apps.public.area_items`` borrowing a forecast for every pin in a
downloaded area — pays one scan however many places it asks about.
``nearest_forecast`` is their composition, with the same tie-break: the
first candidate at the smallest distance wins.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, cast

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


class ForecastCandidate(NamedTuple):
    """One public point with a row for the day, as bare coordinates.

    Attributes:
        pk: The ``Weather`` row's primary key.
        short_id: The point's ``Location.short_id`` — what the weather
            sheet's URL is addressed by.
        lat: The point's latitude.
        lon: The point's longitude.

    """

    pk: int
    short_id: str
    lat: float
    lon: float


def forecast_candidates(observed_on: datetime.date) -> list[ForecastCandidate]:
    """Return every public point with a row for one day, as bare coordinates.

    One query, and none of the heavy ``hourly`` / ``forecast`` columns.

    Args:
        observed_on: The day whose rows count.

    Returns:
        The candidates, in the database's order — the order
        ``nearest_candidate`` breaks a tie by.

    """
    rows = (
        Weather.objects.on_date(observed_on)
        .filter(location__in=Location.objects.public())
        .values_list(
            "pk", "location__short_id", "location__latitude", "location__longitude"
        )
    )
    return [
        ForecastCandidate(pk=pk, short_id=cast("str", short_id), lat=lat, lon=lon)
        for pk, short_id, lat, lon in rows
    ]


def nearest_candidate(
    latitude: float, longitude: float, candidates: Sequence[ForecastCandidate]
) -> tuple[ForecastCandidate, float] | None:
    """Return the candidate nearest a place, and how far away it is.

    No query: the candidates are read once by ``forecast_candidates``.
    The first candidate at the smallest distance wins a tie.

    Args:
        latitude: The place's latitude.
        longitude: Its longitude.
        candidates: The day's candidates.

    Returns:
        ``(candidate, distance_km)``, or None when there are no candidates.

    """
    best: tuple[ForecastCandidate, float] | None = None
    for candidate in candidates:
        distance = haversine_km(latitude, longitude, candidate.lat, candidate.lon)
        if best is None or distance < best[1]:
            best = (candidate, distance)
    return best


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
    best = nearest_candidate(latitude, longitude, forecast_candidates(observed_on))
    if best is None:
        return None
    candidate, distance = best
    row = Weather.objects.select_related("location").get(pk=candidate.pk)
    return NearestForecast(
        row=row, label=forecast_label(row.location), distance_km=distance
    )
