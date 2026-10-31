"""
tests/routes/management/commands/test_render_ski_tours.py

Covers ``render_ski_tours`` (SNOW-1316, SNOW-1345). Tests run tasks under
``ImmediateBackend``, so an enqueued tour is rendered inline:
  - Read-only by default: no request, no write, nothing enqueued.
  - ``--commit`` enqueues one task per tour, which walks, draws and applies it.
  - A second ``--commit`` is a no-op: no candidate, nothing enqueued.
  - No terrain grid exits non-zero and enqueues nothing.
  - An unanswered tour stores nothing and is left for the next run.
  - One failing task does not abort the batch; the command exits non-zero.
  - ``--limit`` caps what is enqueued.

``build_slope_samples`` is patched throughout: the walk is
``tests/routes/test_slope_segments.py``'s subject, the render step
``tests/ingest/test_ski_render.py``'s.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ingest.public import SourceSkiTour
from apps.routes.models import SkiTour
from apps.routes.services.ski_tour_render import reset_grid_cache
from tests.factories import SourceSkiTourFactory
from track.terrain_grid import TerrainGrid, grid_from_payload

pytestmark = pytest.mark.django_db

COMMAND = "render_ski_tours"
_BUILDER = "apps.ingest.ski_render.build_slope_samples"
_GRID = "apps.ingest.ski_render.load_grid"
_TASK = "apps.routes.management.commands.render_ski_tours.render_tour_task"
RECORD: dict[str, Any] = {"segments": [], "heights": [2000.0, 2400.0]}
_GRID_FIXTURE = (
    Path(__file__).parents[3] / "track" / "fixtures" / "terrain" / "grid.json"
)


def _grid() -> TerrainGrid:
    """Return the committed terrain grid definition."""
    return grid_from_payload(json.loads(_GRID_FIXTURE.read_text()))


@pytest.fixture(autouse=True)
def _fresh_grid() -> Any:
    """Each test loads the grid itself: the task caches it per process."""
    reset_grid_cache()
    yield
    reset_grid_cache()


def _run(*args: str) -> str:
    """Run the command with no pause against the committed grid; return stdout."""
    out = StringIO()
    with patch(_GRID, return_value=_grid()):
        call_command(COMMAND, "--delay", "0", *args, stdout=out)
    return out.getvalue()


def test_a_read_only_run_asks_nothing_and_writes_nothing() -> None:
    """The default reports the count and enqueues nothing."""
    source = SourceSkiTourFactory.create()
    with (
        patch(_BUILDER) as build,
        patch(_TASK) as task,
    ):
        out = _run()
    build.assert_not_called()
    task.enqueue.assert_not_called()
    assert "Enqueuing 1 of 1 SAC tour(s) [READ-ONLY]" in out
    assert "Would enqueue 1; 0 failed." in out
    source.refresh_from_db()
    assert source.track_render is None
    assert not SkiTour.objects.exists()


def test_commit_enqueues_a_task_that_walks_draws_and_applies() -> None:
    """The service holds the walk and drawing; this host's tour takes them."""
    source = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        out = _run("--commit")
    assert f"SAC {source.document['sac_id']} {source.document['name']}" in out
    assert "Enqueued 1; 0 failed." in out
    source.refresh_from_db()
    assert source.slope_samples is not None
    assert source.track_render is not None
    tour = SkiTour.objects.get(source_uuid=source.uuid)
    assert tour.track_render == source.track_render
    assert tour.slope_samples == source.slope_samples


def test_commit_enqueues_with_the_delay() -> None:
    """Each candidate is one task, handed its pk and the pause."""
    sources = SourceSkiTourFactory.create_batch(2)
    with patch(_TASK) as task:
        _run("--commit")
    calls = sorted(call.args for call in task.enqueue.call_args_list)
    assert calls == sorted((source.pk, 0.0) for source in sources)


def test_a_second_commit_enqueues_nothing() -> None:
    """Idempotent: both fingerprints match, so no tour is even a candidate."""
    source = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        _run("--commit")
    source.refresh_from_db()
    stamp = source.updated_at
    with patch(_BUILDER) as build:
        out = _run("--commit")
    build.assert_not_called()
    assert "Enqueuing 0 of 0 SAC tour(s)" in out
    source.refresh_from_db()
    assert source.updated_at == stamp


def test_no_terrain_grid_exits_non_zero() -> None:
    """Without the grid nothing can be walked, and the run says so."""
    SourceSkiTourFactory.create()
    with (
        patch(_GRID, return_value=None),
        patch(_TASK) as task,
        pytest.raises(CommandError, match="terrain grid"),
    ):
        call_command(COMMAND, "--commit", stdout=StringIO())
    task.enqueue.assert_not_called()


def test_an_unanswered_tour_stores_nothing() -> None:
    """The origin's silence writes nothing; the tour is a candidate next run."""
    SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=None):
        _run("--commit")
    assert not SourceSkiTour.objects.filter(track_render__isnull=False).exists()
    assert not SkiTour.objects.exists()


def test_one_failing_task_does_not_abort_the_batch() -> None:
    """The other tour is still rendered; the run exits non-zero."""
    SourceSkiTourFactory.create_batch(2)
    calls = {"n": 0}

    def _flaky(points: Any, label: str, *, grid: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return dict(RECORD)

    with (
        patch(_BUILDER, side_effect=_flaky),
        pytest.raises(CommandError, match="1 failure"),
    ):
        _run("--commit")
    assert SourceSkiTour.objects.filter(track_render__isnull=False).count() == 1
    assert SkiTour.objects.count() == 1


def test_limit_caps_what_is_enqueued() -> None:
    """--limit 1 enqueues, and so renders, one tour of two."""
    SourceSkiTourFactory.create_batch(2)
    with patch(_BUILDER, return_value=dict(RECORD)):
        out = _run("--commit", "--limit", "1")
    assert "Enqueuing 1 of 2 SAC tour(s)" in out
    assert SourceSkiTour.objects.filter(track_render__isnull=False).count() == 1
