"""
apps/trips/services/plan_days.py — a route's danger levels by day.

Read by the Plan page's week of day tiles (SNOW-1215) and the trip page's
plan card (SNOW-1216).

"When" on the Plan page is five tiles, today and the four days after it, in
the weather day picker's pattern, plus an "Other" tile for any later day.
A tile shows the day's avalanche danger for each region the route crosses
when a bulletin has been rated for that day, and nothing otherwise.

**Most tiles show no level, and that is the design.** A bulletin covers
today, and tomorrow once the evening issue is out; the days after that
never have one. Hugo's call (2026-10-24): the level is shown when it
is relevant and the page degrades gracefully when it is not — no "no
bulletin" chip on most of the tiles, just the date.

**Every region the route crosses, in the order it crosses them** (SNOW-945,
which replaced SNOW-947's start-only rule). A tour that climbs out of one
region and descends into another is under two bulletins; Hugo's call
(2026-10-31) is that a tile shows each crossed region's level, side by
side in route order, rather than one digit standing for both. A one-region
route reads exactly as before. The regions come from
``apps.regions.services.track_regions``; a track outside every known region
gives no regions and no levels, never an error.

The rating read is ``RegionDayRating``, the calendar's denormalised
per-(region, day) row, so a tile agrees with the calendar and the map's
choropleth about the same day: its ``max_rating``, the day's peak.

**The trip page asks the same question about one day.** ``trip_dangers``
answers it with the same regions and the same rows, so the levels the
organiser saw on a tile are the levels the group reads on the trip — and,
like the tiles, it answers nothing on the many days no bulletin rated.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from django.utils.translation import gettext as _

from apps.bulletins.models import RegionDayRating
from apps.public.templatetags.snowdesk_time import danger_level_digit
from apps.regions.models import MicroRegion

if TYPE_CHECKING:
    from apps.trips.services.trip_weather import TripWeather
    from apps.weather.services.weather_display import ForecastPanelDay

logger = logging.getLogger(__name__)

# Today and the four days after it. The weather picker's seven columns were
# the first try, so the two week strips would read alike; eight columns
# (seven and Other) squashed every tile on a phone, and Hugo cut it to five
# (2026-10-24). A day further out is one tap away, on Other.
PLAN_DAYS = 5

# Level marks a day tile draws before it folds the rest into "+N". Three
# fit a phone-width tile; the caption under the tiles still names them all.
TILE_LEVELS = 3


@dataclass(frozen=True)
class PlanLevel:
    """One region's danger level on one day.

    Attributes:
        region: The region the level is for.
        rating: The day's peak ``RegionDayRating.Rating`` key.
        subdivision: The rating's ``+``/``-`` suffix, or ``""``.

    """

    region: MicroRegion
    rating: str
    subdivision: str = ""

    @property
    def digit(self) -> str:
        """Return the level as its digit and suffix, ``"3+"``."""
        return f"{danger_level_digit(self.rating)}{self.subdivision}"

    @property
    def rating_label(self) -> str:
        """Return the level in words with its digit, ``"Considerable (3)"``."""
        return _("%(level)s (%(digit)s)") % {
            "level": RegionDayRating.Rating(self.rating).label,
            "digit": self.digit,
        }

    @property
    def caption(self) -> str:
        """Return the level and where it is, ``"Considerable (3) in X"``."""
        return _("%(level)s in %(region)s") % {
            "level": self.rating_label,
            "region": self.region.name,
        }

    def to_string(self) -> str:
        """Return the level and region, for logs and test failures."""
        return f"{self.digit} {self.region.region_id}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


@dataclass(frozen=True)
class PlanDay:
    """One tile of the Plan page's week.

    Attributes:
        date: The day.
        levels: The rated crossed regions' levels that day, in route order.
            Empty when no bulletin rated the day for any of them.
        weather: The day's forecast from the week's forecast point, or
            None outside its window (SNOW-1217).

    """

    date: datetime.date
    levels: tuple[PlanLevel, ...] = ()
    weather: ForecastPanelDay | None = None

    @property
    def tile_levels(self) -> tuple[PlanLevel, ...]:
        """Return the levels the tile draws, the first ``TILE_LEVELS``."""
        return self.levels[:TILE_LEVELS]

    @property
    def more_levels(self) -> int:
        """Return how many levels the tile folds into "+N"."""
        return max(len(self.levels) - TILE_LEVELS, 0)

    @property
    def caption(self) -> str:
        """Return every level in words, joined with `` · ``.

        ``"Considerable (3) in X · Moderate (2) in Y"``; ``""`` unrated.
        """
        return " · ".join(level.caption for level in self.levels)

    @property
    def readback(self) -> str:
        """Return the levels for the form's read-back footer.

        One region reads as before, ``"Considerable (3)"`` — the footer
        already names the place. Several name their regions, as the
        caption does, since one level would not say which bulletin it is.
        """
        if len(self.levels) == 1:
            return self.levels[0].rating_label
        return self.caption

    def to_string(self) -> str:
        """Return the day and its levels, for logs and test failures."""
        digits = " ".join(level.to_string() for level in self.levels)
        return f"{self.date.isoformat()} {digits}".strip()

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


@dataclass(frozen=True)
class PlanWeek:
    """The Plan page's week, and the regions its levels are for.

    Attributes:
        days: ``PLAN_DAYS`` tiles from today.
        regions: The regions the route crosses, in route order; empty
            outside coverage.
        weather: The forecast point the tiles read, or None when none is
            near enough (SNOW-1217).

    """

    days: list[PlanDay]
    regions: list[MicroRegion]
    weather: TripWeather | None = None

    @property
    def first(self) -> datetime.date:
        """Return the week's first day, today."""
        return self.days[0].date

    @property
    def last(self) -> datetime.date:
        """Return the week's last tile's day."""
        return self.days[-1].date

    def to_string(self) -> str:
        """Return the week's span and regions, for logs."""
        names = ", ".join(region.name for region in self.regions) or "no region"
        return f"{self.first.isoformat()}–{self.last.isoformat()} {names}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def _rated_levels(
    regions: list[MicroRegion], first: datetime.date, last: datetime.date
) -> dict[datetime.date, tuple[PlanLevel, ...]]:
    """Return each day's levels for the regions, in route order.

    One query whatever the number of regions. A "no rating" row is no
    level, exactly as no row is.

    Args:
        regions: The crossed regions, in route order.
        first: The first day.
        last: The last day, inclusive.

    Returns:
        The rated days, keyed by day, each with its levels in the order of
        ``regions``. A day no bulletin rated is absent.

    """
    if not regions:
        return {}
    order = {region.pk: index for index, region in enumerate(regions)}
    by_pk = {region.pk: region for region in regions}
    found: dict[datetime.date, list[tuple[int, PlanLevel]]] = {}
    rows = (
        RegionDayRating.objects.filter(
            region__in=regions, date__gte=first, date__lte=last
        )
        .exclude(max_rating=RegionDayRating.Rating.NO_RATING)
        .only("region_id", "date", "max_rating", "max_subdivision")
    )
    for row in rows:
        level = PlanLevel(
            by_pk[row.region_id], row.max_rating, row.max_subdivision or ""
        )
        found.setdefault(row.date, []).append((order[row.region_id], level))
    return {
        day: tuple(level for _index, level in sorted(pairs, key=lambda pair: pair[0]))
        for day, pairs in found.items()
    }


