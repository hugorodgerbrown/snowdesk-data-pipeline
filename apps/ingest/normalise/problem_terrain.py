"""
apps/ingest/normalise/problem_terrain.py — problem-terrain rules (SNOW-979).

A bulletin's problems as rules the map can test a pixel of terrain against.

The region sheet's "Sync with map terrain" switch paints the
region's steep ground by what the day's bulletin says about it. The tiles
carry the facts — each pixel's height, aspect octant and slope band — and
the device does the painting (``static/js/bulletin_terrain_core.js``), so
the only daily input is this: one small rule per avalanche problem.

A rule is::

    {
        "rating": 3,            # the problem's EAWS level, 1–5
        "aspects": [7, 0, 1],   # octant indices, N=0 … NW=7; [] = every aspect
        "lower": 2400,          # "above": a pixel at or over this height, or None
        "upper": None,          # "below": a pixel under this height, or None
        "decided": True,        # False → the pixel test cannot place it
    }

How the rules are read from the render model:

- Every trait's problems, morning and afternoon alike. The map has a date
  control, not a time of day, so the worse of the two periods is drawn —
  the peak rule ``docs/compressed-views-rating-rule.md`` sets for every
  compressed view. The client resolves overlaps by the highest rating.
- An empty aspect list matches every aspect.
- A problem the pixel test cannot place is ``decided: False`` and paints
  as UNDECIDED rather than as a match or a miss: one bounded by the
  treeline (no treeline height exists anywhere in the codebase), and one
  in a trait whose geography came from prose only (``geography.source ==
  "prose_only"``), whose aspects and heights did not come from structured
  data.
- "No distinct avalanche problem" and "favourable situation" describe no
  ground, so they make no rule.

The rules say where the bulletin DESCRIBES its problems, not where it is
dangerous: a bulletin's aspects and heights mark its core zone, not a
boundary. See docs/decisions/the-bulletin-terrain-layer-paints-what-it-cannot-decide.md.

Moved here from ``apps/bulletins/services/`` by SNOW-1270: the rules are a
pure reading of the service's render model, so they are the service's too
(``apps.ingest.public.terrain_rules``). The map's feed that groups them by
region, ``apps/map/problem_terrain.py``, stays the instance's.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

# The CAAML aspect enum in octant order, N=0 … NW=7 — the indices the tiles'
# octant bits use. Ingest imports no other apps.* package, so this is its own
# copy of ``track.geo.OCTANTS``; tests/ingest/normalise/
# test_problem_terrain.py checks the two agree.
OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# EAWS level name → the number the map paints and compares.
_RATING_NUMBER: dict[str, int] = {
    "low": 1,
    "moderate": 2,
    "considerable": 3,
    "high": 4,
    "very_high": 5,
}

# Problem types that describe no ground at all.
_NO_GROUND_PROBLEMS = frozenset(
    {"no_distinct_avalanche_problem", "favourable_situation"}
)

# The first 5° slope band the map paints problem terrain on: 30° and up.
# The same threshold as ``STEEP_BAND`` in static/js/bulletin_terrain_core.js,
# so a point the "Your location" sheet calls problem terrain (SNOW-1395) is a
# point the map's layer paints as one.
STEEP_BAND = 6

_TREELINE_TOKEN = "treeline"  # noqa: S105 — not a password; a CAAML bound token


class TerrainRule(TypedDict):
    """One avalanche problem, as the map tests a pixel against it."""

    rating: int
    aspects: list[int]
    lower: int | None
    upper: int | None
    decided: bool


def _octants(aspects: list[Any]) -> list[int]:
    """Map CAAML aspect names to octant indices, dropping anything else.

    Args:
        aspects: The problem's aspects, e.g. ``["N", "NE"]``; CAAML's
            ``"n/a"`` and anything unknown are ignored.

    Returns:
        Sorted, de-duplicated octant indices (N=0 … NW=7).

    """
    found = {
        OCTANTS.index(str(a).upper()) for a in aspects if str(a).upper() in OCTANTS
    }
    return sorted(found)


def _bound(value: Any) -> int | None:
    """Read one elevation bound as whole metres.

    Args:
        value: The bound as the render model holds it — an int, a numeric
            string, ``None``, or the ``"treeline"`` token.

    Returns:
        The height in metres, or ``None`` for no usable number.

    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _is_treeline(elevation: dict[str, Any]) -> bool:
    """Whether either bound of an elevation band is the treeline.

    Args:
        elevation: A parsed elevation dict from the render model.

    Returns:
        True when the band is pivoted on the treeline.

    """
    if elevation.get("treeline"):
        return True
    return any(
        str(elevation.get(key) or "").lower() == _TREELINE_TOKEN
        for key in ("lower", "upper")
    )


def _rating_of(problem: dict[str, Any], trait: dict[str, Any]) -> int | None:
    """Return the EAWS level a problem is painted at.

    Args:
        problem: A render-model problem.
        trait: The trait it belongs to, whose ``danger_level`` is the
            fallback when the problem carries no rating of its own.

    Returns:
        1–5, or ``None`` when neither gives a level.

    """
    rating = _RATING_NUMBER.get(str(problem.get("danger_rating_value") or ""))
    if rating is not None:
        return rating
    level = trait.get("danger_level")
    return level if isinstance(level, int) and 1 <= level <= 5 else None


