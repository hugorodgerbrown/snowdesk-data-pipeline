"""
tests/bulletins/services/test_render_model.py — Tests for the render_model service.

Covers:
  - build_render_model: output shape, trait building, aggregation ordering,
    elevation parsing, danger resolution, geography source detection.
  - compute_day_character: all five cascade rules with render model inputs.
  - Validation: fail-hard on unknown problem types, aggregation mismatch,
    empty problemTypes.
  - Missing aggregation: logs ERROR and returns empty traits (no synthesis).
  - Happy paths: both empty, empty title fallback, 3+ aggregation warning.
  - Version 3: metadata and prose extraction, _parse_iso_timestamp helper,
    tendency list, back-compat top-level snowpack_structure.
  - Version 4: source detection, EUREGIO aggregation synthesis,
    per-problem extras and avalanche_type, avalanche_activity prose,
    danger_patterns; CH regression tests asserting empty new slots.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bulletins.models import Bulletin
from bulletins.services.meteofrance_translator import parse_dpbra_xml
from bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    _build_metadata,
    _build_prose,
    _detect_source,
    _parse_elevation,
    _parse_iso_timestamp,
    _resolve_aggregations,
    _resolve_avalanche_activity,
    _resolve_danger,
    _resolve_danger_patterns,
    _resolve_problem_extras,
    _resolve_problem_rating,
    build_render_model,
    compute_day_character,
)

# Path to test fixtures directory.
_FIXTURE_DIR = Path(__file__).parents[2] / "fixtures"

# Path to the captured MeteoFrance XML files used in end-to-end tests.
# parents[3] = project root (thirsty-ride-57ad2f worktree or main repo).
_MF_BULLETIN_DIR = (
    Path(__file__).parents[3]
    / "docs"
    / "research"
    / "meteofrance"
    / "bulletins-2026-05-18"
)


def _load_sample(filename: str) -> dict[str, Any]:
    """Load a sample JSON fixture and return its ``properties`` dict."""
    path = _FIXTURE_DIR / filename
    data = json.loads(path.read_text())
    return data["properties"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# _parse_elevation
# ---------------------------------------------------------------------------


class TestParseElevation:
    """Tests for the _parse_elevation helper."""

    def test_numeric_string_lower(self) -> None:
        """Numeric string lower bound is parsed to int."""
        result = _parse_elevation({"lowerBound": "2200"})
        assert result is not None
        assert result["lower"] == 2200
        assert result["upper"] is None
        assert result["treeline"] is False

    def test_numeric_int_lower(self) -> None:
        """Integer lower bound is handled correctly."""
        result = _parse_elevation({"lowerBound": 1800})
        assert result is not None
        assert result["lower"] == 1800
        assert result["treeline"] is False

    def test_numeric_string_upper(self) -> None:
        """Numeric string upper bound is parsed to int."""
        result = _parse_elevation({"upperBound": "2400"})
        assert result is not None
        assert result["upper"] == 2400
        assert result["lower"] is None
        assert result["treeline"] is False

    def test_treeline_lower(self) -> None:
        """'treeline' token on lower bound sets treeline=True."""
        result = _parse_elevation({"lowerBound": "treeline"})
        assert result is not None
        assert result["treeline"] is True
        assert result["lower"] is None

    def test_treeline_upper(self) -> None:
        """'treeline' token on upper bound sets treeline=True."""
        result = _parse_elevation({"upperBound": "treeline"})
        assert result is not None
        assert result["treeline"] is True
        assert result["upper"] is None

    def test_both_numeric(self) -> None:
        """Both bounds numeric returns both as ints."""
        result = _parse_elevation({"lowerBound": "1800", "upperBound": "2400"})
        assert result is not None
        assert result["lower"] == 1800
        assert result["upper"] == 2400
        assert result["treeline"] is False

    def test_both_none_returns_none(self) -> None:
        """Both bounds None returns None."""
        assert _parse_elevation({"lowerBound": None, "upperBound": None}) is None

    def test_empty_dict_returns_none(self) -> None:
        """Empty elevation dict returns None."""
        assert _parse_elevation({}) is None

    def test_none_input_returns_none(self) -> None:
        """None input returns None."""
        assert _parse_elevation(None) is None


# ---------------------------------------------------------------------------
# _resolve_danger
# ---------------------------------------------------------------------------


class TestResolveDanger:
    """Tests for the _resolve_danger helper."""

    def test_single_rating(self) -> None:
        """Single rating returns its key, number, and no subdivision."""
        result = _resolve_danger([{"mainValue": "considerable"}])
        assert result["key"] == "considerable"
        assert result["number"] == "3"
        assert result["subdivision"] is None

    def test_picks_highest_rating(self) -> None:
        """Highest rating is selected when multiple exist."""
        result = _resolve_danger(
            [
                {"mainValue": "low"},
                {"mainValue": "considerable"},
                {"mainValue": "moderate"},
            ]
        )
        assert result["key"] == "considerable"

    def test_subdivision_plus(self) -> None:
        """'plus' subdivision maps to '+'."""
        result = _resolve_danger(
            [
                {
                    "mainValue": "considerable",
                    "customData": {"CH": {"subdivision": "plus"}},
                }
            ]
        )
        assert result["subdivision"] == "+"

    def test_subdivision_minus(self) -> None:
        """'minus' subdivision maps to '-'."""
        result = _resolve_danger(
            [
                {
                    "mainValue": "considerable",
                    "customData": {"CH": {"subdivision": "minus"}},
                }
            ]
        )
        assert result["subdivision"] == "-"

    def test_subdivision_equal(self) -> None:
        """'equal' subdivision maps to '='."""
        result = _resolve_danger(
            [
                {
                    "mainValue": "moderate",
                    "customData": {"CH": {"subdivision": "equal"}},
                }
            ]
        )
        assert result["subdivision"] == "="

    def test_no_subdivision_is_none(self) -> None:
        """Missing subdivision returns None (not empty string)."""
        result = _resolve_danger([{"mainValue": "moderate"}])
        assert result["subdivision"] is None

    def test_empty_ratings_returns_low(self) -> None:
        """Empty ratings list defaults to low/1."""
        result = _resolve_danger([])
        assert result["key"] == "low"
        assert result["number"] == "1"

    def test_unknown_main_value_is_skipped(self) -> None:
        """Unknown mainValue entries are skipped, not raising."""
        result = _resolve_danger(
            [
                {"mainValue": "not_a_real_level"},
                {"mainValue": "moderate"},
            ]
        )
        assert result["key"] == "moderate"


# ---------------------------------------------------------------------------
# build_render_model — stable day fixture
# ---------------------------------------------------------------------------


class TestBuildRenderModelStableDay:
    """Tests for build_render_model against a stable day fixture."""

    def test_output_shape(self) -> None:
        """Output has required top-level keys."""
        props = _load_sample("sample_stable_day.json")
        rm = build_render_model(props)

        assert rm["version"] == RENDER_MODEL_VERSION
        assert "danger" in rm
        assert "traits" in rm
        assert "snowpack_structure" in rm
        assert "metadata" in rm
        assert "prose" in rm

    def test_danger_is_low(self) -> None:
        """Stable day has danger=low."""
        props = _load_sample("sample_stable_day.json")
        rm = build_render_model(props)
        assert rm["danger"]["key"] == "low"
        assert rm["danger"]["number"] == "1"

    def test_single_dry_all_day_trait(self) -> None:
        """Stable day produces one dry/all_day trait."""
        props = _load_sample("sample_stable_day.json")
        rm = build_render_model(props)
        assert len(rm["traits"]) == 1
        trait = rm["traits"][0]
        assert trait["category"] == "dry"
        assert trait["time_period"] == "all_day"

    def test_day_character_stable(self) -> None:
        """Stable day yields 'Stable day' character."""
        props = _load_sample("sample_stable_day.json")
        rm = build_render_model(props)
        assert compute_day_character(rm).label == "Stable day"

    def test_snowpack_structure_populated(self) -> None:
        """snowpack_structure is populated from the raw data."""
        props = _load_sample("sample_stable_day.json")
        rm = build_render_model(props)
        assert rm["snowpack_structure"] is not None
        assert "stable" in rm["snowpack_structure"].lower()


# ---------------------------------------------------------------------------
# build_render_model — variable day fixture
# ---------------------------------------------------------------------------


class TestBuildRenderModelVariableDay:
    """Tests for build_render_model against the sample_variable_day fixture."""

    def test_two_traits(self) -> None:
        """Variable day produces two traits (dry/all_day and wet/later)."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert len(rm["traits"]) == 2

    def test_first_trait_is_dry_all_day(self) -> None:
        """First trait is dry/all_day — matching aggregation order."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        trait = rm["traits"][0]
        assert trait["category"] == "dry"
        assert trait["time_period"] == "all_day"

    def test_second_trait_is_wet_later(self) -> None:
        """Second trait is wet/later — matching aggregation order."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        trait = rm["traits"][1]
        assert trait["category"] == "wet"
        assert trait["time_period"] == "later"

    def test_wet_trait_has_wet_and_gliding_problems(self) -> None:
        """Wet/later trait contains wet_snow and gliding_snow problems."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        wet_trait = rm["traits"][1]
        types = {p["problem_type"] for p in wet_trait["problems"]}
        assert "wet_snow" in types
        assert "gliding_snow" in types

    def test_day_character_hard_to_read(self) -> None:
        """Variable day with gliding_snow → Hard-to-read day."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert compute_day_character(rm).label == "Hard-to-read day"

    def test_dry_and_wet_problems_are_segregated(self) -> None:
        """Dry and wet traits each contain only their own problem types."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        dry_trait = rm["traits"][0]
        wet_trait = rm["traits"][1]

        dry_types = {p["problem_type"] for p in dry_trait["problems"]}
        wet_types = {p["problem_type"] for p in wet_trait["problems"]}

        # Wet snow and gliding snow belong only in the wet/later trait.
        assert "wet_snow" not in dry_types
        assert "gliding_snow" not in dry_types

        # no_distinct_avalanche_problem belongs only in the dry/all_day trait.
        assert "no_distinct_avalanche_problem" not in wet_types

    def test_version_is_current(self) -> None:
        """Variable day render model has current RENDER_MODEL_VERSION."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert rm["version"] == RENDER_MODEL_VERSION


# ---------------------------------------------------------------------------
# build_render_model — subdivision 3+ fixture
# ---------------------------------------------------------------------------


class TestBuildRenderModelSubdivision3Plus:
    """Tests for a bulletin with danger=considerable + 'plus' subdivision."""

    def test_subdivision_is_plus(self) -> None:
        """Danger 3+ yields subdivision='+'."""
        props = _load_sample("sample_subdivision_3plus_day.json")
        rm = build_render_model(props)
        assert rm["danger"]["subdivision"] == "+"

    def test_day_character_widespread(self) -> None:
        """Danger 3+ → Widespread danger (rule 3b)."""
        props = _load_sample("sample_subdivision_3plus_day.json")
        rm = build_render_model(props)
        assert compute_day_character(rm).label == "Widespread danger"

    def test_version_is_current(self) -> None:
        """3+ subdivision fixture has current RENDER_MODEL_VERSION."""
        props = _load_sample("sample_subdivision_3plus_day.json")
        rm = build_render_model(props)
        assert rm["version"] == RENDER_MODEL_VERSION


