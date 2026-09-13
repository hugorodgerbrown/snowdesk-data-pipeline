"""
tests/locations/services/test_terrain_grid.py — the grid's geometry.

Covers:
  - project: the EPSG registry's own published test point for 3035, and
    Zermatt landing on the tile the committed fixture is (which pins the
    projection and the addressing together rather than one at a time);
  - global_cell / tile_of_cell: the addressing arithmetic, and the
    half-open boundary rule at exact multiples of the 5 m cell and the
    1280 m tile;
  - offset_in_tile: a neighbouring tile's cell reachable through the
    skirt, and one cell further out that is not;
  - stored_index: the skirt's inward shift;
  - load_grid: parsing the committed grid.json, the one-hour cache, and
    None for an unreachable origin and for a malformed payload.

Outbound HTTP is mocked with ``unittest.mock.patch``, as in
test_elevation.py. No database and no browser: this is arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.cache import cache
from django.test import override_settings

from apps.locations.services.terrain_grid import (
    TerrainGrid,
    global_cell,
    grid_from_payload,
    load_grid,
    offset_in_tile,
    project,
    stored_index,
    tile_of_cell,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "terrain"

# Zermatt village. Its projected coordinate, tile and cell were computed
# outside this codebase from the published grid definition, so they pin the
# implementation rather than describing it.
ZERMATT_VILLAGE = (46.0207, 7.7491)
ZERMATT_EASTING = 4146515.586
ZERMATT_NORTHING = 2547946.114


@pytest.fixture(autouse=True)
def _clear_grid_cache() -> None:
    """Drop any cached grid definition before each test."""
    cache.clear()


def _grid_payload() -> dict[str, Any]:
    """Return the committed grid.json, parsed.

    Returns:
        The published definition as a dict.

    """
    payload: dict[str, Any] = json.loads((FIXTURES / "grid.json").read_text())
    return payload


def _grid() -> TerrainGrid:
    """Return the committed grid definition as a TerrainGrid.

    Returns:
        The parsed grid.

    """
    return grid_from_payload(_grid_payload())


def _mock_get(*, content: bytes, status_code: int = 200) -> MagicMock:
    """Return a mock for requests.get yielding one canned response.

    Args:
        content: The response body.
        status_code: The HTTP status to report.

    Returns:
        The mock.

    """
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.content = content
    return MagicMock(return_value=response)


class TestProject:
    """The WGS84 to EPSG:3035 forward transform."""

    def test_epsg_published_test_point(self) -> None:
        """50N 5E projects to the coordinate the EPSG registry publishes."""
        easting, northing = project(50.0, 5.0)
        assert easting == pytest.approx(3962799.45, abs=0.01)
        assert northing == pytest.approx(2999718.85, abs=0.01)

    def test_zermatt(self) -> None:
        """Zermatt village projects into the fixture tile's ground."""
        easting, northing = project(*ZERMATT_VILLAGE)
        assert easting == pytest.approx(ZERMATT_EASTING, abs=0.01)
        assert northing == pytest.approx(ZERMATT_NORTHING, abs=0.01)

    def test_the_origin_projects_to_the_false_origin(self) -> None:
        """52N 10E is the projection's origin, so it lands on the offsets."""
        easting, northing = project(52.0, 10.0)
        assert easting == pytest.approx(4321000.0, abs=0.01)
        assert northing == pytest.approx(3210000.0, abs=0.01)


