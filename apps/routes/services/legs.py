"""
apps/routes/services/legs.py — cutting a route into legs at its transitions.

SNOW-990. A leg is the stretch between two transitions, so it is one
activity throughout: skinning, or skiing, or bootpacking. What varies
inside a leg is the track and the terrain, which is the next question and
not this module's.

Derived from ``Route.points`` alone. No DEM, no network, no stored column —
a pure function of geometry the row already holds, so it works offline and
costs microseconds on a track of a few hundred points. Nothing here is
cached, on the same reasoning ``apps.routes.services.passages`` gives: a
stored answer would bake today's constants into the row and take away the
freedom to re-tune them.

## THE WINDOW IS IN METRES, AND THAT IS THE WHOLE POINT

The obvious implementation smooths elevation over a window of N POINTS. That
is wrong in a way which is invisible until you hold two recordings side by
side, because a point is not a distance — it is one position fix, at
whatever interval the recording device chose. Across the four canonical
tracks a point is every 3.8 m to every 18.6 m on average, so a ten-point
window spans about 76 m of ground on one and 372 m on another (spacing is
uneven within a track, so the medians are lower: 51 m and 347 m). **The same
setting smooths away a roll on one track that it preserves on another**, and
the same terrain then yields different legs depending on what recorded it.

Measured on the corpus, thinning each track to 1/2, 1/3, 1/4 and 1/6 of its
points — the same route as a coarser recording of itself. The points window
compared against is ten points either side. A boundary's movement is its
along-track distance on the thinned track against the full one, so it
includes the distance thinning cuts off corners as well as any real shift:

* a metres window held the leg COUNT on all four tracks at every level;
  a points window broke it, turning Col de la Chaux's seven legs into
  eleven at 1/6;
* interior boundaries moved less under a metres window in **every one of the
  fifteen** comparisons where a points window still found the same NUMBER of
  legs, typically by half or more. The sixteenth is the Col de la Chaux case
  above, where a points window found no comparable boundaries because it had
  found four extra legs. (Hidden Valley at 1/6 is one of the fifteen on
  count alone: its points window finds four legs, but the second no longer
  gains height overall (+111 m on the full track, -44 m at 1/6), so the
  legs' ``climbing`` flags no longer alternate. Requiring the order too
  leaves fourteen, and metres still wins all of them.)

**What it does NOT do is make boundaries invariant**, and the figures say
so plainly: Hidden Valley's boundaries still move up to ~600 m at 1/6
on the measure above. Mapped back onto the full recording, which leaves
out what thinning cut from the corners, the same boundaries move 74 m.
Three reasons, all limits of the method rather than bugs. Thinning shortens
the track across every corner it cuts, which the measure above counts as
movement; the hysteresis below measures an excursion between two SAMPLED
points, so thinning changes which points those are; and a window cannot
smooth over 100 m of ground when the points are 79 m apart, which is what
1/6 of Hidden Valley is.
A metres window is the right unit, not a guarantee.

## The constants

Both were swept across the corpus rather than chosen. ``SMOOTHING_WINDOW_M``
sits in a plateau — every value from 75 m to 400 m gives the same leg count
on all four tracks; at 50 m Hidden Valley gains two extra legs, and at
500 m Col de la Chaux gains two. ``MIN_LEG_ASCENT_M`` is flat from 12 m to
50 m; at 10 m Hidden Valley splits again, and at 75 m it loses a leg.

Neither sits in the middle of its plateau, though an earlier version of
this paragraph said both did. 100 m is 25 m above the window's lower edge
and 15 m is 3 m above the threshold's, so both are the least smoothing that
holds rather than the most central. A track added to the corpus is more
likely to move the answer at the low edge than the high one. Re-measured
2026-09-23 over the four canonical tracks.

They are keyword arguments, which is the mechanical proof that re-tuning
costs nothing.

## Sub-threshold runs are MERGED, never dropped

The version this was prototyped from discarded them, and on one route the
survivors happened to be contiguous so nothing was visibly lost. That was
luck. Dropping a run loses its distance and its vertical, so the legs stop
summing to the route. Merging conserves both, and
``tests/routes/test_legs.py`` asserts the sum against the track's own
length on every canonical track.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from apps.core.geo import haversine_m

logger = logging.getLogger(__name__)

# Half-width of the elevation smoothing window, in metres of track either
# side of each point. See the module docstring for the sweep.
SMOOTHING_WINDOW_M = 100.0

# Smallest vertical excursion a run must have to survive as its own leg.
# Below it the run is merged into a neighbour.
MIN_LEG_ASCENT_M = 15.0

# The sixteen compass points, in the order a bearing walks them from north.
_COMPASS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


@dataclass(frozen=True)
class Leg:
    """One stretch of a route between two transitions.

    Attributes:
        index: Position in the route, from 1.
        start: Index into ``points`` of the leg's first coordinate.
        end: Index of its last, inclusive. Adjacent legs SHARE this
            boundary — leg n's ``end`` is leg n+1's ``start`` — which is
            what makes the distances sum to the track rather than to the
            track minus one point per join.
        climbing: Whether the leg gains height overall. The activity is
            an inference from this and is deliberately not made here:
            an up-leg is skinning or bootpacking, and nothing in
            ``points`` separates them.
        elevation_start: Metres at the first coordinate.
        elevation_end: Metres at the last.
        net_m: Signed height change, positive for a climb.
        ascent_m: Total climbing inside the leg, as a positive magnitude.
        descent_m: Total dropping inside it, likewise. On a monotonic leg
            one of the two equals ``abs(net_m)`` and the other is near
            zero; they differ on a leg that undulates without a transition.
        distance_m: Along-track length.
        bearing_deg: Compass bearing from the leg's first coordinate to
            its last. A straight-line summary, not a heading — a leg that
            doubles back has a bearing that describes neither half.
        compass: ``bearing_deg`` as one of sixteen points.
        track_angle_deg: ``atan(|net| / distance)``. The angle of the
            TRACK, which on a zigzagging skin track is far gentler than
            the ground it crosses. Never read this as terrain steepness;
            that comes from ``Route.slope_samples`` and nowhere else.

    """

    index: int
    start: int
    end: int
    climbing: bool
    elevation_start: float
    elevation_end: float
    net_m: float
    ascent_m: float
    descent_m: float
    distance_m: float
    bearing_deg: float
    compass: str
    track_angle_deg: float


def _cumulative_distances(points: list[list[float | None]]) -> list[float]:
    """Return along-track distance to each point, starting at zero.

    Args:
        points: ``[lon, lat, ele]`` coordinates in stored order.

    Returns:
        One cumulative distance per point, in metres.

    """
    cumulative = [0.0]
    for index in range(1, len(points)):
        previous, current = points[index - 1], points[index]
        cumulative.append(
            cumulative[-1]
            + haversine_m(
                float(previous[1]),  # type: ignore[arg-type]
                float(previous[0]),  # type: ignore[arg-type]
                float(current[1]),  # type: ignore[arg-type]
                float(current[0]),  # type: ignore[arg-type]
            )
        )
    return cumulative


def _smooth(
    elevations: list[float], cumulative: list[float], window_m: float
) -> list[float]:
    """Average each elevation over every point within ``window_m`` of it.

    A two-pointer walk rather than a slice per point, so the cost is linear
    in the number of points rather than quadratic — which matters because
    the window covers many more points on a densely recorded track, and
    that is exactly the track where the naive version would be slowest.

    Args:
        elevations: Metres per point.
        cumulative: Along-track distance per point.
        window_m: Half-width, in metres of track either side.

    Returns:
        One smoothed elevation per point.

    """
    smoothed = []
    low = high = 0
    running = 0.0
    for index in range(len(elevations)):
        while cumulative[index] - cumulative[low] > window_m:
            running -= elevations[low]
            low += 1
        while (
            high < len(elevations) and cumulative[high] - cumulative[index] <= window_m
        ):
            running += elevations[high]
            high += 1
        smoothed.append(running / (high - low))
    return smoothed


def _runs(smoothed: list[float]) -> list[list[int]]:
    """Group points into runs of constant climbing/descending sign.

    Args:
        smoothed: The smoothed elevation series.

    Returns:
        ``[start, end, sign]`` triples, boundaries shared between
        neighbours.

    """
    runs: list[list[int]] = []
    sign = 0
    start = 0
    for index in range(1, len(smoothed)):
        delta = smoothed[index] - smoothed[index - 1]
        step = 1 if delta > 0 else (-1 if delta < 0 else 0)
        if step == 0:
            continue
        if sign == 0:
            sign = step
        elif step != sign:
            runs.append([start, index - 1, sign])
            start, sign = index - 1, step
    runs.append([start, len(smoothed) - 1, sign or 1])
    return runs


def _excursion(run: list[int], elevations: list[float]) -> float:
    """Return the raw height change across one run.

    Raw and not smoothed: the threshold is a claim about the ground, so
    smoothing it twice would compare a run against a version of itself.

    Args:
        run: A ``[start, end, sign]`` triple.
        elevations: Metres per point.

    Returns:
        Metres, as a positive magnitude.

    """
    return abs(elevations[run[1]] - elevations[run[0]])


def _smallest_offender(
    runs: list[list[int]], elevations: list[float], threshold_m: float
) -> int | None:
    """Return the position of the smallest sub-threshold run, if any.

    Smallest first, so one merge never strands a smaller offender behind
    it — the order matters because merging changes its neighbours' sizes.

    Args:
        runs: The current runs.
        elevations: Metres per point.
        threshold_m: Smallest excursion that survives alone.

    Returns:
        A position, or None when every run clears the threshold.

    """
    offender = None
    smallest = None
    for position, run in enumerate(runs):
        size = _excursion(run, elevations)
        if size < threshold_m and (smallest is None or size < smallest):
            smallest, offender = size, position
    return offender


def _merge_target(runs: list[list[int]], offender: int, elevations: list[float]) -> int:
    """Return which neighbour a sub-threshold run should join.

    The larger neighbour, so a bump between two real legs joins whichever
    it is more plausibly part of. At either end there is only one choice.

    Args:
        runs: The current runs.
        offender: Position of the run being merged away.
        elevations: Metres per point.

    Returns:
        The neighbour's position.

    """
    if offender == 0:
        return 1
    if offender == len(runs) - 1:
        return len(runs) - 2
    before = _excursion(runs[offender - 1], elevations)
    after = _excursion(runs[offender + 1], elevations)
    return offender - 1 if before >= after else offender + 1


def _coalesce(runs: list[list[int]]) -> list[list[int]]:
    """Join any neighbouring runs that now share a sign.

    A merge can leave two adjacent climbs, which is not two legs.

    Args:
        runs: The current runs.

    Returns:
        The runs, with same-sign neighbours joined.

    """
    position = 0
    while position < len(runs) - 1:
        if runs[position][2] == runs[position + 1][2]:
            runs[position] = [
                runs[position][0],
                runs[position + 1][1],
                runs[position][2],
            ]
            runs.pop(position + 1)
        else:
            position += 1
    return runs


def _merge_short_runs(
    runs: list[list[int]], elevations: list[float], threshold_m: float
) -> list[list[int]]:
    """Fold every run below ``threshold_m`` of excursion into a neighbour.

    MERGED, never dropped. Discarding a run loses its distance and its
    vertical, so the legs stop summing to the route; the prototype this
    came from did exactly that and passed only because one route's
    survivors happened to be contiguous.

    Args:
        runs: ``[start, end, sign]`` triples.
        elevations: Raw metres per point.
        threshold_m: Smallest excursion that survives alone.

    Returns:
        The surviving runs, covering exactly the same points.

    """
    runs = [run[:] for run in runs]

    while len(runs) > 1:
        offender = _smallest_offender(runs, elevations, threshold_m)
        if offender is None:
            break

        target = _merge_target(runs, offender, elevations)
        low = min(runs[offender][0], runs[target][0])
        high = max(runs[offender][1], runs[target][1])
        merged = [low, high, 1 if elevations[high] >= elevations[low] else -1]

        for position in sorted({offender, target}, reverse=True):
            runs.pop(position)
        runs.insert(min(offender, target), merged)
        runs = _coalesce(runs)
    return runs


def _bearing(start: list[float | None], end: list[float | None]) -> float:
    """Return the initial great-circle bearing from one point to another.

    Args:
        start: ``[lon, lat, ele]`` of the first point.
        end: ``[lon, lat, ele]`` of the second.

    Returns:
        Degrees clockwise from north, 0–360.

    """
    lat_1 = math.radians(float(start[1]))  # type: ignore[arg-type]
    lat_2 = math.radians(float(end[1]))  # type: ignore[arg-type]
    delta = math.radians(float(end[0]) - float(start[0]))  # type: ignore[arg-type]
    y = math.sin(delta) * math.cos(lat_2)
    x = math.cos(lat_1) * math.sin(lat_2) - math.sin(lat_1) * math.cos(
        lat_2
    ) * math.cos(delta)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _leg(
    points: list[list[float | None]],
    cumulative: list[float],
    elevations: list[float],
    run: list[int],
    index: int,
) -> Leg:
    """Measure one run into a ``Leg``.

    Args:
        points: The route's coordinates.
        cumulative: Along-track distance per point.
        elevations: Metres per point.
        run: The ``[start, end, sign]`` triple to measure.
        index: The leg's position in the route, from 1.

    Returns:
        The measured leg.

    """
    start, end = run[0], run[1]
    distance = cumulative[end] - cumulative[start]
    net = elevations[end] - elevations[start]

    ascent = descent = 0.0
    for position in range(start + 1, end + 1):
        step = elevations[position] - elevations[position - 1]
        if step > 0:
            ascent += step
        else:
            descent -= step

    bearing = _bearing(points[start], points[end])
    return Leg(
        index=index,
        start=start,
        end=end,
        climbing=net > 0,
        elevation_start=elevations[start],
        elevation_end=elevations[end],
        net_m=net,
        ascent_m=ascent,
        descent_m=descent,
        distance_m=distance,
        bearing_deg=bearing,
        compass=_COMPASS[int((bearing + 11.25) % 360 // 22.5)],
        track_angle_deg=(
            math.degrees(math.atan2(abs(net), distance)) if distance else 0.0
        ),
    )


def detect_legs(
    points: list[list[float | None]],
    *,
    window_m: float = SMOOTHING_WINDOW_M,
    threshold_m: float = MIN_LEG_ASCENT_M,
) -> list[Leg]:
    """Cut a route into its legs.

    Args:
        points: ``Route.points`` — ``[lon, lat, ele]`` in stored order.
        window_m: Half-width of the elevation smoothing window, in metres
            of track either side of each point.
        threshold_m: Smallest vertical excursion that survives as its own
            leg. Smaller runs merge into a neighbour.

    Returns:
        The legs, in order, sharing their boundary points so the distances
        sum to the track's own length. Empty when the track is too short
        to have a leg, or carries no elevation — a leg is a change in
        height, and a track without heights supports no claim about one.

    """
    if len(points) < 2:
        return []
    if any(point[2] is None for point in points):
        logger.debug(
            "Leg detection skipped: %d points, some without elevation", len(points)
        )
        return []

    elevations = [float(point[2]) for point in points]  # type: ignore[arg-type]
    cumulative = _cumulative_distances(points)
    smoothed = _smooth(elevations, cumulative, window_m)
    runs = _merge_short_runs(_runs(smoothed), elevations, threshold_m)

    return [
        _leg(points, cumulative, elevations, run, index)
        for index, run in enumerate(runs, start=1)
    ]
