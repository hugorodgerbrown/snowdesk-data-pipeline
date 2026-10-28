"""
tests/ingest/normalise/test_day_rating.py — rate_render_model (SNOW-1270).

The service's reading of one chosen bulletin's render model into a day's
danger range. The helpers it composes (the band split, the min/max and AM/PM
resolution, the ALBINA bands) keep their own tests in
tests/bulletins/services/test_day_rating.py, which also covers the app's
selection and write end to end; this file covers the composition and its
three outcomes: rated, quiet-day and unrated.
"""

from __future__ import annotations

from typing import Any

from apps.ingest.normalise.day_rating import (
    NO_RATING,
    rate_render_model,
    unrated_day,
)


def _trait(level: int, period: str = "all_day") -> dict[str, Any]:
    """A render-model trait at ``level`` for ``period``."""
    return {"danger_level": level, "time_period": period, "problems": []}


def _model(**overrides: Any) -> dict[str, Any]:
    """A considerable SLF render model with one all-day trait."""
    model: dict[str, Any] = {
        "source": "SLF",
        "danger": {"key": "considerable", "subdivision": "+", "ratings": []},
        "traits": [_trait(3)],
    }
    model.update(overrides)
    return model


def test_a_uniform_day_is_the_headline() -> None:
    """One all-day trait: min and max are the headline, no AM/PM split."""
    rating = rate_render_model(_model(), "b-1")
    assert rating["rated"] is True
    assert (rating["min_rating"], rating["max_rating"]) == (
        "considerable",
        "considerable",
    )
    assert rating["max_subdivision"] == "+"
    assert rating["am_rating"] is None
    assert rating["source"] == "SLF"
    assert rating["bands"] is None


def test_an_afternoon_rise_splits_the_day() -> None:
    """A later trait above the morning one: min is the morning, max the afternoon."""
    model = _model(traits=[_trait(2, "earlier"), _trait(3, "later")])
    rating = rate_render_model(model, "b-1")
    assert (rating["min_rating"], rating["max_rating"]) == ("moderate", "considerable")
    assert (rating["am_rating"], rating["pm_rating"]) == ("moderate", "considerable")


def test_a_quiet_day_falls_back_to_the_headline() -> None:
    """No traits: the headline key, still rated."""
    rating = rate_render_model(_model(traits=[]), "b-1")
    assert rating["rated"] is True
    assert rating["min_rating"] == rating["max_rating"] == "considerable"


def test_a_quiet_day_with_an_elevation_split_uses_the_bands() -> None:
    """Two all-day ratings with different keys: min and max are the bands."""
    ratings = [
        {"period": "all_day", "key": "moderate"},
        {"period": "all_day", "key": "considerable"},
    ]
    model = _model(
        traits=[], danger={"key": "considerable", "subdivision": "", "ratings": ratings}
    )
    rating = rate_render_model(model, "b-1")
    assert (rating["min_rating"], rating["max_rating"]) == ("moderate", "considerable")


def test_no_usable_trait_is_unrated() -> None:
    """Traits without a level: every key no_rating, but the source is kept."""
    rating = rate_render_model(_model(traits=[{"danger_level": None}]), "b-1")
    assert rating["rated"] is False
    assert rating["min_rating"] == rating["max_rating"] == NO_RATING
    assert rating["source"] == "SLF"


def test_an_albina_bulletin_carries_its_bands() -> None:
    """ALBINA's elevation bands are derived; other providers' are not."""
    ratings = [
        {"period": "all_day", "key": "moderate", "elevation": {"upper": 2000}},
        {"period": "all_day", "key": "considerable", "elevation": {"lower": 2000}},
    ]
    model = _model(
        source="ALBINA",
        danger={"key": "considerable", "subdivision": "", "ratings": ratings},
    )
    bands = rate_render_model(model, "b-1")["bands"]
    assert bands is not None
    assert [band["band_id"] for band in bands] == ["above-2000", "below-2000"]


def test_a_missing_render_model_is_rated_low() -> None:
    """None reads as an empty model: the headline defaults to low."""
    rating = rate_render_model(None, "b-1")
    assert rating["min_rating"] == "low"


def test_an_unrated_day_names_nothing() -> None:
    """The day no bulletin governs."""
    rating = unrated_day()
    assert rating["rated"] is False
    assert rating["source"] == ""
    assert rating["min_rating"] == rating["max_rating"] == NO_RATING
