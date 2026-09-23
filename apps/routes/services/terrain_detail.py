"""
apps/routes/services/terrain_detail.py — one row per segment, four figures.

SNOW-1020. Four numbers describe what the ground is doing under a route at
any point: the slope angle, the aspect, the track's bearing, and the
gradient along the track. All four were available and no two could be
read together: ``compact_slope`` keeps the aspect off the wire,
``passages`` derives the bearing and throws it away, and nothing computed
the along-track gradient at all. This module puts them in one row per
segment, for the staff page in ``apps.public.debug_views`` and its CSV.

**NOTHING HERE IS STORED.** ``docs/decisions/a-slope-segment-is-the-
shared-record.md`` rejected storing derived terrain figures, and every
figure below is a pure function of the stored record and the route's own
points. Nothing is re-sampled either.

## Where each figure comes from

* ``angle_deg`` / ``aspect_deg`` — the stored segment, verbatim.
* ``bearing_deg`` — the chord between the segment's two stored boundaries,
  the same measurement ``passages`` votes with.
* ``fall_line`` — ``passages.fall_line_alignment`` over those two, so this
  table and a passage's label can never be two measurements.
* ``track_gradient_deg`` — the along-track gradient, SIGNED: positive
  climbing, negative descending, in the direction the track was recorded.

## The track gradient needs the route's points, not just the record

The record's boundaries are ``[lon, lat]`` only — the sampler reads the
terrain and deliberately never the track's own elevation
(``slope_segments``' module docstring). So each boundary's elevation is
recovered by walking ``Route.points`` again with the sampler's own
functions (``cumulative_distances``, ``stride_distances``) at the record's
own ``stride_m``, and interpolating the third ordinate at each boundary
distance. Using the same walk is what makes the recovered boundaries the
stored ones; when the count does not match — the points are not the ones
the record was sampled from — the gradient is None throughout rather than
a figure placed against the wrong ground.

## A segment steeper than its ground is rejected, then the rest smoothed

**A TRACK ON THE GROUND CANNOT RISE FASTER THAN THE GROUND DOES.** So a
segment whose own raw gradient exceeds its stored ground angle by more
than ``GRADIENT_TOLERANCE_DEG`` is not a measurement of the track, and it
is REJECTED: its gradient is None, ``track_gradient_rejected`` says why,
and its rise is left out of every neighbour's window. Leaving it in is
what made smoothing fail on the reference track (SNOW-1020): Mont Fort –
Backside has two stored points 2.7 m apart horizontally and 244.8 m apart
vertically — a break in the recording, not a slope — and a ±2 window
spread it into five segments of about 72 degrees instead of removing it.

The test needs a ground angle, so an unknown segment cannot be checked
and is never rejected. Its raw gradient stands, and a recording break on
unsurveyed ground passes through. That is the honest limit of a check
against the ground: where there is no ground there is nothing to check.

Then the gradient at segment i is the rise over the run across the
ACCEPTED segments from ``i - window`` to ``i + window``: summed height
change over summed length, then the arctangent. That is a length-weighted
mean of the slope RATIO, not a mean of angles, which is the physically
honest average — ``atan`` is not linear. Both are keyword arguments, as
every gate in ``fall_line`` and ``passages`` is.

## What the gradient can never be better than

**THE ORIGINAL GPX IS NOT KEPT** (``docs/decisions/gpx-uploads-are-parsed-
not-stored.md``), so this reads ``Route.points`` and nothing finer. Two
ramifications, both recorded in that decision's consequences:

* On an upload over ``gpx.MAX_POINTS`` the points are the Douglas–Peucker
  remnant. Simplification keeps real elevations at the surviving vertices
  but shortens the horizontal run across every cut corner, so the
  gradient is OVERSTATED on a switchback — the one shape a skin track is
  made of — and more of it is rejected by the check above. The four
  canonical tracks are all under the bound and stored whole, so the
  figures measured on them do not show this.
* A recording break, like the Backside one, is in the stored points for
  good. It can be rejected here; it cannot be repaired, and a rule that
  would repair it applies to new uploads only.

## An unknown segment keeps its track figures

An unknown segment carries its reason, and ``angle_deg``, ``aspect_deg``
and ``fall_line`` are None — an unknown is a reason, never a zero. Its
bearing and track gradient are NOT blanked: they are measurements of the
track rather than of the ground, and the terrain model having no answer
for the ground says nothing about which way the skier was going.
"""

from __future__ import annotations

import math
from typing import Any

from apps.core.geo import initial_bearing_deg
from apps.routes.services.passages import fall_line_alignment
from apps.routes.services.slope_segments import cumulative_distances, stride_distances
from apps.routes.services.slope_summary import segment_lengths_m

