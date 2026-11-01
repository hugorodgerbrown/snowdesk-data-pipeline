"""
tests/public/test_daily_read.py — Tests for apps/public/daily_read.py, the
homepage's daily read (SNOW-1357).

Covers:
  - Movement: rose / fell / held over regions rated on both days, a region
    rated only today not counted.
  - The no-rating rule: a NO_RATING row on either day takes no part in
    movement, the swing or the counts; a country with only NO_RATING rows is
    left out; no rated row at all is no read.
  - The peak level and its count.
  - The problem named most, counted per region (a grouped bulletin over
    three regions names its problem three times), and its tie-break.
  - The biggest swing and its tie-breaks, none when nothing moved.
  - Country order, a fixed query count, the cache, and the wording.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core.cache import cache

from apps.bulletins.models import RegionDayRating
from apps.public.daily_read import (
    CountryRead,
    DailyRead,
    build_daily_read,
    cached_daily_read,
    daily_read_sentences,
    present_daily_read,
)
from tests.factories import (
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    SubRegionFactory,
)

pytestmark = pytest.mark.django_db

DAY = datetime.date(2026, 1, 14)
YESTERDAY = DAY - datetime.timedelta(days=1)
R = RegionDayRating.Rating


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Start every test with an empty cache."""
    cache.clear()


def region(code: str, country: str = "CH", name: str | None = None) -> Any:
    """Return a MicroRegion in ``country``."""
    major = MajorRegionFactory.create(prefix=f"{country}-9", country=country)
    sub = SubRegionFactory.create(prefix=f"{country}-91", major=major)
    return MicroRegionFactory.create(
        region_id=code, name=name or f"Region {code}", subregion=sub
    )


def model(*problems: str) -> dict[str, Any]:
    """Return a render model naming ``problems``."""
    return {
        "danger": {"key": "considerable"},
        "traits": [{"problems": [{"problem_type": p} for p in problems]}],
    }


def rate(
    place: Any, day: datetime.date, level: str, bulletin: Any = None
) -> RegionDayRating:
    """Rate ``place`` at ``level`` on ``day``."""
    return RegionDayRatingFactory.create(
        region=place, date=day, max_rating=level, source_bulletin=bulletin
    )


def only(read: DailyRead | None) -> CountryRead:
    """Return the read's one country."""
    assert read is not None
    assert len(read.countries) == 1
    return read.countries[0]


class TestMovement:
    """Rose, fell and held, over regions rated on both days."""

    def test_counts_rose_fell_and_held(self) -> None:
        """Each region rated on both days lands in exactly one count."""
        up, down, same, new = (region(f"CH-10{i}") for i in range(4))
        rate(up, YESTERDAY, R.MODERATE)
        rate(up, DAY, R.CONSIDERABLE)
        rate(down, YESTERDAY, R.HIGH)
        rate(down, DAY, R.MODERATE)
        rate(same, YESTERDAY, R.LOW)
        rate(same, DAY, R.LOW)
        rate(new, DAY, R.HIGH)

        c = only(build_daily_read(DAY))

        assert (c.rose, c.fell, c.held) == (1, 1, 1)
        assert c.compared == 3
        assert c.rated_count == 4

    def test_no_rating_takes_part_in_nothing(self) -> None:
        """A NO_RATING row on either day is neither moved nor counted."""
        unrated_before, unrated_today, rated = (region(f"CH-11{i}") for i in range(3))
        rate(unrated_before, YESTERDAY, R.NO_RATING)
        rate(unrated_before, DAY, R.HIGH)
        rate(unrated_today, YESTERDAY, R.LOW)
        rate(unrated_today, DAY, R.NO_RATING)
        rate(rated, YESTERDAY, R.LOW)
        rate(rated, DAY, R.LOW)

        c = only(build_daily_read(DAY))

        assert (c.rose, c.fell, c.held) == (0, 0, 1)
        assert c.rated_count == 2
        assert c.swing is None

    def test_a_country_with_only_no_rating_is_left_out(self) -> None:
        """France unrated, Switzerland rated: only Switzerland is read."""
        rate(region("FR-01", "FR"), DAY, R.NO_RATING)
        rate(region("CH-1201"), DAY, R.LOW)

        read = build_daily_read(DAY)

        assert read is not None
        assert [c.country for c in read.countries] == ["CH"]

    def test_no_rated_region_is_no_read(self) -> None:
        """Off season: only NO_RATING rows, or none, gives None."""
        assert build_daily_read(DAY) is None
        rate(region("CH-1301"), DAY, R.NO_RATING)
        assert build_daily_read(DAY) is None


