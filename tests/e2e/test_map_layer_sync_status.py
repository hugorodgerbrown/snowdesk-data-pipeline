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
    dot = page.locator(f'[data-overlay-key="{key}"] .sync-dot')
    # Annotate the local so the Any-typed get_attribute() return is absorbed
    # here rather than tripping mypy's no-any-return on the bare return.
    state: str | None = dot.get_attribute("data-sync-state")
    return state


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


def test_toggling_a_tier_on_flips_its_dot_cached_live(
    live_server: LiveServer, page: Page
) -> None:
    """SNOW-505 iteration: toggling a lazy tier on flips its dot to "cached"
    in real time — no popover re-open needed.

    Opening the popover first re-probes (l2 starts "uncached"); toggling l2
    on triggers its GeoJSON load, and ``markCached`` optimistically greens
    the dot the moment that load resolves.
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


def test_boot_restore_greens_a_dot_without_a_toggle(
    live_server: LiveServer, page: Page
) -> None:
    """SNOW-518: a boot-time overlay restore greens its dot with no toggle.

    Seeds ``localStorage['snowdesk.map.overlay.l2'] = 'true'`` (via
    ``add_init_script``, so it lands before map.js's boot-time overlay
    restore reads ``overlayState`` from localStorage) so l2 restores at
    boot. The assertion targets the optimistic ``markCached`` flip, not a
    probe: the restore's own ``ensureOverlayLoaded('l2')`` fetch hits the
    live server directly for a fresh entry. The ``/api/sub-regions.geojson``
    throwaway-cache seed is defensive — if the new ``visibilitychange``
    handler's ``refresh()`` happens to run during the test, its
    (``ignoreSearch``) probe finds this entry and so can't flip the dot back
    to "uncached" before the assertion lands. Pre-change, the l2 dot stays
    "unknown" until the popover is first opened — only ``refresh()`` on open
    paints it. Post this ticket's change, the boot-restore path's
    ``restoreOverlay`` helper calls ``markCached('l2')`` the moment its
    ``ensureOverlayLoaded`` call resolves, greening the dot with no popover
    open and no toggle click.
    """
    page.add_init_script(
        """
        localStorage.setItem('snowdesk.map.overlay.l2', 'true');
        (async () => {
            const cache = await caches.open('snowdesk-e2e-boot-restore-throwaway');
            await cache.put(
                new Request('/api/sub-regions.geojson'),
                new Response('{}', { headers: { 'Content-Type': 'application/json' } }),
            );
        })();
        """
    )

    _navigate_home_map_loaded(page, live_server.url)

    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"l2\"] .sync-dot')"
        "?.getAttribute('data-sync-state') === 'cached'"
    )
    assert _dot_state(page, "l2") == "cached"


def test_visibilitychange_reprobes_and_greens_a_dot_without_the_popover(
    live_server: LiveServer, page: Page
) -> None:
    """SNOW-518: returning to the tab re-probes and greens a dot with the
    popover closed.

    Loads normally (resorts starts "uncached" — never fetched), seeds
    ``/api/resorts.geojson`` into a throwaway Cache-Storage cache out of
    band (mimicking a feed warmed while the tab was backgrounded), then
    dispatches ``visibilitychange`` — the Playwright page is already
    "visible", so the handler's ``document.visibilityState === 'visible'``
    guard passes and it calls ``window.pwaLayerSyncStatus.refresh()``,
    which re-probes real cache state and greens the resorts dot with no
    popover open.
    """
    _navigate_home_with_sw_stripped(page, live_server.url)

    assert _dot_state(page, "resorts") == "unknown"

    page.evaluate(
        """async () => {
            const cache = await caches.open('snowdesk-e2e-visibilitychange-throwaway');
            await cache.put(
                new Request('/api/resorts.geojson'),
                new Response('{}', { headers: { 'Content-Type': 'application/json' } }),
            );
        }"""
    )

    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")

    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"resorts\"] .sync-dot')"
        "?.getAttribute('data-sync-state') === 'cached'"
    )
    assert _dot_state(page, "resorts") == "cached"
