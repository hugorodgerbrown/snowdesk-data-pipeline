"""
tests/ingest/test_ski_render.py — the service walks and draws each SAC tour once (SNOW-1316).

Covers ``apps.ingest.ski_render``:
  - A walk stores the record, the drawing and the fingerprint it came from.
  - A second render of the same tour walks nothing and writes nothing.
  - A stale drawing is redrawn from the record held, with no walk.
  - A new terrain model makes every tour a candidate again.
  - A rewritten document clears all three, through the weekly sync.
  - An outage stores nothing; a line too short to walk is settled.
  - A preview walks nothing.
  - The v2 feed serves the walk and the drawing beside the document.

``build_slope_samples`` is patched: the walk itself is
``tests/track``'s subject.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from apps.ingest.api.reference import serialise_source_ski_tour
from apps.ingest.models import SourceSkiTour
from apps.ingest.reference import DocumentRecord, sync_source_documents
from apps.ingest.ski_render import (
    RenderOutcome,
    needs_render,
    render_source_tour,
    terrain_grid,
    tour_summits,
    tours_to_render,
    walk_fingerprint,
)
from tests.factories import SourceSkiTourFactory
from track.terrain_grid import TerrainGrid, grid_from_payload

pytestmark = pytest.mark.django_db

_BUILDER = "apps.ingest.ski_render.build_slope_samples"
RECORD: dict[str, Any] = {"segments": [], "heights": [2000.0, 2400.0]}
GRID: TerrainGrid = grid_from_payload(
    json.loads(
        (
            Path(__file__).parents[1] / "track" / "fixtures" / "terrain" / "grid.json"
        ).read_text()
    )
)


def _render(row: SourceSkiTour, *, commit: bool = True) -> RenderOutcome:
    """Render one tour against the fixture grid, re-reading it first."""
    row.refresh_from_db()
    return render_source_tour(row, tour_summits(), GRID, commit=commit)


def test_a_walk_stores_record_drawing_and_fingerprint() -> None:
    """The three fields land together, and updated_at moves for the feed."""
    row = SourceSkiTourFactory.create()
    before = row.updated_at
    with patch(_BUILDER, return_value=dict(RECORD)) as build:
        assert _render(row) == RenderOutcome.RENDERED
    assert build.call_count >= 1
    row.refresh_from_db()
    assert row.render_input_fp == walk_fingerprint(row, GRID)
    assert row.slope_samples is not None
    assert row.track_render is not None
    assert row.track_render["fp"]
    assert row.updated_at > before
    assert not needs_render(row, GRID)
    assert not tours_to_render(GRID).exists()


def test_a_second_render_walks_nothing_and_writes_nothing() -> None:
    """Idempotent: the same inputs answer unchanged without a request."""
    row = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        _render(row)
    row.refresh_from_db()
    stamp = row.updated_at
    with patch(_BUILDER) as build:
        assert _render(row) == RenderOutcome.UNCHANGED
    build.assert_not_called()
    row.refresh_from_db()
    assert row.updated_at == stamp


def test_a_stale_drawing_is_redrawn_from_the_record_held() -> None:
    """A builder change redraws every tour; the terrain is not walked again."""
    row = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        _render(row)
    SourceSkiTour.objects.filter(pk=row.pk).update(track_render={"v": 0})
    row.refresh_from_db()
    assert needs_render(row, GRID)
    with patch(_BUILDER) as build:
        assert _render(row) == RenderOutcome.REDRAWN
    build.assert_not_called()
    row.refresh_from_db()
    assert row.track_render is not None
    assert row.track_render["v"] != 0


def test_a_new_terrain_model_walks_every_tour_again() -> None:
    """The grid's version is part of what a walk was made from."""
    row = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        _render(row)
    row.refresh_from_db()
    assert needs_render(row, dataclasses.replace(GRID, version="v2"))


def test_a_rewritten_document_clears_the_walk() -> None:
    """The weekly sync resets all three on a changed hash, and only then."""
    row = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=dict(RECORD)):
        _render(row)
    row.refresh_from_db()
    same = DocumentRecord(uuid=row.uuid, slug=row.slug, document=row.document)
    sync_source_documents(SourceSkiTour, [same])
    row.refresh_from_db()
    assert row.track_render is not None

    moved = DocumentRecord(
        uuid=row.uuid, slug=row.slug, document={**row.document, "name": "Moved"}
    )
    sync_source_documents(SourceSkiTour, [moved])
    row.refresh_from_db()
    assert (row.slope_samples, row.track_render, row.render_input_fp) == (
        None,
        None,
        "",
    )


def test_an_outage_stores_nothing() -> None:
    """The origin unanswered: the tour is left for the next run."""
    row = SourceSkiTourFactory.create()
    with patch(_BUILDER, return_value=None):
        assert _render(row) == RenderOutcome.UNAVAILABLE
    row.refresh_from_db()
    assert row.render_input_fp == ""
    assert needs_render(row, GRID)


def test_a_line_too_short_to_walk_is_settled() -> None:
    """No stride fits: no terrain, by fact, and not a candidate again."""
    row = SourceSkiTourFactory.create()
    point = [10.05, 46.85]
    document = {
        **row.document,
        "geometry": {"type": "LineString", "coordinates": [point, point]},
    }
    SourceSkiTour.objects.filter(pk=row.pk).update(document=document)
    with patch(_BUILDER) as build:
        assert _render(row) == RenderOutcome.NO_TERRAIN
    build.assert_not_called()
    row.refresh_from_db()
    assert row.track_render is None
    assert not needs_render(row, GRID)


def test_a_preview_walks_nothing() -> None:
    """Without commit, answer what would be done and touch nothing."""
    row = SourceSkiTourFactory.create()
    with patch(_BUILDER) as build:
        assert _render(row, commit=False) == RenderOutcome.RENDERED
    build.assert_not_called()
    row.refresh_from_db()
    assert row.render_input_fp == ""


def test_the_grid_comes_from_the_ingest_setting(
    settings: Any,
) -> None:
    """``terrain_grid`` reads TERRAIN_TILE_BASE_URL through conf."""
    settings.TERRAIN_TILE_BASE_URL = "https://tiles.example/terrain/v1"
    with patch("apps.ingest.ski_render.load_grid", return_value=GRID) as load:
        assert terrain_grid() is GRID
    load.assert_called_once_with("https://tiles.example/terrain/v1")


def test_the_feed_serves_walk_and_drawing() -> None:
    """The v2 item is the document plus both fields, null until walked."""
    row = SourceSkiTourFactory.create()
    item = serialise_source_ski_tour(row)
    assert item["slope_samples"] is None
    assert item["track_render"] is None
    assert item["document"] == row.document