class TestAddressing:
    """Which tile and cell a projected coordinate belongs to."""

    def test_zermatt_lands_on_the_fixture_tile(self) -> None:
        """Projection and addressing together resolve the known cell."""
        grid = _grid()
        easting, northing = project(*ZERMATT_VILLAGE)
        address = tile_of_cell(grid, *global_cell(grid, easting, northing))
        assert (address.tile_x, address.tile_y) == (3239, 1990)
        assert (address.row, address.column) == (106, 119)

    def test_a_cell_east_is_the_next_column(self) -> None:
        """Five metres east is one column on."""
        grid = _grid()
        first = tile_of_cell(grid, *global_cell(grid, 4146515.0, ZERMATT_NORTHING))
        second = tile_of_cell(grid, *global_cell(grid, 4146520.0, ZERMATT_NORTHING))
        assert second.column == first.column + 1

    def test_a_cell_north_is_the_previous_row(self) -> None:
        """Five metres north is one row back, because rows run southward."""
        grid = _grid()
        first = tile_of_cell(grid, *global_cell(grid, ZERMATT_EASTING, 2547946.0))
        second = tile_of_cell(grid, *global_cell(grid, ZERMATT_EASTING, 2547951.0))
        assert second.row == first.row - 1

    def test_cell_boundary_belongs_to_the_cell_east_of_it(self) -> None:
        """An easting on an exact 5 m boundary takes the eastern cell."""
        grid = _grid()
        # 4145920.0 is tile 3239's western edge exactly.
        on_edge = tile_of_cell(grid, *global_cell(grid, 4145920.0, ZERMATT_NORTHING))
        below = tile_of_cell(grid, *global_cell(grid, 4145919.99, ZERMATT_NORTHING))
        assert (on_edge.tile_x, on_edge.column) == (3239, 0)
        assert (below.tile_x, below.column) == (3238, 255)

    def test_cell_boundary_belongs_to_the_cell_north_of_it(self) -> None:
        """A northing on an exact 5 m boundary takes the northern cell."""
        grid = _grid()
        # 2547200.0 is tile 1990's southern edge exactly.
        on_edge = tile_of_cell(grid, *global_cell(grid, ZERMATT_EASTING, 2547200.0))
        below = tile_of_cell(grid, *global_cell(grid, ZERMATT_EASTING, 2547199.99))
        assert (on_edge.tile_y, on_edge.row) == (1990, 255)
        assert (below.tile_y, below.row) == (1989, 0)

    def test_the_cell_above_a_tile_boundary(self) -> None:
        """One cell north of a tile's southern edge is its second-last row."""
        grid = _grid()
        address = tile_of_cell(grid, *global_cell(grid, ZERMATT_EASTING, 2547205.0))
        assert (address.tile_y, address.row) == (1990, 254)

    def test_every_cell_of_a_tile_addresses_within_it(self) -> None:
        """Sweeping a tile's ground never escapes its 256x256 index range."""
        grid = _grid()
        for step in range(0, 256):
            easting = 4145920.0 + step * grid.cell_size_m + 2.5
            northing = 2547200.0 + step * grid.cell_size_m + 2.5
            address = tile_of_cell(grid, *global_cell(grid, easting, northing))
            assert (address.tile_x, address.tile_y) == (3239, 1990)
            assert address.column == step
            assert address.row == 255 - step


class TestSkirt:
    """Reading a neighbouring tile's cell out of a tile's own bytes."""

    def test_a_neighbour_cell_is_reachable_through_the_skirt(self) -> None:
        """Tile 1990's northernmost row sits in tile 1991's skirt."""
        grid = _grid()
        cell = global_cell(grid, ZERMATT_EASTING, 2548475.0)
        assert tile_of_cell(grid, *cell).tile_y == 1990
        offset = offset_in_tile(grid, *cell, 3239, 1991)
        assert offset == (256, 119)

    def test_two_cells_past_the_edge_are_not_reachable(self) -> None:
        """The skirt is one cell deep, so the second row out is not in it."""
        grid = _grid()
        cell = global_cell(grid, ZERMATT_EASTING, 2548470.0)
        assert offset_in_tile(grid, *cell, 3239, 1991) is None

    def test_an_owned_cell_reports_its_own_offset(self) -> None:
        """A tile's own cell offsets to its plain row and column."""
        grid = _grid()
        cell = global_cell(grid, ZERMATT_EASTING, ZERMATT_NORTHING)
        assert offset_in_tile(grid, *cell, 3239, 1990) == (106, 119)

    def test_stored_index_shifts_inward_by_the_skirt(self) -> None:
        """The north-west skirt corner is index 0 and the owned corner 259."""
        grid = _grid()
        assert stored_index(grid, -1, -1) == 0
        assert stored_index(grid, 0, 0) == grid.stored_cells + 1
        assert stored_index(grid, 255, 255) == 256 * grid.stored_cells + 256


