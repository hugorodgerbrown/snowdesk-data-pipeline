"""
tests/e2e/test_downloaded_areas_overlay.py — Playwright regression tests for
the "Available offline" layers-menu overlay (SNOW-570, SNOW-587).

The overlay draws one translucent square per tile actually present in the
pinned basemap caches — the ``cached-tiles`` source, fed straight from a
Cache Storage read, with no stored record of any kind involved. It is off by
default. SNOW-587 removed the overlay's earlier "downloaded areas" rings
(``regions-line-downloaded`` / ``downloaded-area-line``, derived from the
stored ``basemap.regions`` / ``basemap.customAreas`` records and then
validated against the cache) — the tiles alone answer "where is the basemap
I already have?" without needing a second, driftable derivation path.

SNOW-586 gave each downloaded area its OWN Cache Storage bucket
(``snowdesk-basemap-pinned-<areaId>``), so the read is a union across every
bucket carrying that prefix rather than a lookup in one shared cache —
``pwaDownloadedOverlay`` goes through ``pinnedBasemapCacheURLs()``; see that
helper's own comment.

Most tests below write every tile into ONE bucket
(``_PINNED_CACHE_NAME``, an arbitrary name under the shared
``snowdesk-basemap-pinned-`` prefix) — the union read doesn't care which
bucket a tile lives in, only that its name carries the prefix, so this is
a faithful enough stand-in for "some real download's bucket" everywhere
the exact bucket identity isn't the point.
``test_cached_tiles_overlay_unions_across_several_buckets`` is the one
exception, writing into two DISTINCT bucket names to prove the union
itself, which single-bucket tests can't.

**Why these use the plain ``page``/``live_server`` fixtures rather than
``pwa_page``.** With the real service worker controlling, the basemap style
JSON parses but its sources never resolve against the unreachable CDN, so
``map.isStyleLoaded()`` stays false, ``map.on('load')`` never fires, and the
``regions`` source — along with every layer installed beside it, including
the cached-tiles layers — is never added at all. That is precisely why
``test_cache_this_area.py`` injects synthetic features into
``FEATURE_BY_REGION_ID`` and never touches a MapLibre layer. This overlay
*is* MapLibre layers, so it needs the harness where they exist: without the
SW the style falls back cleanly, ``load`` fires, and the real regions (149
of them, with geometry and precomputed download blobs) are on the map.

Nothing is lost by dropping the SW, because the overlay never talks to one.
It reads Cache Storage, which is available to the page directly — so these
tests write tile URLs into the pinned cache by hand and call
``window.pwaDownloadedOverlay.refresh()``. That also makes them
deterministic: the exact tile set is chosen by the test.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.usefixtures("_load_test_data")

_TOGGLE = '#basemap-menu [data-overlay-key="downloaded"]'
_TILE_FILL_LAYER = "cached-tiles-fill"
_TILE_LINE_LAYER = "cached-tiles-line"
_TEMPLATE = "https://tiles.example.invalid/{z}/{x}/{y}.pbf"
# SNOW-586: an arbitrary name under the shared prefix every per-area
# pinned bucket carries — see the module docstring for why one shared
# name is a faithful enough stand-in for most tests here.
_PINNED_CACHE_NAME = "snowdesk-basemap-pinned-test-area"


def _boot(page: Page, live_server: LiveServer) -> None:
    """Navigate, wait for the overlay's layers, and stub the tile template.

    The template stub is what makes the probe answerable at all: resolving
    the real one needs a reachable basemap CDN. ``map.js`` is a classic
    script, so its top-level ``activeBasemapTileTemplate`` is a plain,
    reassignable ``window`` property — the same stub
    ``test_cache_this_area.py`` uses.
    """
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        """() => typeof MAP !== 'undefined' && MAP !== null
            && !!MAP.getSource('regions')
            && !!MAP.getLayer('cached-tiles-fill')
            && !!MAP.getSource('cached-tiles')
            && typeof window.pwaDownloadedOverlay === 'object'""",
        timeout=30000,
    )
    page.evaluate(
        "(template) => { window.activeBasemapTileTemplate = () => template; }",
        _TEMPLATE,
    )


def _tile_url(z: int, x: int, y: int) -> str:
    """The pinned-cache URL for one tile, under the stubbed template."""
    return (
        _TEMPLATE.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    )


def _cache_urls(
    page: Page, urls: list[str], cache_name: str = _PINNED_CACHE_NAME
) -> None:
    """Write `urls` into a pinned basemap bucket as the SW's warm-cache would.

    `cache_name` defaults to the module's one shared stand-in bucket;
    pass a distinct name to prove the union read itself (SNOW-586).
    """
    page.evaluate(
        """async ({ cacheName, urls }) => {
            const cache = await caches.open(cacheName);
            for (const url of urls) {
                await cache.put(url, new Response('stub-tile'));
            }
        }""",
        {"cacheName": cache_name, "urls": urls},
    )


def _refresh(page: Page) -> None:
    """Re-derive the overlay and wait for that pass to settle."""
    page.evaluate("async () => { await window.pwaDownloadedOverlay.refresh(); }")


def _open_layers_menu(page: Page) -> None:
    if not page.locator(_TOGGLE).is_visible():
        page.click("#basemap-toggle")
    page.locator(_TOGGLE).wait_for(state="visible")


def _toggle_overlay(page: Page, *, on: bool) -> None:
    """Set the "Available offline" checkbox to `on` and wait for it to settle.

    Two separate things have to land, and only the first is synchronous:
    the picker flips layer visibility on click, while the cache probe that
    decides WHICH tiles are drawn is async. Waiting only for visibility
    would let a "no tiles drawn" assertion pass simply because the probe
    hadn't finished — every negative test here would be vacuous. The
    trailing refresh awaits that pass (it coalesces with the one the toggle
    already kicked off rather than starting a second).
    """
    _open_layers_menu(page)
    toggle = page.locator(_TOGGLE)
    if (toggle.get_attribute("aria-checked") == "true") != on:
        toggle.click()
    page.wait_for_function(
        """({ layer, expected }) => {
            if (!MAP.getLayer(layer)) return false;
            return (MAP.getLayoutProperty(layer, 'visibility') !== 'none') === expected;
        }""",
        arg={"layer": _TILE_FILL_LAYER, "expected": on},
    )
    if on:
        _refresh(page)


def _layer_visibility(page: Page, layer: str) -> str:
    return cast(
        str,
        page.evaluate(
            "(layer) => MAP.getLayer(layer)"
            " ? (MAP.getLayoutProperty(layer, 'visibility') || 'visible')"
            " : 'absent'",
            layer,
        ),
    )


def test_overlay_is_off_by_default(page: Page, live_server: LiveServer) -> None:
    """The row is unchecked and the cached-tiles layers hidden on a fresh session."""
    _boot(page, live_server)
    _open_layers_menu(page)

    assert page.locator(_TOGGLE).get_attribute("aria-checked") == "false"
    assert _layer_visibility(page, _TILE_FILL_LAYER) == "none"
    assert _layer_visibility(page, _TILE_LINE_LAYER) == "none"


def test_row_is_shaped_like_every_other_overlay_row(
    page: Page, live_server: LiveServer
) -> None:
    """It carries a sync dot, like its neighbours.

    It used to be deliberately dotless, on the reasoning that the row has
    no feed of its own. That made it the only differently-shaped row in the
    menu, which reads as a rendering fault rather than a distinction — and
    the dot does have its own thing to say: whether any basemap tiles are
    pinned at all.
    """
    _boot(page, live_server)
    _open_layers_menu(page)

    assert page.locator(f"{_TOGGLE} .sync-dot").count() == 1
    # The same markup as a row that has always had one.
    peer = '#basemap-menu [data-overlay-key="community_reports"] .sync-dot'
    assert page.locator(peer).count() == 1


def test_dot_goes_green_once_tiles_are_pinned(
    page: Page, live_server: LiveServer
) -> None:
    """The dot answers "is there an offline map at all?".

    Not "did you download an area" — the question every other dot answers
    for its own feed, asked of the basemap tiles.
    """
    _boot(page, live_server)
    _open_layers_menu(page)
    page.evaluate("async () => { await window.pwaLayerSyncStatus.refresh(); }")
    assert _sync_state(page) != "cached"

    _cache_urls(page, [_tile_url(14, 8501, 5820)])
    page.evaluate("async () => { await window.pwaLayerSyncStatus.refresh(); }")
    page.wait_for_function(
        """(sel) => {
            const dot = document.querySelector(sel);
            return !!dot && dot.dataset.syncState === 'cached';
        }""",
        arg=f"{_TOGGLE} .sync-dot",
        timeout=10000,
    )


def _sync_state(page: Page) -> str:
    """The row's current sync-dot state."""
    return cast(
        str,
        page.evaluate(
            """(sel) => {
                const dot = document.querySelector(sel);
                return dot ? (dot.dataset.syncState || '') : '';
            }""",
            f"{_TOGGLE} .sync-dot",
        ),
    )


