"""
tests/pipeline/models/test_models.py — Tests for the Bulletin model
helpers that surface CAAML schema dataclasses from the raw_data payload.
"""

from typing import Any

import pytest

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
        bulletin = BulletinFactory(raw_data={})
        assert bulletin.region_count() == 0

    def test_returns_zero_when_no_regions(self):
        """Properties without a regions key yield zero regions."""
        bulletin = BulletinFactory(raw_data=_wrap({}))
        assert bulletin.region_count() == 0

    def test_counts_regions(self):
        """Returns the length of the regions list."""
        bulletin = BulletinFactory(
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
        bulletin = BulletinFactory(raw_data=_wrap({}))
        assert bulletin.get_danger_ratings() == []

    def test_returns_dataclass_instances(self):
        """Each entry is converted into a DangerRating dataclass."""
        bulletin = BulletinFactory(
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
        bulletin = BulletinFactory(raw_data=_wrap({}))
        assert bulletin.get_avalanche_problems() == []

    def test_returns_dataclass_instances(self):
        """Each entry is converted into an AvalancheProblem dataclass."""
        bulletin = BulletinFactory(
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
        bulletin = BulletinFactory(
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
        bulletin = BulletinFactory(raw_data=_wrap({}))
        assert bulletin.highest_danger_rating() == []
