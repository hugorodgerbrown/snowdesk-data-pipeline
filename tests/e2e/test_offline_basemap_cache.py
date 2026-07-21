"""
tests/e2e/test_offline_basemap_cache.py — SNOW-484 opportunistic basemap
cache Playwright coverage, extended by SNOW-487 for the durable
``meta:app`` allowlist mirror + lazy rehydration.

Covers ``static/js/sw.js``'s ``BASEMAP_CACHE`` ('basemap' fetch
classification, ``_basemapStaleWhileRevalidate()``, ``_trimCache()``,
``_hydrateBasemapOrigins()``) and ``static/js/map.js``'s
``register-basemap-origins`` handoff (postMessage AND the durable
``meta:app`` write).

Design note — why no test drives a real basemap CDN
-----------------------------------------------------
Two established constraints rule out letting a test load a real basemap
style/tile and asserting the SW opportunistically cached it:

1. Real third-party basemap CDNs (openfreemap.org, geo.admin.ch, ...) are
   documented elsewhere as unreachable/flaky in the CI harness —
   ``test_overlay_basemap_persistence.py`` and ``test_map_glyphs.py``
   both stub or avoid loading one for exactly this reason.
2. ``tests/e2e/_spike_results.py`` (Q1_sw_update) found Playwright's
   ``page.route()`` / ``context.route()`` never observe a request the
   service worker issues from its OWN fetch handler — only requests the
   PAGE itself initiates are interceptable. Route-stubbing a real
   basemap origin would therefore not exercise the SW's own re-fetch
   path (the exact code this ticket adds) at all.

Given that, these tests drive the real ``sw.js`` code end to end but
seed the two inputs it can't get from a route-stub — the origin
allowlist and a "the CDN was already fetched once" cache entry — via
``page.context.service_workers[0].evaluate()`` (the same technique
``test_pwa_push_journey.py`` uses to dispatch a synthetic event directly
on the real, activated SW) and a reserved, never-resolving RFC 2606
``.invalid`` domain, rather than a live CDN:

- ``test_map_js_registers_all_picker_basemap_origins_with_service_worker``
  drives the REAL registration path (real ``#basemap-menu`` markup ->
  map.js's ``BASEMAP_OPTIONS`` parse -> ``postMessage`` -> sw.js's
  ``message`` listener) with no seeding at all, and (SNOW-487) also
  asserts the durable ``meta:app`` mirror map.js writes alongside the
  postMessage.
- ``test_registered_basemap_origin_served_from_cache_while_offline``
  seeds ``_basemapOrigins`` + a ``BASEMAP_CACHE`` entry directly, then
  exercises the real ``fetch`` event -> ``_classify()`` ->
  ``_basemapStaleWhileRevalidate()`` pipeline from the page side while
  the browser context is offline.
- ``test_hydrate_basemap_origins_repopulates_empty_set_from_meta_app``
  (SNOW-487) seeds only the durable ``meta:app`` row, resets
  ``_basemapOrigins`` to empty (simulating an idle-terminated-and-
  restarted worker), and calls ``_hydrateBasemapOrigins()`` directly.
- ``test_basemap_served_from_cache_after_restart_via_meta_app_hydration``
  (SNOW-487) reproduces the bug end to end: only ``meta:app`` + a
  ``BASEMAP_CACHE`` entry are seeded, ``_basemapOrigins`` starts empty,
  and a real offline ``fetch()`` must still resolve from cache via the
  lazy-rehydration path.
- ``test_unregistered_origin_stays_network_only_after_hydration_runs``
  (SNOW-487) proves hydration doesn't turn every cross-origin request
  basemap-cacheable: an origin absent from the ``meta:app`` allowlist
  still hits a real (failing) network attempt.
- ``test_in_flight_hydration_never_clobbers_a_fresh_registration``
  (SNOW-487) exercises the ``_basemapOrigins.size === 0`` write-guard: a
  ``register-basemap-origins`` message that lands mid-rehydrate wins over
  the in-flight (stale) ``meta:app`` read.
- ``test_trim_cache_caps_basemap_cache_at_max_entries`` calls the real
  ``_trimCache()`` helper with a small over-the-cap batch (the
  oldest-first algorithm is size-independent, so this proves the
  eviction order without a slow 600-entry write loop).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings

from tests.e2e.conftest import PwaPage

# Matches the picker's curated key order (public.views._BASEMAP_LABELS) —
# see tests/public/test_map_page.py::test_map_view_passes_basemap_catalogue
# for the same hardcoded tuple. ``swisstopo_light`` is deliberately excluded
# from the picker (SNOW-367) and so never reaches the SW's allowlist.
_PICKER_BASEMAP_KEYS = (
    "openfreemap_liberty",
    "swisstopo_winter",
    "ign_plan",
    "basemap_at",
)


def _origin(url: str) -> str:
    """Return ``scheme://host[:port]`` for ``url`` — mirrors map.js's ``new URL(u).origin``."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def test_map_js_registers_all_picker_basemap_origins_with_service_worker(
    pwa_page: PwaPage,
) -> None:
    """map.js hands the real basemap picker's origins off to the SW.

    Exercises the actual registration path end to end: the real
    ``_map_embed.html``-rendered ``#basemap-menu`` -> map.js's
    ``BASEMAP_OPTIONS`` parse -> ``postMessage({type:
    'register-basemap-origins', ...})`` -> sw.js's ``message`` listener
    -> ``_basemapOrigins``. Reading ``_basemapOrigins`` directly off the
    SW (``worker.evaluate``) rather than adding a debug echo message
    keeps the SW's public contract unchanged.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    # The postMessage handoff is async relative to page 'load' (see
    # map.js's SNOW-484 comment) — poll inside the one evaluate call
    # rather than adding a second round trip.
    origins = worker.evaluate(
        """async () => {
            const deadline = Date.now() + 5000;
            while (Date.now() < deadline) {
              if (_basemapOrigins.size > 0) return [..._basemapOrigins].sort();
              await new Promise((r) => setTimeout(r, 100));
            }
            return [..._basemapOrigins].sort();
          }"""
    )

    expected = sorted(
        {_origin(settings.BASEMAP_STYLES[key]) for key in _PICKER_BASEMAP_KEYS}
    )
    assert origins == expected, (
        f"expected the SW's basemap-origin allowlist to match the picker's "
        f"{len(_PICKER_BASEMAP_KEYS)} entries; got {origins!r}"
    )

    # SNOW-487: map.js also mirrors the same allowlist into the durable
    # meta:app store (key basemap.origins) alongside the postMessage, so
    # a later idle-terminated-and-restarted worker can rehydrate
    # _basemapOrigins from IndexedDB instead of losing it. Poll for the
    # same reason as above — window.pwaDb.put() is async relative to
    # page 'load'.
    stored = page.evaluate(
        """async () => {
            const deadline = Date.now() + 5000;
            while (Date.now() < deadline) {
              const row = await window.pwaDb.get('meta:app', 'basemap.origins');
              if (row && Array.isArray(row.value) && row.value.length > 0) {
                return [...row.value].sort();
              }
              await new Promise((r) => setTimeout(r, 100));
            }
            const row = await window.pwaDb.get('meta:app', 'basemap.origins');
            return row ? [...row.value].sort() : null;
          }"""
    )
    assert stored == expected, (
        f"expected the durable meta:app 'basemap.origins' mirror to match the picker's "
        f"{len(_PICKER_BASEMAP_KEYS)} entries; got {stored!r}"
    )


def test_registered_basemap_origin_served_from_cache_while_offline(
    pwa_page: PwaPage,
) -> None:
    """A basemap-origin response already in BASEMAP_CACHE is served offline.

    Seeds ``_basemapOrigins`` and a ``snowdesk-basemap-v1`` entry directly
    on the real SW (see module docstring for why), then issues a genuine
    page-side ``fetch()`` while the browser context is offline. The real
    ``_classify()`` must route it to the 'basemap' strategy and the real
    ``_basemapStaleWhileRevalidate()`` must resolve it from cache with
    zero network access — proving the offline-serve contract this ticket
    exists to deliver, independent of any specific tile URL.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    tile_origin = "https://snow484-test-basemap.invalid"
    tile_url = f"{tile_origin}/tile/0/0/0.pbf"
    tile_body = "snow484-test-tile-bytes"

    worker.evaluate(
        """async ({ origin, url, body }) => {
            _basemapOrigins = new Set([origin]);
            const cache = await caches.open(BASEMAP_CACHE);
            await cache.put(
              url,
              new Response(body, {
                status: 200,
                headers: { 'Access-Control-Allow-Origin': '*' },
              }),
            );
          }""",
        {"origin": tile_origin, "url": tile_url, "body": tile_body},
    )

    page.context.set_offline(True)
    try:
        result = page.evaluate(
            """async (url) => {
                try {
                  const resp = await fetch(url, { mode: 'cors' });
                  const text = await resp.text();
                  return { ok: resp.ok, status: resp.status, text };
                } catch (err) {
                  return { ok: false, error: String(err) };
                }
              }""",
            tile_url,
        )
    finally:
        page.context.set_offline(False)

    assert result["ok"], (
        f"expected the cached basemap response to resolve while offline: {result}"
    )
    assert result["text"] == tile_body


