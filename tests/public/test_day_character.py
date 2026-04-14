"""
tests/public/test_day_character.py — Tests for the compute_day_character function.

Covers all five rules in the day-character cascade, including edge cases
for subdivision, elevation bounds, aspect counts, and the safe default.

Re-pointed to pipeline.services.render_model.compute_day_character which
supersedes the old public.views.day_character implementation.
"""

from __future__ import annotations

import pytest

from pipeline.services.render_model import compute_day_character


def _render_model(
    danger_number: str = "1",
    danger_subdivision: str | None = None,
    problems: list | None = None,
) -> dict:
    """Build a minimal render model dict for compute_day_character testing."""
    return {
        "danger": {
            "key": "low",
            "number": danger_number,
            "subdivision": danger_subdivision,
        },
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "problems": problems or [],
            }
        ],
    }


def _problem(
    problem_type: str = "new_snow",
    aspects: list | None = None,
    lower: int | None = None,
    upper: int | None = None,
) -> dict:
    """Build a minimal render model problem dict."""
    elevation: dict | None = None
    if lower is not None or upper is not None:
        elevation = {"lower": lower, "upper": upper, "treeline": False}
    return {
        "problem_type": problem_type,
        "aspects": aspects or [],
        "elevation": elevation,
        "time_period": "all_day",
        "comment_html": "",
        "core_zone_text": None,
        "danger_rating_value": None,
    }


class TestRule1DangerousConditions:
    """Tests for rule 1 — Dangerous conditions (danger >= 4)."""

    def test_danger_4_returns_dangerous(self) -> None:
        """Danger level 4 → Dangerous conditions."""
        assert compute_day_character(_render_model("4")) == "Dangerous conditions"

    def test_danger_5_returns_dangerous(self) -> None:
        """Danger level 5 → Dangerous conditions."""
        assert compute_day_character(_render_model("5")) == "Dangerous conditions"

    def test_danger_4_ignores_problems(self) -> None:
        """Rule 1 wins regardless of problem types."""
        rm = _render_model(
            "4",
            problems=[_problem("no_distinct_avalanche_problem")],
        )
        assert compute_day_character(rm) == "Dangerous conditions"


class TestRule2HardToReadDay:
    """Tests for rule 2 — Hard-to-read day (persistent/gliding problems)."""

    def test_persistent_weak_layers_at_moderate(self) -> None:
        """Danger 2 + persistent weak layers → Hard-to-read day."""
        rm = _render_model("2", problems=[_problem("persistent_weak_layers")])
        assert compute_day_character(rm) == "Hard-to-read day"

    def test_gliding_snow_at_considerable(self) -> None:
        """Danger 3 + gliding snow → Hard-to-read day."""
        rm = _render_model("3", problems=[_problem("gliding_snow")])
        assert compute_day_character(rm) == "Hard-to-read day"

    def test_does_not_trigger_at_danger_1(self) -> None:
        """Danger 1 + persistent weak layers → does NOT trigger rule 2."""
        rm = _render_model("1", problems=[_problem("persistent_weak_layers")])
        assert compute_day_character(rm) == "Stable day"

    def test_mixed_problems_still_triggers(self) -> None:
        """Any hard-to-read problem among others triggers the rule."""
        rm = _render_model(
            "2",
            problems=[
                _problem("new_snow"),
                _problem("persistent_weak_layers"),
            ],
        )
        assert compute_day_character(rm) == "Hard-to-read day"


class TestRule3WidespreadDanger:
    """Tests for rule 3 — Widespread danger (broad exposure at level 3)."""

    def test_six_or_more_unique_aspects(self) -> None:
        """Danger 3 + >= 6 unique aspects → Widespread danger."""
        rm = _render_model(
            "3",
            problems=[
                _problem(
                    "new_snow",
                    aspects=["N", "NE", "E", "SE", "S", "SW"],
                ),
            ],
        )
        assert compute_day_character(rm) == "Widespread danger"

    def test_low_elevation_bound(self) -> None:
        """Danger 3 + lower bound <= 2000m → Widespread danger."""
        rm = _render_model(
            "3",
            problems=[_problem("new_snow", lower=1800)],
        )
        assert compute_day_character(rm) == "Widespread danger"

    def test_elevation_exactly_2000(self) -> None:
        """Danger 3 + lower bound == 2000m → Widespread danger."""
        rm = _render_model(
            "3",
            problems=[_problem("new_snow", lower=2000)],
        )
        assert compute_day_character(rm) == "Widespread danger"

    def test_two_or_more_problems(self) -> None:
        """Danger 3 + >= 2 problems → Widespread danger."""
        rm = _render_model(
            "3",
            problems=[
                _problem("new_snow", lower=2400),
                _problem("wind_slab", lower=2600),
            ],
        )
        assert compute_day_character(rm) == "Widespread danger"

    def test_not_triggered_at_danger_2(self) -> None:
        """Rule 3 only applies at danger 3, not danger 2."""
        rm = _render_model(
            "2",
            problems=[
                _problem(
                    "new_snow",
                    aspects=["N", "NE", "E", "SE", "S", "SW"],
                ),
            ],
        )
        # Rule 2 doesn't match (new_snow isn't hard-to-read), so falls
        # through to Rule 4 → Manageable day.
        assert compute_day_character(rm) == "Manageable day"


