"""
tests/trips/test_plan_days.py — the Plan page's week of day tiles (SNOW-1215).

Covers ``apps.trips.services.plan_days``: five days from today, the route
start's region, and a level only on a day a bulletin rated — never on a
day rated "no rating", never outside the week, and never for a start
outside every region.
"""

from __future__ import annotations

import datetime

import pytest

from apps.bulletins.models import RegionDayRating
from apps.trips.services.plan_days import PLAN_DAYS, PlanDay, plan_week, trip_danger
from tests.factories import MicroRegionFactory, RegionDayRatingFactory

TODAY = datetime.date(2026, 10, 24)

# A square region (7.0–7.5 E, 46.0–46.5 N), and a track starting inside it.
_SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]],
    ],
}
_TRACK = [[7.2, 46.1, 1500.0], [7.3, 46.2, 2400.0]]


@pytest.fixture
def region() -> object:
    """A region containing the track's start."""
    return MicroRegionFactory.create(
        name="Martigny-Verbier", boundary=_SQUARE, centre={"lon": 7.25, "lat": 46.25}
    )


@pytest.mark.django_db
class TestPlanWeek:
    """``plan_week`` — the five tiles and their levels."""

    def test_five_days_from_today(self) -> None:
        """Today first, then the four days after it, with no route needed."""
        week = plan_week(TODAY, [])

        assert len(week.days) == PLAN_DAYS == 5
        assert week.first == TODAY
        assert week.last == TODAY + datetime.timedelta(days=4)
        assert week.region is None
        assert all(not day.rating for day in week.days)

    def test_a_start_outside_every_region_has_no_levels(self, region: object) -> None:
        """No region is an answer about coverage, not an error."""
        RegionDayRatingFactory.create(region=region, date=TODAY)

        week = plan_week(TODAY, [[9.9, 47.9, 0.0]])

        assert week.region is None
        assert all(not day.rating for day in week.days)

    def test_a_rated_day_carries_its_peak_level(self, region: object) -> None:
        """Today and tomorrow rated; the days after carry nothing."""
        RegionDayRatingFactory.create(
            region=region, date=TODAY, max_rating=RegionDayRating.Rating.MODERATE
        )
        RegionDayRatingFactory.create(
            region=region,
            date=TODAY + datetime.timedelta(days=1),
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            max_subdivision="+",
        )

        week = plan_week(TODAY, _TRACK)

        assert week.region == region
        assert [day.digit for day in week.days] == ["2", "3+", "", "", ""]
        assert week.days[1].rating_label == "Considerable (3+)"
        assert "Martigny-Verbier" in str(week)

    def test_no_rating_and_days_outside_the_week_are_ignored(
        self, region: object
    ) -> None:
        """A "no rating" row, and a row a week out, show nothing."""
        RegionDayRatingFactory.create(
            region=region,
            date=TODAY,
            min_rating=RegionDayRating.Rating.NO_RATING,
            max_rating=RegionDayRating.Rating.NO_RATING,
        )
        RegionDayRatingFactory.create(
            region=region, date=TODAY + datetime.timedelta(days=5)
        )

        week = plan_week(TODAY, _TRACK)

        assert all(not day.rating for day in week.days)


@pytest.mark.django_db
class TestTripDanger:
    """``trip_danger`` — the trip page's one day (SNOW-1216)."""

    def test_a_rated_day_carries_its_level_and_region(self, region: object) -> None:
        """The same row and rule as the Plan page's tile."""
        RegionDayRatingFactory.create(
            region=region, date=TODAY, max_rating=RegionDayRating.Rating.CONSIDERABLE
        )

        danger = trip_danger(TODAY, _TRACK)

        assert danger is not None
        assert danger.region == region
        assert danger.day.rating_label == "Considerable (3)"
        assert str(danger) == "2026-10-24 3 Martigny-Verbier"

    def test_an_unrated_day_is_none(self, region: object) -> None:
        """No row, or a "no rating" row, is no level."""
        RegionDayRatingFactory.create(
            region=region,
            date=TODAY,
            min_rating=RegionDayRating.Rating.NO_RATING,
            max_rating=RegionDayRating.Rating.NO_RATING,
        )

        assert trip_danger(TODAY, _TRACK) is None
        assert trip_danger(TODAY + datetime.timedelta(days=1), _TRACK) is None

    def test_a_start_outside_every_region_is_none(self, region: object) -> None:
        """Coverage, not an error; and a trackless trip asks nothing."""
        RegionDayRatingFactory.create(region=region, date=TODAY)

        assert trip_danger(TODAY, [[9.9, 47.9, 0.0]]) is None
        assert trip_danger(TODAY, []) is None


class TestPlanDay:
    """``PlanDay`` — an unrated day reads as nothing."""

    def test_an_unrated_day_has_no_digit_or_label(self) -> None:
        """The tile's slot stays empty."""
        day = PlanDay(TODAY)

        assert day.digit == ""
        assert day.rating_label == ""
        assert str(day) == "2026-10-24"