# ---------------------------------------------------------------------------
# build_render_model — missing aggregation (synthesised from problems)
# ---------------------------------------------------------------------------


class TestBuildRenderModelNoAggregation:
    """Tests for bulletins missing customData.CH.aggregation.

    aggregation is expected to always be present when avalancheProblems is
    non-empty. Missing aggregation logs an ERROR and produces empty traits
    rather than attempting synthesis.
    """

    def test_no_aggregation_fixture_has_problems(self) -> None:
        """Sanity check: the fixture has problems but no aggregation."""
        props = _load_sample("sample_no_aggregation_day.json")
        assert len(props.get("avalancheProblems", [])) > 0
        assert not props.get("customData", {}).get("CH", {}).get("aggregation")

    def test_missing_aggregation_returns_empty_traits(self) -> None:
        """Missing aggregation produces empty traits and current version."""
        props = _load_sample("sample_no_aggregation_day.json")

        rm = build_render_model(props)

        assert rm["version"] == RENDER_MODEL_VERSION
        assert rm["traits"] == []

    def test_missing_aggregation_logs_error(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing aggregation is logged at ERROR level."""
        import logging

        # config/settings/base.py sets propagate=False on the bulletins logger
        # so that pytest's root caplog doesn't see records by default. Flip it
        # for the duration of this test so caplog can verify the error.
        monkeypatch.setattr(logging.getLogger("bulletins"), "propagate", True)

        props = _load_sample("sample_no_aggregation_day.json")

        with caplog.at_level(logging.ERROR, logger="bulletins.services.render_model"):
            build_render_model(props)

        assert any("cannot build traits" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# build_render_model — prose-only geography
# ---------------------------------------------------------------------------


class TestBuildRenderModelProseOnly:
    """Tests for problems with no aspects AND no elevation (prose_only)."""

    def test_geography_source_is_prose_only(self) -> None:
        """Problem with no aspects/elevation → geography.source = 'prose_only'."""
        props = _load_sample("sample_prose_only_day.json")
        rm = build_render_model(props)
        trait = rm["traits"][0]
        assert trait["geography"]["source"] == "prose_only"

    def test_prose_is_populated(self) -> None:
        """Trait prose is set to the problem's comment."""
        props = _load_sample("sample_prose_only_day.json")
        rm = build_render_model(props)
        trait = rm["traits"][0]
        assert trait["prose"] is not None
        assert len(trait["prose"]) > 0

    def test_geography_source_problems_when_aspects_present(self) -> None:
        """Problem with aspects → geography.source = 'problems'."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        # The dry/all_day trait has aspects on its problem.
        dry_trait = rm["traits"][0]
        assert dry_trait["geography"]["source"] == "problems"

    def test_version_is_current(self) -> None:
        """Prose-only fixture produces current RENDER_MODEL_VERSION render model."""
        props = _load_sample("sample_prose_only_day.json")
        rm = build_render_model(props)
        assert rm["version"] == RENDER_MODEL_VERSION


# ---------------------------------------------------------------------------
# build_render_model — both lists empty
# ---------------------------------------------------------------------------


class TestBuildRenderModelBothEmpty:
    """Tests for bulletins with no problems and no aggregation."""

    def test_both_empty_returns_empty_traits_without_raising(self) -> None:
        """Both lists empty → returns traits=[] without raising."""
        props: dict[str, Any] = {
            "bulletinID": "empty-001",
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
            "customData": {"CH": {}},
        }
        rm = build_render_model(props)
        assert rm["traits"] == []
        assert rm["version"] == RENDER_MODEL_VERSION

    def test_both_empty_stable_day(self) -> None:
        """Empty traits → 'Stable day' from compute_day_character."""
        props: dict[str, Any] = {
            "bulletinID": "empty-002",
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
            "customData": {"CH": {}},
        }
        rm = build_render_model(props)
        assert compute_day_character(rm).label == "Stable day"


# ---------------------------------------------------------------------------
# build_render_model — fail-hard validation
# ---------------------------------------------------------------------------


class TestBuildRenderModelValidation:
    """Tests for fail-hard validation in build_render_model."""

    def test_unknown_problem_type_in_avalanche_problems_raises(self) -> None:
        """Unknown problemType in avalancheProblems → RenderModelBuildError."""
        props = _load_sample("sample_unknown_problem_type.json")
        with pytest.raises(RenderModelBuildError, match="alien_snow_type"):
            build_render_model(props)

    def test_unknown_problem_type_in_aggregation_raises(self) -> None:
        """Unknown problemType in aggregation entries → RenderModelBuildError."""
        props: dict[str, Any] = {
            "bulletinID": "bad-agg-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow", "unknown_type_xyz"],
                            "title": "test",
                        }
                    ]
                }
            },
        }
        with pytest.raises(RenderModelBuildError, match="unknown_type_xyz"):
            build_render_model(props)

    def test_aggregation_problem_set_mismatch_raises(self) -> None:
        """Problem set mismatch between avalancheProblems and aggregation → error."""
        props: dict[str, Any] = {
            "bulletinID": "mismatch-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            # wind_slab is in aggregation but NOT in avalancheProblems
                            "problemTypes": ["wind_slab"],
                            "title": "test",
                        }
                    ]
                }
            },
        }
        with pytest.raises(RenderModelBuildError, match="mismatch"):
            build_render_model(props)

    def test_aggregation_entry_empty_problem_types_raises(self) -> None:
        """Aggregation entry with empty problemTypes → RenderModelBuildError."""
        props: dict[str, Any] = {
            "bulletinID": "empty-pt-001",
            "dangerRatings": [],
            "avalancheProblems": [],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": [],
                            "title": "test",
                        }
                    ]
                }
            },
        }
        with pytest.raises(RenderModelBuildError, match="empty problemTypes"):
            build_render_model(props)

    def test_aggregation_entry_missing_category_raises(self) -> None:
        """Aggregation entry with missing category → RenderModelBuildError."""
        props: dict[str, Any] = {
            "bulletinID": "no-cat-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            # category is missing
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "test",
                        }
                    ]
                }
            },
        }
        with pytest.raises(RenderModelBuildError, match="category"):
            build_render_model(props)

    def test_aggregation_entry_invalid_category_raises(self) -> None:
        """Aggregation entry with invalid category → RenderModelBuildError."""
        props: dict[str, Any] = {
            "bulletinID": "bad-cat-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "mixed",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "test",
                        }
                    ]
                }
            },
        }
        with pytest.raises(RenderModelBuildError, match="category"):
            build_render_model(props)

    def test_unknown_valid_time_period_on_problem_raises(self) -> None:
        """Unknown validTimePeriod on an avalanche problem → RenderModelBuildError."""
        props: dict[str, Any] = {
            "bulletinID": "bad-vtp-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "morning",  # invalid
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "test",
                        }
                    ]
                }
            },
        }
        with pytest.raises(RenderModelBuildError, match="validTimePeriod"):
            build_render_model(props)

    def test_none_valid_time_period_on_problem_is_allowed(self) -> None:
        """None validTimePeriod on a problem is permitted (treated as all_day)."""
        props: dict[str, Any] = {
            "bulletinID": "null-vtp-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": None,
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "test",
                        }
                    ]
                }
            },
        }
        # Should not raise
        rm = build_render_model(props)
        assert len(rm["traits"]) == 1


# ---------------------------------------------------------------------------
# build_render_model — title fallback
# ---------------------------------------------------------------------------


class TestBuildRenderModelTitleFallback:
    """Tests for derived title when aggregation entry has blank title."""

    def test_empty_title_dry_all_day_gets_fallback(self) -> None:
        """Aggregation entry with empty title → 'Dry avalanches'."""
        props: dict[str, Any] = {
            "bulletinID": "no-title-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "",
                        }
                    ]
                }
            },
        }
        rm = build_render_model(props)
        assert rm["traits"][0]["title"] == "Dry avalanches"

    def test_empty_title_wet_later_gets_fallback(self) -> None:
        """Aggregation entry with empty title and wet/later → 'Wet avalanches, later'."""
        props: dict[str, Any] = {
            "bulletinID": "no-title-wet-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "wet",
                            "validTimePeriod": "later",
                            "problemTypes": ["wet_snow"],
                            "title": "",
                        }
                    ]
                }
            },
        }
        rm = build_render_model(props)
        assert rm["traits"][0]["title"] == "Wet avalanches, later"

    def test_empty_title_dry_earlier_gets_fallback(self) -> None:
        """Aggregation entry with empty title and dry/earlier → 'Dry avalanches, earlier'."""
        props: dict[str, Any] = {
            "bulletinID": "no-title-earlier-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "earlier",
                    "aspects": ["N"],
                }
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "earlier",
                            "problemTypes": ["wind_slab"],
                            "title": "",
                        }
                    ]
                }
            },
        }
        rm = build_render_model(props)
        assert rm["traits"][0]["title"] == "Dry avalanches, earlier"


# ---------------------------------------------------------------------------
# build_render_model — 3+ aggregation entries warning
# ---------------------------------------------------------------------------


class TestBuildRenderModel3PlusTraits:
    """Tests for bulletins with 3 or more aggregation entries."""

    def test_three_aggregation_entries_emits_warning(self) -> None:
        """3 aggregation entries → warning logged, all 3 traits emitted, no raise."""
        from unittest.mock import patch

        props: dict[str, Any] = {
            "bulletinID": "three-traits-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                },
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["NE"],
                },
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                },
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "New snow",
                        },
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["wind_slab"],
                            "title": "Wind slab",
                        },
                        {
                            "category": "wet",
                            "validTimePeriod": "later",
                            "problemTypes": ["wet_snow"],
                            "title": "Wet snow",
                        },
                    ]
                }
            },
        }
        with patch("bulletins.services.render_model.logger") as mock_logger:
            rm = build_render_model(props)

        assert len(rm["traits"]) == 3
        # Warning was called — the format string mentions traits and the arg is 3.
        warning_calls = mock_logger.warning.call_args_list
        assert any("traits" in str(call) and 3 in call.args for call in warning_calls)

    def test_three_aggregation_entries_does_not_raise(self) -> None:
        """3 aggregation entries → does not raise RenderModelBuildError."""
        props: dict[str, Any] = {
            "bulletinID": "three-traits-002",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                },
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["NE"],
                },
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                },
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "New snow",
                        },
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["wind_slab"],
                            "title": "Wind slab",
                        },
                        {
                            "category": "wet",
                            "validTimePeriod": "later",
                            "problemTypes": ["wet_snow"],
                            "title": "Wet snow",
                        },
                    ]
                }
            },
        }
        # Should not raise
        rm = build_render_model(props)
        assert rm["version"] == RENDER_MODEL_VERSION


# ---------------------------------------------------------------------------
# build_render_model — aggregation ordering preserved
# ---------------------------------------------------------------------------


class TestBuildRenderModelAggregationOrdering:
    """Tests that aggregation entry order is preserved verbatim in traits."""

    def test_wet_first_order_preserved(self) -> None:
        """When SLF puts wet first in aggregation, output trait order matches."""
        props: dict[str, Any] = {
            "bulletinID": "wet-first-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["S"],
                },
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                },
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        # Wet comes first (unusual but valid)
                        {
                            "category": "wet",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["wet_snow"],
                            "title": "Wet avalanches first",
                        },
                        {
                            "category": "dry",
                            "validTimePeriod": "all_day",
                            "problemTypes": ["new_snow"],
                            "title": "Dry avalanches second",
                        },
                    ]
                }
            },
        }
        rm = build_render_model(props)
        assert rm["traits"][0]["category"] == "wet"
        assert rm["traits"][1]["category"] == "dry"

    def test_problem_order_within_trait_preserved(self) -> None:
        """Problems within a trait follow aggregation's problemTypes order."""
        props: dict[str, Any] = {
            "bulletinID": "problem-order-001",
            "dangerRatings": [],
            "avalancheProblems": [
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                },
                {
                    "problemType": "gliding_snow",
                    "validTimePeriod": "later",
                    "aspects": [],
                },
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "wet",
                            "validTimePeriod": "later",
                            # gliding_snow listed first in the aggregation
                            "problemTypes": ["gliding_snow", "wet_snow"],
                            "title": "Wet avalanches",
                        }
                    ]
                }
            },
        }
        rm = build_render_model(props)
        trait = rm["traits"][0]
        assert trait["problems"][0]["problem_type"] == "gliding_snow"
        assert trait["problems"][1]["problem_type"] == "wet_snow"


