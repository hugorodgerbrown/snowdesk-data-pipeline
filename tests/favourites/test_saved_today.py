"""
tests/favourites/test_saved_today.py — "Today" for a user's saved places
(SNOW-1359).

Covers ``apps.favourites.saved_today``:
  - every kind of saved place gets a row, named as the place;
  - the level, "No bulletin" for no region, no row and ``no_rating``;
  - the trend: up, down and same, subdivisions ignored, none when either
    day is missing;
  - the borrowed forecast: from the place itself, from a region pin's
    centroid, and none when there is nothing to borrow from;
  - the order: highest level, then rising before steady before falling,
    then by name;
  - which point the forecast is from (``source``), for the caption;
  - a fixed number of queries however many places there are.
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from apps.favourites.models import Favourite
from apps.favourites.saved_today import (
    DOWN,
    PIN,
    REGION,
    RESORT,
    SAME,
    UP,
    SavedToday,
    build_saved_today,
    trend_between,
)
from apps.locations.models import Location
from apps.regions.models import MicroRegion
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    UserFactory,
    WeatherFactory,
)

R = RegionDayRating.Rating


def _favourites(user: User) -> list[Favourite]:
    """Return a user's favourites joined the way ``favourite_list`` joins them."""
    return list(
        Favourite.objects.filter(user=user).select_related(
            "region",
            "region__centroid_location",
            "location",
            "resort",
            "resort__region",
            "resort__region__centroid_location",
        )
    )


def _today_ratings(day: datetime.date) -> dict[int, RegionDayRating]:
    """Return every rating for a day by region pk, as the view reads them."""
    return {r.region_id: r for r in RegionDayRating.objects.filter(date=day)}


def _build(user: User) -> list[SavedToday]:
    """Build the rows for a user, today."""
    day = timezone.localdate()
    return build_saved_today(_favourites(user), day, _today_ratings(day))


def _rate(
    region: MicroRegion, today: str | None, yesterday: str | None = None, **kw: str
) -> None:
    """Rate a region today and, optionally, yesterday."""
    day = timezone.localdate()
    if today is not None:
        RegionDayRatingFactory.create(region=region, date=day, max_rating=today, **kw)
    if yesterday is not None:
        RegionDayRatingFactory.create(
            region=region, date=day - datetime.timedelta(days=1), max_rating=yesterday
        )


def _public_point(
    latitude: float, longitude: float, temp: float, code: int = 3
) -> Location:
    """Make a curated (public) forecast point with today's row."""
    location = LocationFactory.create(latitude=latitude, longitude=longitude)
    MicroRegionFactory.create(centroid_location=location)
    WeatherFactory.create(location=location, temperature_2m_max=temp, weather_code=code)
    return location


