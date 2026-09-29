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

* ``terrain_points`` — THE terrain track: the stored vertices merged with
  the record's boundaries, every point carrying a model height. The feeds'
  geometry, the legs (``wire_legs`` indexes into it), the bulletin join
  and the climb figures all read this one list, so an index and a figure
  can never refer to two different tracks.
* ``boundary_heights`` — the same heights at the record's boundaries only,
  for ``terrain_detail``'s along-track gradient.
* ``climb_totals`` — ascent and descent summed over any track.
* ``has_terrain_heights`` — whether a record carries any model height at
  all, which is what decides between the model and the stored columns.
* ``climb_figures`` — THE rule for which ascent and descent a surface
  shows, applied by the feeds and, through ``Route.climb`` / ``Trip.climb``,
  by every template. One place, so the map, the route row and the trip
  card can never quote two different figures for one track.

**THE BOUNDARIES ARE MERGED IN, NOT ONLY USED TO INTERPOLATE.** A planned
route is often a handful of vertices hundreds of metres apart; a straight
leg stored as its two ends that crosses a hill would otherwise read the
model only at the two ends and report no climb. So the terrain track is
the stored vertices and the record's boundary points in along-track
order: each boundary with its own model height, each vertex with the
model height interpolated between the boundaries either side. A boundary
within ``_COINCIDENT_M`` of a vertex is dropped — the vertex already
carries that height, and a near-duplicate point would add a zero-length
step to every consumer. The boundary points lie on the stored polyline
(the sampler interpolated them along it), so the drawn line does not
change; only the point count does.

**THE DEVICE HEIGHT IS THE FALLBACK, REBASED ONTO THE MODEL.** Where the
model has no answer — outside its coverage or a hole inside it — the
recorded height is used, because there is nothing better. Used raw, a
drifting altimeter would put a vertical cliff at every coverage edge: 600
m of drift is a 600 m step between the last model point and the first
device point, which ``climb_totals`` counts and ``detect_legs`` can turn
into a leg. So each run of fallback points is shifted onto the model's
datum: the offset (model − device) is measured at the known point just
before the run and the one just after it, and blended linearly by
distance along the run. A run that touches an end of the track takes the
one offset it has; a track with no model height at all keeps its device
values as they are. The run keeps the SHAPE the device recorded, which is
the part of an altimeter reading that can be trusted over a short span.

**RECORDS WITHOUT HEIGHTS ARE UNTOUCHED.** No ``heights``, no usable
stride, or a re-walk that lands a different number of boundaries than the
record stores (these are not the points it was sampled from) returns the
stored track as it is, point for point.