# ---------------------------------------------------------------------------
# compute_day_character — integration with build_render_model
# ---------------------------------------------------------------------------


class TestComputeDayCharacterRoundTrip:
    """Integration tests building render models then computing day character."""

    def test_stable_day_end_to_end(self) -> None:
        """Stable day fixture produces 'Stable day' end-to-end."""
        props = _load_sample("sample_stable_day.json")
        rm = build_render_model(props)
        assert compute_day_character(rm).label == "Stable day"

    def test_empty_traits_defaults_to_stable(self) -> None:
        """Render model with empty traits defaults to Stable day."""
        rm = {
            "version": RENDER_MODEL_VERSION,
            "danger": {"key": "low", "number": "1", "subdivision": None},
            "traits": [],
        }
        assert compute_day_character(rm).label == "Stable day"

    def test_multiple_traits_flattened_for_rules(self) -> None:
        """Problems across multiple traits are flattened for rule evaluation."""
        rm = {
            "version": RENDER_MODEL_VERSION,
            "danger": {"key": "considerable", "number": "3", "subdivision": None},
            "traits": [
                {
                    "category": "dry",
                    "time_period": "all_day",
                    "problems": [
                        {
                            "problem_type": "new_snow",
                            "aspects": ["N", "NE", "E", "SE"],
                            "elevation": None,
                        }
                    ],
                },
                {
                    "category": "wet",
                    "time_period": "later",
                    "problems": [
                        {
                            "problem_type": "wet_snow",
                            "aspects": ["S", "SW", "W"],
                            "elevation": None,
                        }
                    ],
                },
            ],
        }
        # 7 unique aspects across both traits → Widespread danger
        assert compute_day_character(rm).label == "Widespread danger"

    def test_danger_zero_hits_safe_default(self) -> None:
        """Danger number 0 (malformed input) hits the safe default return path."""
        rm = {
            "version": RENDER_MODEL_VERSION,
            "danger": {"key": "low", "number": "0", "subdivision": None},
            "traits": [
                {
                    "problems": [
                        {"problem_type": "new_snow", "aspects": [], "elevation": None}
                    ]
                }
            ],
        }
        assert compute_day_character(rm).label == "Stable day"


# ---------------------------------------------------------------------------
# Version 3 — RENDER_MODEL_VERSION constant and build_render_model version
# ---------------------------------------------------------------------------


class TestRenderModelVersion:
    """Tests that RENDER_MODEL_VERSION and built version are both 5."""

    def test_constant_is_current(self) -> None:
        """RENDER_MODEL_VERSION constant equals the current version."""
        assert RENDER_MODEL_VERSION == 5

    def test_build_render_model_returns_current_version(self) -> None:
        """build_render_model returns a dict with the current version."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert rm["version"] == RENDER_MODEL_VERSION


# ---------------------------------------------------------------------------
# Version 3 — _parse_iso_timestamp
# ---------------------------------------------------------------------------


class TestParseIsoTimestamp:
    """Tests for the _parse_iso_timestamp helper."""

    def test_z_suffix_parses(self) -> None:
        """Timestamp ending in Z is parsed and returned as ISO string."""
        result = _parse_iso_timestamp("2026-04-08T15:00:00Z")
        assert result is not None
        assert "2026-04-08" in result

    def test_plus_offset_parses(self) -> None:
        """Timestamp with +00:00 offset is parsed correctly."""
        result = _parse_iso_timestamp("2026-04-08T15:00:00+00:00")
        assert result is not None
        assert "2026-04-08" in result

    def test_none_returns_none(self) -> None:
        """None input returns None."""
        assert _parse_iso_timestamp(None) is None

    def test_empty_string_returns_none(self) -> None:
        """Empty string returns None."""
        assert _parse_iso_timestamp("") is None

    def test_bad_string_returns_none(self) -> None:
        """Unparseable string returns None without raising."""
        assert _parse_iso_timestamp("not a date") is None

    def test_integer_returns_none(self) -> None:
        """Non-string value returns None without raising."""
        assert _parse_iso_timestamp(12345) is None


# ---------------------------------------------------------------------------
# Version 3 — _build_metadata happy path
# ---------------------------------------------------------------------------


class TestBuildMetadataHappyPath:
    """Tests for _build_metadata using sample_variable_day.json."""

    def test_all_six_keys_present(self) -> None:
        """Metadata dict always has all six keys."""
        props = _load_sample("sample_variable_day.json")
        metadata = _build_metadata(props)
        assert set(metadata.keys()) == {
            "publication_time",
            "valid_from",
            "valid_until",
            "next_update",
            "unscheduled",
            "lang",
        }

    def test_publication_time_populated(self) -> None:
        """publication_time is extracted from publicationTime."""
        props = _load_sample("sample_variable_day.json")
        metadata = _build_metadata(props)
        assert metadata["publication_time"] is not None
        assert "2026-04-08" in metadata["publication_time"]

    def test_valid_from_populated(self) -> None:
        """valid_from is extracted from validTime.startTime."""
        props = _load_sample("sample_variable_day.json")
        metadata = _build_metadata(props)
        assert metadata["valid_from"] is not None

    def test_valid_until_populated(self) -> None:
        """valid_until is extracted from validTime.endTime."""
        props = _load_sample("sample_variable_day.json")
        metadata = _build_metadata(props)
        assert metadata["valid_until"] is not None

    def test_next_update_populated(self) -> None:
        """next_update is extracted from nextUpdate."""
        props = _load_sample("sample_variable_day.json")
        metadata = _build_metadata(props)
        assert metadata["next_update"] is not None

    def test_unscheduled_is_bool(self) -> None:
        """unscheduled is a boolean."""
        props = _load_sample("sample_variable_day.json")
        metadata = _build_metadata(props)
        assert isinstance(metadata["unscheduled"], bool)
        assert metadata["unscheduled"] is False

    def test_lang_is_string(self) -> None:
        """lang is extracted from the properties."""
        props = _load_sample("sample_variable_day.json")
        metadata = _build_metadata(props)
        assert metadata["lang"] == "en"


# ---------------------------------------------------------------------------
# Version 3 — _build_metadata missing fields
# ---------------------------------------------------------------------------


class TestBuildMetadataMissingFields:
    """Tests for _build_metadata when optional fields are absent."""

    def test_missing_publication_time_returns_none(self) -> None:
        """Missing publicationTime → publication_time: None."""
        props: dict[str, Any] = {}
        metadata = _build_metadata(props)
        assert metadata["publication_time"] is None

    def test_missing_next_update_returns_none(self) -> None:
        """Missing nextUpdate → next_update: None."""
        props: dict[str, Any] = {}
        metadata = _build_metadata(props)
        assert metadata["next_update"] is None

    def test_missing_valid_time_returns_none_pair(self) -> None:
        """Missing validTime → valid_from and valid_until both None."""
        props: dict[str, Any] = {}
        metadata = _build_metadata(props)
        assert metadata["valid_from"] is None
        assert metadata["valid_until"] is None

    def test_missing_unscheduled_defaults_to_false(self) -> None:
        """Missing unscheduled defaults to False."""
        props: dict[str, Any] = {}
        metadata = _build_metadata(props)
        assert metadata["unscheduled"] is False

    def test_missing_lang_defaults_to_en(self) -> None:
        """Missing lang defaults to 'en'."""
        props: dict[str, Any] = {}
        metadata = _build_metadata(props)
        assert metadata["lang"] == "en"


# ---------------------------------------------------------------------------
# Version 3 — _build_metadata bad timestamp
# ---------------------------------------------------------------------------


class TestBuildMetadataBadTimestamp:
    """Tests for _build_metadata with unparseable timestamp."""

    def test_bad_publication_time_returns_none(self) -> None:
        """Unparseable publicationTime → publication_time: None, no raise."""
        props: dict[str, Any] = {"publicationTime": "not a date"}
        metadata = _build_metadata(props)
        assert metadata["publication_time"] is None

    def test_other_fields_unaffected_by_bad_timestamp(self) -> None:
        """Bad publicationTime does not affect other metadata fields."""
        props: dict[str, Any] = {
            "publicationTime": "not a date",
            "lang": "de",
            "unscheduled": True,
        }
        metadata = _build_metadata(props)
        assert metadata["publication_time"] is None
        assert metadata["lang"] == "de"
        assert metadata["unscheduled"] is True


# ---------------------------------------------------------------------------
# Version 3 — _build_prose happy path
# ---------------------------------------------------------------------------


class TestBuildProseHappyPath:
    """Tests for _build_prose using sample_variable_day.json."""

    def test_all_keys_present(self) -> None:
        """Prose dict has all keys (v5: includes tendency_lead)."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        assert set(prose.keys()) == {
            "snowpack_structure",
            "weather_review",
            "weather_forecast",
            "tendency",
            "avalanche_activity",
            "tendency_lead",
        }

    def test_snowpack_structure_is_html_string(self) -> None:
        """snowpack_structure is a non-empty HTML string."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        assert prose["snowpack_structure"] is not None
        assert len(prose["snowpack_structure"]) > 0

    def test_weather_review_is_html_string(self) -> None:
        """weather_review is a non-empty HTML string."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        assert prose["weather_review"] is not None
        assert len(prose["weather_review"]) > 0

    def test_weather_forecast_is_html_string(self) -> None:
        """weather_forecast is a non-empty HTML string."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        assert prose["weather_forecast"] is not None
        assert len(prose["weather_forecast"]) > 0

    def test_tendency_is_list(self) -> None:
        """tendency is a list."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        assert isinstance(prose["tendency"], list)

    def test_tendency_entries_have_required_keys(self) -> None:
        """Each tendency entry has comment, tendency_type, valid_from, valid_until."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        for entry in prose["tendency"]:
            assert "comment" in entry
            assert "tendency_type" in entry
            assert "valid_from" in entry
            assert "valid_until" in entry


# ---------------------------------------------------------------------------
# Version 3 — _build_prose tendency details
# ---------------------------------------------------------------------------


class TestBuildProseTendency:
    """Tests for tendency extraction in _build_prose."""

    def test_tendency_comment_preserved(self) -> None:
        """Tendency comment HTML string is preserved verbatim."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        # Sample has one tendency entry with a comment.
        assert len(prose["tendency"]) == 1
        assert len(prose["tendency"][0]["comment"]) > 0

    def test_tendency_type_none_when_absent(self) -> None:
        """tendency_type is None when tendencyType is absent in raw data."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        # The sample fixture has no tendencyType key.
        assert prose["tendency"][0]["tendency_type"] is None

    def test_tendency_type_round_trip(self) -> None:
        """tendencyType value is stored as tendency_type."""
        props: dict[str, Any] = {
            "tendency": [
                {
                    "comment": "<p>Decreasing hazard.</p>",
                    "tendencyType": "decreasing",
                    "validTime": {
                        "startTime": "2026-04-09T15:00:00Z",
                        "endTime": "2026-04-10T15:00:00Z",
                    },
                }
            ]
        }
        prose = _build_prose(props)
        entry = prose["tendency"][0]
        assert entry["tendency_type"] == "decreasing"
        assert entry["valid_from"] is not None
        assert "2026-04-09" in entry["valid_from"]
        assert entry["valid_until"] is not None
        assert "2026-04-10" in entry["valid_until"]

    def test_tendency_steady_round_trip(self) -> None:
        """'steady' tendencyType round-trips correctly."""
        props: dict[str, Any] = {
            "tendency": [
                {
                    "comment": "<p>Steady.</p>",
                    "tendencyType": "steady",
                    "validTime": {
                        "startTime": "2026-04-09T15:00:00Z",
                        "endTime": "2026-04-10T15:00:00Z",
                    },
                }
            ]
        }
        prose = _build_prose(props)
        assert prose["tendency"][0]["tendency_type"] == "steady"

    def test_tendency_increasing_round_trip(self) -> None:
        """'increasing' tendencyType round-trips correctly."""
        props: dict[str, Any] = {
            "tendency": [
                {
                    "comment": "<p>Increasing risk.</p>",
                    "tendencyType": "increasing",
                }
            ]
        }
        prose = _build_prose(props)
        assert prose["tendency"][0]["tendency_type"] == "increasing"
        assert prose["tendency"][0]["valid_from"] is None
        assert prose["tendency"][0]["valid_until"] is None


# ---------------------------------------------------------------------------
# Version 3 — _build_prose all missing
# ---------------------------------------------------------------------------


class TestBuildProseAllMissing:
    """Tests for _build_prose with empty properties."""

    def test_empty_properties_returns_none_scalars(self) -> None:
        """Empty properties → all three scalar fields are None."""
        props: dict[str, Any] = {}
        prose = _build_prose(props)
        assert prose["snowpack_structure"] is None
        assert prose["weather_review"] is None
        assert prose["weather_forecast"] is None

    def test_empty_properties_returns_empty_tendency(self) -> None:
        """Empty properties → tendency is empty list."""
        props: dict[str, Any] = {}
        prose = _build_prose(props)
        assert prose["tendency"] == []


# ---------------------------------------------------------------------------
# Version 3 — back-compat: top-level snowpack_structure == prose.snowpack_structure
# ---------------------------------------------------------------------------


class TestBackCompatSnowpackStructure:
    """Tests that top-level snowpack_structure mirrors prose.snowpack_structure."""

    def test_top_level_equals_prose_value(self) -> None:
        """render_model['snowpack_structure'] == render_model['prose']['snowpack_structure']."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert rm["snowpack_structure"] == rm["prose"]["snowpack_structure"]

    def test_top_level_equals_prose_when_none(self) -> None:
        """Both are None when snowpackStructure is absent."""
        props: dict[str, Any] = {
            "bulletinID": "no-snowpack-001",
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
            "customData": {"CH": {}},
        }
        rm = build_render_model(props)
        assert rm["snowpack_structure"] is None
        assert rm["prose"]["snowpack_structure"] is None


