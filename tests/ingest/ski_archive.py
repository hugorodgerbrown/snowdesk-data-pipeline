"""
tests/ingest/ski_archive.py — A small swisstopo ski-touring archive, built in the test.

swisstopo's zip holds two GeoPackages, ``ski_network_2056.gpkg`` and
``ski_routes_2056.gpkg``. A GeoPackage is a SQLite file whose geometry
column holds a GeoPackage header followed by WKB, so the fixture is
written here with the standard library: the same columns as the real
layers (checked against the 15 Dec 2025 release), a handful of rows, and
lines in LV95 metres near the Silvrettahütte.

  gpkg_blob(lines, envelope=True)   one geometry column value
  segment_row(segm_id, ...)         one network row, by column name
  tour_row(sacid, ...)              one routes row, by column name
  write_archive(path, segments, tours)
"""

from __future__ import annotations

import sqlite3
import struct
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# A short line in LV95 near the Silvrettahütte, and a second one sharing
# its end, so the two meet as network segments do.
LINE_A: list[tuple[float, float]] = [
    (2_801_000.0, 1_190_000.0),
    (2_801_100.0, 1_190_050.0),
    (2_801_200.0, 1_190_100.0),
]
LINE_B: list[tuple[float, float]] = [
    (2_801_200.0, 1_190_100.0),
    (2_801_400.0, 1_190_400.0),
]

NETWORK_COLUMNS = (
    "fid",
    "geom",
    "segm_id",
    "access",
    "direction",
    "route_info",
    "discipline",
)
ROUTES_COLUMNS = (
    "fid", "geom", "sacid", "sactid", "discipline", "source", "name",
    "name_de", "name_fr", "name_it", "name_en", "difficulty_de",
    "ascent_altitude", "ascent_time_label", "ascent_time", "descent_altitude",
    "url_sac_de", "url_sac_fr", "url_sac_it", "url_sac_en", "difficulty_fr",
    "difficulty_it", "difficulty_en", "difficulty", "target_name",
    "target_altitude", "route_uuid", "route_nr", "source_txt", "source_url",
)  # fmt: skip


def _wkb_line(points: Sequence[tuple[float, float]]) -> bytes:
    """Return a little-endian WKB LineString."""
    body = struct.pack("<BII", 1, 2, len(points))
    return body + b"".join(struct.pack("<dd", x, y) for x, y in points)


def gpkg_blob(
    lines: Sequence[Sequence[tuple[float, float]]], *, envelope: bool = True
) -> bytes:
    """Return a GeoPackage MultiLineString blob (header, optional envelope, WKB).

    Args:
        lines: Each line's LV95 points.
        envelope: Write the 32-byte XY envelope, as swisstopo's file does.

    Returns:
        The column value.

    """
    flags = 0b0000_0011 if envelope else 0b0000_0001  # little-endian; envelope code 1
    header = b"GP" + bytes([0, flags]) + struct.pack("<i", 2056)
    if envelope:
        xs = [x for line in lines for x, _ in line]
        ys = [y for line in lines for _, y in line]
        header += struct.pack("<dddd", min(xs), max(xs), min(ys), max(ys))
    body = struct.pack("<BII", 1, 5, len(lines)) + b"".join(
        _wkb_line(part) for part in lines
    )
    return header + body


def segment_row(
    segm_id: int,
    *,
    line: Sequence[tuple[float, float]] = LINE_A,
    discipline: str = "ski_tour",
    geom: bytes | None = None,
) -> dict[str, Any]:
    """Return one ``ski_network_2056`` row."""
    return {
        "fid": segm_id,
        "geom": geom if geom is not None else gpkg_blob([line]),
        "segm_id": segm_id,
        "access": 0,
        "direction": 0,
        "route_info": 1,
        "discipline": discipline,
    }


def tour_row(
    sacid: int | None,
    *,
    route_uuid: str = "{7e61c1c7-1ec5-41e7-996b-b28db78be4f0}",
    name: str = "Piz Buin, von der Silvrettahütte",
    line: Sequence[tuple[float, float]] = LINE_A,
) -> dict[str, Any]:
    """Return one ``ski_routes_2056`` row; ``sacid=None`` is an unnamed line."""
    unnamed = sacid is None
    row: dict[str, Any] = dict.fromkeys(ROUTES_COLUMNS)
    row.update(
        {
            "fid": sacid or 9999,
            "geom": gpkg_blob([line]),
            "sacid": sacid,
            "discipline": "ski_tour",
            "source": "swisstopo_sac",
            "name": "Keine Routeninfo verfügbar" if unnamed else name,
            "name_de": "Keine Routeninfo verfügbar" if unnamed else name,
            "name_en": "No route information available" if unnamed else name,
            "route_uuid": route_uuid,
            "source_txt": "©swisstopo",
        }
    )
    if not unnamed:
        row.update(
            {
                "sactid": 12295,
                "difficulty_de": "WS",
                "difficulty_fr": "PD",
                "difficulty_it": "PD",
                "difficulty_en": "PD",
                "difficulty": 4,
                "ascent_altitude": 1100,
                "ascent_time_label": "04:00 h",
                "ascent_time": 240,
                "target_name": "Piz Buin",
                "target_altitude": 3311.6,
                "url_sac_en": "https://www.sac-cas.ch/en/huts-and-tours/1",
                "route_nr": "A143",
            }
        )
    return row


def _write_gpkg(
    path: Path, table: str, columns: Sequence[str], rows: Sequence[dict[str, Any]]
) -> None:
    """Write one feature table to a new SQLite file."""
    connection = sqlite3.connect(path)
    try:
        connection.execute(f'CREATE TABLE "{table}" ({", ".join(columns)})')
        marks = ", ".join("?" for _ in columns)
        insert = f'INSERT INTO "{table}" VALUES ({marks})'  # noqa: S608 — module constants, never input
        connection.executemany(insert, [tuple(row[c] for c in columns) for row in rows])
        connection.commit()
    finally:
        connection.close()


def write_archive(
    path: Path,
    segments: Sequence[dict[str, Any]] = (),
    tours: Sequence[dict[str, Any]] = (),
) -> Path:
    """Write a swisstopo-shaped zip holding both GeoPackages.

    Args:
        path: The zip to write.
        segments: ``segment_row()`` values.
        tours: ``tour_row()`` values.

    Returns:
        ``path``.

    """
    with tempfile.TemporaryDirectory() as scratch:
        network = Path(scratch) / "ski_network_2056.gpkg"
        routes = Path(scratch) / "ski_routes_2056.gpkg"
        _write_gpkg(network, "ski_network_2056", NETWORK_COLUMNS, segments)
        _write_gpkg(routes, "ski_routes_2056", ROUTES_COLUMNS, tours)
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(routes, routes.name)
            archive.write(network, network.name)
    return path
