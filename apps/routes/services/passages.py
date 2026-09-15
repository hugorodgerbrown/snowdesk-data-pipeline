"""
apps/routes/services/passages.py — the stretches where the track is no-fall.

SNOW-964, and the THIRD thing a saved route says about terrain. SNOW-910
colours each 25 m segment by the steepness of the ground under it;
SNOW-911 rings the passages where the ground AROUND the skier can release.
Neither says anything about the relationship between the terrain and the
TRACK — so a skin track zigzagging up a 38 degree face and the ski line
straight down it get identical treatment.

A no-fall passage is a run of segments on ground at or above
``NO_FALL_GATE_DEG``, and the label on it says what the track DOES with
that ground: descends the fall line, climbs it, or crosses it. The
measurement is the angle between the segment's track bearing — the chord
from one boundary to the next — and its stored ``aspect_deg``, which is
the direction the ground FACES and therefore the direction downhill.

**THE GATE IS THE GROUND, THE LABEL IS THE TRACK, AND THE LABEL DOES NOT
FILTER.** A rising traverse of 50 degree ground is no-fall terrain too,
and marking only the descents would make the traverse the silent case —
which is the "the map said it was fine" failure the whole slope feature
exists to prevent. Every qualifying passage is returned; the alignment is
carried as a word beside it.

## Derived at read time, and nothing new is stored

Both inputs are already in ``Route.slope_samples``, so a stored
``fall_line_deg`` would cache a pure function of the same row. Worse, it
would manufacture a backfill candidate: ``backfill_route_slope_samples``
selects on a missing key, so re-tuning a threshold would cost a full
re-walk of the tile origin — which
``docs/decisions/a-slope-segment-is-the-shared-record.md`` rejects. The
read costs almost nothing because the grouping SEEDS on ``angle_deg >=
gate_deg`` first: the trigonometry runs only over marked ground, which is
no segments at all on most routes.

**PASSAGES MUST NEVER ENTER ``summary``.** That key is written by the
sampler and read back verbatim, so putting a passage list in it would
bake today's gate into the stored record and take the tuning freedom
above away again.

## A passage list is never incomplete

This is the thing ``cruxes`` cannot say. A crux can be ABSENT because a
probe outage voided the pass, which is why that key is omitted rather
than emptied. A passage needs no probe: it is fully derivable from the
record at any constants, at any time. So an empty list here means
"nothing qualified" and never "we could not look", and there is no third
state to encode.

## Where the lengths come from, and the trap in it

**LENGTHS COME FROM THE RECORD'S OWN ``stride_m``, NEVER FROM CHORDS.**
A chord between two boundary coordinates is SHORTER than the 25 m of
track it stands for — a switchback is the extreme case, and
``apps.routes.services.slope_summary``'s "two length sources" docstring
is where that is argued. Measuring a passage by chords and then testing
it against a 25 m minimum would silently drop most single-segment
passages, and a single segment is the COMMON case: one steep roll on an
otherwise moderate face. The track's last segment is the exception and
gets its chord, because ``stride_distances`` absorbs the trailing stub
into it and it can be anything between half and one and a half strides.

## An unknown segment is never inside a passage

An unknown segment carries no ``angle_deg`` at all, so it fails both the
seed test and the grow test and always stops a run. That is deliberate
and it is why ``routes-slope-unknown`` needs no change: no feature can
ever carry both ``unknown`` and ``passage``. Ground nothing surveyed is
not ground we may mark.

## Honesty

**AN UNMARKED ROUTE IS NOT A ROUTE WITHOUT NO-FALL GROUND.** The gate is
a threshold on a sampled angle taken from a 10 m analysis window over a
5 m grid, and a narrow steep passage between two gentler samples reads
gentler than it is. The surfaces say so — the legend row and
``/help/#help-topic-slope`` — and this module is where the reason lives.
"""

from __future__ import annotations

from typing import Any

from apps.core.geo import haversine_m, initial_bearing_deg
from apps.routes.services.slope_summary import segment_lengths_from_points

# The angle at and above which the ground under the track is no-fall.
#
# Fifty degrees, and deliberately the SAME number as the top band of the
# colour scale (``slope-50``) rather than a threshold of its own. The
# reader already has a colour for that ground; this mark says the track
# is ON it, and a second, nearby number would make the two marks argue
# about where the steep part starts. Lowering it would put the mark on
# the 45-50 band, which on an Alpine ski tour is common enough that a
# mark on it says nothing; raising it would leave the top band — the one
# the scale already paints near-black — unmarked.
NO_FALL_GATE_DEG = 50.0

