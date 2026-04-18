"""
tests/pipeline/models/test_models.py — Tests for the Bulletin model
helpers that surface CAAML schema dataclasses from the raw_data payload.
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from pipeline.models import Bulletin
from pipeline.schema import AvalancheProblem, DangerRating, Elevation
from tests.factories import BulletinFactory


def _wrap(properties: dict[str, Any]) -> dict[str, Any]:
    """Wrap a CAAML properties dict in a GeoJSON Feature envelope."""
    return {"type": "Feature", "geometry": None, "properties": properties}


@pytest.mark.django_db
class TestBulletinRegionCount:
    """Tests for Bulletin.region_count()."""

    def test_returns_zero_when_raw_data_empty(self):
        """An empty raw_data dict yields zero regions."""
        bulletin = BulletinFactory.create(raw_data={})
        assert bulletin.region_count() == 0

    def test_returns_zero_when_no_regions(self):
        """Properties without a regions key yield zero regions."""
        bulletin = BulletinFactory.create(raw_data=_wrap({}))
        assert bulletin.region_count() == 0

    def test_counts_regions(self):
        """Returns the length of the regions list."""
        bulletin = BulletinFactory.create(
            raw_data=_wrap(
                {
                    "regions": [
                        {"regionID": "CH-1", "name": "A"},
                        {"regionID": "CH-2", "name": "B"},
                        {"regionID": "CH-3", "name": "C"},
                    ]
                }
            )
        )
        assert bulletin.region_count() == 3


@pytest.mark.django_db
class TestBulletinGetDangerRatings:
    """Tests for Bulletin.get_danger_ratings()."""

    def test_empty_when_field_missing(self):
        """Returns an empty list when dangerRatings is absent."""
        bulletin = BulletinFactory.create(raw_data=_wrap({}))
        assert bulletin.get_danger_ratings() == []

    def test_returns_dataclass_instances(self):
        """Each entry is converted into a DangerRating dataclass."""
        bulletin = BulletinFactory.create(
            raw_data=_wrap(
                {
                    "dangerRatings": [
                        {"mainValue": "low", "validTimePeriod": "all_day"},
                        {
                            "mainValue": "considerable",
                            "validTimePeriod": "later",
                            "elevation": {"lowerBound": "2000"},
                            "aspects": ["N", "NE"],
                        },
                    ]
                }
            )
        )

        ratings = bulletin.get_danger_ratings()

        assert len(ratings) == 2
        assert all(isinstance(r, DangerRating) for r in ratings)
        assert ratings[0] == DangerRating(
            main_value="low",
            valid_time_period="all_day",
        )
        assert ratings[1] == DangerRating(
            main_value="considerable",
            valid_time_period="later",
            elevation=Elevation(lower_bound="2000"),
            aspects=("N", "NE"),
        )


@pytest.mark.django_db
class TestBulletinGetAvalancheProblems:
    """Tests for Bulletin.get_avalanche_problems()."""

    def test_empty_when_field_missing(self):
        """Returns an empty list when avalancheProblems is absent."""
        bulletin = BulletinFactory.create(raw_data=_wrap({}))
        assert bulletin.get_avalanche_problems() == []

    def test_returns_dataclass_instances(self):
        """Each entry is converted into an AvalancheProblem dataclass."""
        bulletin = BulletinFactory.create(
            raw_data=_wrap(
                {
                    "avalancheProblems": [
                        {
                            "problemType": "wet_snow",
                            "comment": "Wet avalanches expected.",
                            "dangerRatingValue": "considerable",
                            "validTimePeriod": "later",
                        },
                        {
                            "problemType": "wind_slab",
                            "elevation": {"lowerBound": "2200"},
                            "aspects": ["N", "NW"],
                            "avalancheSize": 2,
                            "snowpackStability": "poor",
                            "frequency": "some",
                        },
                    ]
                }
            )
        )

        problems = bulletin.get_avalanche_problems()

        assert len(problems) == 2
        assert all(isinstance(p, AvalancheProblem) for p in problems)
        assert problems[0].problem_type == "wet_snow"
        assert problems[0].comment == "Wet avalanches expected."
        assert problems[0].danger_rating_value == "considerable"
        assert problems[0].valid_time_period == "later"
        assert problems[1].problem_type == "wind_slab"
        assert problems[1].elevation == Elevation(lower_bound="2200")
        assert problems[1].aspects == ("N", "NW")
        assert problems[1].avalanche_size == 2
        assert problems[1].snowpack_stability == "poor"
        assert problems[1].frequency == "some"


@pytest.mark.django_db
class TestBulletinHighestDangerRating:
    """Tests for the existing highest_danger_rating() helper after refactor."""

    def test_returns_main_values_in_order(self):
        """Returns the mainValue strings via the dataclass helper."""
        bulletin = BulletinFactory.create(
            raw_data=_wrap(
                {
                    "dangerRatings": [
                        {"mainValue": "low"},
                        {"mainValue": "considerable"},
                    ]
                }
            )
        )
        assert bulletin.highest_danger_rating() == ["low", "considerable"]

    def test_empty_when_no_ratings(self):
        """Returns an empty list when no ratings are present."""
        bulletin = BulletinFactory.create(raw_data=_wrap({}))
        assert bulletin.highest_danger_rating() == []


@pytest.mark.django_db
class TestBulletinLatestValidFromDate:
    """Tests for ``BulletinQuerySet.latest_valid_from_date()``."""

    def test_returns_none_when_empty(self) -> None:
        """Empty queryset returns ``None`` rather than raising."""
        assert Bulletin.objects.latest_valid_from_date() is None

    def test_returns_max_valid_from_day(self) -> None:
        """Returns the ``valid_from`` day of the newest bulletin."""
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 17, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 12, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 12, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        )

        assert Bulletin.objects.latest_valid_from_date() == date(2026, 4, 15)

    def test_honours_queryset_filters(self) -> None:
        """Works off the chained queryset rather than the full table."""
        BulletinFactory.create(
            lang="en",
            issued_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            lang="de",
            issued_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        )

        assert Bulletin.objects.filter(lang="en").latest_valid_from_date() == date(
            2026, 4, 15
        )