class TestPeakAndProblem:
    """Today's highest level and the problem named most."""

    def test_peak_level_and_its_count(self) -> None:
        """The highest max_rating and how many regions share it."""
        for i, level in enumerate((R.LOW, R.CONSIDERABLE, R.CONSIDERABLE)):
            rate(region(f"CH-14{i}"), DAY, level)

        c = only(build_daily_read(DAY))

        assert (c.peak_level, c.peak_count) == (R.CONSIDERABLE, 2)

    def test_a_grouped_bulletin_counts_once_per_region(self) -> None:
        """One bulletin over three regions names its problem three times."""
        grouped = BulletinFactory.create(render_model=model("wind_slab"))
        lone = BulletinFactory.create(render_model=model("new_snow", "wind_slab"))
        for i in range(3):
            rate(region(f"CH-15{i}"), DAY, R.MODERATE, grouped)
        rate(region("CH-1590"), DAY, R.MODERATE, lone)

        c = only(build_daily_read(DAY))

        assert (c.top_problem, c.top_problem_count) == ("wind_slab", 4)

    def test_a_tie_goes_to_the_problem_code(self) -> None:
        """Two problems each named in two regions: the lower code wins."""
        a = BulletinFactory.create(render_model=model("wind_slab"))
        b = BulletinFactory.create(render_model=model("new_snow"))
        for i, bulletin in enumerate((a, a, b, b)):
            rate(region(f"CH-16{i}"), DAY, R.MODERATE, bulletin)

        c = only(build_daily_read(DAY))

        assert (c.top_problem, c.top_problem_count) == ("new_snow", 2)

    def test_no_problem_without_a_source_bulletin(self) -> None:
        """Rows with no source bulletin name no problem."""
        rate(region("CH-1701"), DAY, R.LOW)

        c = only(build_daily_read(DAY))

        assert c.top_problem is None


class TestSwing:
    """The region whose peak moved furthest."""

    def test_the_furthest_move_wins(self) -> None:
        """A two-level rise beats a one-level fall; it links to the day."""
        far = region("CH-1801", name="Martigny – Verbier")
        near = region("CH-1802")
        rate(far, YESTERDAY, R.MODERATE)
        rate(far, DAY, R.VERY_HIGH)
        rate(near, YESTERDAY, R.HIGH)
        rate(near, DAY, R.CONSIDERABLE)

        swing = only(build_daily_read(DAY)).swing

        assert swing is not None
        assert (swing.region_code, swing.before, swing.after) == (
            "CH-1801",
            R.MODERATE,
            R.VERY_HIGH,
        )
        assert swing.url == far.get_absolute_url(DAY)

    def test_ties_go_to_the_higher_level_then_the_code(self) -> None:
        """Equal moves: the higher level today, then the lower code."""
        low_b, high_b, high_a = region("CH-1903"), region("CH-1902"), region("CH-1901")
        rate(low_b, YESTERDAY, R.LOW)
        rate(low_b, DAY, R.MODERATE)
        for place in (high_b, high_a):
            rate(place, YESTERDAY, R.CONSIDERABLE)
            rate(place, DAY, R.HIGH)

        swing = only(build_daily_read(DAY)).swing

        assert swing is not None
        assert swing.region_code == "CH-1901"

    def test_no_swing_when_nothing_moved(self) -> None:
        """Every region held: no swing."""
        place = region("CH-2001")
        rate(place, YESTERDAY, R.LOW)
        rate(place, DAY, R.LOW)

        assert only(build_daily_read(DAY)).swing is None


