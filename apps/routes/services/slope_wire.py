"""
apps/routes/services/slope_wire.py — a slope record as it goes to a client.

One function, ``compact_slope``, and it is the ONE place the stored
record is reduced to — and, since SNOW-964, DERIVED FROM — for what a map
draws. The reduction was the whole job until the no-fall passages, which
are computed here on every read rather than stored (see
``apps.routes.services.passages`` for why a stored one would manufacture
a backfill candidate). It lived in
``apps/routes/views.py`` until SNOW-962 gave the trip page a coloured
line of its own; a second caller in another app is what moved it here,
rather than a second copy of a reduction whose two halves have to agree
on an invariant.

The stored record is the server-side truth SNOW-911 and SNOW-839 read —
an aspect and a named unknown reason per segment. The wire form is the
subset MapLibre paints. They are deliberately not the same shape; see
docs/decisions/a-slope-segment-is-the-shared-record.md.

``seams`` (SNOW-1053) tie the record's boundaries to the geometry the
same feature carries, so the map can draw each slope-class segment along
the track's real coordinates rather than as a straight 25 m chord between
two boundaries. See ``_seams``.
"""

from __future__ import annotations

import bisect
import logging
from typing import Any

from apps.routes.services.bank import bank_angles
from apps.routes.services.fall_line import aspect_sectors, fall_line_marks
from apps.routes.services.passages import route_passages
from apps.routes.services.slope_segments import cumulative_distances, stride_distances

logger = logging.getLogger(__name__)

# How far past a boundary, in metres along the track, a coordinate may
# measure and still be taken as that boundary. Boundary coordinates are
# stored to six decimals (``slope_segments._COORDINATE_PRECISION``), about
# 0.08 m of error at worst, so a merged boundary re-measures within this.
# Any backtrack it allows is under a pixel at z18 (about 0.4 m a pixel).
_SEAM_TOLERANCE_M = 0.15


