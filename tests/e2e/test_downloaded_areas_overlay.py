"""
tests/e2e/test_downloaded_areas_overlay.py — Playwright regression tests for
the "Downloaded areas" layers-menu overlay (SNOW-570).

The overlay draws two things: a dashed ring around the saved custom area
(``downloaded-area-line``, over its own one-feature ``downloaded-area``
source) once its whole tile set is verified present in
``BASEMAP_PINNED_CACHE``, and a square per tile Cache Storage actually
holds (``cached-tiles-fill``/``cached-tiles-line``, over the
``cached-tiles`` source) — read straight back out of the cache, no stored
record involved. It is off by default.

**SNOW-583 removed the per-region ring.** Region downloads are now clipped
to each region's real boundary plus a margin tile
(``apps.regions.services.basemap_tiles.build_region_blob``), so the tile
set a download actually fetches is no longer recomputable client-side from
the region's own geometry the way ``downloadedIds`` (SNOW-570) did — that
recomputation is exactly what this overlay's old region half relied on.
The cached-tiles squares are now the whole "what do I have offline" answer
for a region; whether *a specific* region's own download is complete is
still answered — precisely, via a stored ``basemap.regions`` record and
the server-computed clipped tile set — by the per-region roundel, covered
in ``tests/e2e/test_cache_this_area.py``. The custom-area ring survives
unchanged: a user-drawn rectangle stays enumerable client-side
(``core.tileRangesForBBox``), so its full-coverage check is unaffected by
the clip.

**Why these use the plain ``page``/``live_server`` fixtures rather than
``pwa_page``.** With the real service worker controlling, the basemap style
JSON parses but its sources never resolve against the unreachable CDN, so
``map.isStyleLoaded()`` stays false, ``map.on('load')`` never fires, and the
``regions`` source — along with every layer installed beside it, including
this overlay's — is never added at all. That is precisely why
``test_cache_this_area.py`` injects synthetic features into
``FEATURE_BY_REGION_ID`` and never touches a MapLibre layer. This overlay
*is* MapLibre layers, so it needs the harness where they exist: without the
SW the style falls back cleanly, ``load`` fires, and the real regions (149
of them, with geometry and precomputed download blobs) are on the map.

Nothing is lost by dropping the SW, because the overlay never talks to one.
It reads Cache Storage, which is available to the page directly — so these
tests write tile URLs into the pinned cache by hand and call
``window.pwaDownloadedOverlay.refresh()``. That also makes them
deterministic in a way a stubbed download never was: the exact tile set is
chosen by the test, so "all of it" and "all but one of it" are both
expressible.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.usefixtures("_load_test_data")

_TOGGLE = '#basemap-menu [data-overlay-key="downloaded"]'
_AREA_LAYER = "downloaded-area-line"
_TILE_FILL_LAYER = "cached-tiles-fill"
_TILE_LINE_LAYER = "cached-tiles-line"
_TEMPLATE = "https://tiles.example.invalid/{z}/{x}/{y}.pbf"
_PINNED_CACHE = "snowdesk-basemap-pinned-v1"

# A small custom area — narrow band, so a full-coverage cache is a handful
# of ``cache.put`` calls rather than a whole micro band's worth.
_CUSTOM_BBOX = [7.5, 46.25, 7.6, 46.35]
_CUSTOM_BAND = [10, 11]


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
            && !!MAP.getLayer('downloaded-area-line')
            && !!MAP.getSource('downloaded-area')
            && !!MAP.getSource('cached-tiles')
            && typeof window.pwaDownloadedOverlay === 'object'""",
        timeout=30000,
    )
    page.evaluate(
        "(template) => { window.activeBasemapTileTemplate = () => template; }",
        _TEMPLATE,
    )


def _custom_area_tiles(page: Page, bbox: list[float], band: list[int]) -> list[str]:
    """Every tile URL a ``band`` download of ``bbox`` fetches.

    Built with the page's own tile math (the custom-area download's own
    client-side re-port — see ``basemap_download_core.js``'s header), not
    hand-listed indices, so the assertions below are about the overlay's
    membership logic on top of it, not about hand-derived tile arithmetic
    drifting from the real thing.
    """
    return cast(
        list[str],
        page.evaluate(
            """({ bbox, band, template }) => {
                const core = window.pwaBasemapDownloadCore;
                const ranges = core.tileRangesForBBox(bbox, band[0], band[1]);
                return core.rangesToTileURLs(template, { z: ranges });
            }""",
            {"bbox": bbox, "band": band, "template": _TEMPLATE},
        ),
    )


