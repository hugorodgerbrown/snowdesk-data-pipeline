"""
tests/ingest/normalise/test_problem_terrain.py — a bulletin's problems as
the map's problem-terrain rules (apps.ingest.normalise.problem_terrain,
SNOW-979).

Covers the rules the map paints from: aspects to octant indices, the
elevation band to whole-metre bounds, a missing problem rating falling back
to the trait's level, treeline and prose-only problems as undecided,
problems that describe no ground dropped, morning and afternoon issues
flattened (the peak rule), duplicates collapsed, highest rating first.
"""

from __future__ import annotations

from typing import Any

from apps.ingest.normalise.problem_terrain import OCTANTS, problems_at, terrain_rules
from track.geo import OCTANTS as CORE_OCTANTS


def _problem(**overrides: Any) -> dict[str, Any]:
    """A render-model problem: a considerable wind slab, N–NE above 2400 m."""
    problem: dict[str, Any] = {
        "problem_type": "wind_slab",
        "aspects": ["N", "NE"],
        "elevation": {"lower": 2400, "upper": None, "treeline": False},
        "danger_rating_value": "considerable",
    }
    problem.update(overrides)
    return problem


def _trait(*problems: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """A render-model trait holding ``problems``."""
    trait: dict[str, Any] = {
        "category": "dry",
        "time_period": "all_day",
        "geography": {"source": "problems"},
        "problems": list(problems),
        "danger_level": 3,
        "elevation": None,
    }
    trait.update(overrides)
    return trait


def _model(*traits: dict[str, Any]) -> dict[str, Any]:
    """A render model with ``traits``."""
    return {"traits": list(traits)}


class TestTerrainRules:
    """terrain_rules reads one rule per problem off the render model."""

    def test_no_render_model_is_no_rules(self) -> None:
        """A day with no bulletin paints nothing."""
        assert terrain_rules(None) == []
        assert terrain_rules({}) == []

    def test_one_problem_is_one_decided_rule(self) -> None:
        """Aspects become octant indices; the band becomes metre bounds."""
        assert terrain_rules(_model(_trait(_problem()))) == [
            {
                "rating": 3,
                "aspects": [0, 1],
                "lower": 2400,
                "upper": None,
                "decided": True,
            }
        ]

    def test_aspects_are_sorted_and_unknown_ones_dropped(self) -> None:
        """NW before N in the bulletin is still [0, 7]; CAAML's n/a is ignored."""
        rules = terrain_rules(_model(_trait(_problem(aspects=["NW", "n/a", "n", "N"]))))
        assert rules[0]["aspects"] == [0, 7]

    def test_empty_aspects_stay_empty_meaning_every_aspect(self) -> None:
        """The client reads [] as every aspect, so nothing is invented here."""
        rules = terrain_rules(_model(_trait(_problem(aspects=[]))))
        assert rules[0]["aspects"] == []

    def test_string_bounds_are_read_as_metres(self) -> None:
        """SLF's bounds arrive as strings; an unusable one is no bound."""
        elevation = {"lower": "2200", "upper": "2800", "treeline": False}
        rules = terrain_rules(_model(_trait(_problem(elevation=elevation))))
        assert (rules[0]["lower"], rules[0]["upper"]) == (2200, 2800)
        rules = terrain_rules(_model(_trait(_problem(elevation={"lower": "high"}))))
        assert rules[0]["lower"] is None

    def test_no_elevation_is_every_height(self) -> None:
        """A problem with no band has no bounds."""
        rules = terrain_rules(_model(_trait(_problem(elevation=None))))
        assert (rules[0]["lower"], rules[0]["upper"]) == (None, None)

    def test_albina_band_on_the_trait_is_the_fallback(self) -> None:
        """ALBINA carries the band on the trait when the problem has none."""
        trait = _trait(
            _problem(elevation=None),
            elevation={"lower": 2000, "upper": None, "treeline": False},
        )
        assert terrain_rules(_model(trait))[0]["lower"] == 2000

    def test_treeline_band_is_undecided(self) -> None:
        """No treeline height exists, so the pixel test cannot place it."""
        elevation = {"lower": None, "upper": None, "treeline": True}
        rule = terrain_rules(_model(_trait(_problem(elevation=elevation))))[0]
        assert rule["decided"] is False
        assert (rule["lower"], rule["upper"]) == (None, None)

    def test_treeline_token_without_the_flag_is_undecided(self) -> None:
        """A raw "treeline" bound is read as the treeline too."""
        elevation = {"lower": "treeline", "upper": None}
        rule = terrain_rules(_model(_trait(_problem(elevation=elevation))))[0]
        assert rule["decided"] is False

    def test_prose_only_trait_is_undecided(self) -> None:
        """Aspects that came from prose are described, but not placed."""
        trait = _trait(_problem(), geography={"source": "prose_only"})
        assert terrain_rules(_model(trait))[0]["decided"] is False

    def test_problem_without_rating_takes_the_traits_level(self) -> None:
        """The trait's level is the fallback; with neither, no rule."""
        trait = _trait(_problem(danger_rating_value=None), danger_level=2)
        assert terrain_rules(_model(trait))[0]["rating"] == 2
        trait = _trait(_problem(danger_rating_value=None), danger_level=None)
        assert terrain_rules(_model(trait)) == []

    def test_problems_that_describe_no_ground_make_no_rule(self) -> None:
        """No distinct problem and a favourable situation paint nothing."""
        trait = _trait(
            _problem(problem_type="no_distinct_avalanche_problem"),
            _problem(problem_type="favourable_situation"),
        )
        assert terrain_rules(_model(trait)) == []

    def test_both_periods_are_kept_highest_rating_first(self) -> None:
        """Morning and afternoon are flattened: the map draws the worse."""
        morning = _trait(
            _problem(danger_rating_value="moderate"), time_period="earlier"
        )
        afternoon = _trait(
            _problem(
                problem_type="wet_snow", aspects=["S"], danger_rating_value="high"
            ),
            category="wet",
            time_period="later",
        )
        rules = terrain_rules(_model(morning, afternoon))
        assert [r["rating"] for r in rules] == [4, 2]

    def test_identical_rules_collapse(self) -> None:
        """The same problem in two traits is one rule."""
        rules = terrain_rules(_model(_trait(_problem()), _trait(_problem())))
        assert len(rules) == 1


def test_the_octants_match_the_cores() -> None:
    """Ingest's copy of the octant order is the same as track.geo's."""
    assert OCTANTS == CORE_OCTANTS


class TestProblemsAt:
    """problems_at tests one point of ground as the map's layer tests a pixel (SNOW-1395)."""

    def _at(
        self, model: dict[str, Any], height: float, octant: int, band: int = 6
    ) -> list[str]:
        """The problem types admitting the point."""
        return [
            p["problem_type"]
            for p in problems_at(model, height_m=height, octant=octant, slope_band=band)
        ]

    def test_inside_a_decided_rule(self) -> None:
        """NE at 2,410 m on 30° ground is inside N–NE above 2,400 m."""
        found = problems_at(
            _model(_trait(_problem())), height_m=2410, octant=1, slope_band=6
        )
        assert found == [
            {"problem_type": "wind_slab", "rating": 3, "lower": 2400, "upper": None}
        ]

    def test_below_the_lower_bound(self) -> None:
        assert self._at(_model(_trait(_problem())), 2399, 1) == []

    def test_the_lower_bound_is_inclusive(self) -> None:
        assert self._at(_model(_trait(_problem())), 2400, 1) == ["wind_slab"]

    def test_the_upper_bound_is_exclusive(self) -> None:
        model = _model(
            _trait(
                _problem(elevation={"lower": None, "upper": 1800, "treeline": False})
            )
        )
        assert self._at(model, 1799, 1) == ["wind_slab"]
        assert self._at(model, 1800, 1) == []

    def test_an_aspect_the_problem_does_not_name(self) -> None:
        assert self._at(_model(_trait(_problem())), 2500, 4) == []

    def test_no_aspects_names_every_aspect(self) -> None:
        assert self._at(_model(_trait(_problem(aspects=[]))), 2500, 4) == ["wind_slab"]

    def test_gentle_ground_is_never_inside(self) -> None:
        """Under 30° the map paints nothing, and the sheet says nothing."""
        assert self._at(_model(_trait(_problem())), 2500, 1, band=5) == []

    def test_an_undecided_rule_says_nothing(self) -> None:
        """A treeline band is the map's hatch, not a match."""
        model = _model(
            _trait(_problem(elevation={"lower": "treeline", "treeline": True}))
        )
        assert self._at(model, 2500, 1) == []

    def test_prose_only_geography_says_nothing(self) -> None:
        model = _model(_trait(_problem(), geography={"source": "prose_only"}))
        assert self._at(model, 2500, 1) == []

    def test_highest_rating_first_one_per_type(self) -> None:
        """Morning and afternoon issues of one type are one entry, at the worse."""
        model = _model(
            _trait(_problem(danger_rating_value="moderate", problem_type="wet_snow")),
            _trait(_problem()),
            _trait(_problem(danger_rating_value="high")),
        )
        found = problems_at(model, height_m=2500, octant=0, slope_band=7)
        assert [(p["problem_type"], p["rating"]) for p in found] == [
            ("wind_slab", 4),
            ("wet_snow", 2),
        ]

    def test_no_render_model(self) -> None:
        assert self._at({}, 2500, 1) == []

    def test_agrees_with_the_maps_rules(self) -> None:
        """Every point problems_at admits, some rule terrain_rules gives admits."""
        model = _model(_trait(_problem()), _trait(_problem(aspects=["S"])))
        rules = terrain_rules(model)
        for octant in range(8):
            for height in (2000, 2400, 3000):
                inside = bool(self._at(model, height, octant))
                ruled = any(
                    (not r["aspects"] or octant in r["aspects"])
                    and (r["lower"] is None or height >= r["lower"])
                    for r in rules
                )
                assert inside == ruled
