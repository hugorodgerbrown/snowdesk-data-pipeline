"""
apps/routes/services/bank.py — how far the ground tilts across a track.

SNOW-1021. A traverse across a 42 degree face and a descent straight down
it read identically on every surface a route has: the line is coloured
by the ground's angle, and the angle is the same under both. What tells
them apart is how the ground's steepness is SPLIT between the direction
of travel and the direction across it.

On a plane that split is exact. With δ the angle from the track's bearing
to the ground's aspect (δ = aspect − bearing):

* ``tan(pitch) = tan(slope) × cos(δ)`` — along the track, which the
  profile already draws as its own gradient;
* ``tan(roll)  = tan(slope) × sin(δ)`` — across it, the BANK ANGLE, and
  the figure this module derives.

So ``tan²(pitch) + tan²(roll) = tan²(slope)``: nothing is lost, the
slope is decomposed into the two directions a skier feels it in. A level
traverse of a 40 degree face banks at 40 degrees and does not pitch; a
fall-line descent pitches at 40 and does not bank.

## The roll is SIGNED, and that is the feature

Positive means the ground falls away on the skier's RIGHT — the aspect is
clockwise of the heading, ``sin(aspect − bearing) > 0``. Negative, the
left. Reversing the track flips the sign and keeps the magnitude.

**DO NOT WRAP IT IN ``abs()``.** The sign was argued both ways and kept on
purpose; ``docs/decisions/the-bank-angle-is-drawn-signed.md`` records the
argument and the measurement that settled it. A magnitude alone would say
"steep across" and never which shoulder the slope falls from, which is
the thing a switchback sequence is made of.

## Derived at read time, and nothing new is stored

The rule ``apps.routes.services.fall_line`` and ``passages`` follow, for
their reasons: every input is already in the record — the angle and the
aspect per segment, and the boundaries whose chord gives the bearing — so
a stored roll would cache a pure function of it and manufacture a
backfill candidate. **NOTHING HERE ENTERS ``summary``.**

## Gentle ground limits itself, so there is no gate

``|roll| ≤ slope`` at every heading, because ``|sin δ| ≤ 1``. A 20 degree
face can never bank a track by more than 20 degrees. Unlike the
fall-line arrow, whose bearing on gentle ground is noise off a stream
bank, a small roll on gentle ground is a small number and draws as a
near-vertical tick; it needs no threshold to stop it lying.

## An unknown segment has no roll

No angle, no aspect, nothing to decompose — the rule every module in this
family follows. Level ground (a known angle with no aspect) likewise has
none rather than a zero: it faces nowhere, so it cannot fall to a side.
"""

from __future__ import annotations

import math
from typing import Any

from apps.core.geo import initial_bearing_deg


def bank_angle_deg(
    angle_deg: float | None,
    aspect_deg: float | None,
    bearing_deg: float | None,
) -> float | None:
    """Return the signed bank angle of a track crossing a plane.

    **SIGNED ON PURPOSE (SNOW-1021).** Positive when the ground falls
    away on the skier's right, negative on the left. Taking the absolute
    value would delete the feature, not tidy it: the decision in
    ``docs/decisions/the-bank-angle-is-drawn-signed.md`` is that the sign
    is what a reader reads.

    Args:
        angle_deg: The ground's slope angle, in degrees.
        aspect_deg: The direction the ground faces — downhill — as a
            compass bearing.
        bearing_deg: The track's direction of travel, as a compass
            bearing.

    Returns:
        ``atan(tan(slope) × sin(aspect − bearing))`` in degrees, in
        ``[-angle, angle]``. None when any input is missing: an unknown
        segment, level ground with no aspect, or a chord with no
        direction.

    """
    if angle_deg is None or aspect_deg is None or bearing_deg is None:
        return None
    delta = math.radians(aspect_deg - bearing_deg)
    return math.degrees(math.atan(math.tan(math.radians(angle_deg)) * math.sin(delta)))


def bank_angles(record: dict[str, Any] | None) -> list[int | None] | None:
    """Return one whole signed bank angle per segment of a slope record.

    The wire form ``compact_slope`` sends as ``banks``: a flat list
    aligned with ``angles``, rather than thinned marks, because the
    drawing places a tick every few pixels on a zoomed leg and would
    need most of them anyway — see the decision doc for the byte count.

    The bearing is the chord between the segment's two stored
    boundaries, the measurement ``terrain_detail`` and ``passages`` use,
    so the three can never be reading two different directions of
    travel.

    Args:
        record: A ``Route.slope_samples`` (or ``Trip.slope_samples``)
            value, or None for a track that has never been sampled.

    Returns:
        One whole degree per segment, in track order, positive where the
        ground falls to the right. None for a segment with no roll (see
        ``bank_angle_deg``). None overall when there is nothing to read
        — never sampled, or a record whose boundaries and segments do
        not pair up — the refusal ``fall_line_marks`` makes, for the same
        reason: a roll placed against the wrong ground.

    """
    if not record:
        return None
    boundaries = record.get("points") or []
    segments = record.get("segments") or []
    if len(boundaries) != len(segments) + 1 or not segments:
        return None

    banks: list[int | None] = []
    for index, segment in enumerate(segments):
        # (lat, lon), the house argument order — the record stores
        # GeoJSON axis order, so the pairs are swapped at the call.
        bearing = initial_bearing_deg(
            boundaries[index][1],
            boundaries[index][0],
            boundaries[index + 1][1],
            boundaries[index + 1][0],
        )
        roll = bank_angle_deg(
            _number(segment.get("angle_deg")),
            _number(segment.get("aspect_deg")),
            bearing,
        )
        # Whole degrees: the tick leans by the roll, and a tenth of a
        # degree moves the top of a 9 px tick by a sixtieth of a pixel.
        banks.append(None if roll is None else round(roll))
    return banks


def _number(value: Any) -> float | None:
    """Return a stored value as a float, or None when it is not a number.

    Args:
        value: One field of a stored segment.

    Returns:
        The float, or None.

    """
    return float(value) if isinstance(value, int | float) else None
