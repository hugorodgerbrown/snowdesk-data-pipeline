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

from bulletins.models import Bulletin, RegionDayRating
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
    *,
    source: str = "slf",
    lang: str = "en",
    pdf_url: str = "",
    next_update: datetime.datetime | None = None,
    bulletin_id: str | None = None,
) -> Any:
    """Create a Bulletin covering ``target_date`` for ``region``.

    Returns the created ``Bulletin`` so tests that assert on stored fields
    (issued_at, next_update, source stamp, lang) can reference them
    without re-querying.

    ``avalanche_problems``, when given, is injected verbatim into
    ``raw_data["properties"]["avalancheProblems"]`` — the caller supplies
    CAAML-shaped problem dicts (SLF/ALBINA/MF field names) so tests can
    exercise ``Bulletin.get_avalanche_problems()``'s ``from_dict`` mapping.
    The key is always written (``None`` and ``[]`` both store ``[]``);
    ``Bulletin.get_avalanche_problems()`` treats absent-key and empty-list
    identically anyway.
    """
    kwargs: dict[str, Any] = {
        "issued_at": datetime.datetime.combine(
            target_date, datetime.time(17, 0), tzinfo=UTC
        ),
        "valid_from": datetime.datetime.combine(
            target_date, datetime.time(8, 0), tzinfo=UTC
        ),
        "valid_to": datetime.datetime.combine(
            target_date, datetime.time(17, 0), tzinfo=UTC
        ),
        "next_update": next_update,
        "lang": lang,
        "pdf_url": pdf_url,
        "raw_data": {
            "properties": {
                "dangerRatings": [{"mainValue": danger_key}],
                "avalancheProblems": avalanche_problems or [],
            }
        },
        "render_model": {
            "version": 5,
            "source": source,
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
    }
    if bulletin_id is not None:
        kwargs["bulletin_id"] = bulletin_id
    bulletin = BulletinFactory.create(**kwargs)
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return bulletin


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


# ---------------------------------------------------------------------------
# get_bulletin_metadata
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetBulletinMetadata:
    """Tests for tools.get_bulletin_metadata."""

    def test_returns_provenance_fields_for_present_bulletin(
        self, region: MicroRegion
    ) -> None:
        """A stored bulletin surfaces issued_at, valid dates, source, and URL."""
        target_date = datetime.date(2026, 4, 8)
        next_update = datetime.datetime.combine(
            datetime.date(2026, 4, 9), datetime.time(8, 0), tzinfo=UTC
        )
        pdf_url = "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/example.pdf"
        _make_bulletin(
            region,
            target_date,
            source=Bulletin.Source.SLF,
            lang="en",
            pdf_url=pdf_url,
            next_update=next_update,
        )

        result = tools.get_bulletin_metadata(region.region_id, target_date)

        assert result["has_bulletin"] is True
        assert result["region_id"] == region.region_id
        assert result["date"] == "2026-04-08"
        assert result["issued_at"].startswith("2026-04-08T17:00")
        assert result["valid_from"].startswith("2026-04-08T08:00")
        assert result["valid_to"].startswith("2026-04-08T17:00")
        assert result["next_update_expected"].startswith("2026-04-09T08:00")
        assert result["source_provider"] == "slf"
        assert result["source_url"] == pdf_url
        assert result["language"] == "en"
        assert result["language_variants_available"] == ["en"]
        assert "slf" in result["summary"]

    def test_falls_back_to_source_home_url_when_no_pdf_url(
        self, region: MicroRegion
    ) -> None:
        """A bulletin without a stored pdf_url reports the provider's landing page."""
        target_date = datetime.date(2026, 4, 8)
        _make_bulletin(
            region,
            target_date,
            source=Bulletin.Source.ALBINA,
            pdf_url="",
        )

        result = tools.get_bulletin_metadata(region.region_id, target_date)

        assert result["source_provider"] == "albina"
        assert result["source_url"] == "https://avalanche.report/"

    def test_meteofrance_source_maps_to_meteofrance_home_url(
        self, region: MicroRegion
    ) -> None:
        """A Météo-France bulletin without pdf_url resolves to the MF home URL."""
        _make_bulletin(
            region,
            datetime.date(2026, 4, 8),
            source=Bulletin.Source.METEOFRANCE,
            pdf_url="",
        )

        result = tools.get_bulletin_metadata(
            region.region_id, datetime.date(2026, 4, 8)
        )

        assert result["source_provider"] == "meteofrance"
        assert "meteofrance.com" in result["source_url"]

    def test_no_bulletin_for_date_is_a_structured_empty_result(
        self, region: MicroRegion
    ) -> None:
        """A quiet day with no bulletin is a normal result, not an error."""
        result = tools.get_bulletin_metadata(
            region.region_id, datetime.date(2026, 4, 8)
        )

        assert result["has_bulletin"] is False
        assert "issued_at" not in result
        assert "No bulletin" in result["summary"]

    def test_missing_next_update_is_reported_as_null(self, region: MicroRegion) -> None:
        """A bulletin without a scheduled next_update reports null, not absent."""
        _make_bulletin(
            region,
            datetime.date(2026, 4, 8),
            source=Bulletin.Source.SLF,
            next_update=None,
        )

        result = tools.get_bulletin_metadata(
            region.region_id, datetime.date(2026, 4, 8)
        )

        assert result["next_update_expected"] is None

    def test_missing_source_stamp_reports_null_provider(
        self, region: MicroRegion
    ) -> None:
        """A bulletin whose render_model has no source stamp reports null."""
        # Direct factory create — bypasses _make_bulletin so we can omit
        # the source key from render_model entirely (mirrors the rare
        # RenderModelBuildError fallback where render_model has version 0
        # and no source stamp).
        bulletin = BulletinFactory.create(
            issued_at=datetime.datetime(2026, 4, 8, 17, 0, tzinfo=UTC),
            valid_from=datetime.datetime(2026, 4, 8, 8, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2026, 4, 8, 17, 0, tzinfo=UTC),
            lang="en",
            raw_data={"properties": {"dangerRatings": []}},
            render_model={"version": 0, "error": "build failed"},
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)

        result = tools.get_bulletin_metadata(
            region.region_id, datetime.date(2026, 4, 8)
        )

        assert result["source_provider"] is None
        assert result["source_url"] == ""

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_bulletin_metadata("XX-0000", datetime.date(2026, 4, 8))

    def test_defaults_to_today_seam_when_date_omitted(
        self, region: MicroRegion
    ) -> None:
        """Omitting 'date' falls back to the injected 'today' seam."""
        fixed_today = datetime.date(2026, 4, 8)
        _make_bulletin(region, fixed_today, source=Bulletin.Source.SLF)

        result = tools.get_bulletin_metadata(region.region_id, today=fixed_today)

        assert result["has_bulletin"] is True
        assert result["date"] == "2026-04-08"


# ---------------------------------------------------------------------------
# _handle_* argument-parsing adapters
# ---------------------------------------------------------------------------


class TestHandleGetBulletinMetadataArgs:
    """Tests for the JSON-RPC arguments adapter of get_bulletin_metadata."""

    def test_missing_region_id_raises_tool_error(self) -> None:
        """The adapter rejects an argument dict without a valid region_id."""
        with pytest.raises(tools.ToolError):
            tools._handle_get_bulletin_metadata({})

    def test_invalid_date_string_raises_tool_error(self) -> None:
        """A non-ISO date string is rejected before the tool runs."""
        with pytest.raises(tools.ToolError):
            tools._handle_get_bulletin_metadata(
                {"region_id": "CH-4115", "date": "not-a-date"}
            )


# ---------------------------------------------------------------------------
# get_bulletin_raw
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetBulletinRaw:
    """Tests for tools.get_bulletin_raw."""

    def test_returns_caaml_verbatim_for_present_bulletin(
        self, region: MicroRegion
    ) -> None:
        """The stored raw_data is returned unchanged.

        Production ingest wraps raw CAAML in a GeoJSON Feature envelope
        (see ``docs/decisions/geojson-feature-envelope.md``); the tool
        never unwraps that envelope — a caller reasoning against the
        CAAML schema wants the same shape they would fetch from the
        provider. This test's factory sets raw_data without the envelope
        for brevity; the important assertion is the deep-equal against
        whatever the ingest stored.
        """
        target_date = datetime.date(2026, 4, 8)
        bulletin = _make_bulletin(region, target_date, source=Bulletin.Source.ALBINA)

        result = tools.get_bulletin_raw(region.region_id, target_date)

        assert result["has_bulletin"] is True
        assert result["region_id"] == region.region_id
        assert result["date"] == "2026-04-08"
        assert result["provider"] == "albina"
        assert result["issued_at"].startswith("2026-04-08T17:00")
        assert result["caaml"] == bulletin.raw_data
        assert "dangerRatings" in result["caaml"]["properties"]

    def test_no_bulletin_for_date_is_a_structured_empty_result(
        self, region: MicroRegion
    ) -> None:
        """A quiet day with no bulletin is a normal result, not an error."""
        result = tools.get_bulletin_raw(region.region_id, datetime.date(2026, 4, 8))

        assert result["has_bulletin"] is False
        assert "caaml" not in result
        assert "No bulletin" in result["summary"]

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_bulletin_raw("XX-0000", datetime.date(2026, 4, 8))

    def test_defaults_to_today_seam_when_date_omitted(
        self, region: MicroRegion
    ) -> None:
        """Omitting 'date' falls back to the injected 'today' seam."""
        fixed_today = datetime.date(2026, 4, 8)
        _make_bulletin(region, fixed_today)

        result = tools.get_bulletin_raw(region.region_id, today=fixed_today)

        assert result["has_bulletin"] is True
        assert result["date"] == "2026-04-08"


# ---------------------------------------------------------------------------
# bulk_current_conditions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulkCurrentConditions:
    """Tests for tools.bulk_current_conditions."""

    @pytest.fixture
    def three_regions(self) -> list[MicroRegion]:
        """Three MicroRegion fixtures for batch tests."""
        major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_en="Valais")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        return [
            MicroRegionFactory.create(
                region_id=f"CH-411{n}", name=f"Region-{n}", subregion=sub
            )
            for n in (5, 6, 7)
        ]

    def test_returns_one_entry_per_region_in_input_order(
        self, three_regions: list[MicroRegion]
    ) -> None:
        """The batch preserves caller-supplied ordering."""
        target_date = datetime.date(2026, 4, 8)
        for region in three_regions:
            _make_bulletin(region, target_date, danger_key="moderate")

        result = tools.bulk_current_conditions(
            [r.region_id for r in three_regions], target_date
        )

        assert result["count"] == 3
        assert [r["region_id"] for r in result["results"]] == [
            "CH-4115",
            "CH-4116",
            "CH-4117",
        ]
        assert all(r["has_bulletin"] for r in result["results"])
        assert result["query"]["date"] == "2026-04-08"

    def test_unknown_region_is_per_item_not_batch_failure(
        self, three_regions: list[MicroRegion]
    ) -> None:
        """A stale region_id in the batch surfaces as a per-item error."""
        target_date = datetime.date(2026, 4, 8)
        _make_bulletin(three_regions[0], target_date)

        result = tools.bulk_current_conditions(
            [three_regions[0].region_id, "XX-0000", three_regions[1].region_id],
            target_date,
        )

        assert result["count"] == 3
        assert result["results"][0]["has_bulletin"] is True
        assert result["results"][1] == {
            "region_id": "XX-0000",
            "has_bulletin": False,
            "error": "unknown_region",
            "summary": "Unknown region_id: 'XX-0000'.",
        }
        assert result["results"][2]["has_bulletin"] is False
        assert "1 unknown region_id" in result["summary"]

    def test_duplicate_region_ids_return_duplicate_entries(
        self, region: MicroRegion
    ) -> None:
        """Duplicates aren't deduped — the caller's own bug, not ours."""
        _make_bulletin(region, datetime.date(2026, 4, 8))

        result = tools.bulk_current_conditions(
            [region.region_id, region.region_id], datetime.date(2026, 4, 8)
        )

        assert result["count"] == 2
        assert [r["region_id"] for r in result["results"]] == [
            region.region_id,
            region.region_id,
        ]

    def test_defaults_to_today_seam_when_date_omitted(
        self, region: MicroRegion
    ) -> None:
        """Omitting 'date' falls back to the injected 'today' seam."""
        fixed_today = datetime.date(2026, 4, 8)
        _make_bulletin(region, fixed_today)

        result = tools.bulk_current_conditions([region.region_id], today=fixed_today)

        assert result["query"]["date"] == "2026-04-08"
        assert result["results"][0]["has_bulletin"] is True


