"""
apps/routes/services/ski_network.py — Applying the ski-touring and rest-zone feeds.

This instance's ``SkiSegment`` and ``SkiTour`` rows are copies of the
reference-data service's ``SourceSkiSegment`` and ``SourceSkiTour``,
served as the v2 ``ski-network`` and ``ski-tours`` feeds (SNOW-1276).
``RestZone`` is a copy of ``SourceRestZone``, the ``rest-zones`` feed
(SNOW-1263): FOEN's polygons, applied by exactly the same rule, so it is a
third kind here rather than a second copy of this module. Two callers
apply them, with the same function:

* ``pull_reference_data``, on every instance that is not the service,
  applies each page it pulls;
* ``import_ski_tours`` and ``import_rest_zones``, on the service's own
  host, apply the rows their import just wrote (``apply_from_service``),
  serialised exactly as the feed would serve them, so the two paths cannot
  drift.

  apply_ski_items(kind, items, *, commit)
      Apply one page of items. A live item is created or rewritten by
      ``source_uuid``, or counted unchanged when its ``updated_at`` is the
      one already held — the service moves ``updated_at`` only on a real
      change, so the geometry is never compared. A tombstone deletes the
      row: nothing a user owns points at a synced line (a route saved from
      a tour is a copy, SNOW-1278).
  synced_since(kind)
      The newest ``updated_at`` held, where the next incremental pull
      starts; None before the first one.
  apply_source_tour(source_pk)
      Apply one service tour here, straight after its task renders it
      (SNOW-1345).
  bbox_of(geometry)
      A GeoJSON line's or polygon's ``(west, south, east, north)``.
  network_overview()
      Every skiable segment as a thinned line, for draw mode at z9-z11
      (SNOW-1322), cached until a sync changes the network.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max
from django.utils.dateparse import parse_datetime

from apps.ingest.public import (
    REST_ZONES,
    SKI_NETWORK,
    SKI_TOURS,
    SourceRestZone,
    SourceSkiSegment,
    SourceSkiTour,
    serialise_source_document,
    serialise_source_ski_tour,
)
from apps.routes.models import RestZone, SkiSegment, SkiTour
from apps.routes.services.ski_tours import overview_geometry
from apps.routes.services.track_render import cleared_terrain

logger = logging.getLogger(__name__)

# What applying an item did.
CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
DELETED = "deleted"
ABSENT = "absent"
FAILED = "failed"
OUTCOMES: tuple[str, ...] = (CREATED, UPDATED, UNCHANGED, DELETED, ABSENT, FAILED)

# The two kinds, by feed name, in the order they are applied.
SKI_KINDS: tuple[str, ...] = (SKI_NETWORK, SKI_TOURS)
# SNOW-1263: FOEN's wildlife rest zones, applied by the same rule.
REST_ZONE_KINDS: tuple[str, ...] = (REST_ZONES,)
_MODELS: dict[str, type[SkiSegment] | type[SkiTour] | type[RestZone]] = {
    SKI_NETWORK: SkiSegment,
    SKI_TOURS: SkiTour,
    REST_ZONES: RestZone,
}
_SOURCE_MODELS: dict[
    str, type[SourceSkiSegment] | type[SourceSkiTour] | type[SourceRestZone]
] = {
    SKI_NETWORK: SourceSkiSegment,
    SKI_TOURS: SourceSkiTour,
    REST_ZONES: SourceRestZone,
}
# How the host's own apply reads a service row: as the v2 feed serves it,
# so a tour carries its walk and drawing (SNOW-1316).
_SERIALISERS: dict[str, Callable[[Any], dict[str, Any]]] = {
    SKI_NETWORK: serialise_source_document,
    SKI_TOURS: serialise_source_ski_tour,
    REST_ZONES: serialise_source_document,
}

# How many service rows the host's own apply reads at a time.
APPLY_CHUNK = 500

# A tour is far heavier than a segment: it carries SAC's whole document,
# its walk and its drawing, and a page holds each one three times over
# (the row, its feed item, the model it writes). 500 at once ran the
# production instance out of memory on its first full apply, so tours
# are read and committed a few at a time. Each page commits on its own,
# so an interrupted apply resumes where it stopped.
TOUR_APPLY_CHUNK = 20

# SNOW-1322: the whole network, thinned as the tours overview is, for draw
# mode zoomed out past the per-view load. ~3.1 MB, ~0.6 MB gzipped.
_OVERVIEW_CACHE_KEY = "routes:ski-network-overview:v1:{count}:{stamp}"
_OVERVIEW_CACHE_SECONDS = 7 * 24 * 3600


class SkiItemError(ValueError):
    """An item that cannot be applied: no uuid, no timestamp, no geometry."""


# How deep each GeoJSON type nests its positions: a LineString is a list of
# positions, a MultiPolygon a list of polygons of rings of positions.
_POSITION_DEPTH = {
    "LineString": 1,
    "MultiLineString": 2,
    "Polygon": 2,
    "MultiPolygon": 3,
}


def bbox_of(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    """Return a GeoJSON line's or polygon's bounding box.

    Args:
        geometry: A ``LineString``, ``MultiLineString``, ``Polygon`` or
            ``MultiPolygon``.

    Returns:
        ``(west, south, east, north)``.

    Raises:
        SkiItemError: The geometry holds no coordinate.

    """
    positions: list[Any] = list(geometry.get("coordinates") or [])
    for _level in range(_POSITION_DEPTH.get(str(geometry.get("type")), 2) - 1):
        positions = [position for part in positions for position in part]
    lons = [point[0] for point in positions]
    lats = [point[1] for point in positions]
    if not lons:
        raise SkiItemError("the line has no coordinates")
    return min(lons), min(lats), max(lons), max(lats)


def _segment_values(document: dict[str, Any]) -> dict[str, Any]:
    """Return the ``SkiSegment`` columns a segment document sets."""
    return {
        "segment_id": int(document["segment_id"]),
        "discipline": str(document.get("discipline") or ""),
        "direction": document.get("direction"),
        "access": document.get("access"),
    }


def _tour_values(document: dict[str, Any]) -> dict[str, Any]:
    """Return the ``SkiTour`` columns a tour document sets."""
    return {
        "sac_id": int(document["sac_id"]),
        "name": str(document.get("name") or "")[:255],
        "names": document.get("names") or {},
        "grade": str(document.get("grade") or "")[:8],
        "grades": document.get("grades") or {},
        "difficulty": document.get("difficulty"),
        "ascent_m": document.get("ascent_m"),
        "ascent_time_label": str(document.get("ascent_time_label") or "")[:32],
        "descent_m": document.get("descent_m"),
        "target_name": str(document.get("target_name") or "")[:255],
        "target_altitude_m": document.get("target_altitude_m"),
        "urls": document.get("urls") or {},
        "source": str(document.get("source") or "")[:64],
    }


def _rest_zone_values(document: dict[str, Any]) -> dict[str, Any]:
    """Return the ``RestZone`` columns a rest-zone document sets (SNOW-1263)."""
    return {
        "feature_id": int(document["feature_id"]),
        "name": str(document.get("name") or "")[:255],
        "binding": bool(document.get("binding")),
        "restriction": document.get("restriction") or {},
        "period": str(document.get("period") or "")[:64],
        "canton": str(document.get("canton") or "")[:2],
        "url": str(document.get("url") or "")[:500],
    }


def _tour_terrain(item: dict[str, Any]) -> dict[str, Any]:
    """Return the walk and drawing the service made of a tour (SNOW-1316).

    Copied as served, never computed here: the service walks every tour
    once and this instance draws what it was given. An item without them
    — a tour the service has not rendered yet, or one whose line has just
    changed — clears both, through the one rule (SNOW-1309), and the tour
    is drawn as SAC's own line until the service has walked it.
    """
    render = item.get("track_render")
    record = item.get("slope_samples")
    if not isinstance(render, dict) or not isinstance(record, dict):
        return cleared_terrain()
    return {"slope_samples": record, "track_render": render}


def _values(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    """Return every column one live item sets.

    Raises:
        SkiItemError: The item is malformed.

    """
    stamp = parse_datetime(str(item.get("updated_at") or ""))
    if stamp is None:
        raise SkiItemError("the item has no updated_at")
    document = item.get("document") or {}
    geometry = document.get("geometry") or {}
    west, south, east, north = bbox_of(geometry)
    try:
        if kind == SKI_NETWORK:
            values = _segment_values(document)
        elif kind == REST_ZONES:
            values = _rest_zone_values(document)
        else:
            values = {**_tour_values(document), **_tour_terrain(item)}
    except (KeyError, TypeError, ValueError) as exc:
        raise SkiItemError(f"malformed document: {exc}") from exc
    return {
        **values,
        "source_updated_at": stamp,
        "geometry": geometry,
        "west": west,
        "south": south,
        "east": east,
        "north": north,
    }


def _key(item: dict[str, Any]) -> uuid.UUID | None:
    """Return an item's service uuid, or None when it has none."""
    try:
        return uuid.UUID(str(item.get("uuid")))
    except ValueError:
        return None