# ---------------------------------------------------------------------------
# Version 3 — day character still works across all sample fixtures
# ---------------------------------------------------------------------------


class TestDayCharacterAcrossAllSamples:
    """Parametrise compute_day_character over all sample_*.json files."""

    @pytest.mark.parametrize(
        "filename",
        [
            p.name
            for p in (_FIXTURE_DIR).iterdir()
            if p.name.startswith("sample_") and p.name.endswith(".json")
        ],
    )
    def test_day_character_does_not_raise(self, filename: str) -> None:
        """compute_day_character produces a label without raising for every sample."""
        valid_labels = {
            "Stable day",
            "Manageable day",
            "Hard-to-read day",
            "Widespread danger",
            "Dangerous conditions",
        }
        path = _FIXTURE_DIR / filename
        data = json.loads(path.read_text())
        props = data["properties"]
        # Skip fixtures we know raise RenderModelBuildError.
        try:
            rm = build_render_model(props)
        except RenderModelBuildError:
            pytest.skip(f"{filename} is expected to raise RenderModelBuildError")
        result = compute_day_character(rm)
        assert result.label in valid_labels
        assert result.explainer


# ---------------------------------------------------------------------------
# Version 3 — quiet day: empty problems, prose and metadata still populate
# ---------------------------------------------------------------------------


class TestQuietDayV3:
    """Tests for quiet-day bulletins (no avalanche problems) with v3 fields."""

    def test_traits_empty_on_quiet_day(self) -> None:
        """Quiet day (no problems, no aggregation) → traits == []."""
        props: dict[str, Any] = {
            "bulletinID": "quiet-v3-001",
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
            "publicationTime": "2026-04-08T15:00:00Z",
            "validTime": {
                "startTime": "2026-04-08T15:00:00Z",
                "endTime": "2026-04-09T15:00:00Z",
            },
            "snowpackStructure": {"comment": "<p>All quiet.</p>"},
            "weatherReview": {"comment": "<p>Sunny.</p>"},
            "tendency": [{"comment": "<p>Stable outlook.</p>"}],
            "customData": {"CH": {}},
        }
        rm = build_render_model(props)
        assert rm["traits"] == []

    def test_metadata_populates_on_quiet_day(self) -> None:
        """Quiet day metadata is populated even with empty problems."""
        props: dict[str, Any] = {
            "bulletinID": "quiet-v3-002",
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
            "publicationTime": "2026-04-08T15:00:00Z",
            "validTime": {
                "startTime": "2026-04-08T15:00:00Z",
                "endTime": "2026-04-09T15:00:00Z",
            },
            "lang": "fr",
            "customData": {"CH": {}},
        }
        rm = build_render_model(props)
        assert rm["metadata"]["publication_time"] is not None
        assert rm["metadata"]["valid_from"] is not None
        assert rm["metadata"]["lang"] == "fr"

    def test_prose_populates_on_quiet_day(self) -> None:
        """Quiet day prose fields are populated even with empty problems."""
        props: dict[str, Any] = {
            "bulletinID": "quiet-v3-003",
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
            "snowpackStructure": {"comment": "<p>All quiet.</p>"},
            "weatherReview": {"comment": "<p>Clear skies.</p>"},
            "weatherForecast": {"comment": "<p>Continuing fine.</p>"},
            "tendency": [{"comment": "<p>No change expected.</p>"}],
            "customData": {"CH": {}},
        }
        rm = build_render_model(props)
        assert rm["prose"]["snowpack_structure"] == "<p>All quiet.</p>"
        assert rm["prose"]["weather_review"] == "<p>Clear skies.</p>"
        assert rm["prose"]["weather_forecast"] == "<p>Continuing fine.</p>"
        assert len(rm["prose"]["tendency"]) == 1


# ---------------------------------------------------------------------------
# Version 4 — _detect_source
# ---------------------------------------------------------------------------


class TestDetectSource:
    """Tests for _detect_source helper."""

    def test_slf_via_ch_custom_data(self) -> None:
        """Properties with customData.CH → Bulletin.Source.SLF."""
        props: dict[str, Any] = {
            "customData": {"CH": {"aggregation": []}},
        }
        assert _detect_source(props) == Bulletin.Source.SLF

    def test_euregio_via_albina_custom_data(self) -> None:
        """Properties with customData.ALBINA → Bulletin.Source.EUREGIO."""
        props: dict[str, Any] = {
            "customData": {"ALBINA": {"mainDate": "2026-05-03"}},
        }
        assert _detect_source(props) == Bulletin.Source.EUREGIO

    def test_euregio_via_lwd_key(self) -> None:
        """Properties with customData.LWD_Tyrol → Bulletin.Source.EUREGIO."""
        props: dict[str, Any] = {
            "customData": {"LWD_Tyrol": {"dangerPatterns": ["DP10"]}},
        }
        assert _detect_source(props) == Bulletin.Source.EUREGIO

    def test_euregio_via_combined_custom_data(self) -> None:
        """Properties with both ALBINA and LWD_Tyrol → Bulletin.Source.EUREGIO."""
        props: dict[str, Any] = {
            "customData": {
                "ALBINA": {"mainDate": "2026-05-03"},
                "LWD_Tyrol": {"dangerPatterns": ["DP10"]},
            },
        }
        assert _detect_source(props) == Bulletin.Source.EUREGIO

    def test_meteofrance_via_mf_custom_data(self) -> None:
        """Properties with customData.MF → Bulletin.Source.MF."""
        props: dict[str, Any] = {
            "customData": {"MF": {"massif_id": "1"}},
        }
        assert _detect_source(props) == Bulletin.Source.MF

    def test_raises_when_no_custom_data(self) -> None:
        """No customData → RenderModelBuildError naming the offending keys."""
        props: dict[str, Any] = {}
        with pytest.raises(
            RenderModelBuildError, match="no recognised customData marker"
        ):
            _detect_source(props)

    def test_raises_when_unknown_custom_data_keys(self) -> None:
        """Unrecognised customData keys → RenderModelBuildError listing the keys."""
        props: dict[str, Any] = {
            "customData": {"UNKNOWN": {"foo": "bar"}},
        }
        with pytest.raises(RenderModelBuildError) as exc_info:
            _detect_source(props)
        assert "UNKNOWN" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Version 4 — _resolve_aggregations
# ---------------------------------------------------------------------------


