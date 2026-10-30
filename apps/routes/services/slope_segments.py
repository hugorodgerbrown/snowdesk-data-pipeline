"""
apps/routes/services/slope_segments.py — sampling a route's terrain, off the request.

The walk itself — the record's shape, the stride, the outage rules — is
``track.slope_samples`` (SNOW-1316), pure of Django so the ingest service
renders SAC's tours with the same code. This module is a route's half of
it: ``build_slope_samples`` loads the grid from ``TERRAIN_TILE_BASE_URL``
and walks, and the ``@task`` worker stores a route's result through
``store_terrain``, off the request cycle.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django_tasks import task

from apps.routes.models import Route
from track.slope_samples import build_slope_samples as walk_slope_samples
from track.terrain_grid import load_grid

logger = logging.getLogger(__name__)


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

    samples = build_slope_samples(route.points, f"route pk={route.pk}")
    if samples is None:
        # Left null on purpose: null is "never sampled", which is what a
        # run that learned nothing leaves true. See the module docstring.
        return

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
