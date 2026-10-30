"""
tests/track/test_sac.py — the pure half of SAC tour ordering (SNOW-1316).

``track.sac`` takes a tour as anything shaped like one (``TourLike``), so
the service can order a document and an instance a row. The ordering rules
are ``tests/routes/test_ski_tours.py``'s subject, through the instance's
wrappers; this file covers what is new to the pure module: ``slugify``
matching Django's, and ``walk_tour`` re-walking a line the terrain says ran
the wrong way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from django.utils.text import slugify as django_slugify

from track.sac import WALKED_REVERSED, build_summits, slugify, walk_tour


@dataclass
class Tour:
    """A minimal ``TourLike``."""

    name: str = "Piz Buin"
    target_name: str = "Piz Buin"
    target_altitude_m: int | None = 3312
    ascent_m: int | None = 1100
    descent_m: int | None = None
    names: dict[str, str] = field(default_factory=dict)
    slope_samples: dict[str, Any] | None = None
    geometry: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "LineString",
            "coordinates": [[10.05, 46.85], [10.06, 46.86]],
        }
    )


@pytest.mark.parametrize(
    "value",
    ["Piz Buin", "Piz Palü / Bellavista", "  Säntis -- Nord  ", "Dent d'Hérens", ""],
)
def test_slugify_matches_django(value: str) -> None:
    """A summit key is the same on the service and an instance."""
    assert slugify(value) == django_slugify(value)


def test_a_walk_the_right_way_is_walked_once() -> None:
    """A way up whose heights climb is kept as walked."""
    tour = Tour()
    calls: list[list[list[float | None]]] = []

    def sample(points: list[list[float | None]], label: str) -> dict[str, Any]:
        calls.append(points)
        return {"heights": [2000.0, 3000.0]}

    summits = {s.key: s for s in build_summits([tour])}
    record = walk_tour(tour, summits, sample, "test")
    assert record is not None
    assert len(calls) == 1
    assert record[WALKED_REVERSED] is False


def test_a_walk_the_wrong_way_is_walked_again_reversed() -> None:
    """A way up whose heights fall is walked the other way round."""
    tour = Tour()
    calls: list[list[list[float | None]]] = []

    def sample(points: list[list[float | None]], label: str) -> dict[str, Any]:
        calls.append(points)
        first, last = (2000.0, 3000.0) if len(calls) > 1 else (3000.0, 2000.0)
        return {"heights": [first, last]}

    summits = {s.key: s for s in build_summits([tour])}
    record = walk_tour(tour, summits, sample, "test")
    assert record is not None
    assert len(calls) == 2
    assert calls[1] == list(reversed(calls[0]))
    assert record[WALKED_REVERSED] is True


def test_an_unanswered_walk_is_none() -> None:
    """The origin down: nothing to keep."""
    tour = Tour()
    summits = {s.key: s for s in build_summits([tour])}
    assert walk_tour(tour, summits, lambda points, label: None, "test") is None
