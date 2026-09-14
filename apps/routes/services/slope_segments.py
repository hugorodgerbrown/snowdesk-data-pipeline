"""
apps/routes/services/slope_segments.py — how steep the ground under a track is.

Walks a stored ``Route.points`` track at a fixed stride, asks
``apps.locations.services.terrain.sample_slope`` how steep the GROUND is at
the middle of each stride, and writes the answers to ``Route.slope_samples``.

**THE TERRAIN, NEVER THE TRACK.** ``Route.points`` already carries an
elevation per coordinate, so a gradient along the track is free arithmetic
— and it is the wrong number. A skin track zigzagging up a 38 degree face
rises about 15 degrees along its own length, and a rising traverse across
the same face rises near zero. Colouring either of those as gentle ground
is precisely the "the map said it was fine" failure the feature exists to
prevent, so nothing here reads the third ordinate of a point.

The record written to ``Route.slope_samples``::

    {
      "window_m": 10.0,
      "stride_m": 25.0,
      "grid": "snowdesk-terrain-5m-3035",
      "points":   [[lon, lat], …],                         # N + 1
      "segments": [{"angle_deg": 34.2, "aspect_deg": 105.3},
                   {"unknown": "outside_coverage"}, …]     # N
    }

``points`` and ``segments`` share endpoints: N + 1 coordinates bound N
segments, so consecutive segments are not each given their own copy of the
coordinate between them. On a 15 km tour that is 600 coordinates rather
than 1,200, and the saving is real because the whole record has to travel
to the map and sit in the offline cache.

**SAMPLE POINTS ARE NOT STORED POINTS.** The stride interpolates along the
track, so a sample coordinate usually falls between two of ``Route.points``
and the segment geometry is independent of it. That is why ``points`` is
part of the record at all rather than being recoverable from the route.

**A SEGMENT'S ANGLE IS SAMPLED AT ITS MIDDLE**, not at either end. One
sample per segment, and the one that is most representative of the ground
the segment actually crosses: an end-sampled segment would take its colour
from a point it only touches, and would have to choose between its two
ends to do it.

Aspect is carried per segment even though nothing renders it yet. It comes
free with the angle (``sample_slope`` computes both from the same kernel),
and SNOW-839 needs it to score a route against a bulletin's aspect bands —
re-sampling a whole route later to recover a figure we already had would be
a second pass over the tile origin for nothing.

All three of ``TerrainUnknown``'s reasons are preserved per segment even
though the map collapses them to one dashed treatment, per that module's
contract that a reason is never reduced to a null.

**AN ALL-UNAVAILABLE RESULT IS NOT STORED.** ``UNAVAILABLE`` is ours and is
transient — an unreachable tile origin answers it for every point on a
track. Writing that out would be a record that says nothing about the
ground AND would take the row out of the backfill command's candidate set,
which selects on the field being null. So a sampling run that learned
nothing leaves the field as it found it.

**AND IT IS NOT WAITED OUT.** The walk gives up the moment an outage is
confirmed rather than at the end. A failure is deliberately not memoised
by ``terrain.py`` — an outage has to be retried, not cached — so without
this every midpoint on a long track re-attempts the same dead tiles at the
transport's full timeout, which on a production task worker is minutes of
a shared worker spent to reach the same "learned nothing" answer. See
``_UNAVAILABLE_RUN_LIMIT``.
"""

from __future__ import annotations

import logging
from typing import Any

from django_tasks import task

from apps.core.geo import haversine_m
from apps.locations.services.terrain import TerrainSlope, TerrainUnknown, sample_slope
from apps.locations.services.terrain_grid import load_grid
from apps.routes.models import Route

logger = logging.getLogger(__name__)

# How far apart the samples are taken, in metres along the track.
#
# Decided with the ticket rather than derived. 25 m is about the length of
# ground a skier commits to at a time, and it is fine enough to catch the
# single steep roll in an otherwise gentle valley — the thing the 10 m
# raster's averaging already threatens to hide. Halving it would double
# both the tile reads and the payload for detail the eye cannot separate on
# a line a few pixels wide.
SAMPLE_STRIDE_M = 25.0

# Decimal places kept on a stored angle or bearing.
#
# One. The gradient comes off a 10 m analysis window over a 5 m grid, so
# the tenth of a degree is already past what the source can support and the
# second would be noise with a byte cost on every segment of every route.
_ANGLE_PRECISION = 1

