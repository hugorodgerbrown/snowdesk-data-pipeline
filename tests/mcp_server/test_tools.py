"""
tests/mcp_server/test_tools.py — Tests for mcp_server.tools.

One test class per tool: ``search_regions``, ``get_current_conditions``,
``get_danger_history``, ``list_resorts_in_region``. Each tool's business
logic is exercised directly (not through the JSON-RPC ``arguments`` dict
adapters) so date/``today`` seams stay explicit and readable.
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
        "raw_data": {"properties": {"dangerRatings": [{"mainValue": danger_key}]}},
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
