"""apps/locations/services/anchor.py — bind a thing to an anonymous Location.

A region has a centroid; a resort has a pin. Both need a ``Location`` at a
known coordinate so weather can hang off them, and neither is a curated
place in its own right — so both anchor to an **anonymous** row.

**Reuse is the point, not an optimisation** (SNOW-771). ``bin/build.sh``
reloads the EAWS fixtures on every deploy and ``loaddata`` NULLs every
column those fixtures do not carry, so ``MicroRegion.centroid_location`` is
wiped and re-linked on each deploy. If the re-link minted a fresh row each
time, the previous one would be orphaned along with every ``Weather`` row
hanging off it — the map would go blank after every deploy until the next
fetch, and the estate would grow without bound. Staging reproduced exactly
that: 467 locations carrying weather before a deploy, 6 after.

One implementation, shared by both callers, because two would drift and the
direction that fails is silent.
"""

from __future__ import annotations

import logging

from apps.locations.models import Location

logger = logging.getLogger(__name__)


def anchor_location(
    latitude: float, longitude: float, elevation: float | None
) -> Location:
    """Return the anonymous Location at this coordinate, creating one if absent.

    Safe to call on every deploy: a coordinate derived from fixture data is
    bit-identical on every run, so the row a previous run created is found
    exactly and rebound rather than replaced.

    The match is deliberately narrow. Only an **anonymous** location is
    reused — a curated, named place may legitimately sit at the same
    coordinate, and rebinding onto it would put that name where it does not
    belong and give a curated row a second owner. Oldest id wins, so the row
    with the longest weather history survives.

    Args:
        latitude: The anchor's latitude.
        longitude: The anchor's longitude.
        elevation: Ground elevation in metres, or ``None`` when unknown.
            A null is not a failure — weather needs a coordinate, not a
            height, and ``Location.objects.unresolved()`` is how one gets
            filled in later.

    Returns:
        The reused or newly created ``Location``.

    """
    existing = (
        Location.objects.anonymous()
        .filter(latitude=latitude, longitude=longitude)
        .order_by("id")
        .first()
    )
    if existing is None:
        return Location.objects.create(
            latitude=latitude, longitude=longitude, elevation_m=elevation
        )
    if elevation is not None and existing.elevation_m != elevation:
        # A fixture rebuild can move a centroid, and a resort's recorded base
        # elevation can be corrected. Keep the row — and its weather — and
        # write the new height onto it. A null incoming elevation never
        # clears a height we already have.
        existing.elevation_m = elevation
        existing.save(update_fields=["elevation_m", "updated_at"])
    return existing
