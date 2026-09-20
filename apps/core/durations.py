"""
apps/core/durations.py — how this project spells an elapsed time.

One function, ``split_hours_minutes``. It lives here rather than on either
model because two of them need it and the two must agree: ``Route`` shows a
recording's elapsed time on the map popup and the routes panel, and ``Trip``
shows the length of the day the trip's source route describes (SNOW-995).
A second copy of the rounding rule below would be one edit away from the
two surfaces disagreeing about the same number, which is exactly the
failure ``Route.duration_hm``'s docstring was written to prevent.

The rule is also mirrored in JavaScript — ``formatDuration`` in
``static/js/map.js`` — because the map popup formats client-side from a
seconds figure on the wire. That copy cannot be removed by extracting this
one, so the agreement between the two is held by tests rather than by
sharing code.
"""

from __future__ import annotations

import math
from datetime import timedelta


def split_hours_minutes(elapsed: timedelta | None) -> dict[str, str] | None:
    """Return an elapsed span split for display, or ``None`` if unknown.

    A display helper: a template cannot divide, and the split has two rules
    a template could not express either way.

    WHOLE MINUTES, ROUNDED HALF UP. A tour is not read to the second, and
    rounding rather than truncating keeps 59.6 minutes from reading as 59.

    ``math.floor(x + 0.5)`` and not the builtin ``round``, which is
    banker's rounding: it breaks a .5 tie to the EVEN number, so
    ``round(270.5)`` is 270 while JavaScript's ``Math.round`` — what
    ``formatDuration`` uses — gives 271. A GPX carries whole-second stamps,
    so a span landing on an exact half-minute is ordinary rather than
    contrived (4h30m30s is one), and on those the popup and the panel row
    would have disagreed by a minute about the same route.

    ``hours`` is the empty string under an hour, and the minutes are NOT
    zero-padded in that case: "0h41m" states an hours figure the recording
    does not have, and an hour count is not a leading zero on a minute
    count. Above an hour the minutes ARE padded, so "4h05m" cannot be
    misread as "4h5m".

    Args:
        elapsed: The span to split, or ``None`` when it is not known.

    Returns:
        ``{"hours": "4", "minutes": "05"}``, ``{"hours": "", "minutes":
        "41"}``, or ``None`` when the span is unknown — in which case the
        caller omits the figure rather than rendering a zero, the same
        contract ``ascent_m``'s null carries. A non-positive span is
        ``None`` too, matching ``formatDuration``: two identical stamps are
        a recording artefact, not a tour that took no time.

    """
    if elapsed is None or elapsed.total_seconds() <= 0:
        return None
    total_minutes = math.floor(elapsed.total_seconds() / 60 + 0.5)
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return {"hours": "", "minutes": str(minutes)}
    return {"hours": str(hours), "minutes": f"{minutes:02d}"}
