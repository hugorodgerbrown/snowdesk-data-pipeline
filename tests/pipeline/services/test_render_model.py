"""
tests/pipeline/services/test_render_model.py — Tests for the render_model service.

Covers:
  - build_render_model: output shape, trait building, aggregation ordering,
    elevation parsing, danger resolution, geography source detection.
  - compute_day_character: all five cascade rules with render model inputs.
  - Validation: fail-hard on unknown problem types, aggregation mismatch,
    empty problemTypes.
  - Aggregation synthesis: missing customData.CH.aggregation is rebuilt
    from problem types (a real-world SLF quirk; aggregation is a display
    hint, not source-of-truth).
  - Happy paths: both empty, empty title fallback, 3+ aggregation warning.
  - Version 3: metadata and prose extraction, _parse_iso_timestamp helper,
    tendency list, back-compat top-level snowpack_structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pipeline.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    _build_metadata,
    _build_prose,
    _parse_elevation,
    _parse_iso_timestamp,
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
        assert compute_day_character(rm) == "Hard-to-read day"

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
        assert compute_day_character(rm) == "Widespread danger"

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

    SLF occasionally publishes bulletins with avalancheProblems but no
    aggregation hint. The builder synthesises aggregation from the
    problem types (dry/wet are disjoint, so grouping is unambiguous)
    and logs a warning. See
    [project_aggregation_purpose.md] — the schema treats aggregation
    as a visualisation hint, not source-of-truth.
    """

    def test_no_aggregation_fixture_has_problems(self) -> None:
        """Sanity check: the fixture exercises the synthesis branch."""
        props = _load_sample("sample_no_aggregation_day.json")
        assert len(props.get("avalancheProblems", [])) > 0
        assert not props.get("customData", {}).get("CH", {}).get("aggregation")

    def test_synthesises_aggregation_from_problems(self) -> None:
        """Missing aggregation is rebuilt; render model returns the current version."""
        props = _load_sample("sample_no_aggregation_day.json")

        rm = build_render_model(props)

        assert rm["version"] == RENDER_MODEL_VERSION
        # The fixture has one dry, all_day problem (new_snow) — exactly one trait.
        assert len(rm["traits"]) == 1
        trait = rm["traits"][0]
        assert trait["category"] == "dry"
        assert trait["time_period"] == "all_day"
        assert [p["problem_type"] for p in trait["problems"]] == ["new_snow"]

    def test_synthesis_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The synthesis path logs a warning so operators can track upstream gaps."""
        import logging

        # config/settings/base.py sets propagate=False on the pipeline logger
        # so that pytest's root caplog doesn't see records by default. Flip it
        # for the duration of this test so caplog can verify the warning.
        monkeypatch.setattr(logging.getLogger("pipeline"), "propagate", True)

        props = _load_sample("sample_no_aggregation_day.json")

        with caplog.at_level(logging.WARNING, logger="pipeline.services.render_model"):
            build_render_model(props)

        assert any(
            "synthesising aggregation" in rec.getMessage() for rec in caplog.records
        )

    def test_synthesis_groups_by_category_and_time_period(self) -> None:
        """Multiple problems group into the right number of traits."""
        props: dict[str, Any] = {
            "bulletinID": "synth-multi-001",
            "dangerRatings": [{"mainValue": "moderate"}],
            "avalancheProblems": [
                {
                    "problemType": "new_snow",
                    "validTimePeriod": "all_day",
                    "aspects": ["N"],
                    "elevation": {"lowerBound": "2000"},
                },
                {
                    "problemType": "wind_slab",
                    "validTimePeriod": "all_day",
                    "aspects": ["NE"],
                    "elevation": {"lowerBound": "2000"},
                },
                {
                    "problemType": "wet_snow",
                    "validTimePeriod": "later",
                    "aspects": ["S"],
                    "elevation": {"upperBound": "2400"},
                },
            ],
        }

        rm = build_render_model(props)

        # Three problems → two traits: (dry, all_day) with new_snow + wind_slab,
        # and (wet, later) with wet_snow.
        assert rm["version"] == RENDER_MODEL_VERSION
        assert len(rm["traits"]) == 2

        dry_trait, wet_trait = rm["traits"]
        assert dry_trait["category"] == "dry"
        assert dry_trait["time_period"] == "all_day"
        assert [p["problem_type"] for p in dry_trait["problems"]] == [
            "new_snow",
            "wind_slab",
        ]

        assert wet_trait["category"] == "wet"
        assert wet_trait["time_period"] == "later"
        assert [p["problem_type"] for p in wet_trait["problems"]] == ["wet_snow"]

    def test_unknown_problem_type_still_raises_via_synthesis_path(self) -> None:
        """Unknown problem types still fail validation before synthesis runs."""
        props: dict[str, Any] = {
            "bulletinID": "synth-unknown-001",
            "dangerRatings": [{"mainValue": "moderate"}],
            "avalancheProblems": [
                {"problemType": "not_a_real_type", "validTimePeriod": "all_day"}
            ],
            # No aggregation — would normally hit the synthesis path.
        }

        with pytest.raises(RenderModelBuildError, match="Unknown problemType"):
            build_render_model(props)


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
        }
        rm = build_render_model(props)
        assert compute_day_character(rm) == "Stable day"


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
        with patch("pipeline.services.render_model.logger") as mock_logger:
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
        assert compute_day_character(rm) == "Stable day"

    def test_empty_traits_defaults_to_stable(self) -> None:
        """Render model with empty traits defaults to Stable day."""
        rm = {
            "version": RENDER_MODEL_VERSION,
            "danger": {"key": "low", "number": "1", "subdivision": None},
            "traits": [],
        }
        assert compute_day_character(rm) == "Stable day"

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
        assert compute_day_character(rm) == "Widespread danger"

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
        assert compute_day_character(rm) == "Stable day"


# ---------------------------------------------------------------------------
# Version 3 — RENDER_MODEL_VERSION constant and build_render_model version
# ---------------------------------------------------------------------------


class TestRenderModelVersion:
    """Tests that RENDER_MODEL_VERSION and built version are both 3."""

    def test_constant_is_3(self) -> None:
        """RENDER_MODEL_VERSION constant equals 3."""
        assert RENDER_MODEL_VERSION == 3

    def test_build_render_model_returns_version_3(self) -> None:
        """build_render_model returns a dict with version == 3."""
        props = _load_sample("sample_variable_day.json")
        rm = build_render_model(props)
        assert rm["version"] == 3


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
        """Prose dict has all four keys."""
        props = _load_sample("sample_variable_day.json")
        prose = _build_prose(props)
        assert set(prose.keys()) == {
            "snowpack_structure",
            "weather_review",
            "weather_forecast",
            "tendency",
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
            for p in (_SAMPLE_DIR).iterdir()
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
        path = _SAMPLE_DIR / filename
        data = json.loads(path.read_text())
        props = data["properties"]
        # Skip fixtures we know raise RenderModelBuildError.
        try:
            rm = build_render_model(props)
        except RenderModelBuildError:
            pytest.skip(f"{filename} is expected to raise RenderModelBuildError")
        label = compute_day_character(rm)
        assert label in valid_labels


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
        }
        rm = build_render_model(props)
        assert rm["prose"]["snowpack_structure"] == "<p>All quiet.</p>"
        assert rm["prose"]["weather_review"] == "<p>Clear skies.</p>"
        assert rm["prose"]["weather_forecast"] == "<p>Continuing fine.</p>"
        assert len(rm["prose"]["tendency"]) == 1