class TestResolveAggregations:
    """Tests for _resolve_aggregations helper."""

    def test_slf_returns_ch_aggregation(self) -> None:
        """SLF source reads customData.CH.aggregation verbatim."""
        agg = [
            {
                "category": "dry",
                "problemTypes": ["new_snow"],
                "validTimePeriod": "all_day",
            }
        ]
        props: dict[str, Any] = {"customData": {"CH": {"aggregation": agg}}}
        result = _resolve_aggregations(props, Bulletin.Source.SLF)
        assert result == agg

    def test_slf_empty_aggregation(self) -> None:
        """SLF source with no aggregation returns empty list."""
        props: dict[str, Any] = {}
        result = _resolve_aggregations(props, Bulletin.Source.SLF)
        assert result == []

    def test_euregio_single_dry_problem(self) -> None:
        """EUREGIO with one dry problem synthesises one dry aggregation entry."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ]
        }
        result = _resolve_aggregations(props, Bulletin.Source.EUREGIO)
        assert len(result) == 1
        assert result[0]["category"] == "dry"
        assert result[0]["problemTypes"] == ["wind_slab"]
        assert result[0]["validTimePeriod"] == "all_day"

    def test_euregio_single_wet_problem(self) -> None:
        """EUREGIO with one wet problem synthesises one wet aggregation entry."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                }
            ]
        }
        result = _resolve_aggregations(props, Bulletin.Source.EUREGIO)
        assert len(result) == 1
        assert result[0]["category"] == "wet"
        assert result[0]["problemTypes"] == ["wet_snow"]
        assert result[0]["validTimePeriod"] == "later"

    def test_euregio_dry_split_across_time_periods(self) -> None:
        """EUREGIO dry problems in different time periods produce two entries."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "persistent_weak_layers",
                    "validTimePeriod": "earlier",
                    "aspects": ["NW"],
                },
                {
                    "problemType": "persistent_weak_layers",
                    "validTimePeriod": "later",
                    "aspects": ["NW"],
                },
            ]
        }
        result = _resolve_aggregations(props, Bulletin.Source.EUREGIO)
        assert len(result) == 2
        assert result[0]["validTimePeriod"] == "earlier"
        assert result[1]["validTimePeriod"] == "later"
        assert all(e["category"] == "dry" for e in result)

    def test_euregio_mixed_dry_and_wet(self) -> None:
        """EUREGIO with dry + wet problems synthesises two separate entries."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                },
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                },
            ]
        }
        result = _resolve_aggregations(props, Bulletin.Source.EUREGIO)
        assert len(result) == 2
        categories = {e["category"] for e in result}
        assert categories == {"dry", "wet"}

    def test_euregio_empty_problems(self) -> None:
        """EUREGIO with no problems returns empty aggregation."""
        props: dict[str, Any] = {"avalancheProblems": []}
        result = _resolve_aggregations(props, Bulletin.Source.EUREGIO)
        assert result == []

    def test_mf_single_dry_problem(self) -> None:
        """MF with one dry problem synthesises one dry aggregation entry (shared path)."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ]
        }
        result = _resolve_aggregations(props, Bulletin.Source.MF)
        assert len(result) == 1
        assert result[0]["category"] == "dry"
        assert result[0]["problemTypes"] == ["wind_slab"]

    def test_mf_single_wet_problem(self) -> None:
        """MF with one wet problem synthesises one wet aggregation entry."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["S"],
                }
            ]
        }
        result = _resolve_aggregations(props, Bulletin.Source.MF)
        assert len(result) == 1
        assert result[0]["category"] == "wet"

    def test_mf_dry_and_wet_problems(self) -> None:
        """MF dry + wet problems produce two separate aggregation entries."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["S"],
                },
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                },
            ]
        }
        result = _resolve_aggregations(props, Bulletin.Source.MF)
        assert len(result) == 2
        categories = {e["category"] for e in result}
        assert categories == {"dry", "wet"}


# ---------------------------------------------------------------------------
# Version 4 — _resolve_problem_rating (EUREGIO branch)
# ---------------------------------------------------------------------------


class TestResolveProblemRatingEuregio:
    """Tests for _resolve_problem_rating EUREGIO branch."""

    def _ratings_low_earlier_moderate_later_above_2600(
        self,
    ) -> list[dict[str, Any]]:
        """Return a representative danger rating set (earlier=low, later moderate above 2600)."""
        return [
            {"mainValue": "low", "validTimePeriod": "earlier"},
            {
                "mainValue": "low",
                "elevation": {"upperBound": "2600"},
                "validTimePeriod": "later",
            },
            {
                "mainValue": "moderate",
                "elevation": {"lowerBound": "2600"},
                "validTimePeriod": "later",
            },
        ]

    def test_earlier_problem_matches_earlier_low_rating(self) -> None:
        """Problem in 'earlier' period gets the low rating."""
        problem: dict[str, Any] = {
            "problemType": "persistent_weak_layers",
            "validTimePeriod": "earlier",
            "elevation": {"lowerBound": "2800"},
        }
        result = _resolve_problem_rating(
            problem,
            self._ratings_low_earlier_moderate_later_above_2600(),
            Bulletin.Source.EUREGIO,
        )
        assert result == "low"

    def test_later_high_elevation_problem_gets_moderate(self) -> None:
        """Problem in 'later' at high elevation (>= 2600) gets moderate."""
        problem: dict[str, Any] = {
            "problemType": "wet_snow",
            "validTimePeriod": "later",
            "elevation": {"lowerBound": "2600"},
        }
        result = _resolve_problem_rating(
            problem,
            self._ratings_low_earlier_moderate_later_above_2600(),
            Bulletin.Source.EUREGIO,
        )
        assert result == "moderate"

    def test_slf_reads_danger_rating_value_directly(self) -> None:
        """SLF problem reads dangerRatingValue directly."""
        problem: dict[str, Any] = {
            "problemType": "new_snow",
            "dangerRatingValue": "considerable",
        }
        result = _resolve_problem_rating(problem, [], Bulletin.Source.SLF)
        assert result == "considerable"

    def test_slf_missing_danger_rating_value_returns_none(self) -> None:
        """SLF problem with no dangerRatingValue returns None."""
        problem: dict[str, Any] = {"problemType": "wet_snow"}
        result = _resolve_problem_rating(problem, [], Bulletin.Source.SLF)
        assert result is None


# ---------------------------------------------------------------------------
# Version 4 — _resolve_problem_extras
# ---------------------------------------------------------------------------


class TestResolveProblemExtras:
    """Tests for _resolve_problem_extras helper."""

    def test_slf_returns_subdivision_and_core_zone_text(self) -> None:
        """SLF problem returns subdivision and core_zone_text extras."""
        problem: dict[str, Any] = {
            "customData": {
                "CH": {
                    "subdivision": "plus",
                    "coreZoneText": "Danger level 3+ above 2800m.",
                }
            }
        }
        extras = _resolve_problem_extras(problem, Bulletin.Source.SLF)
        assert extras["subdivision"] == "plus"
        assert extras["core_zone_text"] == "Danger level 3+ above 2800m."

    def test_slf_missing_ch_data_returns_defaults(self) -> None:
        """SLF problem missing CH data returns empty subdivision and None core_zone_text."""
        problem: dict[str, Any] = {}
        extras = _resolve_problem_extras(problem, Bulletin.Source.SLF)
        assert extras["subdivision"] == ""
        assert extras["core_zone_text"] is None

    def test_euregio_returns_avalanche_type_slab(self) -> None:
        """EUREGIO problem with slab type returns avalanche_type='slab'."""
        problem: dict[str, Any] = {"customData": {"ALBINA": {"avalancheType": "slab"}}}
        extras = _resolve_problem_extras(problem, Bulletin.Source.EUREGIO)
        assert extras["avalanche_type"] == "slab"

    def test_euregio_returns_avalanche_type_loose(self) -> None:
        """EUREGIO problem with loose type returns avalanche_type='loose'."""
        problem: dict[str, Any] = {"customData": {"ALBINA": {"avalancheType": "loose"}}}
        extras = _resolve_problem_extras(problem, Bulletin.Source.EUREGIO)
        assert extras["avalanche_type"] == "loose"

    def test_euregio_missing_albina_data_returns_none_type(self) -> None:
        """EUREGIO problem missing ALBINA data returns avalanche_type=None."""
        problem: dict[str, Any] = {}
        extras = _resolve_problem_extras(problem, Bulletin.Source.EUREGIO)
        assert extras["avalanche_type"] is None

    def test_euregio_extras_do_not_include_slf_fields(self) -> None:
        """EUREGIO extras do not include subdivision or core_zone_text."""
        problem: dict[str, Any] = {}
        extras = _resolve_problem_extras(problem, Bulletin.Source.EUREGIO)
        assert "subdivision" not in extras
        assert "core_zone_text" not in extras


# ---------------------------------------------------------------------------
# Version 4 — _resolve_avalanche_activity
# ---------------------------------------------------------------------------


class TestResolveAvalancheActivity:
    """Tests for _resolve_avalanche_activity helper."""

    def test_slf_returns_empty_strings(self) -> None:
        """SLF source always returns empty highlights and comment."""
        props: dict[str, Any] = {
            "avalancheActivity": {
                "highlights": "Some highlights.",
                "comment": "Some comment.",
            }
        }
        result = _resolve_avalanche_activity(props, Bulletin.Source.SLF)
        assert result == {"highlights": "", "comment": ""}

    def test_euregio_returns_populated_activity(self) -> None:
        """EUREGIO source returns populated highlights and comment."""
        props: dict[str, Any] = {
            "avalancheActivity": {
                "highlights": "Weak layers require caution.",
                "comment": "Wet slides possible in afternoon.",
            }
        }
        result = _resolve_avalanche_activity(props, Bulletin.Source.EUREGIO)
        assert result["highlights"] == "Weak layers require caution."
        assert result["comment"] == "Wet slides possible in afternoon."

    def test_euregio_missing_activity_returns_empty_strings(self) -> None:
        """EUREGIO source with no avalancheActivity returns empty strings."""
        props: dict[str, Any] = {}
        result = _resolve_avalanche_activity(props, Bulletin.Source.EUREGIO)
        assert result == {"highlights": "", "comment": ""}


# ---------------------------------------------------------------------------
# Version 4 — _resolve_danger_patterns
# ---------------------------------------------------------------------------


class TestResolveDangerPatterns:
    """Tests for _resolve_danger_patterns helper."""

    def test_slf_returns_empty_list(self) -> None:
        """SLF source always returns empty list."""
        props: dict[str, Any] = {
            "customData": {"LWD_Tyrol": {"dangerPatterns": ["DP10"]}}
        }
        result = _resolve_danger_patterns(props, Bulletin.Source.SLF)
        assert result == []

    def test_euregio_returns_lwd_tyrol_patterns(self) -> None:
        """EUREGIO source returns dangerPatterns from LWD_Tyrol."""
        props: dict[str, Any] = {
            "customData": {"LWD_Tyrol": {"dangerPatterns": ["DP10", "DP1"]}}
        }
        result = _resolve_danger_patterns(props, Bulletin.Source.EUREGIO)
        assert result == ["DP10", "DP1"]

    def test_euregio_missing_patterns_returns_empty(self) -> None:
        """EUREGIO source with no patterns returns empty list."""
        props: dict[str, Any] = {"customData": {"ALBINA": {"mainDate": "2026-05-03"}}}
        result = _resolve_danger_patterns(props, Bulletin.Source.EUREGIO)
        assert result == []


# ---------------------------------------------------------------------------
# Version 4 — end-to-end with tests/fixtures/euregio_sample_bulletin.json
# ---------------------------------------------------------------------------


def _load_euregio_bulletin() -> dict[str, Any]:
    """Load the EUREGIO sample bulletin from tests/fixtures/euregio_sample_bulletin.json."""
    path = _FIXTURE_DIR / "euregio_sample_bulletin.json"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


class TestBuildRenderModelEuregio:
    """End-to-end tests using the committed EUREGIO sample bulletin."""

    def test_source_is_euregio(self) -> None:
        """EUREGIO bulletin produces source='euregio'."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        assert rm["source"] == "euregio"

    def test_has_populated_traits(self) -> None:
        """EUREGIO bulletin produces traits (non-empty)."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        assert len(rm["traits"]) > 0

    def test_has_danger(self) -> None:
        """EUREGIO bulletin produces a danger dict."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        assert rm["danger"]["key"] in {
            "low",
            "moderate",
            "considerable",
            "high",
            "very_high",
        }

    def test_avalanche_activity_populated(self) -> None:
        """EUREGIO bulletin prose has non-empty avalanche_activity."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        activity = rm["prose"]["avalanche_activity"]
        assert activity["highlights"] != "" or activity["comment"] != ""

    def test_danger_patterns_present(self) -> None:
        """EUREGIO bulletin has danger_patterns (at least one)."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        assert len(rm["danger_patterns"]) > 0

    def test_problems_have_avalanche_type(self) -> None:
        """EUREGIO problems carry avalanche_type in their extras."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        all_problems = [p for t in rm["traits"] for p in t["problems"]]
        # At least one problem should have a non-None avalanche_type.
        types = {p.get("avalanche_type") for p in all_problems}
        assert types - {None}, (
            f"Expected at least one non-None avalanche_type; got {types!r}"
        )

    def test_version_is_current(self) -> None:
        """EUREGIO bulletin render model has the current RENDER_MODEL_VERSION."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        assert rm["version"] == RENDER_MODEL_VERSION


