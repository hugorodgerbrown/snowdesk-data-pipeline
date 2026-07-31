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

**Why these drive ``createDownloadProgressFill`` directly rather than
clicking Download.** The fill is MapLibre layers, and MapLibre layers only
exist in the harness *without* a service worker: with the real SW
controlling, the basemap style JSON parses but its sources never resolve
against the unreachable CDN, so ``map.isStyleLoaded()`` stays false,
``map.on('load')`` never fires, and the ``regions`` source — plus every
layer installed beside it — is never added at all. (That is why
``test_cache_this_area.py`` injects synthetic features into
``FEATURE_BY_REGION_ID`` and never touches a layer.) Dropping the SW gets
the layers back but takes ``window.pwaWarmCache`` with it, so a real
download run cannot drive the fill here either.

So these tests call the factory the download path calls, with the same
arguments, and step it by hand. What that leaves uncovered is two lines of
wiring — ``progressFill.update(pct)`` inside ``onProgress`` and
``await progressFill.finish(ok)`` inside ``finish`` — while covering the
part with the actual machinery in it: the clip, the rising level, the
teardown, and the no-op path. ``map.js`` is a classic script, so its
top-level ``createDownloadProgressFill`` is a plain ``window`` property.

Assertions read the ``download-progress`` source's data — the latitude the
fill has reached — rather than pixels, matching this suite's convention
(see ``test_map_placement_focus.py``'s module docstring). The level is what
the feature is; a screenshot diff would additionally depend on frame timing
this harness deliberately avoids.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.usefixtures("_load_test_data")

_FILL_LAYER = "download-progress-fill"
_LINE_LAYER = "download-progress-line"

# A 1° square in the Valais, chosen so every assertion below can talk about
# plain latitudes (46 at the bottom, 47 at the top) with no projection
# arithmetic in the test itself.
_SOUTH = 46.0
_NORTH = 47.0
_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [[7.0, _SOUTH], [8.0, _SOUTH], [8.0, _NORTH], [7.0, _NORTH], [7.0, _SOUTH]]
    ],
}


def _boot(page: Page, live_server: LiveServer) -> None:
    """Navigate and wait for a map whose style will accept a new source."""
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    # Deliberately not waiting on isStyleLoaded(): it reports
    # Style.loaded(), which also requires every source to have loaded, and
    # the basemap's tile sources never resolve in this harness. The regions
    # source being present is the signal that the style is up — and the
    # fill does not depend on isStyleLoaded() either (see _ensure).
    page.wait_for_function(
        """() => typeof MAP !== 'undefined' && MAP !== null
            && !!MAP.getSource('regions')
            && typeof window.createDownloadProgressFill === 'function'""",
        timeout=30000,
    )


def _start_fill(page: Page, geometry: dict[str, Any] | None = None) -> None:
    """Create a progress fill over `geometry` and park it on the window.

    Held on ``window`` rather than returned because the handle is a live JS
    object whose methods the later steps call — the same object the
    download path holds for the duration of its run. ``None`` means "a
    download with no geometry to fill", which must be a quiet no-op.
    """
    page.evaluate(
        "(geometry) => { window.__fill = createDownloadProgressFill(geometry); }",
        _SQUARE if geometry is None else geometry,
    )


def _start_fill_without_geometry(page: Page) -> None:
    """Create a progress fill with no geometry at all."""
    page.evaluate("() => { window.__fill = createDownloadProgressFill(undefined); }")


def _update(page: Page, pct: int) -> None:
    """Raise the fill to `pct` and wait for the coalesced repaint to land."""
    page.evaluate("(pct) => window.__fill.update(pct)", pct)
    # update() defers its paint to an animation frame, so the source is not
    # written synchronously. Waiting on the data itself, rather than on a
    # timeout, is what makes this deterministic.
    page.wait_for_function(
        """() => {
            const source = MAP.getSource('download-progress');
            if (!source) return false;
            const data = source.serialize().data;
            return !!(data && data.geometry);
        }""",
        timeout=10000,
    )


def _finish(page: Page, *, ok: bool) -> None:
    """Settle the fill and wait for the pulse (success only) to play out."""
    page.evaluate("async (ok) => { await window.__fill.finish(ok); }", ok)


