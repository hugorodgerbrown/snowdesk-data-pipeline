"""
tests/bulletins/services/test_day_summary.py — Tests for the day-summary matrix.

Covers the three pure classifiers (``classify_readability``,
``classify_movement``, ``join_problems``), the totality of the 80-cell
matrix, and ``summary_for`` end to end — including the properties that
make the copy safe to ship: every cell resolves, every placeholder is
filled, and no sentence claims a hazard the bulletin did not name.
"""

from __future__ import annotations

import re

import pytest

from apps.bulletins.services.day_summary import (
    _MATRIX,
    _TRANSITIONS,
    DAY_SUMMARY_VERSION,
    HIDDEN_PROBLEMS,
    NEUTRAL_PROBLEMS,
    PROBLEM_PHRASES,
    READABLE_PROBLEMS,
    classify_movement,
    classify_readability,
    join_problems,
    summary_for,
)
from apps.bulletins.services.render_model import KNOWN_PROBLEM_TYPES

MOVEMENTS: tuple[str, ...] = ("static", "rising", "easing", "shifting")
LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)
READABILITIES: tuple[str, ...] = ("quiet", "readable", "hidden", "mixed")


# ---------------------------------------------------------------------------
# Problem classification
# ---------------------------------------------------------------------------


class TestClassifyReadability:
    """Unit tests for classify_readability."""

    def test_empty_set_is_quiet(self) -> None:
        """No problems at all is a quiet day."""
        assert classify_readability(set()) == "quiet"

    def test_only_neutral_placeholders_is_quiet(self) -> None:
        """Placeholder problem types name no hazard, so they read as quiet."""
        assert classify_readability(set(NEUTRAL_PROBLEMS)) == "quiet"

    @pytest.mark.parametrize("problem_type", sorted(READABLE_PROBLEMS))
    def test_surface_problems_are_readable(self, problem_type: str) -> None:
        """Every surface-evidence problem classifies as readable on its own."""
        assert classify_readability({problem_type}) == "readable"

    @pytest.mark.parametrize("problem_type", sorted(HIDDEN_PROBLEMS))
    def test_buried_problems_are_hidden(self, problem_type: str) -> None:
        """Every buried problem classifies as hidden on its own."""
        assert classify_readability({problem_type}) == "hidden"

    def test_one_of_each_is_mixed(self) -> None:
        """A readable problem beside a buried one is mixed."""
        assert classify_readability({"wind_slab", "persistent_weak_layers"}) == "mixed"

    def test_neutral_does_not_upgrade_a_quiet_class(self) -> None:
        """A placeholder alongside a real problem does not change the class."""
        assert (
            classify_readability({"wind_slab", "no_distinct_avalanche_problem"})
            == "readable"
        )

    def test_none_entries_are_ignored(self) -> None:
        """A missing problem_type never crashes the classifier."""
        assert classify_readability({None, "wind_slab"}) == "readable"  # type: ignore[arg-type]

    def test_every_known_problem_type_is_classified(self) -> None:
        """No EAWS problem type falls outside the three buckets.

        A provider adding a type would otherwise be silently treated as
        quiet, and the explainer would stop naming it.
        """
        classified = READABLE_PROBLEMS | HIDDEN_PROBLEMS | NEUTRAL_PROBLEMS
        assert KNOWN_PROBLEM_TYPES <= classified

    def test_buckets_are_disjoint(self) -> None:
        """A problem type never lands in two buckets."""
        assert not READABLE_PROBLEMS & HIDDEN_PROBLEMS
        assert not READABLE_PROBLEMS & NEUTRAL_PROBLEMS
        assert not HIDDEN_PROBLEMS & NEUTRAL_PROBLEMS


class TestClassifyMovement:
    """Unit tests for classify_movement."""

    def test_no_split_is_static(self) -> None:
        """An unsplit day is static whatever its problems."""
        assert classify_movement("", {"wind_slab"}, set()) == "static"

    def test_rise_is_rising(self) -> None:
        """A rising danger level is a deteriorating day."""
        assert classify_movement("rise", {"wind_slab"}, {"wet_snow"}) == "rising"

    def test_fall_is_easing(self) -> None:
        """A falling danger level is an easing day."""
        assert classify_movement("fall", {"persistent_weak_layers"}, {"wet_snow"}) == (
            "easing"
        )

    def test_flat_split_with_a_new_problem_is_shifting(self) -> None:
        """The level holds but a different problem takes the afternoon."""
        assert classify_movement("none", {"wind_slab"}, {"wet_snow"}) == "shifting"

    def test_flat_split_with_identical_windows_is_static(self) -> None:
        """Six archive bulletins split with two identical windows.

        Nothing changes for the reader, so the copy must not promise that
        something does.
        """
        assert classify_movement("none", {"wind_slab"}, {"wind_slab"}) == "static"

    def test_flat_split_with_no_later_window_is_static(self) -> None:
        """A rating-only split with no later trait is static."""
        assert classify_movement("none", {"wind_slab"}, set()) == "static"


