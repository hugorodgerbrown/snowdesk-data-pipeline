"""
tests/bulletins/services/test_bulletin_change.py — The alert change signal.

Builds render models in their own shape and checks that bulletin_state and
summarise_change report exactly the movements a reader would notice.
"""

from __future__ import annotations

from typing import Any

from apps.bulletins.services.bulletin_change import (
    BulletinState,
    ProblemState,
    bulletin_state,
    summarise_change,
)


def problem(
    problem_type: str,
    aspects: list[str] | None = None,
    lower: int | None = None,
    upper: int | None = None,
    rating: str | None = None,
    period: str = "all_day",
) -> dict[str, Any]:
    """Return one render-model problem dict."""
    elevation = (
        None
        if lower is None and upper is None
        else {"lower": lower, "upper": upper, "treeline": False}
    )
    return {
        "problem_type": problem_type,
        "time_period": period,
        "aspects": aspects or [],
        "elevation": elevation,
        "danger_rating_value": rating,
    }


def render(
    key: str = "moderate",
    subdivision: str | None = None,
    problems: list[dict[str, Any]] | None = None,
    unscheduled: bool = False,
) -> dict[str, Any]:
    """Return a minimal render model."""
    return {
        "danger": {"key": key, "number": "2", "subdivision": subdivision},
        "traits": [{"problems": problems or []}],
        "metadata": {"unscheduled": unscheduled},
    }


WIND = problem("wind_slab", ["N", "NE", "E"], lower=2400, rating="moderate")


class TestBulletinState:
    """bulletin_state reduces a render model."""

    def test_reads_rating_and_problems(self) -> None:
        """The headline, sub-rating and problem facts are read."""
        state = bulletin_state(render("considerable", "+", [WIND]))
        assert state.rating == "considerable"
        assert state.level == 3
        assert state.subdivision == "+"
        assert state.problems == (
            ProblemState("wind_slab", ("N", "NE", "E"), 2400, None, None, "moderate"),
        )

    def test_drops_neutral_problems(self) -> None:
        """Placeholder problems name no hazard."""
        state = bulletin_state(
            render("low", problems=[problem("no_distinct_avalanche_problem")])
        )
        assert state.problems == ()

    def test_merges_windows_of_one_problem(self) -> None:
        """A problem in both windows is one problem with the widest reach."""
        rm = render(
            problems=[
                problem("wet_snow", ["S"], 2000, 2800, "moderate", "earlier"),
                problem("wet_snow", ["SE", "S"], 1800, 3000, "considerable", "later"),
            ]
        )
        (wet,) = bulletin_state(rm).problems
        assert wet.aspects == ("SE", "S")
        assert (wet.lower, wet.upper) == (1800, 3000)
        assert wet.rating == "considerable"

    def test_merge_keeps_an_open_bound_open(self) -> None:
        """One window with no upper bound leaves the merged band open."""
        rm = render(
            problems=[
                problem("wet_snow", ["S"], 2000, 2800, period="earlier"),
                problem("wet_snow", ["S"], 2200, None, period="later"),
            ]
        )
        (wet,) = bulletin_state(rm).problems
        assert (wet.lower, wet.upper) == (2000, None)

    def test_reads_treeline_side(self) -> None:
        """A treeline bound keeps which side it stood for."""
        rm = render(problems=[problem("wind_slab")])
        rm["traits"][0]["problems"][0]["elevation"] = {
            "lower": None,
            "upper": None,
            "treeline": True,
            "treeline_side": "upper",
        }
        (wind,) = bulletin_state(rm).problems
        assert wind.treeline == "upper"

    def test_fingerprint_ignores_order(self) -> None:
        """Same facts in another order give the same fingerprint."""
        new = problem("new_snow", ["W", "N"], 2000)
        a = bulletin_state(render(problems=[WIND, new]))
        b = bulletin_state(render(problems=[new | {"aspects": ["N", "W"]}, WIND]))
        assert a.fingerprint() == b.fingerprint()

    def test_fingerprint_ignores_unscheduled(self) -> None:
        """An off-schedule re-issue saying the same thing is not news."""
        a = bulletin_state(render(problems=[WIND]))
        b = bulletin_state(render(problems=[WIND], unscheduled=True))
        assert a.fingerprint() == b.fingerprint()

    def test_fingerprint_moves_with_content(self) -> None:
        """A moved band changes the fingerprint."""
        a = bulletin_state(render(problems=[WIND]))
        b = bulletin_state(render(problems=[WIND | {"elevation": {"lower": 2200}}]))
        assert a.fingerprint() != b.fingerprint()

    def test_json_round_trip(self) -> None:
        """A stored state reads back equal."""
        state = bulletin_state(render("high", "-", [WIND], unscheduled=True))
        assert BulletinState.from_json(state.to_json()) == state

    def test_from_json_rejects_other_versions(self) -> None:
        """A row written under another version is not compared."""
        data = bulletin_state(render()).to_json() | {"version": 0}
        assert BulletinState.from_json(data) is None
        assert BulletinState.from_json(None) is None

    def test_empty_render_model(self) -> None:
        """A render model with nothing in it reads as unrated."""
        state = bulletin_state({})
        assert state.level == 0
        assert state.problems == ()