def _decide(
    kind: str, item: dict[str, Any], key: uuid.UUID, row: Any
) -> tuple[str, dict[str, Any]]:
    """Return what applying one item does, and the columns it writes.

    Args:
        kind: ``ski-network``, ``ski-tours`` or ``rest-zones``.
        item: The item.
        key: Its service uuid.
        row: The slim row this instance holds for it, or None.

    Returns:
        ``(outcome, values)``; ``values`` is empty unless the outcome writes.

    """
    if item.get("deleted_at"):
        return (ABSENT if row is None else DELETED), {}
    try:
        values = _values(kind, item)
    except SkiItemError as exc:
        logger.warning("ski apply: %s item %s failed: %s", kind, key, exc)
        return FAILED, {}
    if row is None:
        return CREATED, values
    if row.source_updated_at == values["source_updated_at"]:
        return UNCHANGED, {}
    return UPDATED, values


def apply_ski_items(
    kind: str, items: Iterable[dict[str, Any]], *, commit: bool
) -> Counter[str]:
    """Apply one page of ``ski-network`` or ``ski-tours`` items.

    The rows the page names are read in one query, slim; new rows are
    written in one ``bulk_create``. A malformed item is logged and counted
    ``failed``, and then the page writes nothing at all: an instance's
    "since" is the newest line it holds, so a later item written past a
    failed one would carry the next pull beyond it and it would never be
    retried. Callers stop at such a page for the same reason.

    Args:
        kind: ``ski-network``, ``ski-tours`` or ``rest-zones``.
        items: The page's items.
        commit: Write; otherwise only decide.

    Returns:
        How many items had each outcome.

    """
    model = _MODELS[kind]
    counts: Counter[str] = Counter()
    page = [(item, _key(item)) for item in items]
    held = {
        row.source_uuid: row
        for row in model.objects.filter(
            source_uuid__in=[key for _item, key in page if key is not None]
        ).only("id", "source_uuid", "source_updated_at")
    }
    new: list[Any] = []
    changed: list[tuple[int, dict[str, Any]]] = []
    gone: list[int] = []
    for item, key in page:
        if key is None:
            logger.warning("ski apply: %s item has no uuid", kind)
            counts[FAILED] += 1
            continue
        outcome, values = _decide(kind, item, key, held.get(key))
        counts[outcome] += 1
        if outcome == CREATED:
            new.append(model(source_uuid=key, **values))
        elif outcome == UPDATED:
            changed.append((held[key].pk, values))
        elif outcome == DELETED:
            gone.append(held[key].pk)
    if not commit or counts[FAILED]:
        return counts
    with transaction.atomic():
        for pk, values in changed:
            model.objects.filter(pk=pk).update(**values)
        model.objects.bulk_create(new)
        model.objects.filter(pk__in=gone).delete()
    return counts


