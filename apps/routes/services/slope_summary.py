"""
apps/routes/services/slope_summary.py — a sampled route's terrain in figures.

SNOW-910 gave a route six colours and no numbers. This module turns the
record ``apps.routes.services.slope_segments`` stores into the figures a
person plans with — how much of the day is steep, how steep it gets, and
how much of it we could not survey — and nothing else.

The summary is computed ONCE, by the sampler, and stored under the
record's ``summary`` key. Every reader takes it from there rather than
re-deriving it: the popup, the route row, and (next) SNOW-911's marker
count and SNOW-839's coverage caveat are all quoting the same day back to
the user, and a figure derived in four places is a figure that will
eventually disagree with itself.

``summarise_record`` is the one entry point, and it falls back to
computing the summary when the key is absent. That fallback is what
carries every route sampled before this module existed without a backfill
pass over the tile origin — the record already holds everything the
summary needs, so re-reading terrain to recover a figure we can count
would be a second pass for nothing.

**THE TWO LENGTH SOURCES, AND WHY THEY DIFFER.** A segment's length is
the along-track distance between its two boundaries. At write time the
sampler has those boundaries exactly (``stride_distances`` produced
them), so it passes their deltas. At read time all that survives is the
boundary COORDINATES, so the fallback measures the straight line between
consecutive ones — which on a switchback is fractionally shorter than the
track it stands in for. Both feed the same ``summarise``; only the
lengths differ, and the difference is bounded by a chord across a 25 m
stride. It is why the sampler stores the answer rather than leaving every
reader to the approximation.

**THE TOTAL IS THE SAMPLED WALK'S OWN LENGTH, NEVER ``Route.distance_m``.**
That field measures the FULL-RESOLUTION track (``apps/routes/services/
gpx.py``), while the sampler walks the simplified one, so the two are
close but not equal. Mixing them would let a fully-surveyed route report
a few metres unsurveyed. Nothing here reads the route at all.

**A ZERO IS NOT REPORTED IN A CLASS WE DID NOT MEASURE.** Where nothing
was surveyed there are no classes, not six zeroes: a zero is a claim about
the ground, and an all-unknown record supports none. The same rule as
``apps/locations/services/terrain.py``'s — an unknown is a reason, never a
null, and never a zero either.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from apps.core.geo import haversine_m


@dataclass(frozen=True)
class SlopeClass:
    """One steepness class: an id, and the angles that fall in it.

    Named CLASS rather than BAND to match
    ``static/js/route_slope_core.js``'s ``CLASSES`` — which this table is
    explicitly a copy of — and to leave "slope band" free for the
    variable-length run of consecutive segments sharing one of these,
    which is the object the route rail draws and a reader presses.

    ``lower_deg`` is inclusive and ``upper_deg`` exclusive, so a sample of
    exactly 35 degrees is in ``slope-35``. That is the raster's own
    convention — its classes are named for their lower bound — and the
    other way round would put every boundary sample one class too gentle,
    which is the direction that matters.
    """

    id: str
    lower_deg: float
    upper_deg: float | None


# The six classes, gentlest first.
#
# THE PYTHON COPY OF ``static/js/route_slope_core.js``'s CLASSES, and the
# copy is deliberate rather than lazy: a MapLibre paint expression cannot
# call Python and this module cannot import JavaScript, so one of the two
# has to be second. ``tests/routes/test_slope_summary.py`` parses
# that file and asserts the two agree, which is what stops them drifting —
# a route coloured by one table and described by another would disagree
# with itself on the same screen.
SLOPE_CLASSES: tuple[SlopeClass, ...] = (
    SlopeClass(id="slope-gentle", lower_deg=0.0, upper_deg=30.0),
    SlopeClass(id="slope-30", lower_deg=30.0, upper_deg=35.0),
    SlopeClass(id="slope-35", lower_deg=35.0, upper_deg=40.0),
    SlopeClass(id="slope-40", lower_deg=40.0, upper_deg=45.0),
    SlopeClass(id="slope-45", lower_deg=45.0, upper_deg=50.0),
    SlopeClass(id="slope-50", lower_deg=50.0, upper_deg=None),
)

# The angle at and above which ground is reported as steep.
#
# Thirty degrees, which is where the raster starts painting and where the
# help page already draws the line: the large majority of slab avalanches
# release between 30 and 45 degrees. It is one figure on a popup, so it
# has to be the figure the rest of the product already teaches.
STEEP_THRESHOLD_DEG = 30.0

# Decimal places kept on a stored length. Metres to one place — finer than
# the 25 m stride the figure is built from, and enough that a kilometre
# reading rounds honestly.
_LENGTH_PRECISION = 1


def class_for_angle(angle_deg: float) -> SlopeClass | None:
    """Return the class an angle falls in.

    Args:
        angle_deg: Degrees from horizontal.

    Returns:
        The matching ``SlopeClass``, or None when the value is not a real
        angle. A non-finite number is not classified rather than being
        forced into the gentle class, because "not a number" and "not
        steep" must not become the same answer.

    """
    if not math.isfinite(angle_deg):
        return None
    # Walked from the steepest end so the open-ended last class needs no
    # special case, and so a negative angle — which the sampler cannot
    # produce, but a hand-written record could — still lands in the
    # gentle bucket rather than falling off the end.
    for slope_class in reversed(SLOPE_CLASSES[1:]):
        if angle_deg >= slope_class.lower_deg:
            return slope_class
    return SLOPE_CLASSES[0]


def segment_lengths_from_points(points: list[list[float]]) -> list[float]:
    """Return the straight-line length of each segment, in metres.

    The read-time half of the pair described in the module docstring: N +
    1 boundary coordinates bound N segments, so this measures between
    consecutive ones.

    Args:
        points: The record's ``points``, as ``[longitude, latitude]``.

    Returns:
        One length per segment, empty when there are fewer than two
        coordinates.

    """
    return [
        # haversine_m takes latitude first (the house rule), and the
        # record stores GeoJSON axis order.
        haversine_m(points[index][1], points[index][0], nxt[1], nxt[0])
        for index, nxt in enumerate(points[1:])
    ]


def segment_lengths_m(record: dict[str, Any]) -> list[float]:
    """Return the along-track length of each segment, in metres.

    The write-time half of the pair described in the module docstring,
    recovered at read time: the sampler's own ``stride_m``, not the chord
    between two boundaries. **THE PROMOTED FORM OF THE RULE.** It was
    private to ``apps.routes.services.passages`` until
    ``apps.routes.services.fall_line`` needed the same lengths to space
    its marks, and a second copy of a subtlety this carefully argued is
    the copy that goes wrong: every reader that measures along a track
    measures it here.

    The LAST segment is the one the stride does not state, because
    ``stride_distances`` absorbs the track's trailing stub into it;
    ``_final_length_m`` recovers it rather than measuring it.

    Args:
        record: The stored record, read for its ``stride_m``, its
            ``points`` and (for the final segment) its ``summary``.

    Returns:
        One length per segment, in track order. Empty for a record with
        fewer than two boundary coordinates, which bounds no segments.

    """
    points = record.get("points") or []
    if len(points) < 2:
        return []
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


def summarise(
    segments: list[dict[str, Any]], lengths_m: list[float]
) -> dict[str, Any] | None:
    """Reduce a walked track to the figures that describe its terrain.

    Args:
        segments: The record's ``segments``, each carrying an
            ``angle_deg`` or an ``unknown`` reason.
        lengths_m: The along-track length of each segment, in the same
            order. Must pair with ``segments``.

    Returns:
        The summary described below, or None when the two arguments do
        not pair up — which would attribute one segment's steepness to
        another's ground, and is the same refusal ``_compact_slope``
        makes on a malformed record::

            {
              "sampled_m": 14210.4,   # the walk's own length
              "surveyed_m": 12803.1,  # of it, the part with an answer
              "steep_m": 2140.0,      # of THAT, the part at or above 30°
              "steepest_deg": 43.2,   # or absent when nothing was surveyed
              "bands": {"slope-gentle": 10663.1, "slope-35": 900.0, …},
            }

        THE STORED KEY IS STILL ``bands``, and stays that way: it is
        written into every sampled ``Route.slope_samples`` record, and
        renaming it to match the symbols would need a backfill over all
        of them to buy nothing — no reader reads it yet. The SYMBOLS
        moved; the data did not.

        ``bands`` carries only the classes with ground in them, so an
        all-unknown record has an empty one rather than six zeroes, and
        ``steepest_deg`` is absent rather than null for the same reason.

    """
    if len(segments) != len(lengths_m):
        return None

    bands: dict[str, float] = {}
    surveyed_m = 0.0
    steep_m = 0.0
    steepest_deg: float | None = None

    for segment, length_m in zip(segments, lengths_m, strict=True):
        angle_deg = segment.get("angle_deg")
        if not isinstance(angle_deg, int | float):
            # An unknown segment contributes its length to the walk and
            # to nothing else — not to a class, and not to the steep
            # figure. It is ground we did not see.
            continue
        slope_class = class_for_angle(float(angle_deg))
        if slope_class is None:
            continue
        surveyed_m += length_m
        bands[slope_class.id] = bands.get(slope_class.id, 0.0) + length_m
        if angle_deg >= STEEP_THRESHOLD_DEG:
            steep_m += length_m
        if steepest_deg is None or angle_deg > steepest_deg:
            steepest_deg = float(angle_deg)

    summary: dict[str, Any] = {
        "sampled_m": round(sum(lengths_m), _LENGTH_PRECISION),
        "surveyed_m": round(surveyed_m, _LENGTH_PRECISION),
        "steep_m": round(steep_m, _LENGTH_PRECISION),
        "bands": {
            band_id: round(metres, _LENGTH_PRECISION)
            for band_id, metres in bands.items()
        },
    }
    if steepest_deg is not None:
        summary["steepest_deg"] = steepest_deg
    return summary


def summarise_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a stored record's summary, computing it when it is absent.

    The one entry point. Readers call this and never ``summarise``, so a
    record written before SNOW-961 answers the same shape as one written
    after it.

    Args:
        record: A ``Route.slope_samples`` value, or None for a route that
            has never been sampled.

    Returns:
        The summary, or None when there is nothing to summarise — never
        sampled, or a record whose halves do not pair up.

    """
    if not record:
        return None

    stored = record.get("summary")
    if isinstance(stored, dict):
        return stored

    segments = record.get("segments") or []
    points = record.get("points") or []
    if len(points) != len(segments) + 1:
        return None
    return summarise(segments, segment_lengths_from_points(points))
