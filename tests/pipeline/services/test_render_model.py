"""
tests/pipeline/services/test_render_model.py — Tests for the render_model service.

Covers:
  - build_render_model: output shape, trait building, aggregation matching,
    elevation parsing, danger resolution, geography source detection.
  - compute_day_character: all five cascade rules with render model inputs.
  - Edge cases: missing aggregation (synthetic fallback), prose-only geography,
    dry/wet disambiguation, subdivision variants.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.services.render_model import (
    RENDER_MODEL_VERSION,
    _parse_elevation,
    _resolve_danger,
    build_render_model,
    compute_day_character,
)

# Path to sample data fixtures.
_SAMPLE_DIR = Path(__file__).parents[3] / "sample_data"


def _load_sample(filename: str) -> dict[str, Any]:
    """Load a sample JSON fixture and return its ``properties`` dict."""
    path = _SAMPLE_DIR / filename
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
        assert "fallback_key_message" in rm
        assert "snowpack_structure" in rm

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
        assert compute_day_character(rm) == "Stable day"

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
        """First trait is dry/all_day."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        trait = rm["traits"][0]
        assert trait["category"] == "dry"
        assert trait["time_period"] == "all_day"

    def test_second_trait_is_wet_later(self) -> None:
        """Second trait is wet/later."""
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
        # gliding_snow is in _HARD_TO_READ_PROBLEMS, danger=considerable=3
        assert compute_day_character(rm) == "Hard-to-read day"

    def test_dry_wet_disambiguation(self) -> None:
        """Dry and wet traits each get only their own period's problems."""
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
        assert compute_day_character(rm) == "Widespread danger"


# ---------------------------------------------------------------------------
# build_render_model — missing aggregation (synthetic fallback)
# ---------------------------------------------------------------------------


class TestBuildRenderModelNoAggregation:
    """Tests for bulletins missing customData.CH.aggregation."""

    def test_single_synthetic_trait(self) -> None:
        """Missing aggregation → single synthetic dry/all_day trait."""
        props = _load_sample("sample_no_aggregation_day.json")
        rm = build_render_model(props)

        assert len(rm["traits"]) == 1
        trait = rm["traits"][0]
        assert trait["category"] == "dry"
        assert trait["time_period"] == "all_day"

    def test_warning_logged_when_no_aggregation(self) -> None:
        """Missing aggregation calls logger.warning with 'aggregation' in message."""
        from unittest.mock import patch

        props = _load_sample("sample_no_aggregation_day.json")

        with patch("pipeline.services.render_model.logger") as mock_logger:
            build_render_model(props)

        # Ensure warning was called at least once with 'aggregation' mentioned.
        warning_calls = mock_logger.warning.call_args_list
        assert any("aggregation" in str(call) for call in warning_calls)

    def test_all_problems_under_synthetic_trait(self) -> None:
        """All avalanche problems appear under the synthetic trait."""
        props = _load_sample("sample_no_aggregation_day.json")
        rm = build_render_model(props)
        types = {p["problem_type"] for p in rm["traits"][0]["problems"]}
        assert "new_snow" in types


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
        """Trait prose is set to the first problem's comment."""
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


# ---------------------------------------------------------------------------
# compute_day_character — integration with build_render_model
# ---------------------------------------------------------------------------


class TestComputeDayCharacterRoundTrip:
    """Integration tests building render models then computing day character."""

    def test_stable_day_end_to_end(self) -> None:
        """Stable day fixture produces 'Stable day' end-to-end."""
        props = _load_sample("sample_stable_day.json")
        rm = build_render_model(props)
        assert compute_day_character(rm) == "Stable day"

    def test_empty_traits_defaults_to_stable(self) -> None:
        """Render model with empty traits defaults to Stable day."""
        rm = {
            "version": 1,
            "danger": {"key": "low", "number": "1", "subdivision": None},
            "traits": [],
        }
        assert compute_day_character(rm) == "Stable day"

    def test_multiple_traits_flattened_for_rules(self) -> None:
        """Problems across multiple traits are flattened for rule evaluation."""
        rm = {
            "version": 1,
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
        assert compute_day_character(rm) == "Widespread danger"

    def test_danger_zero_hits_safe_default(self) -> None:
        """Danger number 0 (malformed input) hits the safe default return path."""
        # danger=0 falls through all rules: <4, no problems for rule 2,
        # not ==3 for rules 3/3b, _is_stable returns False (danger != 1 and
        # the danger==2 check fails), danger not in {2, 3} → safe default.
        rm = {
            "version": 1,
            "danger": {"key": "low", "number": "0", "subdivision": None},
            "traits": [],
        }
        assert compute_day_character(rm) == "Stable day"
