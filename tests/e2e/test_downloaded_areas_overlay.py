"""
tests/e2e/test_downloaded_areas_overlay.py — Playwright regression tests for
the "Downloaded areas" layers-menu overlay (SNOW-570).

The overlay outlines every area currently held in ``BASEMAP_PINNED_CACHE``:
each loaded micro-region whose whole tile set is present (a ``downloaded``
feature-state driving ``regions-line-downloaded``'s ``line-opacity`` — the
same paint-driven-visibility trick ``regions-line-selected`` uses, because
MapLibre rejects feature-state inside a filter), plus the saved custom area
on its own ``downloaded-area`` source. It is off by default.

**Why these use the plain ``page``/``live_server`` fixtures rather than
``pwa_page``.** With the real service worker controlling, the basemap style
JSON parses but its sources never resolve against the unreachable CDN, so
``map.isStyleLoaded()`` stays false, ``map.on('load')`` never fires, and the
``regions`` source — along with every layer installed beside it, including
both of this overlay's — is never added at all. That is precisely why
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
chosen by the test, so "all of it", "all but one of it" and "only the
centre tile" are all expressible.

That last case is the point of the whole file. Both download shapes write
to one pinned cache over one zoom band with one URL template, so their
tiles are indistinguishable strings: a custom-area download whose frame
merely crosses a region caches some of that region's tiles — its centre one
included — without covering it. The overlay originally probed only that
centre tile and duly ringed whole regions it had never covered.
``test_centre_tile_alone_is_not_downloaded`` is the regression test.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

pytestmark = pytest.mark.usefixtures("_load_test_data")

_TOGGLE = '#basemap-menu [data-overlay-key="downloaded"]'
_REGION_LAYER = "regions-line-downloaded"
_AREA_LAYER = "downloaded-area-line"
_TILE_FILL_LAYER = "cached-tiles-fill"
_TILE_LINE_LAYER = "cached-tiles-line"
_TEMPLATE = "https://tiles.example.invalid/{z}/{x}/{y}.pbf"
_PINNED_CACHE = "snowdesk-basemap-pinned-v1"


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
            && !!MAP.getLayer('regions-line-downloaded')
            && !!MAP.getSource('downloaded-area')
            && !!MAP.getSource('cached-tiles')
            && typeof window.pwaDownloadedOverlay === 'object'""",
        timeout=30000,
    )
    page.evaluate(
        "(template) => { window.activeBasemapTileTemplate = () => template; }",
        _TEMPLATE,
    )


def _pick_region(page: Page) -> dict[str, Any]:
    """The loaded region with the fewest tiles, plus its tile URLs.

    Smallest-first purely for speed: every tile has to be written into the
    cache one ``cache.put`` at a time, and the assertions don't care which
    region they are about. Derived with the app's own tile math, which is
    the same arithmetic the server used to build the region's stored blob
    (``build_blob(bbox_from_boundary(boundary), *MICRO_BAND)``) — so this is
    genuinely the tile set a download of this region would fetch.
    """
    return cast(
        dict[str, Any],
        page.evaluate(
            """() => {
                const core = window.pwaBasemapDownloadCore;
                const [minZ, maxZ] = core.MICRO_BAND;
                let best = null;
                for (const [regionId, feature] of Object.entries(FEATURE_BY_REGION_ID)) {
                    if (!feature?.properties?.download || !feature.geometry) continue;
                    const bbox = core.geometryBounds(feature.geometry);
                    if (!bbox) continue;
                    const ranges = core.tileRangesForBBox(bbox, minZ, maxZ);
                    const count = core.tileCount(ranges);
                    if (!best || count < best.count) {
                        best = {
                            regionId,
                            featureId: feature.id,
                            count,
                            bbox,
                            band: [minZ, maxZ],
                            urls: core.rangesToTileURLs(
                                'https://tiles.example.invalid/{z}/{x}/{y}.pbf',
                                { z: ranges },
                            ),
                            centreUrl: core.centreTileURL(
                                'https://tiles.example.invalid/{z}/{x}/{y}.pbf',
                                { centre_tile: core.centreTile(bbox, maxZ) },
                            ),
                        };
                    }
                }
                return best;
            }"""
        ),
    )


