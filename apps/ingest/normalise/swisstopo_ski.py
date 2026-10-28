"""
apps/ingest/normalise/swisstopo_ski.py — Reading swisstopo's ski-touring GeoPackage.

swisstopo publishes its ski-touring map as one GeoPackage per layer, in the
Swiss LV95 grid (EPSG:2056), under the STAC collection
``ch.swisstopo-karto.skitouren`` (SNOW-1276). Two layers are read:

  ski_network_2056
      The network: about 20,000 segments that meet at shared ends, so they
      form a graph a drawn route can snap to and be routed along.
  ski_routes_2056
      The named routes: SAC's tours, each one line from a start to a
      summit or back down, with its grade, height gain and links. Rows
      with no SAC id carry no route information at all ("Keine
      Routeninfo verfügbar") and are not routes; they are skipped.

Everything here takes bytes or rows and returns plain values; none of it
reads a setting, the network or the database:

  parse_gpkg_lines(blob)
      A GeoPackage geometry blob (header + WKB) as a list of lines, each a
      list of ``(easting, northing)`` pairs.
  lv95_to_wgs84(easting, northing)
      swisstopo's published approximation, good to about a metre.
  simplify(points, tolerance_m)
      Douglas-Peucker, run in grid metres before the transform.
  segment_document(row) / tour_document(row)
      One row of each layer as the document the service stores.

The licence is OGD; the credit is "© swisstopo".
"""

from __future__ import annotations

import logging
import struct
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

# The tolerance lines are simplified to, in metres. A snapped route follows
# the line within this, which is well inside a GPS track's own scatter.
SIMPLIFY_TOLERANCE_M = 5.0

# Decimal places a WGS84 coordinate keeps: 1e-6 degrees is about 0.1 m.
COORDINATE_PLACES = 6

# The namespace a segment's uuid is derived in. swisstopo gives a segment
# an integer id and no uuid; the service needs one that is the same on
# every import, so it is a uuid5 of the id.
SEGMENT_NAMESPACE = uuid.UUID("5b0f5c34-1d4e-4b57-9c8e-3c2f2b5a7e11")

# The tables each layer's GeoPackage holds.
NETWORK_TABLE = "ski_network_2056"
ROUTES_TABLE = "ski_routes_2056"

# The languages swisstopo names a route and links its SAC page in.
LANGUAGES: tuple[str, ...] = ("de", "fr", "it", "en")

# Envelope bytes after the 8-byte GeoPackage header, by the flags' envelope code.
_ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
_WKB_LINESTRING = 2
_WKB_MULTILINESTRING = 5


class GeometryError(ValueError):
    """A geometry blob that is not a GeoPackage line or multi-line."""


def parse_gpkg_lines(blob: bytes) -> list[list[tuple[float, float]]]:
    """Return a GeoPackage line geometry as its lines of ``(x, y)`` pairs.

    Reads the GeoPackage binary header (magic, version, flags, srs id, an
    optional envelope) and then the WKB body, which may be a LineString or
    a MultiLineString. A Z or M ordinate is not expected and is refused.

    Args:
        blob: The ``geom`` column's bytes.

    Returns:
        One list of points per line.

    Raises:
        GeometryError: The blob is not a GeoPackage line geometry.

    """
    if len(blob) < 8 or blob[:2] != b"GP":
        raise GeometryError("not a GeoPackage geometry")
    envelope = _ENVELOPE_BYTES.get((blob[3] >> 1) & 0b111)
    if envelope is None:
        raise GeometryError("unknown envelope code")
    body = memoryview(blob)[8 + envelope :]

    def read(offset: int) -> tuple[list[list[tuple[float, float]]], int]:
        """Read one WKB geometry at ``offset``; return its lines and the next offset."""
        try:
            order = "<" if body[offset] == 1 else ">"
            kind = struct.unpack_from(order + "I", body, offset + 1)[0]
            offset += 5
            count = struct.unpack_from(order + "I", body, offset)[0]
            offset += 4
            if kind == _WKB_LINESTRING:
                points = [
                    struct.unpack_from(order + "dd", body, offset + 16 * i)
                    for i in range(count)
                ]
                return [list(points)], offset + 16 * count
            if kind == _WKB_MULTILINESTRING:
                lines: list[list[tuple[float, float]]] = []
                for _ in range(count):
                    part, offset = read(offset)
                    lines.extend(part)
                return lines, offset
        except (IndexError, struct.error) as exc:
            raise GeometryError("truncated geometry") from exc
        raise GeometryError(f"unsupported WKB type {kind}")

    return read(0)[0]


