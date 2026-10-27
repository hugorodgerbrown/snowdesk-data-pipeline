"""
apps/routes/services/estimate.py — how long a planned route takes (SNOW-1258).

A drawn route has no recording, so the only duration it can carry is an
ESTIMATE, and it must read as one everywhere ("≈ 4 h 10"), never in the
slot an uploaded route uses for its recorded elapsed time.

THE RULE, decided 27 Oct 2026. The DIN 33466 walking-time rule that the SAC
also teaches, for the climb and the flat:

* horizontal distance at 4 km/h;
* ascent at 300 m/h;
* total = the larger of the two, plus half the smaller.

A DESCENT ON SKIS counts at 1,500 m/h rather than DIN's 500 m/h on foot,
because a touring descent is skied, not walked; it is added to the vertical
half of the rule beside the ascent. The result is rounded to ten minutes:
an estimate quoted to the minute claims a precision the rule does not have.

Null in, null out: a route with no known climb (outside the terrain model's
coverage, or not yet sampled) gets no estimate, because a rule applied to a
flat track would quote a walking time for a mountain.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

HORIZONTAL_M_PER_H = 4_000.0
ASCENT_M_PER_H = 300.0
SKI_DESCENT_M_PER_H = 1_500.0
ROUND_TO_S = 600


def estimate_duration_s(
    distance_m: float, ascent_m: float | None, descent_m: float | None
) -> int | None:
    """Return the estimated time for a route in seconds, or None.

    Args:
        distance_m: The route's length along the ground, in metres.
        ascent_m: Total climb in metres, or None when unknown.
        descent_m: Total descent in metres, or None when unknown.

    Returns:
        Whole seconds rounded to the nearest ten minutes (never below ten
        minutes for a route of any length), or None when the climb is
        unknown.

    """
    if ascent_m is None or descent_m is None:
        return None
    horizontal_h = max(distance_m, 0.0) / HORIZONTAL_M_PER_H
    vertical_h = (
        max(ascent_m, 0.0) / ASCENT_M_PER_H + max(descent_m, 0.0) / SKI_DESCENT_M_PER_H
    )
    hours = max(horizontal_h, vertical_h) + min(horizontal_h, vertical_h) / 2
    if hours <= 0:
        return None
    rounded = round(hours * 3600 / ROUND_TO_S) * ROUND_TO_S
    return max(rounded, ROUND_TO_S)
