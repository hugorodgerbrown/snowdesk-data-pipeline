"""
apps/routes/services/render_tasks.py — the render rebuild's row tasks (SNOW-1400).

``rebuild_track_renders --commit`` only enqueues, through
``apps.core.command_tasks.enqueue_each``, one task per stale row:

  rebuild_route_render_task(route_pk)
      Rebuild one route's ``track_render`` from its own points and terrain
      record.

  rebuild_tour_render_task(tour_pk)
      Rebuild one walked SAC tour's render from the record it holds.

The trip's task lives in ``apps.trips.services.render_tasks``, because
``apps.routes`` must not import ``apps.trips``.

Each re-reads its row through ``stale_q`` under a row lock, so a duplicate
enqueue, a row another writer brought current first, or one deleted since,
is a no-op. Pure computation: no request to any origin, so no pace and no
breaker.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django_tasks import task

from apps.core.command_tasks import BULK_TASK_PRIORITY
from apps.routes.models import Route, SkiTour
from apps.routes.services.ski_tours import rebuild_tour_render
from apps.routes.services.track_render import stale_q, store_terrain

logger = logging.getLogger(__name__)

REBUILT = "rebuilt"
UNWALKED = "unwalked"


@task(priority=BULK_TASK_PRIORITY)
def rebuild_route_render_task(route_pk: int) -> str | None:
    """Background worker: store a current render on one stale route.

    Args:
        route_pk: The ``Route``'s primary key.

    Returns:
        ``"rebuilt"``, kept on the task result, or None when the route is
        gone or already current.

    """
    with transaction.atomic():
        route = Route.objects.select_for_update().filter(stale_q(), pk=route_pk).first()
        if route is None:
            logger.info("route pk=%s needs no render rebuild; skipping", route_pk)
            return None
        store_terrain(route, route.slope_samples)
    return REBUILT


@task(priority=BULK_TASK_PRIORITY)
def rebuild_tour_render_task(tour_pk: int) -> str | None:
    """Background worker: store a current render on one walked, stale tour.

    Args:
        tour_pk: The ``SkiTour``'s primary key.

    Returns:
        ``"rebuilt"``; ``"unwalked"`` when the tour holds no record for its
        line's order, so has no render by design; or None when the tour is
        gone or already current.

    """
    with transaction.atomic():
        tour = (
            SkiTour.objects.select_for_update()
            .filter(stale_q(), slope_samples__isnull=False, pk=tour_pk)
            .first()
        )
        if tour is None:
            logger.info("tour pk=%s needs no render rebuild; skipping", tour_pk)
            return None
        if not rebuild_tour_render(tour):
            return UNWALKED
    return REBUILT
