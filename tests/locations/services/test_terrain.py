"""
tests/locations/services/test_terrain.py — sampling the ground.

Covers:
  - TerrainHeight / TerrainSlope: exactly one of a figure and a reason,
    enforced rather than documented;
  - Horn's arithmetic on synthetic planes of known angle and known facing,
    including a flat one answering exactly 0.0 degrees;
  - window_m: the grid's default, three windows giving three different
    answers over the same stored heights, and a window that is not a whole
    multiple of the cell size raising rather than rounding;
  - the skirt: a step-1 kernel on a tile's outermost cell costs ONE fetch,
    and a step-3 kernel at a corner fetches exactly the three neighbours
    that own the cells it reaches;
  - THE UNKNOWNS, which is the ticket: a flat 0 degrees and a "we do not
    know" are never the same value. 204, nodata, an unreachable origin and
    a point outside every source's rectangle each answer differently;
  - the real committed Zermatt tile — height, three slope windows and the
    provenance that comes with them.

The synthetic tiles encode a plane, so the expected angle is exact rather
than approximate: at a 5 m cell and a 0.25 m height scale every gradient
used here quantises without remainder.

Outbound HTTP is mocked with ``unittest.mock.patch`` as in
test_elevation.py, and ``load_grid`` is patched with the committed
definition so no test depends on the grid and the tiles arriving in one
order. No database and no browser.
"""

from __future__ import annotations

import array
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.cache import cache

from apps.locations.services.terrain import (
    TerrainHeight,
    TerrainSlope,
    TerrainUnknown,
    _fetch_tile,
    sample_height,
    sample_slope,
)
from apps.locations.services.terrain_grid import TerrainGrid, grid_from_payload

FIXTURES = Path(__file__).parent.parent / "fixtures" / "terrain"

# The one real tile committed as a fixture, and the coordinates whose
# heights, tiles and cells were computed outside this codebase.
FIXTURE_TILE = (3239, 1990)
ZERMATT_VILLAGE = (46.0207, 7.7491)
ZERMATT_STATION = (46.0237, 7.7476)

# Coordinates landing on the fixture tile's own outermost cells — row 0
# column 0, and row 255 column 255. Derived by inverting the projection
# onto the middle of each cell.
TILE_NORTH_WEST_CELL = (46.0253223, 7.7412535)
TILE_SOUTH_EAST_CELL = (46.0141904, 7.7581695)

