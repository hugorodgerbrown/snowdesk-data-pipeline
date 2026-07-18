"""
tests/mcp_server/test_tools.py — Tests for mcp_server.tools.

One test class per tool: ``search_regions``, ``get_current_conditions``,
``get_avalanche_problems``, ``get_danger_history``, ``list_resorts_in_region``.
Each tool's business logic is exercised directly (not through the JSON-RPC
``arguments`` dict adapters) so date/``today`` seams stay explicit and
readable.
"""

from __future__ import annotations

import datetime
from datetime import UTC
from typing import Any

import pytest
from django.core.cache import cache

from bulletins.models import RegionDayRating
from mcp_server import tools
from regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
    SubRegionFactory,
)


@pytest.fixture(autouse=True)
def clear_mcp_candidate_cache() -> None:
    """Ensure the resolvers candidate-pool cache is clear before every test."""
    cache.clear()


@pytest.fixture
def region() -> MicroRegion:
    """A single MicroRegion fixture shared by most tool tests."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_en="Valais")
    sub = SubRegionFactory.create(prefix="CH-41", major=major)
    return MicroRegionFactory.create(
        region_id="CH-4115", name="Bas-Valais", subregion=sub
    )


def _make_bulletin(
    region: MicroRegion,
    target_date: datetime.date,
    danger_key: str = "considerable",
    avalanche_problems: list[dict[str, Any]] | None = None,
) -> None:
    """Create a Bulletin covering ``target_date`` for ``region``.

    ``avalanche_problems``, when given, is injected verbatim into
    ``raw_data["properties"]["avalancheProblems"]`` — the caller supplies
    CAAML-shaped problem dicts (SLF/ALBINA/MF field names) so tests can
    exercise ``Bulletin.get_avalanche_problems()``'s ``from_dict`` mapping.
    The key is always written (``None`` and ``[]`` both store ``[]``);
    ``Bulletin.get_avalanche_problems()`` treats absent-key and empty-list
    identically anyway.
    """
    bulletin = BulletinFactory.create(
        issued_at=datetime.datetime.combine(
            target_date, datetime.time(17, 0), tzinfo=UTC
        ),
        valid_from=datetime.datetime.combine(
            target_date, datetime.time(8, 0), tzinfo=UTC
        ),
        valid_to=datetime.datetime.combine(
            target_date, datetime.time(17, 0), tzinfo=UTC
        ),
        raw_data={
            "properties": {
                "dangerRatings": [{"mainValue": danger_key}],
                "avalancheProblems": avalanche_problems or [],
            }
        },
        render_model={
            "version": 5,
            "danger": {
                "key": danger_key,
                "number": "3",
                "subdivision": None,
                "ratings": [],
            },
            "prose": {
                "snowpack_structure": "A weak layer persists near the ground.",
                "weather_review": "Cloudy overnight.",
                "weather_forecast": "Sunny with light winds.",
                "tendency": [],
                "avalanche_activity": {"highlights": "", "comment": ""},
                "tendency_lead": None,
            },
        },
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)


# ---------------------------------------------------------------------------
# search_regions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSearchRegions:
    """Tests for tools.search_regions."""

    def test_finds_a_resort_by_name(self, region: MicroRegion) -> None:
        """A resort name resolves to its parent region_id."""
        ResortFactory.create(name="Verbier", region=region)
        result = tools.search_regions("Verbier")
        assert result["count"] == 1
        assert result["results"][0]["region_id"] == region.region_id
        assert "Verbier" in result["summary"]

    def test_no_match_returns_empty_results(self, region: MicroRegion) -> None:
        """An unmatched query returns an empty, non-error result."""
        result = tools.search_regions("Nonexistent Place")
        assert result["results"] == []
        assert result["count"] == 0
        assert "No regions" in result["summary"]


# ---------------------------------------------------------------------------
# get_current_conditions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetCurrentConditions:
    """Tests for tools.get_current_conditions."""

    def test_happy_path_returns_danger_and_prose(self, region: MicroRegion) -> None:
        """A region with a bulletin on the requested day returns full data."""
        target_date = datetime.date(2026, 4, 8)
        _make_bulletin(region, target_date, danger_key="considerable")

        result = tools.get_current_conditions(region.region_id, target_date)

        assert result["has_bulletin"] is True
        assert result["region_id"] == region.region_id
        assert result["date"] == "2026-04-08"
        assert result["danger_level"] == "considerable"
        assert result["danger_ratings"] == ["considerable"]
        assert result["prose"]["snowpack_structure"]
        assert result["prose"]["weather_forecast"]
        # weather_review / tendency_lead are deliberately excluded from the
        # tool's prose subset (plan: snowpack_structure, weather_forecast,
        # avalanche_activity, tendency only).
        assert set(result["prose"]) == {
            "snowpack_structure",
            "weather_forecast",
            "avalanche_activity",
        }
        assert "considerable" in result["summary"]

    def test_no_bulletin_for_date_is_a_structured_empty_result(
        self, region: MicroRegion
    ) -> None:
        """A quiet day with no bulletin is a normal result, not an error."""
        result = tools.get_current_conditions(
            region.region_id, datetime.date(2026, 4, 8)
        )
        assert result["has_bulletin"] is False
        assert "No bulletin" in result["summary"]

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError rather than a bare exception."""
        with pytest.raises(tools.ToolError):
            tools.get_current_conditions("XX-0000", datetime.date(2026, 4, 8))

    def test_defaults_to_today_seam_when_date_omitted(
        self, region: MicroRegion
    ) -> None:
        """Omitting 'date' falls back to the injected 'today' seam."""
        fixed_today = datetime.date(2026, 4, 8)
        _make_bulletin(region, fixed_today, danger_key="high")

        result = tools.get_current_conditions(region.region_id, today=fixed_today)

        assert result["has_bulletin"] is True
        assert result["date"] == "2026-04-08"


