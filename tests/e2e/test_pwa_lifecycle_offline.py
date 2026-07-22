"""
tests/e2e/test_pwa_lifecycle_offline.py — SNOW-389 real-SW offline journeys.

Covers Scenarios P7, P8, P9 (docs/testing-scenarios.md §"PWA Shell"): an
offline reload of a cached ``/?d=YYYY-MM-DD`` URL (including the SNOW-347
regression guard for a URL never fetched with that exact query string),
the persistent offline banner + ``data-network-required`` disabling, and
the branded offline fallback for a page never visited at all. Also covers
the SNOW-482 dual sync/freshness clock: a real (un-stamped) response
advances the persisted "Synced" clock and appends a ``log:sync`` row; a
Cache-Storage replay (``X-SW-Cache: hit``) does not. SNOW-490 adds the
regression guard for the bug that motivated the tightened qualifier: an
offline reload used to reset ``sync.last_at`` to "now" the moment the
page issued ANY un-stamped same-origin fetch, including one that missed
the SW's cache and fell back to a synthesized 504 with no real network
round-trip behind it.
"""

from __future__ import annotations

import re
import uuid

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import PwaPage

# ---------------------------------------------------------------------------
# P7 — offline reload of a cached page, including /?d= variants
# ---------------------------------------------------------------------------


def test_offline_reload_of_visited_date_url(pwa_page: PwaPage) -> None:
    """A /?d= URL fetched online is served from its own exact cache entry offline.

    Navigating to the dated URL directly (rather than driving the season
    scrubber, which map.js's own commitDate() does via history.replaceState
    with no fetch — see test_offline_reload_of_never_visited_date_url below)
    means the SW's network-first navigate strategy caches this exact URL,
    including its query string.
    """
    page = pwa_page.page
    dated_url = pwa_page.live_server_url + "/?d=2026-04-08"
    page.goto(dated_url)
    page.wait_for_load_state("load")
    page.wait_for_selector("#season-scrubber")

    page.context.set_offline(True)
    try:
        page.wait_for_selector("#pwa-offline-banner:not(.hidden)", timeout=5000)
        page.reload()
        page.wait_for_load_state("load")
    finally:
        page.context.set_offline(False)

    assert page.url == dated_url
    page.wait_for_selector("#season-scrubber")
    assert page.locator("h1", has_text="This page isn't available offline").count() == 0


def test_offline_reload_of_never_visited_date_url(pwa_page: PwaPage) -> None:
    """SNOW-347 regression guard: a /?d= URL never fetched still serves the shell.

    The address bar is moved client-side via history.replaceState — the
    same mechanism map.js's commitDate() uses when scrubbing the season
    timeline (no fetch is issued for the intermediate dates) — so this
    exact URL was genuinely never requested from the server, and the only
    way an offline reload can succeed is the SW's ``ignoreSearch: true``
    cache-match fallback in ``_networkFirst``.
    """
    page = pwa_page.page
    never_visited_url = pwa_page.live_server_url + "/?d=2026-03-01"
    page.evaluate("(url) => history.replaceState(null, '', url)", never_visited_url)
    assert page.url == never_visited_url

    page.context.set_offline(True)
    try:
        page.reload()
        page.wait_for_load_state("load")
    finally:
        page.context.set_offline(False)

    assert page.url == never_visited_url
    page.wait_for_selector("#season-scrubber")
    assert page.locator("h1", has_text="This page isn't available offline").count() == 0


# ---------------------------------------------------------------------------
# P8 — offline banner, freshness, and network-required controls
# ---------------------------------------------------------------------------


