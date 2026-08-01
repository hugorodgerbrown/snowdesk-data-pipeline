"""
tests/e2e/test_download_progress_grid.py — Playwright regression tests for
the on-map download progress grid (SNOW-569, since reworked as a tile grid).

While a basemap download runs, the tiles it is fetching are drawn over the
map as an empty grid of squares — one per Web Mercator tile at the plan's
``gridZ`` — and each square fills in as its own tiles land. The whole grid
pulses once on success and is removed; only then does the roundel flip to
its green ``done`` state.

The squares are real tile footprints, not a percentage re-expressed as a
water level (which is what this replaced). That means the load-bearing
property is no longer "the level rises" but "a square lights up exactly
when its own tiles have landed, and not before" — a square filled early
would be claiming ground the cache does not hold.

**Why these drive ``createDownloadProgressGrid`` directly rather than
clicking Download.** The grid is MapLibre layers, and MapLibre layers only
exist in the harness *without* a service worker: with the real SW
controlling, the basemap style JSON parses but its sources never resolve
against the unreachable CDN, so ``map.isStyleLoaded()`` stays false,
``map.on('load')`` never fires, and the ``regions`` source — plus every
layer installed beside it — is never added at all. (That is why
``test_cache_this_area.py`` injects synthetic features into
``FEATURE_BY_REGION_ID`` and never touches a layer.) Dropping the SW gets
the layers back but takes ``window.pwaWarmCache`` with it, so a real
download run cannot drive the grid here either.

So these tests call the factory the download path calls, with a plan built
by the same ``pwaBasemapDownloadCore.tileGridPlan`` call, and feed it the
same ``(done, total, settled)`` reports the service worker posts. What that
leaves uncovered is two lines of wiring — ``progressFill.update(...)``
inside ``onProgress`` and ``await progressFill.finish(ok)`` inside
``finish`` — while covering the part with the actual machinery in it: the
per-cell countdown, the URL-offset arithmetic, the fallback for an older
worker, the teardown, and the no-op path. ``map.js`` is a classic script,
so its top-level ``createDownloadProgressGrid`` is a plain ``window``
property.

Assertions read each cell's MapLibre **feature state** — the grid lights a
square with ``setFeatureState`` rather than rewriting the source, since at
the band's detail floor a large region is several thousand cells. Reading
state rather than pixels matches this suite's convention (see
``test_map_placement_focus.py``'s module docstring): the flags are what the
feature is, and a screenshot diff would additionally depend on frame timing
this harness deliberately avoids.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.usefixtures("_load_test_data")

_FILL_LAYER = "download-progress-fill"
_LINE_LAYER = "download-progress-line"
_SOURCE = "download-progress"

_TEMPLATE = "https://tiles.example/{z}/{x}/{y}.pbf"

# A z10 tile over the Valais plus the 2x2 of z11 tiles beneath it — the
# same shape ``tests/js/test_basemap_download_core.js`` reasons about, so
# the grid is four cells and the z10 tile belongs to the first of them.
# Small enough that a test can settle a whole cell by hand.
_BLOB: dict[str, Any] = {
    "z": {"10": [536, 536, 358, 358], "11": [1072, 1073, 716, 717]},
}

# How many cells that blob produces, and how many tiles the first (north-
# westmost) cell owns: its own z11 tile plus the shallower z10 tile.
_CELLS = 4
_FIRST_CELL_TILES = 2


def _boot(page: Page, live_server: LiveServer) -> None:
    """Navigate and wait for a map whose style will accept a new source."""
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    # Deliberately not waiting on isStyleLoaded(): it reports
    # Style.loaded(), which also requires every source to have loaded, and
    # the basemap's tile sources never resolve in this harness. The regions
    # source being present is the signal that the style is up — and the
    # grid does not depend on isStyleLoaded() either (see _ensure).
    page.wait_for_function(
        """() => typeof MAP !== 'undefined' && MAP !== null
            && !!MAP.getSource('regions')
            && typeof window.createDownloadProgressGrid === 'function'
            && !!window.pwaBasemapDownloadCore""",
        timeout=30000,
    )


def _start_grid(page: Page, *, offset: int = 0) -> None:
    """Build a plan for `_BLOB` and park a grid over it on the window.

    Held on ``window`` rather than returned because the handle is a live JS
    object whose methods the later steps call — the same object the
    download path holds for the duration of its run. ``offset`` mirrors the
    feed URLs the real path prepends to the worker's list.
    """
    page.evaluate(
        """({template, blob, offset}) => {
            window.__plan = window.pwaBasemapDownloadCore.tileGridPlan(template, blob);
            window.__grid = window.createDownloadProgressGrid(window.__plan, offset);
        }""",
        {"template": _TEMPLATE, "blob": _BLOB, "offset": offset},
    )


def _start_grid_without_plan(page: Page) -> None:
    """Create a grid with no plan at all."""
    page.evaluate(
        "() => { window.__grid = window.createDownloadProgressGrid(null, 0); }"
    )


def _url_indices_for_cell(page: Page, cell: int, *, offset: int = 0) -> list[int]:
    """The worker-list indices of every tile belonging to `cell`."""
    return cast(
        list[int],
        page.evaluate(
            """({cell, offset}) => {
                const out = [];
                window.__plan.cellOfURL.forEach((c, i) => {
                    if (c === cell) out.push(i + offset);
                });
                return out;
            }""",
            {"cell": cell, "offset": offset},
        ),
    )


def _update(page: Page, done: int, total: int, settled: list[int] | None) -> None:
    """Feed one progress report in.

    ``setFeatureState`` applies synchronously, so unlike the setData version
    this needed no frame to settle — but a frame is still awaited so the
    assertions that follow describe a state MapLibre has actually rendered
    rather than merely accepted.
    """
    page.evaluate(
        "({done, total, settled}) => window.__grid.update(done, total, settled)",
        {"done": done, "total": total, "settled": settled},
    )
    page.evaluate(
        "() => new Promise((r) => requestAnimationFrame("
        "() => requestAnimationFrame(() => r(null))))"
    )


def _done_flags(page: Page) -> list[bool]:
    """Which cells are currently filled, in plan order.

    Read from MapLibre feature state rather than the source data: the grid
    lights a square with ``setFeatureState`` and never rewrites the
    collection, so the features themselves carry no done flag.
    """
    return cast(
        list[bool],
        page.evaluate(
            """() => {
                const source = MAP.getSource('download-progress');
                if (!source) return [];
                const data = source.serialize().data;
                if (!data || !data.features) return [];
                return data.features.map((f) => {
                    const state = MAP.getFeatureState(
                        {source: 'download-progress', id: f.id});
                    return !!(state && state.done);
                });
            }"""
        ),
    )


def _has_grid_layers(page: Page) -> bool:
    """Whether the grid's source and both its layers are on the style."""
    return cast(
        bool,
        page.evaluate(
            """() => !!MAP.getSource('download-progress')
                && !!MAP.getLayer('download-progress-fill')
                && !!MAP.getLayer('download-progress-line')"""
        ),
    )