# Half-width of the track-gradient smoothing window, in segments either
# side. Two: five segments, about 125 m on a 25 m stride — enough to take
# the spike out of a GPS elevation trace and short enough to keep a real
# 100 m steep step visible.
GRADIENT_WINDOW = 2

# How far a segment's raw track gradient may exceed its ground angle, in
# degrees, before it is rejected as not a measurement of the track.
#
# Five: a 2 m error in one boundary's elevation — ordinary for GPS, and
# the stored ground angle is itself an average over a 10 m window — tilts
# a 25 m segment by about 4.6 degrees. Measured on the four canonical
# tracks and the seeded Verbier track, 5 degrees rejects 4 to 29 segments
# a route (at most 5.6 %), including the Backside recording break; 2
# degrees rejects nearly twice as many, which is noise being thrown away
# as if it were a gap.
GRADIENT_TOLERANCE_DEG = 5.0

# Decimal places kept on a reported angle or bearing, matching the stored
# record's own precision (``slope_segments._ANGLE_PRECISION``).
_ANGLE_PRECISION = 1

# Decimal places kept on a reported distance, matching ``slope_summary``.
_LENGTH_PRECISION = 1

# The columns, in order. The CSV header and the page's table both read
# this, so the two cannot list different fields.
COLUMNS: tuple[str, ...] = (
    "i",
    "from_m",
    "length_m",
    "angle_deg",
    "aspect_deg",
    "bearing_deg",
    "track_gradient_deg",
    "track_gradient_rejected",
    "fall_line",
    "unknown",
)


def terrain_detail(
    record: dict[str, Any] | None,
    points: list[list[float | None]] | None,
    *,
    gradient_window: int = GRADIENT_WINDOW,
    tolerance_deg: float = GRADIENT_TOLERANCE_DEG,
) -> list[dict[str, Any]] | None:
    """Return one row per segment of a stored slope record.

    Args:
        record: A ``Route.slope_samples`` (or ``Trip.slope_samples``)
            value, or None for a track that has never been sampled.
        points: The track the record was sampled from, as
            ``[[lon, lat, ele], …]``. Read only for its elevations.
        gradient_window: Half-width of the track-gradient smoothing, in
            segments. 0 gives each accepted segment's raw gradient.
        tolerance_deg: How far a segment's raw gradient may exceed its
            ground angle before it is rejected.

    Returns:
        One dict per segment, in track order, keyed by ``COLUMNS``. Every
        figure that could not be derived is None. None overall when there
        is nothing to read — never sampled, or a record whose boundaries
        and segments do not pair up — the refusals ``route_passages``
        makes, for the same reason.

    """
    if not record:
        return None
    boundaries = record.get("points") or []
    segments = record.get("segments") or []
    if len(boundaries) != len(segments) + 1 or not segments:
        return None

    lengths = segment_lengths_m(record)
    elevations = _boundary_elevations(record, points or [], len(boundaries))
    rises: list[float | None] = [
        None if a is None or b is None else b - a
        for a, b in zip(elevations, elevations[1:], strict=False)
    ]
    rejected = [
        _steeper_than_ground(
            rises[index],
            lengths[index],
            _number(segment.get("angle_deg")),
            tolerance_deg,
        )
        for index, segment in enumerate(segments)
    ]
    accepted = [
        None if bad else rise for rise, bad in zip(rises, rejected, strict=True)
    ]
    gradients = _track_gradients(accepted, lengths, gradient_window)

    rows: list[dict[str, Any]] = []
    from_m = 0.0
    for index, segment in enumerate(segments):
        angle_deg = _number(segment.get("angle_deg"))
        aspect_deg = (
            _number(segment.get("aspect_deg")) if angle_deg is not None else None
        )
        # (lat, lon), the house argument order — the record stores
        # GeoJSON axis order, so the pairs are swapped at the call.
        bearing_deg = initial_bearing_deg(
            boundaries[index][1],
            boundaries[index][0],
            boundaries[index + 1][1],
            boundaries[index + 1][0],
        )
        rows.append(
            {
                "i": index,
                "from_m": round(from_m, _LENGTH_PRECISION),
                "length_m": round(lengths[index], _LENGTH_PRECISION),
                "angle_deg": angle_deg,
                "aspect_deg": aspect_deg,
                "bearing_deg": _rounded(bearing_deg),
                "track_gradient_deg": (
                    None if rejected[index] else _rounded(gradients[index])
                ),
                "track_gradient_rejected": rejected[index],
                "fall_line": fall_line_alignment(bearing_deg, aspect_deg),
                "unknown": segment.get("unknown"),
            }
        )
        from_m += lengths[index]
    return rows


