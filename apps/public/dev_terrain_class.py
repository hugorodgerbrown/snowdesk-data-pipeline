"""
apps/public/dev_terrain_class.py — DEBUG-only synthetic terrain-class tiles (SNOW-978).

The map's terrain filter reads class tiles from the snowdesk-tiles origin
(``settings.TERRAIN_CLASS_TILE_URL``). Those tiles may not be published in a
given environment, and a developer working on the filter needs tiles that
exercise every branch of the pixel contract — every aspect, every slope
band, level ground and a no-data hole — rather than whatever a real survey
happens to hold in view. This module generates contract-exact tiles on the
fly from an analytic surface around Verbier, so

    TERRAIN_CLASS_TILE_URL=http://localhost:3000/dev/terrain-class/v1/{z}/{x}/{y}.png

is enough to see the whole filter working locally.

THE PIXEL CONTRACT (the same one the real tiles follow; full statement in
docs/decisions/terrain-filter-is-class-tiles-filtered-on-device.md):

* Opaque RGBA PNG, 256 px, alpha always 255.
* R, G — height in whole metres, uint16 big-endian.
* B — ``octant << 5 | band``: octant N=0 … NW=7 with N covering 337.5–22.5°
  (``apps.core.geo.octant_for``'s split), band ``floor(slope / 5)`` in 0..17.
* B = 254 — level ground (no aspect; R, G still hold the height).
* B = 255 — no data (R = G = 0).
* A tile with no data, or a zoom outside 12–14, answers HTTP 204.

THE SURFACE. Two "domes" whose slope angle grows linearly from 0° at the
summit to 89° at the rim — ``h(r) = H0 + ln(cos(a·r)) / a`` with
``a = 89° / R`` — so every band from 0 to 17 occurs at a known radius and
every aspect occurs all the way round. They stand on a base plane rising
gently to the north (a band-0, south-facing slope), with a flat lake (level
ground) and a circular hole with no data. Everything is computed
analytically: the slope angle and the aspect come from the closed-form
gradient, never from finite differences, so a test can predict a pixel.

The PNG is written with ``zlib`` and ``struct`` so Pillow never becomes a
runtime dependency for a development aid. ``bin/build-terrain-class-fixture``
writes one tile from here into ``tests/js/fixtures/terrain-class/`` for the
Vitest decode test.

Mounted from ``config/urls.py`` only when ``settings.DEBUG`` is true;
production never imports this module.
"""

from __future__ import annotations

import logging
import math
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)

TILE_SIZE = 256
MIN_ZOOM = 12
MAX_ZOOM = 14

B_LEVEL = 254
B_NODATA = 255
BAND_DEG = 5
MAX_BAND = 17

# The fixture's extent, [west, south, east, north]. Tiles wholly outside it
# answer 204; pixels of an edge tile that fall outside it are no-data.
BOX = (7.10, 46.00, 7.40, 46.20)

# The local metric frame's origin — roughly Verbier.
ORIGIN_LNG = 7.23
ORIGIN_LAT = 46.10
_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LNG = 111_320.0 * math.cos(math.radians(ORIGIN_LAT))

# The steepest a dome gets, at its rim. Short of 90° so tan() stays finite.
_RIM_SLOPE_RAD = math.radians(89.0)


@dataclass(frozen=True)
class Dome:
    """A summit whose slope angle grows linearly with distance from it.

    Attributes:
        lng: Summit longitude.
        lat: Summit latitude.
        summit_m: Summit height in metres.
        radius_m: Distance at which the slope reaches 89°.

    """

    lng: float
    lat: float
    summit_m: float
    radius_m: float

    def to_string(self) -> str:
        """Return a short human-readable description."""
        return f"Dome({self.lng}, {self.lat}, {self.summit_m} m, r={self.radius_m} m)"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


DOMES = (
    Dome(lng=7.235, lat=46.105, summit_m=3400.0, radius_m=1000.0),
    Dome(lng=7.272, lat=46.088, summit_m=2700.0, radius_m=600.0),
)

# The base plane: 1200 m at the origin, rising 5 cm per metre northward.
BASE_M = 1200.0
BASE_RISE_PER_M_NORTH = 0.05

# A flat lake (level ground) and a hole with no data, as (lng, lat, radius).
# Every feature — both domes, the lake and the hole — sits inside the one
# z12 tile 12/2130/1455, which is the tile the Vitest fixture is cut from.
# A dome is tall enough for its slope to pass 85° (band 17) before it meets
# the base plane: the drop to angle θ is -ln(cos θ)·R/89°.
LAKE = (7.222, 46.085, 350.0)
LAKE_M = 1800
HOLE = (7.275, 46.122, 300.0)


@dataclass(frozen=True)
class Cell:
    """The facts one pixel carries.

    Attributes:
        height_m: Height in whole metres (0 for no data).
        blue: The blue channel — ``octant << 5 | band``, 254 or 255.

    """

    height_m: int
    blue: int

    def to_string(self) -> str:
        """Return a short human-readable description."""
        return f"Cell({self.height_m} m, B={self.blue})"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def _local_metres(
    lng: float, lat: float, ref_lng: float, ref_lat: float
) -> tuple[float, float]:
    """Return (east, north) metres from a reference point."""
    return (lng - ref_lng) * _M_PER_DEG_LNG, (lat - ref_lat) * _M_PER_DEG_LAT