def _cached_tile_features(page: Page) -> list[dict[str, Any]]:
    """The features currently drawn by the cached-tiles overlay."""
    return cast(
        list[dict[str, Any]],
        page.evaluate(
            """() => {
                const source = MAP.getSource('cached-tiles');
                if (!source) return [];
                const data = source.serialize().data;
                return (data && data.features) || [];
            }"""
        ),
    )


def test_cached_tiles_drop_a_tile_evicted_behind_the_apps_back(
    page: Page, live_server: LiveServer
) -> None:
    """An evicted tile drops out of the overlay on the next refresh.

    The load-bearing test for "probed, never stored": the tile is deleted
    without the app being told, so only a real Cache Storage read gets this
    right. A flag set when the user clicked Download would still claim it.
    """
    # Tiles at CACHED_TILES_ZOOM (z14) so each cached URL is one square. A
    # whole band's worth would collapse: the overlay draws z14 only, so the
    # deeper zooms of a real download fold into the z14 square containing
    # them and the count would not be the number of URLs written.
    tiles = [
        _tile_url(14, 8501, 5820),
        _tile_url(14, 8502, 5820),
        _tile_url(14, 8503, 5820),
    ]
    _boot(page, live_server)
    _toggle_overlay(page, on=True)
    _cache_urls(page, tiles)
    _refresh(page)
    assert len(_cached_tile_features(page)) == 3

    page.evaluate(
        """async ({ cacheName, url }) => {
            const cache = await caches.open(cacheName);
            await cache.delete(url);
        }""",
        {"cacheName": _PINNED_CACHE_NAME, "url": tiles[0]},
    )
    _refresh(page)

    assert len(_cached_tile_features(page)) == 2


