"""
tests/ingest/normalise/test_swisstopo_ski.py — swisstopo's ski-touring GeoPackage, read (SNOW-1276).

The geometry blob parser (with and without an envelope, both WKB line
types, and the refusals), the LV95 transform against swisstopo's own
reference point, Douglas-Peucker, and each layer's row as a document.
"""

from __future__ import annotations

import struct
import uuid

import pytest

from apps.ingest.normalise.swisstopo_ski import (
    GeometryError,
    is_tour,
    lv95_to_wgs84,
    parse_gpkg_lines,
    segment_document,
    segment_uuid,
    simplify,
    to_geometry,
    tour_document,
    tour_uuid,
)
from tests.ingest.ski_archive import LINE_A, LINE_B, gpkg_blob, segment_row, tour_row


@pytest.mark.parametrize("envelope", [True, False])
def test_a_multilinestring_is_read_with_or_without_its_envelope(envelope: bool) -> None:
    """Every line, every point, in order."""
    lines = parse_gpkg_lines(gpkg_blob([LINE_A, LINE_B], envelope=envelope))
    assert lines == [LINE_A, LINE_B]


def test_a_bare_linestring_is_read() -> None:
    """A LineString body is one line."""
    header = b"GP" + bytes([0, 1]) + struct.pack("<i", 2056)
    body = struct.pack("<BII", 1, 2, 2) + struct.pack("<dddd", 1.0, 2.0, 3.0, 4.0)
    assert parse_gpkg_lines(header + body) == [[(1.0, 2.0), (3.0, 4.0)]]


def test_a_big_endian_body_is_read() -> None:
    """The byte-order flag of each WKB geometry is honoured."""
    header = b"GP" + bytes([0, 1]) + struct.pack("<i", 2056)
    body = struct.pack(">BII", 0, 2, 1) + struct.pack(">dd", 5.0, 6.0)
    assert parse_gpkg_lines(header + body) == [[(5.0, 6.0)]]


@pytest.mark.parametrize(
    ("blob", "message"),
    [
        (b"XX\x00\x01\x00\x00\x00\x00", "not a GeoPackage"),
        (b"GP\x00" + bytes([0b1110]) + b"\x00\x00\x00\x00", "envelope"),
        (gpkg_blob([LINE_A])[:-4], "truncated"),
        (
            b"GP\x00\x01" + struct.pack("<i", 2056) + struct.pack("<BII", 1, 1, 0),
            "unsupported",
        ),
    ],
)
def test_a_blob_that_is_not_a_line_is_refused(blob: bytes, message: str) -> None:
    """A bad magic, envelope code, a cut-off body or a point all raise."""
    with pytest.raises(GeometryError, match=message):
        parse_gpkg_lines(blob)


def test_lv95_reference_point_is_bern() -> None:
    """swisstopo's projection centre maps to the old Bern observatory."""
    lon, lat = lv95_to_wgs84(2_600_000, 1_200_000)
    assert lon == pytest.approx(7.438633, abs=1e-5)
    assert lat == pytest.approx(46.951081, abs=1e-5)


def test_simplify_drops_collinear_points_and_keeps_both_ends() -> None:
    """A straight line keeps its ends; a kink past the tolerance is kept."""
    straight = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (10.0, 0.0)]
    assert simplify(straight) == [(0.0, 0.0), (10.0, 0.0)]
    kinked = [(0.0, 0.0), (5.0, 1.0), (10.0, 20.0), (20.0, 0.0)]
    assert simplify(kinked, 5.0) == [(0.0, 0.0), (10.0, 20.0), (20.0, 0.0)]
    assert simplify([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]) == [(0.0, 0.0), (0.0, 0.0)]
    assert simplify([(1.0, 1.0)]) == [(1.0, 1.0)]


def test_to_geometry_is_a_wgs84_multilinestring() -> None:
    """LV95 in, simplified WGS84 out, always a MultiLineString."""
    geometry = to_geometry(gpkg_blob([LINE_A]))
    assert geometry["type"] == "MultiLineString"
    (line,) = geometry["coordinates"]
    assert len(line) == 2  # LINE_A's middle point is on the straight line
    lon, lat = line[0]
    assert 10.0 < lon < 10.2
    assert 46.8 < lat < 46.9


def test_a_segment_row_is_its_document() -> None:
    """swisstopo's codes are kept as published."""
    document = segment_document(segment_row(42))
    assert document["segment_id"] == 42
    assert (document["discipline"], document["direction"], document["access"]) == (
        "ski_tour",
        0,
        0,
    )
    assert document["route_info"] == 1
    assert document["geometry"]["type"] == "MultiLineString"


def test_a_segment_uuid_is_stable() -> None:
    """The same id always gives the same uuid; another id another."""
    assert segment_uuid(42) == segment_uuid(42)
    assert segment_uuid(42) != segment_uuid(43)


def test_a_tour_row_is_its_document() -> None:
    """Names, grades and links by language; the summit's height rounded."""
    row = tour_row(5956)
    assert is_tour(row)
    assert tour_uuid(row) == uuid.UUID("7e61c1c7-1ec5-41e7-996b-b28db78be4f0")
    document = tour_document(row)
    assert document["sac_id"] == 5956
    assert document["name"] == "Piz Buin, von der Silvrettahütte"
    assert document["grade"] == "PD"
    assert document["grades"]["de"] == "WS"
    assert document["difficulty"] == 4
    assert document["ascent_m"] == 1100
    assert document["descent_m"] is None
    assert document["target_altitude_m"] == 3312
    assert document["urls"]["en"].startswith("https://www.sac-cas.ch/")
    assert document["urls"]["fr"] == ""


def test_an_unnamed_line_is_not_a_tour() -> None:
    """The layer's "no route information" lines have no SAC id."""
    assert not is_tour(tour_row(None))
