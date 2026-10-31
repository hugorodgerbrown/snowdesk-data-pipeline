"""
tests/ingest/normalise/test_derived.py — the render model's derived block (SNOW-1341).

The builder stores what every reader used to compute from the model:
terrain rules, period transition, problem family, headline inputs, alert
state and danger levels. Each must equal what the function it replaces
computes, for a sentinel from every provider, and the readers' accessors
(``derived_of``, ``period_transition_of``, ``state_of``) must read the
stored block and compute only when there is none.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from apps.ingest.normalise.bulletin_state import bulletin_state
from apps.ingest.normalise.problem_terrain import terrain_rules
from apps.ingest.normalise.render_model import (
    build_derived,
    build_render_model,
    compute_period_transition,
    derive_problem_family,
    derived_of,
    period_transition_of,
    state_of,
)
from tests.ingest.normalise.test_bulletin_state import GOLDEN_FINGERPRINTS

SENTINELS_DIR = Path(__file__).resolve().parents[2] / "sentinels"


def _model(sentinel: str) -> dict[str, Any]:
    """Return a sentinel's render model, as the builder makes it."""
    raw = json.loads((SENTINELS_DIR / sentinel / "source.json").read_text())
    return build_render_model(raw)


def _without_derived(model: dict[str, Any]) -> dict[str, Any]:
    """Return the model as the readers saw it before SNOW-1341."""
    return {k: v for k, v in model.items() if k != "derived"}


@pytest.mark.parametrize("sentinel", sorted(GOLDEN_FINGERPRINTS))
class TestEverySentinel:
    """The stored block equals what each reader computed."""

    def test_the_block_is_the_builders(self, sentinel: str) -> None:
        """Stored once, from the model it sits in."""
        model = _model(sentinel)
        assert model["derived"] == build_derived(_without_derived(model))

    def test_each_value_is_the_function_it_replaces(self, sentinel: str) -> None:
        """Terrain rules, transition, family and state, one by one."""
        model = _model(sentinel)
        bare = _without_derived(model)
        derived = model["derived"]
        assert derived["terrain_rules"] == terrain_rules(bare)
        transition = compute_period_transition(bare)
        assert derived["period_transition"] == (
            dataclasses.asdict(transition) if transition else None
        )
        assert period_transition_of(derived) == transition
        assert derived["problem_family"] == derive_problem_family(bare)
        state = bulletin_state(bare)
        assert derived["state"] == {
            **state.to_json(),
            "fingerprint": state.fingerprint(),
        }
        assert derived["state"]["fingerprint"] == GOLDEN_FINGERPRINTS[sentinel]

    def test_headline_inputs_are_the_five_the_view_projected(
        self, sentinel: str
    ) -> None:
        """Source, partition, peak rating, direction and family."""
        model = _model(sentinel)
        bare = _without_derived(model)
        transition = compute_period_transition(bare)
        danger = bare["danger"]
        sub = danger.get("subdivision") or ""
        assert model["derived"]["headline_inputs"] == {
            "source": bare["source"],
            "partition_type": transition.partition_type if transition else "none",
            "peak_rating": f"{danger['number']}{sub}",
            "direction": transition.direction if transition else "none",
            "family": derive_problem_family(bare),
        }

    def test_danger_levels_are_the_ratings_keys_up_the_scale(
        self, sentinel: str
    ) -> None:
        """Distinct, ordered low → very_high, the max the last of them."""
        model = _model(sentinel)
        order = ["low", "moderate", "considerable", "high", "very_high"]
        keys = {r["key"] for r in model["danger"]["ratings"]}
        levels = model["derived"]["danger_levels"]["levels"]
        assert levels == [k for k in order if k in keys]
        assert model["derived"]["danger_levels"]["max"] == levels[-1]


def test_an_elevation_split_lists_both_levels() -> None:
    """ALBINA B rates low below 2200 m and considerable above."""
    assert _model("albina/B-elevation-band-split")["derived"]["danger_levels"] == {
        "levels": ["low", "considerable"],
        "max": "considerable",
    }


def test_levels_are_ordered_whatever_order_the_ratings_come_in() -> None:
    """Highest first in the CAAML; still low first in the block."""
    model = {
        "danger": {
            "ratings": [{"key": "high"}, {"key": "moderate"}, {"key": "high"}],
        }
    }
    assert build_derived(model)["danger_levels"] == {
        "levels": ["moderate", "high"],
        "max": "high",
    }


def test_a_model_with_no_ratings_names_no_level() -> None:
    """The version-0 sentinel: no levels, no max, no rules."""
    sentinel = {"version": 0, "error": "boom", "error_type": "RenderModelBuildError"}
    derived = derived_of(sentinel)
    assert derived["danger_levels"] == {"levels": [], "max": None}
    assert derived["terrain_rules"] == []
    assert derived["period_transition"] is None
    assert derived["headline_inputs"] == {
        "source": "",
        "partition_type": "none",
        "peak_rating": "1",
        "direction": "none",
        "family": "other",
    }


class TestReaders:
    """derived_of, period_transition_of and state_of."""

    def test_derived_of_reads_the_stored_block(self) -> None:
        """It is returned as stored, not recomputed."""
        stored = {"terrain_rules": ["stored"]}
        assert derived_of({"derived": stored, "traits": []}) is stored

    def test_derived_of_computes_for_a_model_without_one(self) -> None:
        """A pre-v10 or sentinel model reads as before."""
        model = _without_derived(_model("slf/C-split-day-multi-problem"))
        assert derived_of(model) == build_derived(model)
        assert derived_of(None) == build_derived({})

    def test_state_of_takes_the_stored_fingerprint(self) -> None:
        """The send log's key is the one the service stored."""
        model = _model("slf/A-single-level")
        model["derived"]["state"]["fingerprint"] = "stored-value"
        state, fingerprint = state_of(model)
        assert fingerprint == "stored-value"
        assert state == bulletin_state(_without_derived(model))

    def test_state_of_recomputes_a_state_of_another_version(self) -> None:
        """A state stored under another BULLETIN_STATE_VERSION is not trusted."""
        model = _model("slf/A-single-level")
        model["derived"]["state"] = {"version": 999, "fingerprint": "old"}
        state, fingerprint = state_of(model)
        assert state == bulletin_state(_without_derived(model))
        assert fingerprint == GOLDEN_FINGERPRINTS["slf/A-single-level"]
