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
 *                                /sw.js itself, the region/resort
 *                                GeoJSON feeds which don't change
 *                                between deploys, and /api/ratings/
 *                                whose URL encodes the date window —
 *                                see STATIC_PATHS below for the full
 *                                list and the per-entry safety argument)
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
 *     → network-only. Bulletin JSON, calendar partials, and map tiles
 *     must always reflect server-side freshness; cached avalanche
 *     ratings are dangerous. Exception: /api/ratings/ gets
 *     stale-while-revalidate because its URL encodes the (country,
 *     date) window via query parameters — a stale entry can never be
 *     served for the wrong day, since date rollover changes the URL.
 *
 * Update contract (the important part)
 * -------------------------------------
 * The goal is a contract a non-technical user can rely on: *if there is
 * an update, you see one "Reload" message; if there is no message, you
 * are already on the latest version.* No silent swaps, no stale tab that
 * never catches up.
 *
 * To make that true the worker does NOT call ``skipWaiting()`` on
 * install. A freshly-installed worker sits in the "waiting" state — that
 * waiting worker IS the pending update, and ``sw_register.js`` shows the
 * banner for exactly that condition. The worker only activates when the
 * page tells it to, by posting ``{ type: 'SKIP_WAITING' }`` (the user
 * clicked "Reload"). On ``activate`` it then calls ``clients.claim()`` so
 * it takes control of every open tab immediately; that fires
 * ``controllerchange`` in the page, which does ONE guarded reload onto
 * the new shell. Because activation is user-driven, claiming here cannot
 * reproduce the dev reload-loop the previous design hit — that loop
 * required auto-skipWaiting on install, which we no longer do.
 *
 * Cache version
 * -------------
 * Bump ``CACHE_VERSION`` whenever the shell changes — a new version
 * string changes the bytes of this script, which is what makes the
 * browser detect the update and surface the banner. On ``activate``,
 * every cache key not matching the current version is deleted so old SW
 * deploys leave nothing behind. The version is also surfaced via a
 * ``message`` handler so devtools can confirm which SW version is in
 * control.
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

const CACHE_VERSION = 'snowdesk-shell-v8';

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

// Same-origin URL paths that are safe to serve stale-while-revalidate.
// Two classes of entry:
//
//   Geo feeds — versioned-by-deploy. Polygon and resort geometry
//   changes only on deploy, so a stale polygon never misleads the user
//   about danger (unlike a stale rating, which would).
//
//   /api/ratings/ — safe for a different reason: its URL encodes the
//   data window via ?d=YYYY-MM-DD and ?country= parameters, so each
//   (country, date) variant cache-keys separately. Date rollover
//   changes the URL, meaning a stale entry can never be returned for
//   the wrong day. The cache rule is the same as for geo feeds; the
//   safety argument is URL-encoded date window rather than
//   deploy-versioned geometry.
//
// Note on Vary: the ratings view emits ``Vary: Accept-Encoding`` and
// the Cache API honours Vary on match. In practice every request from
// a given browser session carries the same Accept-Encoding header (the
// UA sets it, not page JS), so cached entries hit reliably. A UA that
// somehow rotated Accept-Encoding mid-session would just cache-miss
// and re-fetch — not a correctness risk.
//
// Any new entry must be similarly safe to cache across a session.
const STATIC_PATHS = new Set([
  '/api/ratings/',
  '/api/regions.geojson',
  '/api/major-regions.geojson',
  '/api/sub-regions.geojson',
  '/api/resorts.geojson',
]);

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
  // Deliberately NOT calling self.skipWaiting() here. The new worker
  // stays "waiting" until the page posts SKIP_WAITING (the user clicked
  // "Reload" on the update banner). A waiting worker is exactly what the
  // banner means by "an update is available" — activating silently would
  // break that contract. See the message handler below.
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
      // Take control of every open client the moment we activate. This is
      // safe now precisely because we no longer auto-skipWaiting on
      // install: activation only happens after the user opts into the
      // update (SKIP_WAITING) or after every tab has closed, so claiming
      // can't drive the dev reload-loop the old design avoided. Claiming
      // fires ``controllerchange`` in the page, which sw_register.js turns
      // into exactly one reload onto the new shell — guaranteeing the tab
      // actually moves to the new version rather than lingering on the old
      // worker.
      await self.clients.claim();
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
  // The page sends this when the user clicks "Reload" on the update
  // banner. Activating the waiting worker triggers ``activate`` (and its
  // ``clients.claim()``), which hands control to this new shell.
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// ---------------------------------------------------------------------------
// Web Push (spike) — receive + click
// ---------------------------------------------------------------------------
//
// The 'push' event fires when the OS push service delivers a payload to
// this SW. We parse the JSON body the server sent (title, body, url) and
// display a notification. On click we focus an existing tab on the
// payload URL if one is already open, otherwise open a new window.

self.addEventListener('push', (event) => {
  let payload = { title: 'Snowdesk', body: '', url: '/' };
  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() };
    } catch (_err) {
      payload.body = event.data.text();
    }
  }
  const promise = self.registration.showNotification(payload.title, {
    body: payload.body,
    icon: '/static/icons/pwa/icon-192.png',
    badge: '/static/icons/pwa/icon-192.png',
    data: { url: payload.url },
    tag: 'snowdesk-push',
  });
  event.waitUntil(promise);
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = event.notification.data?.url || '/';
  const promise = (async () => {
    const all = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true,
    });
    for (const client of all) {
      if (new URL(client.url).pathname === target && 'focus' in client) {
        return client.focus();
      }
    }
    if (self.clients.openWindow) {
      return self.clients.openWindow(target);
    }
    return null;
  })();
  event.waitUntil(promise);
});