def _record_download(page: Page, region: dict[str, Any]) -> None:
    """Record `region` in meta:app as the download path does on success.

    The overlay draws what you DOWNLOADED, verified against the cache — so
    a test that only fills the cache proves nothing, and one that only
    records proves nothing either. Both halves are needed, which is the
    point: neither alone can put a ring on the map.
    """
    page.evaluate(
        """async ({ regionId, bbox, band }) => {
            await window.pwaDb.put('meta:app', {
                key: 'basemap.regions',
                value: [{
                    region_id: regionId,
                    bbox,
                    band,
                    savedAt: '2026-01-01T00:00:00.000Z',
                }],
            });
        }""",
        {
            "regionId": region["regionId"],
            "bbox": region["bbox"],
            "band": region["band"],
        },
    )


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


def _is_outlined(page: Page, feature_id: int) -> bool:
    return cast(
        bool,
        page.evaluate(
            """(featureId) => {
                const state = MAP.getFeatureState({ source: 'regions', id: featureId });
                return !!(state && state.downloaded);
            }""",
            feature_id,
        ),
    )


def _open_layers_menu(page: Page) -> None:
    if not page.locator(_TOGGLE).is_visible():
        page.click("#basemap-toggle")
    page.locator(_TOGGLE).wait_for(state="visible")