# Decimal places kept on a stored sample coordinate. Six is about 0.1 m at
# these latitudes — far finer than the 5 m cell the sample came from, and
# it keeps a coordinate to a fixed, short length in the JSON.
_COORDINATE_PRECISION = 6

# How many UNAVAILABLE answers IN A ROW end the walk.
#
# Three. One or two are a blip — a single tile the origin failed to serve,
# on a track whose other 130 samples are fine — and aborting on those would
# throw away a run that was about to store good data. Three consecutive is
# the origin being down, and every remaining midpoint would spend the
# transport's full timeout to be told so again: a 15 km track is 600
# samples, which at a 10 s timeout is over an hour of a task worker for an
# answer already known after the third.
#
# CONSECUTIVE, not cumulative, and the counter resets on ANY other result.
# ``OUTSIDE_COVERAGE`` and ``NO_DATA`` are answers about the ground rather
# than failures to reach it, so they break a run exactly as an angle does —
# a track along the coverage edge must not read as an outage.
_UNAVAILABLE_RUN_LIMIT = 3


def build_slope_samples(route: Route) -> dict[str, Any] | None:
    """Sample the terrain along a route and return the record for it.

    Pure of the database: takes a route, returns what should be stored on
    it, and writes nothing. ``_worker_sample_route_slopes`` is what saves
    the result, and the backfill command shares this function so the two
    paths can never produce differently-shaped records.

    NEVER RAISES for a data problem, because ``sample_slope`` does not —
    every failure to answer arrives as a reason on a segment.

    Args:
        route: The route to sample. Only its ``points`` are read.

    Returns:
        The record described in the module docstring, or None when there
        is nothing worth storing: a track too short to hold one segment,
        a run in which the tile origin answered nothing at all, or one
        abandoned part-way because it had (see the module docstring on
        ``UNAVAILABLE``).

    """
    grid = load_grid()
    if grid is None:
        # No definition means no sample can succeed, so this is the
        # all-unavailable case reached before doing any work.
        logger.warning(
            "route slope sampling: terrain grid unavailable, route pk=%s left "
            "unsampled",
            route.pk,
        )
        return None

    cumulative = _cumulative_distances(route.points)
    if not cumulative or cumulative[-1] <= 0:
        return None

    boundaries = stride_distances(cumulative[-1], SAMPLE_STRIDE_M)
    if len(boundaries) < 2:
        return None
    midpoints = [
        (boundaries[index] + boundaries[index + 1]) / 2
        for index in range(len(boundaries) - 1)
    ]

    coordinates = _interpolate_along(route.points, cumulative, boundaries)

    # A loop rather than a comprehension, for the short-circuit below: the
    # run of consecutive failures has to be counted AS the walk proceeds,
    # because the whole point is not to finish it.
    segments: list[dict[str, Any]] = []
    unavailable_run = 0
    for longitude, latitude in _interpolate_along(route.points, cumulative, midpoints):
        segment = _segment_record(sample_slope(latitude, longitude))
        segments.append(segment)
        if segment.get("unknown") != TerrainUnknown.UNAVAILABLE:
            unavailable_run = 0
            continue
        unavailable_run += 1
        if unavailable_run >= _UNAVAILABLE_RUN_LIMIT:
            # Same outcome as the all-unavailable branch below — the field
            # is left null for the backfill to retry — reached in seconds
            # instead of minutes. Logged separately because the two say
            # different things to an operator: this one is an origin that
            # went down, and it names the route and nothing else (no
            # coordinate ever reaches a log line — SNOW-718/732).
            logger.warning(
                "route slope sampling: %d consecutive unavailable samples, "
                "route pk=%s abandoned unsampled",
                unavailable_run,
                route.pk,
            )
            return None

    # Still needed with the short-circuit above: a route short enough to
    # hold fewer than _UNAVAILABLE_RUN_LIMIT segments can be entirely
    # unavailable without ever reaching it.
    if all(
        segment.get("unknown") == TerrainUnknown.UNAVAILABLE for segment in segments
    ):
        logger.warning(
            "route slope sampling: every sample was unavailable, route pk=%s "
            "left unsampled",
            route.pk,
        )
        return None

    return {
        "window_m": grid.default_analysis_window_m,
        "stride_m": SAMPLE_STRIDE_M,
        "grid": grid.grid,
        "points": [
            [
                round(longitude, _COORDINATE_PRECISION),
                round(latitude, _COORDINATE_PRECISION),
            ]
            for longitude, latitude in coordinates
        ],
        "segments": segments,
    }


