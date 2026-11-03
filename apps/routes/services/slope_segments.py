"""
apps/routes/services/slope_segments.py — sampling a route's terrain, off the request.

The walk itself — the record's shape, the stride, the outage rules — is
``track.slope_samples`` (SNOW-1316), pure of Django so the ingest service
renders SAC's tours with the same code. This module is a route's half of
it: ``build_slope_samples`` loads the grid from ``TERRAIN_TILE_BASE_URL``
and walks, and the ``@task`` workers store a route's result through
``store_terrain``, off the request cycle.

Two workers walk a route, one per caller:

  _worker_sample_route_slopes(route_pk)
      An upload's, at the default priority: the owner is looking at a
      flat line until it runs.

  backfill_route_slopes_task(route_pk, delay_s)
      ``backfill_route_slope_samples``' (SNOW-1398), one task per stale
      route through ``apps.core.command_tasks.enqueue_each``, at
      ``BULK_TASK_PRIORITY`` so an upload never waits behind a backfill.
      It re-reads the row and returns when it is no longer stale, and
      takes its turn on the tile origin with ``wait_for_slot`` — the same
      ``TILE_ORIGIN`` slot the SAC tour render takes, so the two bulk runs
      together never ask the origin faster than one of them would.

No breaker is OPENED on an unanswered walk, unlike the tour render, but
one the tour render opened is honoured: the task returns without walking
and its row stays a candidate.
``build_slope_samples`` answers None for a track too short to sample as
well as for an outage, and a degenerate route that stays a candidate
would otherwise close the origin for every task behind it, every night.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db.models import Q
from django_tasks import task

from apps.core.command_tasks import (
    BULK_TASK_PRIORITY,
    backing_off,
    calls_outside,
    wait_for_slot,
)
from apps.routes.models import Route
from track.slope_samples import build_slope_samples as walk_slope_samples
from track.terrain_grid import load_grid

logger = logging.getLogger(__name__)

# The shared name for the terrain tile origin, for wait_for_slot and the
# breaker: every bulk task that asks it takes its turn under this one name.
TILE_ORIGIN = "terrain-tiles"

# A row whose terrain needs a walk: never sampled (null), or sampled before
# SNOW-1043 added ``heights``, so it still draws the device's altimeter. A
# record carrying the key is current even when the list holds nulls ("no
# ground there" is an answer). Routes and trips share the rule.
STALE_TERRAIN = Q(slope_samples__isnull=True) | ~Q(slope_samples__has_key="heights")


def build_slope_samples(
    points: list[list[float | None]], label: str
) -> dict[str, Any] | None:
    """Sample the terrain along a track, against this instance's tile origin.

    Args:
        points: The stored track, as ``[lon, lat, ele]`` triples.
        label: How the caller names this track in a log line.

    Returns:
        ``track.slope_samples.build_slope_samples``'s record, or None.

    """
    return walk_slope_samples(
        points, label, grid=load_grid(settings.TERRAIN_TILE_BASE_URL)
    )


# ---------------------------------------------------------------------------
# Worker function — decorated with @task so django-tasks can enqueue and
# replay it. Accepts only JSON-serialisable primitives.
# ---------------------------------------------------------------------------


@task()
def _worker_sample_route_slopes(route_pk: int) -> None:
    """Background worker: sample one route's terrain and store the result.

    Takes a primary key rather than the route itself, and re-loads the row
    inside, so the task body is JSON-serialisable into the database and
    replayable on retry. Mirrors
    ``apps.accounts.push_service._worker_dispatch_push``.

    A route that no longer exists is an EXPECTED race — deleted between
    the enqueue and the run — so it logs at INFO and returns rather than
    failing the task.

    ``save(update_fields=…)`` writes the one column and nothing else: the
    row is the user's, the sampler has no business touching the geometry
    or the name, and a full save would race an owner renaming the route
    while the sampling ran.

    Args:
        route_pk: Primary key of the ``Route`` to sample.

    """
    try:
        route = Route.objects.get(pk=route_pk)
    except Route.DoesNotExist:
        logger.info(
            "route pk=%s no longer exists — skipping slope sampling worker",
            route_pk,
        )
        return

    _walk_and_store(route)


def _walk_and_store(route: Route) -> str:
    """Walk one route's terrain and store it; both workers' shared body.

    Args:
        route: The route, loaded.

    Returns:
        ``"sampled"``, or ``"unanswered"`` when the walk learned nothing.

    """
    samples = build_slope_samples(route.points, f"route pk={route.pk}")
    if samples is None:
        # Left null on purpose: null is "never sampled", which is what a
        # run that learned nothing leaves true. See the module docstring.
        return "unanswered"

    # SNOW-1309: the record and its render, written together. Imported
    # here: the render's builders import this module's stride walk.
    from apps.routes.services.track_render import store_terrain  # noqa: PLC0415

    store_terrain(route, samples)
    logger.info(
        "route slopes sampled: pk=%s uuid=%s segments=%d unknown=%d",
        route.pk,
        route.uuid,
        len(samples["segments"]),
        sum(1 for segment in samples["segments"] if "unknown" in segment),
    )
    return "sampled"


@task(priority=BULK_TASK_PRIORITY)
def backfill_route_slopes_task(route_pk: int, delay_s: float = 0.0) -> str | None:
    """Background worker: walk one stale route for the backfill (SNOW-1398).

    Re-reads the row through ``STALE_TERRAIN``, so a duplicate enqueue, a
    route the upload worker reached first, or one deleted since, is a
    no-op. ``store_terrain`` writes the record and its render in one
    UPDATE.

    Args:
        route_pk: Primary key of the ``Route`` to sample.
        delay_s: Seconds between one walk's start and the next.

    Returns:
        ``"sampled"`` or ``"unanswered"``, kept on the task result, or None
        when the route no longer needed a walk or the tile origin's
        breaker is open.

    """
    if backing_off(TILE_ORIGIN):
        logger.info(
            "route pk=%s: backing off the tile origin; left for the next run",
            route_pk,
        )
        return None
    route = Route.objects.filter(STALE_TERRAIN, pk=route_pk).first()
    if route is None:
        logger.info("route pk=%s needs no walk; skipping backfill task", route_pk)
        return None
    wait_for_slot(TILE_ORIGIN, delay_s)
    return _walk_and_store(route)


# Each task asks the tile origin, so enqueue_each refuses it unpaced.
calls_outside(backfill_route_slopes_task)


def enqueue_route_slope_sampling(route: Route) -> None:
    """Enqueue terrain sampling for ``route`` to run off the request cycle.

    **CALL THIS OUTSIDE ANY OPEN TRANSACTION.** Under ``ImmediateBackend``
    — dev, test AND staging — ``.enqueue()`` runs the worker INLINE, so an
    enqueue inside ``create_route``'s ``atomic()`` block would make a
    tile-origin round trip per terrain tile the track crosses while
    holding the ``select_for_update`` lock on the user row. Every backend
    reads the same on a green test run; only production would differ.

    SNOW-1228: AN ENQUEUE FAILURE IS LOGGED, NOT RAISED. This runs after
    the route has committed, so raising would answer the user with a 500
    for a route that does exist, and a retry would upload a second one.
    The row keeps a null ``slope_samples``, which the daily
    ``reconcile_slope_samples`` job (``schedule.py``) picks up.

    Args:
        route: The route to sample.

    """
    try:
        _worker_sample_route_slopes.enqueue(route.pk)
    except Exception:  # noqa: BLE001 — broad catch intentional: any backend failure must not fail a committed upload
        logger.exception("route slope sampling enqueue failed: route pk=%s", route.pk)
