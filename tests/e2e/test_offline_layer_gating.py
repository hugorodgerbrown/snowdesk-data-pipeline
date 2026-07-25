"""
tests/e2e/test_offline_layer_gating.py — Playwright regression tests for the
offline-integrity rework of the map layers menu.

Two behaviours are covered:

1. Offline gating (map_layer_sync_status.js + basemapPickerInit in map.js) —
   while ``navigator.onLine === false``, a layer/basemap that isn't cached
   can't be loaded, so its menu row gets the red ``unavailable-offline`` dot
   AND is disabled (``aria-disabled``); its click is inert. A cached row
   stays green and interactive, and the active basemap is never disabled
   (you can't be stranded on a map you can't leave). Coming back online
   re-enables every row this module disabled.

2. Micro-region (L4) recovery (map.js) — if a style swap dropped the
   ``regions`` layers, toggling Micro regions back on must rebuild them
   rather than silently no-op (the reported "toggling micro-regions does
   nothing" bug). The toggle dispatches ``snowdesk:regions-reinstall``,
   which re-installs from the in-memory GeoJSON cache.

Uses the SW-stripped navigation pattern (mirrors
test_map_layer_sync_status.py): ``window.pwaLayerSyncStatus``'s probes read
Cache Storage / IndexedDB directly and don't need an active SW controller,
so stripping ``navigator.serviceWorker`` sidesteps the documented e2e/SW
gotchas. ``navigator.onLine`` is overridden in-page to simulate offline —
the module reads it live on every ``refresh()``.
"""

from __future__ import annotations

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home_map_loaded(page: Page, live_server_url: str) -> None:
    """Load / with the SW stripped and wait for MAP + the sync module to boot."""
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    page.wait_for_function("() => typeof window.pwaLayerSyncStatus === 'object'")


def _set_online(page: Page, online: bool) -> None:
    """Override ``navigator.onLine`` in the page (the module reads it live)."""
    page.evaluate(
        "(online) => Object.defineProperty(navigator, 'onLine', "
        "{ value: online, configurable: true })",
        online,
    )


def _seed_cache(page: Page, path: str) -> None:
    """Seed a same-origin path into a throwaway Cache-Storage cache.

    The GeoJSON probe uses the GLOBAL ``caches.match()`` (searches every
    cache), so an arbitrarily-named cache is enough to resolve a row to
    "cached" without a live fetch or the real shell cache.
    """
    page.evaluate(
        """async (path) => {
            const cache = await caches.open('snowdesk-e2e-offline-gating-throwaway');
            await cache.put(
                new Request(path),
                new Response('{}', { headers: { 'Content-Type': 'application/json' } }),
            );
        }""",
        path,
    )


def _overlay_dot_state(page: Page, key: str) -> str | None:
    state: str | None = page.locator(
        f'[data-overlay-key="{key}"] .sync-dot'
    ).get_attribute("data-sync-state")
    return state


def _overlay_disabled(page: Page, key: str) -> str | None:
    disabled: str | None = page.locator(f'[data-overlay-key="{key}"]').get_attribute(
        "aria-disabled"
    )
    return disabled


def test_offline_uncached_overlay_row_is_red_and_disabled(
    live_server: LiveServer, page: Page
) -> None:
    """Offline + uncached overlay → red ``unavailable-offline`` dot, row disabled,
    and clicking it is inert (aria-checked unchanged).
    """
    _navigate_home_map_loaded(page, live_server.url)
    _set_online(page, False)

    page.click("#basemap-toggle")
    l2 = page.locator('[data-overlay-key="l2"]')
    l2.wait_for(state="visible")
    page.evaluate("() => window.pwaLayerSyncStatus.refresh()")

    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"l2\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'unavailable-offline'"
    )
    assert _overlay_dot_state(page, "l2") == "unavailable-offline"
    assert _overlay_disabled(page, "l2") == "true"

    # Click is inert — the picker's aria-disabled guard returns before any
    # toggle. force=True bypasses Playwright's actionability wait (it treats
    # aria-disabled="true" as not-actionable and would otherwise time out
    # rather than dispatch), so the real click handler runs and we can prove
    # it no-ops: aria-checked is unchanged.
    before = l2.get_attribute("aria-checked")
    l2.click(force=True)
    assert l2.get_attribute("aria-checked") == before


