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
    # SNOW-524: the layers menu disables an uncached row while offline, and
    # the picker's click handler honours that. Headless Chromium in this
    # harness has no route to the internet and can report
    # ``navigator.onLine === false``, which would silently make every row
    # inert and any toggle below a no-op. Pin it true so the test controls
    # connectivity rather than inheriting the runner's.
    page.add_init_script(
        "Object.defineProperty(navigator, 'onLine', "
        "{ value: true, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )
    page.wait_for_function("() => typeof window.pwaLayerSyncStatus === 'object'")
    # SNOW-524: boot's country load fetches L1/L2/L4 + ratings and greens
    # their dots when it resolves. Settling on network idle keeps those
    # optimistic writes from landing in the middle of an assertion below.
    page.wait_for_load_state("networkidle")


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


def test_boot_country_load_greens_the_country_scoped_tier_dots(
    live_server: LiveServer, page: Page
) -> None:
    """SNOW-524: booting greens l1/l2/l4 without the user touching a tier.

    Replaces the older "toggle l2 on and watch its dot flip" case, whose
    premise no longer holds. Boot used to mark Switzerland loaded off its
    critical-path fetches alone and skip it in the country restore loop, so
    L1/L2 were never fetched and l2 reliably started "uncached". Boot now runs
    the same country load for CH as for any country the user switches on,
    which fetches all three tiers — so with a real service worker caching
    them, l2 is legitimately cached before the popover is ever opened.

    That is the behaviour worth asserting: enabling a country populates every
    tier's offline data, not just the one tier being displayed. The live
    toggle→green flip is still covered for countries by
    ``test_country_row_shows_a_pending_state_before_it_greens`` and for a
    boot-restored tier by ``test_boot_restore_greens_a_dot_without_a_toggle``.
    """
    _navigate_home_map_loaded(page, live_server.url)

    page.click("#basemap-toggle")
    page.locator('[data-overlay-key="l2"]').wait_for(state="visible")

    for key in ("l1", "l2", "l4"):
        page.wait_for_function(
            '(k) => document.querySelector(`[data-overlay-key="${k}"] .sync-dot`)'
            ".getAttribute('data-sync-state') === 'cached'",
            arg=key,
        )
        assert _dot_state(page, key) == "cached"


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


# ---------------------------------------------------------------------------
# SNOW-524: per-country dots, offline gating, and the pending state
# ---------------------------------------------------------------------------
#
# The unit suite (tests/js/test_map_layer_sync_status.js) covers the probe
# logic against a fake CacheStorage. What needs a real browser is the wiring:
# that the country rows the template renders actually carry dots, that the
# probe distinguishes one country's cached feeds from another's (the bug —
# an ``ignoreSearch`` probe reported Austria cached off Switzerland's entry),
# and that the offline gate really disables the row a user would otherwise
# click into four failing fetches.

_COUNTRY_FEEDS = (
    "/api/major-regions.geojson",
    "/api/sub-regions.geojson",
    "/api/regions.geojson",
    "/api/ratings/",
)


def _seed_country_feeds(page: Page, code: str) -> None:
    """Seed every ``?country=<code>``-scoped feed into a throwaway cache.

    Mirrors the seeding idiom above: the cache name sits outside the
    ``snowdesk-basemap-`` prefix, and the module's probe uses the GLOBAL
    ``caches.match()``, so an arbitrarily-named cache resolves the row.
    """
    page.evaluate(
        """async ({ paths, code }) => {
            const cache = await caches.open('snowdesk-e2e-country-throwaway');
            for (const path of paths) {
                await cache.put(
                    new Request(`${path}?country=${code}`),
                    new Response('{}', {
                        headers: { 'Content-Type': 'application/json' },
                    }),
                );
            }
        }""",
        {"paths": list(_COUNTRY_FEEDS), "code": code},
    )


def _set_offline(page: Page, offline: bool) -> None:
    """Flip ``navigator.onLine`` and broadcast the app's connectivity event.

    ``pwa_offline.js`` owns that event in production; driving it directly
    keeps the test to the sync-status module's own contract rather than
    depending on the offline banner's timing.
    """
    page.evaluate(
        """(offline) => {
            Object.defineProperty(navigator, 'onLine', {
                value: !offline, configurable: true,
            });
            document.dispatchEvent(
                new CustomEvent('snowdesk:connectivity-changed'),
            );
        }""",
        offline,
    )


def test_country_dot_distinguishes_one_country_from_another(
    live_server: LiveServer, page: Page
) -> None:
    """A country's dot reflects ITS OWN feeds, not another country's.

    The regression: the probe used ``ignoreSearch: true`` while the service
    worker caches per full URL including ``?country=``, so a cached
    Switzerland painted every other country cached too.
    """
    _navigate_home_with_sw_stripped(page, live_server.url)

    _seed_country_feeds(page, "ch")

    page.click("#basemap-toggle")
    page.locator('[data-overlay-key="country.ch"]').wait_for(state="visible")
    page.evaluate("() => window.pwaLayerSyncStatus.refresh()")

    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"country.ch\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'cached'"
    )

    assert _dot_state(page, "country.ch") == "cached"
    # Italy shares three of the four feed PATHS with Switzerland and differs
    # only by query string — exactly what the old probe could not tell apart.
    assert _dot_state(page, "country.it") == "uncached"


def test_offline_disables_an_uncached_country_row(
    live_server: LiveServer, page: Page
) -> None:
    """Offline, an uncached country is red AND its row is disabled.

    A cached country stays available and interactive, so going offline never
    strands the user on a map they cannot use.
    """
    _navigate_home_with_sw_stripped(page, live_server.url)

    _seed_country_feeds(page, "ch")

    page.click("#basemap-toggle")
    page.locator('[data-overlay-key="country.it"]').wait_for(state="visible")
    _set_offline(page, True)

    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"country.it\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'unavailable-offline'"
    )

    italy = page.locator('[data-overlay-key="country.it"]')
    assert italy.get_attribute("aria-disabled") == "true"

    switzerland = page.locator('[data-overlay-key="country.ch"]')
    assert _dot_state(page, "country.ch") == "cached"
    assert switzerland.get_attribute("aria-disabled") is None

    # Back online, the uncached row returns to the grey advisory state.
    _set_offline(page, False)
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"country.it\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'uncached'"
    )
    assert italy.get_attribute("aria-disabled") is None


def test_country_row_shows_a_pending_state_before_it_greens(
    live_server: LiveServer, page: Page
) -> None:
    """``markSyncing`` paints the pulsing pending state; ``markCached`` greens
    it once the data has landed.

    Driven through the module's own API rather than a live country toggle:
    the basemap CDN is unreachable in the harness, so a real toggle's fetches
    are not a dependable clock. What matters here is that the state the map's
    load lifecycle drives is rendered by a real browser against the real
    stylesheet — including that the row is never disabled mid-fetch.
    """
    _navigate_home_with_sw_stripped(page, live_server.url)

    page.click("#basemap-toggle")
    page.locator('[data-overlay-key="country.fr"]').wait_for(state="visible")

    page.evaluate("() => window.pwaLayerSyncStatus.markSyncing('country.fr')")
    assert _dot_state(page, "country.fr") == "syncing"
    assert (
        page.locator('[data-overlay-key="country.fr"]').get_attribute("aria-disabled")
        is None
    )

    page.evaluate("() => window.pwaLayerSyncStatus.markCached('country.fr')")
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"country.fr\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'cached'"
    )