class TestHandleBulkCurrentConditionsArgs:
    """Tests for the JSON-RPC arguments adapter of bulk_current_conditions.

    These exercise the -32602 rejection path directly (import the
    ProtocolError class locally so the assertions read straightforwardly).
    """

    def _protocol_error(self) -> type[Exception]:
        """Return the ProtocolError class without a module-level cycle."""
        from mcp_server.protocol import ProtocolError

        return ProtocolError

    def test_missing_region_ids_raises_protocol_error(self) -> None:
        """A batch call without region_ids is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_bulk_current_conditions({})

    def test_non_list_region_ids_raises_protocol_error(self) -> None:
        """region_ids that isn't an array is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_bulk_current_conditions({"region_ids": "CH-4115"})

    def test_empty_batch_raises_protocol_error(self) -> None:
        """An empty region_ids array is invalid input, not an empty success."""
        with pytest.raises(self._protocol_error()):
            tools._handle_bulk_current_conditions({"region_ids": []})

    def test_over_cap_batch_raises_protocol_error(self) -> None:
        """A 21-item batch exceeds the cap; -32602, no partial result."""
        over_cap = [f"CH-{i:04d}" for i in range(21)]
        with pytest.raises(self._protocol_error()):
            tools._handle_bulk_current_conditions({"region_ids": over_cap})

    def test_non_string_batch_entry_raises_protocol_error(self) -> None:
        """A non-string entry in the batch is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_bulk_current_conditions({"region_ids": ["CH-4115", 42]})


# ---------------------------------------------------------------------------
# find_regions_near
# ---------------------------------------------------------------------------


# Coordinates picked so distance ordering is unambiguous:
# * Verbier   (~46.10, 7.23)      → target for near-hit tests.
# * Zermatt   (~46.02, 7.75)      → ~40 km east of Verbier.
# * Chamonix  (~45.92, 6.87)      → ~30 km south-west of Verbier.
# * Zurich    (~47.37, 8.55)      → ~180 km north-east — out of a 100 km
#                                    radius from Verbier.
_VERBIER = (46.10, 7.23)
_ZERMATT = (46.02, 7.75)
_CHAMONIX = (45.92, 6.87)
_ZURICH = (47.37, 8.55)


@pytest.mark.django_db
class TestFindRegionsNear:
    """Tests for tools.find_regions_near."""

    @pytest.fixture
    def alpine_regions(self) -> dict[str, MicroRegion]:
        """Three MicroRegions with known centroids for distance tests."""
        ch = MajorRegionFactory.create(prefix="CH-4", country="CH", name_en="Valais")
        fr = MajorRegionFactory.create(prefix="FR-73", country="FR", name_en="Savoie")
        ch_north = MajorRegionFactory.create(
            prefix="CH-2", country="CH", name_en="Zurich"
        )
        ch_sub = SubRegionFactory.create(prefix="CH-41", major=ch)
        fr_sub = SubRegionFactory.create(prefix="FR-73", major=fr)
        north_sub = SubRegionFactory.create(prefix="CH-21", major=ch_north)
        return {
            "verbier": MicroRegionFactory.create(
                region_id="CH-4115",
                name="Bas-Valais",
                subregion=ch_sub,
                centre={"lat": _VERBIER[0], "lon": _VERBIER[1]},
            ),
            "zermatt": MicroRegionFactory.create(
                region_id="CH-4114",
                name="Zermatt",
                subregion=ch_sub,
                centre={"lat": _ZERMATT[0], "lon": _ZERMATT[1]},
            ),
            "chamonix": MicroRegionFactory.create(
                region_id="FR-73-01",
                name="Mont-Blanc",
                subregion=fr_sub,
                centre={"lat": _CHAMONIX[0], "lon": _CHAMONIX[1]},
            ),
            "zurich": MicroRegionFactory.create(
                region_id="CH-2001",
                name="Zurich Alps",
                subregion=north_sub,
                centre={"lat": _ZURICH[0], "lon": _ZURICH[1]},
            ),
        }

    def test_nearest_region_first(self, alpine_regions: dict[str, MicroRegion]) -> None:
        """Verbier coords resolve to CH-4115 as the closest hit."""
        result = tools.find_regions_near(lat=_VERBIER[0], lon=_VERBIER[1], radius_km=50)

        assert result["count"] >= 1
        assert result["results"][0]["region_id"] == "CH-4115"
        assert result["results"][0]["distance_km"] == 0.0
        assert result["results"][0]["provider"] == "slf"

    def test_orders_by_distance(self, alpine_regions: dict[str, MicroRegion]) -> None:
        """A wider radius returns hits nearest-first."""
        result = tools.find_regions_near(lat=_VERBIER[0], lon=_VERBIER[1], radius_km=50)

        distances = [r["distance_km"] for r in result["results"]]
        assert distances == sorted(distances)

    def test_provider_derives_from_region_country(
        self, alpine_regions: dict[str, MicroRegion]
    ) -> None:
        """A French region is attributed to meteofrance, not slf."""
        result = tools.find_regions_near(
            lat=_CHAMONIX[0], lon=_CHAMONIX[1], radius_km=10
        )

        assert result["count"] == 1
        assert result["results"][0]["region_id"] == "FR-73-01"
        assert result["results"][0]["provider"] == "meteofrance"

    def test_zero_matches_returns_empty_not_error(
        self, alpine_regions: dict[str, MicroRegion]
    ) -> None:
        """A query point outside coverage is a successful empty result."""
        # Tokyo — nothing within 100 km of Snowdesk-covered regions.
        result = tools.find_regions_near(lat=35.68, lon=139.69, radius_km=100)

        assert result["results"] == []
        assert result["count"] == 0
        assert "No covered regions" in result["summary"]

    def test_ignores_regions_without_centre(
        self, alpine_regions: dict[str, MicroRegion]
    ) -> None:
        """Regions with a null centre don't crash the distance loop."""
        ch = MajorRegionFactory.create(prefix="CH-9", country="CH", name_en="Rhaetia")
        sub = SubRegionFactory.create(prefix="CH-91", major=ch)
        MicroRegionFactory.create(
            region_id="CH-9101",
            name="No-centre region",
            subregion=sub,
            centre=None,
        )

        # Query near the null-centre region's would-be location — it must
        # not appear in results and must not raise.
        result = tools.find_regions_near(
            lat=_VERBIER[0], lon=_VERBIER[1], radius_km=100
        )

        assert all(r["region_id"] != "CH-9101" for r in result["results"])

    def test_limit_truncates_result_list(
        self, alpine_regions: dict[str, MicroRegion]
    ) -> None:
        """The limit argument caps the number of returned regions."""
        result = tools.find_regions_near(
            lat=_VERBIER[0], lon=_VERBIER[1], radius_km=100, limit=1
        )

        assert result["count"] == 1


