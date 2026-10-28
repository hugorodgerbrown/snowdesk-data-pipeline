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
from apps.ingest.reference import DocumentRecord, sync_source_documents

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


def segment_records(path: Path) -> Iterator[DocumentRecord]:
    """Yield the network GeoPackage's segments as records.

    A segment whose geometry cannot be read is logged and skipped rather
    than failing the import: one broken line must not tombstone the rest.

    Args:
        path: ``ski_network_2056.gpkg``.

    Yields:
        One record per segment.

    """
    for row in _rows(path, NETWORK_TABLE):
        try:
            document = segment_document(row)
        except GeometryError:
            logger.warning(
                "ski import: segment %s has no readable line", row["segm_id"]
            )
            continue
        yield DocumentRecord(
            uuid=segment_uuid(document["segment_id"]),
            slug=f"segment-{document['segment_id']}",
            document=document,
        )


def tour_records(path: Path) -> Iterator[DocumentRecord]:
    """Yield the routes GeoPackage's named tours as records.

    Args:
        path: ``ski_routes_2056.gpkg``.

    Yields:
        One record per named tour.

    """
    for row in _rows(path, ROUTES_TABLE):
        if not is_tour(row):
            continue
        try:
            key = tour_uuid(row)
            document = tour_document(row)
        except GeometryError, ValueError:
            logger.warning("ski import: tour %s is unreadable", row["sacid"])
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
        ``unchanged``), keyed ``ski-network`` and ``ski-tours``.

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