**NOTHING HERE IS STORED.** ``Route.points``, ``ascent_m`` and
``descent_m`` stay what the parser wrote, and the GPX export reads the
stored points. The heights are applied at read time, so a record
resampled against a newer grid changes every figure without a migration
over the user's rows.
"""

from __future__ import annotations

import bisect
from typing import Any, NamedTuple

from apps.routes.services.slope_segments import cumulative_distances, stride_distances

# Decimal places kept on an interpolated height — the record's own
# precision (``slope_segments._HEIGHT_PRECISION``).
_HEIGHT_PRECISION = 1

# Decimal places kept on an ascent or descent total.
_TOTAL_PRECISION = 1

# How close, in metres along the track, a record boundary may fall to a
# stored vertex before it is dropped from the merged track as the same
# place. Half a metre: under the 0.1 m the record's rounded boundary
# coordinates can sit off the line, well above float noise, and far below
# the 5 m cell a height comes from.
_COINCIDENT_M = 0.5


class _Station(NamedTuple):
    """One point of the merged terrain track, before it is written out.

    Attributes:
        distance_m: Along-track distance from the start.
        longitude: Degrees east.
        latitude: Degrees north.
        device_m: The recorded height here — a vertex's own, or one
            interpolated between vertices for a boundary — or None.
        model_m: The terrain model's height here, or None where it has
            none.
        vertex: The stored point's index, or None for a boundary.
        boundary: The record boundary's index, or None for a vertex.

    """

    distance_m: float
    longitude: float
    latitude: float
    device_m: float | None
    model_m: float | None
    vertex: int | None
    boundary: int | None


class ClimbFigures(NamedTuple):
    """A track's ascent and descent as shown, in metres.

    Attributes:
        ascent_m: Total climb, or None when unknown.
        descent_m: Total drop as a positive magnitude, or None when
            unknown.

    """

    ascent_m: float | None
    descent_m: float | None


def climb_figures(
    points: list[list[float | None]],
    samples: dict[str, Any] | None,
    stored_ascent_m: float | None,
    stored_descent_m: float | None,
) -> ClimbFigures:
    """Return the ascent and descent every surface shows for one track.

    Summed over ``terrain_points`` when the slope record carries model
    heights, and the stored columns otherwise — which keeps a route
    nothing has sampled, or one sampled before SNOW-1043, showing what
    the parser measured on its full-resolution track. A null stored figure
    passes through: "unknown", not zero.

    Args:
        points: The stored track, ``[lon, lat, ele]`` triples.
        samples: Its slope record, or None.
        stored_ascent_m: The row's ``ascent_m``.
        stored_descent_m: The row's ``descent_m``.

    Returns:
        The two figures.

    """
    if not has_terrain_heights(samples):
        return ClimbFigures(stored_ascent_m, stored_descent_m)
    ascent_m, descent_m = climb_totals(terrain_points(points, samples))
    return ClimbFigures(ascent_m, descent_m)


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
    """Return the terrain track: vertices and boundaries on model heights.

    See the module docstring for the merge and the fallback rule.

    Args:
        points: The stored track, ``[lon, lat, ele]`` triples.
        samples: The track's slope record, or None.

    Returns:
        A new list; the input is never mutated. With usable heights, the
        stored vertices and the record's boundary points in along-track
        order, each as ``[lon, lat, height]``. Without them, the stored
        track as it is.

    """
    stations = _stations(points, samples)
    if stations is None:
        return [list(point) for point in points]
    heights = _rebased_heights(stations)
    vertex_distances = [
        station.distance_m for station in stations if station.vertex is not None
    ]
    result: list[list[float | None]] = []
    for station, height in zip(stations, heights, strict=True):
        if station.vertex is None and _near_vertex(
            vertex_distances, station.distance_m
        ):
            continue
        if station.vertex is not None and station.model_m is None:
            # A fallback vertex keeps its own coordinate objects and, when
            # nothing could rebase it, its recorded value exactly.
            stored = points[station.vertex]
            result.append([stored[0], stored[1], height])
            continue
        result.append([station.longitude, station.latitude, height])
    return result


def boundary_heights(
    points: list[list[float | None]],
    samples: dict[str, Any] | None,
) -> list[float | None] | None:
    """Return the terrain track's height at each of the record's boundaries.

    The same heights ``terrain_points`` writes, fallback rebasing
    included, read at the boundaries rather than at the merged points —
    what ``terrain_detail`` measures its along-track gradient on.

    Args:
        points: The stored track.
        samples: Its slope record.

    Returns:
        One height per boundary (None where neither the model nor the
        device has one), or None when the record carries no usable
        heights for these points.

    """
    stations = _stations(points, samples)
    if stations is None:
        return None
    by_boundary: dict[int, float | None] = {}
    for station, height in zip(stations, _rebased_heights(stations), strict=True):
        if station.boundary is not None:
            by_boundary[station.boundary] = height
    return [by_boundary[index] for index in range(len(by_boundary))]


def _stations(
    points: list[list[float | None]],
    samples: dict[str, Any] | None,
) -> list[_Station] | None:
    """Return the vertices and boundaries of a track in along-track order.

    Args:
        points: The stored track.
        samples: Its slope record.

    Returns:
        Every vertex and every boundary, vertices first on a tie, or None
        when the record has no usable heights for these points.

    """
    if samples is None or not has_terrain_heights(samples):
        return None
    heights: list[Any] = samples["heights"]
    coordinates = samples.get("points")
    if not isinstance(coordinates, list) or len(coordinates) != len(heights):
        return None
    walk = _repeat_walk(points, samples, len(heights))
    if walk is None:
        return None
    cumulative, boundaries = walk
    model = [float(height) if _is_number(height) else None for height in heights]
    device = [_elevation(point) for point in points]

    vertices = [
        _Station(
            distance,
            float(point[0] or 0.0),
            float(point[1] or 0.0),
            device[index],
            _interpolate(boundaries, model, distance),
            index,
            None,
        )
        for index, (point, distance) in enumerate(zip(points, cumulative, strict=True))
    ]
    stops = [
        _Station(
            distance,
            float(coordinates[index][0]),
            float(coordinates[index][1]),
            _interpolate(cumulative, device, distance),
            model[index],
            None,
            index,
        )
        for index, distance in enumerate(boundaries)
    ]
    # Both lists are ascending, so this is a merge; the sort key puts a
    # vertex ahead of a boundary at the same distance.
    return sorted(
        vertices + stops,
        key=lambda station: (station.distance_m, station.vertex is None),
    )


def _rebased_heights(stations: list[_Station]) -> list[float | None]:
    """Return each station's height, fallback runs rebased onto the model.

    Args:
        stations: The merged track, in along-track order.

    Returns:
        One height per station: the model's where it has one, and the
        device's shifted by the blended offset (see the module docstring)
        elsewhere. A fallback station with no device height stays None,
        and one no offset can be measured for keeps its device height.

    """
    heights: list[float | None] = [
        None if station.model_m is None else round(station.model_m, _HEIGHT_PRECISION)
        for station in stations
    ]
    index = 0
    while index < len(stations):
        if stations[index].model_m is not None:
            index += 1
            continue
        first = index
        while index < len(stations) and stations[index].model_m is None:
            index += 1
        last = index - 1
        before = stations[first - 1] if first > 0 else None
        after = stations[last + 1] if last + 1 < len(stations) else None
        for position in range(first, last + 1):
            heights[position] = _rebased(stations[position], before, after)
    return heights


def _rebased(
    station: _Station, before: _Station | None, after: _Station | None
) -> float | None:
    """Return one fallback station's height on the model's datum.

    Args:
        station: A station the model has no height for.
        before: The known station just before its run, or None at the
            track's start.
        after: The known station just after its run, or None at its end.

    Returns:
        The device height plus the blended offset, the device height
        unshifted when no offset can be measured, or None when the
        device has no height here either.

    """
    if station.device_m is None:
        return None
    offset_before = _offset(before)
    offset_after = _offset(after)
    if offset_before is None and offset_after is None:
        return station.device_m
    if offset_before is None or before is None:
        offset = offset_after
    elif offset_after is None or after is None:
        offset = offset_before
    else:
        span = after.distance_m - before.distance_m
        fraction = 0.0 if span <= 0 else (station.distance_m - before.distance_m) / span
        offset = offset_before + (offset_after - offset_before) * fraction
    return round(station.device_m + (offset or 0.0), _HEIGHT_PRECISION)


def _offset(anchor: _Station | None) -> float | None:
    """Return model minus device at a known station, or None.

    Args:
        anchor: A station carrying a model height, or None.

    Returns:
        The datum offset there, or None when there is no anchor or it has
        no device height to measure against.

    """
    if anchor is None or anchor.model_m is None or anchor.device_m is None:
        return None
    return anchor.model_m - anchor.device_m


def _near_vertex(vertex_distances: list[float], distance_m: float) -> bool:
    """Return whether a boundary falls on a stored vertex.

    Args:
        vertex_distances: Every vertex's along-track distance, ascending.
        distance_m: The boundary's.

    Returns:
        True within ``_COINCIDENT_M`` of either neighbouring vertex.

    """
    position = bisect.bisect_left(vertex_distances, distance_m)
    neighbours = vertex_distances[max(0, position - 1) : position + 1]
    return any(abs(distance_m - other) <= _COINCIDENT_M for other in neighbours)


def _interpolate(
    distances: list[float], values: list[float | None], target: float
) -> float | None:
    """Return a value at ``target``, linear between its two neighbours.

    Args:
        distances: Ascending along-track distances.
        values: One value per distance, possibly None.
        target: The distance to read at.

    Returns:
        The interpolated value, or None when either neighbour has none.

    """
    position = bisect.bisect_left(distances, target)
    if position < len(distances) and distances[position] == target:
        return values[position]
    position = min(max(position, 1), len(distances) - 1)
    start, end = values[position - 1], values[position]
    if start is None or end is None:
        return None
    span = distances[position] - distances[position - 1]
    fraction = 0.0 if span <= 0 else (target - distances[position - 1]) / span
    fraction = min(max(fraction, 0.0), 1.0)
    return start + (end - start) * fraction


def _elevation(point: list[float | None]) -> float | None:
    """Return a stored point's elevation, or None.

    Args:
        point: ``[lon, lat, ele]``.

    Returns:
        The elevation as a float, or None when absent.

    """
    value = point[2] if len(point) > 2 else None
    return float(value) if _is_number(value) else None  # type: ignore[arg-type]


def _repeat_walk(
    points: list[list[float | None]],
    samples: dict[str, Any],
    boundary_count: int,
) -> tuple[list[float], list[float]] | None:
    """Repeat the sampler's stride walk over ``points``, or decline.

    Args:
        points: The stored track.
        samples: Its slope record, read for ``stride_m``.
        boundary_count: How many heights the record stores.

    Returns:
        ``(cumulative, boundaries)`` — each point's along-track distance
        and each boundary's — or None when the walk cannot be repeated or
        lands a different number of boundaries than the record holds.

    """
    stride_m = samples.get("stride_m")
    if not isinstance(stride_m, int | float) or stride_m <= 0:
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
