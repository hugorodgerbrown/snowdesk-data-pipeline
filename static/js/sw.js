/*
 * static/js/sw.js — Service worker for the Snowdesk offline-map feature.
 *
 * Responsibilities:
 *   - Serve cached assets first (cache-first fetch strategy).
 *   - Accept "precache" messages from the client, chunk-fetch a manifest
 *     URL, and store each asset in a versioned cache.
 *   - Clean up caches from previous versions on activate.
 *   - Post progress and completion messages back to all clients.
 *
 * Scope: registered from /sw.js (root path), which gives it control over
 * the entire site. The ``Service-Worker-Allowed: /`` header on the response
 * makes this explicit.
 *
 * i18n: This worker never renders UI, so there are no translatable strings.
 */

'use strict';

// Cache version set when the first precache message arrives.
// Kept at module scope so activate can clean up stale versions.
let currentVersion = null;

// ---------------------------------------------------------------------------
// Lifecycle — install
// ---------------------------------------------------------------------------

self.addEventListener('install', () => {
  // Skip the "waiting" phase so the new SW takes over immediately.
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Lifecycle — activate
// ---------------------------------------------------------------------------

self.addEventListener('activate', (event) => {
  // Claim all clients so this SW controls open pages without a reload.
  event.waitUntil(
    self.clients.claim().then(async () => {
      // Delete caches whose name matches the map-shell prefix but is
      // not the current version (indicates a stale precache).
      if (!currentVersion) {
        return;
      }
      const cacheNames = await caches.keys();
      const deletions = cacheNames
        .filter((name) => name.startsWith('map-shell-') && name !== currentVersion)
        .map((name) => caches.delete(name));
      await Promise.all(deletions);
    }),
  );
});

// ---------------------------------------------------------------------------
// Fetch — cache-first with graceful offline tile fallback
// ---------------------------------------------------------------------------

// Any URL served from the OpenFreeMap origin: vector tiles, raster
// tiles, style JSON, TileJSON, sprites, and glyph PBFs. Used in the
// fetch handler to turn cache-miss-plus-network-fail into a synthetic
// 204 when offline, so MapLibre renders gracefully (parent-tile
// upscale for missing tiles; no-op for missing sprites/glyphs) instead
// of bubbling a TypeError out of ``event.respondWith`` as an
// "Uncaught (in promise)" console error.
const OFM_URL_PATTERN = /^https:\/\/tiles\.openfreemap\.org\//;

self.addEventListener('fetch', (event) => {
  event.respondWith(
    (async () => {
      const cached = await caches.match(event.request);
      if (cached) {
        return cached;
      }
      try {
        return await fetch(event.request);
      } catch (err) {
        // Offline and the network fetch failed. For OpenFreeMap URLs,
        // return a synthetic 204 so MapLibre keeps rendering (it treats
        // 204 as "no data at this URL" and falls back to parent-tile
        // upscale / skips missing sprites + glyphs). Non-OFM URLs (our
        // own API, third-party CDN, etc.) re-throw so genuine bugs stay
        // visible in DevTools.
        if (OFM_URL_PATTERN.test(event.request.url)) {
          return new Response(null, { status: 204, statusText: 'No Content' });
        }
        throw err;
      }
    })(),
  );
});

// ---------------------------------------------------------------------------
// Message — precache
// ---------------------------------------------------------------------------

/**
 * Post a message to every active client.
 *
 * @param {object} msg - The message object to broadcast.
 */
async function _broadcast(msg) {
  const clients = await self.clients.matchAll({ includeUncontrolled: true });
  clients.forEach((c) => c.postMessage(msg));
}

/**
 * Split an array into consecutive chunks of at most ``size`` elements.
 *
 * @param {Array} arr - The source array.
 * @param {number} size - Maximum elements per chunk.
 * @returns {Array[]} Array of chunk arrays.
 */
function _chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) {
    out.push(arr.slice(i, i + size));
  }
  return out;
}

self.addEventListener('message', (event) => {
  if (!event.data || event.data.type !== 'precache') {
    return;
  }

  const { manifestUrl } = event.data;

  event.waitUntil(
    (async () => {
      // --- 1. Fetch manifest ---
      let manifest;
      try {
        const resp = await fetch(manifestUrl);
        manifest = await resp.json();
      } catch (_err) {
        await _broadcast({ type: 'error', reason: 'manifest_fetch_failed' });
        return;
      }

      currentVersion = manifest.version;

      // --- 2. Open versioned cache ---
      const cache = await caches.open(manifest.version);

      // --- 3. Chunk-fetch all URLs ---
      const urls = manifest.urls;
      const total = urls.length;
      const chunks = _chunk(urls, 20);

      let totalCached = 0;
      let totalFailed = 0;

      for (const chunk of chunks) {
        const results = await Promise.allSettled(
          chunk.map((url) => cache.add(url)),
        );

        const fulfilled = results.filter((r) => r.status === 'fulfilled').length;
        const rejected = results.length - fulfilled;

        totalCached += fulfilled;
        totalFailed += rejected;

        await _broadcast({
          type: 'progress',
          cached: totalCached,
          total,
          failed: totalFailed,
        });
      }

      // --- 4. Final summary ---
      await _broadcast({
        type: 'complete',
        cached: totalCached,
        total,
        failed: totalFailed,
      });

      // --- 5. Clean up stale map-shell caches now that we have a version ---
      const cacheNames = await caches.keys();
      const deletions = cacheNames
        .filter((name) => name.startsWith('map-shell-') && name !== currentVersion)
        .map((name) => caches.delete(name));
      await Promise.all(deletions);
    })(),
  );
});
