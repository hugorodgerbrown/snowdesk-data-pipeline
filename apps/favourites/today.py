"""
apps/favourites/today.py — "Today" for pinned regions and resorts (SNOW-1366).

Grown from SNOW-1359's "Saved today".

The map's Today panel, opened from the sun roundel in the date row: one row
per avalanche region the user cares about, carrying that region's danger
today and how it moved since yesterday, with the user's pinned resorts in it
nested underneath, each carrying the resort's weather today. A region is in
the list because the user pinned it, or because they pinned a resort in it
(``pinned`` says which). Dropped pins are not listed: a pin is the user's
own spot, not somewhere Snowdesk publishes a reading for (Hugo, 2 Nov 2026).

SNOW-1359 put these readings on top of the Favourites panel, one flat row
per saved place, every kind; that duplicated the pins listed beneath it,
and Hugo asked for today to be a surface of its own instead.

Built for a whole list at once and in a fixed number of queries, however
many places there are: today's ratings are passed in (the view reads them
once), this reads yesterday's ratings once and borrows every resort's
forecast through ``nearest_forecasts``, which costs the same handful of
queries for one place or forty. ``nearest_forecast`` is deliberately NOT
called: it queries per place. A resort's own pin is a curated point, so in
practice it reads its own forecast; the rule
``docs/decisions/snowdesk-decides-where-a-forecast-is-fetched.md`` sets is
still the one applied, and the row names the point when it is elsewhere.
A region borrows nothing: the danger is the region's reading, and the
weather belongs to the places in it.

The trend compares level RANKS only. A subdivision ("3+") is part of the
day's level as written, not a step on the scale, so "3-" to "3+" is Same.
``no_rating`` is no bulletin, for the level and the trend alike.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

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

UP = "up"
SAME = "same"
DOWN = "down"


@dataclass(frozen=True)
class TodayResort:
    """One pinned resort's row: its name and its weather today.

    Attributes:
        uuid: The favourite's uuid, for a stable DOM id.
        name: The resort's name (never the user's own label for it).
        slug: The resort's slug, which its map sheet is opened by; "" for
            a resort not yet backfilled with one.
        url: The resort's page, for a reader off the map; "" with no slug.
        icon: The forecast's day icon filename, or None.
        temp_max: The forecast's maximum, °C, or None.
        source: Which curated point the forecast is from, how high and how
            far away, or None when there is no forecast.

    """

    uuid: str
    name: str
    slug: str
    url: str
    icon: str | None
    temp_max: float | None
    source: NearestForecast | None = None

    def to_string(self) -> str:
        """Return the row in one line, for logs and test failures."""
        return f"resort {self.name}: {self.icon} {self.temp_max}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


@dataclass(frozen=True)
class TodayRegion:
    """One region's row: its danger today, its trend, its pinned resorts.

    Attributes:
        region_id: The micro-region's EAWS id ("CH-4115").
        name: The region's name.
        url: The region's canonical bulletin page.
        pinned: Whether the user pinned the region itself; False for a
            region that is listed only as a pinned resort's parent.
        level: Today's ``max_rating`` key, or None when there is no bulletin.
        subdivision: Today's ``max_subdivision`` ("+", "-" or "").
        trend: ``"up"``, ``"down"``, ``"same"``, or None when either day has
            no bulletin.
        rating: Today's rating row, for the response's freshness headers.
        resorts: The user's pinned resorts in this region, by name.

    """

    region_id: str
    name: str
    url: str
    pinned: bool
    level: str | None
    subdivision: str
    trend: str | None
    rating: RegionDayRating | None
    resorts: list[TodayResort] = field(default_factory=list)

    def to_string(self) -> str:
        """Return the row in one line, for logs and test failures."""
        return f"region {self.region_id}: {self.level} {self.trend}"

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


def today_subjects(favourites: Iterable[Favourite]) -> list[Favourite]:
    """Return the favourites the Today panel lists: region pins and resorts.

    Args:
        favourites: Any of a user's favourites.

    Returns:
        The region pins and the saved resorts among them; dropped pins are
        left out.

    """
    return [f for f in favourites if f.resort_id is not None or f.latitude is None]


def today_region_pks(favourites: Iterable[Favourite]) -> set[int]:
    """Return the pks of every region the Today panel will list.

    A region pin's own region and each saved resort's region, read off
    already-joined relations, so the caller can fetch today's ratings for
    exactly these in one query.

    Args:
        favourites: The panel's subjects (``today_subjects``), with
            ``region`` and ``resort__region`` joined.

    Returns:
        The regions' primary keys.

    """
    pks: set[int] = set()
    for favourite in favourites:
        region = _parent(favourite)
        if region is not None:
            pks.add(region.pk)
    return pks


def _parent(favourite: Favourite) -> MicroRegion | None:
    """Return the region a subject is listed under: the resort's, or its own."""
    if favourite.resort is not None:
        return favourite.resort.region
    return favourite.region