# Well outside every source's rectangle: no tile is fetched for it.
OFF_THE_GRID = (55.0, -3.0)


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Drop the process tile cache and the grid cache before each test.

    A warm ``lru_cache`` entry outlives a patched origin, so a test that
    did not clear it would assert against the previous test's tiles.
    """
    _fetch_tile.cache_clear()
    cache.clear()


def _grid() -> TerrainGrid:
    """Return the committed grid definition.

    Returns:
        The parsed grid.

    """
    payload: dict[str, Any] = json.loads((FIXTURES / "grid.json").read_text())
    return grid_from_payload(payload)


def _fixture_tile_bytes() -> bytes:
    """Return the committed Zermatt tile.

    Returns:
        Its 133,128 bytes.

    """
    return (FIXTURES / "3239_1990.s16").read_bytes()


def _cell_of(
    grid: TerrainGrid, tile_x: int, tile_y: int, row: int, column: int
) -> tuple[int, int]:
    """Return the global cell at one tile's row and column.

    Args:
        grid: The grid definition.
        tile_x: The tile's column.
        tile_y: The tile's row.
        row: Row within the tile.
        column: Column within the tile.

    Returns:
        ``(cell_x, cell_y)``.

    """
    return (
        tile_x * grid.tile_cells + column,
        -grid.tile_cells * (tile_y + 1) + row,
    )


def _plane_tile(
    grid: TerrainGrid,
    tile_x: int,
    tile_y: int,
    *,
    east_gradient: float = 0.0,
    south_gradient: float = 0.0,
    base_m: float = 1000.0,
) -> bytes:
    """Encode one tile of a plane whose gradient is known exactly.

    The plane is defined in GLOBAL cell coordinates against the fixture
    tile's north-west corner, so neighbouring tiles join without a step and
    a kernel that crosses a tile boundary still measures the same plane.

    Args:
        grid: The grid definition.
        tile_x: The tile's column.
        tile_y: The tile's row.
        east_gradient: Metres of rise per metre eastward.
        south_gradient: Metres of rise per metre southward.
        base_m: The height at the fixture tile's north-west cell.

    Returns:
        The tile's ``grid.tile_bytes`` bytes.

    """
    origin_x, origin_y = _cell_of(grid, *FIXTURE_TILE, 0, 0)
    values: array.array[int] = array.array("h")
    for row in range(grid.stored_cells):
        for column in range(grid.stored_cells):
            cell_x, cell_y = _cell_of(
                grid, tile_x, tile_y, row - grid.skirt_cells, column - grid.skirt_cells
            )
            east_m = (cell_x - origin_x) * grid.cell_size_m
            south_m = (cell_y - origin_y) * grid.cell_size_m
            height = base_m + east_gradient * east_m + south_gradient * south_m
            values.append(round((height - grid.height_offset_m) / grid.height_scale_m))
    return values.tobytes()


def _with_nodata(grid: TerrainGrid, raw: bytes, row: int, column: int) -> bytes:
    """Return a tile's bytes with one cell replaced by the nodata sentinel.

    Args:
        grid: The grid definition.
        raw: The tile's bytes.
        row: The cell's row within the tile.
        column: The cell's column within the tile.

    Returns:
        The edited bytes.

    """
    values: array.array[int] = array.array("h")
    values.frombytes(raw)
    index = (row + grid.skirt_cells) * grid.stored_cells + (column + grid.skirt_cells)
    values[index] = grid.nodata
    return values.tobytes()


class _Origin:
    """A stand-in for the tile origin that records what was asked of it.

    Attributes:
        tiles: The tiles it publishes, keyed ``(tile_x, tile_y)``. Anything
            else answers 204, which is what the real origin does over the
            half of its rectangle no source covers.
        requested: Every tile asked for, in order.

    """

    def __init__(
        self,
        tiles: dict[tuple[int, int], bytes],
        *,
        error: Exception | None = None,
        status: int | None = None,
        body: bytes | None = None,
    ) -> None:
        """Configure what the origin answers.

        Args:
            tiles: The tiles it publishes.
            error: Raised instead of answering, for the outage cases.
            status: Forced status for every request, for the error cases.
            body: Forced body, for the truncated-tile case.

        """
        self.tiles = tiles
        self.error = error
        self.status = status
        self.body = body
        self.requested: list[tuple[int, int]] = []

    def __call__(self, url: str, timeout: int | None = None) -> MagicMock:
        """Answer one tile request.

        Args:
            url: The tile URL.
            timeout: Ignored; accepted because the caller passes it.

        Returns:
            A response mock.

        Raises:
            Exception: Whatever ``error`` was configured with.

        """
        tile_x, rest = url.rsplit("/", 2)[1:]
        key = (int(tile_x), int(rest.removesuffix(".s16")))
        self.requested.append(key)

        if self.error is not None:
            raise self.error
        if self.status is not None:
            return _response(self.status, self.body or b"")
        raw = self.tiles.get(key)
        if raw is None:
            return _response(204, b"")
        return _response(200, raw)


def _response(status_code: int, content: bytes) -> MagicMock:
    """Return a response mock carrying one status and body.

    Args:
        status_code: The HTTP status.
        content: The body.

    Returns:
        The mock.

    """
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.content = content
    return response


@contextmanager
def _serving(grid: TerrainGrid | None, origin: _Origin) -> Iterator[_Origin]:
    """Patch the grid loader and the tile origin for one block.

    Args:
        grid: The definition ``load_grid`` returns, or None to simulate an
            origin that cannot even be described.
        origin: The tile origin.

    Yields:
        The origin, so a test can assert what it was asked for.

    """
    with (
        patch("apps.locations.services.terrain.load_grid", return_value=grid),
        patch("apps.locations.services.terrain.requests.get", origin),
    ):
        yield origin


class TestResultInvariants:
    """A result is an answer or a reason, never both and never neither."""

    def test_a_height_cannot_be_both(self) -> None:
        """A figure alongside a reason is a contradiction."""
        with pytest.raises(ValueError, match="exactly one"):
            TerrainHeight(height_m=1200.0, unknown=TerrainUnknown.NO_DATA, source=None)

    def test_a_height_cannot_be_neither(self) -> None:
        """A bare None is the shape this module exists to refuse."""
        with pytest.raises(ValueError, match="exactly one"):
            TerrainHeight(height_m=None, unknown=None, source=None)

    def test_a_slope_cannot_be_both(self) -> None:
        """Same rule on the gradient."""
        with pytest.raises(ValueError, match="exactly one"):
            TerrainSlope(
                angle_deg=30.0,
                aspect_deg=180.0,
                window_m=10.0,
                unknown=TerrainUnknown.NO_DATA,
                source=None,
            )

    def test_an_unknown_slope_cannot_carry_an_aspect(self) -> None:
        """A facing without a gradient is provenance for nothing."""
        with pytest.raises(ValueError, match="cannot carry an aspect"):
            TerrainSlope(
                angle_deg=None,
                aspect_deg=180.0,
                window_m=10.0,
                unknown=TerrainUnknown.NO_DATA,
                source=None,
            )

    def test_a_sloping_result_must_carry_an_aspect(self) -> None:
        """Only exactly level ground is allowed to face nowhere."""
        with pytest.raises(ValueError, match="must carry an aspect"):
            TerrainSlope(
                angle_deg=30.0,
                aspect_deg=None,
                window_m=10.0,
                unknown=None,
                source=None,
            )


class TestSlopeOnAKnownPlane:
    """Horn's arithmetic, against planes whose answer is arithmetic."""

    def _slope_on(
        self, *, east_gradient: float = 0.0, south_gradient: float = 0.0
    ) -> TerrainSlope:
        """Sample the middle of the fixture tile over one synthetic plane.

        Args:
            east_gradient: Metres of rise per metre eastward.
            south_gradient: Metres of rise per metre southward.

        Returns:
            The sample.

        """
        grid = _grid()
        tiles = {
            FIXTURE_TILE: _plane_tile(
                grid,
                *FIXTURE_TILE,
                east_gradient=east_gradient,
                south_gradient=south_gradient,
            )
        }
        with _serving(grid, _Origin(tiles)):
            return sample_slope(*ZERMATT_VILLAGE)

    def test_flat_ground_is_exactly_zero_and_known(self) -> None:
        """The central requirement: a real 0 degrees is a KNOWN answer."""
        result = self._slope_on()
        assert result.angle_deg == 0.0
        assert result.is_known is True
        assert result.unknown is None

    def test_flat_ground_faces_nowhere(self) -> None:
        """A bearing invented for a level cell would be read as a real one."""
        assert self._slope_on().aspect_deg is None

    def test_a_45_degree_plane(self) -> None:
        """A metre of rise per metre east is 45 degrees, facing west."""
        result = self._slope_on(east_gradient=1.0)
        assert result.angle_deg == pytest.approx(45.0)
        assert result.aspect_deg == pytest.approx(270.0)

    def test_a_plane_falling_east_faces_east(self) -> None:
        """Aspect is the direction of DESCENT, not of the gradient."""
        assert self._slope_on(east_gradient=-1.0).aspect_deg == pytest.approx(90.0)

    def test_a_plane_falling_north_faces_north(self) -> None:
        """Rising southward means falling northward."""
        assert self._slope_on(south_gradient=1.0).aspect_deg == pytest.approx(0.0)

    def test_a_plane_falling_south_faces_south(self) -> None:
        """And the other way round."""
        assert self._slope_on(south_gradient=-1.0).aspect_deg == pytest.approx(180.0)

    def test_a_plane_falling_south_east_faces_south_east(self) -> None:
        """Both components together give the diagonal bearing and angle."""
        result = self._slope_on(east_gradient=-1.0, south_gradient=-1.0)
        assert result.aspect_deg == pytest.approx(135.0)
        assert result.angle_deg == pytest.approx(math.degrees(math.atan(math.sqrt(2))))

    def test_a_gentle_plane(self) -> None:
        """A quarter-metre per metre is 14.04 degrees, not 25."""
        result = self._slope_on(east_gradient=-0.25)
        assert result.angle_deg == pytest.approx(math.degrees(math.atan(0.25)))
        assert result.aspect_deg == pytest.approx(90.0)


