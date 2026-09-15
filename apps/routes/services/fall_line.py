"""
apps/routes/services/fall_line.py — which way the ground under a route falls.

The FOURTH thing a saved route says about terrain, and the first that is
about a direction rather than a magnitude. SNOW-910 colours each 25 m
segment by the steepness of the ground under it; SNOW-911 rings the
passages where the ground around the skier can release; SNOW-964 splits
the line where the track itself is on no-fall ground and names, in words,
what the track does with the fall line there.

None of them says which way the fall line actually runs — so a reader
looking at a 38 degree stretch cannot tell from the map whether the track
is cutting across the face or pointed down it, and on the ground that is
the difference between a traverse and a committing line.

This module answers that with a MARK PER PLACE: a bearing at a point on
the track, drawn as an arrow pointing the way the ground falls. The
bearing is the segment's stored ``aspect_deg`` — the direction the ground
FACES, which is the direction water runs and a slab slides, therefore
downhill.

## The aspect was always there and nothing drew it

``sample_slope`` computes the angle and the aspect from one kernel, so
the record has carried both since SNOW-910. Only SNOW-839's bulletin
scoring and SNOW-964's alignment vote have ever read the aspect, both
server-side. ``docs/decisions/a-slope-segment-is-the-shared-record.md``
carried a section titled "Aspect is stored and not sent" whose closing
line was "Nothing draws it"; this module is what made that false, and
that doc records the change rather than being quietly contradicted.

## Derived at read time, and nothing new is stored

The rule ``apps.routes.services.passages`` established, for its reasons:
the input is already in the row, so a stored mark list would cache a pure
function of it — and worse, it would manufacture a backfill candidate,
because ``backfill_route_slope_samples`` selects on a missing key. A
re-tune of either constant below would then cost a full re-walk of the
tile origin. Both are keyword arguments, which is the mechanical proof
that re-tuning is free.

**MARKS MUST NEVER ENTER ``summary``**, for the same reason: that key is
written by the sampler and read back verbatim.

## No arrow on gentle ground, and that is not a claim it is flat

The gate is ``FALL_LINE_GATE_DEG``. Below it there is no mark at all —
not a shorter arrow, not a paler one. Two reasons, and the second is the
important one:

- An aspect sampled on near-level ground is noise. A 5 m grid gives a
  two degree valley floor a confident bearing off a stream bank or a
  road cutting, and drawing it would be a claim with no content in it.
- Arrows are a DENSITY, not a list. Paint one on every gentle stretch of
  a 15 km tour and the reader stops seeing them, which costs them the
  ones on the 40 degree face. Withholding the meaningless ones is what
  makes the rest legible.

The surfaces have to carry that, because the absence of a mark is
readable as an absence of steep ground. The legend row and
``/help/#help-topic-slope`` say so; the colour of the line underneath is
the part that never goes quiet.

## One mark per stretch, then one every ``FALL_LINE_SPACING_M``

A mark per segment would be 600 arrows on a long tour: unreadable on
screen and a payload the offline cache has to hold. So a run of steep
segments gets a mark at its first classifiable segment and then one
every spacing thereafter, and the count resets at every gap — a spacing
carried across gentle ground would put the next arrow at an arbitrary
point of the next steep stretch rather than at its start.

The consequence worth stating: **a lone 25 m roll gets exactly one
arrow**, which is the common case and the one a length-based minimum
would have dropped.

Thinning further is the CLIENT's job, and MapLibre's collision engine
does it for free — see the layer note in ``static/js/map.js``. A dropped
arrow costs nothing, because the remaining ones say the same thing about
the same face; that is precisely the opposite of a dropped crux ring,
which would understate the day.

## A mark is an index and a bearing, never a second geometry

``{"i": 12, "deg": 112}``. ``i`` indexes the same ``angles`` array the
client already holds — the shape ``passages`` uses for its ``from`` and
``to`` — so the arrow is placed on geometry that is already on the page
and cannot disagree with the colour under it.

The bearing is a WHOLE number of degrees. The arrow is drawn 20 CSS
pixels tall, where a tenth of a degree moves the tip by a hundredth of a
pixel, and rounding is also the honest reading of a bearing taken from a
10 m analysis window.

## An unknown segment never carries a mark

It has no ``angle_deg``, so it fails the gate and ends the run — the
rule every module in this family follows. Unsurveyed ground is not
ground we may point at.
"""

from __future__ import annotations

from typing import Any

from apps.routes.services.slope_summary import segment_lengths_m

# The angle at and above which the ground's fall line is worth drawing.
#
# Thirty degrees, and deliberately the number the rest of the product
# already teaches: where the raster starts painting, where the popup's
# steep length is counted from, and where the help page draws the line
# between gentle and not. A gate of its own here would make the map argue
# with itself — an arrow on ground the colour scale calls gentle, or a
# gentle-looking stretch with no arrow on it just above the threshold the
# popup quotes.
#
# It is NOT an import of ``STEEP_THRESHOLD_DEG``, and the difference is
# not pedantry: that constant is what a length is COUNTED against, this
# is what a mark is DRAWN at, and either may move without the other. An
# import would make one product decision out of two and hide the second
# from whoever re-tunes the first.
#
# What holds them together is a TEST rather than an import —
# ``tests/routes/test_fall_line.py`` asserts the two are equal, the same
# mechanism ``test_slope_summary.py`` uses to keep ``SLOPE_BANDS`` and
# the JavaScript CLASSES in step. So a deliberate divergence is one
# edited assertion with a reason beside it, and an accidental one is a
# red build.
FALL_LINE_GATE_DEG = 30.0