class ProblemAtPoint(TypedDict):
    """One avalanche problem whose decided rule admits a point of ground."""

    problem_type: str
    rating: int
    lower: int | None
    upper: int | None


def _problem_rules(
    render_model: dict[str, Any] | None,
) -> Iterator[tuple[str, TerrainRule]]:
    """Yield each problem of a render model as its type and its rule.

    The one reading of a bulletin's problems as ground, shared by the map's
    rules (``terrain_rules``) and the point test (``problems_at``), so the
    two cannot disagree about which ground a problem describes.

    Args:
        render_model: A bulletin's render model, or ``None``.

    Yields:
        ``(problem_type, rule)`` for every problem that describes ground and
        carries a rating, in the order the model holds them.

    """
    if not render_model:
        return
    for trait in render_model.get("traits") or []:
        prose_only = (trait.get("geography") or {}).get("source") == "prose_only"
        for problem in trait.get("problems") or []:
            problem_type = str(problem.get("problem_type") or "")
            if problem_type in _NO_GROUND_PROBLEMS:
                continue
            rating = _rating_of(problem, trait)
            if rating is None:
                logger.debug("problem %r has no rating; skipped", problem_type)
                continue
            # ALBINA carries its band on the trait; a problem's own wins.
            elevation = problem.get("elevation") or trait.get("elevation") or {}
            treeline = _is_treeline(elevation)
            yield (
                problem_type,
                {
                    "rating": rating,
                    "aspects": _octants(problem.get("aspects") or []),
                    "lower": None if treeline else _bound(elevation.get("lower")),
                    "upper": None if treeline else _bound(elevation.get("upper")),
                    "decided": not (prose_only or treeline),
                },
            )


def terrain_rules(render_model: dict[str, Any] | None) -> list[TerrainRule]:
    """Turn a bulletin's render model into the map's problem-terrain rules.

    Args:
        render_model: A bulletin's render model (the panel's, so a v3
            bulletin has already been rebuilt), or ``None``.

    Returns:
        One rule per distinct problem, highest rating first; empty when the
        bulletin describes no ground.

    """
    rules: list[TerrainRule] = []
    seen: set[tuple[Any, ...]] = set()
    for _problem_type, rule in _problem_rules(render_model):
        key = (
            rule["rating"],
            tuple(rule["aspects"]),
            rule["lower"],
            rule["upper"],
            rule["decided"],
        )
        if key in seen:
            continue
        seen.add(key)
        rules.append(rule)
    rules.sort(key=lambda r: -r["rating"])
    return rules


def _admits(rule: TerrainRule, height_m: float, octant: int) -> bool:
    """Whether a rule admits one point of ground, as the map's layer tests a pixel.

    Args:
        rule: One problem's rule.
        height_m: The ground's height in metres.
        octant: The ground's aspect octant, N=0 … NW=7.

    Returns:
        True when the aspect is one the rule names (an empty list names
        every aspect) and the height is at or over ``lower`` and under
        ``upper``, each where it is set.

    """
    if rule["aspects"] and octant not in rule["aspects"]:
        return False
    if rule["lower"] is not None and height_m < rule["lower"]:
        return False
    return not (rule["upper"] is not None and height_m >= rule["upper"])


def problems_at(
    render_model: dict[str, Any] | None,
    *,
    height_m: float,
    octant: int,
    slope_band: int,
) -> list[ProblemAtPoint]:
    """Return the problems whose ground one point is on (SNOW-1395).

    The "Your location" sheet's test: the same rules, the same aspect and
    height test and the same steepness threshold as the map's
    problem-terrain layer (``static/js/bulletin_terrain_core.js``), so the
    sheet calls a point problem terrain exactly where the map paints it as
    one. Only DECIDED rules count: an undecided one is the map's hatched
    "cannot tell", and the sheet says nothing it cannot back (Hugo, 3 Nov
    2026: alert when inside, otherwise say nothing).

    Args:
        render_model: The day's bulletin's render model, or ``None``.
        height_m: The ground's height in metres.
        octant: The ground's aspect octant, N=0 … NW=7.
        slope_band: ``floor(slope / 5)`` — the tiles' band.

    Returns:
        One entry per distinct problem type that admits the point, highest
        rating first; empty on ground under 30°, or when nothing admits it.

    """
    if slope_band < STEEP_BAND:
        return []
    found: dict[str, ProblemAtPoint] = {}
    for problem_type, rule in _problem_rules(render_model):
        if not rule["decided"] or not _admits(rule, height_m, octant):
            continue
        held = found.get(problem_type)
        if held is None or rule["rating"] > held["rating"]:
            found[problem_type] = {
                "problem_type": problem_type,
                "rating": rule["rating"],
                "lower": rule["lower"],
                "upper": rule["upper"],
            }
    return sorted(found.values(), key=lambda p: -p["rating"])