class TestJoinProblems:
    """Unit tests for join_problems."""

    def test_empty_list_returns_empty_string(self) -> None:
        """Nothing to name yields an empty phrase."""
        assert join_problems([]) == ""

    def test_single_problem_is_bare(self) -> None:
        """One problem is returned without a conjunction."""
        assert join_problems(["wind_slab"]) == "wind slab"

    def test_two_problems_join_with_and(self) -> None:
        """Two problems are joined with 'and', no comma."""
        assert join_problems(["wind_slab", "wet_snow"]) == "wind slab and wet snow"

    def test_three_problems_use_commas_and_a_final_and(self) -> None:
        """Three or more use commas with a final 'and'."""
        assert join_problems(["wind_slab", "wet_snow", "new_snow"]) == (
            "wind slab, wet snow and new snow"
        )

    def test_caller_order_is_preserved(self) -> None:
        """Editorial aggregation order survives the join."""
        assert join_problems(["wet_snow", "wind_slab"]) == "wet snow and wind slab"

    def test_duplicates_are_dropped(self) -> None:
        """A type named by two traits is listed once."""
        assert join_problems(["wind_slab", "wind_slab"]) == "wind slab"

    def test_neutral_placeholders_are_never_named(self) -> None:
        """Placeholder types carry no phrase and are skipped."""
        assert join_problems(["no_distinct_avalanche_problem", "wind_slab"]) == (
            "wind slab"
        )

    def test_unknown_type_is_skipped(self) -> None:
        """An unrecognised type is dropped rather than crashing the page."""
        assert join_problems(["not_a_real_problem", "wind_slab"]) == "wind slab"


# ---------------------------------------------------------------------------
# Matrix totality
# ---------------------------------------------------------------------------


class TestMatrixTotality:
    """The grid is complete, so no bulletin falls back to generic copy."""

    def test_version_is_an_int(self) -> None:
        """DAY_SUMMARY_VERSION is an integer callers can compare."""
        assert isinstance(DAY_SUMMARY_VERSION, int)

    def test_every_cell_is_populated(self) -> None:
        """All 4 × 5 × 4 combinations carry hand-authored copy."""
        missing = [
            (movement, level, readability)
            for movement in MOVEMENTS
            for level in LEVELS
            for readability in READABILITIES
            if (movement, level, readability) not in _MATRIX
        ]
        assert missing == []

    def test_matrix_holds_no_extra_cells(self) -> None:
        """The matrix has exactly the 80 cells the axes describe."""
        assert len(_MATRIX) == len(MOVEMENTS) * len(LEVELS) * len(READABILITIES)

    def test_every_sentence_is_distinct(self) -> None:
        """No two cells share copy — a duplicate means a cell was missed."""
        rendered = [str(value) for value in _MATRIX.values()]
        assert len(set(rendered)) == len(rendered)

    def test_quiet_cells_never_reference_problems(self) -> None:
        """A quiet day has nothing to name, so %(problems)s must not appear."""
        for (movement, level, readability), template in _MATRIX.items():
            if readability == "quiet":
                assert "%(problems)s" not in str(template), (movement, level)

    def test_non_quiet_cells_always_reference_problems(self) -> None:
        """Every cell with problems to name interpolates them."""
        for (movement, level, readability), template in _MATRIX.items():
            if readability != "quiet":
                assert "%(problems)s" in str(template), (movement, level, readability)

    def test_only_changing_cells_take_a_transition_clause(self) -> None:
        """A static or shifting day has one level, so it opens on its own."""
        for (movement, level, readability), template in _MATRIX.items():
            has_transition = "%(transition)s" in str(template)
            assert has_transition == (movement in {"rising", "easing"}), (
                movement,
                level,
                readability,
            )

    def test_every_placeholder_is_one_the_renderer_supplies(self) -> None:
        """No cell asks for an interpolation summary_for does not provide."""
        supplied = {"problems", "transition"}
        for key, template in _MATRIX.items():
            asked = set(re.findall(r"%\((\w+)\)s", str(template)))
            assert asked <= supplied, (key, asked - supplied)

    def test_every_changing_movement_has_both_transition_clauses(self) -> None:
        """Each of rising and easing needs a same-band and a cross-band opening."""
        expected = {(m, same) for m in ("rising", "easing") for same in (True, False)}
        assert set(_TRANSITIONS) == expected

    def test_transition_clauses_ask_only_for_level_words(self) -> None:
        """The opening clause interpolates level words and nothing else."""
        for key, clause in _TRANSITIONS.items():
            asked = set(re.findall(r"%\((\w+)\)s", str(clause)))
            assert asked <= {"from_word", "to_word"}, (key, asked)

    def test_same_band_clauses_never_name_the_source_level(self) -> None:
        """Naming both ends of a subdivision-only move repeats the same word."""
        for (movement, same_band), clause in _TRANSITIONS.items():
            if same_band:
                assert "%(from_word)s" not in str(clause), movement