class TestAnalysisWindow:
    """What window_m means, and what it refuses."""

    def _real_tile_slope(self, window_m: float | None = None) -> TerrainSlope:
        """Sample the real Zermatt tile at one window.

        Args:
            window_m: The spacing to measure across.

        Returns:
            The sample.

        """
        grid = _grid()
        with _serving(grid, _Origin({FIXTURE_TILE: _fixture_tile_bytes()})):
            return sample_slope(*ZERMATT_VILLAGE, window_m=window_m)

    def test_the_default_comes_from_the_grid(self) -> None:
        """Not a constant in this module — the tileset states it."""
        assert self._real_tile_slope().window_m == _grid().default_analysis_window_m

    def test_three_windows_give_three_answers(self) -> None:
        """Same stored heights, different spacings, different gradients.

        The reference angles were computed outside this codebase from the
        committed tile, so they pin the projection, the row order and the
        height scale together — each of which returns a plausible number
        when it is wrong.
        """
        assert self._real_tile_slope(5).angle_deg == pytest.approx(5.076214, abs=1e-6)
        assert self._real_tile_slope(10).angle_deg == pytest.approx(8.111279, abs=1e-6)
        assert self._real_tile_slope(15).angle_deg == pytest.approx(4.006011, abs=1e-6)

    def test_the_aspects_at_three_windows(self) -> None:
        """A wider window smooths the facing as well as the gradient."""
        assert self._real_tile_slope(5).aspect_deg == pytest.approx(
            140.710593, abs=1e-6
        )
        assert self._real_tile_slope(10).aspect_deg == pytest.approx(
            105.255119, abs=1e-6
        )
        assert self._real_tile_slope(15).aspect_deg == pytest.approx(
            112.750976, abs=1e-6
        )

    def test_a_window_that_is_not_a_whole_multiple_raises(self) -> None:
        """Answering a 7 m request at 5 m would make the argument a hint."""
        with pytest.raises(ValueError, match="whole multiple"):
            self._real_tile_slope(7)

    def test_a_zero_window_raises(self) -> None:
        """There is no gradient across no ground."""
        with pytest.raises(ValueError, match="must be positive"):
            self._real_tile_slope(0)

    def test_a_negative_window_raises(self) -> None:
        """Nor across negative ground."""
        with pytest.raises(ValueError, match="must be positive"):
            self._real_tile_slope(-5)