# ---------------------------------------------------------------------------
# Version 4 — regression: SLF bulletins produce v4 shape with empty new fields
# ---------------------------------------------------------------------------


class TestV4SLFRegressionNewEmptySlots:
    """Regression tests: SLF bulletins produce v4 shape with empty new slots."""

    def test_slf_bulletin_source_is_slf(self) -> None:
        """SLF bulletin has source='slf'."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert rm["source"] == "slf"

    def test_slf_bulletin_danger_patterns_is_empty(self) -> None:
        """SLF bulletin has empty danger_patterns list."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert rm["danger_patterns"] == []

    def test_slf_bulletin_avalanche_activity_is_empty_strings(self) -> None:
        """SLF bulletin has empty avalanche_activity strings in prose."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        activity = rm["prose"]["avalanche_activity"]
        assert activity == {"highlights": "", "comment": ""}

    def test_slf_problem_has_avalanche_type_none(self) -> None:
        """SLF problems have avalanche_type=None."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        for trait in rm["traits"]:
            for problem in trait["problems"]:
                assert problem["avalanche_type"] is None

    def test_slf_problem_extras_contains_subdivision_and_core_zone_text(
        self,
    ) -> None:
        """SLF problems have extras with 'subdivision' and 'core_zone_text' keys."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        for trait in rm["traits"]:
            for problem in trait["problems"]:
                assert "subdivision" in problem["extras"]
                assert "core_zone_text" in problem["extras"]

    @pytest.mark.parametrize(
        "filename",
        [
            p.name
            for p in (_FIXTURE_DIR).iterdir()
            if p.name.startswith("sample_") and p.name.endswith(".json")
        ],
    )
    def test_sample_bulletins_v4_shape(self, filename: str) -> None:
        """Every SLF sample produces a v4 render model with the new slots."""
        path = _FIXTURE_DIR / filename
        data = json.loads(path.read_text())
        props = data["properties"]
        try:
            rm = build_render_model(props)
        except RenderModelBuildError:
            pytest.skip(f"{filename} is expected to raise RenderModelBuildError")
        # v4 fields present.
        assert "source" in rm
        assert "danger_patterns" in rm
        assert "avalanche_activity" in rm["prose"]
        # SLF-specific empty values.
        assert rm["source"] == "slf"
        assert rm["danger_patterns"] == []
        assert rm["prose"]["avalanche_activity"] == {"highlights": "", "comment": ""}


# ---------------------------------------------------------------------------
# Version 4 — MeteoFrance resolver unit tests
# ---------------------------------------------------------------------------


class TestResolveProblemMeteoFrance:
    """Targeted unit tests for resolver helpers on the MeteoFrance branch."""

    def test_mf_problem_rating_derived_from_danger_ratings(self) -> None:
        """MF rating is derived by elevation match, not dangerRatingValue."""
        problem: dict[str, Any] = {
            "problemType": "wet_snow",
            "validTimePeriod": "all_day",
            "elevation": {"lowerBound": "2400"},
        }
        ratings = [
            {
                "mainValue": "low",
                "elevation": {"upperBound": "2400"},
                "validTimePeriod": "all_day",
            },
            {
                "mainValue": "moderate",
                "elevation": {"lowerBound": "2400"},
                "validTimePeriod": "all_day",
            },
        ]
        result = _resolve_problem_rating(problem, ratings, Bulletin.Source.MF)
        assert result == "moderate"

    def test_mf_problem_comment_is_empty(self) -> None:
        """MF per-problem comment is always empty (activity lives at bulletin level)."""
        from bulletins.services.render_model import _resolve_problem_comment

        problem: dict[str, Any] = {"comment": "Some prose."}
        result = _resolve_problem_comment(problem, Bulletin.Source.MF)
        assert result == ""

    def test_mf_problem_extras_is_empty_dict(self) -> None:
        """MF extras dict is empty — no source-specific problem-level fields."""
        problem: dict[str, Any] = {}
        extras = _resolve_problem_extras(problem, Bulletin.Source.MF)
        assert extras == {}

    def test_mf_problem_avalanche_type_is_none(self) -> None:
        """MF avalanche_type is always None (no ALBINA customData)."""
        from bulletins.services.render_model import _resolve_problem_avalanche_type

        problem: dict[str, Any] = {}
        result = _resolve_problem_avalanche_type(problem, Bulletin.Source.MF)
        assert result is None

    def test_mf_avalanche_activity_populated(self) -> None:
        """MF avalanche activity is read from properties.avalancheActivity."""
        props: dict[str, Any] = {
            "avalancheActivity": {
                "highlights": "Wet slides possible.",
                "comment": "Caution near steep terrain.",
            }
        }
        result = _resolve_avalanche_activity(props, Bulletin.Source.MF)
        assert result["highlights"] == "Wet slides possible."
        assert result["comment"] == "Caution near steep terrain."

    def test_mf_avalanche_activity_missing_returns_empty_strings(self) -> None:
        """MF avalanche activity with no field returns empty strings."""
        props: dict[str, Any] = {}
        result = _resolve_avalanche_activity(props, Bulletin.Source.MF)
        assert result == {"highlights": "", "comment": ""}

    def test_mf_danger_patterns_is_empty(self) -> None:
        """MF danger_patterns is always an empty list."""
        props: dict[str, Any] = {"customData": {"MF": {"someField": "value"}}}
        result = _resolve_danger_patterns(props, Bulletin.Source.MF)
        assert result == []


# ---------------------------------------------------------------------------
# Version 4 — MeteoFrance end-to-end test
# ---------------------------------------------------------------------------


def _load_mf_bulletin(filename: str) -> dict[str, Any]:
    """Load a MF XML file and parse it with parse_dpbra_xml."""
    path = _MF_BULLETIN_DIR / filename
    xml_bytes = path.read_bytes()
    return parse_dpbra_xml(xml_bytes)


class TestBuildRenderModelMeteoFranceEndToEnd:
    """End-to-end tests using a captured MeteoFrance BRA XML bulletin."""

    def test_source_is_meteofrance(self) -> None:
        """MF bulletin produces source='meteofrance'."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        assert rm["source"] == "meteofrance"

    def test_has_populated_traits(self) -> None:
        """MF bulletin with avalancheProblems produces non-empty traits."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        assert len(rm["traits"]) > 0

    def test_danger_patterns_is_empty(self) -> None:
        """MF bulletin always has empty danger_patterns."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        assert rm["danger_patterns"] == []

    def test_avalanche_activity_is_populated(self) -> None:
        """MF bulletin prose has non-empty avalanche_activity."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        activity = rm["prose"]["avalanche_activity"]
        assert activity["highlights"] != "" or activity["comment"] != ""

    def test_version_is_current(self) -> None:
        """MF bulletin render model has the current RENDER_MODEL_VERSION."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        assert rm["version"] == RENDER_MODEL_VERSION

    def test_problem_extras_is_empty_dict(self) -> None:
        """MF problems carry an empty extras dict."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        for trait in rm["traits"]:
            for problem in trait["problems"]:
                assert problem["extras"] == {}

    def test_problem_avalanche_type_is_none(self) -> None:
        """MF problems always have avalanche_type=None."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        for trait in rm["traits"]:
            for problem in trait["problems"]:
                assert problem["avalanche_type"] is None


# ---------------------------------------------------------------------------
# Version 5 — SlfAdapter unit tests
# ---------------------------------------------------------------------------