def test_hydrate_basemap_origins_repopulates_empty_set_from_meta_app(
    pwa_page: PwaPage,
) -> None:
    """SNOW-487: ``_hydrateBasemapOrigins()`` rehydrates an empty in-memory Set.

    Simulates the idle-worker-restart case this ticket exists to fix: the
    durable ``meta:app`` row is seeded (as ``static/js/map.js`` would have
    written it before the browser terminated the worker), while
    ``_basemapOrigins`` — which does NOT survive a worker restart — starts
    out empty, as it would in a freshly (re)created worker global. Calling
    ``_hydrateBasemapOrigins()`` directly proves the read-and-repopulate
    path in isolation from the ``fetch`` pipeline exercised by the next
    test.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    origins = ["https://snow487-hydrate-test.invalid"]
    page.evaluate(
        """async (origins) => {
            await window.pwaDb.put('meta:app', { key: 'basemap.origins', value: origins });
          }""",
        origins,
    )

    result = worker.evaluate(
        """async () => {
            _basemapOrigins = new Set();
            _basemapHydration = null;
            await _hydrateBasemapOrigins();
            return [..._basemapOrigins].sort();
          }"""
    )
    assert result == origins, (
        f"expected _hydrateBasemapOrigins() to repopulate _basemapOrigins from the "
        f"durable meta:app row; got {result!r}"
    )


def test_basemap_served_from_cache_after_restart_via_meta_app_hydration(
    pwa_page: PwaPage,
) -> None:
    """A cached basemap response survives a simulated worker restart.

    Unlike ``test_registered_basemap_origin_served_from_cache_while_offline``
    (which seeds ``_basemapOrigins`` directly), this seeds ONLY the durable
    ``meta:app`` row and leaves ``_basemapOrigins`` empty — reproducing the
    exact bug this ticket fixes: an idle-terminated worker with no
    in-memory allowlist, but a durable ``BASEMAP_CACHE`` entry from before
    the restart. The real ``fetch`` -> ``_classify()`` ->
    ``_hydrateBasemapOrigins()`` -> ``_basemapStaleWhileRevalidate()``
    pipeline must still resolve the request from cache while offline.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    tile_origin = "https://snow487-restart-test.invalid"
    tile_url = f"{tile_origin}/tile/0/0/0.pbf"
    tile_body = "snow487-restart-tile-bytes"

    page.evaluate(
        """async (origin) => {
            await window.pwaDb.put('meta:app', { key: 'basemap.origins', value: [origin] });
          }""",
        tile_origin,
    )

    worker.evaluate(
        """async ({ url, body }) => {
            _basemapOrigins = new Set();
            _basemapHydration = null;
            const cache = await caches.open(BASEMAP_CACHE);
            await cache.put(
              url,
              new Response(body, {
                status: 200,
                headers: { 'Access-Control-Allow-Origin': '*' },
              }),
            );
          }""",
        {"url": tile_url, "body": tile_body},
    )

    page.context.set_offline(True)
    try:
        result = page.evaluate(
            """async (url) => {
                try {
                  const resp = await fetch(url, { mode: 'cors' });
                  const text = await resp.text();
                  return { ok: resp.ok, status: resp.status, text };
                } catch (err) {
                  return { ok: false, error: String(err) };
                }
              }""",
            tile_url,
        )
    finally:
        page.context.set_offline(False)

    assert result["ok"], (
        f"expected the cached basemap response to resolve after meta:app "
        f"hydration, with no in-memory allowlist ever set directly: {result}"
    )
    assert result["text"] == tile_body