class TestTileFetching:
    """What the skirt saves, and what it cannot."""

    def test_a_kernel_on_the_north_west_cell_costs_one_fetch(self) -> None:
        """The outermost owned cell's neighbours are in the tile's skirt."""
        grid = _grid()
        tiles = {FIXTURE_TILE: _plane_tile(grid, *FIXTURE_TILE, east_gradient=-1.0)}
        with _serving(grid, _Origin(tiles)) as origin:
            result = sample_slope(*TILE_NORTH_WEST_CELL, window_m=5)

        assert result.is_known is True
        assert origin.requested == [FIXTURE_TILE]

    def test_a_kernel_on_the_south_east_cell_costs_one_fetch(self) -> None:
        """The same at the far corner."""
        grid = _grid()
        tiles = {FIXTURE_TILE: _plane_tile(grid, *FIXTURE_TILE, east_gradient=-1.0)}
        with _serving(grid, _Origin(tiles)) as origin:
            result = sample_slope(*TILE_SOUTH_EAST_CELL, window_m=5)

        assert result.is_known is True
        assert origin.requested == [FIXTURE_TILE]

    def test_a_wide_kernel_at_a_corner_fetches_its_neighbours(self) -> None:
        """A 15 m spacing reaches three cells out, past the one-cell skirt."""
        grid = _grid()
        neighbours = [(3239, 1990), (3238, 1990), (3239, 1991), (3238, 1991)]
        tiles = {
            tile: _plane_tile(grid, *tile, east_gradient=-1.0) for tile in neighbours
        }
        with _serving(grid, _Origin(tiles)) as origin:
            result = sample_slope(*TILE_NORTH_WEST_CELL, window_m=15)

        assert set(origin.requested) == set(neighbours)
        assert origin.requested[0] == FIXTURE_TILE
        # The plane is continuous across the tile joins, so crossing them
        # must not change the answer.
        assert result.angle_deg == pytest.approx(45.0)
        assert result.aspect_deg == pytest.approx(90.0)

    def test_a_tile_is_fetched_once_per_process(self) -> None:
        """Two samples on one tile are one request — the tiles are immutable."""
        grid = _grid()
        tiles = {FIXTURE_TILE: _fixture_tile_bytes()}
        with _serving(grid, _Origin(tiles)) as origin:
            sample_height(*ZERMATT_VILLAGE)
            sample_height(*ZERMATT_STATION)

        assert origin.requested == [FIXTURE_TILE]


