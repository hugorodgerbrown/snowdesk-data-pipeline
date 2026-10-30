"""
tests/routes/management/commands/test_render_ski_tours.py

Covers ``render_ski_tours`` (SNOW-1316):
  - Read-only by default: no request, no write.
  - ``--commit`` walks and draws a tour on the service and applies it here.
  - A second ``--commit`` is a no-op: nothing walked, nothing written.
  - No terrain grid, or any tour unanswered, exits non-zero; three in a row stop it.
  - One failing tour does not abort the batch; the command exits non-zero.
  - ``--limit`` stops where it says.

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
from tests.factories import SourceSkiTourFactory
from track.terrain_grid import TerrainGrid, grid_from_payload

pytestmark = pytest.mark.django_db

COMMAND = "render_ski_tours"
_BUILDER = "apps.ingest.ski_render.build_slope_samples"
_GRID = "apps.ingest.ski_render.load_grid"
RECORD: dict[str, Any] = {"segments": [], "heights": [2000.0, 2400.0]}
_GRID_FIXTURE = (
    Path(__file__).parents[3] / "track" / "fixtures" / "terrain" / "grid.json"
)


def _grid() -> TerrainGrid:
    """Return the committed terrain grid definition."""
    return grid_from_payload(json.loads(_GRID_FIXTURE.read_text()))


def _run(*args: str) -> str:
    """Run the command with no pause against the committed grid; return stdout."""
    out = StringIO()
    with patch(_GRID, return_value=_grid()):
        call_command(COMMAND, "--delay", "0", *args, stdout=out)
    return out.getvalue()


def test_a_read_only_run_asks_nothing_and_writes_nothing() -> None:
    """The default reports the count and walks nothing."""
    source = SourceSkiTourFactory.create()
    with patch(_BUILDER) as build:
        out = _run()
    build.assert_not_called()
    assert "Rendering 1 SAC tour(s) [READ-ONLY]" in out
    source.refresh_from_db()
    assert source.track_render is None
    assert not SkiTour.objects.exists()


def test_commit_walks_draws_and_applies_here() -> None:
    """The service holds the walk and drawing; this host's tour takes them."""
    source = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        out = _run("--commit")
    assert f"SAC {source.document['sac_id']} {source.document['name']}" in out
    assert "1 rendered" in out
    source.refresh_from_db()
    assert source.slope_samples is not None
    assert source.track_render is not None
    tour = SkiTour.objects.get(source_uuid=source.uuid)
    assert tour.track_render == source.track_render
    assert tour.slope_samples == source.slope_samples


def test_a_second_commit_walks_and_writes_nothing() -> None:
    """Idempotent: both fingerprints match, so no tour is even a candidate."""
    source = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        _run("--commit")
    source.refresh_from_db()
    stamp = source.updated_at
    with patch(_BUILDER) as build:
        out = _run("--commit")
    build.assert_not_called()
    assert "Rendering 0 SAC tour(s)" in out
    assert "0 rendered" in out
    source.refresh_from_db()
    assert source.updated_at == stamp


def test_no_terrain_grid_exits_non_zero() -> None:
    """Without the grid nothing can be walked, and the run says so."""
    SourceSkiTourFactory.create()
    with (
        patch(_GRID, return_value=None),
        pytest.raises(CommandError, match="terrain grid"),
    ):
        call_command(COMMAND, "--commit", stdout=StringIO())


def test_the_origin_down_stops_the_run() -> None:
    """Three tours unanswered in a row: stop, store nothing, exit non-zero."""
    SourceSkiTourFactory.create_batch(4)
    with (
        patch(_BUILDER, return_value=None) as build,
        pytest.raises(CommandError, match="3 unavailable"),
    ):
        _run("--commit")
    # Stopped at the third; each tour is walked once in each direction it could run.
    assert build.call_count <= 3 * 2
    assert not SourceSkiTour.objects.filter(track_render__isnull=False).exists()


def test_one_unanswered_tour_still_fails_the_run() -> None:
    """A partial outage is retried next run, but exits non-zero now."""
    SourceSkiTourFactory.create_batch(2)
    records = iter([None, dict(RECORD), dict(RECORD)])
    with (
        patch(_BUILDER, side_effect=lambda *a, **k: next(records)),
        pytest.raises(CommandError, match="1 unavailable"),
    ):
        _run("--commit")
    assert SourceSkiTour.objects.filter(track_render__isnull=False).count() == 1


def test_one_failing_tour_does_not_abort_the_batch() -> None:
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


def test_limit_stops_where_it_says() -> None:
    """--limit 1 renders one tour of two."""
    SourceSkiTourFactory.create_batch(2)
    with patch(_BUILDER, return_value=dict(RECORD)):
        _run("--commit", "--limit", "1")
    assert SourceSkiTour.objects.filter(track_render__isnull=False).count() == 1
