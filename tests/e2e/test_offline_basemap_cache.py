"""
tests/e2e/test_offline_basemap_cache.py — SNOW-484 opportunistic basemap
cache Playwright coverage.

Covers ``static/js/sw.js``'s ``BASEMAP_CACHE`` ('basemap' fetch
classification, ``_basemapStaleWhileRevalidate()``, ``_trimCache()``) and
``static/js/map.js``'s ``register-basemap-origins`` handoff.

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
  ``message`` listener) with no seeding at all.
- ``test_registered_basemap_origin_served_from_cache_while_offline``
  seeds ``_basemapOrigins`` + a ``BASEMAP_CACHE`` entry directly, then
  exercises the real ``fetch`` event -> ``_classify()`` ->
  ``_basemapStaleWhileRevalidate()`` pipeline from the page side while
  the browser context is offline.
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


def test_trim_cache_caps_basemap_cache_at_max_entries(pwa_page: PwaPage) -> None:
    """BASEMAP_CACHE_MAX_ENTRIES caps snowdesk-basemap-v1, oldest-first.

    Calls the real ``_trimCache()`` helper (``worker.evaluate``) against a
    small over-the-cap batch — ``Cache.keys()`` returns insertion order,
    and the trim deletes the earliest ``length - max`` keys regardless of
    scale, so 10 entries trimmed to 6 proves the same algorithm 600 would.
    """
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]

    remaining = worker.evaluate(
        """async () => {
            const cache = await caches.open(BASEMAP_CACHE);
            for (const key of await cache.keys()) await cache.delete(key);
            for (let i = 0; i < 10; i++) {
              await cache.put(
                `https://snow484-trim-test.invalid/tile-${i}.pbf`,
                new Response(String(i)),
              );
            }
            await _trimCache(cache, 6);
            return (await cache.keys()).map((k) => k.url);
          }"""
    )

    kept = sorted(int(url.rsplit("tile-", 1)[1].split(".")[0]) for url in remaining)
    assert kept == [4, 5, 6, 7, 8, 9], (
        f"expected the 6 most-recently-inserted entries to survive the trim; kept {kept}"
    )