def _boundary_elevations(
    record: dict[str, Any],
    points: list[list[float | None]],
    boundary_count: int,
) -> list[float | None]:
    """Return the track's elevation at each of the record's boundaries.

    Args:
        record: The stored record, read for its ``stride_m``.
        points: The route's ``[lon, lat, ele]`` track.
        boundary_count: How many boundaries the record stores.

    Returns:
        One elevation per boundary, None where either stored point around
        it has no elevation. All None when the walk cannot be repeated —
        no stride on the record (one written before the sampler stored
        it), or a re-walk that lands a different number of boundaries,
        which means these are not the points the record came from.

    """
    missing: list[float | None] = [None] * boundary_count
    stride_m = record.get("stride_m")
    if not isinstance(stride_m, int | float) or stride_m <= 0:
        return missing
    cumulative = cumulative_distances(points)
    if not cumulative or cumulative[-1] <= 0:
        return missing
    targets = stride_distances(cumulative[-1], float(stride_m))
    if len(targets) != boundary_count:
        return missing
    return _interpolate_elevations(points, cumulative, targets)


def _interpolate_elevations(
    points: list[list[float | None]],
    cumulative: list[float],
    targets: list[float],
) -> list[float | None]:
    """Return the elevation at each along-track distance.

    Linear between the two stored points a target falls between, walked
    the way ``slope_segments._interpolate_along`` walks the coordinates so
    a target lands between the same pair of points in both.

    Args:
        points: The route's ``[lon, lat, ele]`` track.
        cumulative: Its cumulative distances.
        targets: Ascending along-track distances, in metres.

    Returns:
        One elevation per target, None where either neighbour has none.

    """
    result: list[float | None] = []
    leg = 0
    for target in targets:
        while leg < len(cumulative) - 2 and cumulative[leg + 1] < target:
            leg += 1
        start = _number(points[leg][2] if len(points[leg]) > 2 else None)
        end = _number(points[leg + 1][2] if len(points[leg + 1]) > 2 else None)
        if start is None or end is None:
            result.append(None)
            continue
        span = cumulative[leg + 1] - cumulative[leg]
        fraction = 0.0 if span <= 0 else (target - cumulative[leg]) / span
        fraction = min(max(fraction, 0.0), 1.0)
        result.append(start + (end - start) * fraction)
    return result


def _steeper_than_ground(
    rise_m: float | None,
    length_m: float,
    angle_deg: float | None,
    tolerance_deg: float,
) -> bool:
    """Return whether a segment's raw gradient is steeper than its ground.

    Args:
        rise_m: Height change across the segment, or None when unknown.
        length_m: Its along-track length.
        angle_deg: The stored ground angle, or None for an unknown segment.
        tolerance_deg: The allowance above the ground angle.

    Returns:
        True only when both figures exist and the track outruns the
        ground. An unknown segment, or one with no elevation, is never
        rejected: there is nothing to check it against.

    """
    if rise_m is None or angle_deg is None or length_m <= 0:
        return False
    return math.degrees(math.atan(abs(rise_m) / length_m)) > angle_deg + tolerance_deg


def _track_gradients(
    rises: list[float | None],
    lengths: list[float],
    window: int,
) -> list[float | None]:
    """Return the smoothed, signed along-track gradient of each segment.

    Args:
        rises: One height change per segment, None where unknown or
            rejected — either way it contributes nothing to any window.
        lengths: One along-track length per segment, in metres.
        window: Half-width of the smoothing, in segments.

    Returns:
        Degrees, positive climbing. None for a segment whose window holds
        no segment with a usable rise.

    """
    gradients: list[float | None] = []
    for index in range(len(lengths)):
        rise = 0.0
        run = 0.0
        for neighbour in range(
            max(0, index - window), min(len(lengths), index + window + 1)
        ):
            neighbour_rise = rises[neighbour]
            if neighbour_rise is None:
                continue
            rise += neighbour_rise
            run += lengths[neighbour]
        gradients.append(math.degrees(math.atan2(rise, run)) if run > 0 else None)
    return gradients


def _number(value: Any) -> float | None:
    """Return a stored value as a float, or None when it is not a number.

    Args:
        value: One field of a stored segment or point.

    Returns:
        The float, or None.

    """
    return float(value) if isinstance(value, int | float) else None


def _rounded(value: float | None) -> float | None:
    """Round an angle to the record's precision, keeping None.

    Args:
        value: Degrees, or None.

    Returns:
        The rounded value, or None.

    """
    return None if value is None else round(value, _ANGLE_PRECISION)
