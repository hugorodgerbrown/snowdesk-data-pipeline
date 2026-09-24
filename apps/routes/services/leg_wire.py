"""
apps/routes/services/leg_wire.py — a route's legs as they go to a client.

SNOW-1018. ``apps.routes.services.legs.detect_legs`` cuts a route at its
transitions and answers in indices into ``Route.points``. The rail draws
the route's profile against the SLOPE record instead — the ``angles``
array the map already holds, one entry per 25 m segment — and the cursor
(``static/js/route_cursor_core.js``) is keyed on that array's index. So a
leg goes on the wire as two SAMPLE indices, never as two point indices.

**THE TWO INDEX SPACES ARE NOT THE SAME LENGTH, AND NOT PROPORTIONAL.**
The slope record is resampled on its own fixed stride
(``slope_segments.stride_distances``), while the stored points fall
wherever the recording device put them — every 4 m on one canonical
track and every 19 m on another, unevenly within each. A point index
copied across as a segment index would land on the wrong ground, and on
a long track run off the end of ``angles`` altogether. So each transition
is mapped by ALONG-TRACK DISTANCE: the transition point's distance down
the stored track, measured with the sampler's own
``cumulative_distances``, placed against the segment boundaries the
sampler's own ``stride_distances`` produced. Using the sampler's two
functions is what makes the two distance scales one scale.

Placed in its own module rather than inside ``slope_wire.compact_slope``
because it needs ``Route.points``, which ``compact_slope`` does not take —
and ``compact_slope`` has a second caller (the trip page) that has no use
for legs.

## The wire shape

``[{"i": 1, "from": 0, "to": 41, "climbing": true}, …]`` — ``from`` and
``to`` are the first and last segment of the leg, BOTH INCLUSIVE, so the
legs tile ``angles`` exactly: the first ``from`` is 0, the last ``to`` is
N − 1, and each ``from`` is the previous ``to`` plus one. Unlike
``Leg.start``/``Leg.end``, adjacent legs do not share a boundary here: a
segment is a stretch of ground and belongs to one leg, where a point is a
boundary and belongs to both.
"""

from __future__ import annotations

import bisect
import logging
from typing import Any

from apps.routes.services.legs import detect_legs
from apps.routes.services.slope_segments import (
    cumulative_distances,
    stride_distances,
)

logger = logging.getLogger(__name__)


def _segment_boundaries(
    total_m: float, samples: dict[str, Any], segment_count: int
) -> list[float]:
    """Return the along-track distance of each segment boundary.

    The sampler's own walk, re-run at the record's own ``stride_m`` over
    the track's own length. When that walk does not reproduce the stored
    segment count — a record written without a stride, or points that are
    not the ones the record was sampled from — the boundaries are spread
    evenly along the track instead, which is the same share-of-length
    convention the client uses to place a sample index on the profile.

    Args:
        total_m: The stored track's length in metres.
        samples: The stored slope record.
        segment_count: How many segments the record holds.

    Returns:
        ``segment_count + 1`` ascending distances from 0.0 to ``total_m``.

    """
    stride_m = samples.get("stride_m")
    if isinstance(stride_m, int | float) and stride_m > 0:
        boundaries = stride_distances(total_m, float(stride_m))
        if len(boundaries) == segment_count + 1:
            return boundaries
        logger.debug(
            "leg wire: stride walk gave %d boundaries for %d segments; "
            "placing legs by share of length",
            len(boundaries),
            segment_count,
        )
    return [total_m * index / segment_count for index in range(segment_count + 1)]


def _nearest_boundary(boundaries: list[float], distance_m: float) -> int:
    """Return the index of the segment boundary nearest ``distance_m``.

    Nearest rather than the enclosing segment's start, so a transition
    that falls late in a segment hands that segment to the leg it mostly
    belongs to.

    Args:
        boundaries: Ascending boundary distances.
        distance_m: A distance along the track.

    Returns:
        An index into ``boundaries``.

    """
    position = bisect.bisect_left(boundaries, distance_m)
    if position <= 0:
        return 0
    if position >= len(boundaries):
        return len(boundaries) - 1
    before = distance_m - boundaries[position - 1]
    after = boundaries[position] - distance_m
    return position - 1 if before <= after else position


def wire_legs(
    points: list[list[float | None]], samples: dict[str, Any] | None
) -> list[dict[str, Any]] | None:
    """Return a route's legs in slope-sample indices, for the map's rail.

    A leg that collapses to no segment at all — two transitions inside one
    25 m stride — is dropped, and the neighbours it separated merge if
    they now read the same way, so coverage stays exact and contiguous and
    no two adjacent legs are both climbs or both descents.

    Args:
        points: ``Route.points`` — ``[lon, lat, ele]`` in stored order.
        samples: The row's ``slope_samples``, or None if never sampled.

    Returns:
        ``[{"i", "from", "to", "climbing"}, …]``, or None when there is
        nothing to send: never sampled (no ``slope`` means no ``legs``), a
        record with no segments, or a track ``detect_legs`` finds no leg
        in.

    """
    if not samples:
        return None
    segment_count = len(samples.get("segments") or [])
    if segment_count == 0:
        return None

    legs = detect_legs(points)
    if not legs:
        return None

    cumulative = cumulative_distances(points)
    boundaries = _segment_boundaries(cumulative[-1], samples, segment_count)

    # One cut per leg start: leg k covers segments cuts[k] .. cuts[k+1] - 1.
    cuts = [0]
    cuts.extend(
        _nearest_boundary(boundaries, cumulative[leg.start]) for leg in legs[1:]
    )
    cuts.append(segment_count)

    spans: list[list[Any]] = []
    for index, leg in enumerate(legs):
        first, last = cuts[index], cuts[index + 1] - 1
        if last < first:
            continue
        if spans and spans[-1][2] == leg.climbing:
            spans[-1][1] = last
            continue
        spans.append([first, last, leg.climbing])

    return [
        {"i": number, "from": first, "to": last, "climbing": climbing}
        for number, (first, last, climbing) in enumerate(spans, start=1)
    ]