def lv95_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Return an LV95 coordinate as WGS84 ``(longitude, latitude)``.

    swisstopo's approximate formulas ("Formulas and constants for the
    calculation of the Swiss conformal cylindrical projection"), accurate
    to about a metre across Switzerland — well inside the 5 m the lines are
    simplified to.

    Args:
        easting: LV95 E, in metres (about 2,480,000 to 2,840,000).
        northing: LV95 N, in metres (about 1,070,000 to 1,300,000).

    Returns:
        ``(longitude, latitude)`` in degrees, rounded to ``COORDINATE_PLACES``.

    """
    y = (easting - 2_600_000) / 1_000_000
    x = (northing - 1_200_000) / 1_000_000
    lam = (
        2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x * x - 0.0436 * y**3
    )
    phi = (
        16.9023892
        + 3.238272 * x
        - 0.270978 * y * y
        - 0.002528 * x * x
        - 0.0447 * y * y * x
        - 0.0140 * x**3
    )
    return (
        round(lam * 100 / 36, COORDINATE_PLACES),
        round(phi * 100 / 36, COORDINATE_PLACES),
    )


def _offset(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """Return how far ``point`` lies from the segment ``start``–``end``, in metres."""
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    if length == 0:
        return float(((px - ax) ** 2 + (py - ay) ** 2) ** 0.5)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return float(((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5)


def simplify(
    points: Sequence[tuple[float, float]], tolerance_m: float = SIMPLIFY_TOLERANCE_M
) -> list[tuple[float, float]]:
    """Return ``points`` thinned by Douglas-Peucker, keeping both ends.

    Iterative, so a long line cannot hit the recursion limit. The ends are
    always kept: they are where segments meet, and a junction that moved
    would break the graph.

    Args:
        points: A line in grid metres.
        tolerance_m: The largest offset a dropped point may have.

    Returns:
        The kept points, in order.

    """
    if len(points) <= 2:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        furthest, index = 0.0, first
        for i in range(first + 1, last):
            offset = _offset(points[i], points[first], points[last])
            if offset > furthest:
                furthest, index = offset, i
        if furthest > tolerance_m:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [point for point, kept in zip(points, keep, strict=True) if kept]


def to_geometry(blob: bytes) -> dict[str, Any]:
    """Return a GeoPackage line geometry as simplified WGS84 GeoJSON.

    Always a ``MultiLineString``, whatever the source held, so a reader
    has one shape to handle.

    Args:
        blob: The ``geom`` column's bytes.

    Returns:
        A GeoJSON geometry.

    """
    lines = [
        [list(lv95_to_wgs84(e, n)) for e, n in simplify(line)]
        for line in parse_gpkg_lines(blob)
        if len(line) >= 2
    ]
    return {"type": "MultiLineString", "coordinates": lines}


def segment_uuid(segment_id: int) -> uuid.UUID:
    """Return the service's uuid for swisstopo segment ``segment_id``."""
    return uuid.uuid5(SEGMENT_NAMESPACE, f"segment:{segment_id}")


def _int(value: object) -> int | None:
    """Return ``value`` as an int, or None when it is empty."""
    return None if value is None or value == "" else int(str(value))


def segment_document(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return one network row as the document the service stores.

    ``direction`` and ``access`` are kept as swisstopo codes them: the STAC
    record does not say what their values mean, and nothing reads them yet.

    Args:
        row: A ``ski_network_2056`` row, by column name.

    Returns:
        The document.

    """
    return {
        "segment_id": int(row["segm_id"]),
        "discipline": str(row["discipline"] or ""),
        "direction": _int(row["direction"]),
        "access": _int(row["access"]),
        "route_info": _int(row["route_info"]),
        "geometry": to_geometry(row["geom"]),
    }


def is_tour(row: Mapping[str, Any]) -> bool:
    """Return whether a routes row is a named SAC tour.

    The layer also holds lines with no route information at all, named
    "Keine Routeninfo verfügbar" in four languages; those have no SAC id.
    """
    return row["sacid"] is not None


def tour_uuid(row: Mapping[str, Any]) -> uuid.UUID:
    """Return the service's uuid for a tour: swisstopo's own ``route_uuid``.

    Raises:
        ValueError: The row has no usable ``route_uuid``.

    """
    return uuid.UUID(str(row["route_uuid"]).strip("{}"))


def tour_document(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return one named route as the document the service stores.

    Args:
        row: A ``ski_routes_2056`` row, by column name.

    Returns:
        The document.

    """
    target = row["target_altitude"]
    return {
        "sac_id": int(row["sacid"]),
        "sac_tour_id": _int(row["sactid"]),
        "route_number": row["route_nr"] or "",
        "source": row["source"] or "",
        "source_text": row["source_txt"] or "",
        "name": row["name"] or "",
        "names": {lang: row[f"name_{lang}"] or "" for lang in LANGUAGES},
        "difficulty": _int(row["difficulty"]),
        "grade": row["difficulty_en"] or "",
        "grades": {lang: row[f"difficulty_{lang}"] or "" for lang in LANGUAGES},
        "ascent_m": _int(row["ascent_altitude"]),
        "ascent_time_min": _int(row["ascent_time"]),
        "ascent_time_label": row["ascent_time_label"] or "",
        "descent_m": _int(row["descent_altitude"]),
        "target_name": row["target_name"] or "",
        "target_altitude_m": None if target is None else round(float(target)),
        "urls": {lang: row[f"url_sac_{lang}"] or "" for lang in LANGUAGES},
        "geometry": to_geometry(row["geom"]),
    }