def _resort_point(favourite: Favourite) -> tuple[float, float] | None:
    """Return where a saved resort reads its forecast from: its own pin."""
    if favourite.latitude is not None and favourite.longitude is not None:
        return (favourite.latitude, favourite.longitude)
    if favourite.resort is not None:
        return favourite.resort.pin_coordinates
    return None


def build_today(
    favourites: Iterable[Favourite],
    day: datetime.date,
    today_ratings: Mapping[int, RegionDayRating],
) -> list[TodayRegion]:
    """Build the Today panel's regions, each with its pinned resorts.

    Args:
        favourites: A user's favourites, with ``region``, ``resort`` and
            ``resort__region`` (and ``resort__location``) already joined, so
            reading them queries nothing. Dropped pins are ignored.
        day: Today.
        today_ratings: Today's ``RegionDayRating`` rows by region pk, read
            once by the caller for every region the favourites name.

    Returns:
        The regions in name order, each with its resorts in name order: a
        fixed order, so a place is where it was yesterday whatever its
        danger today.

    """
    subjects = today_subjects(favourites)
    regions: dict[int, MicroRegion] = {}
    pinned: set[int] = set()
    resorts_by_region: dict[int, list[Favourite]] = {}
    for favourite in subjects:
        region = _parent(favourite)
        if region is None:
            # A region pin always names its region and a resort always has
            # one (``Resort.region`` is not null), so this is a row whose
            # region was deleted under it: nothing to list it beneath.
            logger.debug("today: %s has no region to list it under", favourite.uuid)
            continue
        regions[region.pk] = region
        if favourite.resort is None:
            pinned.add(region.pk)
        else:
            resorts_by_region.setdefault(region.pk, []).append(favourite)

    yesterday = {
        rating.region_id: rating
        for rating in RegionDayRating.objects.filter(
            region_id__in=set(regions), date=day - datetime.timedelta(days=1)
        ).only("id", "region_id", "date", "max_rating")
    }
    points = {
        f.pk: point
        for group in resorts_by_region.values()
        for f in group
        if (point := _resort_point(f)) is not None
    }
    weather = nearest_forecasts(points, day)

    rows: list[TodayRegion] = []
    for pk, region in regions.items():
        today_rating = today_ratings.get(pk)
        if not _rank(today_rating):
            today_rating = None
        resorts = sorted(
            (_resort_row(f, weather.get(f.pk)) for f in resorts_by_region.get(pk, [])),
            key=lambda r: r.name.casefold(),
        )
        rows.append(
            TodayRegion(
                region_id=region.region_id,
                name=region.name,
                url=region.get_absolute_url(),
                pinned=pk in pinned,
                level=today_rating.max_rating if today_rating else None,
                subdivision=(today_rating.max_subdivision or "")
                if today_rating
                else "",
                trend=trend_between(today_rating, yesterday.get(pk)),
                rating=today_rating,
                resorts=resorts,
            )
        )

    # Alphabetical, never by urgency (Hugo, 2 Nov 2026): a list that
    # reorders itself as the danger changes puts each place somewhere new
    # every morning, and a reader finds a place by where it was yesterday.
    rows.sort(key=lambda r: (r.name.casefold(), r.region_id))
    return rows


def _resort_row(favourite: Favourite, source: NearestForecast | None) -> TodayResort:
    """Build one saved resort's row from its favourite and its forecast."""
    resort = favourite.resort
    if resort is None:  # build_today passes resorts only
        raise ValueError(f"{favourite.uuid} is not a saved resort")
    row = source.row if source is not None else None
    slug = resort.slug or ""
    return TodayResort(
        uuid=str(favourite.uuid),
        name=resort.name,
        slug=slug,
        url=resort.get_absolute_url() if slug else "",
        icon=weather_icon_filename(weather_code_icon_bucket(row.weather_code), "day")
        if row is not None
        else None,
        temp_max=row.temperature_2m_max if row is not None else None,
        source=source,
    )