@pytest.mark.django_db
class TestKinds:
    """Every kind of saved place is a row, named as the place."""

    def test_a_pin_a_resort_and_a_region_pin(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        FavouriteFactory.create(user=user, name="Car park", region=region)
        FavouriteFactory.create(
            user=user,
            name="my label",
            region=region,
            resort=ResortFactory.create(name="Verbier", region=region),
        )
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        rows = {row.kind: row for row in _build(user)}

        assert rows[PIN].name == "Car park"
        assert rows[RESORT].name == "Verbier"
        assert rows[REGION].name == "Martigny-Verbier"
        assert rows[REGION].latitude is None
        assert rows[REGION].region_url == region.get_absolute_url()
        assert rows[PIN].latitude is not None

    def test_an_unnamed_pin_has_an_empty_name(self) -> None:
        """The template supplies "Unnamed pin", as the Places row does."""
        user = UserFactory.create()
        FavouriteFactory.create(user=user)
        assert _build(user)[0].name == ""

    def test_a_resort_reads_its_resorts_region(self) -> None:
        """A resort favourite with no region of its own reads the resort's."""
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        _rate(region, R.HIGH)
        FavouriteFactory.create(
            user=user, region=None, resort=ResortFactory.create(region=region)
        )
        assert _build(user)[0].level == R.HIGH


@pytest.mark.django_db
class TestLevel:
    """Today's level, or no bulletin."""

    def test_the_level_and_its_subdivision(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        _rate(region, R.CONSIDERABLE, max_subdivision="+")
        FavouriteFactory.create(user=user, region=region)

        row = _build(user)[0]
        assert row.level == R.CONSIDERABLE
        assert row.subdivision == "+"
        assert row.rating is not None

    @pytest.mark.parametrize("today", [None, R.NO_RATING])
    def test_no_rating_row_or_no_rating_is_no_bulletin(self, today: str | None) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        _rate(region, today, R.MODERATE)
        FavouriteFactory.create(user=user, region=region)

        row = _build(user)[0]
        assert row.level is None
        assert row.trend is None
        assert row.rating is None

    def test_a_pin_outside_every_region_is_no_bulletin(self) -> None:
        user = UserFactory.create()
        FavouriteFactory.create(user=user, region=None)

        row = _build(user)[0]
        assert row.level is None
        assert row.region_id is None
        assert row.region_url == ""


class TestTrend:
    """How the level moved: ranks only, none when a day is missing."""

    @staticmethod
    def _rating(key: str, subdivision: str = "") -> RegionDayRating:
        return RegionDayRating(max_rating=key, max_subdivision=subdivision)

    @pytest.mark.parametrize(
        ("today", "yesterday", "expected"),
        [
            (R.HIGH, R.CONSIDERABLE, UP),
            (R.MODERATE, R.CONSIDERABLE, DOWN),
            (R.CONSIDERABLE, R.CONSIDERABLE, SAME),
        ],
    )
    def test_up_down_and_same(self, today: str, yesterday: str, expected: str) -> None:
        assert trend_between(self._rating(today), self._rating(yesterday)) == expected

    def test_a_subdivision_is_not_a_step(self) -> None:
        """3- to 3+ is the same level as written."""
        assert (
            trend_between(
                self._rating(R.CONSIDERABLE, "+"), self._rating(R.CONSIDERABLE, "-")
            )
            == SAME
        )

    @pytest.mark.parametrize(
        ("today", "yesterday"),
        [(R.HIGH, None), (None, R.HIGH), (R.HIGH, R.NO_RATING), (R.NO_RATING, R.LOW)],
    )
    def test_no_trend_when_a_day_is_missing(
        self, today: str | None, yesterday: str | None
    ) -> None:
        assert (
            trend_between(
                self._rating(today) if today else None,
                self._rating(yesterday) if yesterday else None,
            )
            is None
        )


@pytest.mark.django_db
class TestForecast:
    """The forecast a place borrows from the nearest curated point."""

    def test_a_pin_borrows_the_nearest_point(self) -> None:
        user = UserFactory.create()
        _public_point(46.10, 7.40, temp=-3.0)
        _public_point(47.50, 9.00, temp=12.0)
        FavouriteFactory.create(user=user, latitude=46.11, longitude=7.41)

        row = _build(user)[0]
        assert row.temp_max == -3.0
        assert row.icon is not None and row.icon.endswith(".svg")
        assert row.source is not None
        assert row.source.distance_km == pytest.approx(1.4, abs=0.1)
        assert not row.source.is_same_place

    def test_a_region_pin_borrows_from_its_centroid(self) -> None:
        user = UserFactory.create()
        near = _public_point(46.50, 8.00, temp=1.0)
        _public_point(46.10, 7.40, temp=9.0)
        region = MicroRegion.objects.get(centroid_location=near)
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        row = _build(user)[0]
        assert row.temp_max == 1.0
        assert row.source is not None
        assert row.source.is_same_place

    def test_a_region_pin_with_no_centroid_has_no_forecast(self) -> None:
        user = UserFactory.create()
        _public_point(46.10, 7.40, temp=9.0)
        FavouriteFactory.create(
            user=user, region=MicroRegionFactory.create(), region_pin=True
        )

        row = _build(user)[0]
        assert row.icon is None
        assert row.temp_max is None
        assert row.source is None

    def test_no_point_with_a_row_today_is_no_forecast(self) -> None:
        user = UserFactory.create()
        FavouriteFactory.create(user=user)
        assert _build(user)[0].icon is None


@pytest.mark.django_db
class TestOrder:
    """Most urgent first."""

    def test_level_then_trend_then_name(self) -> None:
        user = UserFactory.create()
        rising = MicroRegionFactory.create()
        steady = MicroRegionFactory.create()
        falling = MicroRegionFactory.create()
        higher = MicroRegionFactory.create()
        _rate(rising, R.CONSIDERABLE, R.MODERATE)
        _rate(steady, R.CONSIDERABLE, R.CONSIDERABLE)
        _rate(falling, R.CONSIDERABLE, R.HIGH)
        _rate(higher, R.HIGH, R.HIGH)
        for name, region in [
            ("a-falling", falling),
            ("b-steady", steady),
            ("c-rising", rising),
            ("d-higher", higher),
        ]:
            FavouriteFactory.create(user=user, name=name, region=region)
        FavouriteFactory.create(user=user, name="e-nothing", region=None)
        FavouriteFactory.create(user=user, name="A-nothing", region=None)

        assert [row.name for row in _build(user)] == [
            "d-higher",
            "c-rising",
            "b-steady",
            "a-falling",
            "A-nothing",
            "e-nothing",
        ]


@pytest.mark.django_db
class TestQueries:
    """A fixed number of queries, however many places."""

    def _place_everything(self, user: User, n: int) -> None:
        for i in range(n):
            point = _public_point(46.0 + i * 0.01, 7.0, temp=float(i))
            region = MicroRegion.objects.get(centroid_location=point)
            _rate(region, R.MODERATE, R.LOW)
            FavouriteFactory.create(
                user=user, region=region, latitude=46.0, longitude=7.0
            )
            FavouriteFactory.create(
                user=user,
                region=region,
                resort=ResortFactory.create(region=region),
                latitude=46.0,
                longitude=7.0,
            )
            FavouriteFactory.create(user=user, region=region, region_pin=True)

    @pytest.mark.parametrize("n", [1, 4])
    def test_six_queries_once_the_favourites_and_todays_ratings_are_read(
        self, n: int
    ) -> None:
        """Yesterday's ratings, then ``nearest_forecasts``' five."""
        user = UserFactory.create()
        self._place_everything(user, n)
        favourites = _favourites(user)
        day = timezone.localdate()
        ratings = _today_ratings(day)

        with CaptureQueriesContext(connection) as queries:
            rows = build_saved_today(favourites, day, ratings)

        assert len(rows) == 3 * n
        assert len(queries) == 6