class TestLoadGrid:
    """Fetching, parsing and caching the published definition."""

    @override_settings(TERRAIN_TILE_BASE_URL="https://tiles.example/terrain/v1")
    def test_parses_the_published_definition(self) -> None:
        """Every field the sampler depends on comes off grid.json."""
        mock_get = _mock_get(content=(FIXTURES / "grid.json").read_bytes())
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            grid = load_grid()

        assert grid is not None
        assert grid.grid == "snowdesk-terrain-5m-3035"
        assert grid.crs == "EPSG:3035"
        assert grid.cell_size_m == 5
        assert grid.tile_cells == 256
        assert grid.tile_size_m == 1280
        assert grid.skirt_cells == 1
        assert grid.stored_cells == 258
        assert grid.tile_bytes == 133128
        assert grid.height_scale_m == 0.25
        assert grid.height_offset_m == 0
        assert grid.nodata == -32768
        assert grid.row_order == "north-to-south"
        assert grid.column_order == "west-to-east"
        assert grid.default_analysis_window_m == 10
        assert grid.version == "v1"
        assert len(grid.sources) == 1

    @override_settings(TERRAIN_TILE_BASE_URL="https://tiles.example/terrain/v1")
    def test_requests_grid_json_under_the_configured_base(self) -> None:
        """The base is a base: grid.json is composed onto it."""
        mock_get = _mock_get(content=(FIXTURES / "grid.json").read_bytes())
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            load_grid()
        assert mock_get.call_args[0][0] == "https://tiles.example/terrain/v1/grid.json"

    @override_settings(TERRAIN_TILE_BASE_URL="https://tiles.example/terrain/v1/")
    def test_tolerates_a_trailing_slash_on_the_base(self) -> None:
        """A trailing slash in the environment does not double up."""
        mock_get = _mock_get(content=(FIXTURES / "grid.json").read_bytes())
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            load_grid()
        assert mock_get.call_args[0][0] == "https://tiles.example/terrain/v1/grid.json"

    def test_the_second_call_is_served_from_the_cache(self) -> None:
        """One request an hour, not one per sample."""
        mock_get = _mock_get(content=(FIXTURES / "grid.json").read_bytes())
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            first = load_grid()
            second = load_grid()

        assert mock_get.call_count == 1
        assert first == second

    def test_unreachable_origin_returns_none(self) -> None:
        """A timeout is not an exception the caller has to handle."""
        with patch(
            "apps.locations.services.terrain_grid.requests.get",
            side_effect=requests.Timeout("too slow"),
        ):
            assert load_grid() is None

    def test_a_server_error_returns_none(self) -> None:
        """A 500 is no definition."""
        mock_get = _mock_get(content=b"nope", status_code=500)
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            assert load_grid() is None

    def test_a_body_that_is_not_json_returns_none(self) -> None:
        """A proxy's error page is not a grid."""
        mock_get = _mock_get(content=b"<html>who knows</html>")
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            assert load_grid() is None

    def test_a_definition_missing_a_field_returns_none(self) -> None:
        """Half a definition is worse than none — it samples the wrong cell."""
        payload = _grid_payload()
        del payload["cell_size_m"]
        mock_get = _mock_get(content=json.dumps(payload).encode())
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            assert load_grid() is None

    def test_a_failed_fetch_is_not_cached(self) -> None:
        """An outage must not suppress the retry that fixes itself."""
        with patch(
            "apps.locations.services.terrain_grid.requests.get",
            side_effect=requests.ConnectionError("refused"),
        ):
            assert load_grid() is None

        mock_get = _mock_get(content=(FIXTURES / "grid.json").read_bytes())
        with patch("apps.locations.services.terrain_grid.requests.get", mock_get):
            assert load_grid() is not None