def test_unregistered_origin_stays_network_only_after_hydration_runs(
    pwa_page: PwaPage,
) -> None:
    """An unregistered cross-origin request is never served from the basemap cache.

    Seeds ``meta:app`` with one registered origin (distinct from the one
    fetched), leaves ``_basemapOrigins`` empty so hydration must run, then
    fetches a DIFFERENT, never-cached ``.invalid`` origin. The real network
    attempt to a reserved (RFC 2606) domain always fails — proving the
    request was routed to plain ``fetch()``, not the basemap cache — while
    the follow-up read of ``_basemapOrigins`` confirms hydration DID run
    (and simply didn't match this origin), rather than the assertion
    passing by accident because hydration was skipped.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    registered_origin = "https://snow487-registered.invalid"
    unregistered_url = "https://snow487-unregistered.invalid/tile/0/0/0.pbf"

    page.evaluate(
        """async (origin) => {
            await window.pwaDb.put('meta:app', { key: 'basemap.origins', value: [origin] });
          }""",
        registered_origin,
    )

    worker.evaluate(
        """() => {
            _basemapOrigins = new Set();
            _basemapHydration = null;
          }"""
    )

    result = page.evaluate(
        """async (url) => {
            try {
              const resp = await fetch(url, { mode: 'cors' });
              return { ok: resp.ok, status: resp.status };
            } catch (err) {
              return { ok: false, error: String(err) };
            }
          }""",
        unregistered_url,
    )
    assert result["ok"] is False, (
        f"expected the unregistered .invalid origin to hit a real (failing) "
        f"network attempt rather than being served from BASEMAP_CACHE: {result}"
    )

    hydrated = worker.evaluate("""() => [..._basemapOrigins].sort()""")
    assert hydrated == [registered_origin], (
        f"expected hydration to have run and populated _basemapOrigins from "
        f"meta:app even though the fetched origin didn't match; got {hydrated!r}"
    )


def test_in_flight_hydration_never_clobbers_a_fresh_registration(
    pwa_page: PwaPage,
) -> None:
    """SNOW-487: a live registration wins a race against an in-flight rehydrate.

    Reproduces the interleaving the ``_basemapOrigins.size === 0`` write-guard
    exists to defend: a fresh worker starts rehydrating ``_basemapOrigins``
    from a (now stale) ``meta:app`` row, and while that IndexedDB read is in
    flight a live page posts ``register-basemap-origins`` with the current
    allowlist. The explicit registration must win — a stale durable read must
    never overwrite it.

    The race is made deterministic without pausing the DB read: an async IIFE
    runs synchronously up to its first ``await``, so ``_hydrateBasemapOrigins()``
    is suspended at the DB-open ``await`` the moment it returns its promise.
    Dispatching the ``message`` event synchronously then runs the real
    ``register-basemap-origins`` handler before the read resolves, so when
    hydration resumes it finds a non-empty Set and leaves it untouched.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    stale_origin = "https://snow487-stale-db.invalid"
    fresh_origin = "https://snow487-fresh-registration.invalid"

    page.evaluate(
        """async (origin) => {
            await window.pwaDb.put('meta:app', { key: 'basemap.origins', value: [origin] });
          }""",
        stale_origin,
    )

    result = worker.evaluate(
        """async (fresh) => {
            _basemapOrigins = new Set();
            _basemapHydration = null;
            // Starts the rehydrate; the async IIFE runs to its first await
            // (the IndexedDB open) and yields, leaving the read in flight.
            const inFlight = _hydrateBasemapOrigins();
            // A live page registers the current allowlist mid-read. The real
            // 'message' listener runs synchronously via dispatchEvent, setting
            // _basemapOrigins and resetting _basemapHydration.
            self.dispatchEvent(new MessageEvent('message', {
              data: { type: 'register-basemap-origins', origins: [fresh] },
            }));
            await inFlight;
            return [..._basemapOrigins].sort();
          }""",
        fresh_origin,
    )
    assert result == [fresh_origin], (
        f"expected the live register-basemap-origins allowlist to survive the "
        f"in-flight stale meta:app read; got {result!r}"
    )


