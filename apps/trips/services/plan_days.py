"""
apps/trips/services/plan_days.py — the Plan page's week of day tiles (SNOW-1215).

"When" on the Plan page is seven tiles, today and the six days after it, in
the weather day picker's pattern, plus an "Other" tile for any later day.
A tile shows the day's avalanche danger for the route's region when a
bulletin has been rated for that day, and nothing otherwise.

**Most tiles show no level, and that is the design.** A bulletin covers
today, and tomorrow once the evening issue is out; the five days after
that never have one. Hugo's call (2026-10-24): the level is shown when it
is relevant and the page degrades gracefully when it is not — no "no
bulletin" chip on five tiles out of seven, just the date.

**The region is the route's start.** A trip meets at or near where the
route starts, and a route crossing a region boundary would otherwise need
a rule for which of two ratings a tile shows; the start is the answer a
reader expects ("the bulletin for where we're going"). A start outside
every known region gives no region and no levels, never an error.

The rating read is ``RegionDayRating``, the calendar's denormalised
per-(region, day) row, so a tile agrees with the calendar and the map's
choropleth about the same day: its ``max_rating``, the day's peak.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from django.utils.translation import gettext as _

from apps.bulletins.models import RegionDayRating
from apps.public.templatetags.snowdesk_time import danger_level_digit
from apps.regions.models import MicroRegion
from apps.regions.services.point_match import region_for_point

logger = logging.getLogger(__name__)

# Today and the six days after it: the weather picker's seven columns, so
# the two week strips read alike.
PLAN_DAYS = 7


@dataclass(frozen=True)
class PlanDay:
    """One tile of the Plan page's week.

    Attributes:
        date: The day.
        rating: The day's peak ``RegionDayRating.Rating`` key, or ``""``
            when no bulletin rated it.
        subdivision: The rating's ``+``/``-`` suffix, or ``""``.

    """

    date: datetime.date
    rating: str = ""
    subdivision: str = ""

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

    """

    days: list[PlanDay]
    region: MicroRegion | None

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
    region = region_for_point(points[0][1], points[0][0]) if points else None
    if region is None:
        return PlanWeek([PlanDay(day) for day in dates], None)
    rated = {
        row.date: row
        for row in RegionDayRating.objects.for_region_range(
            region, dates[0], dates[-1]
        ).exclude(max_rating=RegionDayRating.Rating.NO_RATING)
    }
    days = [
        PlanDay(day, rated[day].max_rating, rated[day].max_subdivision or "")
        if day in rated
        else PlanDay(day)
        for day in dates
    ]
    return PlanWeek(days, region)
