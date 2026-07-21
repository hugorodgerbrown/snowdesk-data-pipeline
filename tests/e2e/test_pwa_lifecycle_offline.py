"""
tests/e2e/test_pwa_lifecycle_offline.py — SNOW-389 real-SW offline journeys.

Covers Scenarios P7, P8, P9 (docs/testing-scenarios.md §"PWA Shell"): an
offline reload of a cached ``/?d=YYYY-MM-DD`` URL (including the SNOW-347
regression guard for a URL never fetched with that exact query string),
the persistent offline banner + ``data-network-required`` disabling, and
the branded offline fallback for a page never visited at all. Also covers
the SNOW-482 dual sync/freshness clock: a real (un-stamped) response
advances the persisted "Synced" clock and appends a ``log:sync`` row; a
Cache-Storage replay (``X-SW-Cache: hit``) does not.
"""

from __future__ import annotations

import uuid

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
    assert page.locator("h1", has_text="You're offline").count() == 0


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
    assert page.locator("h1", has_text="You're offline").count() == 0


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
    ``X-SW-Cache: hit``. A cache-busting query string keeps this test's
    URL distinct from anything ``pwa_page``'s earlier navigations may have
    already warmed.
    """
    page = pwa_page.page
    page.goto(pwa_page.live_server_url + "/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("load")

    cache_bust = uuid.uuid4().hex
    geojson_url = f"/api/regions.geojson?e2e={cache_bust}"

    # First fetch: real network round-trip, no cache entry yet — advances
    # the sync clock and appends a log:sync row.
    first = page.evaluate(
        """async (url) => {
            const res = await fetch(url);
            await res.arrayBuffer();
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

    # The offline banner reads the persisted clock — going offline shows
    # "Synced …" from the value the fetches above already wrote, with no
    # further network activity required.
    page.context.set_offline(True)
    try:
        page.wait_for_selector("#pwa-offline-banner:not(.hidden)", timeout=5000)
        freshness_text = page.eval_on_selector(
            '[data-role="offline-freshness"]', "(el) => el.textContent"
        )
    finally:
        page.context.set_offline(False)
    assert freshness_text is not None and "Synced" in freshness_text


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

    assert page.locator("h1", has_text="You're offline").count() == 1
    assert page.locator("button", has_text="Retry").count() == 1
