"""
tests/public/test_dev_terrain_class.py — the DEBUG-only synthetic terrain-class tiles.

SNOW-978. The route stands in for the snowdesk-tiles origin while the map's
terrain filter is developed, so the only thing worth asserting is that what
it serves obeys the pixel contract the filter decodes: an opaque RGBA PNG,
B = octant << 5 | band with band 0..17, 254 for level ground and 255 for no
data — and 204 wherever there is no tile. DEBUG is True under
config.settings.development, so the route is mounted in the test run.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

import pytest
from django.test import Client

from apps.core.geo import OCTANTS, octant_for
from apps.public import dev_terrain_class as dev

# Some middleware on the response path reads the database for a 204.
pytestmark = pytest.mark.django_db

URL = "/dev/terrain-class/v1/{z}/{x}/{y}.png"
# The tile holding both domes, the lake and the hole.
FIXTURE_TILE = (12, 2130, 1455)


def _decode_png(body: bytes) -> tuple[int, int, int, bytes]:
    """Return (width, height, colour type, raw RGBA) from a filter-0 PNG."""
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    width = height = colour_type = 0
    idat = b""
    while pos < len(body):
        (length,) = struct.unpack(">I", body[pos : pos + 4])
        kind = body[pos + 4 : pos + 8]
        data = body[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, _depth, colour_type = struct.unpack(">IIBB", data[:10])
        elif kind == b"IDAT":
            idat += data
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    rows = [raw[r * (stride + 1) + 1 : (r + 1) * (stride + 1)] for r in range(height)]
    return width, height, colour_type, b"".join(rows)


def _get(z: int, x: int, y: int) -> Any:
    """Fetch one tile through the test client."""
    return Client().get(URL.format(z=z, x=x, y=y))


def test_a_tile_inside_the_box_is_an_opaque_256px_rgba_png() -> None:
    """The fixture tile answers 200 with a 256 px RGBA PNG, alpha all 255."""
    response = _get(*FIXTURE_TILE)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    width, height, colour_type, rgba = _decode_png(response.content)
    assert (width, height, colour_type) == (256, 256, 6)
    assert set(rgba[3::4]) == {255}


def test_every_blue_value_is_inside_the_contract() -> None:
    """B is octant<<5|band with band ≤ 17, or one of the two reserved values.

    The fixture tile is built to hold every octant, every band, level
    ground and a no-data hole, so the filter can be exercised end to end.
    """
    _w, _h, _c, rgba = _decode_png(_get(*FIXTURE_TILE).content)
    blues = set(rgba[2::4])
    for b in blues:
        assert b in (dev.B_LEVEL, dev.B_NODATA) or (b & 31) <= dev.MAX_BAND
    data = {b for b in blues if b < dev.B_LEVEL}
    assert {b >> 5 for b in data} == set(range(8))
    assert {b & 31 for b in data} == set(range(dev.MAX_BAND + 1))
    assert dev.B_LEVEL in blues
    assert dev.B_NODATA in blues


def test_no_data_pixels_carry_zero_height() -> None:
    """B = 255 means R = G = 0."""
    _w, _h, _c, rgba = _decode_png(_get(*FIXTURE_TILE).content)
    for i in range(0, len(rgba), 4):
        if rgba[i + 2] == dev.B_NODATA:
            assert rgba[i] == rgba[i + 1] == 0


@pytest.mark.parametrize(
    "tile",
    [
        (11, 1065, 727),  # below the served zoom range
        (15, 17042, 11645),  # above it
        (12, 2200, 1455),  # in range, well east of the box
        (12, -1, 1455),  # not a tile at all
    ],
)
def test_no_tile_answers_204(tile: tuple[int, int, int]) -> None:
    """Outside z12–14 or outside the box, the route answers 204, empty."""
    z, x, y = tile
    if x < 0:
        assert dev.tile_png(z, x, y) is None
        return
    response = _get(z, x, y)
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("bearing", [0.0, 22.4, 22.5, 67.5, 180.0, 337.4, 337.5, 359.9])
def test_octant_split_matches_core_geo(bearing: float) -> None:
    """The fixture splits octants exactly as apps.core.geo.octant_for does."""
    assert OCTANTS[dev.octant_index(bearing)] == octant_for(bearing)


def test_blue_for_clamps_the_band_at_17() -> None:
    """A slope past 85° stays in band 17; the octant sits in the top bits."""
    assert dev.blue_for(89.0, 90.0) == (2 << 5) | 17
    assert dev.blue_for(34.9, 0.0) == 6
    assert dev.blue_for(35.0, 315.0) == (7 << 5) | 7


def test_dataclasses_describe_themselves() -> None:
    """The two dataclasses read sensibly in a log line."""
    assert "3400.0 m" in str(dev.DOMES[0])
    assert str(dev.Cell(height_m=1800, blue=254)) == "Cell(1800 m, B=254)"