def test_trim_cache_caps_basemap_cache_at_max_entries(pwa_page: PwaPage) -> None:
    """BASEMAP_CACHE_MAX_ENTRIES caps snowdesk-basemap-v1, oldest-first.

    Calls the real ``_trimCache()`` helper (``worker.evaluate``) against a
    small over-the-cap batch — ``Cache.keys()`` returns insertion order,
    and the trim deletes the earliest ``length - max`` keys regardless of
    scale, so 10 entries trimmed to 6 proves the same algorithm 600 would.

    Runs against a dedicated throwaway cache, NOT the live ``BASEMAP_CACHE``:
    this fixture drives the real service worker, whose
    ``_basemapStaleWhileRevalidate`` writes genuine basemap tiles into
    ``BASEMAP_CACHE`` as the map paints. One of those real ``cache.put``s
    landing during this evaluate polluted the cache with a non-``tile-*``
    URL, so the parse below raised ``IndexError`` (and, when the intruder
    landed last, stole a survivor slot from the trim). ``_trimCache(cache,
    max)`` is cache-agnostic — it takes the cache as an argument and never
    names ``BASEMAP_CACHE`` — so a private cache exercises the identical
    algorithm with no interference from the worker's own writes.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    remaining = worker.evaluate(
        """async () => {
            const TEST_CACHE = 'snow484-trim-test-cache';
            await caches.delete(TEST_CACHE);
            const cache = await caches.open(TEST_CACHE);
            for (let i = 0; i < 10; i++) {
              await cache.put(
                `https://snow484-trim-test.invalid/tile-${i}.pbf`,
                new Response(String(i)),
              );
            }
            await _trimCache(cache, 6);
            const urls = (await cache.keys()).map((k) => k.url);
            await caches.delete(TEST_CACHE);
            return urls;
          }"""
    )

    kept = sorted(int(url.rsplit("tile-", 1)[1].split(".")[0]) for url in remaining)
    assert kept == [4, 5, 6, 7, 8, 9], (
        f"expected the 6 most-recently-inserted entries to survive the trim; kept {kept}"
    )