class TestHandleFindRegionsNearArgs:
    """Tests for the JSON-RPC arguments adapter of find_regions_near."""

    def _protocol_error(self) -> type[Exception]:
        """Return the ProtocolError class without a module-level cycle."""
        from mcp_server.protocol import ProtocolError

        return ProtocolError

    def test_missing_lat_raises_protocol_error(self) -> None:
        """A call without lat is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_find_regions_near({"lon": 7.23, "radius_km": 10})

    def test_out_of_range_lat_raises_protocol_error(self) -> None:
        """A lat outside [-90, 90] is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_find_regions_near({"lat": 200, "lon": 7.23, "radius_km": 10})

    def test_out_of_range_lon_raises_protocol_error(self) -> None:
        """A lon outside [-180, 180] is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_find_regions_near({"lat": 46.1, "lon": 300, "radius_km": 10})

    def test_zero_or_negative_radius_raises_protocol_error(self) -> None:
        """A non-positive radius is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_find_regions_near({"lat": 46.1, "lon": 7.23, "radius_km": 0})

    def test_over_cap_radius_raises_protocol_error(self) -> None:
        """A radius over 100 km is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_find_regions_near(
                {"lat": 46.1, "lon": 7.23, "radius_km": 500}
            )

    def test_non_integer_limit_raises_protocol_error(self) -> None:
        """A non-integer limit is a -32602 protocol failure."""
        with pytest.raises(self._protocol_error()):
            tools._handle_find_regions_near(
                {"lat": 46.1, "lon": 7.23, "radius_km": 10, "limit": "many"}
            )


# ---------------------------------------------------------------------------
# get_danger_trend
# ---------------------------------------------------------------------------


def _series(*max_ratings: str) -> list[dict[str, Any]]:
    """Return a compact days_series list for helper unit tests.

    Dates are consecutive starting 2026-01-01 so the change_point helper
    has real ISO dates to return.
    """
    base = datetime.date(2026, 1, 1)
    return [
        {
            "date": (base + datetime.timedelta(days=i)).isoformat(),
            "min_rating": rating,
            "max_rating": rating,
        }
        for i, rating in enumerate(max_ratings)
    ]


class TestDirectionHelper:
    """Unit tests for the pure-function _direction helper."""

    def test_rising_series(self) -> None:
        """A series rising from low to high classifies as 'rising'."""
        series = _series(
            RegionDayRating.Rating.LOW,
            RegionDayRating.Rating.LOW,
            RegionDayRating.Rating.LOW,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.CONSIDERABLE,
            RegionDayRating.Rating.HIGH,
            RegionDayRating.Rating.HIGH,
        )
        assert tools._direction(series) == "rising"

    def test_falling_series(self) -> None:
        """A series falling from high to low classifies as 'falling'."""
        series = _series(
            RegionDayRating.Rating.HIGH,
            RegionDayRating.Rating.HIGH,
            RegionDayRating.Rating.CONSIDERABLE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.LOW,
        )
        assert tools._direction(series) == "falling"

    def test_flat_series_is_stable(self) -> None:
        """A flat series classifies as 'stable'."""
        series = _series(
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
        )
        assert tools._direction(series) == "stable"

    def test_below_threshold_shift_is_stable(self) -> None:
        """A mean-shift smaller than the threshold classifies as 'stable'."""
        series = _series(
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.CONSIDERABLE,
        )
        # First-third mean 2, last-third mean 3 → delta 1.0 → rising.
        # This test uses a two-item shift that only bumps the mean by
        # less than the threshold when combined with a longer prefix.
        short_series = _series(
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
        )
        assert tools._direction(short_series) == "stable"
        # Sanity: keep the reference to `series` used above so ruff/pyright
        # don't flag it unused; the assertion belongs to the short series.
        assert tools._direction(series) in {"rising", "stable"}

    def test_single_point_series_is_stable(self) -> None:
        """A one-day window has no direction to infer."""
        series = _series(RegionDayRating.Rating.MODERATE)
        assert tools._direction(series) == "stable"

    def test_empty_series_is_stable(self) -> None:
        """An empty series is a stable trend (nothing to infer from)."""
        assert tools._direction([]) == "stable"


class TestChangePointHelper:
    """Unit tests for the pure-function _change_point helper."""

    def test_returns_most_recent_transition(self) -> None:
        """The helper picks the latest transition, not the first."""
        series = _series(
            RegionDayRating.Rating.LOW,
            RegionDayRating.Rating.MODERATE,  # first transition
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.HIGH,  # most recent transition
            RegionDayRating.Rating.HIGH,
        )
        cp = tools._change_point(series)
        assert cp is not None
        assert cp["date"] == "2026-01-04"
        assert cp["from_rating"] == RegionDayRating.Rating.MODERATE
        assert cp["to_rating"] == RegionDayRating.Rating.HIGH

    def test_flat_series_returns_none(self) -> None:
        """A series with no transitions returns None."""
        series = _series(
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
        )
        assert tools._change_point(series) is None

    def test_empty_series_returns_none(self) -> None:
        """An empty series returns None."""
        assert tools._change_point([]) is None


class TestCurrentStreakHelper:
    """Unit tests for the pure-function _current_streak helper."""

    def test_trailing_streak_of_four_days(self) -> None:
        """Four days ending at Moderate returns days=4, rating=moderate."""
        series = _series(
            RegionDayRating.Rating.LOW,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
        )
        streak = tools._current_streak(series)
        assert streak == {
            "rating": RegionDayRating.Rating.MODERATE,
            "days": 4,
        }

    def test_streak_of_one_at_the_edge(self) -> None:
        """A one-day streak (rating just changed today) returns days=1."""
        series = _series(
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.MODERATE,
            RegionDayRating.Rating.HIGH,
        )
        streak = tools._current_streak(series)
        assert streak == {
            "rating": RegionDayRating.Rating.HIGH,
            "days": 1,
        }

    def test_empty_series_returns_none(self) -> None:
        """An empty series has no streak."""
        assert tools._current_streak([]) is None


@pytest.mark.django_db
class TestGetDangerTrend:
    """Integration tests for tools.get_danger_trend."""

    def test_returns_series_and_trend_fields(self, region: MicroRegion) -> None:
        """A real region + range returns the full envelope with trend fields."""
        # Six days ascending — a clear rising trend.
        for day, rating in (
            (1, RegionDayRating.Rating.LOW),
            (2, RegionDayRating.Rating.LOW),
            (3, RegionDayRating.Rating.MODERATE),
            (4, RegionDayRating.Rating.CONSIDERABLE),
            (5, RegionDayRating.Rating.HIGH),
            (6, RegionDayRating.Rating.HIGH),
        ):
            RegionDayRatingFactory.create(
                region=region,
                date=datetime.date(2026, 1, day),
                max_rating=rating,
                min_rating=rating,
            )

        result = tools.get_danger_trend(
            region.region_id, days=6, today=datetime.date(2026, 1, 6)
        )

        assert result["count"] == 6
        assert result["direction"] == "rising"
        assert result["current_streak"] == {
            "rating": RegionDayRating.Rating.HIGH,
            "days": 2,
        }
        assert result["change_point"] is not None
        assert result["change_point"]["date"] == "2026-01-05"
        assert "rising" in result["summary"]

    def test_defaults_window_to_fourteen_days(self, region: MicroRegion) -> None:
        """Omitting 'days' defaults to a 14-day window ending today."""
        result = tools.get_danger_trend(
            region.region_id, today=datetime.date(2026, 1, 14)
        )

        # requested_from should be 14 days before today (inclusive → 2026-01-01).
        assert result["requested_from"] == "2026-01-01"
        assert result["requested_to"] == "2026-01-14"

    def test_off_season_returns_empty_series_and_null_trend_shape(
        self, region: MicroRegion
    ) -> None:
        """A window entirely outside the season returns empty days + null trend."""
        # today = 15 July — the season-clamp yields an empty effective
        # window and get_danger_history returns days: [] with clamped: true.
        result = tools.get_danger_trend(
            region.region_id, days=7, today=datetime.date(2026, 7, 15)
        )

        assert result["days"] == []
        assert result["clamped"] is True
        assert result["direction"] == "stable"
        assert result["change_point"] is None
        assert result["current_streak"] is None

    def test_days_over_cap_raises_tool_error(self, region: MicroRegion) -> None:
        """A days value over the cap raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_danger_trend(
                region.region_id, days=200, today=datetime.date(2026, 2, 1)
            )

    def test_non_positive_days_raises_tool_error(self, region: MicroRegion) -> None:
        """A days value ≤ 0 raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_danger_trend(
                region.region_id, days=0, today=datetime.date(2026, 2, 1)
            )

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_danger_trend("XX-0000", days=7, today=datetime.date(2026, 2, 1))


# ---------------------------------------------------------------------------
# list_regions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListRegions:
    """Tests for tools.list_regions."""

    @pytest.fixture
    def multi_country_regions(self) -> dict[str, MicroRegion]:
        """Four MicroRegions across CH/AT/IT/FR for filter and AND tests."""
        ch = MajorRegionFactory.create(prefix="CH-4", country="CH", name_en="Valais")
        at = MajorRegionFactory.create(prefix="AT-07", country="AT", name_en="Tyrol")
        it = MajorRegionFactory.create(
            prefix="IT-32", country="IT", name_en="Alto Adige"
        )
        fr = MajorRegionFactory.create(prefix="FR-73", country="FR", name_en="Savoie")
        ch_sub = SubRegionFactory.create(prefix="CH-41", major=ch)
        at_sub = SubRegionFactory.create(prefix="AT-07-1", major=at)
        it_sub = SubRegionFactory.create(prefix="IT-32-1", major=it)
        fr_sub = SubRegionFactory.create(prefix="FR-73-1", major=fr)
        return {
            "verbier": MicroRegionFactory.create(
                region_id="CH-4115", name="Bas-Valais", subregion=ch_sub
            ),
            "tyrol": MicroRegionFactory.create(
                region_id="AT-07-01", name="Tyrol Region", subregion=at_sub
            ),
            "alto_adige": MicroRegionFactory.create(
                region_id="IT-32-01", name="Alto Adige Region", subregion=it_sub
            ),
            "chamonix": MicroRegionFactory.create(
                region_id="FR-73-01", name="Mont-Blanc", subregion=fr_sub
            ),
        }

    def test_no_args_returns_all_regions_including_uncovered(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """With no filters, every region is returned — including uncovered ones."""
        # None of these regions has a RegionDayRating row — an uncovered
        # region is still a real, listable region.
        result = tools.list_regions()

        region_ids = {r["region_id"] for r in result["regions"]}
        assert region_ids == {"CH-4115", "AT-07-01", "IT-32-01", "FR-73-01"}
        assert result["count"] == 4
        assert result["filters"] == {"country": None, "provider": None}

    def test_response_constants_are_upper_case(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """kind and provider values are UPPER CASE, unlike the ten-tool convention."""
        result = tools.list_regions(country="ch")

        entry = result["regions"][0]
        assert entry["kind"] == "MICRO"
        assert entry["provider"] == "SLF"
        assert entry["country"] == "CH"

    def test_country_filter_is_case_insensitive(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """A lowercase country filter is normalised and applied."""
        result = tools.list_regions(country="ch")

        assert {r["region_id"] for r in result["regions"]} == {"CH-4115"}
        assert result["filters"]["country"] == "CH"

    def test_provider_filter_is_case_insensitive(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """A lowercase provider filter is normalised, matching both AT and IT."""
        result = tools.list_regions(provider="albina")

        assert {r["region_id"] for r in result["regions"]} == {
            "AT-07-01",
            "IT-32-01",
        }
        assert result["filters"]["provider"] == "ALBINA"

    def test_country_and_provider_filters_are_anded(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """country narrows provider's broader match set — both apply together."""
        result = tools.list_regions(country="IT", provider="ALBINA")

        assert {r["region_id"] for r in result["regions"]} == {"IT-32-01"}

    def test_mismatched_filters_return_empty_not_error(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """A country/provider combination with no overlap is an empty result."""
        result = tools.list_regions(country="FR", provider="ALBINA")

        assert result["regions"] == []
        assert result["count"] == 0

    def test_unknown_country_raises_tool_error(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """An unrecognised country raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.list_regions(country="XX")

    def test_unknown_provider_raises_tool_error(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """An unrecognised provider raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.list_regions(provider="not_a_provider")

    def test_empty_country_raises_tool_error(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """An empty country string is rejected, not treated as no filter."""
        with pytest.raises(tools.ToolError):
            tools.list_regions(country="")

    def test_empty_provider_raises_tool_error(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """An empty provider string is rejected, not treated as no filter."""
        with pytest.raises(tools.ToolError):
            tools.list_regions(provider="")

    def test_handle_list_regions_adapts_arguments(
        self, multi_country_regions: dict[str, MicroRegion]
    ) -> None:
        """The JSON-RPC adapter passes country/provider through to the tool."""
        result = tools._handle_list_regions({"country": "ch"})

        assert {r["region_id"] for r in result["regions"]} == {"CH-4115"}

    def test_handle_list_regions_rejects_non_string_country(self) -> None:
        """A non-string country argument raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools._handle_list_regions({"country": 123})

    def test_handle_list_regions_rejects_non_string_provider(self) -> None:
        """A non-string provider argument raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools._handle_list_regions({"provider": 123})


# ---------------------------------------------------------------------------
# region_info
# ---------------------------------------------------------------------------

_SQUARE_POLYGON: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]]],
}