class TestCountriesQueriesAndCache:
    """Order, query count and caching."""

    def test_countries_in_the_fixed_order(self) -> None:
        """CH, FR, AT, IT first, then any other by code."""
        for code, country in (
            ("IT-01", "IT"),
            ("DE-01", "DE"),
            ("AT-01", "AT"),
            ("FR-01", "FR"),
            ("CH-2101", "CH"),
        ):
            rate(region(code, country), DAY, R.LOW)

        read = build_daily_read(DAY)

        assert read is not None
        assert [c.country for c in read.countries] == ["CH", "FR", "AT", "IT", "DE"]

    def test_a_fixed_query_count(self, django_assert_num_queries: Any) -> None:
        """Three queries for many regions and bulletins."""
        for i in range(6):
            place = region(f"CH-22{i}")
            bulletin = BulletinFactory.create(render_model=model("wind_slab"))
            rate(place, YESTERDAY, R.LOW)
            rate(place, DAY, R.MODERATE, bulletin)

        with django_assert_num_queries(3):
            build_daily_read(DAY)

    def test_the_cache_serves_a_second_read(
        self, django_assert_num_queries: Any
    ) -> None:
        """A read, or the lack of one, is built once per window."""
        rate(region("CH-2301"), DAY, R.LOW)
        first = cached_daily_read(DAY)
        with django_assert_num_queries(0):
            assert cached_daily_read(DAY) == first
        with django_assert_num_queries(1):
            assert cached_daily_read(YESTERDAY) is None
        with django_assert_num_queries(0):
            assert cached_daily_read(YESTERDAY) is None


def country(**kwargs: Any) -> CountryRead:
    """Return a CountryRead with quiet defaults."""
    values: dict[str, Any] = {
        "country": "CH",
        "rated_count": 40,
        "rose": 0,
        "fell": 0,
        "held": 0,
        "peak_level": R.CONSIDERABLE,
        "peak_count": 8,
        "top_problem": "wind_slab",
        "top_problem_count": 24,
        "swing": None,
    }
    values.update(kwargs)
    return CountryRead(**values)


class TestWording:
    """The sentences, in English."""

    @pytest.mark.parametrize(
        ("counts", "sentence"),
        [
            ((12, 3, 25), "Danger rose in 12 regions and fell in 3 since yesterday."),
            ((1, 1, 0), "Danger rose in 1 region and fell in 1 since yesterday."),
            ((0, 0, 40), "Danger held in every region since yesterday."),
            ((40, 0, 0), "Danger rose in all 40 regions since yesterday."),
            ((0, 40, 0), "Danger fell in all 40 regions since yesterday."),
            ((2, 0, 38), "Danger rose in 2 regions since yesterday and held in 38."),
            ((0, 1, 39), "Danger fell in 1 region since yesterday and held in 39."),
        ],
    )
    def test_movement(self, counts: tuple[int, int, int], sentence: str) -> None:
        """Each shape of movement reads as one sentence."""
        rose, fell, held = counts
        assert daily_read_sentences(country(rose=rose, fell=fell, held=held))[0] == (
            sentence
        )

    @pytest.mark.parametrize(
        ("counts", "sentence"),
        [
            ((0, 0, 1), "Danger held in the one region rated both days."),
            ((0, 0, 12), "Danger held in all 12 regions rated both days."),
            ((8, 0, 0), "Danger rose in all 8 regions rated both days."),
            ((0, 5, 0), "Danger fell in all 5 regions rated both days."),
            ((1, 0, 0), "Danger rose in the one region rated both days."),
            ((2, 0, 3), "Danger rose in 2 regions since yesterday and held in 3."),
        ],
    )
    def test_movement_when_only_some_regions_compare(
        self, counts: tuple[int, int, int], sentence: str
    ) -> None:
        """40 rated today, fewer yesterday: no claim reaches past those compared."""
        rose, fell, held = counts
        assert daily_read_sentences(country(rose=rose, fell=fell, held=held))[0] == (
            sentence
        )

    def test_peak_and_problem(self) -> None:
        """With nothing to compare, the peak and the problem are all."""
        assert daily_read_sentences(country()) == [
            "Highest today: Considerable, in 8 of 40 regions.",
            "Named most: wind slab, in 24 of 40 regions.",
        ]

    def test_singular_region(self) -> None:
        """One rated region reads in the singular."""
        assert daily_read_sentences(
            country(rated_count=1, peak_count=1, top_problem=None)
        ) == ["Highest today: Considerable, in 1 of 1 region."]

    def test_present_names_the_country_and_the_swing_levels(self) -> None:
        """present_daily_read words the swing's levels and the country."""
        place = region("CH-2401", name="Martigny – Verbier")
        rate(place, YESTERDAY, R.MODERATE)
        rate(place, DAY, R.HIGH)

        presented = present_daily_read(build_daily_read(DAY))

        assert presented is not None
        ch = presented["countries"][0]
        assert ch["label"] == "Switzerland"
        assert ch["swing"]["before"] == "Moderate"
        assert ch["swing"]["after"] == "High"
        assert present_daily_read(None) is None