def compact_slope(
    samples: dict[str, Any] | None,
    *,
    coordinates: list[list[float | None]] | None = None,
) -> dict[str, Any] | None:
    """Reduce a stored slope record to what the map actually draws.

    THE STORED RECORD AND THE WIRE FORM ARE DELIBERATELY DIFFERENT.
    ``Route.slope_samples`` is the server-side truth SNOW-911 and SNOW-839
    read, and it carries an aspect and a named unknown reason per segment.
    The map needs neither in that form: it paints one colour per angle
    band and one dashed treatment for every unknown, whatever the
    reason. On a 15 km
    tour that is several hundred segments, and sending the full record
    would roughly double a payload the offline cache has to hold.

    So ``angles`` is a flat list, one per segment, with **null for an
    unknown**. That null means "sampled, no answer" and is safe here
    precisely because the key's PRESENCE already carries the other fact:
    a never-sampled route has no ``slope`` property at all. The two are
    distinguishable on the client, which is the rule
    ``Route.slope_samples``' help_text sets and the map's two layers
    depend on. ``Trip.slope_samples`` carries the same rule.

    Args:
        samples: The row's ``slope_samples``, or None if never sampled.

    A record sampled before SNOW-1066 may still hold a ``cruxes`` list
    (SNOW-911). It is NOT sent: Snowdesk keeps no route-level crux, and
    where the dangerous ground is is the daily avalanche terrain layer's
    question (SNOW-979). The stored key is left in place and ignored.

    ``fall_lines`` are the fall-line marks — which way the ground under
    the track falls, at a point every few hundred metres of steep ground.
    DERIVED HERE on the ``passages`` terms and for the same reasons
    (``apps.routes.services.fall_line``). This is where the decision
    doc's "aspect is stored and not sent" stopped being true, and it is
    sent as a BEARING PER PLACE rather than as the per-segment aspect the
    record holds: a flat ``aspects`` array beside ``angles`` would have
    been the doubled payload that doc rejects, and 600 arrows is not a
    drawing anyone can read. (SNOW-976 later sent that array after all,
    binned to eight sectors, for a reader other than the arrows — see
    ``aspects`` below.) The key is present whenever the record could
    be read at all, and **empty means nothing qualified** — a complete
    answer, the ``passages`` rule again.

    ``banks`` (SNOW-1021) are the bank angle — how far the ground tilts
    ACROSS the track, signed, positive where it falls away on the skier's
    right. DERIVED HERE on the same terms (``apps.routes.services.bank``).
    Unlike ``fall_lines`` this IS a flat per-segment list aligned with
    ``angles``, one whole degree each and **null for an unknown**, and the
    difference is the drawing: the ribbon places a tick every few pixels
    on a zoomed leg, so marks thinned by distance would still be needed
    about every other segment, at five times the bytes of a bare integer.
    ``docs/decisions/the-bank-angle-is-drawn-signed.md`` has the count.
    Present whenever the record could be read at all.

    ``aspects`` (SNOW-976) are the ground's aspect per segment, the field
    the ``fall_lines`` paragraph above says the record holds and the wire
    did not. It travels now because the aspect wheel (SNOW-1063) shows
    which way the ground faces under the rail cursor, and the cursor sits
    on moderate ground as often as on steep: the 30 degree arrow gate
    would leave the wheel blank on a 20 degree slope. The cut-off is
    ``ASPECT_FLAT_DEG`` (5°), the rail's own flat ground, below which the
    aspect is noise. It is sent as a SECTOR INDEX, 0 (N) to 7 (NW), not
    as degrees, because the wheel draws eight sectors and nothing on the
    page reads a finer bearing — the arrows keep their own whole degree.
    A flat list aligned with ``angles``, the ``banks`` shape, **null**
    where the angle is unknown or under 5° or there is no aspect: about
    two bytes a segment, 1.3 kB on the 638-segment Col de la Chaux
    canonical tour against 2.0 kB for its ``banks``. Present whenever the
    record could be read at all.

    ``passages`` (SNOW-964) are the stretches where the TRACK is on
    no-fall ground, and they are DERIVED HERE rather than read out of the
    record: both their inputs are already stored, so caching them would
    freeze today's threshold into a row and make re-tuning a backfill.
    Each one is two inclusive segment indices into the ``angles`` array
    the client already holds — never a second copy of the geometry, which
    could disagree with the first — the length in metres, and a word for
    what the track does with the fall line. **Empty means nothing
    qualified**, and that is always a complete answer: a passage needs
    no probe, so there is no "we could not look" state.

    ``seams`` (SNOW-1053) are one coordinate index per boundary: the
    last coordinate of ``coordinates`` — the geometry the SAME feature
    carries — at or before that boundary. With them the client draws a
    segment as its two boundary points with the coordinates between them,
    so a class segment lies on the leg casing instead of cutting a 25 m
    chord across a bend (up to 9.9 m off on the Hidden Valley canonical
    track). Present only when the caller passes ``coordinates`` and the
    stride walk can be repeated over them; absent, the client keeps
    drawing chords, which is what an offline-cached payload from before
    this key still does.

    Args:
        samples: The row's ``slope_samples``, or None if never sampled.
        coordinates: The ``LineString`` coordinates the feature carries
            beside this value (``terrain_points``' output), or None for a
            caller that sends no geometry, which gets no ``seams``.

    Returns:
        ``{"points": [[lon, lat], …], "angles": [34.2, None, …]}``, plus
        ``passages``,
        ``fall_lines``, ``banks`` and ``aspects`` whenever the record
        could be read at all, and ``seams`` where ``coordinates`` allow
        them. None when there is nothing to draw — never sampled, or a record whose
        halves do not pair up (N + 1 coordinates to N angles), which
        would draw segments against the wrong ground.

    """
    if not samples:
        return None

    points = samples.get("points") or []
    segments = samples.get("segments") or []
    if len(points) != len(segments) + 1:
        logger.warning(
            "route slope record is malformed: %d point(s) to %d segment(s)",
            len(points),
            len(segments),
        )
        return None

    passages = route_passages(samples)
    fall_lines = fall_line_marks(samples)
    banks = bank_angles(samples)
    aspects = aspect_sectors(samples)
    seams = (
        _seams(coordinates, samples, len(points)) if coordinates is not None else None
    )
    return {
        "points": points,
        "angles": [segment.get("angle_deg") for segment in segments],
        # A missing ``passages`` is a record this function has already
        # refused above. ``route_passages`` can only answer None on a
        # record the pairing check has rejected, so in practice the key is
        # always present here.
        **({"passages": passages} if isinstance(passages, list) else {}),
        # The ``passages`` rule a third time, and the same note applies:
        # ``fall_line_marks`` can only answer None on a record the
        # pairing check above has already refused, so in practice the key
        # is always present here.
        **({"fall_lines": fall_lines} if isinstance(fall_lines, list) else {}),
        # And a fourth: ``bank_angles`` refuses exactly the records the
        # pairing check has refused, plus an empty one, which has nothing
        # to align with anyway.
        **({"banks": banks} if isinstance(banks, list) else {}),
        # A fifth, and ``aspect_sectors`` refuses the same records
        # ``bank_angles`` does.
        **({"aspects": aspects} if isinstance(aspects, list) else {}),
        # Absent rather than null when they cannot be placed: the client
        # tests the key and falls back to chords, as it does for a
        # payload cached before SNOW-1053.
        **({"seams": seams} if seams is not None else {}),
    }