class TestSlfAdapter:
    """Unit tests for the SlfAdapter class."""

    def _adapter(self) -> Any:
        from bulletins.services.render_model import SlfAdapter

        return SlfAdapter()

    def test_resolve_aggregations_reads_ch_key(self) -> None:
        """SlfAdapter reads aggregation from customData.CH verbatim."""
        agg = [
            {
                "category": "dry",
                "problemTypes": ["new_snow"],
                "validTimePeriod": "all_day",
            }
        ]
        props: dict[str, Any] = {"customData": {"CH": {"aggregation": agg}}}
        result = self._adapter().resolve_aggregations(props)
        assert result == agg

    def test_resolve_aggregations_missing_returns_empty(self) -> None:
        """SlfAdapter returns empty list when CH aggregation is absent."""
        result = self._adapter().resolve_aggregations({})
        assert result == []

    def test_resolve_problem_rating_reads_danger_rating_value(self) -> None:
        """SlfAdapter reads dangerRatingValue directly from the problem."""
        problem: dict[str, Any] = {"dangerRatingValue": "considerable"}
        result = self._adapter().resolve_problem_rating(problem, [])
        assert result == "considerable"

    def test_resolve_problem_rating_missing_returns_none(self) -> None:
        """SlfAdapter returns None when dangerRatingValue is absent."""
        result = self._adapter().resolve_problem_rating({}, [])
        assert result is None

    def test_resolve_problem_comment_returns_comment_field(self) -> None:
        """SlfAdapter returns the per-problem comment field."""
        problem: dict[str, Any] = {"comment": "<p>Wind slab hazard.</p>"}
        assert (
            self._adapter().resolve_problem_comment(problem)
            == "<p>Wind slab hazard.</p>"
        )

    def test_resolve_problem_extras_reads_subdivision_and_core_zone_text(self) -> None:
        """SlfAdapter reads subdivision and coreZoneText from customData.CH."""
        problem: dict[str, Any] = {
            "customData": {
                "CH": {"subdivision": "plus", "coreZoneText": "Danger 3+ above 2800m."}
            }
        }
        extras = self._adapter().resolve_problem_extras(problem)
        assert extras["subdivision"] == "plus"
        assert extras["core_zone_text"] == "Danger 3+ above 2800m."

    def test_resolve_problem_extras_missing_ch_returns_defaults(self) -> None:
        """SlfAdapter returns empty subdivision and None core_zone_text when CH absent."""
        extras = self._adapter().resolve_problem_extras({})
        assert extras["subdivision"] == ""
        assert extras["core_zone_text"] is None

    def test_resolve_problem_avalanche_type_is_always_none(self) -> None:
        """SlfAdapter always returns None for avalanche_type."""
        assert self._adapter().resolve_problem_avalanche_type({}) is None

    def test_resolve_problem_eaws_fields_all_none(self) -> None:
        """SlfAdapter returns None for all three EAWS problem fields."""
        fields = self._adapter().resolve_problem_eaws_fields({})
        assert fields["avalanche_size"] is None
        assert fields["frequency"] is None
        assert fields["snowpack_stability"] is None

    def test_resolve_avalanche_activity_always_empty(self) -> None:
        """SlfAdapter returns empty strings for avalanche_activity regardless of properties."""
        props: dict[str, Any] = {
            "avalancheActivity": {"highlights": "Some text.", "comment": "More text."}
        }
        result = self._adapter().resolve_avalanche_activity(props)
        assert result == {"highlights": "", "comment": ""}

    def test_resolve_danger_patterns_always_empty(self) -> None:
        """SlfAdapter always returns an empty danger_patterns list."""
        result = self._adapter().resolve_danger_patterns(
            {"customData": {"LWD_Tyrol": {"dangerPatterns": ["DP10"]}}}
        )
        assert result == []

    def test_resolve_tendency_lead_always_none(self) -> None:
        """SlfAdapter always returns None for tendency_lead."""
        props: dict[str, Any] = {"tendency": [{"highlights": "Some lead text."}]}
        assert self._adapter().resolve_tendency_lead(props) is None

    def test_resolve_danger_rating_subdivision_reads_ch(self) -> None:
        """SlfAdapter reads subdivision from customData.CH on a dangerRating."""
        rating: dict[str, Any] = {"customData": {"CH": {"subdivision": "plus"}}}
        assert self._adapter().resolve_danger_rating_subdivision(rating) == "+"

    def test_resolve_danger_rating_subdivision_minus(self) -> None:
        """SlfAdapter maps 'minus' to '-'."""
        rating: dict[str, Any] = {"customData": {"CH": {"subdivision": "minus"}}}
        assert self._adapter().resolve_danger_rating_subdivision(rating) == "-"

    def test_resolve_danger_rating_subdivision_none_when_absent(self) -> None:
        """SlfAdapter returns None when no CH subdivision is present."""
        assert self._adapter().resolve_danger_rating_subdivision({}) is None


# ---------------------------------------------------------------------------
# Version 5 — EuregioAdapter unit tests
# ---------------------------------------------------------------------------


class TestEuregioAdapter:
    """Unit tests for the EuregioAdapter class."""

    def _adapter(self) -> Any:
        from bulletins.services.render_model import EuregioAdapter

        return EuregioAdapter()

    def test_resolve_aggregations_dry_problem(self) -> None:
        """EuregioAdapter synthesises one dry aggregation entry from a dry problem."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ]
        }
        result = self._adapter().resolve_aggregations(props)
        assert len(result) == 1
        assert result[0]["category"] == "dry"
        assert result[0]["problemTypes"] == ["wind_slab"]

    def test_resolve_aggregations_wet_problem(self) -> None:
        """EuregioAdapter synthesises one wet entry from a wet problem."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                }
            ]
        }
        result = self._adapter().resolve_aggregations(props)
        assert result[0]["category"] == "wet"

    def test_resolve_aggregations_empty_problems_returns_empty(self) -> None:
        """EuregioAdapter returns empty aggregation when no problems exist."""
        assert self._adapter().resolve_aggregations({"avalancheProblems": []}) == []

    def test_resolve_problem_rating_matches_by_elevation_and_period(self) -> None:
        """EuregioAdapter derives rating by elevation + vtp matching."""
        problem: dict[str, Any] = {
            "problemType": "persistent_weak_layers",
            "validTimePeriod": "all_day",
            "elevation": {"lowerBound": "2000"},
        }
        ratings = [
            {
                "mainValue": "low",
                "elevation": {"upperBound": "2000"},
                "validTimePeriod": "all_day",
            },
            {
                "mainValue": "moderate",
                "elevation": {"lowerBound": "2000"},
                "validTimePeriod": "all_day",
            },
        ]
        result = self._adapter().resolve_problem_rating(problem, ratings)
        assert result == "moderate"

    def test_resolve_problem_comment_always_empty(self) -> None:
        """EuregioAdapter per-problem comment is always empty."""
        assert self._adapter().resolve_problem_comment({"comment": "Some text."}) == ""

    def test_resolve_problem_extras_reads_albina_avalanche_type(self) -> None:
        """EuregioAdapter extras include avalanche_type from customData.ALBINA."""
        problem: dict[str, Any] = {"customData": {"ALBINA": {"avalancheType": "slab"}}}
        extras = self._adapter().resolve_problem_extras(problem)
        assert extras["avalanche_type"] == "slab"

    def test_resolve_problem_extras_missing_albina_returns_none_type(self) -> None:
        """EuregioAdapter extras have avalanche_type=None when ALBINA absent."""
        extras = self._adapter().resolve_problem_extras({})
        assert extras["avalanche_type"] is None

    def test_resolve_problem_avalanche_type_reads_albina(self) -> None:
        """EuregioAdapter reads avalanche_type from customData.ALBINA."""
        problem: dict[str, Any] = {"customData": {"ALBINA": {"avalancheType": "loose"}}}
        assert self._adapter().resolve_problem_avalanche_type(problem) == "loose"

    def test_resolve_problem_eaws_fields_from_euregio_sample(self) -> None:
        """EuregioAdapter reads avalanche_size/frequency/snowpack_stability from the sample bulletin."""
        # The euregio_sample_bulletin.json has avalancheSize=3, frequency='some', snowpackStability='poor'
        props = _load_euregio_bulletin()
        problems = props.get("avalancheProblems") or []
        assert problems, "Sanity check: euregio sample must have problems"
        first_problem = problems[0]
        fields = self._adapter().resolve_problem_eaws_fields(first_problem)
        # Verify the three slots are present and non-None (sample has all three).
        assert "avalanche_size" in fields
        assert "frequency" in fields
        assert "snowpack_stability" in fields
        assert fields["avalanche_size"] is not None
        assert fields["frequency"] is not None
        assert fields["snowpack_stability"] is not None

    def test_resolve_problem_eaws_fields_populated_values(self) -> None:
        """EuregioAdapter returns exact EAWS field values from the problem dict."""
        problem: dict[str, Any] = {
            "avalancheSize": 3,
            "frequency": "some",
            "snowpackStability": "poor",
        }
        fields = self._adapter().resolve_problem_eaws_fields(problem)
        assert fields["avalanche_size"] == 3
        assert fields["frequency"] == "some"
        assert fields["snowpack_stability"] == "poor"

    def test_resolve_problem_eaws_fields_missing_returns_none(self) -> None:
        """EuregioAdapter returns None for absent EAWS fields."""
        fields = self._adapter().resolve_problem_eaws_fields({})
        assert fields["avalanche_size"] is None
        assert fields["frequency"] is None
        assert fields["snowpack_stability"] is None

    def test_resolve_avalanche_activity_reads_properties(self) -> None:
        """EuregioAdapter reads avalancheActivity from properties."""
        props: dict[str, Any] = {
            "avalancheActivity": {"highlights": "Weak layers.", "comment": "Caution."}
        }
        result = self._adapter().resolve_avalanche_activity(props)
        assert result["highlights"] == "Weak layers."
        assert result["comment"] == "Caution."

    def test_resolve_danger_patterns_reads_lwd_tyrol(self) -> None:
        """EuregioAdapter reads dangerPatterns from customData.LWD_Tyrol."""
        props: dict[str, Any] = {
            "customData": {"LWD_Tyrol": {"dangerPatterns": ["DP10", "DP1"]}}
        }
        result = self._adapter().resolve_danger_patterns(props)
        assert result == ["DP10", "DP1"]

    def test_resolve_danger_patterns_reads_other_lwd_key(self) -> None:
        """EuregioAdapter reads dangerPatterns from any LWD_* key."""
        props: dict[str, Any] = {
            "customData": {"LWD_Salzburg": {"dangerPatterns": ["DP6"]}}
        }
        result = self._adapter().resolve_danger_patterns(props)
        assert result == ["DP6"]

    def test_resolve_tendency_lead_from_first_tendency_highlights(self) -> None:
        """EuregioAdapter reads tendency_lead from tendency[0].highlights."""
        props: dict[str, Any] = {
            "tendency": [
                {"highlights": "Weak layers require caution.", "comment": "..."}
            ]
        }
        result = self._adapter().resolve_tendency_lead(props)
        assert result == "Weak layers require caution."

    def test_resolve_tendency_lead_returns_none_when_empty(self) -> None:
        """EuregioAdapter returns None when tendency[0].highlights is empty."""
        props: dict[str, Any] = {"tendency": [{"highlights": ""}]}
        assert self._adapter().resolve_tendency_lead(props) is None

    def test_resolve_tendency_lead_returns_none_when_tendency_absent(self) -> None:
        """EuregioAdapter returns None when tendency list is absent."""
        assert self._adapter().resolve_tendency_lead({}) is None

    def test_resolve_danger_rating_subdivision_always_none(self) -> None:
        """EuregioAdapter per-rating subdivision is always None."""
        rating: dict[str, Any] = {
            "customData": {"CH": {"subdivision": "plus"}},  # CH data must not be read
            "mainValue": "moderate",
        }
        assert self._adapter().resolve_danger_rating_subdivision(rating) is None


# ---------------------------------------------------------------------------
# Version 5 — MeteoFranceAdapter unit tests
# ---------------------------------------------------------------------------


