"""
tests/favourites/test_today.py — "Today" for a user's pinned regions and
resorts (SNOW-1366, from SNOW-1359's "Saved today").

Covers ``apps.favourites.today``:
  - which favourites are listed: region pins and saved resorts, never a
    dropped pin;
  - the nesting: a resort under its region, a pinned resort bringing in an
    unpinned parent region, a pinned region and its resort as one row;
  - the level, "No bulletin" for no row and ``no_rating``;
  - the trend: up, down and same, subdivisions ignored, none when either
    day is missing;
  - the resorts' weather and which point it is from; regions borrow none;
  - the order: alphabetical, regions and the resorts in each, never by
    danger;
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
from apps.favourites.today import (
    DOWN,
    SAME,
    UP,
    TodayRegion,
    build_today,
    today_region_pks,
    today_subjects,
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
    """Return a user's favourites joined the way the ``today`` view joins them."""
    return list(
        Favourite.objects.filter(user=user).select_related(
            "region", "resort", "resort__region", "resort__location"
        )
    )


def _build(user: User) -> list[TodayRegion]:
    """Build the rows for a user, today, reading ratings as the view does."""
    day = timezone.localdate()
    subjects = today_subjects(_favourites(user))
    ratings = {
        r.region_id: r
        for r in RegionDayRating.objects.filter(
            region_id__in=today_region_pks(subjects), date=day
        )
    }
    return build_today(subjects, day, ratings)


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
class TestWhatIsListed:
    """Region pins and saved resorts; never a dropped pin."""

    def test_a_dropped_pin_is_not_listed(self) -> None:
        user = UserFactory.create()
        FavouriteFactory.create(
            user=user, name="Car park", region=MicroRegionFactory.create()
        )
        assert _build(user) == []

    def test_a_region_pin_is_a_pinned_region_with_no_resorts(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        [row] = _build(user)
        assert row.name == "Martigny-Verbier"
        assert row.region_id == region.region_id
        assert row.url == region.get_absolute_url()
        assert row.pinned is True
        assert row.resorts == []

    def test_a_resort_is_nested_under_its_resorts_region(self) -> None:
        """The resort's own region, whatever the favourite's region says."""
        user = UserFactory.create()
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        FavouriteFactory.create(
            user=user,
            name="my label",
            region=MicroRegionFactory.create(name="Elsewhere"),
            resort=ResortFactory.create(name="Verbier", region=region),
        )

        [row] = _build(user)
        assert row.name == "Martigny-Verbier"
        assert row.pinned is False
        assert [r.name for r in row.resorts] == ["Verbier"]

    def test_a_pinned_region_and_its_resort_are_one_row(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=user, region=region, region_pin=True)
        resort = ResortFactory.create(region=region)
        FavouriteFactory.create(user=user, region=region, resort=resort)

        [row] = _build(user)
        assert row.pinned is True
        assert [r.slug for r in row.resorts] == [resort.slug]
        assert row.resorts[0].url == resort.get_absolute_url()


@pytest.mark.django_db
class TestLevel:
    """Today's level, or no bulletin."""

    def test_the_level_and_its_subdivision(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        _rate(region, R.CONSIDERABLE, max_subdivision="+")
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        [row] = _build(user)
        assert row.level == R.CONSIDERABLE
        assert row.subdivision == "+"
        assert row.rating is not None

    @pytest.mark.parametrize("today", [None, R.NO_RATING])
    def test_no_rating_row_or_no_rating_is_no_bulletin(self, today: str | None) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        _rate(region, today, R.MODERATE)
        FavouriteFactory.create(user=user, region=region, region_pin=True)

        [row] = _build(user)
        assert row.level is None
        assert row.trend is None
        assert row.rating is None

    def test_the_trend_is_read_from_yesterday(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        _rate(region, R.HIGH, R.CONSIDERABLE)
        FavouriteFactory.create(user=user, region=region, region_pin=True)
        assert _build(user)[0].trend == UP


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
class TestWeather:
    """A resort's weather today; a region carries none."""

    def test_a_resort_reads_the_nearest_point(self) -> None:
        user = UserFactory.create()
        _public_point(46.10, 7.40, temp=-3.0)
        _public_point(47.50, 9.00, temp=12.0)
        FavouriteFactory.create(
            user=user,
            latitude=46.11,
            longitude=7.41,
            resort=ResortFactory.create(),
        )

        resort = _build(user)[0].resorts[0]
        assert resort.temp_max == -3.0
        assert resort.icon is not None and resort.icon.endswith(".svg")
        assert resort.source is not None
        assert resort.source.distance_km == pytest.approx(1.4, abs=0.1)

    def test_no_point_with_a_row_today_is_no_weather(self) -> None:
        user = UserFactory.create()
        FavouriteFactory.create(user=user, resort=ResortFactory.create())
        resort = _build(user)[0].resorts[0]
        assert resort.icon is None
        assert resort.temp_max is None
        assert resort.source is None


@pytest.mark.django_db
class TestOrder:
    """Alphabetical, never by danger (Hugo, 2 Nov 2026)."""

    def test_regions_by_name_whatever_their_level(self) -> None:
        user = UserFactory.create()
        for name, level in [
            ("Zermatt", R.HIGH),
            ("arolla", R.LOW),
            ("Bex", None),
            ("Champéry", R.CONSIDERABLE),
        ]:
            region = MicroRegionFactory.create(name=name)
            _rate(region, level)
            FavouriteFactory.create(user=user, region=region, region_pin=True)

        assert [row.name for row in _build(user)] == [
            "arolla",
            "Bex",
            "Champéry",
            "Zermatt",
        ]

    def test_resorts_by_name_within_their_region(self) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        for name in ["Verbier", "Bruson", "la Tzoumaz"]:
            FavouriteFactory.create(
                user=user, resort=ResortFactory.create(name=name, region=region)
            )

        assert [r.name for r in _build(user)[0].resorts] == [
            "Bruson",
            "la Tzoumaz",
            "Verbier",
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
                user=user,
                region=region,
                resort=ResortFactory.create(region=region),
                latitude=46.0,
                longitude=7.0,
            )
            FavouriteFactory.create(user=user, region=region, region_pin=True)
            FavouriteFactory.create(user=user, region=region)  # a pin: ignored

    @pytest.mark.parametrize("n", [1, 4])
    def test_six_queries_once_the_favourites_and_todays_ratings_are_read(
        self, n: int
    ) -> None:
        """Yesterday's ratings, then ``nearest_forecasts``' five."""
        user = UserFactory.create()
        self._place_everything(user, n)
        favourites = _favourites(user)
        day = timezone.localdate()
        ratings = {r.region_id: r for r in RegionDayRating.objects.filter(date=day)}

        with CaptureQueriesContext(connection) as queries:
            rows = build_today(favourites, day, ratings)

        assert len(rows) == n
        assert all(len(row.resorts) == 1 for row in rows)
        assert len(queries) == 6