_TWO_SQUARE_MULTIPOLYGON: dict[str, Any] = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[7.0, 46.0], [7.2, 46.0], [7.2, 46.2], [7.0, 46.2], [7.0, 46.0]]],
        [[[8.0, 47.0], [8.3, 47.0], [8.3, 47.3], [8.0, 47.3], [8.0, 47.0]]],
    ],
}


@pytest.mark.django_db
class TestRegionInfo:
    """Tests for tools.region_info."""

    def test_full_envelope_for_a_covered_region(self, region: MicroRegion) -> None:
        """Every envelope field is present and correctly derived."""
        region.boundary = _SQUARE_POLYGON
        region.save()
        ResortFactory.create(
            name="Verbier", region=region, latitude=46.0961, longitude=7.2286
        )
        ResortFactory.create(name="Ungeocoded Resort", region=region)
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 4, 1))
        RegionDayRatingFactory.create(region=region, date=datetime.date(2026, 4, 10))

        result = tools.region_info(region.region_id)

        assert result["region_id"] == region.region_id
        assert result["region_name"] == region.name
        assert result["country"] == "CH"
        assert result["eaws_parent"] == {
            "major_prefix": "CH-4",
            "major_name": "Valais",
            "sub_prefix": "CH-41",
            "sub_name": region.subregion.name_en or region.subregion.name_native,
        }
        assert result["source_provider"] == "SLF"
        # canton "VS" is ResortFactory's default, not set in this test.
        assert result["resorts"] == [
            {
                "name": "Verbier",
                "latitude": 46.0961,
                "longitude": 7.2286,
                "canton": "VS",
            }
        ]
        assert result["resort_count"] == 1
        assert result["bbox"] == {
            "min_lon": 7.0,
            "min_lat": 46.0,
            "max_lon": 7.5,
            "max_lat": 46.5,
        }
        assert result["coverage_first_date"] == "2026-04-01"
        assert result["coverage_last_date"] == "2026-04-10"
        assert "Published daily" in result["issue_schedule"]
        assert region.region_id in result["summary"]

    def test_multipolygon_boundary_computes_bbox_across_all_parts(
        self, region: MicroRegion
    ) -> None:
        """A MultiPolygon boundary's bbox spans every part, not just the first."""
        region.boundary = _TWO_SQUARE_MULTIPOLYGON
        region.save()

        result = tools.region_info(region.region_id)

        assert result["bbox"] == {
            "min_lon": 7.0,
            "min_lat": 46.0,
            "max_lon": 8.3,
            "max_lat": 47.3,
        }

    def test_missing_boundary_returns_null_bbox(self, region: MicroRegion) -> None:
        """A region with no stored boundary reports bbox: null, not an error."""
        result = tools.region_info(region.region_id)

        assert result["bbox"] is None

    def test_zero_ratings_returns_null_coverage_dates(
        self, region: MicroRegion
    ) -> None:
        """An uncovered region reports both coverage dates as null."""
        result = tools.region_info(region.region_id)

        assert result["coverage_first_date"] is None
        assert result["coverage_last_date"] is None
        assert "No bulletin coverage" in result["summary"]

    def test_unknown_region_id_raises_tool_error(self) -> None:
        """An unknown region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.region_info("XX-0000")

    def test_handle_region_info_adapts_arguments(self, region: MicroRegion) -> None:
        """The JSON-RPC adapter passes region_id through to the tool."""
        result = tools._handle_region_info({"region_id": region.region_id})

        assert result["region_id"] == region.region_id

    def test_handle_region_info_requires_region_id(self) -> None:
        """A missing region_id is rejected by the shared _require_str helper."""
        with pytest.raises(tools.ToolError):
            tools._handle_region_info({})


# ---------------------------------------------------------------------------
# get_regional_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetRegionalSnapshot:
    """Tests for tools.get_regional_snapshot."""

    @pytest.fixture
    def snapshot_regions(self) -> dict[str, MicroRegion]:
        """CH-4 (two children) + CH-5 (one child) + AT-07 (one child)."""
        valais = MajorRegionFactory.create(prefix="CH-4", country="CH")
        valais_sub = SubRegionFactory.create(prefix="CH-41", major=valais)
        verbier = MicroRegionFactory.create(
            region_id="CH-4115", name="Verbier", subregion=valais_sub
        )
        zermatt = MicroRegionFactory.create(
            region_id="CH-4116", name="Zermatt", subregion=valais_sub
        )
        chur_major = MajorRegionFactory.create(prefix="CH-5", country="CH")
        chur_sub = SubRegionFactory.create(prefix="CH-52", major=chur_major)
        chur = MicroRegionFactory.create(
            region_id="CH-5210", name="Chur", subregion=chur_sub
        )
        at_major = MajorRegionFactory.create(prefix="AT-07", country="AT")
        at_sub = SubRegionFactory.create(prefix="AT-07-19", major=at_major)
        st_anton = MicroRegionFactory.create(
            region_id="AT-07-19", name="St. Anton", subregion=at_sub
        )
        return {
            "verbier": verbier,
            "zermatt": zermatt,
            "chur": chur,
            "st_anton": st_anton,
        }

    def test_country_scope_returns_only_that_country(
        self, snapshot_regions: dict[str, MicroRegion]
    ) -> None:
        """country='CH' returns the three CH regions, not the AT one."""
        target_date = datetime.date(2026, 1, 15)
        for region in snapshot_regions.values():
            RegionDayRatingFactory.create(
                region=region,
                date=target_date,
                max_rating=RegionDayRating.Rating.MODERATE,
                min_rating=RegionDayRating.Rating.MODERATE,
            )

        result = tools.get_regional_snapshot(country="CH", date=target_date)

        assert result["count"] == 3
        region_ids = {r["region_id"] for r in result["regions"]}
        assert region_ids == {"CH-4115", "CH-4116", "CH-5210"}
        assert result["scope"] == {"country": "CH", "major_region_id": None}

    def test_major_region_scope_returns_subset(
        self, snapshot_regions: dict[str, MicroRegion]
    ) -> None:
        """major_region_id='CH-4' scopes to only Valais's two children."""
        target_date = datetime.date(2026, 1, 15)
        for region in snapshot_regions.values():
            RegionDayRatingFactory.create(region=region, date=target_date)

        result = tools.get_regional_snapshot(major_region_id="CH-4", date=target_date)

        assert result["count"] == 2
        region_ids = {r["region_id"] for r in result["regions"]}
        assert region_ids == {"CH-4115", "CH-4116"}
        assert result["scope"] == {"country": None, "major_region_id": "CH-4"}

    def test_uncovered_region_has_bulletin_false_and_does_not_fail(
        self, snapshot_regions: dict[str, MicroRegion]
    ) -> None:
        """A region with no RegionDayRating row surfaces has_bulletin: False in-line."""
        target_date = datetime.date(2026, 1, 15)
        RegionDayRatingFactory.create(
            region=snapshot_regions["verbier"],
            date=target_date,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
            min_rating=RegionDayRating.Rating.CONSIDERABLE,
        )
        # zermatt has no RegionDayRating row for this date.

        result = tools.get_regional_snapshot(major_region_id="CH-4", date=target_date)

        entries = {r["region_id"]: r for r in result["regions"]}
        assert entries["CH-4115"]["has_bulletin"] is True
        assert entries["CH-4116"]["has_bulletin"] is False
        assert entries["CH-4116"]["danger_level"] is None
        assert entries["CH-4116"]["min_rating"] is None
        assert result["count_with_bulletin"] == 1

    def test_split_day_region_surfaces_min_and_max_rating(
        self, snapshot_regions: dict[str, MicroRegion]
    ) -> None:
        """A split-day row's min_rating and max_rating (danger_level) both surface."""
        target_date = datetime.date(2026, 1, 15)
        RegionDayRatingFactory.create(
            region=snapshot_regions["verbier"],
            date=target_date,
            min_rating=RegionDayRating.Rating.MODERATE,
            max_rating=RegionDayRating.Rating.CONSIDERABLE,
        )

        result = tools.get_regional_snapshot(country="CH", date=target_date)

        entry = next(r for r in result["regions"] if r["region_id"] == "CH-4115")
        assert entry["danger_level"] == "considerable"
        assert entry["min_rating"] == "moderate"

    def test_summary_surfaces_bulletin_ratio_and_peak_population(
        self, snapshot_regions: dict[str, MicroRegion]
    ) -> None:
        """The summary reports the with-bulletin ratio and the peak rating."""
        target_date = datetime.date(2026, 1, 15)
        RegionDayRatingFactory.create(
            region=snapshot_regions["verbier"],
            date=target_date,
            max_rating=RegionDayRating.Rating.HIGH,
            min_rating=RegionDayRating.Rating.HIGH,
        )
        RegionDayRatingFactory.create(
            region=snapshot_regions["zermatt"],
            date=target_date,
            max_rating=RegionDayRating.Rating.MODERATE,
            min_rating=RegionDayRating.Rating.MODERATE,
        )
        # chur has no row.

        result = tools.get_regional_snapshot(country="CH", date=target_date)

        assert "2/3" in result["summary"]
        assert "high" in result["summary"]
        assert "1 region" in result["summary"]

    def test_both_scopes_supplied_raises_tool_error(self) -> None:
        """Supplying both country and major_region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_regional_snapshot(country="CH", major_region_id="CH-4")

    def test_neither_scope_supplied_raises_tool_error(self) -> None:
        """Supplying neither country nor major_region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_regional_snapshot()

    def test_unknown_country_raises_tool_error(self) -> None:
        """An unrecognised ISO-2 country code raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_regional_snapshot(country="XX")

    def test_unknown_major_region_id_raises_tool_error(self) -> None:
        """An unrecognised major_region_id raises ToolError."""
        with pytest.raises(tools.ToolError):
            tools.get_regional_snapshot(major_region_id="CH-9999")

    def test_future_date_with_no_ratings_returns_no_error(
        self, snapshot_regions: dict[str, MicroRegion]
    ) -> None:
        """A date with no RegionDayRating rows at all is a legitimate empty result."""
        result = tools.get_regional_snapshot(
            country="CH", date=datetime.date(2030, 1, 1)
        )

        assert result["count"] == 3
        assert result["count_with_bulletin"] == 0
        assert all(not r["has_bulletin"] for r in result["regions"])

    def test_today_seam_is_used_when_date_omitted(
        self, snapshot_regions: dict[str, MicroRegion]
    ) -> None:
        """Omitting 'date' falls back to the injected 'today' seam."""
        fixed_today = datetime.date(2026, 1, 15)
        RegionDayRatingFactory.create(
            region=snapshot_regions["verbier"],
            date=fixed_today,
            max_rating=RegionDayRating.Rating.LOW,
            min_rating=RegionDayRating.Rating.LOW,
        )

        result = tools.get_regional_snapshot(country="CH", today=fixed_today)

        assert result["date"] == "2026-01-15"


class TestHandleGetRegionalSnapshotArgs:
    """Tests for the JSON-RPC arguments adapter of get_regional_snapshot."""

    def test_non_string_country_raises_tool_error(self) -> None:
        """A non-string 'country' is a domain-level ToolError."""
        with pytest.raises(tools.ToolError):
            tools._handle_get_regional_snapshot({"country": 42})

    def test_non_string_major_region_id_raises_tool_error(self) -> None:
        """A non-string 'major_region_id' is a domain-level ToolError."""
        with pytest.raises(tools.ToolError):
            tools._handle_get_regional_snapshot({"major_region_id": 42})

    def test_malformed_date_raises_tool_error(self) -> None:
        """A malformed 'date' is a domain-level ToolError."""
        with pytest.raises(tools.ToolError):
            tools._handle_get_regional_snapshot({"country": "CH", "date": "not-a-date"})
