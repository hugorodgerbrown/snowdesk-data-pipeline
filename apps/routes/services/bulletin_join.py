"""
apps/routes/services/bulletin_join.py — where a route meets today's problem.

SNOW-839. The one row on /compare/ where both halves have been in this
codebase for months without being joined: the terrain a route crosses
(``Route.slope_samples``, SNOW-910) and the per-problem ``aspects`` and
``elevation`` the render model resolves across three providers.

The sentence this exists to produce:

    Your route crosses N–NE between 2200 and 2800 m, which is where
    today's persistent weak layer sits.

**AN INTERSECTION, NEVER A SCORE.** No number comes out of here, and that
is a position rather than a simplification. ``docs/decisions/
location-first-information-model.md`` already states the rule the whole
product is built on — *highlight, never suppress*: a problem is never
hidden because it does not apply at the user's elevation, because that is
"the line between helping someone read a bulletin and deciding for them,
and it is what our liability disclaimer rests on". A score crosses that
line in the other direction. It suppresses everything it does not score,
and the suppression is invisible: a reader shown "72" cannot tell what
was weighed, what was missing, or what the number would have been had the
forecast been one level out on their slope. So this module reports which
stretches of a track fall inside a problem's own stated aspects and
elevations, and stops there. The reader does the deciding, with the
bulletin they were going to read anyway.

**ELEVATION COMES FROM THE TRACK, AND THAT IS THE EXACT INVERSE OF
SNOW-910'S RULE.** Steepness must never be read off the track's own
elevation series, because a skin track zigzagging up a 38 degree face
rises about 15 degrees along its own length. Where the skier IS, however,
is precisely the track — a GPX's ``<ele>`` is a measurement of where the
recorder stood — so the third ordinate of ``Route.points`` is the right
and only source for the elevation band. Both rules follow from one
principle: take each figure from the thing that actually carries it. They
look contradictory side by side, which is why they are written down
together here and in ``slope_segments``.

**ASPECT COMES FROM THE GROUND**, and was stored for this by SNOW-910 —
``sample_slope`` computes it from the same kernel as the angle, so it
cost nothing then and re-sampling a whole route to recover it now would
be a second pass over the tile origin.

**TREELINE IS AN HONEST UNKNOWN.** A CAAML band bounded by ``treeline``
carries no altitude, and this project has no treeline model. Such a
problem is matched on aspect and reported with its band named rather than
resolved — "above the treeline" is what the bulletin said, and inventing
a metre figure for it would be our guess wearing the forecaster's words.

Nothing here reads the database. ``apps.routes.services.route_bulletin``
is what resolves regions and bulletins and calls this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The eight compass octants, in the order a bearing walks them from north.
# The CAAML enum's own spellings (``sample_data/openapi.json``), so a
# matched aspect is the string the bulletin used and never a translation
# of it.
OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Degrees of bearing each octant spans. 360 / 8.
_OCTANT_DEG = 45.0


@dataclass(frozen=True)
class SegmentFacts:
    """One stretch of track, reduced to what a bulletin can be asked about.

    ``aspect_deg`` and ``length_m`` come from the terrain record;
    ``elevation_m`` comes from the track's own elevation series. Any of
    the three may be None — a segment the terrain could not answer for
    has no aspect, and a GPX with no ``<ele>`` has no height — and a None
    is never guessed at.
    """

    aspect_deg: float | None
    elevation_m: float | None
    length_m: float


@dataclass
class ProblemOverlap:
    """The stretch of one route that falls inside one problem's geography.

    ``length_m`` is what the reader is being told about: not "you are
    exposed", but "this much of your day is inside the ground the
    forecaster described". ``aspects`` and the elevation pair are the
    track's own, not the problem's — the reader already has the problem's
    from the bulletin, and repeating them back would say nothing about
    their route.
    """

    problem_type: str
    danger_rating_value: str | None
    length_m: float = 0.0
    aspects: set[str] = field(default_factory=set)
    lowest_m: float | None = None
    highest_m: float | None = None
    # True when the problem's band is bounded by the treeline, so the
    # elevation half of the match could not be decided. Reported, never
    # silently dropped.
    elevation_undecided: bool = False


def octant_for(aspect_deg: float | None) -> str | None:
    """Return the compass octant a bearing falls in.

    Args:
        aspect_deg: A compass bearing in degrees, or None.

    Returns:
        One of ``OCTANTS``, or None when there is no bearing. The bands
        are centred on their own name — N is 337.5 to 22.5 — because that
        is what a bulletin means by "north facing", rather than the
        22.5-degree-offset reading that would put due north on a boundary.

    """
    if aspect_deg is None:
        return None
    index = int(((aspect_deg % 360.0) + _OCTANT_DEG / 2) // _OCTANT_DEG) % len(OCTANTS)
    return OCTANTS[index]


def elevation_matches(
    elevation: dict[str, Any] | None, metres: float | None
) -> bool | None:
    """Return whether a height falls inside a problem's elevation band.

    Args:
        elevation: The problem's parsed ``elevation`` — ``lower``,
            ``upper``, ``treeline`` and ``treeline_side`` — or None when
            the problem states no elevation constraint at all.
        metres: The track's own height at that point, or None.

    Returns:
        True or False where the question can be answered, and **None
        where it cannot**: a band bounded by the treeline (we hold no
        treeline model) or a track with no elevation series. None is not
        a no — the caller reports it as undecided rather than dropping
        the problem, because a reader who is told nothing about a band
        will assume it did not apply.

    """
    if not elevation:
        # No constraint stated: the problem applies at every height, which
        # is an answer rather than an absence.
        return True

    lower = elevation.get("lower")
    upper = elevation.get("upper")
    if elevation.get("treeline") and lower is None and upper is None:
        return None
    if metres is None:
        return None

    if lower is not None and metres < lower:
        return False
    return not (upper is not None and metres > upper)


def overlaps(
    segments: list[SegmentFacts], problems: list[dict[str, Any]]
) -> list[ProblemOverlap]:
    """Return, per problem, the stretch of this route that falls inside it.

    A segment counts towards a problem when BOTH halves of the problem's
    own geography admit it: its aspect is one the problem names (or the
    problem names none, which means all of them), and its height is
    inside the problem's band.

    **A SEGMENT WITH NO ASPECT IS NOT MATCHED AND NOT COUNTED AGAINST
    ANYTHING.** Ground the terrain could not answer for is ground nothing
    looked at; attributing it to a problem would be inventing exposure,
    and attributing it to safety would be worse. It is simply absent, and
    the surface saying so is the route's own "not surveyed" figure
    (SNOW-961).

    Args:
        segments: The track, one entry per sampled segment.
        problems: The render model's problems for the day, each carrying
            ``problem_type``, ``aspects`` and ``elevation``.

    Returns:
        One ``ProblemOverlap`` per problem the route actually meets, in
        the order the problems were given. A problem the route never
        enters produces NO entry — the bulletin page is where every
        problem is listed, including the ones that do not apply here, and
        this answers only "which of them does my line cross".

    """
    found: list[ProblemOverlap] = []
    for problem in problems:
        overlap = _overlap_for(segments, problem)
        if overlap is not None:
            found.append(overlap)
    return found


def _overlap_for(
    segments: list[SegmentFacts], problem: dict[str, Any]
) -> ProblemOverlap | None:
    """Return one problem's overlap with a track, or None if it has none.

    Args:
        segments: The track, one entry per sampled segment.
        problem: One render-model problem.

    Returns:
        The overlap, or None when no segment falls inside it.

    """
    problem_aspects = set(problem.get("aspects") or [])
    elevation = problem.get("elevation") or None

    overlap = ProblemOverlap(
        problem_type=str(problem.get("problem_type") or ""),
        danger_rating_value=problem.get("danger_rating_value"),
    )
    matched = False
    for segment in segments:
        octant = octant_for(segment.aspect_deg)
        if octant is None:
            continue
        # An empty aspect list is the CAAML way of saying "every aspect",
        # which is common on wet-snow problems — see the render model's
        # note on prose-only geography.
        if problem_aspects and octant not in problem_aspects:
            continue

        decided = elevation_matches(elevation, segment.elevation_m)
        if decided is False:
            continue
        if decided is None:
            overlap.elevation_undecided = True

        matched = True
        overlap.length_m += segment.length_m
        overlap.aspects.add(octant)
        _widen(overlap, segment.elevation_m)

    return overlap if matched else None


def _widen(overlap: ProblemOverlap, metres: float | None) -> None:
    """Stretch an overlap's height range to include one more segment.

    Args:
        overlap: The overlap so far. Mutated.
        metres: The segment's height, or None for a track with no
            elevation series — which widens nothing rather than being
            treated as sea level.

    """
    if metres is None:
        return
    if overlap.lowest_m is None or metres < overlap.lowest_m:
        overlap.lowest_m = metres
    if overlap.highest_m is None or metres > overlap.highest_m:
        overlap.highest_m = metres
