"""
apps/ingest/ski_render.py — the service walks and draws each SAC tour once (SNOW-1316).

Hugo, 2026-10-30: a render that is deterministic from external data is
calculated as soon as possible and kept with that data, offline when it is
expensive, and calculating it twice is a no-op. So the service, not each
instance, walks the terrain under every ``SourceSkiTour``'s line and stores
the record and the drawing beside the document; the v2 ``ski-tours`` feed
serves both, and every instance copies them.

The maths is ``track`` — the same package an instance draws a user's
upload with — so a tour here renders exactly as it would there:

* ``track.sac.walk_tour`` orders the line (a way up ends at its summit, a
  way down starts there) and walks it;
* ``track.render.build_track_render`` draws it.

**Two fingerprints keep it idempotent.** ``render_input_fp`` names what a
walk was made from — the document's ``content_hash`` and the terrain
model's identity (``grid``, its analysis window) — so a tour walked from
these inputs is not walked again. The render's own ``fp`` names what the
drawing was made from, so a render already current is not rewritten. A
tour whose document changes has all three cleared by the sync
(``SourceSkiTour.changed_document_resets``) and is walked afresh.

**An outage writes nothing.** The walk answers None when the tile origin
is down (``track.slope_samples``), and the tour is left for the next run.
A line too short to hold one stride is a fact, not an outage: it gets a
render with no terrain, so it is not tried every day.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from django.db.models import BooleanField, Case, QuerySet, Value, When
from django.db.models.fields.json import KeyTransform
from django.utils import timezone

from apps.ingest import conf
from apps.ingest.models import SourceSkiTour
from track import sac
from track.render import build_track_render, is_current, same_render
from track.slope_samples import (
    SAMPLE_STRIDE_M,
    build_slope_samples,
    cumulative_distances,
)
from track.terrain_grid import TerrainGrid, load_grid

logger = logging.getLogger(__name__)

# Rows per fetch for the two startup passes over every tour (SNOW-1316's
# render_ski_tours died at startup on production when they were read whole).
_STARTUP_CHUNK = 200


class RenderOutcome(StrEnum):
    """What ``render_source_tour`` did with one tour."""

    RENDERED = "rendered"
    REDRAWN = "redrawn"
    UNCHANGED = "unchanged"
    NO_TERRAIN = "no_terrain"
    UNAVAILABLE = "unavailable"


@dataclass
class DocumentTour:
    """A ``SourceSkiTour`` document read as ``track.sac.TourLike``.

    The text fields are cut as an instance's ``SkiTour`` columns cut them
    (``apps.routes.services.ski_network``), so a summit's key is the same
    on both sides.
    """

    geometry: Any
    name: str
    names: Any
    ascent_m: Any
    descent_m: Any
    target_name: str
    target_altitude_m: Any
    slope_samples: dict[str, Any] | None
    sac_id: Any = None

    @classmethod
    def of(cls, row: SourceSkiTour, *, samples: bool = True) -> DocumentTour:
        """Return ``row``'s document as a tour, with the record it holds.

        ``samples=False`` leaves the record out, for a row loaded without
        it: reading a deferred field would query once per tour.
        """
        document = row.document or {}
        return cls(
            geometry=document.get("geometry") or {},
            name=str(document.get("name") or "")[:255],
            names=document.get("names") or {},
            ascent_m=document.get("ascent_m"),
            descent_m=document.get("descent_m"),
            target_name=str(document.get("target_name") or "")[:255],
            target_altitude_m=document.get("target_altitude_m"),
            slope_samples=row.slope_samples if samples else None,
            sac_id=document.get("sac_id"),
        )


def terrain_grid() -> TerrainGrid | None:
    """Return the terrain model's definition, from ``TERRAIN_TILE_BASE_URL``."""
    return load_grid(conf.text("TERRAIN_TILE_BASE_URL"))


def walk_fingerprint(row: SourceSkiTour, grid: TerrainGrid) -> str:
    """Return what a walk of ``row`` against ``grid`` is made from, hashed.

    Args:
        row: The tour.
        grid: The terrain model's definition.

    Returns:
        A 64-character hex digest.

    """
    return _walk_fingerprint(row.content_hash, grid)