def test_toggling_off_hides_the_cached_tiles_layers(
    page: Page, live_server: LiveServer
) -> None:
    """Switching the overlay off hides both cached-tiles layers."""
    _boot(page, live_server)
    _toggle_overlay(page, on=True)
    _toggle_overlay(page, on=False)

    assert _layer_visibility(page, _TILE_FILL_LAYER) == "none"
    assert _layer_visibility(page, _TILE_LINE_LAYER) == "none"


def test_cached_tiles_overlay_draws_one_square_per_cached_tile(
    page: Page, live_server: LiveServer
) -> None:
    """Exactly the tiles Cache Storage holds, one square each.

    This overlay is derived from the cache ALONE — no stored download
    record — which is what makes it unable to drift from what is on disk.
    It is also why it can afford to: drawing the tiles attributes them to
    nothing, so a custom-area download that merely crossed a region cannot
    be mistaken for a download OF that region.
    """
    _boot(page, live_server)
    # The refresh is a no-op while the overlay is switched off — it is a
    # cache probe, and probing for something nobody is looking at is waste.
    _toggle_overlay(page, on=True)
    _cache_urls(page, [_tile_url(14, 8501, 5820), _tile_url(14, 8502, 5820)])
    _refresh(page)

    features = _cached_tile_features(page)
    assert len(features) == 2
    # Each square is that tile's own footprint, whole — a tile is cached in
    # full whether or not a region boundary happens to cross it.
    expected = cast(
        list[list[float]],
        page.evaluate(
            """() => [
                window.pwaBasemapDownloadCore.tileBounds(14, 8501, 5820),
                window.pwaBasemapDownloadCore.tileBounds(14, 8502, 5820),
            ]"""
        ),
    )
    drawn = sorted(
        [min(p[0] for p in f["geometry"]["coordinates"][0]) for f in features]
    )
    assert drawn == pytest.approx(sorted(bounds[0] for bounds in expected))


def test_cached_tiles_overlay_unions_across_several_buckets(
    page: Page, live_server: LiveServer
) -> None:
    """SNOW-586: tiles split across per-area buckets are all drawn.

    The single-bucket tests above cannot catch a read that only ever looks
    at one bucket, because there only ever is one — and that is exactly the
    bug SNOW-586's split introduces the opportunity for. `caches.keys()`
    order is not specified, so a first-match read would drop whichever
    bucket happened to sort second; writing one tile into each of two
    distinct buckets fails against such a read regardless of which one wins.
    """
    _boot(page, live_server)
    _toggle_overlay(page, on=True)
    _cache_urls(
        page,
        [_tile_url(14, 8501, 5820)],
        cache_name="snowdesk-basemap-pinned-region-CH-4115",
    )
    _cache_urls(
        page,
        [_tile_url(14, 8502, 5820)],
        cache_name="snowdesk-basemap-pinned-custom",
    )
    _refresh(page)

    assert len(_cached_tile_features(page)) == 2


def test_cached_tiles_overlay_ignores_another_basemaps_tiles(
    page: Page, live_server: LiveServer
) -> None:
    """Per-template, so a basemap swap empties it.

    Tiles cached for one origin genuinely are not cached for another; the
    overlay reporting otherwise would be the same lie the roundels avoid.
    """
    _boot(page, live_server)
    _toggle_overlay(page, on=True)
    _cache_urls(
        page,
        [_tile_url(14, 8501, 5820), "https://other.example.invalid/14/8501/5820.pbf"],
    )
    _refresh(page)

    assert len(_cached_tile_features(page)) == 1


def test_cached_tiles_overlay_follows_the_downloaded_toggle(
    page: Page, live_server: LiveServer
) -> None:
    """The squares are part of the "Available offline" overlay, not always on."""
    _boot(page, live_server)

    assert _layer_visibility(page, _TILE_FILL_LAYER) == "none"
    assert _layer_visibility(page, _TILE_LINE_LAYER) == "none"

    _toggle_overlay(page, on=True)
    assert _layer_visibility(page, _TILE_FILL_LAYER) == "visible"
    assert _layer_visibility(page, _TILE_LINE_LAYER) == "visible"
