"""
tests/public/test_tile_utils.py — Tests for the slippy-map tile-URL helpers.

Covers the three module-private helpers in ``public.api``:

* ``_lon_to_tile_x`` — longitude → tile X coordinate.
* ``_lat_to_tile_y`` — latitude → tile Y coordinate.
* ``_generate_tile_urls`` — enumerate tile URLs for a bounding box.

Expected tile counts for the Swiss bbox (5.9, 45.8, 10.5, 47.8) are
derived from the architect's specification and verified against the
standard slippy-map projection formulas.
"""

from __future__ import annotations

from public.api import _generate_tile_urls, _lat_to_tile_y, _lon_to_tile_x

_SWISS_BBOX = (5.9, 45.8, 10.5, 47.8)
_DUMMY_TEMPLATE = "https://tiles.example.com/{z}/{x}/{y}.pbf"


# ---------------------------------------------------------------------------
# _lon_to_tile_x
# ---------------------------------------------------------------------------


def test_lon_to_tile_x_known_values() -> None:
    """Known longitude → tile X values at z10 for the Swiss bbox edges."""
    assert _lon_to_tile_x(5.9, 10) == 528
    assert _lon_to_tile_x(10.5, 10) == 541


# ---------------------------------------------------------------------------
# _lat_to_tile_y
# ---------------------------------------------------------------------------


def test_lat_to_tile_y_known_values() -> None:
    """Known latitude → tile Y values at z10 for the Swiss bbox edges."""
    # Higher latitude → lower Y (Y axis inverted).
    assert _lat_to_tile_y(47.8, 10) == 356
    assert _lat_to_tile_y(45.8, 10) == 365


# ---------------------------------------------------------------------------
# _generate_tile_urls — tile counts
# ---------------------------------------------------------------------------


def test_generate_tile_urls_swiss_bbox_z5_through_z10() -> None:
    """Vector tile count for the Swiss bbox at z5–z10 must equal 193."""
    urls = _generate_tile_urls(_DUMMY_TEMPLATE, _SWISS_BBOX, range(5, 11))
    assert len(urls) == 193


def test_generate_tile_urls_swiss_bbox_raster_z5_z6() -> None:
    """Natural Earth raster tile count for the Swiss bbox at z5–z6 must equal 2."""
    urls = _generate_tile_urls(_DUMMY_TEMPLATE, _SWISS_BBOX, range(5, 7))
    assert len(urls) == 2


def test_generate_tile_urls_empty_range() -> None:
    """An empty zoom range produces an empty URL list."""
    urls = _generate_tile_urls(_DUMMY_TEMPLATE, _SWISS_BBOX, range(0, 0))
    assert urls == []


# ---------------------------------------------------------------------------
# _generate_tile_urls — URL formatting
# ---------------------------------------------------------------------------


def test_generate_tile_urls_url_template_formatting() -> None:
    """No ``{`` or ``}`` placeholders remain in any returned URL."""
    urls = _generate_tile_urls(_DUMMY_TEMPLATE, _SWISS_BBOX, range(5, 8))
    assert urls  # Sanity check: list is non-empty.
    for url in urls:
        assert "{" not in url
        assert "}" not in url
