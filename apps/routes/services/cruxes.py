"""
apps/routes/services/cruxes.py — the passages where the ground above can go.

SNOW-911. A crux is a stretch of track where the terrain AROUND the skier
is capable of releasing a slab they could trigger — which is a different
question from "how steep is the ground I am standing on", and one the
per-segment colouring (SNOW-910) structurally cannot answer. A rising
traverse along the foot of a 40 degree face is gentle underfoot and is
exactly the passage a marker is for.

**STATIC, AND THAT IS THE POINT.** Nothing here reads a bulletin. A
regional forecast is highly generalised, and applied to one slope it can
be a danger level or more off the mark — so at *low* and *moderate* a
risk calculation paints genuinely touchy spots green. A marker derived
from the ground alone survives the forecast being wrong about one slope,
which a marker derived from the forecast cannot. Skitourenguru draw
theirs grey for the same reason (`Schlüsselstellen`), and SNOW-839's
bulletin join is deliberately a separate register on the same line.

## What this builds, and what it does not

Skitourenguru's ATHM delineates the surrounding slope (it ends at the
next terrain fold), takes statistics over that whole area, and classes it
by slope angle, slope size, plan curvature and forest cover. That is a
project. This is the floor of it: **the maximum slope angle within an
uphill arc of each sample point**. It catches the case above, which is
the main thing a marker is for, and it is honest about being a floor —
`/help/#help-topic-slope` says so in as many words.

Slope size and plan curvature are what make their output good, and each
is a later ticket that can be judged against a marker set that already
exists. Building all four before anything ships would mean the first
thing a user sees is also the first thing anyone has evaluated.

## The probe geometry, and why it is shaped this way

**Uphill comes from the aspect we already store.** A segment's
`aspect_deg` is the direction the ground FACES, which is downhill, so
uphill is its opposite. Searching an arc centred there rather than a full
circle is what keeps a track along the TOP of a face from being marked by
the steep ground below it — ground it cannot be released onto.

Where the aspect is unknown the ground is flat enough to have no facing,
and there is no uphill to search. Those probe the four cardinals at the
outer radius instead: a flat bench under a face is a real crux, and
guessing a direction for it would be worse than looking four ways once.

The radii bracket what a skier commits to — near enough that a release
reaches them, far enough to see over a roll. They are a decision, not a
derivation, and the module is the place that says so.

## Cost

Every probe is a `sample_slope`, and each of those reads nine cells. The
probes sit within ~100 m of a track whose tiles the walk has already
fetched, and `_fetch_tile` is memoised for the life of the process, so
the cost is CPU rather than a second pass over the tile origin — which
`docs/decisions/a-slope-segment-is-the-shared-record.md` rejects.

## Honesty

**The markers are not exhaustive, and a route with none is not a safe
route.** That is the same failure as an unshaded raster reading as "no
steep terrain here", and it is stated on the surface rather than only
here: the legend row links to the help topic, and the topic says to
cross-check against the slope overlay. Duty of care resolves as it does
everywhere else in this codebase — highlight, never suppress; this marks
ground, it does not issue a verdict about a day.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.core.geo import destination
from apps.locations.services.terrain import TerrainUnknown, sample_slope

# The angle at and above which surrounding ground makes a passage a crux.
#
# Thirty-five degrees, and deliberately NOT the 30 the line colouring
# counts from. Thirty is where slab release becomes possible and is the
# right threshold for "how much of this day is steep", which is a figure.
# A marker is an instruction to look, and one placed on every 30 degree
# roll in the Alps would be on most of every track — a marker that is
# always on says nothing. Thirty-five is where the large majority of
# skier-triggered slabs release, which is the ground worth a ring.
CRUX_THRESHOLD_DEG = 35.0

# How far from the track to look, in metres.
#
# Forty and eighty. The near radius is about the length of ground a
# release would reach from directly; the far one sees over a convex roll
# that hides the steep part of a face from the track itself. Beyond that
# the ground stops being terrain the skier is committed to and starts
# being scenery.
PROBE_RADII_M = (40.0, 80.0)

# Bearings to search, as offsets from straight uphill.
#
# A face is rarely square to the track, and the steepest part of it is
# rarely straight up the local aspect — but a half-circle would reach
# around to ground on the other side of a ridge. Forty-five degrees each
# way is a quadrant centred uphill.
PROBE_BEARINGS_DEG = (-45.0, 0.0, 45.0)

# What to look at when the ground has no facing at all.
#
# A flat bench beneath a face is a real crux and has no uphill to search,
# so it looks four ways at the outer radius only — eight probes' worth of
# coverage for four probes' cost, on the segments least likely to need
# it.
FLAT_PROBE_BEARINGS_DEG = (0.0, 90.0, 180.0, 270.0)


@dataclass(frozen=True)
class UphillProbe:
    """What the arc search found, and whether it could look at all.

    ``unavailable`` is the distinction the whole retry story rests on. An
    ``OUTSIDE_COVERAGE`` probe is a fact about the GROUND — there is no
    survey there and there never will be on this tileset — so a record
    written over it is complete and final. ``UNAVAILABLE`` is a fact
    about US: the tile origin could not be read, and the same probe will
    answer next time. Collapsing the two would let a transient outage be
    stored as "nothing was flagged", which
    ``apps/locations/services/terrain.py`` refuses one layer down for the
    same reason and in the same words.
    """

    steepest_deg: float | None
    unavailable: bool


def uphill_max_angle(
    latitude: float,
    longitude: float,
    aspect_deg: float | None,
    window_m: float | None = None,
) -> UphillProbe:
    """Return the steepest ground found uphill of one point.

    Args:
        latitude: Latitude of the sample point, in degrees.
        longitude: Longitude of the sample point, in degrees.
        aspect_deg: The direction the ground at that point FACES, or None
            when it is flat enough to have none.
        window_m: Passed through to ``sample_slope`` so a probe is
            measured at the same spacing as the track sample it belongs
            to. Defaults to None, meaning the grid's own default.

    Returns:
        An ``UphillProbe``. ``steepest_deg`` is None when no probe could
        be answered — **"we did not see", never "nothing steep"** — and
        ``unavailable`` says whether that was our fault rather than the
        survey's.

    """
    bearings: tuple[float, ...]
    radii: tuple[float, ...]
    if aspect_deg is None:
        bearings = FLAT_PROBE_BEARINGS_DEG
        radii = (PROBE_RADII_M[-1],)
    else:
        # The aspect faces downhill, so uphill is its opposite.
        uphill = (aspect_deg + 180.0) % 360.0
        bearings = tuple((uphill + offset) % 360.0 for offset in PROBE_BEARINGS_DEG)
        radii = PROBE_RADII_M

    steepest: float | None = None
    unavailable = False
    for radius_m in radii:
        for bearing_deg in bearings:
            probe_lat, probe_lon = destination(
                latitude, longitude, bearing_deg, radius_m
            )
            probe = sample_slope(probe_lat, probe_lon, window_m)
            if probe.unknown == TerrainUnknown.UNAVAILABLE:
                unavailable = True
                continue
            if probe.angle_deg is None:
                continue
            if steepest is None or probe.angle_deg > steepest:
                steepest = probe.angle_deg
    return UphillProbe(steepest_deg=steepest, unavailable=unavailable)


def is_crux(angle_deg: float | None, uphill_deg: float | None) -> bool:
    """Return whether a segment is a crux.

    The maximum of the two, against the threshold: ground underfoot
    counts as much as ground above, because a skier standing on a 38
    degree slope is on the thing that can release. The arc search is what
    ADDS the case the angle alone misses, not what replaces it.

    Args:
        angle_deg: The segment's own slope angle, or None if unknown.
        uphill_deg: The steepest angle found uphill, or None if none was.

    Returns:
        True when either is at or above ``CRUX_THRESHOLD_DEG``. False when
        both are unknown — an unmarked segment is not a claim that the
        ground is gentle, which is what the help topic and the legend
        exist to say.

    """
    return any(
        value is not None and value >= CRUX_THRESHOLD_DEG
        for value in (angle_deg, uphill_deg)
    )


def crux_points(
    points: list[list[float]], segments: list[dict[str, Any]]
) -> list[list[float]]:
    """Group flagged segments into one marker each.

    **A RUN OF FLAGGED SEGMENTS IS ONE CRUX, NOT TWENTY.** A traverse
    under a face is marked segment by segment at a 25 m stride, and
    drawing a ring on each would bury the track under its own markers and
    tell the reader that twenty separate things need their attention.
    Consecutive flagged segments are one passage, marked once, at its
    middle — the same reasoning that puts a segment's sample at its
    midpoint rather than at an end.

    Args:
        points: The record's ``points`` — N + 1 boundary coordinates as
            ``[longitude, latitude]``.
        segments: The record's ``segments``, N of them, some carrying
            ``crux``.

    Returns:
        One ``[longitude, latitude]`` per run of flagged segments, in
        track order. Empty when nothing is flagged, and empty when the
        two arguments do not pair up — a marker placed against the wrong
        geometry is worse than no marker.

    """
    if len(points) != len(segments) + 1:
        return []

    markers: list[list[float]] = []
    run_start: int | None = None
    for index, segment in enumerate(segments):
        if segment.get("crux"):
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            markers.append(_run_midpoint(points, run_start, index - 1))
            run_start = None
    if run_start is not None:
        markers.append(_run_midpoint(points, run_start, len(segments) - 1))
    return markers


def _run_midpoint(points: list[list[float]], first: int, last: int) -> list[float]:
    """Return the coordinate at the middle of a run of segments.

    The run spans ``points[first]`` to ``points[last + 1]``, so its middle
    is the boundary halfway between those two. An odd-length run lands on
    a boundary exactly; an even-length one takes the boundary just before
    the middle rather than interpolating, because a marker is an arrow at
    a passage and not a measurement of it.

    Args:
        points: The record's boundary coordinates.
        first: Index of the first flagged segment in the run.
        last: Index of the last.

    Returns:
        One ``[longitude, latitude]``.

    """
    return points[(first + last + 1) // 2]
