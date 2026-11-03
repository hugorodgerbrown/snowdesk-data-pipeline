"""
apps/trips/services/render_tasks.py — the trip half of the render rebuild (SNOW-1400).

``rebuild_track_renders --commit`` enqueues ``rebuild_trip_render_task``
once per stale trip, beside the route and tour tasks in
``apps.routes.services.render_tasks``. It lives here because
``apps.routes`` must not import ``apps.trips``. The task re-reads its row
through ``stale_q`` under a row lock, so a duplicate enqueue or a trip
brought current since is a no-op. Pure computation: no pace.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django_tasks import task

from apps.core.command_tasks import BULK_TASK_PRIORITY
from apps.routes.services.render_tasks import REBUILT
from apps.routes.services.track_render import stale_q, store_terrain
from apps.trips.models import Trip

logger = logging.getLogger(__name__)


@task(priority=BULK_TASK_PRIORITY)
def rebuild_trip_render_task(trip_pk: int) -> str | None:
    """Background worker: store a current render on one stale trip.

    Args:
        trip_pk: The ``Trip``'s primary key.

    Returns:
        ``"rebuilt"``, kept on the task result, or None when the trip is
        gone or already current.

    """
    with transaction.atomic():
        trip = Trip.objects.select_for_update().filter(stale_q(), pk=trip_pk).first()
        if trip is None:
            logger.info("trip pk=%s needs no render rebuild; skipping", trip_pk)
            return None
        store_terrain(trip, trip.slope_samples)
    return REBUILT