class TestRule3bWidespreadSubdivision:
    """Tests for rule 3b — Widespread danger (3+ subdivision)."""

    def test_three_plus_returns_widespread(self) -> None:
        """Danger 3+ → Widespread danger even without broad exposure."""
        rm = _render_model(
            "3",
            danger_subdivision="+",
            problems=[_problem("new_snow", lower=2600, aspects=["N"])],
        )
        assert compute_day_character(rm) == "Widespread danger"

    def test_three_minus_does_not_trigger(self) -> None:
        """Danger 3- does not trigger rule 3b."""
        rm = _render_model(
            "3",
            danger_subdivision="-",
            problems=[_problem("new_snow", lower=2600, aspects=["N"])],
        )
        assert compute_day_character(rm) == "Manageable day"


class TestRule4ManageableDay:
    """Tests for rule 4 — Manageable day (danger 2 or 3, no earlier match)."""

    def test_danger_2_with_new_snow(self) -> None:
        """Danger 2 + new_snow (not hard-to-read) → Manageable day."""
        rm = _render_model("2", problems=[_problem("new_snow")])
        assert compute_day_character(rm) == "Manageable day"

    def test_danger_3_narrow_exposure(self) -> None:
        """Danger 3 with narrow exposure and no hard-to-read problems."""
        rm = _render_model(
            "3",
            problems=[_problem("wind_slab", lower=2600, aspects=["N", "NE"])],
        )
        assert compute_day_character(rm) == "Manageable day"


class TestRule5StableDay:
    """Tests for rule 5 — Stable day."""

    def test_danger_1_is_stable(self) -> None:
        """Danger 1 → Stable day."""
        assert compute_day_character(_render_model("1")) == "Stable day"

    def test_danger_2_all_benign_problems(self) -> None:
        """Danger 2 with only no_distinct_avalanche_problem → Stable day."""
        rm = _render_model(
            "2",
            problems=[_problem("no_distinct_avalanche_problem")],
        )
        assert compute_day_character(rm) == "Stable day"

    def test_danger_2_no_problems_is_stable(self) -> None:
        """Danger 2 with empty problems list → Stable day."""
        rm = _render_model("2", problems=[])
        assert compute_day_character(rm) == "Stable day"


class TestSafeDefault:
    """Tests for the safe default fallback."""

    def test_empty_render_model_defaults_to_stable(self) -> None:
        """A completely empty render model dict defaults to Stable day."""
        assert compute_day_character({}) == "Stable day"


@pytest.mark.django_db
class TestDayCharacterInPanelContext:
    """Tests that day_character is included in _build_panel_context."""

    def test_panel_context_contains_day_character(self) -> None:
        """The panel context dict includes the day_character key."""
        from datetime import UTC, datetime

        from public.views import _build_panel_context
        from tests.factories import BulletinFactory

        def _wrap(properties: dict) -> dict:
            return {"type": "Feature", "geometry": None, "properties": properties}

        bulletin = BulletinFactory.create(
            raw_data=_wrap(
                {
                    "dangerRatings": [{"mainValue": "considerable"}],
                    "avalancheProblems": [
                        {
                            "problemType": "persistent_weak_layers",
                            "validTimePeriod": "all_day",
                        }
                    ],
                }
            ),
            issued_at=datetime(2025, 3, 15, 8, 0, tzinfo=UTC),
            valid_from=datetime(2025, 3, 15, 7, 0, tzinfo=UTC),
            valid_to=datetime(2025, 3, 15, 18, 0, tzinfo=UTC),
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["day_character"] == "Hard-to-read day"
