"""
apps/favourites/saved_today.py — "Today" for a user's saved places (SNOW-1359).

The Favourites panel opens on one compact row per saved place, every kind
the user keeps (a dropped pin, a saved resort, a region pin), each with the
region's danger today, how it moved since yesterday and the forecast it
borrows from the nearest curated point. OpenSnow's side-by-side mountains,
in the shape a map panel can hold.

Built for a whole list at once and in a fixed number of queries, however
many places there are: today's ratings are passed in (the view reads them
once for both of the panel's sections), and this reads yesterday's ratings
once, and borrows every place's forecast through ``nearest_forecasts``,
which costs the same handful of queries for one place or forty.
``nearest_forecast`` is deliberately NOT called: it queries per place.
Which point a place borrows is the rule
``docs/decisions/snowdesk-decides-where-a-forecast-is-fetched.md`` sets:
the nearest curated point, at any distance, and the row says which point
it is (``source``, rendered by ``includes/_forecast_source.html``).

The trend compares level RANKS only. A subdivision ("3+") is part of the
day's level as written, not a step on the scale, so "3-" to "3+" is Same.
``no_rating`` is no bulletin, for the level and the trend alike.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from apps.bulletins.models import RegionDayRating
from apps.favourites.models import Favourite
from apps.public.templatetags.snowdesk_time import danger_level_digit
from apps.regions.models import MicroRegion
from apps.weather.services.nearest import NearestForecast, nearest_forecasts
from apps.weather.services.weather_display import (
    weather_code_icon_bucket,
    weather_icon_filename,
)

logger = logging.getLogger(__name__)

PIN = "pin"
RESORT = "resort"
REGION = "region"

UP = "up"
SAME = "same"
DOWN = "down"

# Rising first: of two places at one level, the one getting worse is the
# one to read first. A place with no trend sorts after all three.
_TREND_ORDER = {UP: 0, SAME: 1, DOWN: 2, None: 3}


@dataclass(frozen=True)
class SavedToday:
    """One saved place's row: its danger, its trend and its forecast.

    Attributes:
        uuid: The favourite's uuid, for a stable DOM id.
        kind: ``"pin"``, ``"resort"`` or ``"region"``.
        name: What the row is called; "" for a pin the user never named.
        level: Today's ``max_rating`` key, or None when there is no bulletin.
        subdivision: Today's ``max_subdivision`` ("+", "-" or "").
        trend: ``"up"``, ``"down"``, ``"same"``, or None when either day has
            no bulletin.
        latitude: Where a pin or resort row frames the map; None for a region.
        longitude: As ``latitude``.
        region_id: The place's micro-region id, or None.
        region_url: The region's canonical bulletin page, or "".
        icon: The borrowed forecast's day icon filename, or None.
        temp_max: The borrowed forecast's maximum, °C, or None.
        source: Which curated point the forecast is borrowed from, how
            high and how far away, or None when there is no forecast.
        rating: Today's rating row, for the response's freshness headers.

    """

    uuid: str
    kind: str
    name: str
    level: str | None
    subdivision: str
    trend: str | None
    latitude: float | None
    longitude: float | None
    region_id: str | None
    region_url: str
    icon: str | None
    temp_max: float | None
    rating: RegionDayRating | None
    source: NearestForecast | None = None

    def to_string(self) -> str:
        """Return the row in one line, for logs and test failures."""
        return f"{self.kind} {self.name or self.uuid}: {self.level} {self.trend}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def _rank(rating: RegionDayRating | None) -> int:
    """Return a rating's level as a number, 0 for no bulletin."""
    if rating is None:
        return 0
    digit = danger_level_digit(rating.max_rating)
    return int(digit) if digit else 0


def trend_between(
    today: RegionDayRating | None, yesterday: RegionDayRating | None
) -> str | None:
    """Return how a region's level moved since yesterday.

    Args:
        today: Today's rating, or None.
        yesterday: Yesterday's rating, or None.

    Returns:
        ``"up"``, ``"down"`` or ``"same"``; None when either day has no
        bulletin, because a move from or to nothing is not a move.

    """
    now, before = _rank(today), _rank(yesterday)
    if not now or not before:
        return None
    if now > before:
        return UP
    if now < before:
        return DOWN
    return SAME