def _seams(
    coordinates: list[list[float | None]],
    samples: dict[str, Any],
    boundary_count: int,
) -> list[int] | None:
    """Return, per boundary, the last coordinate index at or before it.

    Re-walks ``coordinates`` with the sampler's own two functions, so the
    boundaries land where the record's were placed. The coordinates may be
    the stored track or ``terrain_points``' merge of it with the
    boundaries; the merged boundary points lie on the stored polyline, so
    either walk measures the same distances to within rounding.

    ``_SEAM_TOLERANCE_M`` absorbs that rounding, so a merged boundary
    re-measured a hair past its own distance is still its own seam. It is
    deliberately far tighter than the half metre ``terrain_points`` drops
    a boundary on a vertex by: a tolerance that wide would pull a vertex
    lying just PAST a boundary into the segment ending there, and the
    drawn path would run out to the vertex and double back. A vertex past
    the tolerance falls in the next segment instead, which starts at the
    boundary point on the line, so nothing is drawn twice.

    Args:
        coordinates: The feature's ``LineString`` coordinates.
        samples: The slope record, read for ``stride_m``.
        boundary_count: How many boundary points the record holds.

    Returns:
        ``boundary_count`` non-decreasing indices, the first 0 and the
        last ``len(coordinates) - 1``; or None when the stride is
        unusable, the track has no length, or the walk lands a different
        number of boundaries than the record holds (these are not the
        coordinates it was sampled along).

    """
    stride_m = samples.get("stride_m")
    if (
        not isinstance(stride_m, int | float)
        or isinstance(stride_m, bool)
        or stride_m <= 0
    ):
        return None
    cumulative = cumulative_distances(coordinates)
    if not cumulative or cumulative[-1] <= 0:
        return None
    boundaries = stride_distances(cumulative[-1], float(stride_m))
    if len(boundaries) != boundary_count:
        return None
    last = len(coordinates) - 1
    seams = [
        min(
            max(bisect.bisect_right(cumulative, boundary + _SEAM_TOLERANCE_M) - 1, 0),
            last,
        )
        for boundary in boundaries
    ]
    # The walk's first and last boundaries are the track's ends by
    # construction. Pinned rather than trusted to float comparison: a
    # last boundary measured a hair short of the track's end, or a
    # trailing zero-length step, would otherwise leave a coordinate
    # outside every segment.
    seams[0] = 0
    seams[-1] = last
    return seams
