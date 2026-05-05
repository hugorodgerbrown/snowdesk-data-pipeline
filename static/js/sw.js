/*
 * static/js/sw.js — PWA shell service worker for Snowdesk.
 *
 * Replaces the SNOW-9 precache controller (~190 lines, opt-in "Save
 * offline" button + chunked manifest fetch + version dance) with a
 * minimal runtime cache that makes the second load of any page
 * instant without the user having to opt in. Removes the source of
 * the "stuck on stale data" reports that motivated SNOW-79.
 *
 * Strategies:
 *
 *   - Same-origin static shell  (CSS, JS, fonts, images, manifest,
 *                                /sw.js itself, the regions GeoJSON
 *                                feed which doesn't change between
 *                                deploys for a given session)
 *     → stale-while-revalidate.
 *
 *   - HTML navigations          → network-first with a per-page cache
 *                                fallback so an offline reload still
 *                                surfaces the last-seen version, and a
 *                                pre-cached /static/offline.html if the
 *                                requested URL has never been visited
 *                                (SNOW-118).
 *
 *   - Everything else           (most /api/* endpoints, third-party
 *                                origins like maplibre + tiles)
 *     → network-only. Bulletin JSON, today-summaries, calendar
 *     partials, and map tiles must always reflect server-side
 *     freshness; cached avalanche ratings are dangerous.
 *
 * Cache version
 * -------------
 * Bump ``CACHE_VERSION`` whenever the cache contract changes (e.g. a
 * new asset class added, or a rule that would cause stale entries to
 * be re-served incorrectly under the new fetch logic). On
 * ``activate``, every cache key not matching the current version is
 * deleted so old SW deploys leave nothing behind. The version is also
 * surfaced via a ``message`` handler so devtools can confirm which SW
 * version is in control.
 *
 * Scope
 * -----
 * Registered from /sw.js (root path) so the SW controls the whole
 * site. The Service-Worker-Allowed header on the response from
 * ``public.views.serve_sw`` makes that scope explicit.
 *
 * i18n: this worker never renders UI, so there are no translatable
 * strings.
 */

'use strict';

const CACHE_VERSION = 'snowdesk-shell-v3';

// Pre-cached on install so the offline fallback is reliably available
// the moment the network drops, even on the very first navigation that
// loses connectivity. Keep this list short — anything hashed by
// ManifestStaticFilesStorage can't be precached by stable URL, and
// stale-while-revalidate already handles the shell on the second visit.
const OFFLINE_FALLBACK = '/static/offline.html';
const PRECACHE_URLS = [OFFLINE_FALLBACK];

// File extensions that count as same-origin static shell. Anything
// not in this set, and not a same-origin GeoJSON feed, falls through
// to network-only. The list deliberately excludes ``.json`` —
// generic JSON paths under /api/ may be region summaries / bulletins,
// which must stay fresh.
const STATIC_SHELL_EXTENSIONS = new Set([
  '.css',
  '.js',
  '.svg',
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.ico',
  '.woff',
  '.woff2',
  '.webmanifest',
]);

// Same-origin URL paths that are versioned-by-deploy and therefore
// safe to serve stale-while-revalidate. Limited to the regions
// GeoJSON feed today; any new entry must be similarly safe to cache
// across a session (a stale region polygon never misleads the user
// about danger; a stale rating would).
const STATIC_PATHS = new Set(['/api/regions.geojson']);