class TestUnknowns:
    """The ticket: an unknown is never mistakable for gentle ground."""

    def test_outside_every_rectangle_asks_for_nothing(self) -> None:
        """No source claims it, so there is no tile worth requesting."""
        grid = _grid()
        with _serving(grid, _Origin({})) as origin:
            height = sample_height(*OFF_THE_GRID)
            slope = sample_slope(*OFF_THE_GRID)

        assert height.unknown is TerrainUnknown.OUTSIDE_COVERAGE
        assert slope.unknown is TerrainUnknown.OUTSIDE_COVERAGE
        assert origin.requested == []

    def test_an_absent_tile_is_outside_coverage(self) -> None:
        """A 204 is the origin saying no source covers this ground."""
        grid = _grid()
        with _serving(grid, _Origin({})):
            height = sample_height(*ZERMATT_VILLAGE)
            slope = sample_slope(*ZERMATT_VILLAGE)

        assert height.unknown is TerrainUnknown.OUTSIDE_COVERAGE
        assert height.height_m is None
        assert height.is_known is False
        assert slope.unknown is TerrainUnknown.OUTSIDE_COVERAGE
        assert slope.angle_deg is None
        assert slope.is_known is False

    def test_a_404_is_absent_rather_than_broken(self) -> None:
        """It logs, because it is our arithmetic, but it is still no data."""
        grid = _grid()
        with _serving(grid, _Origin({}, status=404)):
            assert sample_height(*ZERMATT_VILLAGE).unknown is (
                TerrainUnknown.OUTSIDE_COVERAGE
            )

    def test_a_nodata_cell_is_not_outside_coverage(self) -> None:
        """A hole inside a surveyed area is its own answer."""
        grid = _grid()
        raw = _with_nodata(grid, _fixture_tile_bytes(), 106, 119)
        with _serving(grid, _Origin({FIXTURE_TILE: raw})):
            result = sample_height(*ZERMATT_VILLAGE)

        assert result.unknown is TerrainUnknown.NO_DATA
        assert result.height_m is None

    def test_one_hole_voids_the_whole_kernel(self) -> None:
        """Eight heights and a guess is a fabrication, not a gradient."""
        grid = _grid()
        # One cell away from the sampled centre, so it is in the kernel but
        # is not the cell the coordinate falls in.
        raw = _with_nodata(grid, _fixture_tile_bytes(), 105, 119)
        with _serving(grid, _Origin({FIXTURE_TILE: raw})):
            result = sample_slope(*ZERMATT_VILLAGE, window_m=5)

        assert result.unknown is TerrainUnknown.NO_DATA
        assert result.angle_deg is None

    def test_an_unreachable_origin_is_not_outside_coverage(self) -> None:
        """An outage must never read as ground nobody surveyed."""
        grid = _grid()
        with _serving(grid, _Origin({}, error=requests.Timeout("too slow"))):
            height = sample_height(*ZERMATT_VILLAGE)
            slope = sample_slope(*ZERMATT_VILLAGE)

        assert height.unknown is TerrainUnknown.UNAVAILABLE
        assert slope.unknown is TerrainUnknown.UNAVAILABLE

    def test_a_server_error_is_unavailable(self) -> None:
        """A 500 says nothing about the ground."""
        grid = _grid()
        with _serving(grid, _Origin({}, status=503)):
            assert sample_height(*ZERMATT_VILLAGE).unknown is (
                TerrainUnknown.UNAVAILABLE
            )

    def test_a_truncated_tile_is_unavailable(self) -> None:
        """A short body is a proxy's error page, not a statement of height."""
        grid = _grid()
        with _serving(grid, _Origin({}, status=200, body=b"not a tile")):
            assert sample_height(*ZERMATT_VILLAGE).unknown is (
                TerrainUnknown.UNAVAILABLE
            )

    def test_no_grid_definition_is_unavailable(self) -> None:
        """Without the geometry there is nothing to address, and we say so."""
        with _serving(None, _Origin({})) as origin:
            height = sample_height(*ZERMATT_VILLAGE)
            slope = sample_slope(*ZERMATT_VILLAGE)

        assert height.unknown is TerrainUnknown.UNAVAILABLE
        assert slope.unknown is TerrainUnknown.UNAVAILABLE
        assert slope.window_m == 0.0
        assert origin.requested == []

    def test_an_outage_is_retried_rather_than_remembered(self) -> None:
        """Only a 204 memoises; a failure must not outlive the failure."""
        grid = _grid()
        with _serving(grid, _Origin({}, error=requests.ConnectionError("refused"))):
            assert sample_height(*ZERMATT_VILLAGE).unknown is (
                TerrainUnknown.UNAVAILABLE
            )

        with _serving(grid, _Origin({FIXTURE_TILE: _fixture_tile_bytes()})):
            assert sample_height(*ZERMATT_VILLAGE).height_m == 1602.75

    def test_a_flat_answer_and_an_unknown_are_distinguishable(self) -> None:
        """The whole point, stated as one comparison."""
        grid = _grid()
        with _serving(grid, _Origin({FIXTURE_TILE: _plane_tile(grid, *FIXTURE_TILE)})):
            flat = sample_slope(*ZERMATT_VILLAGE)

        # The flat tile is now memoised, and a memoised tile outlives the
        # patched origin — which is the whole reason the fixture clears it.
        _fetch_tile.cache_clear()
        with _serving(grid, _Origin({})):
            unknown = sample_slope(*ZERMATT_VILLAGE)

        assert (flat.angle_deg, flat.is_known) == (0.0, True)
        assert (unknown.angle_deg, unknown.is_known) == (None, False)
        assert flat.unknown is None
        assert unknown.unknown is TerrainUnknown.OUTSIDE_COVERAGE


