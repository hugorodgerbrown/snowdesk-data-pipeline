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
    # l3 is never cacheable (network-only in sw.js) — always uncached too.
    assert _dot_state(page, "l3") == "uncached"