def synced_since(kind: str) -> dt.datetime | None:
    """Return the newest service ``updated_at`` this instance holds for ``kind``.

    Args:
        kind: ``ski-network``, ``ski-tours`` or ``rest-zones``.

    Returns:
        The timestamp, or None when nothing is held.

    """
    newest: dt.datetime | None = _MODELS[kind].objects.aggregate(
        newest=Max("source_updated_at")
    )["newest"]
    return newest


def _service_pages(
    kind: str, since: dt.datetime | None
) -> Iterator[list[dict[str, Any]]]:
    """Yield the service's rows of ``kind`` changed since ``since``, as feed items.

    In chunks of ``APPLY_CHUNK`` segments or ``TOUR_APPLY_CHUNK`` tours, so
    the geometry is never held whole.
    """
    size = TOUR_APPLY_CHUNK if kind == SKI_TOURS else APPLY_CHUNK
    rows = _SOURCE_MODELS[kind].objects.order_by("updated_at", "id")
    if since is not None:
        rows = rows.filter(updated_at__gte=since)
    chunk: list[dict[str, Any]] = []
    for row in rows.iterator(chunk_size=size):
        chunk.append(_SERIALISERS[kind](row))
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def apply_from_service(
    *, commit: bool, kinds: tuple[str, ...] = SKI_KINDS
) -> dict[str, Counter[str]]:
    """Apply the service's own rows here, on the host that imports them.

    Reads only what changed since the newest row held, as a pull would.

    Args:
        commit: Write; otherwise only decide.
        kinds: The kinds to apply: the two ski kinds by default, or
            ``REST_ZONE_KINDS`` for ``import_rest_zones``.

    Returns:
        Each kind's outcomes.

    """
    result: dict[str, Counter[str]] = {}
    for kind in kinds:
        counts: Counter[str] = Counter()
        for page in _service_pages(kind, synced_since(kind)):
            outcomes = apply_ski_items(kind, page, commit=commit)
            counts.update(outcomes)
            if outcomes[FAILED]:
                break
        result[kind] = counts
    return result