class TestSummariseChange:
    """summarise_change names every movement."""

    def test_no_previous(self) -> None:
        """With nothing to compare, the change is empty."""
        change = summarise_change(None, bulletin_state(render()))
        assert change.is_empty
        assert change.rating_direction == 0

    def test_no_change(self) -> None:
        """Identical states are empty."""
        a = bulletin_state(render(problems=[WIND]))
        assert summarise_change(a, a).is_empty

    def test_rating_rose_and_fell(self) -> None:
        """A level move is directional."""
        low = bulletin_state(render("moderate"))
        high = bulletin_state(render("considerable"))
        assert summarise_change(low, high).rating_direction == 1
        assert summarise_change(high, low).rating_direction == -1
        assert not summarise_change(high, low).is_empty

    def test_subdivision_only(self) -> None:
        """A sub-rating move with the level held is a change."""
        a = bulletin_state(render("considerable", "-"))
        b = bulletin_state(render("considerable", "+"))
        change = summarise_change(a, b)
        assert change.rating_direction == 0
        assert change.subdivision_moved
        assert not change.is_empty

    def test_subdivision_not_reported_with_level_move(self) -> None:
        """A level move carries its own sub-rating; no second movement."""
        a = bulletin_state(render("moderate", "+"))
        b = bulletin_state(render("considerable", "-"))
        assert not summarise_change(a, b).subdivision_moved

    def test_problem_added_and_removed(self) -> None:
        """A named problem arriving or leaving is listed."""
        none = bulletin_state(render())
        wind = bulletin_state(render(problems=[WIND]))
        added = summarise_change(none, wind)
        assert [p.problem_type for p in added.added] == ["wind_slab"]
        removed = summarise_change(wind, none)
        assert [p.problem_type for p in removed.removed] == ["wind_slab"]

    def test_band_moved_with_rating_held(self) -> None:
        """An elevation shift is a movement on its own."""
        a = bulletin_state(render(problems=[WIND]))
        b = bulletin_state(render(problems=[WIND | {"elevation": {"lower": 2000}}]))
        (move,) = summarise_change(a, b).moved
        assert move.band_moved
        assert not move.aspects_moved

    def test_aspects_moved_with_rating_held(self) -> None:
        """An aspect shift is a movement on its own."""
        a = bulletin_state(render(problems=[WIND]))
        b = bulletin_state(render(problems=[WIND | {"aspects": ["N", "NW"]}]))
        (move,) = summarise_change(a, b).moved
        assert move.aspects_moved
        assert not move.band_moved

    def test_problem_rating_moved(self) -> None:
        """A problem's own rating moving is reported."""
        a = bulletin_state(render(problems=[WIND]))
        b = bulletin_state(
            render(problems=[WIND | {"danger_rating_value": "considerable"}])
        )
        (move,) = summarise_change(a, b).moved
        assert move.rating_moved

    def test_unscheduled_alone_is_empty(self) -> None:
        """The season's off-schedule re-issue saying the same is empty."""
        a = bulletin_state(render(problems=[WIND]))
        b = bulletin_state(render(problems=[WIND], unscheduled=True))
        change = summarise_change(a, b)
        assert change.is_empty
        assert change.current.unscheduled

    def test_albina_shape_has_no_subdivision(self) -> None:
        """ALBINA and Météo-France send no sub-rating; None never moves."""
        a = bulletin_state(render("considerable", None))
        b = bulletin_state(render("considerable", None))
        assert not summarise_change(a, b).subdivision_moved
        assert a.rank == (3, 1)