def octant_index(bearing_deg: float) -> int:
    """Return the octant 0 (N) … 7 (NW) a bearing falls in.

    The same split as ``apps.core.geo.octant_for``: N covers 337.5–22.5°.
    """
    return int(((bearing_deg % 360.0) + 22.5) // 45.0) % 8


def blue_for(slope_deg: float, aspect_deg: float) -> int:
    """Return the B channel for a slope angle and the bearing it faces."""
    band = min(MAX_BAND, int(slope_deg // BAND_DEG))
    return (octant_index(aspect_deg) << 5) | band


def _dome_height_and_gradient(
    dome: Dome, lng: float, lat: float
) -> tuple[float, float, float] | None:
    """Return (height, slope_deg, aspect_deg) on a dome, or None beyond its rim."""
    east, north = _local_metres(lng, lat, dome.lng, dome.lat)
    r = math.hypot(east, north)
    if r >= dome.radius_m:
        return None
    a = _RIM_SLOPE_RAD / dome.radius_m
    height = dome.summit_m + math.log(math.cos(a * r)) / a
    slope_deg = math.degrees(a * r)
    # Downhill on a dome is straight away from the summit.
    aspect_deg = math.degrees(math.atan2(east, north)) % 360.0
    return height, slope_deg, aspect_deg


def cell_at(lng: float, lat: float) -> Cell:
    """Classify one point of the synthetic surface."""
    west, south, east, north = BOX
    if not (west <= lng <= east and south <= lat <= north):
        return Cell(height_m=0, blue=B_NODATA)
    hole_e, hole_n = _local_metres(lng, lat, HOLE[0], HOLE[1])
    if math.hypot(hole_e, hole_n) < HOLE[2]:
        return Cell(height_m=0, blue=B_NODATA)
    lake_e, lake_n = _local_metres(lng, lat, LAKE[0], LAKE[1])
    if math.hypot(lake_e, lake_n) < LAKE[2]:
        return Cell(height_m=LAKE_M, blue=B_LEVEL)

    _, north_m = _local_metres(lng, lat, ORIGIN_LNG, ORIGIN_LAT)
    height = BASE_M + BASE_RISE_PER_M_NORTH * north_m
    slope_deg = math.degrees(math.atan(BASE_RISE_PER_M_NORTH))
    aspect_deg = 180.0  # rising northward, so it faces south
    for dome in DOMES:
        on_dome = _dome_height_and_gradient(dome, lng, lat)
        if on_dome is not None and on_dome[0] > height:
            height, slope_deg, aspect_deg = on_dome
    whole_m = max(0, min(65535, round(height)))
    if slope_deg == 0.0:
        return Cell(height_m=whole_m, blue=B_LEVEL)
    return Cell(height_m=whole_m, blue=blue_for(slope_deg, aspect_deg))


def pixel_lng_lat(z: int, x: int, y: int, px: int, py: int) -> tuple[float, float]:
    """Return the (lng, lat) of a tile pixel's centre in Web Mercator."""
    n = TILE_SIZE * (2**z)
    gx = (x * TILE_SIZE + px + 0.5) / n
    gy = (y * TILE_SIZE + py + 0.5) / n
    lng = gx * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy))))
    return lng, lat


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return a tile's [west, south, east, north] edges in degrees."""
    n = 2**z

    def lat_of(row: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * row / n))))

    return x / n * 360.0 - 180.0, lat_of(y + 1), (x + 1) / n * 360.0 - 180.0, lat_of(y)


def tile_has_data(z: int, x: int, y: int) -> bool:
    """Whether a tile is in the served zoom range and overlaps the box."""
    if not MIN_ZOOM <= z <= MAX_ZOOM or not 0 <= x < 2**z or not 0 <= y < 2**z:
        return False
    west, south, east, north = tile_bounds(z, x, y)
    bwest, bsouth, beast, bnorth = BOX
    return west < beast and east > bwest and south < bnorth and north > bsouth


def tile_rgba(z: int, x: int, y: int) -> bytes:
    """Return a tile's raw RGBA pixels, row-major, 256 × 256 × 4 bytes."""
    out = bytearray(TILE_SIZE * TILE_SIZE * 4)
    for py in range(TILE_SIZE):
        for px in range(TILE_SIZE):
            cell = cell_at(*pixel_lng_lat(z, x, y, px, py))
            i = (py * TILE_SIZE + px) * 4
            out[i] = cell.height_m >> 8
            out[i + 1] = cell.height_m & 0xFF
            out[i + 2] = cell.blue
            out[i + 3] = 255
    return bytes(out)


def encode_png(rgba: bytes, width: int, height: int) -> bytes:
    """Encode raw RGBA as a PNG with only the standard library.

    Colour type 6 (RGBA), bit depth 8, filter 0 on every row.
    """

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    stride = width * 4
    raw = b"".join(
        b"\x00" + rgba[row * stride : (row + 1) * stride] for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


@lru_cache(maxsize=256)
def tile_png(z: int, x: int, y: int) -> bytes | None:
    """Return a tile as PNG bytes, or None when it should answer 204."""
    if not tile_has_data(z, x, y):
        return None
    return encode_png(tile_rgba(z, x, y), TILE_SIZE, TILE_SIZE)


def terrain_class_tile(request: HttpRequest, z: int, x: int, y: int) -> HttpResponse:
    """Serve one synthetic terrain-class tile, or 204 where there is none.

    Args:
        request: The incoming request (unused beyond routing).
        z: Zoom.
        x: Column.
        y: Row.

    Returns:
        A ``image/png`` response, or an empty 204.

    """
    png = tile_png(z, x, y)
    if png is None:
        return HttpResponse(status=204)
    logger.debug("dev terrain-class tile %s/%s/%s", z, x, y)
    return HttpResponse(png, content_type="image/png")