def _fill_north_edge(page: Page) -> float | None:
    """The latitude the fill has currently reached, or ``None``."""
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


def test_fill_rises_from_the_southern_edge(page: Page, live_server: LiveServer) -> None:
    """Mid-run the geometry is partly filled, from its south edge upwards."""
    _boot(page, live_server)
    _start_fill(page)
    _update(page, 25)

    bounds = cast(
        list[float],
        page.evaluate(
            """() => {
                const data = MAP.getSource('download-progress').serialize().data;
                return window.pwaBasemapDownloadCore.geometryBounds(data.geometry);
            }"""
        ),
    )
    # Anchored on the southern edge, and stopping short of the northern one.
    # Asserting the strict interior is what separates a rising fill from a
    # layer that simply appears whole.
    assert bounds[1] == _SOUTH
    assert _SOUTH < bounds[3] < _NORTH


def test_fill_level_rises_with_progress(page: Page, live_server: LiveServer) -> None:
    """Each tick moves the level up, and never back down."""
    _boot(page, live_server)
    _start_fill(page)

    levels: list[float] = []
    for pct in (10, 40, 80):
        _update(page, pct)
        level = _fill_north_edge(page)
        assert level is not None, f"no level painted at {pct}%"
        levels.append(level)

    assert levels == sorted(levels)
    assert levels[0] != levels[-1]


def test_a_successful_run_clears_the_fill(page: Page, live_server: LiveServer) -> None:
    """The fill pulses out and leaves nothing on the map."""
    _boot(page, live_server)
    _start_fill(page)
    _update(page, 50)
    assert _has_fill_layers(page)

    _finish(page, ok=True)

    # finish() resolves only after the pulse, so by the time the download
    # path flips its roundel the map is back to normal.
    assert not _has_fill_layers(page)
    assert _fill_north_edge(page) is None


def test_a_failed_run_clears_the_fill_too(page: Page, live_server: LiveServer) -> None:
    """A failure clears the fill rather than pulsing it green.

    A failed download must never read as a downloaded region, however
    briefly — so this path skips the pulse entirely.
    """
    _boot(page, live_server)
    _start_fill(page)
    _update(page, 50)

    _finish(page, ok=False)

    assert not _has_fill_layers(page)


def test_no_geometry_degrades_to_a_no_op(page: Page, live_server: LiveServer) -> None:
    """A download with nothing to fill still runs to completion.

    The region control passes ``feature && feature.geometry``, which is
    undefined for a feature carrying no boundary. That must be a quiet
    no-op, not an error inside a download's progress handler.
    """
    _boot(page, live_server)
    _start_fill_without_geometry(page)

    page.evaluate("() => window.__fill.update(50)")
    page.evaluate("async () => { await window.__fill.finish(true); }")

    assert not _has_fill_layers(page)


def test_fill_sits_above_the_choropleth(page: Page, live_server: LiveServer) -> None:
    """The fill reads as the region filling up, under its outline and label.

    Ordering is the whole visual: above the choropleth so it reads as that
    region filling, below ``regions-line`` so the selection ring and region
    name stay legible through it.
    """
    _boot(page, live_server)
    _start_fill(page)
    _update(page, 50)

    order = cast(
        list[str],
        page.evaluate("() => MAP.getStyle().layers.map((layer) => layer.id)"),
    )
    assert order.index("regions-fill") < order.index(_FILL_LAYER)
    assert order.index(_FILL_LAYER) < order.index("regions-line")
    assert order.index(_LINE_LAYER) < order.index("regions-line")


def test_clipped_level_is_well_formed_geojson(
    page: Page, live_server: LiveServer
) -> None:
    """The clipped level is a closed MultiPolygon.

    A ring MapLibre accepts today but that isn't valid GeoJSON would be a
    latent bug the rendered result happens to hide; asserting the shape
    catches it at the source.
    """
    _boot(page, live_server)
    _start_fill(page)
    _update(page, 50)

    geometry = json.loads(
        cast(
            str,
            page.evaluate(
                "() => JSON.stringify("
                "MAP.getSource('download-progress').serialize().data.geometry)"
            ),
        )
    )

    assert geometry["type"] == "MultiPolygon"
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            assert len(ring) >= 4
            assert ring[0] == ring[-1]
