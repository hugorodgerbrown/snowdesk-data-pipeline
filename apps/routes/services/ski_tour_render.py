"""
apps/routes/services/ski_tour_render.py — one SAC tour rendered as one task (SNOW-1345).

``render_ski_tours --commit`` used to walk every stale tour in one process,
for over an hour, and copy the drawings onto the map only at the end; a
dropped shell lost the lot. Now it only enqueues, and each tour is its own
task on the background runner (``db_worker`` in production; inline under
``ImmediateBackend`` in dev, test and staging):

  render_tour_task.enqueue(source_pk, delay_s)
      Re-read the tour and skip it if it no longer needs work (a duplicate
      enqueue, or a run that already did it), walk and draw it with its own
      summit only (``tour_summit``), copy it onto this host's ``SkiTour``
      (``apply_source_tour``) in the same transaction, then pause
      ``delay_s`` after a real walk, the politeness towards the tile origin
      the old loop's sleep gave.

The tasks are enqueued at low priority so alert emails and pushes on the
same ``default`` queue are never stuck behind two thousand tours.
"""

from __future__ import annotations

import logging
import time

from django.db import transaction
from django_tasks import task

from apps.ingest.public import (
    RenderOutcome,
    SourceSkiTour,
    needs_render,
    render_source_tour,
    terrain_grid,
    tour_summit,
)
from apps.routes.services.ski_network import apply_source_tour
from track.terrain_grid import TerrainGrid

logger = logging.getLogger(__name__)

# Below the default (0), so user-facing work on the queue goes first.
RENDER_TASK_PRIORITY = -50

# The terrain model's definition, read once per worker process. Only a
# loaded grid is kept: an outage is retried by the next task.
_GRID: TerrainGrid | None = None


class TerrainGridUnavailable(RuntimeError):
    """The terrain grid could not be loaded, so no tour can be walked."""


def _grid() -> TerrainGrid:
    """Return the terrain grid, loading it once per process.

    Raises:
        TerrainGridUnavailable: It could not be loaded.

    """
    global _GRID
    if _GRID is None:
        _GRID = terrain_grid()
        if _GRID is None:
            raise TerrainGridUnavailable("the terrain grid could not be loaded")
    return _GRID


def reset_grid_cache() -> None:
    """Forget the cached grid, for tests and after a terrain model change."""
    global _GRID
    _GRID = None


def render_one_tour(source_pk: int, delay_s: float = 0.0) -> RenderOutcome | None:
    """Walk, draw and apply one tour; the task's body, callable directly.

    Args:
        source_pk: The ``SourceSkiTour``'s primary key.
        delay_s: Seconds to pause after a walk that asked the tile origin.

    Returns:
        What was done, or None when the tour is gone or needed nothing.

    Raises:
        TerrainGridUnavailable: The grid could not be loaded; the task fails
            and the tour is enqueued again by the next run.

    """
    row = SourceSkiTour.objects.live().filter(pk=source_pk).first()
    if row is None:
        logger.info("ski tour render: source tour pk=%s is gone, skipping", source_pk)
        return None
    grid = _grid()
    if not needs_render(row, grid):
        return None
    # The service's write and this host's copy commit together: a render
    # whose copy failed, or a worker stopped between the two, rolls back and
    # leaves the tour a candidate, rather than marking it done with the map
    # still stale (nothing else would ever re-apply it).
    with transaction.atomic():
        outcome = render_source_tour(row, tour_summit(row), grid, commit=True)
        if outcome == RenderOutcome.UNAVAILABLE:
            logger.warning(
                "ski tour render: tile origin did not answer for pk=%s; "
                "left for the next run",
                source_pk,
            )
            return outcome
        apply_source_tour(source_pk)
    walked = outcome == RenderOutcome.RENDERED
    if walked and delay_s:
        time.sleep(delay_s)
    return outcome


@task(priority=RENDER_TASK_PRIORITY)
def render_tour_task(source_pk: int, delay_s: float = 0.0) -> str | None:
    """Background worker: render and apply one SAC tour (SNOW-1345).

    Takes a primary key, not the row, so the task body is JSON-serialisable
    into the database and replayable.

    Args:
        source_pk: The ``SourceSkiTour``'s primary key.
        delay_s: Seconds to pause after a walk.

    Returns:
        The outcome's value, kept on the task result, or None.

    """
    outcome = render_one_tour(source_pk, delay_s)
    return outcome.value if outcome is not None else None
