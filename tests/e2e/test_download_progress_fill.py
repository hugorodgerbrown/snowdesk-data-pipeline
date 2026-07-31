"""
tests/e2e/test_download_progress_fill.py — Playwright regression tests for
the on-map download progress fill (SNOW-569).

While a basemap download runs, the thing being downloaded fills from its
southern edge upwards — a region's own boundary for
``#map-download-control``, the framed rectangle for
``#map-custom-download-control`` — pulses once on success, and is removed;
only then does the roundel flip to its green ``done`` state. The geometry
is real, not a paint trick: MapLibre has no clip-path and no per-pixel
gradient on a ``fill`` layer, so each tick clips the geometry against the
half-plane below the current level (``pwaBasemapDownloadCore.
clipPolygonBelow``) and pushes the result through ``setData``.

These tests therefore assert on the ``download-progress`` SOURCE's data —
the latitude the fill has reached — rather than on pixels, matching this
suite's established "assert state, not screenshots" convention (see
``test_map_placement_focus.py``'s module docstring). The level is what the
feature is; a screenshot diff would additionally depend on frame timing
this harness deliberately avoids.

Reuses ``test_cache_this_area.py``'s helpers rather than duplicating them.
``_stub_warm_cache``'s ``progress_steps``/``step_delay_ms`` are what make
an intermediate fill observable at all: they drive ``onProgress`` through
the real ``sw.js``/``sw_register.js`` message round trip, spaced far
enough apart that a ``wait_for_function`` poll lands inside the run. The
page-side ``window.pwaWarmCache`` is frozen and non-writable, so stubbing
the SW's own ``self._warmCache`` is the only place a stub can go — see
that helper's docstring.

``_select_region`` in that module injects a synthetic feature with
PROPERTIES ONLY, no geometry. That is deliberate there and load-bearing
here: a download with no geometry to fill must still run to completion, so
this file has its own ``_select_region_with_geometry`` for the cases that
need something to fill, and one test pins the geometry-less path.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import PwaPage
from tests.e2e.test_cache_this_area import (
    _MICRO_SUMMARY,
    _reload_home,
    _select_region,
    _stub_active_basemap_template,
    _stub_region_basemap_tiles,
    _stub_warm_cache,
    _wait_for_map_ready,
    _wait_for_state,
)
from tests.e2e.test_custom_download_area import (
    _frame_a_downloadable_area,
    _open_framing,
)

pytestmark = pytest.mark.usefixtures("_load_test_data")

_CONTROL = "#map-download-control"
_CUSTOM_CONTROL = "#map-custom-download-control"

# The synthetic region's boundary: a 1° square in the Valais, chosen so
# every assertion below can talk about plain latitudes (46 at the bottom,
# 47 at the top) with no projection arithmetic in the test itself.
_SOUTH = 46.0
_NORTH = 47.0
_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [[7.0, _SOUTH], [8.0, _SOUTH], [8.0, _NORTH], [7.0, _NORTH], [7.0, _SOUTH]]
    ],
}

# Four evenly-spaced ticks, a quarter-second apart. The spacing is the
# whole point: it holds each intermediate level on screen long enough for
# a wait_for_function poll to see it, without making the test slow.
_PROGRESS_STEPS = [(1, 4), (2, 4), (3, 4), (4, 4)]
_STEP_DELAY_MS = 250


def _select_region_with_geometry(page: Page, region_id: str) -> None:
    """Focus ``region_id`` with a real boundary attached.

    ``test_cache_this_area._select_region`` injects properties only, which
    leaves the progress fill with nothing to clip — see the module
    docstring. This is the same injection plus ``_SQUARE`` as the
    feature's geometry.
    """
    page.evaluate(
        """({ regionId, download, geometry }) => {
            FEATURE_BY_REGION_ID[regionId] = {
                type: 'Feature',
                geometry,
                properties: { id: regionId, regionID: regionId, download },
            };
            document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
                detail: { region_id: regionId, region_name: regionId },
            }));
        }""",
        {"regionId": region_id, "download": _MICRO_SUMMARY, "geometry": _SQUARE},
    )


def _wait_for_style_loaded(page: Page) -> None:
    """Wait until the style will accept an ``addSource``.

    The fill adds its source on the first progress tick and skips (to
    retry on the next one) while the style is in no state to take one —
    so without this a fast run could conceivably finish having never
    painted. The regions source is added inside ``map.on('load')``, which
    makes it the available signal that the style is up.
    """
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && "
        "!!MAP.getSource('regions') && MAP.isStyleLoaded()"
    )


def _fill_north_edge(page: Page) -> float | None:
    """The latitude the fill has currently reached, or ``None``.

    ``None`` covers both "no source on the style" and "source present but
    holding the empty collection" — neither is a level, and a test that
    wants to tell them apart should ask ``_has_fill_layers`` instead.
    """
    return cast(
        float | None,
        page.evaluate(
            """() => {
                const source = MAP.getSource('download-progress');
                if (!source) return null;
                const data = source.serialize().data;
                if (!data || !data.geometry) return null;
                const bounds = window.pwaBasemapDownloadCore.geometryBounds(data.geometry);
                return bounds ? bounds[3] : null;
            }"""
        ),
    )


def _has_fill_layers(page: Page) -> bool:
    """Whether the fill's source and both its layers are on the style."""
    return cast(
        bool,
        page.evaluate(
            """() => !!MAP.getSource('download-progress')
                && !!MAP.getLayer('download-progress-fill')
                && !!MAP.getLayer('download-progress-line')"""
        ),
    )