// ---------------------------------------------------------------------------
// Lifecycle — install
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      // Pre-cache the offline fallback so the network-first strategy
      // can return it from cache when both network and per-page cache
      // miss (e.g. user opens a never-visited page while offline).
      const cache = await caches.open(CACHE_VERSION);
      await cache.addAll(PRECACHE_URLS);
    })(),
  );
  // Skip the "waiting" phase so the new SW takes over on the next
  // page load without forcing the user to close every open tab.
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Lifecycle — activate
// ---------------------------------------------------------------------------

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // Reap caches from earlier SW versions so disk doesn't grow.
      // Use ``startsWith('snowdesk-shell-')`` rather than a strict
      // equality check so legacy ``map-shell-*`` caches from the
      // SNOW-9 precache controller also get cleared on first install
      // of the SNOW-79 SW (see the catch-all sweep below).
      const cacheNames = await caches.keys();
      const deletions = cacheNames
        .filter(
          (name) =>
            name.startsWith('snowdesk-shell-') ||
            name.startsWith('map-shell-'),
        )
        .filter((name) => name !== CACHE_VERSION)
        .map((name) => caches.delete(name));
      await Promise.all(deletions);
      // Deliberately NOT calling ``self.clients.claim()`` — pairing it
      // with ``skipWaiting`` and a controllerchange-based auto-reload in
      // ``sw_register.js`` produces a tight reload loop in dev, where the
      // browser-side SW update check fires on every navigation. Letting
      // the new SW take control on the next natural navigation gives the
      // same end-state without the loop.
    })(),
  );
});

// ---------------------------------------------------------------------------
// Fetch — strategy router
// ---------------------------------------------------------------------------

/**
 * Decide which strategy applies to a given request.
 *
 * Returns one of: ``'static'`` | ``'navigate'`` | ``'network'``.
 *
 * @param {Request} request
 * @returns {'static' | 'navigate' | 'network'}
 */
function _classify(request) {
  if (request.method !== 'GET') return 'network';
  const url = new URL(request.url);

  if (url.origin !== self.location.origin) return 'network';

  if (request.mode === 'navigate' || request.destination === 'document') {
    return 'navigate';
  }

  if (STATIC_PATHS.has(url.pathname)) return 'static';

  const dot = url.pathname.lastIndexOf('.');
  if (dot !== -1) {
    const ext = url.pathname.slice(dot).toLowerCase();
    if (STATIC_SHELL_EXTENSIONS.has(ext)) return 'static';
  }

  return 'network';
}

/**
 * Stale-while-revalidate: serve the cached response immediately if
 * present, kick off a background re-fetch to refresh the cache for
 * the next call. Falls through to network-only on cache miss.
 */
async function _staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_VERSION);
  const cached = await cache.match(request);
  const fetchPromise = fetch(request)
    .then((response) => {
      // Only cache successful, basic (same-origin) responses. ``opaque``
      // responses from cross-origin no-cors requests are unreadable, and
      // 4xx/5xx would poison the cache.
      if (response && response.ok && response.type === 'basic') {
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    })
    .catch(() => null);
  if (cached) return cached;
  const network = await fetchPromise;
  if (network) return network;
  return new Response('', { status: 504, statusText: 'Gateway Timeout' });
}

/**
 * Network-first: try the network, fall back to cache on failure, then
 * to the offline fallback page if the request is a navigation. Use for
 * HTML navigations so the user sees fresh data normally, the last-seen
 * page when offline-but-cached, and a branded offline page when neither
 * network nor cache has the URL (a page they've never visited before).
 */
async function _networkFirst(request) {
  const cache = await caches.open(CACHE_VERSION);
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === 'basic') {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    if (request.mode === 'navigate' || request.destination === 'document') {
      const fallback = await cache.match(OFFLINE_FALLBACK);
      if (fallback) return fallback;
    }
    throw err;
  }
}

self.addEventListener('fetch', (event) => {
  const strategy = _classify(event.request);
  if (strategy === 'static') {
    event.respondWith(_staleWhileRevalidate(event.request));
  } else if (strategy === 'navigate') {
    event.respondWith(_networkFirst(event.request));
  }
  // 'network' → fall through to the default browser fetch. No
  // event.respondWith() call means the request is never seen by the
  // SW's caching layer at all.
});

// ---------------------------------------------------------------------------
// Message — version probe (dev convenience)
// ---------------------------------------------------------------------------

self.addEventListener('message', (event) => {
  if (event.data === 'version') {
    event.source?.postMessage({ type: 'version', version: CACHE_VERSION });
  }
});