def test_offline_cached_overlay_row_stays_green_and_enabled(
    live_server: LiveServer, page: Page
) -> None:
    """Offline but cached (regions.geojson seeded) → green dot, row enabled."""
    _navigate_home_map_loaded(page, live_server.url)
    _seed_cache(page, "/api/regions.geojson")
    _set_online(page, False)

    page.click("#basemap-toggle")
    page.locator('[data-overlay-key="l4"]').wait_for(state="visible")
    page.evaluate("() => window.pwaLayerSyncStatus.refresh()")

    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"l4\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'cached'"
    )
    assert _overlay_dot_state(page, "l4") == "cached"
    assert _overlay_disabled(page, "l4") in (None, "false")


def test_back_online_reenables_a_disabled_row(
    live_server: LiveServer, page: Page
) -> None:
    """A row disabled while offline is re-enabled when connectivity returns."""
    _navigate_home_map_loaded(page, live_server.url)
    _set_online(page, False)

    page.click("#basemap-toggle")
    page.locator('[data-overlay-key="l2"]').wait_for(state="visible")
    page.evaluate("() => window.pwaLayerSyncStatus.refresh()")
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"l2\"]')"
        ".getAttribute('aria-disabled') === 'true'"
    )

    _set_online(page, True)
    page.evaluate("() => window.pwaLayerSyncStatus.refresh()")
    page.wait_for_function(
        "() => !document.querySelector('[data-overlay-key=\"l2\"]')"
        ".hasAttribute('aria-disabled')"
    )
    assert _overlay_disabled(page, "l2") is None
    assert _overlay_dot_state(page, "l2") == "uncached"


def test_offline_inactive_basemap_disabled_active_stays_selectable(
    live_server: LiveServer, page: Page
) -> None:
    """Offline: an uncached, non-active basemap is red + disabled; the active
    basemap stays available (green, enabled).
    """
    _navigate_home_map_loaded(page, live_server.url)
    _set_online(page, False)

    page.click("#basemap-toggle")
    page.locator("#basemap-menu .basemap-menu-item[data-basemap-url]").first.wait_for(
        state="visible"
    )
    page.evaluate("() => window.pwaLayerSyncStatus.refresh()")

    # The active basemap (aria-checked="true") must never be disabled.
    page.wait_for_function(
        "() => { const a = document.querySelector("
        "'#basemap-menu .basemap-menu-item[data-basemap-url][aria-checked=\"true\"]');"
        " return a && a.querySelector('.sync-dot').getAttribute('data-sync-state') === 'cached'"
        " && !a.hasAttribute('aria-disabled'); }"
    )
    # An inactive, uncached basemap is red + disabled.
    page.wait_for_function(
        "() => { const items = Array.from(document.querySelectorAll("
        "'#basemap-menu .basemap-menu-item[data-basemap-url]'));"
        " const other = items.find(i => i.getAttribute('aria-checked') !== 'true');"
        " return other && other.getAttribute('aria-disabled') === 'true'"
        " && other.querySelector('.sync-dot').getAttribute('data-sync-state') === 'unavailable-offline'; }"
    )


def test_micro_region_toggle_recovers_dropped_layers(
    live_server: LiveServer, page: Page
) -> None:
    """Toggling Micro regions back on rebuilds the regions layers if a prior
    style swap dropped them — the reported "toggling does nothing" bug.

    Simulates the dropped-layers state by removing the regions source + layers
    directly, then drives the L4 toggle off→on. The on-toggle dispatches
    ``snowdesk:regions-reinstall``, which re-installs from the in-memory cache.
    """
    _navigate_home_map_loaded(page, live_server.url)

    # Regions layers are installed at boot.
    page.wait_for_function("() => !!MAP.getLayer('regions-fill')")

    # Drop them, mimicking a setStyle that wiped the overlay.
    page.evaluate(
        """() => {
            for (const id of ['regions-fill', 'regions-line',
                              'regions-line-selected', 'regions-label']) {
                if (MAP.getLayer(id)) MAP.removeLayer(id);
            }
            if (MAP.getSource('regions')) MAP.removeSource('regions');
        }"""
    )
    assert page.evaluate("() => !!MAP.getLayer('regions-fill')") is False

    page.click("#basemap-toggle")
    l4 = page.locator('[data-overlay-key="l4"]')
    l4.wait_for(state="visible")
    # L4 starts checked (default-on): first click toggles it off, second on —
    # the on-toggle is what triggers the reinstall recovery.
    l4.click()
    l4.click()

    page.wait_for_function("() => !!MAP.getLayer('regions-fill')")
    assert (
        page.evaluate("() => MAP.getLayoutProperty('regions-fill', 'visibility')")
        == "visible"
    )