class TestMeteoFranceAdapter:
    """Unit tests for the MeteoFranceAdapter class."""

    def _adapter(self) -> Any:
        from bulletins.services.render_model import MeteoFranceAdapter

        return MeteoFranceAdapter()

    def test_resolve_aggregations_dry_problem(self) -> None:
        """MeteoFranceAdapter synthesises dry aggregation from a dry problem."""
        props: dict[str, Any] = {
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                }
            ]
        }
        result = self._adapter().resolve_aggregations(props)
        assert len(result) == 1
        assert result[0]["category"] == "dry"

    def test_resolve_aggregations_empty_returns_empty(self) -> None:
        """MeteoFranceAdapter returns empty aggregation with no problems."""
        assert self._adapter().resolve_aggregations({"avalancheProblems": []}) == []

    def test_resolve_problem_rating_matches_by_elevation_and_period(self) -> None:
        """MeteoFranceAdapter derives rating by elevation + vtp matching."""
        problem: dict[str, Any] = {
            "problemType": "wet_snow",
            "validTimePeriod": "all_day",
            "elevation": {"lowerBound": "2400"},
        }
        ratings = [
            {
                "mainValue": "low",
                "elevation": {"upperBound": "2400"},
                "validTimePeriod": "all_day",
            },
            {
                "mainValue": "moderate",
                "elevation": {"lowerBound": "2400"},
                "validTimePeriod": "all_day",
            },
        ]
        result = self._adapter().resolve_problem_rating(problem, ratings)
        assert result == "moderate"

    def test_resolve_problem_comment_always_empty(self) -> None:
        """MeteoFranceAdapter per-problem comment is always empty."""
        assert self._adapter().resolve_problem_comment({"comment": "Some text."}) == ""

    def test_resolve_problem_extras_always_empty_dict(self) -> None:
        """MeteoFranceAdapter extras dict is always empty."""
        assert self._adapter().resolve_problem_extras({}) == {}

    def test_resolve_problem_avalanche_type_always_none(self) -> None:
        """MeteoFranceAdapter avalanche_type is always None."""
        assert self._adapter().resolve_problem_avalanche_type({}) is None

    def test_resolve_problem_eaws_fields_all_none(self) -> None:
        """MeteoFranceAdapter returns None for all three EAWS problem fields."""
        fields = self._adapter().resolve_problem_eaws_fields({})
        assert fields["avalanche_size"] is None
        assert fields["frequency"] is None
        assert fields["snowpack_stability"] is None

    def test_resolve_avalanche_activity_reads_properties(self) -> None:
        """MeteoFranceAdapter reads avalancheActivity from properties."""
        props: dict[str, Any] = {
            "avalancheActivity": {
                "highlights": "Wet slides possible.",
                "comment": "Caution.",
            }
        }
        result = self._adapter().resolve_avalanche_activity(props)
        assert result["highlights"] == "Wet slides possible."
        assert result["comment"] == "Caution."

    def test_resolve_danger_patterns_always_empty(self) -> None:
        """MeteoFranceAdapter always returns an empty danger_patterns list."""
        result = self._adapter().resolve_danger_patterns(
            {"customData": {"LWD_Tyrol": {"dangerPatterns": ["DP10"]}}}
        )
        assert result == []

    def test_resolve_tendency_lead_always_none(self) -> None:
        """MeteoFranceAdapter always returns None for tendency_lead."""
        props: dict[str, Any] = {"tendency": [{"highlights": "Some lead text."}]}
        assert self._adapter().resolve_tendency_lead(props) is None

    def test_resolve_danger_rating_subdivision_always_none(self) -> None:
        """MeteoFranceAdapter per-rating subdivision is always None."""
        rating: dict[str, Any] = {"customData": {"CH": {"subdivision": "plus"}}}
        assert self._adapter().resolve_danger_rating_subdivision(rating) is None


# ---------------------------------------------------------------------------
# Version 5 — danger.ratings projection
# ---------------------------------------------------------------------------


class TestDangerRatingsProjection:
    """Tests for the danger.ratings list added in v5."""

    def test_single_rating_produces_one_entry(self) -> None:
        """A single CAAML dangerRating produces one entry in danger.ratings."""
        result = _resolve_danger([{"mainValue": "moderate"}])
        assert len(result["ratings"]) == 1

    def test_entry_shape_has_required_keys(self) -> None:
        """Each ratings entry has period, key, subdivision, and elevation keys."""
        result = _resolve_danger([{"mainValue": "moderate"}])
        entry = result["ratings"][0]
        assert set(entry.keys()) == {"period", "key", "subdivision", "elevation"}

    def test_period_defaults_to_all_day(self) -> None:
        """A rating without validTimePeriod gets period='all_day'."""
        result = _resolve_danger([{"mainValue": "moderate"}])
        assert result["ratings"][0]["period"] == "all_day"

    def test_period_is_preserved_from_rating(self) -> None:
        """validTimePeriod from a dangerRating is preserved in the entry."""
        result = _resolve_danger([{"mainValue": "low", "validTimePeriod": "earlier"}])
        assert result["ratings"][0]["period"] == "earlier"

    def test_key_matches_main_value(self) -> None:
        """The entry key matches the mainValue."""
        result = _resolve_danger([{"mainValue": "considerable"}])
        assert result["ratings"][0]["key"] == "considerable"

    def test_elevation_parsed_when_present(self) -> None:
        """Elevation is parsed to a structured dict when present."""
        result = _resolve_danger(
            [{"mainValue": "moderate", "elevation": {"lowerBound": "2000"}}]
        )
        elev = result["ratings"][0]["elevation"]
        assert elev is not None
        assert elev["lower"] == 2000

    def test_elevation_none_when_absent(self) -> None:
        """Elevation is None when not present in the rating."""
        result = _resolve_danger([{"mainValue": "moderate"}])
        assert result["ratings"][0]["elevation"] is None

    def test_unknown_main_value_excluded_from_ratings(self) -> None:
        """Ratings with unknown mainValue are excluded from the ratings list."""
        result = _resolve_danger(
            [{"mainValue": "not_a_level"}, {"mainValue": "moderate"}]
        )
        assert len(result["ratings"]) == 1
        assert result["ratings"][0]["key"] == "moderate"

    def test_slf_subdivision_read_from_ch_custom_data(self) -> None:
        """SLF per-rating subdivision is read from customData.CH."""
        result = _resolve_danger(
            [
                {
                    "mainValue": "considerable",
                    "customData": {"CH": {"subdivision": "plus"}},
                }
            ],
            source=Bulletin.Source.SLF,
        )
        assert result["ratings"][0]["subdivision"] == "+"

    def test_euregio_subdivision_always_none(self) -> None:
        """EUREGIO per-rating subdivision is always None."""
        result = _resolve_danger(
            [{"mainValue": "moderate", "customData": {"CH": {"subdivision": "plus"}}}],
            source=Bulletin.Source.EUREGIO,
        )
        assert result["ratings"][0]["subdivision"] is None

    def test_mf_subdivision_always_none(self) -> None:
        """MF per-rating subdivision is always None."""
        result = _resolve_danger(
            [{"mainValue": "moderate"}],
            source=Bulletin.Source.MF,
        )
        assert result["ratings"][0]["subdivision"] is None

    def test_multiple_ratings_all_appear_in_list(self) -> None:
        """All valid ratings appear in the ratings list."""
        result = _resolve_danger(
            [
                {"mainValue": "low", "validTimePeriod": "earlier"},
                {"mainValue": "moderate", "validTimePeriod": "later"},
            ]
        )
        assert len(result["ratings"]) == 2
        periods = {r["period"] for r in result["ratings"]}
        assert periods == {"earlier", "later"}

    def test_ratings_list_present_in_full_render_model(self) -> None:
        """build_render_model includes danger.ratings in the output."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert "ratings" in rm["danger"]
        assert isinstance(rm["danger"]["ratings"], list)


# ---------------------------------------------------------------------------
# Version 5 — prose.tendency_lead projection
# ---------------------------------------------------------------------------


class TestTendencyLeadProjection:
    """Tests for the prose.tendency_lead field added in v5."""

    def test_slf_tendency_lead_is_none(self) -> None:
        """SLF bulletins always produce tendency_lead=None in prose."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert rm["prose"]["tendency_lead"] is None

    def test_euregio_tendency_lead_populated_from_highlights(self) -> None:
        """EUREGIO bulletin extracts tendency[0].highlights as tendency_lead."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        # The euregio fixture has highlights='Weak layers in the old snowpack necessitate caution.'
        assert rm["prose"]["tendency_lead"] is not None
        assert "caution" in rm["prose"]["tendency_lead"].lower()

    def test_mf_tendency_lead_is_none(self) -> None:
        """MF bulletins always produce tendency_lead=None in prose."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        assert rm["prose"]["tendency_lead"] is None

    def test_tendency_lead_key_present_for_all_sample_fixtures(self) -> None:
        """Every SLF sample produces a tendency_lead key in prose (may be None)."""
        for path in _FIXTURE_DIR.iterdir():
            if not (path.name.startswith("sample_") and path.name.endswith(".json")):
                continue
            data = json.loads(path.read_text())
            props = data["properties"]
            try:
                rm = build_render_model(props)
            except RenderModelBuildError:
                continue
            assert "tendency_lead" in rm["prose"], f"{path.name} missing tendency_lead"


# ---------------------------------------------------------------------------
# Version 5 — per-problem EAWS fields
# ---------------------------------------------------------------------------


class TestPerProblemEawsFields:
    """Tests for avalanche_size / frequency / snowpack_stability added in v5."""

    def test_euregio_problems_carry_eaws_fields(self) -> None:
        """EUREGIO build_render_model populates EAWS fields on problems from the sample fixture."""
        props = _load_euregio_bulletin()
        rm = build_render_model(props)
        all_problems = [p for t in rm["traits"] for p in t["problems"]]
        assert all_problems, "Sanity: EUREGIO sample must produce problems"
        # The sample fixture has exactly one problem with avalancheSize=3.
        problem = all_problems[0]
        assert problem["avalanche_size"] == 3
        assert problem["frequency"] == "some"
        assert problem["snowpack_stability"] == "poor"

    def test_slf_problems_have_none_eaws_fields(self) -> None:
        """SLF problems always have None for all three EAWS fields."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        for trait in rm["traits"]:
            for problem in trait["problems"]:
                assert problem["avalanche_size"] is None, (
                    f"expected None, got {problem['avalanche_size']!r}"
                )
                assert problem["frequency"] is None
                assert problem["snowpack_stability"] is None

    def test_mf_problems_have_none_eaws_fields(self) -> None:
        """MF problems always have None for all three EAWS fields."""
        props = _load_mf_bulletin("massif-001.xml")
        rm = build_render_model(props)
        for trait in rm["traits"]:
            for problem in trait["problems"]:
                assert problem["avalanche_size"] is None
                assert problem["frequency"] is None
                assert problem["snowpack_stability"] is None

    def test_eaws_fields_present_in_problem_dict(self) -> None:
        """All three EAWS field keys are present in every problem dict regardless of source."""
        for filename, source_props in [
            ("sample_variable_day.json", _load_sample("sample_variable_day.json")),
            (None, _load_euregio_bulletin()),
        ]:
            try:
                rm = build_render_model(source_props)
            except RenderModelBuildError:
                continue
            for trait in rm["traits"]:
                for problem in trait["problems"]:
                    assert "avalanche_size" in problem, (
                        f"missing avalanche_size in {filename or 'euregio'}"
                    )
                    assert "frequency" in problem, (
                        f"missing frequency in {filename or 'euregio'}"
                    )
                    assert "snowpack_stability" in problem, (
                        f"missing snowpack_stability in {filename or 'euregio'}"
                    )