def _toggle_overlay(page: Page, *, on: bool) -> None:
    """Set the "Downloaded areas" checkbox to `on` and wait for it to settle.

    Two separate things have to land, and only the first is synchronous:
    the picker flips layer visibility on click, while the cache probe that
    decides WHICH areas are outlined is async. Waiting only for visibility
    would let a "not outlined" assertion pass simply because the probe
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
        arg={"layer": _REGION_LAYER, "expected": on},
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
    """The row is unchecked and both layers hidden on a fresh session."""
    _boot(page, live_server)
    _open_layers_menu(page)

    assert page.locator(_TOGGLE).get_attribute("aria-checked") == "false"
    assert _layer_visibility(page, _REGION_LAYER) == "none"
    assert _layer_visibility(page, _AREA_LAYER) == "none"


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


def test_a_fully_cached_region_is_outlined(page: Page, live_server: LiveServer) -> None:
    """Every tile present means the region really is available offline."""
    _boot(page, live_server)
    region = _pick_region(page)
    _record_download(page, region)
    _cache_urls(page, region["urls"])
    _toggle_overlay(page, on=True)

    assert _is_outlined(page, region["featureId"])


def test_centre_tile_alone_is_not_downloaded(
    page: Page, live_server: LiveServer
) -> None:
    """One tile is not a download — the reported bug, as a regression test.

    A custom-area download whose frame crosses a region caches that
    region's centre tile without covering the region. The overlay used to
    ring the whole thing on that evidence.
    """
    _boot(page, live_server)
    region = _pick_region(page)
    # Guard against a degenerate pick: a region whose entire download IS one
    # tile could not distinguish the two rules, so this test would pass
    # vacuously against the very bug it exists to catch.
    assert region["count"] > 1, "need a multi-tile region to tell the rules apart"
    _record_download(page, region)
    _cache_urls(page, [region["centreUrl"]])
    _toggle_overlay(page, on=True)

    assert not _is_outlined(page, region["featureId"])


def test_one_missing_tile_is_not_downloaded(
    page: Page, live_server: LiveServer
) -> None:
    """A partly-cached area cannot be used offline, so it is not outlined."""
    _boot(page, live_server)
    region = _pick_region(page)
    assert region["count"] > 1, "need a multi-tile region for a partial cache"
    _record_download(page, region)
    _cache_urls(page, region["urls"][1:])
    _toggle_overlay(page, on=True)

    assert not _is_outlined(page, region["featureId"])


def test_an_uncached_record_is_not_outlined(
    page: Page, live_server: LiveServer
) -> None:
    """Intent alone is not enough — the tiles have to be there.

    The stored record says the user asked for this region; it never says
    the tiles survived. Recording without caching must draw nothing, or
    the record would be exactly the stale flag this design avoids.
    """
    _boot(page, live_server)
    region = _pick_region(page)
    _record_download(page, region)
    _toggle_overlay(page, on=True)

    assert not _is_outlined(page, region["featureId"])


def test_a_crossing_area_download_does_not_ring_the_region(
    page: Page, live_server: LiveServer
) -> None:
    """The reported bug: a framed area must not outline regions it crosses.

    Caches every tile of the region — which a generous area download over
    the same ground would do — but records only a custom-area download.
    The region was never chosen, so it must not be ringed, however much of
    it happens to be cached. Before intent-and-verify this drew a full
    region outline around someone's rectangle.
    """
    _boot(page, live_server)
    region = _pick_region(page)
    _cache_urls(page, region["urls"])
    page.evaluate(
        """async ({ bbox, band }) => {
            await window.pwaDb.put('meta:app', {
                key: 'basemap.customArea',
                value: { bbox, band, savedAt: '2026-01-01T00:00:00.000Z' },
            });
        }""",
        {"bbox": region["bbox"], "band": region["band"]},
    )
    _toggle_overlay(page, on=True)

    # The area itself is drawn — it was downloaded and its tiles are there.
    data = cast(
        dict[str, Any],
        page.evaluate("() => MAP.getSource('downloaded-area').serialize().data"),
    )
    assert data.get("geometry"), "the framed area itself should be outlined"
    # The region underneath it is not.
    assert not _is_outlined(page, region["featureId"])


def test_outline_follows_the_cache_not_a_flag(
    page: Page, live_server: LiveServer
) -> None:
    """An evicted tile drops the ring on the next refresh.

    The load-bearing test for "probed, never stored": the tile is deleted
    without the app being told, so only a real Cache Storage read gets this
    right. A flag set when the user clicked Download would still claim it.
    """
    _boot(page, live_server)
    region = _pick_region(page)
    _record_download(page, region)
    _cache_urls(page, region["urls"])
    _toggle_overlay(page, on=True)
    assert _is_outlined(page, region["featureId"])

    page.evaluate(
        """async ({ cacheName, url }) => {
            const cache = await caches.open(cacheName);
            await cache.delete(url);
        }""",
        {"cacheName": _PINNED_CACHE, "url": region["urls"][0]},
    )
    _refresh(page)

    assert not _is_outlined(page, region["featureId"])


def test_outline_is_per_basemap(page: Page, live_server: LiveServer) -> None:
    """Tiles cached for one basemap say nothing about another."""
    _boot(page, live_server)
    region = _pick_region(page)
    _record_download(page, region)
    _cache_urls(page, region["urls"])
    _toggle_overlay(page, on=True)
    assert _is_outlined(page, region["featureId"])

    page.evaluate(
        "() => { window.activeBasemapTileTemplate = "
        "() => 'https://other.example.invalid/{z}/{x}/{y}.pbf'; }"
    )
    _refresh(page)

    assert not _is_outlined(page, region["featureId"])


def test_toggling_off_hides_both_layers(page: Page, live_server: LiveServer) -> None:
    """Switching the overlay off hides the region and custom-area layers."""
    _boot(page, live_server)
    _toggle_overlay(page, on=True)
    _toggle_overlay(page, on=False)

    assert _layer_visibility(page, _REGION_LAYER) == "none"
    assert _layer_visibility(page, _AREA_LAYER) == "none"


def test_custom_area_source_starts_empty(page: Page, live_server: LiveServer) -> None:
    """With no saved custom area the area source holds no features."""
    _boot(page, live_server)
    _toggle_overlay(page, on=True)

    data = cast(
        dict[str, Any],
        page.evaluate("() => MAP.getSource('downloaded-area').serialize().data"),
    )
    assert data.get("features") == []


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
    them to nothing, so the mis-attribution the rings guard against (a
    custom-area download that merely crossed a region) cannot arise.
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
