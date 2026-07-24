"""
tests/e2e/test_map_layer_sync_status.py — Playwright regression test for
SNOW-505: the map layers popover's sync-status dots.

Uses the SW-stripped navigation pattern (mirrors
test_map_overlay_cache_isolation.py's ``_navigate_home_with_sw_stripped``)
rather than a real registered service worker.
``window.pwaLayerSyncStatus``'s probes read Cache Storage / IndexedDB
directly — they don't depend on an active SW controller — so stripping
``navigator.serviceWorker`` sidesteps the two documented e2e/SW gotchas
(docs/client-side-tests.md): a real SW intercepting fetches, and a real
SW's own Cache-Storage writes (shell cache population, BASEMAP_CACHE tile
writes as the map pans) polluting the assertions below.

The seeded cache is deliberately named outside the ``snowdesk-basemap-``
prefix ``window.pwaLayerSyncStatus`` looks for, so it can never
accidentally satisfy the basemap probe — ``window.pwaLayerSyncStatus``'s
GeoJSON probe uses the GLOBAL ``caches.match()``, which searches every
cache regardless of name, so seeding an arbitrarily-named throwaway cache
is enough to resolve the "resorts" row to "cached" without needing the
real shell cache or a live fetch.
"""

from __future__ import annotations

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map to boot.

    Same technique as test_map_overlay_cache_isolation.py's helper of the
    same name — duplicated here (rather than imported) so this module has
    no dependency on that file's internals.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function("() => typeof window.pwaLayerSyncStatus === 'object'")


def _navigate_home_map_loaded(page: Page, live_server_url: str) -> None:
    """Load / and wait for MAP to finish loading before interacting.

    The live-update test toggles a *lazy* overlay tier on, which triggers a
    real GeoJSON fetch + ``installOverlayLayers`` against the current style.
    That install throws if the style isn't ready yet, so the tier's load
    never reaches ``overlayLoaded[key] = true`` and ``markCached`` never
    fires. Waiting for ``MAP.loaded()`` first (same guard as
    test_overlay_basemap_persistence.py's ``_navigate_home``) removes that
    race — the basemap tile CDN is unreachable in the harness, so this
    resolves once the SNOW-483 inline fallback style has loaded.
    """
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    page.wait_for_function("() => typeof window.pwaLayerSyncStatus === 'object'")


def _dot_state(page: Page, key: str) -> str | None:
    return page.locator(f'[data-overlay-key="{key}"] .sync-dot').get_attribute(
        "data-sync-state"
    )


def test_seeded_row_resolves_cached_unseeded_row_stays_uncached(
    live_server: LiveServer, page: Page
) -> None:
    """A Cache-Storage-seeded row resolves "cached"; an unseeded one stays
    "uncached" after ``refresh()``.

    Seeds ``/api/resorts.geojson`` into a throwaway cache, then asserts the
    resorts row's dot resolves to "cached" while "l2" (Minor regions,
    off by default — never fetched by the happy path, so never cached)
    stays "uncached".
    """
    _navigate_home_with_sw_stripped(page, live_server.url)

    page.evaluate(
        """async () => {
            const cache = await caches.open('snowdesk-e2e-sync-status-throwaway');
            await cache.put(
                new Request('/api/resorts.geojson'),
                new Response('{}', { headers: { 'Content-Type': 'application/json' } }),
            );
        }"""
    )

    page.click("#basemap-toggle")
    page.locator('[data-overlay-key="resorts"]').wait_for(state="visible")

    page.evaluate("() => window.pwaLayerSyncStatus.refresh()")
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"resorts\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'cached'"
    )

    assert _dot_state(page, "resorts") == "cached"
    assert _dot_state(page, "l2") == "uncached"
    # l3 is never cacheable (network-only in sw.js) — its own hollow
    # "unavailable" state, distinct from the grey "uncached" fill.
    assert _dot_state(page, "l3") == "unavailable"


def test_toggling_a_tier_on_flips_its_dot_cached_live(
    live_server: LiveServer, page: Page
) -> None:
    """SNOW-505 iteration: toggling a lazy tier on flips its dot to "cached"
    in real time — no popover re-open needed — while l3 (network-only) keeps
    its hollow "unavailable" dot even after being toggled on.

    Opening the popover first re-probes (l2 starts "uncached"); toggling l2
    on triggers its GeoJSON load, and ``markCached`` optimistically greens
    the dot the moment that load resolves. l3's load succeeds too, but its
    dot must remain "unavailable" because bulletin groupings are never
    cached for offline use.
    """
    _navigate_home_map_loaded(page, live_server.url)

    page.click("#basemap-toggle")
    l2 = page.locator('[data-overlay-key="l2"]')
    l2.wait_for(state="visible")
    # At open, l2 has never been fetched, so its dot re-probes to uncached.
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"l2\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'uncached'"
    )

    # Toggle l2 on and wait for its GeoJSON load to resolve — that resolution
    # is what fires markCached. Wrapping the click in expect_response mirrors
    # test_overlay_basemap_persistence.py's proven toggle pattern.
    with page.expect_response(lambda r: "sub-regions" in r.url and ".geojson" in r.url):
        l2.click()
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"l2\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'cached'"
    )
    assert _dot_state(page, "l2") == "cached"

    # Toggling l3 on loads it too, but its dot must stay in the hollow
    # "unavailable" state — markCached no-ops for l3 (network-only, never
    # cached). This holds regardless of whether the bulletin-groupings fetch
    # itself succeeds, so it needn't be awaited; a brief settle is enough to
    # catch an erroneous flip.
    page.locator('[data-overlay-key="l3"]').click()
    page.wait_for_timeout(500)
    assert _dot_state(page, "l3") == "unavailable"