class TestTheRealTile:
    """The committed Zermatt tile, end to end."""

    def test_the_village_height(self) -> None:
        """1602.75 m — the value the encoded tile actually carries."""
        grid = _grid()
        with _serving(grid, _Origin({FIXTURE_TILE: _fixture_tile_bytes()})):
            result = sample_height(*ZERMATT_VILLAGE)
        assert result.height_m == 1602.75

    def test_the_station_height(self) -> None:
        """A second cell of the same tile, to catch a transposed index."""
        grid = _grid()
        with _serving(grid, _Origin({FIXTURE_TILE: _fixture_tile_bytes()})):
            result = sample_height(*ZERMATT_STATION)
        assert result.height_m == 1605.75

    def test_a_known_height_names_its_source(self) -> None:
        """Provenance travels with the figure, not beside it."""
        grid = _grid()
        with _serving(grid, _Origin({FIXTURE_TILE: _fixture_tile_bytes()})):
            result = sample_height(*ZERMATT_VILLAGE)

        assert result.source is not None
        assert result.source.id == "swissalti3d"
        assert result.source.native_resolution_m == 2.0
        assert result.source.attribution == "© swisstopo"

    def test_a_known_slope_names_its_source(self) -> None:
        """And so does the gradient."""
        grid = _grid()
        with _serving(grid, _Origin({FIXTURE_TILE: _fixture_tile_bytes()})):
            result = sample_slope(*ZERMATT_VILLAGE)

        assert result.source is not None
        assert result.source.id == "swissalti3d"
        assert result.source.native_resolution_m != grid.cell_size_m
