"""
apps/trips/services/slope.py — sampling the ground under a trip's snapshot.

A trip carries its own copy of the source route's terrain record
(``Trip.slope_samples``, SNOW-962), copied at creation and never re-read,
the same rule the geometry beside it follows. This module is the one case
that copy cannot cover: a trip created from a route that has never been
sampled, or created in the window before that route's own sampler
finished, has nothing to inherit and samples for itself.

**WHY NOT JUST RE-READ THE ROUTE LATER.** Because the snapshot is the
trip. A route can be renamed, deleted or resampled against a rebuilt
grid, and a trip that went looking for its terrain at render time would
show the organiser one thing and a participant another — which is the
whole failure the snapshot exists to prevent.

**WHY THIS LIVES IN apps/trips AND NOT BESIDE THE ROUTE SAMPLER.**
``apps.routes`` must not import ``apps.trips``: trips already depends on
routes (it copies a route's geometry and saves routes back out), and a
second edge in the other direction would make the pair mutually
dependent. So the walk is imported from there and the trip-shaped worker
lives here.

The walk itself is ``apps.routes.services.slope_segments`` unchanged —
same stride, same record, same rule that an all-unavailable run stores
NOTHING so the field stays null and a later run can retry.
"""

from __future__ import annotations

import logging

from django_tasks import task

from apps.routes.services.slope_segments import build_slope_samples
from apps.routes.services.track_render import store_terrain
from apps.trips.models import Trip

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker function — decorated with @task so django-tasks can enqueue and
# replay it. Accepts only JSON-serialisable primitives.
# ---------------------------------------------------------------------------


@task()
def _worker_sample_trip_slopes(trip_pk: int) -> None:
    """Background worker: sample one trip's snapshot and store the result.

    Mirrors ``_worker_sample_route_slopes`` exactly — a primary key rather
    than the row, re-loaded inside so the task body is JSON-serialisable
    and replayable, and a missing row treated as the expected race rather
    than a failure.

    ``save(update_fields=…)`` writes the one column: the row is the
    organiser's, and a full save would race them renaming the trip or
    moving its meeting point while the sampling ran.

    Args:
        trip_pk: Primary key of the ``Trip`` to sample.

    """
    try:
        trip = Trip.objects.get(pk=trip_pk)
    except Trip.DoesNotExist:
        logger.info(
            "trip pk=%s no longer exists — skipping slope sampling worker",
            trip_pk,
        )
        return

    samples = build_slope_samples(trip.points, f"trip pk={trip.pk}")
    if samples is None:
        # Left null on purpose: null is "never sampled", which is what a
        # run that learned nothing leaves true.
        return

    # SNOW-1309: the record and its render, written together.
    store_terrain(trip, samples)
    logger.info(
        "trip slopes sampled: pk=%s uuid=%s segments=%d unknown=%d",
        trip.pk,
        trip.uuid,
        len(samples["segments"]),
        sum(1 for segment in samples["segments"] if "unknown" in segment),
    )


def enqueue_trip_slope_sampling(trip: Trip) -> None:
    """Enqueue terrain sampling for ``trip``, if it has nothing to draw.

    **CALL THIS OUTSIDE ANY OPEN TRANSACTION**, for the reason
    ``enqueue_route_slope_sampling`` states at length: under
    ``ImmediateBackend`` — dev, test AND staging — ``.enqueue()`` runs the
    worker INLINE, so an enqueue inside ``create_trip``'s ``atomic()``
    block would hold the cap's row lock for a tile-origin round trip per
    terrain tile the track crosses.

    A NO-OP WHEN THE SNAPSHOT ALREADY CARRIES A RECORD, which is the
    common case: a trip is normally made from a route uploaded long
    enough ago to have been sampled, and re-walking the same track would
    buy an identical answer at the cost of a second pass over the tile
    origin.

    SNOW-1228: AN ENQUEUE FAILURE IS LOGGED, NOT RAISED. This runs after
    the trip has committed, so raising would answer the user with a 500
    for a trip that does exist, and would skip whatever the caller enqueues
    next. The row keeps a null ``slope_samples``, which the daily
    ``reconcile_slope_samples`` job (``schedule.py``) picks up.

    Args:
        trip: The trip just created.

    """
    if trip.slope_samples is not None:
        return
    try:
        _worker_sample_trip_slopes.enqueue(trip.pk)
    except Exception:  # noqa: BLE001 — broad catch intentional: any backend failure must not fail a committed trip
        logger.exception("trip slope sampling enqueue failed: trip pk=%s", trip.pk)