def _save_custom_area(page: Page, bbox: list[float], band: list[int]) -> None:
    """Persist ``basemap.customArea`` exactly as a confirmed download would."""
    page.evaluate(
        """async ({ bbox, band }) => {
            await window.pwaDb.put('meta:app', {
                key: 'basemap.customArea',
                value: { bbox, band, savedAt: '2026-01-01T00:00:00.000Z' },
            });
        }""",
        {"bbox": bbox, "band": band},
    )


def _area_outline_geometry(page: Page) -> dict[str, Any] | None:
    """The geometry currently drawn on the ``downloaded-area`` source, or None."""
    data = cast(
        dict[str, Any],
        page.evaluate("() => MAP.getSource('downloaded-area').serialize().data"),
    )
    return cast("dict[str, Any] | None", data.get("geometry"))


def _cache_urls(page: Page, urls: list[str]) -> None:
    """Write `urls` into the pinned basemap cache as the SW's warm-cache would."""
    page.evaluate(
        """async ({ cacheName, urls }) => {
            const cache = await caches.open(cacheName);
            for (const url of urls) {
                await cache.put(url, new Response('stub-tile'));
            }
        }""",
        {"cacheName": _PINNED_CACHE, "urls": urls},
    )


def _refresh(page: Page) -> None:
    """Re-derive the overlay and wait for that pass to settle."""
    page.evaluate("async () => { await window.pwaDownloadedOverlay.refresh(); }")


def _open_layers_menu(page: Page) -> None:
    if not page.locator(_TOGGLE).is_visible():
        page.click("#basemap-toggle")
    page.locator(_TOGGLE).wait_for(state="visible")


