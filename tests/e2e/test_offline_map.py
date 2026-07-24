"""
tests/e2e/test_offline_map.py — Playwright coverage for SNOW-492 ("Further
adventures in offline sync"): the blank-map bug fix, the favourites /
community-reports offline overlay cache, the per-overlay "unavailable
offline" toast, and the "Cache this area for offline" control.

Scope and deliberate omissions
------------------------------
Full end-to-end offline-SW testing is intricate: a real service worker's
own fetches are invisible to ``page.route()`` (see
``docs/client-side-tests.md``), so each test below picks the narrowest
technique that still exercises the real production code path:

* The blank-map regression (commit 1) is tested by firing a *synthetic*
  ``error`` event directly on the real ``MAP`` instance with
  ``isStyleLoaded`` stubbed false, rather than driving a real offline zoom
  to an uncached tile. A live pan/zoom would depend on a third-party
  basemap CDN actually being reachable from CI, which
  ``tests/e2e/test_offline_basemap_cache.py``'s module docstring already
  documents as flaky/unavailable — the same reason that file drives
  ``sw.js`` directly via ``worker.evaluate()`` instead. Firing the event
  directly is deterministic AND exercises the exact branch the fix changed.
* The favourites cache test uses a real service worker + a real offline
  browser context so the whole write-through/read-back path runs for
  real, including actual ``IndexedDB`` persistence across a reload — but
  via a locally-built ``_authenticated_pwa_navigate`` helper rather than
  the shared ``signed_in_page`` fixture. ``signed_in_page`` navigates
  anonymously first (``pwa_page``) and only attaches the session cookie
  afterwards; a THIRD navigation on top of that (needed here to make the
  boot-time favourites fetch see an eligible session) reproducibly hung
  waiting for the map to finish loading against the real basemap CDN in
  this harness — a fresh two-navigation dance (register SW, THEN reload),
  mirroring ``pwa_page``'s own sequence but with the cookie already
  attached before the first navigation, does not.
* The community-reports cache test avoids a real service worker AND the
  live basemap CDN entirely: a repro run showed the real CDN sometimes
  never responds at all in this harness (not merely slow — the map never
  finished loading even after 45s), the same class of flakiness
  ``tests/e2e/test_offline_basemap_cache.py``'s module docstring already
  documents and designs around. It routes the basemap style URL to abort
  (forcing the SNOW-483 inline fallback, which needs no network) and the
  community-reports fetch itself to abort, then seeds
  ``data:map_overlays`` directly via ``window.pwaMapOverlayCache`` — no
  real prior fetch is needed since write-through and read-back are
  independent functions. This proves ``ensureOverlayLoaded``'s
  offline-fetch-failed branch and ``dropExpiredCommunityReports`` without
  needing a real offline browser context at all.
* The "nothing cached yet" toast test also strips the service worker and
  uses ``page.route()`` to abort the overlay fetch — deterministic, and the
  same technique ``test_offline_basemap_fallback.py`` uses to simulate an
  offline fetch failure without a real offline browser context.
* The "cache this area" control's URL assembly depends on the *active*
  MapLibre basemap's own vector-tile sources, which in turn depends on a
  reachable third-party basemap CDN (also documented as unreliable in CI,
  see above) — so the warm-cache test calls ``window.pwaWarmCache``
  directly with known-reachable same-origin URLs, proving the SW message
  handler + ``sw_register.js`` bridge work end to end, without depending on
  any specific basemap tile actually resolving.
* Adding a favourite / submitting an observation while offline, and both
  draining on reconnect, are already covered by
  ``tests/e2e/test_offline_favourite_submit.py`` and
  ``tests/e2e/test_offline_observation_submit.py`` (SNOW-479 / SNOW-475) —
  not duplicated here.
"""

from __future__ import annotations

import datetime
import time
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.e2e.conftest import PwaPage, _session_login
from tests.factories import AccountFactory, FavouriteFactory

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to ``/`` and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')"
    )


