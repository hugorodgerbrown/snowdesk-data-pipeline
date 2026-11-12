"""
apps/ingest/ski.py — The service's copy of swisstopo's ski-touring map (SNOW-1276).

``import_ski_archive(path)`` makes the service's ``SourceSkiSegment`` and
``SourceSkiTour`` rows equal what one swisstopo GeoPackage zip holds:

* each network segment, keyed by a uuid5 of swisstopo's segment id;
* each named SAC tour, keyed by swisstopo's own ``route_uuid``. The
  routes layer's unnamed lines (no SAC id, "no route information") are
  not tours and are skipped; their ground is in the network anyway.

Both go through ``sync_source_documents``: an unchanged row is not
rewritten, a line swisstopo drops is tombstoned, and one that comes back
is revived, so the v2 feeds' "since" stays quiet between releases.

A row that cannot be read is logged at ERROR and handed on as an
``UnreadableRecord`` (SNOW-1489): its stored copy is kept, not tombstoned,
and the import's counts show it as ``failed``. A tour whose ``route_uuid``
is unreadable too carries no uuid, which holds back that layer's
tombstones for the run.

The zip is read with the standard library: a GeoPackage is a SQLite file,
and ``apps/ingest/normalise/swisstopo_ski.py`` reads its geometry blobs.
Each layer is streamed row by row, never loaded whole.
"""

from __future__ import annotations

import logging
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from django.utils.text import slugify

from apps.ingest.models import SourceSkiSegment, SourceSkiTour
from apps.ingest.normalise.swisstopo_ski import (
    NETWORK_TABLE,
    ROUTES_TABLE,
    GeometryError,
    is_tour,
    segment_document,
    segment_uuid,
    tour_document,
    tour_uuid,
)
from apps.ingest.reference import (
    DocumentRecord,
    UnreadableRecord,
    sync_source_documents,
)

logger = logging.getLogger(__name__)

# The kinds the import reports, in order: the feed names they are served as.
SKI_NETWORK = "ski-network"
SKI_TOURS = "ski-tours"


class SkiArchiveError(ValueError):
    """The zip is not swisstopo's ski-touring GeoPackage pair."""


def _rows(path: Path, table: str) -> Iterator[dict[str, Any]]:
    """Yield every row of ``table`` in the GeoPackage at ``path``, by fid.

    Args:
        path: A GeoPackage file.
        table: The feature table.

    Yields:
        Each row as a dict, by column name.

    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        # The table name is one of the two module constants, never input.
        for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY fid'):  # noqa: S608 — constant table name, see comment
            yield dict(row)
    finally:
        connection.close()


def segment_records(path: Path) -> Iterator[DocumentRecord | UnreadableRecord]:
    """Yield the network GeoPackage's segments as records.

    A segment whose geometry cannot be read is logged at ERROR and yielded
    as an ``UnreadableRecord`` under its uuid rather than failing the
    import: one broken line must not fail, or tombstone, the rest. One
    whose ``segm_id`` cannot be read is yielded with no uuid, which holds
    back the layer's tombstones for that run.

    Args:
        path: ``ski_network_2056.gpkg``.

    Yields:
        One record per segment.

    """
    for row in _rows(path, NETWORK_TABLE):
        label = f"segment {row['segm_id']}"
        try:
            key = segment_uuid(int(row["segm_id"]))
        except TypeError, ValueError:
            logger.error("ski import: %s has no readable segm_id", label)
            yield UnreadableRecord(uuid=None, label=label)
            continue
        try:
            document = segment_document(row)
        except GeometryError:
            logger.error("ski import: %s has no readable line", label)
            yield UnreadableRecord(uuid=key, label=label)
            continue
        yield DocumentRecord(
            uuid=segment_uuid(document["segment_id"]),
            slug=f"segment-{document['segment_id']}",
            document=document,
        )


def tour_records(path: Path) -> Iterator[DocumentRecord | UnreadableRecord]:
    """Yield the routes GeoPackage's named tours as records.

    The uuid is read first, so a tour whose document cannot be read is
    yielded as an ``UnreadableRecord`` under it and its stored copy kept.
    A tour whose ``route_uuid`` cannot be read is yielded with no uuid.
    Either is logged at ERROR.

    Args:
        path: ``ski_routes_2056.gpkg``.

    Yields:
        One record per named tour.

    """
    for row in _rows(path, ROUTES_TABLE):
        if not is_tour(row):
            continue
        label = f"tour {row['sacid']}"
        try:
            key = tour_uuid(row)
        except ValueError:
            logger.error("ski import: %s has no readable route_uuid", label)
            yield UnreadableRecord(uuid=None, label=label)
            continue
        try:
            document = tour_document(row)
        except GeometryError, ValueError:
            logger.error("ski import: %s is unreadable", label)
            yield UnreadableRecord(uuid=key, label=label)
            continue
        yield DocumentRecord(
            uuid=key,
            slug=(slugify(document["name"])[:200] or "tour") + f"-{document['sac_id']}",
            document=document,
        )


def _member(archive: zipfile.ZipFile, table: str) -> str:
    """Return the name of the zip member holding ``table``'s GeoPackage.

    Raises:
        SkiArchiveError: The zip has no such member.

    """
    for name in archive.namelist():
        if Path(name).name == f"{table}.gpkg":
            return name
    raise SkiArchiveError(f"the archive has no {table}.gpkg")


def import_ski_archive(path: Path) -> dict[str, dict[str, int]]:
    """Make the ski network and tours equal one swisstopo GeoPackage zip.

    Args:
        path: The ``skitouren_2056.gpkg.zip`` swisstopo publishes.

    Returns:
        Each kind's counts (``created``, ``updated``, ``deleted``,
        ``unchanged``, ``failed``), keyed ``ski-network`` and ``ski-tours``.

    Raises:
        SkiArchiveError: The zip lacks either GeoPackage.

    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise SkiArchiveError(f"{path} is not a zip") from exc
    with archive, tempfile.TemporaryDirectory() as scratch:
        network = Path(archive.extract(_member(archive, NETWORK_TABLE), scratch))
        routes = Path(archive.extract(_member(archive, ROUTES_TABLE), scratch))
        result = {
            SKI_NETWORK: sync_source_documents(
                SourceSkiSegment, segment_records(network)
            ),
            SKI_TOURS: sync_source_documents(SourceSkiTour, tour_records(routes)),
        }
    logger.info("import_ski_archive: %s", result)
    return result
