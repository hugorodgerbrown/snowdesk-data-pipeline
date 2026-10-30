"""
apps/routes/services/track_render.py — a track's drawing, built once and stored.

The builder is ``track.render`` (SNOW-1316), pure of Django; this module
is its Django half: reading a row's fields into it, and the one writer.

SNOW-1309. Hugo, 2026-10-30: "Tours and routes don't change. All of the
render data should be precalculated. Both should store the same shape of
data. Only the source changes." So a Route, a SkiTour and a Trip each hold
one ``track_render`` record: everything the map, the route panel, the
point card and the trip page draw a line from. It is built by one pure
function (``track.render.build_track_render``, whose header documents the
record), written here, by one writer, and every reader only reads it.

The row's own stored columns (``distance_m``, ``bounds``, ``duration``)
are not copied in: they are already stored, and a second copy is a
second place for them to disagree.

## The one writer

``store_terrain`` is the only path that writes ``slope_samples``, and it
writes the render with it, so the two cannot be left describing different
things. A creation path stamps the no-terrain render with ``stamp_render``
before its row is first saved. A SkiTour is the exception in one respect:
its no-terrain state is a NULL render (``cleared_terrain``), read by the
tours layer as SAC's own stored line, because the order its line runs in
is a guess from the summits until the terrain is walked (SNOW-1306).

## Readers

``current_render`` answers the stored record when it is current. A missing
or stale one — a deploy before ``rebuild_track_renders --commit`` has run,
or an unsaved draft — is built in memory, and a saved row's is logged. A
GET never writes one.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from django.db import models
from django.db.models import Q
from django.utils import timezone

from track.render import (
    TRACK_RENDER_VERSION,
    build_track_render,
    is_current,
    same_render,
)

logger = logging.getLogger(__name__)


class Tracked(Protocol):
    """What the builder reads off a Route, a Trip or a draft Route."""

    pk: Any
    points: Any
    slope_samples: dict[str, Any] | None
    ascent_m: Any
    descent_m: Any
    track_render: dict[str, Any] | None


def render_of(
    obj: Tracked, *, points: list[list[float | None]] | None = None
) -> dict[str, Any]:
    """Build ``obj``'s record from its own fields.

    Args:
        obj: A Route, a Trip or a draft Route.
        points: The track, in place of ``obj.points`` — a SkiTour's line
            as ``tour_points`` orders it. Its stored ascent and descent are
            then not used: SAC's figures are not the line's (SNOW-1302).

    Returns:
        The record.

    """
    if points is not None:
        return build_track_render(
            points, obj.slope_samples, ascent_m=None, descent_m=None
        )
    return build_track_render(
        obj.points,
        obj.slope_samples,
        ascent_m=obj.ascent_m,
        descent_m=obj.descent_m,
    )


def stale_q() -> Q:
    """Return the filter for rows whose record is missing or out of date."""
    return Q(track_render__isnull=True) | ~Q(track_render__v=TRACK_RENDER_VERSION)


def current_render(
    obj: Tracked, *, points: list[list[float | None]] | None = None
) -> dict[str, Any]:
    """Return ``obj``'s render record: the stored one, or one built in memory.

    Never writes. A saved row met with no current record is logged, since
    every writer stores one: it means ``rebuild_track_renders`` has not
    run since the builder changed.

    Args:
        obj: A Route, a Trip or a draft Route.
        points: As ``render_of``'s.

    Returns:
        The record.

    """
    if is_current(obj.track_render):
        return obj.track_render
    if obj.pk is not None:
        logger.warning(
            "Track render missing or stale; built in memory: %s pk=%s",
            type(obj).__name__,
            obj.pk,
        )
    return render_of(obj, points=points)


def stamp_render(obj: Tracked) -> None:
    """Give an unsaved row its record before it is first written.

    Args:
        obj: A Route or Trip about to be created, ``slope_samples`` set.

    """
    obj.track_render = render_of(obj)


def stamped_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Return a create() field dict with ``track_render`` built from it.

    For the copy paths, which create from values rather than from an
    instance: the record is built from the copied track and terrain, not
    taken from the source, so a copy can never carry a stale one.

    Args:
        fields: Holds ``points``, ``slope_samples``, ``ascent_m`` and
            ``descent_m``.

    Returns:
        ``fields`` with ``track_render``.

    """
    return {
        **fields,
        "track_render": build_track_render(
            fields["points"],
            fields.get("slope_samples"),
            ascent_m=fields.get("ascent_m"),
            descent_m=fields.get("descent_m"),
        ),
    }


def store_terrain(
    obj: models.Model,
    record: dict[str, Any] | None,
    *,
    points: list[list[float | None]] | None = None,
) -> None:
    """Write ``record`` as ``obj``'s terrain, and its render with it.

    The one writer of ``slope_samples`` (SNOW-1309). ``updated_at`` moves
    too, which is what the tours overview's cache key reads. Nothing is
    written when the stored render's fingerprint equals the new one
    (SNOW-1316): the record and the render are already these.

    Args:
        obj: A saved Route, Trip or SkiTour.
        record: The slope record, or None to clear it.
        points: As ``render_of``'s; a SkiTour passes its ordered line.

    """
    stored = obj.track_render  # type: ignore[attr-defined]
    obj.slope_samples = record  # type: ignore[attr-defined]
    obj.track_render = render_of(obj, points=points)  # type: ignore[arg-type, attr-defined]
    if same_render(stored, obj.track_render):  # type: ignore[attr-defined]
        # SNOW-1316: built from exactly what the stored one was, so there
        # is nothing to write — a second call is a no-op.
        return
    type(obj)._default_manager.filter(pk=obj.pk).update(
        slope_samples=record,
        track_render=obj.track_render,  # type: ignore[attr-defined]
        updated_at=timezone.now(),
    )


def cleared_terrain() -> dict[str, None]:
    """Return the field values a SkiTour sync writes to forget its terrain.

    Both fields together, always: a tour whose line changed holds neither
    a record nor a drawing of the old line. A bulk sync writes values, not
    instances, so this is its half of ``store_terrain``'s rule.
    """
    return {"slope_samples": None, "track_render": None}