def test_offline_banner_and_network_required_controls(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """The offline banner and data-network-required controls track connectivity.

    Uses the canonical bulletin page (pre-seeded by ``_load_test_data``)
    because it carries the subscribe form's ``data-network-required``
    attribute (``accounts/templates/accounts/partials/subscribe_form.html``).
    """
    page = pwa_page.page
    page.goto(pwa_page.live_server_url + "/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("load")

    assert "hidden" in (
        page.eval_on_selector("#pwa-offline-banner", "(el) => el.className") or ""
    )

    page.context.set_offline(True)
    try:
        page.wait_for_selector("#pwa-offline-banner:not(.hidden)", timeout=5000)

        offline_state = page.evaluate(
            """() => {
                const form = document.querySelector('form[data-network-required]');
                const button = form.querySelector('button');
                return {
                  formAriaDisabled: form.getAttribute('aria-disabled'),
                  buttonDisabled: button.disabled,
                };
              }"""
        )
        assert offline_state == {"formAriaDisabled": "true", "buttonDisabled": True}
    finally:
        page.context.set_offline(False)

    page.wait_for_function(
        "() => document.getElementById('pwa-offline-banner').classList.contains('hidden')",
        timeout=5000,
    )
    online_state = page.evaluate(
        """() => {
            const form = document.querySelector('form[data-network-required]');
            const button = form.querySelector('button');
            return {
              formAriaDisabled: form.hasAttribute('aria-disabled'),
              buttonDisabled: button.disabled,
            };
          }"""
    )
    assert online_state == {"formAriaDisabled": False, "buttonDisabled": False}

    # SW invariant: toggling connectivity never touched the registration.
    registration_count = page.evaluate(
        "async () => (await navigator.serviceWorker.getRegistrations()).length"
    )
    assert registration_count == 1


def test_sync_clock_advances_on_real_response_not_on_cache_hit(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """SNOW-482: the persisted "Synced" clock only advances on a real round-trip.

    ``/api/regions.geojson`` is one of ``sw.js``'s stale-while-revalidate
    ``STATIC_PATHS`` — a real first fetch populates the SW cache; an
    immediate second fetch of the SAME URL is served from that cache with
    ``X-SW-Cache: hit``. Requires ``?country=ch`` (the endpoint 400s
    without it, and ``_staleWhileRevalidate`` only caches ``response.ok``
    responses); a cache-busting query param keeps this test's URL
    distinct from anything ``pwa_page``'s earlier navigations may have
    already warmed.
    """
    page = pwa_page.page
    page.goto(pwa_page.live_server_url + "/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("load")

    cache_bust = uuid.uuid4().hex
    geojson_url = f"/api/regions.geojson?country=ch&e2e={cache_bust}"

    # First fetch: real network round-trip, no cache entry yet — advances
    # the sync clock and appends a log:sync row. sw.js's
    # stale-while-revalidate strategy writes to Cache Storage as a
    # fire-and-forget promise (not awaited before the response is
    # returned), so this polls for the cache entry to land before
    # returning — otherwise the second fetch below can race it and miss
    # the cache too.
    first = page.evaluate(
        """async (url) => {
            const res = await fetch(url);
            await res.arrayBuffer();
            for (let i = 0; i < 50; i++) {
              if (await caches.match(url)) break;
              await new Promise((resolve) => setTimeout(resolve, 50));
            }
            return {
              cacheHit: res.headers.get('X-SW-Cache'),
              syncedAt: (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value,
              logCount: await window.pwaDb.count('log:sync'),
            };
          }""",
        geojson_url,
    )
    assert first["cacheHit"] is None
    assert first["syncedAt"]
    assert first["logCount"] >= 1

    # Second fetch of the exact same URL is served from the SW's
    # stale-while-revalidate cache — X-SW-Cache: hit — and must NOT
    # advance the sync clock or append another log:sync row.
    second = page.evaluate(
        """async (url) => {
            const before = (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value;
            const beforeCount = await window.pwaDb.count('log:sync');
            const res = await fetch(url);
            await res.arrayBuffer();
            return {
              cacheHit: res.headers.get('X-SW-Cache'),
              before,
              after: (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value,
              beforeCount,
              afterCount: await window.pwaDb.count('log:sync'),
            };
          }""",
        geojson_url,
    )
    assert second["cacheHit"] == "hit"
    assert second["after"] == second["before"]
    assert second["afterCount"] == second["beforeCount"]

    # A further real (un-stamped) request to a different qualifying,
    # never-cached URL advances the clock again.
    third = page.evaluate(
        """async () => {
            const before = (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value;
            const res = await fetch('/api/version');
            await res.text();
            return {
              cacheHit: res.headers.get('X-SW-Cache'),
              before,
              after: (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value,
            };
          }"""
    )
    assert third["cacheHit"] is None
    assert third["after"] != third["before"]

    # The offline banner reads the persisted clock — going offline fills the
    # "Offline — last synced …" summary from the value the fetches above
    # already wrote, with no further network activity required. The clock
    # resolves to a relative phrase (Intl.RelativeTimeFormat, e.g. "now" or
    # "5 seconds ago"), never the em-dash placeholder.
    page.context.set_offline(True)
    try:
        page.wait_for_selector("#pwa-offline-banner:not(.hidden)", timeout=5000)
        synced_text = page.eval_on_selector(
            '#pwa-offline-banner [data-role="synced-at"]', "(el) => el.textContent"
        )
    finally:
        page.context.set_offline(False)
    assert synced_text is not None
    assert synced_text.strip() not in ("", "—")
    assert re.search(r"\bago\b|\bnow\b", synced_text.strip())


def test_last_synced_phrase_auto_updates_while_offline(
    live_server: LiveServer, page: Page, _load_test_data: None
) -> None:
    """SNOW-482: the "last synced" phrase re-renders on a timer while shown.

    With the page clock frozen, seed the sync clock 30 s in the past and
    go offline (which reveals the banner and starts its re-render ticker).
    Fast-forwarding two minutes must advance the phrase from a
    seconds-granularity read to a minutes one WITHOUT any user
    interaction — proving the timer, not just the initial render, updates
    the value.

    The real service worker is stripped (as in test_pwa_db.py) so its
    cache replays can't perturb the ledger. Stripping the SW is NOT enough
    on its own, though. ``pwa_offline.js``'s ``absorbFreshness`` advances
    ``syncLastAt`` (and re-renders the banner) on every settled,
    successful (2xx), non-synthesized same-origin, non-cache response
    (SNOW-490 tightened this from "every non-cache response" to also
    require a 2xx status and a non-empty resolved URL) — and the home
    page keeps such traffic flowing under a faked clock:
    ``page.clock.fast_forward`` fires maplibre-gl's internal
    timers, which rebuild the map style and re-fetch ``/api/regions.geojson``
    / ``/api/ratings`` at the just-advanced clock time, plus telemetry.js's
    faked flush interval. Any of these settling mid-fast-forward stamps
    ``syncLastAt`` to the advanced "now", flipping the phrase back off
    "2 minutes ago" and racing the assertion below — the source of the
    SNOW-482 serial-suite flake (it's load-sensitive, so it only surfaced
    under the full serial run).

    This test needs no network at all: the banner is revealed by the
    ``offline`` event, the phrase is seeded into IndexedDB, and the ticker
    is a pure timer. So every ``fetch`` is stubbed to a promise that never
    settles — nothing reaches ``absorbFreshness``, and the freshness ticker
    is left as the ONLY thing that can re-render the banner, which is exactly
    what this test means to isolate. Document navigation, ``<script>`` /
    ``<link>`` asset loads and IndexedDB are unaffected (none go through
    ``window.fetch``).
    """
    page.clock.install()
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    # Neutralise every fetch (see docstring): a never-settling promise, not
    # a stub response — even a resolved or rejected one would re-render the
    # banner via absorbFreshness / the fetch-failure path and mask the ticker.
    page.add_init_script("window.fetch = () => new Promise(() => {});")
    page.goto(live_server.url)
    page.wait_for_function("() => typeof window.pwaDb === 'object'")

    # Seed the sync clock 30 s before the frozen "now", then reload so
    # pwa_offline.js hydrates it from meta:app on init.
    page.evaluate(
        """() => window.pwaDb.put('meta:app', {
            key: 'sync.last_at',
            value: new Date(Date.now() - 30000).toISOString(),
        })"""
    )
    page.reload()
    page.wait_for_function("() => typeof window.pwaDb === 'object'")

    selector = '#pwa-offline-banner [data-role="synced-at"]'
    page.context.set_offline(True)
    try:
        page.wait_for_selector("#pwa-offline-banner:not(.hidden)", timeout=5000)
        first = page.eval_on_selector(selector, "(el) => el.textContent")
        # 30 s in the past → a seconds-granularity phrase, not minutes.
        assert first is not None and "minute" not in first

        # Advance two minutes; the 30 s ticker fires and re-renders the
        # phrase against the advanced clock.
        page.clock.fast_forward("02:00")
        page.wait_for_function(
            """(sel) => {
                const el = document.querySelector(sel);
                return el && /minute/.test(el.textContent);
            }""",
            arg=selector,
            timeout=5000,
        )
        second = page.eval_on_selector(selector, "(el) => el.textContent")
    finally:
        page.context.set_offline(False)
    assert second is not None and "minute" in second
    assert second != first


def test_offline_reload_preserves_sync_clock(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """SNOW-490 regression guard: an offline reload must not reset ``sync.last_at``.

    Before this fix, ``absorbFreshness()`` advanced the sync clock for any
    un-stamped, same-origin response — and ``static/js/sw.js``'s
    synthesized 504 fallback for an offline cache miss was both un-stamped
    and had ``url === ''``, which resolves against ``location.href`` and
    is misclassified as a same-origin success. Every offline cache miss
    (an unfetched ``/api/ratings/`` variant here) therefore silently reset
    the "last synced" clock to "now" and appended a bogus ``log:sync`` row.

    Warms the page online first with one real qualifying fetch so
    ``sync.last_at`` already has a value to protect, then goes offline,
    reloads, and issues a never-cached ``/api/ratings/`` request — which
    misses the SW's stale-while-revalidate cache and, offline, falls
    through to the synthesized 504.
    """
    page = pwa_page.page
    page.goto(pwa_page.live_server_url + "/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("load")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")

    # One real, qualifying fetch online — establishes a sync.last_at value
    # and a log:sync row to protect against the offline reload below.
    warm = page.evaluate(
        """async () => {
            const res = await fetch('/api/version');
            await res.text();
            return {
              status: res.status,
              syncedAt: (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value,
              logCount: await window.pwaDb.count('log:sync'),
            };
          }"""
    )
    assert warm["status"] == 200
    assert warm["syncedAt"]
    recorded_synced_at = warm["syncedAt"]
    recorded_log_count = warm["logCount"]

    page.context.set_offline(True)
    try:
        page.reload()
        page.wait_for_load_state("load")
        page.wait_for_selector("#pwa-offline-banner:not(.hidden)", timeout=5000)

        cache_bust = uuid.uuid4().hex
        never_cached_url = f"/api/ratings/?d=2026-02-02&country=ch&e2e={cache_bust}"
        miss = page.evaluate(
            """async (url) => {
                const res = await fetch(url);
                return {
                  status: res.status,
                  cacheHeader: res.headers.get('X-SW-Cache'),
                  syncedAt: (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value,
                  logCount: await window.pwaDb.count('log:sync'),
                };
              }""",
            never_cached_url,
        )
        synced_text = page.eval_on_selector(
            '#pwa-offline-banner [data-role="synced-at"]', "(el) => el.textContent"
        )
    finally:
        page.context.set_offline(False)

    assert miss["status"] == 504
    assert miss["cacheHeader"] == "miss"
    assert miss["syncedAt"] == recorded_synced_at
    assert miss["logCount"] == recorded_log_count
    assert synced_text is not None
    assert synced_text.strip() not in ("", "—")


def test_online_5xx_does_not_advance_sync_clock(pwa_page: PwaPage) -> None:
    """SNOW-490: a real network round-trip that returns a 5xx must not sync.

    Only a *successful* (2xx) response advances ``sync.last_at``. Routes
    ``/api/version`` to a stubbed 500 — the SW does not ``respondWith`` for
    a network-classified (non-``STATIC_PATHS``) same-origin URL, so
    Playwright's route layer still sees and can intercept the request.
    """
    page = pwa_page.page
    page.route(
        "**/api/version",
        lambda route: route.fulfill(status=500, body="boom"),
    )
    try:
        result = page.evaluate(
            """async () => {
                const before = (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value;
                const res = await fetch('/api/version');
                await res.text();
                return {
                  status: res.status,
                  before,
                  after: (await window.pwaDb.get('meta:app', 'sync.last_at'))?.value,
                };
              }"""
        )
    finally:
        page.unroute("**/api/version")

    assert result["status"] == 500
    assert result["after"] == result["before"]


# ---------------------------------------------------------------------------
# P9 — offline navigation to a URL never visited
# ---------------------------------------------------------------------------


def test_offline_navigation_to_never_visited_url_shows_offline_fallback(
    pwa_page: PwaPage,
) -> None:
    """A never-visited URL, requested while offline, shows the branded fallback.

    Both the network (offline) and the per-page cache (never fetched) miss,
    so ``_networkFirst`` falls back to the pre-cached ``/static/offline.html``
    — installed alongside the shell on first activation, so it's always
    available the moment the network drops.
    """
    page = pwa_page.page

    page.context.set_offline(True)
    try:
        page.goto(pwa_page.live_server_url + "/some-page-never-visited/")
        page.wait_for_load_state("load")
    finally:
        page.context.set_offline(False)

    assert page.locator("h1", has_text="This page isn't available offline").count() == 1
    assert page.locator("button", has_text="Retry").count() == 1
