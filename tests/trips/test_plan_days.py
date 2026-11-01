"""
tests/trips/test_plan_days.py — the Plan page's week of day tiles (SNOW-1215).

Covers ``apps.trips.services.plan_days``: five days from today, a level for
each crossed region in route order (SNOW-945), and a level only on a day a
bulletin rated — never on a day rated "no rating", never outside the week,
and never without regions. ``trip_dangers`` is the trip page's one day.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from apps.bulletins.models import RegionDayRating
from apps.regions.models import MicroRegion
from apps.trips.services.plan_days import (
    PLAN_DAYS,
    TILE_LEVELS,
    PlanDay,
    PlanLevel,
    plan_week,
    trip_dangers,
    with_weather,
)
from apps.trips.services.trip_weather import meeting_point_weather
from tests.factories import (
    LocationFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortLocationFactory,
    TripFactory,
    WeatherFactory,
    forecast_days,
)

TODAY = datetime.date(2026, 10, 24)

# A square region (7.0–7.5 E, 46.0–46.5 N), and a track starting inside it.
_SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]],
    ],
}


@pytest.fixture
def region() -> MicroRegion:
    """The first region a route crosses."""
    return MicroRegionFactory.create(
        name="Martigny-Verbier", boundary=_SQUARE, centre={"lon": 7.25, "lat": 46.25}
    )


@pytest.fixture
def second() -> MicroRegion:
    """The second region a route crosses."""
    return MicroRegionFactory.create(name="Val de Bagnes")


@pytest.mark.django_db
class TestPlanWeek:
    """``plan_week`` — the five tiles and their levels."""

    def test_five_days_from_today(self) -> None:
        """Today first, then the four days after it, with no route needed."""
        week = plan_week(TODAY, [])

        assert len(week.days) == PLAN_DAYS == 5
        assert week.first == TODAY
        assert week.last == TODAY + datetime.timedelta(days=4)
        assert week.regions == []
        assert all(not day.levels for day in week.days)
        assert "no region" in str(week)

    def test_one_region_carries_one_level_per_rated_day(
        self, region: MicroRegion
    ) -> None:
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

        week = plan_week(TODAY, [region])

        assert week.regions == [region]
        assert [[lv.digit for lv in day.levels] for day in week.days] == [
            ["2"],
            ["3+"],
            [],
            [],
            [],
        ]
        assert week.days[1].levels[0].rating_label == "Considerable (3+)"
        assert week.days[1].readback == "Considerable (3+)"
        assert "Martigny-Verbier" in str(week)

    def test_several_regions_are_in_route_order(
        self, region: MicroRegion, second: MicroRegion
    ) -> None:
        """The route's order, not the rows' — and an unrated region is skipped."""
        RegionDayRatingFactory.create(
            region=second, date=TODAY, max_rating=RegionDayRating.Rating.MODERATE
        )
        RegionDayRatingFactory.create(
            region=region, date=TODAY, max_rating=RegionDayRating.Rating.CONSIDERABLE
        )
        RegionDayRatingFactory.create(
            region=second,
            date=TODAY + datetime.timedelta(days=1),
            max_rating=RegionDayRating.Rating.LOW,
        )

        week = plan_week(TODAY, [region, second])

        assert [lv.region for lv in week.days[0].levels] == [region, second]
        assert week.days[0].caption == (
            "Considerable (3) in Martigny-Verbier · Moderate (2) in Val de Bagnes"
        )
        assert week.days[0].readback == week.days[0].caption
        assert [lv.region for lv in week.days[1].levels] == [second]

    def test_one_query_for_every_region(
        self, region: MicroRegion, second: MicroRegion, django_assert_num_queries: Any
    ) -> None:
        """The whole week, every region, one read."""
        with django_assert_num_queries(1):
            plan_week(TODAY, [region, second])

    def test_no_rating_and_days_outside_the_week_are_ignored(
        self, region: MicroRegion
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

        week = plan_week(TODAY, [region])

        assert all(not day.levels for day in week.days)


@pytest.mark.django_db
class TestTripDangers:
    """``trip_dangers`` — the trip page's one day (SNOW-1216, SNOW-945)."""

    def test_each_rated_region_in_route_order(
        self, region: MicroRegion, second: MicroRegion
    ) -> None:
        """The same rows and rule as the Plan page's tile."""
        RegionDayRatingFactory.create(
            region=second, date=TODAY, max_rating=RegionDayRating.Rating.MODERATE
        )
        RegionDayRatingFactory.create(
            region=region, date=TODAY, max_rating=RegionDayRating.Rating.CONSIDERABLE
        )

        dangers = trip_dangers(TODAY, [region, second])

        assert [danger.region for danger in dangers] == [region, second]
        assert dangers[0].level.rating_label == "Considerable (3)"
        assert str(dangers[0]) == f"2026-10-24 3 {region.region_id}"

    def test_an_unrated_region_is_skipped(
        self, region: MicroRegion, second: MicroRegion
    ) -> None:
        """No row, or a "no rating" row, is no line."""
        RegionDayRatingFactory.create(
            region=region,
            date=TODAY,
            min_rating=RegionDayRating.Rating.NO_RATING,
            max_rating=RegionDayRating.Rating.NO_RATING,
        )
        RegionDayRatingFactory.create(
            region=second, date=TODAY, max_rating=RegionDayRating.Rating.LOW
        )

        assert [d.region for d in trip_dangers(TODAY, [region, second])] == [second]
        assert trip_dangers(TODAY + datetime.timedelta(days=1), [region]) == []

    def test_no_regions_asks_nothing(self, django_assert_num_queries: Any) -> None:
        """A track outside coverage: no lines and no query."""
        with django_assert_num_queries(0):
            assert trip_dangers(TODAY, []) == []


@pytest.mark.django_db
class TestWithWeather:
    """``with_weather`` — each tile's forecast (SNOW-1217)."""

    def test_each_tile_in_the_window_takes_its_day(self) -> None:
        """The forecast reaches two days here; the rest keep none."""
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)
        WeatherFactory.create(
            location=location, observed_on=TODAY, forecast=forecast_days(TODAY, [73])
        )
        weather = meeting_point_weather(
            TripFactory.create(date=TODAY, meeting_point=location), TODAY
        )

        week = with_weather(plan_week(TODAY, []), weather)

        assert week.weather is weather
        assert [day.weather is not None for day in week.days] == [
            True,
            True,
            False,
            False,
            False,
        ]

    def test_no_forecast_point_leaves_the_week_alone(self) -> None:
        """No curated point has a forecast: the same week back."""
        week = plan_week(TODAY, [])

        assert with_weather(week, None) is week


@pytest.mark.django_db
class TestPlanDay:
    """``PlanDay`` — an unrated day reads as nothing; many fold into "+N"."""

    def test_an_unrated_day_has_no_levels(self) -> None:
        """The tile's slot stays empty."""
        day = PlanDay(TODAY)

        assert day.tile_levels == ()
        assert day.more_levels == 0
        assert day.caption == ""
        assert str(day) == "2026-10-24"

    def test_past_three_the_rest_fold_into_more(self) -> None:
        """Four regions: three marks and "+1", and the caption names all four."""
        levels = tuple(
            PlanLevel(
                MicroRegionFactory.create(name=f"Region {n}"),
                RegionDayRating.Rating.MODERATE,
            )
            for n in range(4)
        )
        day = PlanDay(TODAY, levels)

        assert len(day.tile_levels) == TILE_LEVELS == 3
        assert day.more_levels == 1
        assert day.caption.count(" in Region ") == 4