def test_the_grid_starts_empty_and_complete(
    page: Page, live_server: LiveServer
) -> None:
    """Every square is drawn before a single tile has landed.

    The empty grid is the point of the redesign: the user sees the extent
    of what they asked for up front, then watches it fill.
    """
    _boot(page, live_server)
    _start_grid(page)

    flags = _done_flags(page)
    assert len(flags) == _CELLS
    assert not any(flags)
    assert _has_grid_layers(page)


def test_a_cell_fills_only_once_all_its_tiles_land(
    page: Page, live_server: LiveServer
) -> None:
    """A partially-settled square stays empty; the last tile fills it.

    This is the property the whole redesign rests on — a square must mean
    "this ground is cached", so it cannot light up on the first of its
    tiles.
    """
    _boot(page, live_server)
    _start_grid(page)

    indices = _url_indices_for_cell(page, 0)
    assert len(indices) == _FIRST_CELL_TILES

    # All but the last tile of cell 0.
    _update(page, len(indices) - 1, 8, indices[:-1])
    assert not any(_done_flags(page))

    # The last one completes it, and only it.
    _update(page, len(indices), 8, indices[-1:])
    flags = _done_flags(page)
    assert flags[0] is True
    assert not any(flags[1:])


def test_cells_fill_one_at_a_time(page: Page, live_server: LiveServer) -> None:
    """Working through the plan's URL order lights the squares in turn.

    ``tileGridPlan`` groups each cell's URLs together precisely so this
    holds; if the ordering regressed to zoom-major, the count would stay at
    zero until the run was nearly done.
    """
    _boot(page, live_server)
    _start_grid(page)

    total = cast(int, page.evaluate("() => window.__plan.urls.length"))
    filled: list[int] = []
    for i in range(total):
        _update(page, i + 1, total, [i])
        filled.append(sum(_done_flags(page)))

    # Monotonic, ends complete, and gets there in steps rather than all at
    # the end.
    assert filled == sorted(filled)
    assert filled[-1] == _CELLS
    assert 0 < filled[len(filled) // 2] < _CELLS


def test_feed_urls_are_ignored(page: Page, live_server: LiveServer) -> None:
    """Indices below the offset belong to no cell and fill nothing.

    The real path prepends the feed warm-up URLs to the worker's list, so
    the first few settled indices are not tiles at all.
    """
    _boot(page, live_server)
    _start_grid(page, offset=3)

    _update(page, 3, 11, [0, 1, 2])
    assert not any(_done_flags(page))

    # And the tiles behind them still land in the right cell.
    _update(page, 5, 11, _url_indices_for_cell(page, 0, offset=3))
    flags = _done_flags(page)
    assert flags[0] is True
    assert not any(flags[1:])


def test_counts_only_reports_fall_back_to_a_proportional_fill(
    page: Page, live_server: LiveServer
) -> None:
    """An older worker sends no ``settled``; the grid still fills.

    A page updated ahead of the service worker controlling it would
    otherwise show an empty grid for the whole run.
    """
    _boot(page, live_server)
    _start_grid(page)

    _update(page, 50, 100, None)
    assert sum(_done_flags(page)) == _CELLS // 2

    _update(page, 100, 100, None)
    assert sum(_done_flags(page)) == _CELLS


def test_a_successful_run_completes_then_clears_the_grid(
    page: Page, live_server: LiveServer
) -> None:
    """The grid pulses out whole and leaves nothing on the map."""
    _boot(page, live_server)
    _start_grid(page)
    _update(page, 1, 8, _url_indices_for_cell(page, 0))
    assert _has_grid_layers(page)

    page.evaluate("async () => { await window.__grid.finish(true); }")

    # finish() resolves only after the pulse, so by the time the download
    # path flips its roundel the map is back to normal.
    assert not _has_grid_layers(page)
    assert _done_flags(page) == []


def test_a_failed_run_clears_the_grid_without_filling_it(
    page: Page, live_server: LiveServer
) -> None:
    """A failure clears the grid rather than pulsing it green.

    A failed download must never read as a downloaded area, however
    briefly — so this path skips the completion and the pulse entirely.
    """
    _boot(page, live_server)
    _start_grid(page)
    _update(page, 1, 8, _url_indices_for_cell(page, 0))

    page.evaluate("async () => { await window.__grid.finish(false); }")

    assert not _has_grid_layers(page)


def test_no_plan_degrades_to_a_no_op(page: Page, live_server: LiveServer) -> None:
    """A download with nothing to draw still runs to completion.

    Both controls pass whatever ``tileGridPlan`` returned, which is null
    for a blob carrying no tile ranges. That must be a quiet no-op, not an
    error inside a download's progress handler.
    """
    _boot(page, live_server)
    _start_grid_without_plan(page)

    page.evaluate("() => window.__grid.update(1, 2, [0])")
    page.evaluate("async () => { await window.__grid.finish(true); }")

    assert not _has_grid_layers(page)


def test_grid_sits_above_every_region_layer(
    page: Page, live_server: LiveServer
) -> None:
    """The grid is its own overlay, not a wash over one region.

    Ordering is the whole visual. Under ``regions-line`` the grid picked up
    the danger colour through its own translucency, so a square straddling
    the boundary rendered as two shades and read as CUT — when in truth the
    whole tile is cached. Above every region layer it reads uniform,
    whatever is beneath it. Still below the labels, so region names and the
    selection ring survive a run.
    """
    _boot(page, live_server)
    _start_grid(page)
    _update(page, 1, 8, _url_indices_for_cell(page, 0))

    order = cast(
        list[str],
        page.evaluate("() => MAP.getStyle().layers.map((layer) => layer.id)"),
    )
    for region_layer in ("regions-fill", "regions-line"):
        assert order.index(region_layer) < order.index(_FILL_LAYER)
        assert order.index(region_layer) < order.index(_LINE_LAYER)

    first_symbol = cast(
        int | None,
        page.evaluate(
            """() => {
                const layers = MAP.getStyle().layers;
                for (let i = 0; i < layers.length; i++) {
                    if (layers[i].type === 'symbol') return i;
                }
                return null;
            }"""
        ),
    )
    if first_symbol is not None:
        assert order.index(_FILL_LAYER) < first_symbol


def test_cells_are_well_formed_closed_polygons(
    page: Page, live_server: LiveServer
) -> None:
    """Each square is a closed GeoJSON ring covering its own tile.

    A ring MapLibre accepts today but that isn't valid GeoJSON would be a
    latent bug the rendered result happens to hide; asserting the shape
    catches it at the source.
    """
    _boot(page, live_server)
    _start_grid(page)

    features = cast(
        list[dict[str, Any]],
        page.evaluate(
            "() => MAP.getSource('download-progress').serialize().data.features"
        ),
    )
    assert len(features) == _CELLS
    for feature, cell in zip(
        features,
        cast(list[dict[str, Any]], page.evaluate("() => window.__plan.cells")),
        strict=True,
    ):
        assert feature["geometry"]["type"] == "Polygon"
        ring = feature["geometry"]["coordinates"][0]
        assert len(ring) == 5
        assert ring[0] == ring[-1]
        lons = [position[0] for position in ring]
        lats = [position[1] for position in ring]
        assert min(lons) == pytest.approx(cell["bbox"][0])
        assert max(lons) == pytest.approx(cell["bbox"][2])
        assert min(lats) == pytest.approx(cell["bbox"][1])
        assert max(lats) == pytest.approx(cell["bbox"][3])
