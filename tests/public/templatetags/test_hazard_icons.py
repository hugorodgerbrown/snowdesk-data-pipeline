"""
tests/public/templatetags/test_hazard_icons.py — Tests for the hazard icon
template filters.
"""

from __future__ import annotations

import pytest

from public.templatetags.hazard_icons import category_danger_icon, hazard_icon


class TestHazardIcon:
    """Exercise ``hazard_icon`` filter mapping problem types to icon paths."""

    def test_known_problem_type_returns_path(self) -> None:
        assert hazard_icon("wet_snow") == "icons/eaws/avalanche_problems/Wet-Snow.svg"

    def test_gliding_snow(self) -> None:
        assert (
            hazard_icon("gliding_snow")
            == "icons/eaws/avalanche_problems/Gliding-Snow.svg"
        )

    def test_unknown_problem_type_returns_empty(self) -> None:
        assert hazard_icon("mystery_type") == ""

    def test_empty_input(self) -> None:
        assert hazard_icon("") == ""


class TestCategoryDangerIcon:
    """Exercise ``category_danger_icon`` filter for per-category level pictograms."""

    @pytest.mark.parametrize(
        "level, expected",
        [
            (1, "icons/eaws/danger_levels/Dry-Snow-1.svg"),
            (2, "icons/eaws/danger_levels/Dry-Snow-2.svg"),
            (3, "icons/eaws/danger_levels/Dry-Snow-3.svg"),
            (4, "icons/eaws/danger_levels/Dry-Snow-4-5.svg"),
            (5, "icons/eaws/danger_levels/Dry-Snow-4-5.svg"),
        ],
    )
    def test_dry_levels(self, level: int, expected: str) -> None:
        trait = {"category": "dry", "danger_level": level}
        assert category_danger_icon(trait) == expected

    @pytest.mark.parametrize(
        "level, expected",
        [
            (1, "icons/eaws/danger_levels/Wet-Snow-1.svg"),
            (2, "icons/eaws/danger_levels/Wet-Snow-2.svg"),
            (3, "icons/eaws/danger_levels/Wet-Snow-3.svg"),
            (4, "icons/eaws/danger_levels/Wet-Snow-4.svg"),
            (5, "icons/eaws/danger_levels/Wet-Snow-5.svg"),
        ],
    )
    def test_wet_levels(self, level: int, expected: str) -> None:
        trait = {"category": "wet", "danger_level": level}
        assert category_danger_icon(trait) == expected

    def test_unknown_category_returns_empty(self) -> None:
        assert category_danger_icon({"category": "mixed", "danger_level": 2}) == ""

    def test_out_of_range_level_returns_empty(self) -> None:
        assert category_danger_icon({"category": "dry", "danger_level": 0}) == ""
        assert category_danger_icon({"category": "wet", "danger_level": 6}) == ""

    def test_non_int_level_returns_empty(self) -> None:
        assert category_danger_icon({"category": "dry", "danger_level": "2"}) == ""

    def test_none_trait_returns_empty(self) -> None:
        assert category_danger_icon(None) == ""

    def test_missing_keys_returns_empty(self) -> None:
        assert category_danger_icon({}) == ""
        assert category_danger_icon({"category": "dry"}) == ""
        assert category_danger_icon({"danger_level": 2}) == ""