# ---------------------------------------------------------------------------
# get_avalanche_problems
# ---------------------------------------------------------------------------

_CURATED_PROBLEM_FIELDS = {
    "problem_type",
    "danger_rating_value",
    "valid_time_period",
    "elevation",
    "aspects",
    "comment",
    "avalanche_size",
}


@pytest.mark.django_db
class TestGetAvalancheProblems:
    """Tests for tools.get_avalanche_problems."""

    def test_happy_path_serialises_slf_shaped_problems(
        self, region: MicroRegion
    ) -> None:
        """Two SLF-shaped problems serialise to the curated field subset."""
        target_date = datetime.date(2026, 4, 8)
        _make_bulletin(
            region,
            target_date,
            avalanche_problems=[
                {
                    "problemType": "persistent_weak_layers",
                    "dangerRatingValue": "considerable",
                    "validTimePeriod": "all_day",
                    "elevation": {"lowerBound": "2200"},
                    "aspects": ["N", "NE", "E", "NW"],
                    "comment": "Distinct weak layers persist near the ground.",
                },
                {
                    "problemType": "wet_snow",
                    "dangerRatingValue": "moderate",
                    "validTimePeriod": "earlier",
                    "elevation": {"upperBound": "2600"},
                    "aspects": ["S", "SW"],
                    "comment": "Wet snow avalanches possible on sunny slopes.",
                },
            ],
        )

        result = tools.get_avalanche_problems(region.region_id, target_date)

        assert result["has_bulletin"] is True
        assert result["region_id"] == region.region_id
        assert result["date"] == "2026-04-08"
        assert result["count"] == 2

        first, second = result["problems"]
        assert set(first) == _CURATED_PROBLEM_FIELDS
        assert first["problem_type"] == "persistent_weak_layers"
        assert first["danger_rating_value"] == "considerable"
        assert first["valid_time_period"] == "all_day"
        assert first["elevation"] == {"lower_bound": "2200", "upper_bound": None}
        assert first["aspects"] == ["N", "NE", "E", "NW"]
        assert isinstance(first["aspects"], list)
        assert first["comment"] == "Distinct weak layers persist near the ground."
        assert first["avalanche_size"] is None

        assert second["problem_type"] == "wet_snow"
        assert second["elevation"] == {"lower_bound": None, "upper_bound": "2600"}

        assert "persistent_weak_layers" in result["summary"]
        assert "considerable" in result["summary"]
        assert "2 avalanche problems" in result["summary"]

    def test_serialises_albina_shaped_problem_and_ignores_custom_data(
        self, region: MicroRegion
    ) -> None:
        """An ALBINA problem's avalancheSize is kept; customData is dropped."""
        target_date = datetime.date(2026, 4, 8)
        _make_bulletin(
            region,
            target_date,
            avalanche_problems=[
                {
                    "problemType": "gliding_snow",
                    "elevation": {"upperBound": "2400"},
                    "avalancheSize": 2,
                    "customData": {"ALBINA": {"avalancheType": "glide"}},
                },
            ],
        )

        result = tools.get_avalanche_problems(region.region_id, target_date)

        assert result["count"] == 1
        problem = result["problems"][0]
        assert set(problem) == _CURATED_PROBLEM_FIELDS
        assert problem["problem_type"] == "gliding_snow"
        assert problem["avalanche_size"] == 2
        assert problem["elevation"] == {"lower_bound": None, "upper_bound": "2400"}

    def test_serialises_minimal_meteofrance_shaped_problem(
        self, region: MicroRegion
    ) -> None:
        """A minimal MF problem with no elevation still serialises cleanly."""
        target_date = datetime.date(2026, 4, 8)
        _make_bulletin(
            region,
            target_date,
            avalanche_problems=[
                {
                    "problemType": "wet_snow",
                    "aspects": ["S"],
                    "validTimePeriod": "later",
                },
            ],
        )

        result = tools.get_avalanche_problems(region.region_id, target_date)

        assert result["count"] == 1
        problem = result["problems"][0]
        assert set(problem) == _CURATED_PROBLEM_FIELDS
        assert problem["problem_type"] == "wet_snow"
        assert problem["valid_time_period"] == "later"
        assert problem["aspects"] == ["S"]
        assert problem["elevation"] is None
        assert problem["comment"] is None
        assert problem["danger_rating_value"] is None
        assert problem["avalanche_size"] is None

    def test_no_bulletin_for_date_is_a_structured_empty_result(
        self, region: MicroRegion
    ) -> None:
        """A quiet day with no bulletin is a normal result, not an error."""
        result = tools.get_avalanche_problems(
            region.region_id, datetime.date(2026, 4, 8)
        )
        assert result["has_bulletin"] is False
        assert result["problems"] == []
        assert result["count"] == 0
        assert "No bulletin" in result["summary"]

    def test_bulletin_with_no_problems_is_distinct_from_no_bulletin(
        self, region: MicroRegion
    ) -> None:
        """A bulletin with an empty avalancheProblems array is not an error.

        Distinguished from the no-bulletin case by ``has_bulletin: True``
        and different summary wording.
        """
        target_date = datetime.date(2026, 4, 8)
        _make_bulletin(region, target_date, avalanche_problems=[])

        result = tools.get_avalanche_problems(region.region_id, target_date)

        assert result["has_bulletin"] is True
        assert result["problems"] == []
        assert result["count"] == 0
        assert "No bulletin" not in result["summary"]
        assert "0 avalanche problems" in result["summary"]

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError rather than a bare exception."""
        with pytest.raises(tools.ToolError):
            tools.get_avalanche_problems("XX-0000", datetime.date(2026, 4, 8))

    def test_defaults_to_today_seam_when_date_omitted(
        self, region: MicroRegion
    ) -> None:
        """Omitting 'date' falls back to the injected 'today' seam."""
        fixed_today = datetime.date(2026, 4, 8)
        _make_bulletin(
            region,
            fixed_today,
            avalanche_problems=[{"problemType": "wind_slab", "aspects": ["N"]}],
        )

        result = tools.get_avalanche_problems(region.region_id, today=fixed_today)

        assert result["has_bulletin"] is True
        assert result["date"] == "2026-04-08"
        assert result["count"] == 1


# ---------------------------------------------------------------------------
# get_danger_history
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetDangerHistory:
    """Tests for tools.get_danger_history."""

    def test_range_fully_inside_season_is_not_clamped(
        self, region: MicroRegion
    ) -> None:
        """A request wholly inside the season window is returned unclamped."""
        for day, rating in (
            (10, RegionDayRating.Rating.LOW),
            (11, RegionDayRating.Rating.MODERATE),
            (12, RegionDayRating.Rating.HIGH),
        ):
            RegionDayRatingFactory.create(
                region=region,
                date=datetime.date(2026, 1, day),
                max_rating=rating,
            )

        result = tools.get_danger_history(
            region.region_id,
            datetime.date(2026, 1, 10),
            datetime.date(2026, 1, 12),
            today=datetime.date(2026, 2, 1),
        )

        assert result["clamped"] is False
        assert result["effective_from"] == "2026-01-10"
        assert result["effective_to"] == "2026-01-12"
        assert result["count"] == 3
        assert [d["max_rating"] for d in result["days"]] == [
            "low",
            "moderate",
            "high",
        ]

    def test_range_straddling_season_start_is_clamped(
        self, region: MicroRegion
    ) -> None:
        """A request starting before the season is clamped to season_start."""
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2025, 11, 2),
            max_rating=RegionDayRating.Rating.MODERATE,
        )

        result = tools.get_danger_history(
            region.region_id,
            datetime.date(2025, 10, 25),
            datetime.date(2025, 11, 5),
            today=datetime.date(2025, 11, 10),
        )

        assert result["clamped"] is True
        assert result["effective_from"] == "2025-11-01"
        assert result["season_start"] == "2025-11-01"
        assert result["season_end"] == "2026-05-31"

    def test_range_entirely_outside_season_returns_empty_days(
        self, region: MicroRegion
    ) -> None:
        """A range entirely outside the season window is a successful empty result."""
        result = tools.get_danger_history(
            region.region_id,
            datetime.date(2026, 6, 1),
            datetime.date(2026, 6, 10),
            today=datetime.date(2026, 2, 1),
        )

        assert result["days"] == []
        assert result["clamped"] is True
        assert result["count"] == 0

    def test_min_rating_adds_a_qualifying_day_count(self, region: MicroRegion) -> None:
        """min_rating adds a count of days at or above the threshold.

        The full ``days`` list is unfiltered (every day in the effective
        range is still returned) — ``min_rating`` narrows the *count*
        metric, not the returned rows, so a caller can see both the full
        picture and the qualifying-day count in one call.
        """
        for day, rating in (
            (10, RegionDayRating.Rating.LOW),
            (11, RegionDayRating.Rating.HIGH),
            (12, RegionDayRating.Rating.VERY_HIGH),
        ):
            RegionDayRatingFactory.create(
                region=region,
                date=datetime.date(2026, 1, day),
                max_rating=rating,
            )

        result = tools.get_danger_history(
            region.region_id,
            datetime.date(2026, 1, 10),
            datetime.date(2026, 1, 12),
            min_rating=RegionDayRating.Rating.HIGH,
            today=datetime.date(2026, 2, 1),
        )

        assert result["count"] == 3
        assert result["count_at_or_above_min_rating"] == 2
        assert "2 day(s)" in result["summary"]

    def test_invalid_min_rating_raises_tool_error(self, region: MicroRegion) -> None:
        """An unrecognised min_rating value raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_danger_history(
                region.region_id,
                datetime.date(2026, 1, 1),
                datetime.date(2026, 1, 2),
                min_rating="extreme",
                today=datetime.date(2026, 2, 1),
            )

    def test_no_rating_min_rating_raises_tool_error(self, region: MicroRegion) -> None:
        """min_rating='no_rating' is rejected, not silently accepted.

        'At or above no_rating' is a null constraint that would match every
        day — the schema description only ever advertised the five real
        ratings (low..very_high), so the value is treated as invalid rather
        than silently matching everything.
        """
        with pytest.raises(tools.ToolError):
            tools.get_danger_history(
                region.region_id,
                datetime.date(2026, 1, 1),
                datetime.date(2026, 1, 2),
                min_rating=RegionDayRating.Rating.NO_RATING,
                today=datetime.date(2026, 2, 1),
            )

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_danger_history(
                "XX-0000",
                datetime.date(2026, 1, 1),
                datetime.date(2026, 1, 2),
                today=datetime.date(2026, 2, 1),
            )


# ---------------------------------------------------------------------------
# list_resorts_in_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListResortsInRegion:
    """Tests for tools.list_resorts_in_region."""

    def test_lists_only_geocoded_resorts(self, region: MicroRegion) -> None:
        """Resorts without coordinates are excluded, matching /api/resorts-by-region/."""
        ResortFactory.create(
            name="Verbier", region=region, latitude=46.0961, longitude=7.2286
        )
        ResortFactory.create(name="Ungeocoded Resort", region=region)

        result = tools.list_resorts_in_region(region.region_id)

        assert result["count"] == 1
        assert result["resorts"][0]["name"] == "Verbier"
        assert result["resorts"][0]["latitude"] == 46.0961

    def test_no_resorts_returns_empty_list(self, region: MicroRegion) -> None:
        """A region with no resorts returns an empty, non-error result."""
        result = tools.list_resorts_in_region(region.region_id)
        assert result["resorts"] == []
        assert result["count"] == 0
        assert "No geocoded resorts" in result["summary"]

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.list_resorts_in_region("XX-0000")