@dataclass(frozen=True)
class TripDanger:
    """One crossed region's danger level on a trip's day.

    Attributes:
        day: The trip's day.
        level: The region's level that day.

    """

    day: datetime.date
    level: PlanLevel

    @property
    def region(self) -> MicroRegion:
        """Return the region the level is for."""
        return self.level.region

    def to_string(self) -> str:
        """Return the day, level and region, for logs and test failures."""
        return f"{self.day.isoformat()} {self.level.to_string()}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def trip_dangers(day: datetime.date, regions: list[MicroRegion]) -> list[TripDanger]:
    """Return the rated crossed regions' levels on one day, in route order.

    Empty is the ordinary answer: a track outside every region, or a day no
    bulletin rated — every day but today and, after the evening issue,
    tomorrow. One query, none without regions.

    Args:
        day: The trip's day.
        regions: The regions the route crosses, in route order
            (``track_regions``).

    Returns:
        One ``TripDanger`` per rated region, in route order.

    """
    levels = _rated_levels(regions, day, day).get(day, ())
    return [TripDanger(day, level) for level in levels]


def with_weather(week: PlanWeek, weather: TripWeather | None) -> PlanWeek:
    """Return the week with each tile's forecast from ``weather`` (SNOW-1217).

    A tile outside the forecast's window keeps no forecast, as a tile no
    bulletin rated keeps no level.

    Args:
        week: The week from ``plan_week``.
        weather: The forecast point, or None.

    Returns:
        A new week; ``week`` itself is unchanged.

    """
    if weather is None:
        return week
    days = [replace(day, weather=weather.on(day.date)) for day in week.days]
    return replace(week, days=days, weather=weather)


def plan_week(today: datetime.date, regions: list[MicroRegion]) -> PlanWeek:
    """Build the week of tiles for a route's Plan page.

    One query for every crossed region's ratings over the week, none
    without regions.

    Args:
        today: The site's current day, the first tile.
        regions: The regions the route crosses, in route order
            (``track_regions``).

    Returns:
        The week.

    """
    dates = [today + datetime.timedelta(days=n) for n in range(PLAN_DAYS)]
    rated = _rated_levels(regions, dates[0], dates[-1])
    return PlanWeek([PlanDay(day, rated.get(day, ())) for day in dates], list(regions))