def _toggle_overlay(page: Page, *, on: bool) -> None:
    """Set the "Downloaded areas" checkbox to `on` and wait for it to settle.

    Two separate things have to land, and only the first is synchronous:
    the picker flips layer visibility on click, while the cache probe that
    decides what is outlined is async. Waiting only for visibility would
    let a "not outlined" assertion pass simply because the probe hadn't
    finished — every negative test here would be vacuous. The trailing
    refresh awaits that pass (it coalesces with the one the toggle already
    kicked off rather than starting a second).
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
        arg={"layer": _AREA_LAYER, "expected": on},
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
    """The row is unchecked and every overlay layer hidden on a fresh session."""
    _boot(page, live_server)
    _open_layers_menu(page)

    assert page.locator(_TOGGLE).get_attribute("aria-checked") == "false"
    assert _layer_visibility(page, _AREA_LAYER) == "none"
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

    _cache_urls(
        page,
        [_TEMPLATE.replace("{z}", "14").replace("{x}", "8501").replace("{y}", "5820")],
    )
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


def test_custom_area_source_starts_empty(page: Page, live_server: LiveServer) -> None:
    """With no saved custom area the area source holds no features."""
    _boot(page, live_server)
    _toggle_overlay(page, on=True)

    data = cast(
        dict[str, Any],
        page.evaluate("() => MAP.getSource('downloaded-area').serialize().data"),
    )
    assert data.get("features") == []


def test_custom_area_is_outlined_once_its_tiles_are_cached(
    page: Page, live_server: LiveServer
) -> None:
    """A saved custom area gets a ring once every one of its tiles is cached."""
    _boot(page, live_server)
    urls = _custom_area_tiles(page, _CUSTOM_BBOX, _CUSTOM_BAND)
    _cache_urls(page, urls)
    _save_custom_area(page, _CUSTOM_BBOX, _CUSTOM_BAND)
    _toggle_overlay(page, on=True)

    assert _area_outline_geometry(page), "the saved area should be outlined"


def test_custom_area_record_without_cached_tiles_is_not_outlined(
    page: Page, live_server: LiveServer
) -> None:
    """Intent alone is not enough — the tiles have to be there.

    The stored record says the user asked for this area; it never says the
    tiles survived. Recording without caching must draw nothing, or the
    record would be exactly the stale flag this design avoids.
    """
    _boot(page, live_server)
    _save_custom_area(page, _CUSTOM_BBOX, _CUSTOM_BAND)
    _toggle_overlay(page, on=True)

    assert not _area_outline_geometry(page)


def test_custom_area_outline_follows_the_cache_not_a_flag(
    page: Page, live_server: LiveServer
) -> None:
    """An evicted tile drops the ring on the next refresh.

    The load-bearing test for "probed, never stored": the tile is deleted
    without the app being told, so only a real Cache Storage read gets this
    right. A flag set when the user confirmed the download would still
    claim it.
    """
    _boot(page, live_server)
    urls = _custom_area_tiles(page, _CUSTOM_BBOX, _CUSTOM_BAND)
    _cache_urls(page, urls)
    _save_custom_area(page, _CUSTOM_BBOX, _CUSTOM_BAND)
    _toggle_overlay(page, on=True)
    assert _area_outline_geometry(page)

    page.evaluate(
        """async ({ cacheName, url }) => {
            const cache = await caches.open(cacheName);
            await cache.delete(url);
        }""",
        {"cacheName": _PINNED_CACHE, "url": urls[0]},
    )
    _refresh(page)

    assert not _area_outline_geometry(page)


def test_custom_area_outline_is_per_basemap(
    page: Page, live_server: LiveServer
) -> None:
    """Tiles cached for one basemap say nothing about another."""
    _boot(page, live_server)
    urls = _custom_area_tiles(page, _CUSTOM_BBOX, _CUSTOM_BAND)
    _cache_urls(page, urls)
    _save_custom_area(page, _CUSTOM_BBOX, _CUSTOM_BAND)
    _toggle_overlay(page, on=True)
    assert _area_outline_geometry(page)

    page.evaluate(
        "() => { window.activeBasemapTileTemplate = "
        "() => 'https://other.example.invalid/{z}/{x}/{y}.pbf'; }"
    )
    _refresh(page)

    assert not _area_outline_geometry(page)


def test_toggling_off_hides_every_overlay_layer(
    page: Page, live_server: LiveServer
) -> None:
    """Switching the overlay off hides the custom-area and cached-tiles layers."""
    _boot(page, live_server)
    _toggle_overlay(page, on=True)
    _toggle_overlay(page, on=False)

    assert _layer_visibility(page, _AREA_LAYER) == "none"
    assert _layer_visibility(page, _TILE_FILL_LAYER) == "none"
    assert _layer_visibility(page, _TILE_LINE_LAYER) == "none"


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


def test_cached_tiles_overlay_draws_one_square_per_cached_tile(
    page: Page, live_server: LiveServer
) -> None:
    """Exactly the tiles Cache Storage holds, one square each.

    This half of the overlay is derived from the cache ALONE — no stored
    download record — which is what makes it unable to drift from what is
    on disk. It is also why it can afford to: drawing the tiles attributes
    them to nothing, so the mis-attribution the old region ring guarded
    against (a custom-area download that merely crossed a region) cannot
    arise — SNOW-583 removed that ring, but this half was never built on
    the assumption it existed.
    """
    _boot(page, live_server)
    # The refresh is a no-op while the overlay is switched off — it is a
    # cache probe, and probing for something nobody is looking at is waste.
    _toggle_overlay(page, on=True)
    _cache_urls(
        page,
        [
            _TEMPLATE.replace("{z}", "14")
            .replace("{x}", "8501")
            .replace("{y}", "5820"),
            _TEMPLATE.replace("{z}", "14")
            .replace("{x}", "8502")
            .replace("{y}", "5820"),
        ],
    )
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
        [
            _TEMPLATE.replace("{z}", "14")
            .replace("{x}", "8501")
            .replace("{y}", "5820"),
            "https://other.example.invalid/14/8501/5820.pbf",
        ],
    )
    _refresh(page)

    assert len(_cached_tile_features(page)) == 1


def test_cached_tiles_overlay_follows_the_downloaded_toggle(
    page: Page, live_server: LiveServer
) -> None:
    """The squares are part of the "Downloaded areas" overlay, not always on."""
    _boot(page, live_server)

    assert _layer_visibility(page, _TILE_FILL_LAYER) == "none"
    assert _layer_visibility(page, _TILE_LINE_LAYER) == "none"

    _toggle_overlay(page, on=True)
    assert _layer_visibility(page, _TILE_FILL_LAYER) == "visible"
    assert _layer_visibility(page, _TILE_LINE_LAYER) == "visible"
