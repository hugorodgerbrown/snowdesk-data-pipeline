"""
apps/trips/services/plan_days.py — a route's danger level by day.

Read by the Plan page's week of day tiles (SNOW-1215) and the trip page's
plan card (SNOW-1216).

"When" on the Plan page is five tiles, today and the four days after it, in
the weather day picker's pattern, plus an "Other" tile for any later day.
A tile shows the day's avalanche danger for the route's region when a
bulletin has been rated for that day, and nothing otherwise.

**Most tiles show no level, and that is the design.** A bulletin covers
today, and tomorrow once the evening issue is out; the days after that
never have one. Hugo's call (2026-10-24): the level is shown when it
is relevant and the page degrades gracefully when it is not — no "no
bulletin" chip on most of the tiles, just the date.

**The region is the route's start.** A trip meets at or near where the
route starts, and a route crossing a region boundary would otherwise need
a rule for which of two ratings a tile shows; the start is the answer a
reader expects ("the bulletin for where we're going"). A start outside
every known region gives no region and no levels, never an error.

The rating read is ``RegionDayRating``, the calendar's denormalised
per-(region, day) row, so a tile agrees with the calendar and the map's
choropleth about the same day: its ``max_rating``, the day's peak.

**The trip page asks the same question about one day.** ``trip_danger``
answers it with the same region rule and the same row, so the level the
organiser saw on a tile is the level the group reads on the trip — and,
like the tiles, it answers None on the many days no bulletin rated.
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
from apps.regions.services.point_match import region_for_point

if TYPE_CHECKING:
    from apps.trips.services.trip_weather import TripWeather
    from apps.weather.services.weather_display import ForecastPanelDay

logger = logging.getLogger(__name__)

# Today and the four days after it. The weather picker's seven columns were
# the first try, so the two week strips would read alike; eight columns
# (seven and Other) squashed every tile on a phone, and Hugo cut it to five
# (2026-10-24). A day further out is one tap away, on Other.
PLAN_DAYS = 5


@dataclass(frozen=True)
class PlanDay:
    """One tile of the Plan page's week.

    Attributes:
        date: The day.
        rating: The day's peak ``RegionDayRating.Rating`` key, or ``""``
            when no bulletin rated it.
        subdivision: The rating's ``+``/``-`` suffix, or ``""``.
        weather: The day's forecast from the week's forecast point, or
            None outside its window (SNOW-1217).

    """

    date: datetime.date
    rating: str = ""
    subdivision: str = ""
    weather: ForecastPanelDay | None = None

    @property
    def digit(self) -> str:
        """Return the level as its digit and suffix, ``"3+"``; ``""`` unrated."""
        if not self.rating:
            return ""
        return f"{danger_level_digit(self.rating)}{self.subdivision}"

    @property
    def rating_label(self) -> str:
        """Return the level in words with its digit, ``"Considerable (3)"``."""
        if not self.rating:
            return ""
        return _("%(level)s (%(digit)s)") % {
            "level": RegionDayRating.Rating(self.rating).label,
            "digit": self.digit,
        }

    def to_string(self) -> str:
        """Return the day and its level, for logs and test failures."""
        return f"{self.date.isoformat()} {self.digit}".strip()

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


@dataclass(frozen=True)
class PlanWeek:
    """The Plan page's week, and the region its levels are for.

    Attributes:
        days: ``PLAN_DAYS`` tiles from today.
        region: The route start's region, or None outside coverage.
        weather: The forecast point the tiles read, or None when none is
            near enough (SNOW-1217).

    """

    days: list[PlanDay]
    region: MicroRegion | None
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
        """Return the week's span and region, for logs."""
        region = self.region.name if self.region else "no region"
        return f"{self.first.isoformat()}–{self.last.isoformat()} {region}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def _rated_days(
    region: MicroRegion, first: datetime.date, last: datetime.date
) -> dict[datetime.date, PlanDay]:
    """Return the rated days of a region between two days, inclusive.

    One query. A "no rating" row is no level, exactly as no row is.

    Args:
        region: The region.
        first: The first day.
        last: The last day.

    Returns:
        A ``PlanDay`` per day a bulletin rated, keyed by day.

    """
    return {
        row.date: PlanDay(row.date, row.max_rating, row.max_subdivision or "")
        for row in RegionDayRating.objects.for_region_range(
            region, first, last
        ).exclude(max_rating=RegionDayRating.Rating.NO_RATING)
    }


def _start_region(points: list[list[float]]) -> MicroRegion | None:
    """Return the region a track starts in, or None (see the module note)."""
    return region_for_point(points[0][1], points[0][0]) if points else None


@dataclass(frozen=True)
class TripDanger:
    """A trip day's danger level and the region it is for.

    Attributes:
        day: The rated day.
        region: The route start's region.

    """

    day: PlanDay
    region: MicroRegion

    def to_string(self) -> str:
        """Return the level and region, for logs and test failures."""
        return f"{self.day.to_string()} {self.region.name}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def trip_danger(day: datetime.date, points: list[list[float]]) -> TripDanger | None:
    """Return the danger level on a route's start for one day, or None.

    None is the ordinary answer: a start outside every region, or a day no
    bulletin rated — every day but today and, after the evening issue,
    tomorrow. Two queries at most.

    Args:
        day: The trip's day.
        points: The route's track as ``[[lon, lat, …], …]``.

    Returns:
        The level and its region, or None.

    """
    region = _start_region(points)
    if region is None:
        return None
    rated = _rated_days(region, day, day).get(day)
    return TripDanger(rated, region) if rated else None


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


def plan_week(today: datetime.date, points: list[list[float]]) -> PlanWeek:
    """Build the week of tiles for a route's Plan page.

    Two queries at most: the region lookup and one range read of its
    ratings. A route with no points, or a start outside every region,
    costs the region lookup alone and has no levels.

    Args:
        today: The site's current day, the first tile.
        points: The route's track as ``[[lon, lat, …], …]``.

    Returns:
        The week.

    """
    dates = [today + datetime.timedelta(days=n) for n in range(PLAN_DAYS)]
    region = _start_region(points)
    if region is None:
        return PlanWeek([PlanDay(day) for day in dates], None)
    rated = _rated_days(region, dates[0], dates[-1])
    return PlanWeek([rated.get(day, PlanDay(day)) for day in dates], region)