def apply_source_tour(source_pk: int) -> Counter[str]:
    """Apply one of the service's tours here, as soon as it is drawn (SNOW-1345).

    The per-tour task calls this after it renders a tour, so the tour is
    on this host's map at once rather than at the end of a long run. It
    is the same ``apply_ski_items`` a page goes through, so the two
    paths cannot drift.

    Args:
        source_pk: The ``SourceSkiTour``'s primary key.

    Returns:
        The item's outcome, or an empty count when the row is gone.

    """
    row = SourceSkiTour.objects.filter(pk=source_pk).first()
    if row is None:
        return Counter()
    return apply_ski_items(SKI_TOURS, [serialise_source_ski_tour(row)], commit=True)


def _network_stamp() -> str:
    """Return the overview's cache key, which changes when the network does.

    It carries the segments' count and newest ``source_updated_at``, not
    ``updated_at``: ``apply_ski_items`` rewrites a row with
    ``QuerySet.update()``, which leaves ``auto_now`` where it was.
    """
    stamp = SkiSegment.objects.aggregate(
        count=Count("id"), newest=Max("source_updated_at")
    )
    return _OVERVIEW_CACHE_KEY.format(
        count=stamp["count"],
        stamp=stamp["newest"].timestamp() if stamp["newest"] else 0,
    )


def network_overview() -> list[dict[str, Any]]:
    """Return every skiable segment as a thinned line Feature, cached.

    SNOW-1322. What draw mode shows from z9, where the SAC tours start,
    until z12, where the per-view load and snapping take over. Only drawn,
    never snapped to, so a Feature carries no id and no properties.

    Returns:
        The Features, in segment id order.

    """
    key = _network_stamp()
    cached: list[dict[str, Any]] | None = cache.get(key)
    if cached is None:
        cached = []
        rows = SkiSegment.objects.skiable().order_by("segment_id").only("geometry")
        for row in rows.iterator(chunk_size=APPLY_CHUNK):
            geometry = overview_geometry(row.geometry)
            if geometry is not None:
                cached.append(
                    {"type": "Feature", "geometry": geometry, "properties": {}}
                )
        cache.set(key, cached, _OVERVIEW_CACHE_SECONDS)
    return cached