# How far from the fall line a track may point and still be called a
# descent or a climb, in degrees.
#
# Thirty. The label is a reading aid, not a measurement, and it has to
# survive a bearing taken from a single 25 m chord of a track a GPS
# recorded — which wanders by more than a few degrees on its own. Thirty
# either side means a descent is anything within a 60 degree cone of
# straight down; tightening it would send genuine ski lines into
# ``crossing``, which is the label that claims least and so the one a
# mistake should land in.
FALL_LINE_TOLERANCE_DEG = 30.0

# The angle a passage keeps growing outward through, in degrees.
#
# Forty-five: the band immediately below the gate. A face is not a step
# function and a sampled angle is an average over a 10 m window, so the
# segments either side of a 50 degree roll are usually in the high
# forties and are part of the same committing passage. Growing through
# them is what stops one continuous steep face being reported as three
# passages with two gaps. Dropping it to the gate would do exactly that;
# lowering it much further would let a passage run out across a whole
# moderate slope.
PASSAGE_GROW_FLOOR_DEG = 45.0

# The shortest run of track reported as a passage, in metres.
#
# One stride. A single 25 m segment IS the common case — one steep roll
# between two gentler samples — so the minimum exists to drop nothing but
# the trailing stub, which ``stride_distances`` can leave as short as
# half a stride. Raising it to two strides would silently discard most of
# what this feature is for; see the module docstring on why measuring
# with chords amounts to the same mistake by accident.
PASSAGE_MIN_M = 25.0

# The three things a track can be doing with the ground it is on.
#
# Words rather than the raw angle, and that choice reaches the wire: a
# number beside a passage invites a future client to draw a barb on the
# line, which would be a claim about a direction measured from one 25 m
# chord. ``crossing`` is the residue and claims least, which is why a
# tie resolves to it.
DESCENDING = "descending"
CLIMBING = "climbing"
CROSSING = "crossing"

# Decimal places kept on a reported passage length. Metres to one place,
# matching ``slope_summary``'s stored figures so the two cannot disagree
# about a rounding.
_LENGTH_PRECISION = 1


def angular_difference(bearing_deg: float, aspect_deg: float) -> float:
    """Return the angle between two compass bearings, in degrees.

    The shortest way round, so the answer is always in ``[0, 180]`` and
    is symmetric in its arguments: 350 and 10 are 20 degrees apart, not
    340.

    Args:
        bearing_deg: One compass bearing, in degrees.
        aspect_deg: The other, in degrees.

    Returns:
        The separation in ``[0, 180]``.

    """
    delta = abs(bearing_deg - aspect_deg) % 360.0
    return 360.0 - delta if delta > 180.0 else delta


def fall_line_alignment(
    bearing_deg: float | None,
    aspect_deg: float | None,
    tolerance_deg: float = FALL_LINE_TOLERANCE_DEG,
) -> str | None:
    """Return what a track bearing is doing with the ground's fall line.

    The aspect is the direction the ground FACES, which is the direction
    water runs and a slab slides: downhill. So a track pointing the same
    way is descending the fall line and one pointing the opposite way is
    climbing it.

    Args:
        bearing_deg: The track's direction of travel, in degrees, or None
            for a chord between two coincident boundaries — which has no
            direction at all (``initial_bearing_deg``).
        aspect_deg: The direction the ground faces, in degrees, or None
            for exactly level ground, which faces nowhere.
        tolerance_deg: How far off the fall line still counts as with it
            or against it. Defaults to ``FALL_LINE_TOLERANCE_DEG``; it is
            an argument so the tuning command can sweep it without
            touching the constant.

    Returns:
        ``"descending"``, ``"climbing"`` or ``"crossing"``, or **None
        when either input is missing** — there is no fourth label for
        "we could not tell", because a segment that cannot be measured
        casts no vote rather than voting for a fourth thing.

    """
    if bearing_deg is None or aspect_deg is None:
        return None
    delta = angular_difference(bearing_deg, aspect_deg)
    if delta <= tolerance_deg:
        return DESCENDING
    if delta >= 180.0 - tolerance_deg:
        return CLIMBING
    return CROSSING