def _boot(pwa_page: PwaPage, *, ok: int = 1, failed: int = 0) -> Page:
    """Reload, stub the SW warm-cache run and the basemap/tiles lookups.

    Every test here needs the same sequence. ``ok``/``failed`` choose the
    outcome the stubbed run reports back.
    """
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    _stub_warm_cache(
        page.context.service_workers[0],
        ok=ok,
        failed=failed,
        progress_steps=_PROGRESS_STEPS,
        step_delay_ms=_STEP_DELAY_MS,
    )
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _wait_for_style_loaded(page)
    return page


def test_region_fill_rises_from_the_southern_edge(pwa_page: PwaPage) -> None:
    """Mid-run the region is partly filled, from its south edge upwards."""
    page = _boot(pwa_page)
    _select_region_with_geometry(page, "CH-4115")
    icon = page.locator(_CONTROL)
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    icon.click()

    # Partly filled: the level has left the southern edge but not yet
    # reached the northern one. Asserting the strict interior is what
    # separates a rising fill from a layer that simply appears whole.
    page.wait_for_function(
        """({ south, north }) => {
            const source = MAP.getSource('download-progress');
            if (!source) return false;
            const data = source.serialize().data;
            if (!data || !data.geometry) return false;
            const bounds = window.pwaBasemapDownloadCore.geometryBounds(data.geometry);
            return !!bounds && bounds[1] === south && bounds[3] > south && bounds[3] < north;
        }""",
        arg={"south": _SOUTH, "north": _NORTH},
        timeout=10000,
    )


def test_region_fill_is_cleared_once_the_icon_goes_green(pwa_page: PwaPage) -> None:
    """The fill pulses out and is gone by the time the roundel reads done."""
    page = _boot(pwa_page)
    _select_region_with_geometry(page, "CH-4115")
    icon = page.locator(_CONTROL)
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    icon.click()
    page.wait_for_function("() => !!MAP.getSource('download-progress')", timeout=10000)
    _wait_for_state(page, "done", timeout=10000)

    # The pulse is awaited before the state flips, so by the time the icon
    # is green the map is back to normal — no green region left behind.
    assert not _has_fill_layers(page)
    assert _fill_north_edge(page) is None


def test_failed_region_run_leaves_no_fill_behind(pwa_page: PwaPage) -> None:
    """A run that fails clears the fill rather than pulsing it green."""
    page = _boot(pwa_page, ok=0, failed=1)
    _select_region_with_geometry(page, "CH-4115")
    icon = page.locator(_CONTROL)
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    icon.click()
    _wait_for_state(page, "busy")
    # SNOW-568's error state, and nothing left on the map — a failure must
    # never read as a downloaded region, however briefly. The fill is
    # cleared without a pulse, so the error arrives with no added delay.
    _wait_for_state(page, "error", timeout=10000)
    assert not _has_fill_layers(page)


def test_region_without_geometry_still_completes(pwa_page: PwaPage) -> None:
    """No boundary to fill degrades to the pre-SNOW-569 behaviour."""
    page = _boot(pwa_page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)
    icon = page.locator(_CONTROL)
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    icon.click()
    _wait_for_state(page, "done", timeout=10000)
    assert not _has_fill_layers(page)


def test_custom_area_fill_rises_and_clears(pwa_page: PwaPage) -> None:
    """The framed custom area fills and clears exactly as a region does."""
    page = _boot(pwa_page)
    _wait_for_state(page, "idle", selector=_CUSTOM_CONTROL)
    _open_framing(page)
    # The default view frames most of Switzerland, which is over the
    # ceiling — see this helper's docstring in test_custom_download_area.
    _frame_a_downloadable_area(page)
    page.click("#map-frame-confirm")

    # Same two beats as the region case: a level somewhere inside the
    # framed box mid-run, then nothing once the roundel goes green.
    page.wait_for_function(
        """() => {
            const source = MAP.getSource('download-progress');
            if (!source) return false;
            const data = source.serialize().data;
            return !!(data && data.geometry);
        }""",
        timeout=10000,
    )
    _wait_for_state(page, "done", timeout=10000, selector=_CUSTOM_CONTROL)
    assert not _has_fill_layers(page)


def test_fill_source_is_json_serialisable_geojson(pwa_page: PwaPage) -> None:
    """The clipped level is a well-formed, closed MultiPolygon.

    A ring MapLibre accepts today but that isn't valid GeoJSON would be a
    latent bug the rendered result happens to hide; asserting the shape
    catches it at the source.
    """
    page = _boot(pwa_page)
    _select_region_with_geometry(page, "CH-4115")
    icon = page.locator(_CONTROL)
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    icon.click()
    page.wait_for_function(
        """() => {
            const source = MAP.getSource('download-progress');
            if (!source) return false;
            const data = source.serialize().data;
            return !!(data && data.geometry);
        }""",
        timeout=10000,
    )
    geometry = json.loads(
        cast(
            str,
            page.evaluate(
                "() => JSON.stringify(MAP.getSource('download-progress')"
                ".serialize().data.geometry)"
            ),
        )
    )

    assert geometry["type"] == "MultiPolygon"
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            assert len(ring) >= 4
            assert ring[0] == ring[-1]