def _kind(favourite: Favourite) -> str:
    """Return which of the three saved things a favourite is."""
    if favourite.resort_id is not None:
        return RESORT
    if favourite.latitude is None:
        return REGION
    return PIN


def _region(favourite: Favourite) -> MicroRegion | None:
    """Return the favourite's region: its own, else its resort's."""
    if favourite.region is not None:
        return favourite.region
    if favourite.resort is not None:
        return favourite.resort.region
    return None


def _forecast_point(
    favourite: Favourite, region: MicroRegion | None
) -> tuple[float, float] | None:
    """Return where a place borrows its forecast from: itself, or a centroid.

    A pin and a resort borrow from their own coordinate; a region pin has
    none and borrows from its region's centroid Location when it has one.
    """
    if favourite.latitude is not None and favourite.longitude is not None:
        return (favourite.latitude, favourite.longitude)
    if region is not None and region.centroid_location is not None:
        anchor = region.centroid_location
        return (anchor.latitude, anchor.longitude)
    return None


def _name(favourite: Favourite, kind: str, region: MicroRegion | None) -> str:
    """Return what a row is called: the resort, the region or the pin's label."""
    if kind == RESORT and favourite.resort is not None:
        return favourite.resort.name
    if kind == REGION and region is not None:
        return region.name
    return favourite.name


def build_saved_today(
    favourites: Iterable[Favourite],
    day: datetime.date,
    today_ratings: Mapping[int, RegionDayRating],
) -> list[SavedToday]:
    """Build the Today rows for a user's saved places.

    Args:
        favourites: Every one of the user's favourites, with ``region``,
            ``region__centroid_location``, ``resort`` and ``resort__region``
            already joined, so reading them queries nothing.
        day: Today.
        today_ratings: Today's ``RegionDayRating`` rows by region pk, read
            once by the caller for every region the favourites name.

    Returns:
        The rows, most urgent first: highest level, then rising before
        steady before falling, then by name.

    """
    favourites = list(favourites)
    regions = {f.pk: _region(f) for f in favourites}
    region_pks = {r.pk for r in regions.values() if r is not None}
    yesterday = {
        rating.region_id: rating
        for rating in RegionDayRating.objects.filter(
            region_id__in=region_pks, date=day - datetime.timedelta(days=1)
        ).only("id", "region_id", "date", "max_rating")
    }
    points = {
        f.pk: point
        for f in favourites
        if (point := _forecast_point(f, regions[f.pk])) is not None
    }
    weather = nearest_forecasts(points, day)

    rows: list[SavedToday] = []
    for favourite in favourites:
        kind = _kind(favourite)
        region = regions[favourite.pk]
        today_rating = today_ratings.get(region.pk) if region is not None else None
        if not _rank(today_rating):
            today_rating = None
        yesterday_rating = yesterday.get(region.pk) if region is not None else None
        source = weather.get(favourite.pk)
        row = source.row if source is not None else None
        rows.append(
            SavedToday(
                uuid=str(favourite.uuid),
                kind=kind,
                name=_name(favourite, kind, region),
                level=today_rating.max_rating if today_rating else None,
                subdivision=(today_rating.max_subdivision or "")
                if today_rating
                else "",
                trend=trend_between(today_rating, yesterday_rating),
                latitude=favourite.latitude if kind != REGION else None,
                longitude=favourite.longitude if kind != REGION else None,
                region_id=region.region_id if region is not None else None,
                region_url=region.get_absolute_url() if region is not None else "",
                icon=weather_icon_filename(
                    weather_code_icon_bucket(row.weather_code), "day"
                )
                if row is not None
                else None,
                temp_max=row.temperature_2m_max if row is not None else None,
                rating=today_rating,
                source=source,
            )
        )

    rows.sort(
        key=lambda r: (
            -int(danger_level_digit(r.level) or 0),
            _TREND_ORDER[r.trend],
            r.name.casefold(),
        )
    )
    return rows