def _authenticated_pwa_navigate(page: Page, live_server_url: str, user: User) -> None:
    """Log in, then drive the real SW to a stable, SW-controlled, loaded map.

    Mirrors ``tests/e2e/conftest.py``'s ``pwa_page`` fixture's own
    register-then-reload dance (a fresh SW registration does not control
    the page that triggered it — see that fixture's docstring), but with
    the session cookie attached BEFORE the first navigation rather than
    after, so the boot-time favourites/community-reports fetch already
    sees an eligible session on this, its only navigation of the map.
    """
    _session_login(page.context, live_server_url, user)
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("load")
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )
    page.reload()
    page.wait_for_load_state("load")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')"
    )


def _poll(
    page: Page,
    predicate_js: str,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> Any:
    """Poll ``page.evaluate(predicate_js)`` until it returns a truthy value.

    One ``page.evaluate`` round-trip per attempt, matching
    ``tests/e2e/test_offline_favourite_submit.py``'s established pattern
    (see that module's docstring for why an async ``wait_for_function``
    predicate is avoided here).
    """
    deadline = time.monotonic() + timeout_s
    result = None
    while time.monotonic() < deadline:
        result = page.evaluate(predicate_js)
        if result:
            return result
        time.sleep(interval_s)
    raise AssertionError(
        f"condition never became true: {predicate_js!r} (last={result!r})"
    )


# ---------------------------------------------------------------------------
# Commit 1 — blank-map regression guard
# ---------------------------------------------------------------------------


def test_tile_scoped_map_error_does_not_engage_blank_fallback(
    pwa_page: PwaPage,
) -> None:
    """A source/tile-scoped MapLibre error must never blank the map.

    Regression guard for the bug this ticket fixes: before the fix, ANY
    ``error`` event while ``!isStyleLoaded()`` (which is transiently true
    mid-zoom, while tiles are in flight) swapped in the empty fallback
    style. Stubbing ``isStyleLoaded`` to always return false reproduces
    that transient window deterministically, then firing a real MapLibre
    error shape carrying ``sourceId`` proves the new early-return in
    ``map.on('error', ...)`` (static/js/map.js) keeps the real basemap up.
    """
    page = pwa_page.page

    # map.setStyle() applies asynchronously — a genuine engagement wouldn't
    # necessarily be visible in the very same tick the event fires in, so a
    # NEGATIVE assertion needs to wait out that same window rather than
    # read too early and pass by accident. 500ms comfortably covers
    # buildFallbackStyle's "synchronous inline style" application per its
    # own docstring in map.js.
    engaged = page.evaluate(
        """async () => {
            const original = MAP.isStyleLoaded;
            MAP.isStyleLoaded = () => false;
            try {
                MAP.fire('error', { sourceId: 'regions', tile: {} });
                await new Promise((r) => setTimeout(r, 500));
                return MAP.getStyle()?.name === 'snowdesk-offline-fallback';
            } finally {
                MAP.isStyleLoaded = original;
            }
        }"""
    )
    assert engaged is False, (
        "a tile/source-scoped error must not engage the blank cold-boot fallback"
    )


def test_non_tile_error_with_no_style_loaded_still_engages_fallback(
    pwa_page: PwaPage,
) -> None:
    """Control case: a genuine style-load failure still engages the fallback.

    Proves the SNOW-492 guard is scoped to tile/source-scoped errors only —
    an error carrying neither ``sourceId`` nor ``tile`` (the shape a real
    ``Style.loadURL`` failure produces) must still swap in the SNOW-483
    inline fallback when no style is currently loaded.
    """
    page = pwa_page.page

    # Poll rather than read once — map.setStyle() (which the fallback path
    # calls) applies asynchronously, so the style name doesn't necessarily
    # update in the same tick the 'error' event handler runs.
    engaged = page.evaluate(
        """async () => {
            const original = MAP.isStyleLoaded;
            MAP.isStyleLoaded = () => false;
            try {
                MAP.fire('error', { error: new Error('style fetch failed') });
                const deadline = Date.now() + 5000;
                while (Date.now() < deadline) {
                    if (MAP.getStyle()?.name === 'snowdesk-offline-fallback') return true;
                    await new Promise((r) => setTimeout(r, 50));
                }
                return false;
            } finally {
                MAP.isStyleLoaded = original;
            }
        }"""
    )
    assert engaged is True, (
        "a genuine style-load failure with no style up must still engage the fallback"
    )


# ---------------------------------------------------------------------------
# Commit 2/3 — favourites / community-reports offline cache + toast
# ---------------------------------------------------------------------------


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_favourites_offline_toast_when_nothing_cached_yet(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """The favourites "unavailable offline" toast fires with nothing cached.

    Service worker stripped + the favourites-geojson fetch routed to abort
    (the same "simulate an offline fetch failure without a real offline
    browser context" technique as
    ``tests/e2e/test_offline_basemap_fallback.py``) — deterministic, and
    proves ``ensureOverlayLoaded``'s new no-cached-fallback branch reveals
    ``#map-offline-toast-favourites`` rather than silently no-opping.
    """
    with django_db_blocker.unblock():
        account = AccountFactory.create()
    _session_login(page.context, live_server.url, account.user)

    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.route("**/favourites/favourites.geojson", lambda route: route.abort())

    _navigate_home(page, live_server.url)

    page.wait_for_selector("#map-offline-toast-favourites:not(.hidden)", timeout=10000)


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_favourites_overlay_installs_from_cache_after_offline_reload(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """An offline reload installs favourites from the write-through cache.

    Online: navigate as an already-authenticated account (favourites is
    default-on, so the boot-time ``ensureOverlayLoaded('favourites')`` fetch
    fires automatically and, per SNOW-492, writes the payload into
    ``data:map_overlays`` via ``map_overlay_offline_cache.js``). Offline:
    reload — the same boot-time fetch fails, and the layer installs from
    that cached copy instead of leaving the favourites source empty.
    """
    with django_db_blocker.unblock():
        account = AccountFactory.create()
        favourite = FavouriteFactory.create(
            user=account.user, name="Cached Offline Peak"
        )
    fav_uuid = str(favourite.uuid)

    _authenticated_pwa_navigate(page, live_server.url, account.user)

    _poll(
        page,
        """() => window.pwaDb.get('data:map_overlays', 'favourites')
            .then((row) => !!(row && row.geojson))""",
    )

    page.context.set_offline(True)
    try:
        page.reload()
        page.wait_for_load_state("load")
        page.wait_for_selector('#season-scrubber[data-state="ready"]')
        page.wait_for_function(
            "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')"
        )
        _poll(
            page,
            f"""() => {{
                const src = MAP.getSource('favourites');
                if (!src) return false;
                const data = src.serialize().data;
                return (data.features || []).some(
                    (f) => f.properties && f.properties.uuid === {fav_uuid!r},
                );
            }}""",
        )
    finally:
        page.context.set_offline(False)


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_community_reports_toggle_drops_expired_features_from_cache(
    live_server: LiveServer, page: Page
) -> None:
    """A stale cached community-reports copy expires visually at 48h.

    Deliberately avoids both a real service worker AND the live basemap
    CDN: this sandbox intermittently never gets a response at all from the
    real CDN (not merely slow — ``MAP.loaded()``/``isStyleLoaded()`` never
    resolved even after 45s in a repro run), the same class of flakiness
    ``tests/e2e/test_offline_basemap_cache.py``'s module docstring already
    documents and designs around. Routing the style URL to abort forces
    the SNOW-483 inline fallback (synchronous, no network) so ``MAP``
    reaches ``load`` almost immediately regardless of CDN reachability.

    Seeds ``data:map_overlays`` directly via ``window.pwaMapOverlayCache``
    (no prior real fetch needed — write-through and read-back are
    independent functions) with two features — one fresh, one just past
    the 48h horizon — then routes the community-reports fetch itself to
    abort so toggling the overlay on exercises the exact offline-fetch-
    failed branch of ``ensureOverlayLoaded`` against that seeded cache.
    Only the fresh feature must survive, proving
    ``dropExpiredCommunityReports`` (static/js/map.js) runs on read-back.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.route(settings.BASEMAP_STYLE_URL, lambda route: route.abort())
    page.route("**/api/community-reports.geojson", lambda route: route.abort())

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")

    fresh_iso = timezone.now().isoformat(timespec="seconds")
    stale_iso = (timezone.now() - datetime.timedelta(hours=49)).isoformat(
        timespec="seconds"
    )
    page.evaluate(
        """({ freshIso, staleIso }) => window.pwaMapOverlayCache.putOverlay(
            'community_reports',
            {
                type: 'FeatureCollection',
                features: [
                    {
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [7.6, 46.2] },
                        properties: {
                            type: 'WHUMPFING',
                            type_label: 'Whumpfing',
                            observed_at: freshIso,
                            region_name: 'Fresh region',
                        },
                    },
                    {
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [7.7, 46.3] },
                        properties: {
                            type: 'WHUMPFING',
                            type_label: 'Whumpfing',
                            observed_at: staleIso,
                            region_name: 'Stale region',
                        },
                    },
                ],
            },
        )""",
        {"freshIso": fresh_iso, "staleIso": stale_iso},
    )

    toggle.click()
    features = _poll(
        page,
        """() => {
            const src = MAP.getSource('community-reports');
            if (!src) return null;
            const feats = src.serialize().data.features || [];
            return feats.length > 0 ? feats : null;
        }""",
    )

    assert len(features) == 1, (
        f"expected only the fresh (< 48h) feature to survive the read-back; "
        f"got {features!r}"
    )
    assert features[0]["properties"]["region_name"] == "Fresh region"


# ---------------------------------------------------------------------------
# Commit 4 — "Cache this area for offline" control
# ---------------------------------------------------------------------------


def test_cache_now_warms_same_origin_urls_into_cache_storage(
    pwa_page: PwaPage,
) -> None:
    """``window.pwaWarmCache`` fetches a URL list and reports {ok, failed}.

    Calls the sw_register.js bridge directly with known-reachable
    same-origin URLs rather than driving the real "Cache this area" button
    (whose URL assembly depends on the active basemap's own vector tiles —
    a third-party CDN dependency ``test_offline_basemap_cache.py`` already
    documents as unreliable in CI). This still exercises the real SW
    message handler (static/js/sw.js's 'warm-cache' branch) end to end.
    """
    page = pwa_page.page
    page.wait_for_function("() => typeof window.pwaWarmCache === 'function'")

    urls = ["/api/version", "/api/resorts.geojson"]
    result = page.evaluate(
        "(urls) => window.pwaWarmCache(urls)",
        urls,
    )
    assert result is not None
    assert result["ok"] == len(urls), result
    assert result["failed"] == 0, result

    cached = page.evaluate(
        """async (urls) => {
            const hits = [];
            for (const url of urls) {
                hits.push(!!(await caches.match(url)));
            }
            return hits;
          }""",
        urls,
    )
    assert cached == [True, True], (
        f"expected every warmed URL to be readable back from Cache Storage: {cached}"
    )


def test_cache_now_button_completes_and_reveals_toast(pwa_page: PwaPage) -> None:
    """Clicking "Cache this area for offline" completes and shows a toast.

    Smoke test for the real button wiring (basemapPickerInit's
    ``snowdesk:cache-now`` dispatch -> cacheNowInit -> ``pwaWarmCache`` ->
    the completion toast) — does not assert on how many basemap tiles were
    actually cached, since that depends on the active basemap's CDN being
    reachable (see the module docstring). Like
    ``test_favourites_overlay_installs_from_cache_after_offline_reload``,
    this test still depends on ``pwa_page``'s own boot reaching a real
    basemap (``MAP`` existing is enough here — the click handler degrades
    gracefully to warming zero tile URLs if the style never loaded), so it
    carries the same theoretical live-CDN flakiness risk documented in the
    module docstring, even though the completion toast itself never
    depends on the CDN succeeding.

    SNOW-493 finding 7: the completion toast now branches on the actual
    {ok, failed} counts (complete/partial/failed — see
    ``test_cache_this_area.py`` for coverage of each branch with stubbed
    counts), so this smoke test accepts whichever of the three the live
    CDN outcome happens to produce rather than asserting a specific one.
    """
    page = pwa_page.page
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && "
        "typeof window.pwaWarmCache === 'function'"
    )

    page.click("#basemap-toggle")
    button = page.locator("#cache-now-toggle")
    button.wait_for(state="visible")
    button.click()

    page.wait_for_selector(
        "#map-cache-now-toast-complete:not(.hidden), "
        "#map-cache-now-toast-partial:not(.hidden), "
        "#map-cache-now-toast-failed:not(.hidden)",
        timeout=15000,
    )
    # The button re-enables once the run completes (busy guard cleared).
    assert button.get_attribute("aria-disabled") is None