def route_passages(
    record: dict[str, Any] | None,
    *,
    gate_deg: float = NO_FALL_GATE_DEG,
    floor_deg: float = PASSAGE_GROW_FLOOR_DEG,
    min_m: float = PASSAGE_MIN_M,
    tolerance_deg: float = FALL_LINE_TOLERANCE_DEG,
) -> list[dict[str, Any]] | None:
    """Return the no-fall passages of one stored slope record.

    Takes the WHOLE record, mirroring ``summarise_record``, so it can read
    the walk's own ``stride_m`` itself rather than importing a constant
    from the sampler and hoping the two agree.

    **THE FOUR GATES ARE KEYWORD ARGUMENTS**, with the module constants as
    their defaults. That is the mechanical proof that re-tuning is free:
    ``report_route_passages`` sweeps candidate values over a record it has
    already loaded, with no further database access and nothing stored to
    invalidate.

    Args:
        record: A ``Route.slope_samples`` (or ``Trip.slope_samples``)
            value, or None for a track that has never been sampled.
        gate_deg: The angle a segment must reach to SEED a passage.
        floor_deg: The angle a passage keeps growing outward through. A
            value above ``gate_deg`` is clamped down to it, so a seed is
            always inside its own passage.
        min_m: The shortest passage reported.
        tolerance_deg: Passed to ``fall_line_alignment``.

    Returns:
        One dict per passage, in track order::

            {"from": 12, "to": 15, "m": 100.0, "fall_line": "descending"}

        ``from`` and ``to`` are INCLUSIVE indices into the record's
        ``segments`` — which is the same index space as the ``angles``
        array the client already holds, so the client draws the mark on
        geometry it already has rather than on a second copy that could
        disagree with the first.

        ``fall_line`` is ABSENT, never null, when nothing in the passage
        could be classified — the rule ``summary["steepest_deg"]``
        follows. The passage still exists: steep ground is what earns the
        mark, and a missing aspect is a fact about our survey.

        An empty list means nothing qualified, which is a complete answer
        (see the module docstring). None means there was nothing to read
        — never sampled, or a record whose halves do not pair up, which
        would mark segments against the wrong ground.

    """
    if not record:
        return None

    points = record.get("points") or []
    segments = record.get("segments") or []
    if len(points) != len(segments) + 1 or not segments:
        return None

    angles = [_angle_of(segment) for segment in segments]
    # A floor above the gate would leave a seed outside its own run, so
    # the two are ordered here rather than trusted. Only a sweep can
    # produce that pairing, and it should answer rather than misbehave.
    grow_floor = min(floor_deg, gate_deg)
    lengths_m = _segment_lengths(record, points)

    passages: list[dict[str, Any]] = []
    for first, last in _runs(angles, gate_deg, grow_floor):
        metres = sum(lengths_m[first : last + 1])
        if metres < min_m:
            continue
        passage: dict[str, Any] = {
            "from": first,
            "to": last,
            "m": round(metres, _LENGTH_PRECISION),
        }
        fall_line = _passage_alignment(
            points, segments, lengths_m, first, last, tolerance_deg
        )
        if fall_line is not None:
            passage["fall_line"] = fall_line
        passages.append(passage)
    return passages


def _angle_of(segment: dict[str, Any]) -> float | None:
    """Return one segment's slope angle, or None when it has none.

    Args:
        segment: One entry of the record's ``segments``.

    Returns:
        The angle in degrees, or None for an unknown segment — which is
        every segment carrying a reason instead of a number, and is what
        keeps unsurveyed ground out of every passage.

    """
    angle = segment.get("angle_deg")
    return float(angle) if isinstance(angle, int | float) else None


def _segment_lengths(record: dict[str, Any], points: list[list[float]]) -> list[float]:
    """Return the along-track length of each segment, in metres.

    The stride, not the chord — see the module docstring for the trap
    that avoids. The LAST segment is the one the stride does not state,
    because ``stride_distances`` absorbs the track's trailing stub into
    it; ``_final_length_m`` recovers it rather than measuring it.

    Args:
        record: The stored record, read for its ``stride_m``.
        points: The record's boundary coordinates, as ``[lon, lat]``.

    Returns:
        One length per segment, in track order.

    """
    stride_m = record.get("stride_m")
    if not isinstance(stride_m, int | float) or stride_m <= 0:
        # A record written before the sampler stored its stride has only
        # its coordinates left, so every length is the chord between two
        # of them — the read-time half of the pair.
        return segment_lengths_from_points(points)

    lengths = [float(stride_m)] * (len(points) - 1)
    if lengths:
        lengths[-1] = _final_length_m(record, points, float(stride_m), len(lengths))
    return lengths