def stride_distances(total_m: float, stride_m: float) -> list[float]:
    """Return the along-track distance of every segment boundary.

    The whole stride walk, and the only place its one rule lives. The
    first boundary is the track's start and the last is its end, so the
    boundaries span the track and never stop short of it.

    **THE TRAILING STUB IS ABSORBED, NOT APPENDED.** A track whose length
    is not a whole number of strides would otherwise end in a segment of
    whatever remainder was left — possibly centimetres, painted a colour
    the eye reads as a real stretch of ground, and sampled at a midpoint
    that is effectively the track's last metre. So a remainder under half
    a stride REPLACES the last whole-stride boundary rather than following
    it, which bounds every segment to between half and one and a half
    strides.

    Args:
        total_m: The track's full length in metres.
        stride_m: Distance along the track between boundaries, in metres.

    Returns:
        Ascending distances starting at 0.0 and ending at ``total_m``; a
        single ``[0.0]`` for a track with no length, which holds no
        segment.

    """
    if total_m <= 0:
        return [0.0]

    whole_strides = int(total_m // stride_m)
    boundaries = [index * stride_m for index in range(whole_strides + 1)]
    if len(boundaries) > 1 and total_m - boundaries[-1] < stride_m / 2:
        boundaries[-1] = total_m
    else:
        boundaries.append(total_m)
    return boundaries


def stride_coordinates(
    points: list[list[float | None]], stride_m: float
) -> list[tuple[float, float]]:
    """Walk a stored track and return a coordinate every ``stride_m``.

    ``stride_distances`` decides where the boundaries fall; this places
    them on the track. Separated so the rule can be tested without a
    track and the interpolation without a stride.

    Args:
        points: The stored track as ``[[lon, lat, ele], …]``. The
            elevation is ignored — see the module docstring.
        stride_m: Distance along the track between coordinates, in metres.

    Returns:
        ``(lon, lat)`` pairs, start first. Empty for a track of fewer than
        two points; a single pair for a track with no length at all, which
        the caller discards as holding no segment.

    """
    cumulative = _cumulative_distances(points)
    if not cumulative:
        return []

    boundaries = stride_distances(cumulative[-1], stride_m)
    if len(boundaries) < 2:
        return [_coordinate(points[0])]
    return _interpolate_along(points, cumulative, boundaries)


def _segment_record(slope: TerrainSlope) -> dict[str, Any]:
    """Turn one terrain sample into the segment's stored record.

    Args:
        slope: What ``sample_slope`` answered for the segment's midpoint.

    Returns:
        Either ``{"angle_deg": …, "aspect_deg": …}`` or
        ``{"unknown": "<reason>"}`` — never both, and never a null angle,
        so a reader cannot mistake one for the other.

    """
    if slope.angle_deg is None:
        # Branched on the ANGLE rather than on the reason, which is what
        # narrows the type for the return below. The two are equivalent:
        # TerrainSlope.__post_init__ rejects a result carrying neither or
        # both, so no angle means exactly one named reason.
        return {"unknown": str(slope.unknown)}
    return {
        "angle_deg": round(slope.angle_deg, _ANGLE_PRECISION),
        # None on exactly level ground, which faces nowhere — see
        # TerrainSlope.aspect_deg. Not an unknown: the angle is known.
        "aspect_deg": (
            None
            if slope.aspect_deg is None
            else round(slope.aspect_deg, _ANGLE_PRECISION)
        ),
    }


def _cumulative_distances(points: list[list[float | None]]) -> list[float]:
    """Return the along-track distance of each stored point.

    Args:
        points: The stored track as ``[[lon, lat, ele], …]``.

    Returns:
        One distance per point, starting at 0.0 — empty for a track of
        fewer than two points, which has nothing to walk.

    """
    if not points or len(points) < 2:
        return []

    distances = [0.0]
    for previous, current in zip(points, points[1:], strict=False):
        start_lon, start_lat = _coordinate(previous)
        end_lon, end_lat = _coordinate(current)
        # (lat, lon), the house argument order — the stored points are
        # (lon, lat, ele), so the pair is swapped at the call.
        distances.append(
            distances[-1] + haversine_m(start_lat, start_lon, end_lat, end_lon)
        )
    return distances


def _interpolate_along(
    points: list[list[float | None]],
    cumulative: list[float],
    targets: list[float],
) -> list[tuple[float, float]]:
    """Return the coordinate at each along-track distance.

    Linear in longitude and latitude between the two stored points a
    target falls between. That is a straight line in the projection rather
    than a great circle, and over one leg of a simplified ski track — tens
    to a few hundred metres — the difference is far under the 5 m cell the
    result is sampled against.

    ``targets`` must be ascending, which lets one pass over the track
    answer all of them.

    Args:
        points: The stored track as ``[[lon, lat, ele], …]``.
        cumulative: That track's cumulative leg distances.
        targets: Ascending along-track distances, in metres.

    Returns:
        One ``(lon, lat)`` pair per target.

    """
    result: list[tuple[float, float]] = []
    leg = 0
    for target in targets:
        while leg < len(cumulative) - 2 and cumulative[leg + 1] < target:
            leg += 1
        span = cumulative[leg + 1] - cumulative[leg]
        # A zero-length leg (two identical stored points) has no direction
        # to walk along, so the target lands on its start.
        fraction = 0.0 if span <= 0 else (target - cumulative[leg]) / span
        fraction = min(max(fraction, 0.0), 1.0)
        start_lon, start_lat = _coordinate(points[leg])
        end_lon, end_lat = _coordinate(points[leg + 1])
        result.append(
            (
                start_lon + (end_lon - start_lon) * fraction,
                start_lat + (end_lat - start_lat) * fraction,
            )
        )
    return result


def _coordinate(point: list[float | None]) -> tuple[float, float]:
    """Return one stored point's ``(lon, lat)``, dropping its elevation.

    Args:
        point: One entry of ``Route.points`` — ``[lon, lat, ele]``, where
            the elevation may be null.

    Returns:
        The longitude and latitude.

    """
    # The ``or 0.0`` is for the type checker, not for real data: only the
    # THIRD ordinate is ever null (Route.points' help_text), and 0.0 is
    # its own truthiness fallback so a genuine zero coordinate survives.
    return float(point[0] or 0.0), float(point[1] or 0.0)


# ---------------------------------------------------------------------------
# Worker function — decorated with @task so django-tasks can enqueue and
# replay it. Accepts only JSON-serialisable primitives.
# ---------------------------------------------------------------------------


@task()
def _worker_sample_route_slopes(route_pk: int) -> None:
    """Background worker: sample one route's terrain and store the result.

    Takes a primary key rather than the route itself, and re-loads the row
    inside, so the task body is JSON-serialisable into the database and
    replayable on retry. Mirrors
    ``apps.accounts.push_service._worker_dispatch_push``.

    A route that no longer exists is an EXPECTED race — deleted between
    the enqueue and the run — so it logs at INFO and returns rather than
    failing the task.

    ``save(update_fields=…)`` writes the one column and nothing else: the
    row is the user's, the sampler has no business touching the geometry
    or the name, and a full save would race an owner renaming the route
    while the sampling ran.

    Args:
        route_pk: Primary key of the ``Route`` to sample.

    """
    try:
        route = Route.objects.get(pk=route_pk)
    except Route.DoesNotExist:
        logger.info(
            "route pk=%s no longer exists — skipping slope sampling worker",
            route_pk,
        )
        return

    samples = build_slope_samples(route)
    if samples is None:
        # Left null on purpose: null is "never sampled", which is what a
        # run that learned nothing leaves true. See the module docstring.
        return

    route.slope_samples = samples
    route.save(update_fields=["slope_samples", "updated_at"])
    logger.info(
        "route slopes sampled: pk=%s uuid=%s segments=%d unknown=%d",
        route.pk,
        route.uuid,
        len(samples["segments"]),
        sum(1 for segment in samples["segments"] if "unknown" in segment),
    )


def enqueue_route_slope_sampling(route: Route) -> None:
    """Enqueue terrain sampling for ``route`` to run off the request cycle.

    **CALL THIS OUTSIDE ANY OPEN TRANSACTION.** Under ``ImmediateBackend``
    — dev, test AND staging — ``.enqueue()`` runs the worker INLINE, so an
    enqueue inside ``create_route``'s ``atomic()`` block would make a
    tile-origin round trip per terrain tile the track crosses while
    holding the ``select_for_update`` lock on the user row. Every backend
    reads the same on a green test run; only production would differ.

    Args:
        route: The route to sample.

    """
    _worker_sample_route_slopes.enqueue(route.pk)
