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

# Metres. An elevation reaches this function as a float, from a fixture
# column on one run and from an Open-Meteo resolution on another, so two
# runs can disagree in the last bits of the mantissa for a height that has
# not actually moved. A difference below this is floating-point noise, not a
# correction: treating it as one would re-save every anchored row on every
# deploy and churn ``updated_at`` for nothing.
ELEVATION_TOLERANCE_M = 1e-6


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
    # Read-then-create, and deliberately **not** ``get_or_create`` behind a
    # unique constraint on ``(latitude, longitude)``. Duplicate anonymous
    # rows at one coordinate are legitimate elsewhere in the estate:
    # ``submit_report`` mints one location per field observation precisely so
    # that two reports at the same point are not merged into one place
    # (SNOW-709), and two users may pin the same coordinate as a favourite.
    # Even a partial unique index scoped to anonymous rows would turn both of
    # those into an ``IntegrityError`` on a request path — and would fail to
    # apply at all on an environment still carrying the pre-SNOW-771 orphan
    # generations, which are by construction repeated anonymous rows at one
    # centroid.
    #
    # What is left is a narrow create-create race: two callers resolving the
    # same coordinate while no row exists yet both create one. The caller
    # that actually runs concurrently — three services running
    # ``link_region_centroid_locations`` from one release — is already
    # serialised by the ``select_for_update()`` it takes on the parent
    # region, so the race needs two *different* parents landing on one
    # coordinate at one instant. Its cost is a single duplicate anonymous
    # row, which the oldest-id-wins read below makes invisible to every
    # subsequent call and which ``prune_orphan_locations`` collects.
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
    # Compared with a tolerance rather than for exact float equality: the
    # same height arriving twice can differ in its last bits, and only a real
    # move is worth a write. A row that has no height yet always takes one.
    moved = elevation is not None and (
        existing.elevation_m is None
        or abs(existing.elevation_m - elevation) > ELEVATION_TOLERANCE_M
    )
    if moved:
        # A fixture rebuild can move a centroid, and a resort's recorded base
        # elevation can be corrected. Keep the row — and its weather — and
        # write the new height onto it. A null incoming elevation never
        # clears a height we already have.
        existing.elevation_m = elevation
        existing.save(update_fields=["elevation_m", "updated_at"])
    return existing
