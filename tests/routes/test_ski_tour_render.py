"""
tests/routes/test_ski_tour_render.py

Covers ``apps.routes.services.ski_tour_render`` (SNOW-1345), one SAC tour
rendered as one task:
  - The task walks, draws and applies its tour, and pauses after a walk.
  - A tour that needs nothing, or is gone, is skipped without a request.
  - An unanswered tour stores and applies nothing.
  - No terrain grid fails the task; the grid is loaded once per process.
  - ``apply_source_tour`` copies one service tour here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from apps.ingest.public import RenderOutcome, SourceSkiTour
from apps.routes.models import SkiTour
from apps.routes.services.ski_network import CREATED, apply_source_tour
from apps.routes.services.ski_tour_render import (
    RENDER_TASK_PRIORITY,
    TerrainGridUnavailable,
    render_one_tour,
    render_tour_task,
    reset_grid_cache,
)
from tests.factories import SourceSkiTourFactory
from track.terrain_grid import TerrainGrid, grid_from_payload

pytestmark = pytest.mark.django_db

_BUILDER = "apps.ingest.ski_render.build_slope_samples"
_GRID = "apps.ingest.ski_render.load_grid"
_SLEEP = "apps.routes.services.ski_tour_render.time.sleep"
RECORD: dict[str, Any] = {"segments": [], "heights": [2000.0, 2400.0]}
_GRID_FIXTURE = (
    Path(__file__).parents[1] / "track" / "fixtures" / "terrain" / "grid.json"
)


def _grid() -> TerrainGrid:
    """Return the committed terrain grid definition."""
    return grid_from_payload(json.loads(_GRID_FIXTURE.read_text()))


@pytest.fixture(autouse=True)
def _fresh_grid() -> Any:
    """Start every test with no cached grid."""
    reset_grid_cache()
    yield
    reset_grid_cache()


def test_the_task_walks_draws_applies_and_pauses() -> None:
    """One tour: stored on the service, copied here, then the pause."""
    source = SourceSkiTourFactory.create()
    with (
        patch(_GRID, return_value=_grid()),
        patch(_BUILDER, return_value=dict(RECORD)),
        patch(_SLEEP) as sleep,
    ):
        result = render_tour_task.enqueue(source.pk, 1.5)
    assert result.return_value == RenderOutcome.RENDERED.value
    sleep.assert_called_once_with(1.5)
    source.refresh_from_db()
    assert source.track_render is not None
    assert SkiTour.objects.get(source_uuid=source.uuid).track_render == (
        source.track_render
    )


def test_the_task_is_low_priority() -> None:
    """User-facing work on the default queue goes first."""
    assert render_tour_task.priority == RENDER_TASK_PRIORITY < 0


def test_a_tour_already_drawn_is_skipped() -> None:
    """A duplicate enqueue walks nothing and does not pause."""
    source = SourceSkiTourFactory.create()
    with patch(_GRID, return_value=_grid()), patch(_BUILDER, return_value=dict(RECORD)):
        render_one_tour(source.pk)
    with (
        patch(_GRID, return_value=_grid()),
        patch(_BUILDER) as build,
        patch(_SLEEP) as sleep,
    ):
        assert render_one_tour(source.pk, 1.0) is None
    build.assert_not_called()
    sleep.assert_not_called()


def test_a_gone_tour_is_skipped() -> None:
    """Deleted between enqueue and run: an expected race, not a failure."""
    with patch(_GRID) as grid:
        assert render_one_tour(999_999) is None
    grid.assert_not_called()


def test_an_unanswered_tour_stores_and_applies_nothing() -> None:
    """The origin's silence leaves the tour for the next run."""
    source = SourceSkiTourFactory.create()
    with (
        patch(_GRID, return_value=_grid()),
        patch(_BUILDER, return_value=None),
        patch(_SLEEP) as sleep,
    ):
        assert render_one_tour(source.pk, 1.0) == RenderOutcome.UNAVAILABLE
    sleep.assert_not_called()
    assert not SourceSkiTour.objects.filter(track_render__isnull=False).exists()
    assert not SkiTour.objects.exists()


def test_no_grid_fails_the_task() -> None:
    """Nothing can be walked without the terrain model."""
    source = SourceSkiTourFactory.create()
    with patch(_GRID, return_value=None), pytest.raises(TerrainGridUnavailable):
        render_one_tour(source.pk)


def test_the_grid_is_loaded_once_per_process() -> None:
    """Two tasks, one grid fetch."""
    sources = SourceSkiTourFactory.create_batch(2)
    with (
        patch(_GRID, return_value=_grid()) as load,
        patch(_BUILDER, return_value=dict(RECORD)),
    ):
        for source in sources:
            render_one_tour(source.pk)
    assert load.call_count == 1


def test_apply_source_tour_copies_one_tour() -> None:
    """The one row is created here; a gone row applies nothing."""
    source = SourceSkiTourFactory.create()
    assert apply_source_tour(source.pk)[CREATED] == 1
    assert SkiTour.objects.filter(source_uuid=source.uuid).exists()
    assert not apply_source_tour(999_999)
