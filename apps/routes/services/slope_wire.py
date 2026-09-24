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
"""

from __future__ import annotations

import logging
from typing import Any

from apps.routes.services.bank import bank_angles
from apps.routes.services.fall_line import fall_line_marks
from apps.routes.services.passages import route_passages

logger = logging.getLogger(__name__)


def compact_slope(samples: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce a stored slope record to what the map actually draws.

    THE STORED RECORD AND THE WIRE FORM ARE DELIBERATELY DIFFERENT.
    ``Route.slope_samples`` is the server-side truth SNOW-911 and SNOW-839
    read, and it carries an aspect and a named unknown reason per segment.
    The map needs neither: it paints one colour per angle band and one
    dashed treatment for every unknown, whatever the reason. On a 15 km
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

    ``cruxes`` (SNOW-911) travel as the COORDINATES ALONE. The angle that
    flagged one stays server-side: a marker says "look here", and a number
    beside it would invite the reader to compare two rings and treat the
    larger as the more dangerous — a severity claim a max-in-an-arc does
    not support. The key is absent rather than empty for a record written
    before cruxes existed, so "nothing was flagged" and "nothing looked"
    stay apart on the client exactly as they do one level up.

    ``fall_lines`` are the fall-line marks — which way the ground under
    the track falls, at a point every few hundred metres of steep ground.
    DERIVED HERE on the ``passages`` terms and for the same reasons
    (``apps.routes.services.fall_line``). This is where the decision
    doc's "aspect is stored and not sent" stopped being true, and it is
    sent as a BEARING PER PLACE rather than as the per-segment aspect the
    record holds: a flat ``aspects`` array beside ``angles`` would have
    been the doubled payload that doc rejects, and 600 arrows is not a
    drawing anyone can read. The key is present whenever the record could
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

    ``passages`` (SNOW-964) are the stretches where the TRACK is on
    no-fall ground, and they are DERIVED HERE rather than read out of the
    record: both their inputs are already stored, so caching them would
    freeze today's threshold into a row and make re-tuning a backfill.
    Each one is two inclusive segment indices into the ``angles`` array
    the client already holds — never a second copy of the geometry, which
    could disagree with the first — the length in metres, and a word for
    what the track does with the fall line. **Empty means nothing
    qualified**, and unlike ``cruxes`` that is always a complete answer:
    a passage needs no probe, so there is no "we could not look" state.

    Args:
        samples: The row's ``slope_samples``, or None if never sampled.

    Returns:
        ``{"points": [[lon, lat], …], "angles": [34.2, None, …]}``, plus
        ``cruxes`` where the record has them, and ``passages``,
        ``fall_lines`` and ``banks`` whenever the record could be read
        at all. None
        when there is nothing to draw — never sampled, or a record whose
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

    cruxes = samples.get("cruxes")
    passages = route_passages(samples)
    fall_lines = fall_line_marks(samples)
    banks = bank_angles(samples)
    return {
        "points": points,
        "angles": [segment.get("angle_deg") for segment in segments],
        **({"cruxes": cruxes} if isinstance(cruxes, list) else {}),
        # The ``cruxes`` rule verbatim, and it reads the same because the
        # two keys mean different things by their absence: a missing
        # ``cruxes`` is an outage, a missing ``passages`` is a record this
        # function has already refused above. ``route_passages`` can only
        # answer None on a record the pairing check has rejected, so in
        # practice the key is always present here.
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
    }