def _walk_fingerprint(content_hash: str, grid: TerrainGrid) -> str:
    """Return the walk fingerprint of a tour whose document hashes to ``content_hash``.

    Args:
        content_hash: The tour document's hash.
        grid: The terrain model's definition.

    Returns:
        A 64-character hex digest.

    """
    payload = {
        "content_hash": content_hash,
        "grid": grid.grid,
        "version": grid.version,
        "window_m": grid.default_analysis_window_m,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def tour_summits() -> dict[str, sac.Summit]:
    """Return every live tour's summit by key: the first guess at each order."""
    # Streamed: each document is a whole SAC tour with its line, and the
    # tours are a few thousand, so holding them all at once is what ran a
    # small instance out of memory before the first tour was walked.
    rows = (
        SourceSkiTour.objects.live()
        .only("id", "document", "content_hash")
        .iterator(chunk_size=_STARTUP_CHUNK)
    )
    return {
        summit.key: summit
        for summit in sac.build_summits(
            DocumentTour.of(row, samples=False) for row in rows
        )
    }


def needs_render(row: SourceSkiTour, grid: TerrainGrid) -> bool:
    """Return whether ``row`` has a walk or a drawing to make.

    Args:
        row: The tour, with ``render_input_fp``, ``content_hash`` and
            ``track_render`` loaded.
        grid: The terrain model's definition.

    Returns:
        False when it was walked from these inputs and, if the walk found
        terrain, drawn by the current builder.

    """
    if row.render_input_fp != walk_fingerprint(row, grid):
        return True
    return row.track_render is not None and not is_current(row.track_render)


def _stale(
    content_hash: str,
    render_input_fp: str | None,
    has_render: bool,
    render_version: Any,
    grid: TerrainGrid,
) -> bool:
    """Return ``needs_render``'s answer from the four values it reads.

    The same rule, decided from the drawing's version alone so the drawing
    itself is never loaded: ``is_current`` reads nothing else.

    Args:
        content_hash: The tour document's hash.
        render_input_fp: The fingerprint the stored walk was made from.
        has_render: Whether a drawing is stored.
        render_version: The stored drawing's ``v``, or None.
        grid: The terrain model's definition.

    Returns:
        True when the tour has a walk or a drawing to make.

    """
    if render_input_fp != _walk_fingerprint(content_hash, grid):
        return True
    return has_render and not is_current({"v": render_version})


def tours_to_render(grid: TerrainGrid) -> QuerySet[SourceSkiTour]:
    """Return the live tours with a walk or a drawing to make, newest first.

    Read in full only for the ids: the fingerprint is computed per row, and
    the tours are a few thousand.

    Args:
        grid: The terrain model's definition.

    Returns:
        The tours, ordered ``-id``.

    """
    # Only the drawing's version is read, in the database: a stored drawing
    # is the whole track with its point-card rows, and loading every one at
    # once ran a small instance out of memory before the first tour.
    rows = (
        SourceSkiTour.objects.live()
        .annotate(
            has_render=Case(
                When(track_render__isnull=True, then=Value(False)),
                default=Value(True),
                output_field=BooleanField(),
            ),
            render_version=KeyTransform("v", "track_render"),
        )
        .values_list(
            "id", "content_hash", "render_input_fp", "has_render", "render_version"
        )
        .iterator(chunk_size=_STARTUP_CHUNK)
    )
    stale = [
        pk
        for pk, content_hash, render_input_fp, has_render, render_version in rows
        if _stale(content_hash, render_input_fp, has_render, render_version, grid)
    ]
    return SourceSkiTour.objects.filter(pk__in=stale).order_by("-id")


def render_source_tour(
    row: SourceSkiTour,
    summits: dict[str, sac.Summit],
    grid: TerrainGrid,
    *,
    commit: bool,
) -> RenderOutcome:
    """Walk and draw one tour, and store both unless nothing changed.

    A tour walked from these inputs is only redrawn from the record it
    holds — no request to the tile origin — and not even that when the
    stored drawing's fingerprint is the new one's.

    Args:
        row: The tour.
        summits: Every summit by key (``tour_summits``).
        grid: The terrain model's definition.
        commit: Write the result. Without it, walk nothing, write nothing,
            and answer what would be done.

    Returns:
        What was done, or would be.

    """
    tour = DocumentTour.of(row)
    walked_from = walk_fingerprint(row, grid)
    if row.render_input_fp == walked_from:
        if row.slope_samples is None:
            return RenderOutcome.UNCHANGED
        render = _draw(tour, row.slope_samples, summits)
        if same_render(row.track_render, render):
            return RenderOutcome.UNCHANGED
        if commit:
            _store(row, row.slope_samples, render, walked_from)
        return RenderOutcome.REDRAWN

    if not commit:
        return RenderOutcome.RENDERED
    if _too_short(tour, summits):
        _store(row, None, None, walked_from)
        return RenderOutcome.NO_TERRAIN
    record = sac.walk_tour(
        tour,
        summits,
        functools.partial(build_slope_samples, grid=grid),
        f"ski tour {tour.sac_id}",
    )
    if record is None:
        # The origin did not answer: nothing is stored, and the tour is
        # tried again on the next run.
        return RenderOutcome.UNAVAILABLE
    _store(row, record, _draw(tour, record, summits), walked_from)
    return RenderOutcome.RENDERED


def _draw(
    tour: DocumentTour, record: dict[str, Any], summits: dict[str, sac.Summit]
) -> dict[str, Any]:
    """Return the drawing of ``tour``'s line, in the order ``record`` was walked.

    As an instance draws a tour (``apps.routes.services.ski_tours._keep``):
    SAC's height figures are not the line's, so none are passed.
    """
    tour.slope_samples = record
    return build_track_render(
        sac.tour_points(tour, summits), record, ascent_m=None, descent_m=None
    )


def _store(
    row: SourceSkiTour,
    record: dict[str, Any] | None,
    render: dict[str, Any] | None,
    walked_from: str,
) -> None:
    """Write one tour's walk and drawing; ``updated_at`` moves for the feed."""
    SourceSkiTour.objects.filter(pk=row.pk).update(
        slope_samples=record,
        track_render=render,
        render_input_fp=walked_from,
        updated_at=timezone.now(),
    )


def _too_short(tour: DocumentTour, summits: dict[str, sac.Summit]) -> bool:
    """Return whether the line cannot hold one stride: no terrain, by fact."""
    distances = cumulative_distances(sac.tour_points(tour, summits))
    return not distances or distances[-1] < SAMPLE_STRIDE_M
