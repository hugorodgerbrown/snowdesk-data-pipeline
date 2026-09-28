"""
apps/routes/services/terrain_heights.py — a track's heights from the model.

SNOW-1043. A stored track's third ordinate is what the recording device's
barometric altimeter said, and an altimeter drifts. On the Mont Fort –
Backside canonical tour the recorded height starts 139 m above the terrain
model and rises to 744 m above it over the first 1.5 km while the skier is
descending, so ``detect_legs`` reads a 591 m descent as a 215 m climb and
an 874 m drop that does not exist. The profile, the ascent and descent
totals and the along-track gradient all inherit the same error.

The slope sampler already visits the terrain grid at every 25 m boundary,
and since SNOW-1043 it records the model's height there as the record's
``heights`` list (``apps.routes.services.slope_segments``). This module
puts those heights back onto the track:

* ``terrain_points`` — the stored track with its third ordinate replaced
  by the model height, interpolated along the track between the record's
  boundaries.
* ``climb_totals`` — ascent and descent summed over any track, which the
  feeds call on the result of ``terrain_points``.
* ``has_terrain_heights`` — whether a record carries any model height at
  all, which is what decides between those totals and the stored columns.

**THE DEVICE HEIGHT IS THE FALLBACK, NOT DISCARDED.** Where the model has
no answer — outside its coverage, a hole inside it, or a record written
before SNOW-1043 — the stored elevation stays. On the Chamonix – Col de
Balme track that is 100 of 1,134 points, on the French side of the border.
A track with a drifting altimeter over uncovered ground is still read as
recorded, because there is nothing better to read it against.

**NOTHING HERE IS STORED.** ``Route.points``, ``ascent_m`` and
``descent_m`` stay what the parser wrote. The heights are applied at read
time, so a record resampled against a newer grid changes every figure
without a migration over the user's rows, and the device series is still
there for the day someone wants to compare the two.
"""

from __future__ import annotations

from typing import Any

from apps.routes.services.slope_segments import cumulative_distances, stride_distances

# Decimal places kept on an interpolated height — the record's own
# precision (``slope_segments._HEIGHT_PRECISION``).
_HEIGHT_PRECISION = 1

# Decimal places kept on an ascent or descent total.
_TOTAL_PRECISION = 1


def has_terrain_heights(samples: dict[str, Any] | None) -> bool:
    """Return whether a slope record carries any model height.

    Args:
        samples: A ``Route.slope_samples`` or ``Trip.slope_samples``
            value, or None for a track nothing has sampled.

    Returns:
        True when the record has a ``heights`` list with at least one
        number in it. A list of nulls — a track wholly outside the
        model's coverage — is False: there is nothing to read the track
        against, and the stored figures are the better answer.

    """
    if not isinstance(samples, dict):
        return False
    heights = samples.get("heights")
    if not isinstance(heights, list):
        return False
    return any(_is_number(height) for height in heights)


def terrain_points(
    points: list[list[float | None]],
    samples: dict[str, Any] | None,
) -> list[list[float | None]]:
    """Return the track with its heights read from the terrain model.

    Each stored point keeps its longitude and latitude; its elevation
    becomes the model height at its along-track distance, linear between
    the two record boundaries it falls between. The boundaries are found
    the way ``terrain_detail._boundary_elevations`` finds them: the
    sampler's own walk (``cumulative_distances``, ``stride_distances``)
    repeated at the record's ``stride_m``, so a boundary here is the one
    the height was sampled at.

    Args:
        points: The stored track, ``[lon, lat, ele]`` triples.
        samples: The track's slope record, or None.

    Returns:
        A new list; the input is never mutated. Every point keeps its
        device elevation where either bracketing boundary has no model
        height. The whole track is returned as stored when the record has
        no ``heights``, carries no usable stride, or the re-walk lands a
        different number of boundaries than it stores — which means these
        are not the points the record was sampled from, and a height
        placed from it would be against the wrong ground.

    """
    unchanged = [list(point) for point in points]
    if samples is None or not has_terrain_heights(samples):
        return unchanged
    heights: list[Any] = samples["heights"]
    walk = _repeat_walk(points, samples, len(heights))
    if walk is None:
        return unchanged
    cumulative, boundaries = walk

    result: list[list[float | None]] = []
    gap = 0
    for point, distance in zip(points, cumulative, strict=True):
        while gap < len(boundaries) - 2 and boundaries[gap + 1] < distance:
            gap += 1
        start = heights[gap]
        end = heights[gap + 1]
        device = point[2] if len(point) > 2 else None
        if not _is_number(start) or not _is_number(end):
            result.append([point[0], point[1], device])
            continue
        span = boundaries[gap + 1] - boundaries[gap]
        fraction = 0.0 if span <= 0 else (distance - boundaries[gap]) / span
        fraction = min(max(fraction, 0.0), 1.0)
        height = start + (end - start) * fraction
        result.append([point[0], point[1], round(height, _HEIGHT_PRECISION)])
    return result


def _repeat_walk(
    points: list[list[float | None]],
    samples: dict[str, Any],
    boundary_count: int,
) -> tuple[list[float], list[float]] | None:
    """Repeat the sampler's stride walk over ``points``, or decline.

    Args:
        points: The stored track.
        samples: Its slope record, read for ``stride_m`` and ``points``.
        boundary_count: How many heights the record stores.

    Returns:
        ``(cumulative, boundaries)`` — each point's along-track distance
        and each boundary's — or None when the walk cannot be repeated or
        lands a different number of boundaries than the record holds.

    """
    stride_m = samples.get("stride_m")
    if not _is_number(stride_m) or stride_m <= 0:
        return None
    stored = samples.get("points")
    if isinstance(stored, list) and len(stored) != boundary_count:
        return None
    cumulative = cumulative_distances(points)
    if not cumulative or cumulative[-1] <= 0:
        return None
    boundaries = stride_distances(cumulative[-1], float(stride_m))
    if len(boundaries) != boundary_count:
        return None
    return cumulative, boundaries


def climb_totals(
    points: list[list[float | None]],
) -> tuple[float | None, float | None]:
    """Return a track's total climb and total drop, in metres.

    The rule ``gpx._total_ascent_descent_m`` applies at ingest: only
    consecutive pairs where both points carry an elevation contribute, and
    the two totals are summed independently rather than netted. Repeated
    here rather than imported because that function takes the parser's
    tuples and is private to it.

    Args:
        points: A track as ``[lon, lat, ele]``.

    Returns:
        ``(ascent_m, descent_m)``, descent as a positive magnitude, each
        to one decimal. ``(None, None)`` when no point carries an
        elevation — "unknown", not zero.

    """
    elevations = [point[2] if len(point) > 2 else None for point in points]
    if not any(_is_number(elevation) for elevation in elevations):
        return None, None
    ascent = 0.0
    descent = 0.0
    for previous, current in zip(elevations, elevations[1:], strict=False):
        if not _is_number(previous) or not _is_number(current):
            continue
        delta = float(current) - float(previous)  # type: ignore[arg-type]
        if delta > 0:
            ascent += delta
        else:
            descent -= delta
    return round(ascent, _TOTAL_PRECISION), round(descent, _TOTAL_PRECISION)


def _is_number(value: Any) -> bool:
    """Return whether a stored value is a usable number.

    ``bool`` is excluded: it is an ``int`` to Python and never a height.

    Args:
        value: One entry of a record or a point.

    Returns:
        True for an int or float that is not a bool.

    """
    return isinstance(value, int | float) and not isinstance(value, bool)