# ---------------------------------------------------------------------------
# summary_for
# ---------------------------------------------------------------------------


class TestSummaryFor:
    """End-to-end behaviour of the public entry point."""

    def test_every_combination_renders_without_a_placeholder_left(self) -> None:
        """No reachable input leaves a raw %(...)s in the rendered sentence."""
        for movement in MOVEMENTS:
            for level in LEVELS:
                for problem_types in (
                    [],
                    ["wind_slab"],
                    ["persistent_weak_layers"],
                    ["wind_slab", "persistent_weak_layers"],
                ):
                    text = summary_for(movement, level, problem_types, from_level=2)
                    assert "%(" not in text, (movement, level, problem_types)
                    assert text.endswith(".")

    def test_static_names_the_problem(self) -> None:
        """A static day's sentence carries the problem's own name."""
        text = summary_for("static", 3, ["persistent_weak_layers"])
        assert "persistent weak layers" in text
        assert text.startswith("Considerable")

    def test_readable_and_hidden_copy_differ_at_the_same_level(self) -> None:
        """The visible/invisible split is what the reader is meant to notice."""
        readable = summary_for("static", 3, ["wind_slab"])
        hidden = summary_for("static", 3, ["persistent_weak_layers"])
        assert readable != hidden
        assert "surface" in readable
        assert "buried" in hidden

    def test_rising_names_both_levels(self) -> None:
        """A deteriorating day states where it starts and where it ends."""
        text = summary_for("rising", 3, ["wet_snow"], from_level=2)
        assert "moderate this morning" in text
        assert "considerable by afternoon" in text

    def test_same_band_move_never_names_the_level_twice(self) -> None:
        """Direction ranks on (level, subdivision), so a day can move on the
        subdivision alone — 45 of the archive's 211 changing days do.

        Naming both ends would render "moderate this morning, moderate by
        afternoon", which reads as a rendering bug rather than a subdivision.
        """
        text = summary_for("rising", 2, ["wet_snow"], from_level=2)
        assert text.startswith("Deteriorating within moderate")
        assert "this morning" not in text

    def test_same_band_easing_says_the_move_happened_inside_the_level(self) -> None:
        """The falling equivalent of the subdivision-only case."""
        text = summary_for("easing", 2, ["wet_snow"], from_level=2)
        assert text.startswith("Easing within moderate")
        assert text.count("moderate") == 1

    def test_easing_never_claims_the_day_improves(self) -> None:
        """All 22 falling days in the archive swap a dry problem for wet snow.

        The number drops while the hazard changes, so the copy must not
        read as an all-clear.
        """
        text = summary_for(
            "easing", 2, ["persistent_weak_layers", "wet_snow"], from_level=3
        )
        assert "improv" not in text.lower()
        assert "swaps rather than clears" in text

    def test_quiet_day_names_no_problem(self) -> None:
        """With nothing named, the sentence says so rather than listing nothing."""
        text = summary_for("static", 2, [])
        assert "no distinct problem named" in text

    def test_neutral_only_day_is_treated_as_quiet(self) -> None:
        """A placeholder problem type produces quiet copy, not an empty list."""
        text = summary_for("static", 2, ["no_distinct_avalanche_problem"])
        assert text == summary_for("static", 2, [])

    @pytest.mark.parametrize("level", [0, -1, 6, 99])
    def test_out_of_range_level_is_clamped(self, level: int) -> None:
        """A malformed level renders real copy rather than raising KeyError."""
        text = summary_for("static", level, ["wind_slab"])
        assert "%(" not in text
        assert text

    def test_missing_from_level_falls_back_to_the_destination(self) -> None:
        """Omitting the source level never leaves %(from_word)s unfilled."""
        text = summary_for("rising", 3, ["wet_snow"])
        assert "%(" not in text

    def test_problem_phrases_cover_every_named_type(self) -> None:
        """Each classified hazard has mid-sentence phrasing.

        A type in READABLE/HIDDEN without a phrase would classify the day
        and then vanish from the sentence describing it.
        """
        named = READABLE_PROBLEMS | HIDDEN_PROBLEMS
        assert named <= set(PROBLEM_PHRASES)