# How much steep track separates two marks, in metres.
#
# Two hundred and fifty: ten strides. The number is set by what a reader
# can take in rather than by anything about the ground — at the zoom a
# track is actually read at (z14 and in) it puts the arrows about 40
# pixels apart, which reads as a sequence of marks rather than as a
# textured line, and at country scale the client's collision engine
# thins what is left.
#
# It also bounds the cost: an all-steep 15 km tour carries about 60
# marks, roughly a kilobyte of the payload the offline cache holds,
# against the 13 kilobytes its own boundary coordinates already take.
# That is the number the payload objection in
# ``docs/decisions/a-slope-segment-is-the-shared-record.md`` is answered
# with, so a much smaller spacing needs that doc revisited rather than
# just this constant changed.
FALL_LINE_SPACING_M = 250.0


def fall_line_marks(
    record: dict[str, Any] | None,
    *,
    gate_deg: float = FALL_LINE_GATE_DEG,
    spacing_m: float = FALL_LINE_SPACING_M,
) -> list[dict[str, Any]] | None:
    """Return the fall-line marks of one stored slope record.

    Takes the WHOLE record, mirroring ``summarise_record`` and
    ``route_passages``, so it can read the walk's own ``stride_m``
    through ``segment_lengths_m`` rather than importing a constant from
    the sampler and hoping the two agree.

    **BOTH GATES ARE KEYWORD ARGUMENTS**, with the module constants as
    their defaults, which is what makes re-tuning free of a backfill (see
    the module docstring).

    Args:
        record: A ``Route.slope_samples`` (or ``Trip.slope_samples``)
            value, or None for a track that has never been sampled.
        gate_deg: The angle a segment's ground must reach to be marked.
        spacing_m: How much steep track separates two marks within one
            run. A non-positive value marks every qualifying segment,
            which is what a sweep asking "how dense could this be?"
            wants; the constant is never that.

    Returns:
        One dict per mark, in track order::

            [{"i": 12, "deg": 112}, {"i": 22, "deg": 118}]

        ``i`` is an index into the record's ``segments``, which is the
        same index space as the ``angles`` array the client already
        holds. ``deg`` is the ground's aspect there, rounded to a whole
        compass degree in ``[0, 360)``.

        An empty list means nothing qualified, which — like a passage
        list and unlike ``cruxes`` — is always a complete answer: the
        marks are derivable from the record at any constants, so there is
        no "we could not look" state to encode.

        None means there was nothing to read: never sampled, or a record
        whose halves do not pair up, which would point an arrow at the
        wrong ground.

    """
    if not record:
        return None

    points = record.get("points") or []
    segments = record.get("segments") or []
    if len(points) != len(segments) + 1 or not segments:
        return None

    lengths_m = segment_lengths_m(record)
    marks: list[dict[str, Any]] = []
    # Metres of steep track since the last mark. Seeded at the spacing so
    # the first segment of a run is always due one — which is what gives
    # a single-segment run its mark, and is why this is a counter rather
    # than an interval walk over the whole track.
    since_m = spacing_m
    for index, segment in enumerate(segments):
        angle_deg = segment.get("angle_deg")
        if not isinstance(angle_deg, int | float) or angle_deg < gate_deg:
            # Gentle ground and unsurveyed ground both END THE RUN, and
            # the next steep stretch starts due a mark of its own.
            since_m = spacing_m
            continue
        if since_m >= spacing_m:
            bearing = _bearing_of(segment)
            if bearing is not None:
                marks.append({"i": index, "deg": bearing})
                # Reset only on a mark actually emitted. A steep segment
                # with no aspect — which the sampler cannot produce, but
                # a hand-written record can — must not consume the
                # spacing and silence the run's only arrow.
                since_m = 0.0
        since_m += lengths_m[index]
    return marks


def _bearing_of(segment: dict[str, Any]) -> int | None:
    """Return one segment's aspect as a whole compass degree.

    Args:
        segment: One entry of the record's ``segments``.

    Returns:
        The bearing in ``[0, 360)``, or None when the segment carries no
        aspect. ``TerrainSlope`` guarantees a sloping sample has one, so
        in practice that is a hand-written record — but a null reaching
        ``icon-rotate`` would be read as due north, which is the one
        wrong answer that looks like a right one.

    """
    aspect_deg = segment.get("aspect_deg")
    if not isinstance(aspect_deg, int | float):
        return None
    # Normalised AFTER rounding: 359.7 rounds to 360, which is due north
    # and must be reported as 0 rather than as a bearing off the end of
    # the compass.
    return round(aspect_deg) % 360