def _final_length_m(
    record: dict[str, Any],
    points: list[list[float]],
    stride_m: float,
    count: int,
) -> float:
    """Return the along-track length of the track's LAST segment.

    **THE CHORD IS THE LAST RESORT HERE, NOT THE RULE.** Every other
    segment is exactly one stride, so the chord trap the module docstring
    describes was closed for all of them — but the final segment is the
    stub-absorbing one, and measuring THAT as a chord reopens the same
    trap on the one segment most likely to be a lone passage. It is worse
    there than elsewhere: ``stride_distances`` bounds it to between half
    and one and a half strides, so a genuine 37 m of track can chord to
    well under ``PASSAGE_MIN_M`` across a bend, and even a straight one
    loses a decimetre or so to the six-decimal rounding of the stored
    coordinates. A qualifying passage would vanish, and it would vanish
    silently.

    So the length is RECOVERED rather than measured. ``summary`` carries
    ``sampled_m``, the walk's own total, and every segment but this one
    is known to be exactly a stride — so the remainder is the sampler's
    own figure for it, arrived at without re-reading any geometry.

    Args:
        record: The stored record, read for its ``summary``.
        points: The record's boundary coordinates, as ``[lon, lat]``.
        stride_m: The record's stride, already validated by the caller.
        count: How many segments the record holds.

    Returns:
        The final segment's length in metres.

    """
    summary = record.get("summary")
    if isinstance(summary, dict):
        sampled_m = summary.get("sampled_m")
        if isinstance(sampled_m, int | float):
            remainder = float(sampled_m) - stride_m * (count - 1)
            # ``stride_distances`` bounds every segment to between half
            # and one and a half strides. A remainder outside that came
            # from a record whose summary and segments disagree about the
            # same walk, and the chord is the more trustworthy of two
            # figures that cannot both be right.
            if stride_m / 2.0 <= remainder <= stride_m * 1.5:
                return remainder

    # No summary to recover it from — a record predating SNOW-961 — so
    # the chord is all there is. It under-measures a bend, which is a
    # known and bounded loss on one segment of an old record.
    #
    # haversine_m takes latitude first (the house rule) and the record
    # stores GeoJSON axis order, so the pair is swapped here.
    return haversine_m(points[-2][1], points[-2][0], points[-1][1], points[-1][0])


def _runs(
    angles: list[float | None], gate_deg: float, floor_deg: float
) -> list[tuple[int, int]]:
    """Group the segments into maximal passages, seeded then grown.

    A run is a maximal stretch of segments at or above ``floor_deg`` that
    contains at least one segment at or above ``gate_deg``. Grouping it
    this way is what makes "grow outward from every seed, then merge the
    runs that meet" a single pass rather than an interval merge: two
    seeds in one stretch of steep ground are already in one run.

    Both comparisons are inclusive, matching ``band_for_angle`` — a
    sample of exactly 50.0 is in ``slope-50``, so it must be exactly as
    much of a seed as the colour under it says it is.

    Args:
        angles: One angle per segment, None for an unknown.
        gate_deg: The angle that seeds a run.
        floor_deg: The angle a run extends through.

    Returns:
        Inclusive ``(first, last)`` index pairs, in track order.

    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    seeded = False
    for index, angle in enumerate(angles):
        if angle is not None and angle >= floor_deg:
            if start is None:
                start = index
            seeded = seeded or angle >= gate_deg
            continue
        if start is not None and seeded:
            runs.append((start, index - 1))
        start = None
        seeded = False
    if start is not None and seeded:
        runs.append((start, len(angles) - 1))
    return runs


def _passage_alignment(
    points: list[list[float]],
    segments: list[dict[str, Any]],
    lengths_m: list[float],
    first: int,
    last: int,
    tolerance_deg: float,
) -> str | None:
    """Return one passage's alignment label, by a vote of its segments.

    **A VOTE, NOT A CIRCULAR MEAN.** A mean bearing misleads on a passage
    that crosses a col — two opposite headings average to a third that
    the track never took — and it cannot be reasoned about without
    circular arithmetic. Each segment votes with the ground it covers,
    and the most-covered label wins.

    **A TIE GOES TO ``crossing``**, which is the label that claims least.
    A passage genuinely split between climbing and descending is not a
    descent, and saying so would be the more confident of two readings on
    the evidence for neither.

    Args:
        points: The record's boundary coordinates, as ``[lon, lat]``.
        segments: The record's segments.
        lengths_m: Their along-track lengths.
        first: Index of the passage's first segment.
        last: Index of its last, inclusive.
        tolerance_deg: Passed to ``fall_line_alignment``.

    Returns:
        One of the three labels, or None when NOTHING in the passage
        could vote — every segment either has no aspect (exactly level
        ground faces nowhere) or no bearing (a chord between two
        coincident boundaries has no direction). The caller omits the key
        entirely on a None.

    """
    covered: dict[str, float] = {}
    for index in range(first, last + 1):
        aspect_deg = segments[index].get("aspect_deg")
        # (lat, lon), the house argument order — the record stores
        # GeoJSON axis order, so the pairs are swapped at the call.
        bearing_deg = initial_bearing_deg(
            points[index][1],
            points[index][0],
            points[index + 1][1],
            points[index + 1][0],
        )
        label = fall_line_alignment(
            bearing_deg,
            float(aspect_deg) if isinstance(aspect_deg, int | float) else None,
            tolerance_deg,
        )
        if label is None:
            continue
        covered[label] = covered.get(label, 0.0) + lengths_m[index]

    if not covered:
        return None
    winner = max(covered.values())
    leaders = [label for label, metres in covered.items() if metres == winner]
    return leaders[0] if len(leaders) == 1 else CROSSING
