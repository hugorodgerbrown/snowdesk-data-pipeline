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
 * Background Sync (SNOW-376)
 * ---------------------------
 * The ``sync`` event listener below is keyed on the single shared tag
 * ``static/js/mutation_queue_core.js`` exports as ``SYNC_TAG``
 * (``'mutation-queue'``), registered by ``static/js/mutation_queue.js`` at
 * enqueue-time via ``registration.sync.register(...)`` (feature-detected —
 * a no-op on browsers without ``SyncManager``, e.g. iOS Safari). Two
 * cases:
 *
 *   - A tab is still open → post ``{type: 'drain-mutations'}`` to it;
 *     ``sw_register.js``'s message bridge calls the REAL
 *     ``window.pwaMutationQueue.drain()``, which already has ``window.pwaDb``
 *     / ``window.pwaTelemetry`` wired up, rather than duplicating that
 *     logic here. This path resolves ``waitUntil`` immediately, before the
 *     page's own drain has even run — it deliberately does NOT throw to
 *     get a Background Sync reschedule if that drain leaves retryable
 *     rows; the page's own 30s timer / ``visibilitychange`` / ``online``
 *     triggers carry it forward instead (see ``_handleMutationSync``'s
 *     own docstring for the full rationale).
 *   - No tab is open (the actual Background-Sync case this API exists
 *     for) → self-drain directly against IndexedDB using the shared
 *     ``mutation_queue_core.js`` helpers, since a worker has no
 *     ``window.pwaDb``. If any row still needs a further retry after this
 *     pass, the handler THROWS so ``event.waitUntil`` rejects — that's
 *     what tells the browser to reschedule the sync per its own backoff.
 *
 * Account-change guard (SNOW-462)
 * --------------------------------
 * ``_selfDrainMutations()`` only fires with no tab open, so it cannot read
 * ``<meta name="pwa-user-id">`` — there is no page. Instead it best-effort
 * reads the last-seen principal persisted by the page in ``meta:app``
 * (key ``mutations.principal``, written by
 * ``static/js/mutation_queue.js``'s ``_reconcilePrincipal()``) and
 * discards (deletes, does not replay) any row whose stamped ``principal``
 * doesn't match it. This is a best-effort backstop, not the airtight
 * guarantee — the page-side drain-guard in ``mutation_queue.js``'s
 * ``_processRow()`` is what makes the property hold whenever a tab is
 * open; SNOW-463 is the server-side backstop that makes it hold
 * regardless.
 *
 * i18n: this worker never renders UI, so there are no translatable
 * strings.
 */

'use strict';

// SNOW-376: pure backoff/classification helpers shared with the page
// (static/js/mutation_queue.js). A worker has no <script> tags, hence
// importScripts rather than a second <script src> tag. Wrapped in
// try/catch so a transient 404 / network hiccup on this one script can't
// take down SW startup — the ``sync`` handler below falls back to a
// literal tag string when the global didn't load.
try {
  importScripts('/static/js/mutation_queue_core.js');
} catch (_importErr) {
  // Non-fatal — see fallback in the 'sync' listener below.
}

const CACHE_VERSION = 'snowdesk-shell-v12';

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
// Telemetry bridge (SNOW-384)
// ---------------------------------------------------------------------------
//
// A service worker has no ``window`` — ``window.pwaTelemetry`` (SNOW-385,
// static/js/telemetry.js) is unreachable from here. Instead we post a
// ``{type: 'pwa-telemetry', event, properties}`` message to the client(s)
// this worker controls; ``sw_register.js`` carries a matching
// ``navigator.serviceWorker.addEventListener('message', ...)`` listener
// that forwards it to ``window.pwaTelemetry.emit(event, properties)``. The
// envelope-level context fields (platform, install_state, sw_state,
// client_version, …) are attached by ``telemetry.js`` on the page side from
// ``window.pwaDb.context()`` — this helper only needs to supply the event
// name and any event-specific properties.
//
// Best-effort throughout: telemetry must never affect SW lifecycle
// behaviour, so every step here is wrapped and swallows its own errors.
//
// @param {string} eventName
// @param {object} [properties]
// @param {string} [clientId] Target a single client (e.g. the ``fetch``
//   event's originating client) rather than broadcasting to every open tab.
function _postTelemetry(eventName, properties, clientId) {
  const message = { type: 'pwa-telemetry', event: eventName, properties: properties || {} };
  const send = (client) => {
    try {
      client.postMessage(message);
    } catch (_err) {
      // Ignore — a torn-down client is not worth retrying for telemetry.
    }
  };
  try {
    if (clientId) {
      self.clients
        .get(clientId)
        .then((client) => {
          if (client) send(client);
        })
        .catch(() => {});
      return;
    }
    self.clients
      .matchAll({ includeUncontrolled: true, type: 'window' })
      .then((all) => all.forEach(send))
      .catch(() => {});
  } catch (_err) {
    // Non-fatal — telemetry must never break the SW lifecycle.
  }
}

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
      // SNOW-384: the browser fires 'install' once per successful
      // install cycle (a failed install can retry). Emitting after
      // cache.addAll() resolves means we only record successful
      // installs, so no extra idempotency guard is needed.
      _postTelemetry('pwa.sw.installed', { cache_version: CACHE_VERSION });
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
      try {
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
        // SNOW-384: the browser fires 'activate' exactly once per SW
        // instance — no extra gating needed for idempotency here.
        _postTelemetry('pwa.sw.activated', { cache_version: CACHE_VERSION });
      } catch (err) {
        // SNOW-384: pwa.sw.activation_failed is a critical event
        // (telemetry.js CRITICAL_EVENTS) — fires sendBeacon immediately
        // once forwarded by sw_register.js.
        _postTelemetry('pwa.sw.activation_failed', {
          cache_version: CACHE_VERSION,
          message: String((err && err.message) || err || 'unknown'),
        });
        throw err;
      }
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
 *
 * The offline-fallback branch does a second cache lookup with
 * ``ignoreSearch: true`` before giving up. Rationale: the map page adds
 * ``?d=YYYY-MM-DD`` client-side via ``history.replaceState`` while the
 * user scrubs the timeline (see ``static/js/map.js``), so those URLs are
 * never fetched from the server and never cached. An offline reload of
 * ``/?d=2026-01-23`` would otherwise miss the exact-URL cache lookup and
 * fall straight to ``offline.html`` even though the ``/`` shell HTML
 * (which is byte-identical for every ``?d`` value — the date is read
 * back off ``location.search`` by page-level JS) has been cached since
 * the first visit. Matching with ``ignoreSearch: true`` returns that
 * cached shell, and the page reinitialises to the requested date.
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
      const searchless = await cache.match(request, { ignoreSearch: true });
      if (searchless) return searchless;
      const fallback = await cache.match(OFFLINE_FALLBACK);
      if (fallback) return fallback;
    }
    throw err;
  }
}

/**
 * Wrap a strategy promise so an unexpected ``undefined`` / non-Response
 * resolution — which would otherwise surface to the page as a raw
 * network error with no diagnostic trail — is caught, reported via the
 * SNOW-384 telemetry bridge, and replaced with a same-request network
 * fetch so the navigation/asset load still has a chance to succeed.
 *
 * Both ``_staleWhileRevalidate`` and ``_networkFirst`` always resolve to
 * a ``Response`` today; this is a defensive guard against a future
 * regression in either function, not a documented current failure mode.
 *
 * @param {Promise<Response>} responsePromise
 * @param {Request} request
 * @param {string} [clientId]
 * @returns {Promise<Response>}
 */
async function _guardedRespond(responsePromise, request, clientId) {
  const response = await responsePromise;
  if (!response || typeof response.status !== 'number') {
    // SNOW-384: pwa.sw.fetch_undefined is a critical event — sendBeacon
    // fires immediately once sw_register.js forwards it.
    _postTelemetry(
      'pwa.sw.fetch_undefined',
      { url: request.url, mode: request.mode },
      clientId,
    );
    return fetch(request);
  }
  return response;
}

self.addEventListener('fetch', (event) => {
  const strategy = _classify(event.request);
  if (strategy === 'static') {
    event.respondWith(
      _guardedRespond(
        _staleWhileRevalidate(event.request),
        event.request,
        event.clientId,
      ),
    );
  } else if (strategy === 'navigate') {
    event.respondWith(
      _guardedRespond(
        _networkFirst(event.request),
        event.request,
        event.clientId,
      ),
    );
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
// Background Sync (SNOW-376) — mutation-queue replay
// ---------------------------------------------------------------------------

/**
 * Open (or lazily create) the shared PWA IndexedDB database directly —
 * a worker has no ``window.pwaDb``. Mirrors ``static/js/db.js``'s
 * ``DB_NAME`` exactly so it opens the SAME database a page already
 * created. It opens WITHOUT a version number so it attaches to whatever
 * schema version the page most recently migrated to (db.js owns
 * ``DB_VERSION`` — currently 2 — and bumps it as stores are added; a
 * hardcoded version here would throw ``VersionError`` the moment db.js
 * moved ahead). The ``onupgradeneeded`` branch below only fires in the
 * (rare) case a Background Sync fires before any page has ever opened the
 * DB, creating a fresh v1 DB with ONLY the one store this worker needs,
 * mirroring ``db.js::STORES['queue:mutations']``; the next page load
 * upgrades it to the full schema. The page-side ``db.js`` remains the
 * single source of truth for the full schema; see
 * docs/indexeddb-scaffolding.md.
 *
 * @returns {Promise<IDBDatabase>}
 */
function _openMutationsDb() {
  return new Promise((resolve, reject) => {
    let req;
    try {
      req = indexedDB.open('snowdesk-pwa-v1');
    } catch (err) {
      reject(err);
      return;
    }
    req.onupgradeneeded = (evt) => {
      const db = evt.target.result;
      if (!db.objectStoreNames.contains('queue:mutations')) {
        db.createObjectStore('queue:mutations', { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('idb_open_failed'));
    req.onblocked = () => reject(new Error('idb_blocked'));
  });
}

function _idbGetAll(db, storeName) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readonly');
    const req = tx.objectStore(storeName).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error || new Error('getAll failed'));
  });
}

function _idbPut(db, storeName, value) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    const req = tx.objectStore(storeName).put(value);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('put failed'));
  });
}

function _idbDelete(db, storeName, key) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    const req = tx.objectStore(storeName).delete(key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error || new Error('delete failed'));
  });
}

/**
 * One-pass replay of every eligible ``queue:mutations`` row, run directly
 * against IndexedDB (no open tab to delegate to — see the tag-handler
 * below for the has-a-tab fast path). Uses the SAME classification /
 * backoff rules as the page (``self.pwaMutationQueueCore``), and sends
 * the identical ``Idempotency-Key`` header per row.
 *
 * Throws if any row still needs a further retry once this pass completes,
 * so the caller's ``event.waitUntil`` rejects and the browser reschedules
 * the Background Sync per its own backoff — per spec, a fulfilled
 * ``waitUntil`` tells the browser the sync succeeded and it stops
 * retrying, which would silently strand a row still in backoff.
 *
 * @returns {Promise<void>}
 */
async function _selfDrainMutations() {
  // Fall back to a hand-rolled version of the shared helpers if
  // importScripts failed at startup — keeps this path self-contained
  // rather than a hard dependency on the earlier try/catch succeeding.
  const core = self.pwaMutationQueueCore || {
    MAX_ATTEMPTS: 20,
    backoffDelayMs: (attempts) => Math.min(Math.pow(2, attempts), 300) * 1000,
    classifyStatus: (status) => {
      if (status >= 200 && status < 300) return 'success';
      if (status === 408 || status === 429) return 'retry';
      if (status >= 500) return 'retry';
      if (status >= 400) return 'permanent';
      return 'retry';
    },
    isRowEligible: (row, now) =>
      !!row && row.status !== 'failed' && (row.next_attempt_at || 0) <= now,
  };

  const db = await _openMutationsDb();
  let rows;
  try {
    rows = await _idbGetAll(db, 'queue:mutations');
  } catch (_e) {
    // Store may not exist yet on a brand-new DB — nothing to drain.
    rows = [];
  }

  // SNOW-462: best-effort tab-closed principal guard — see the header
  // comment's "Account-change guard" section. ``meta:app`` may not exist
  // on a worker-created DB (the onupgradeneeded branch above only
  // creates ``queue:mutations``); the try/catch below handles that by
  // leaving ``storedPrincipal`` undefined, which skips the guard entirely
  // rather than discarding every row.
  let storedPrincipal;
  try {
    const meta = await _idbGetAll(db, 'meta:app');
    const row = meta.find((r) => r.key === 'mutations.principal');
    storedPrincipal = row ? row.value : undefined;
  } catch (_e) {
    storedPrincipal = undefined;
  }

  const now = Date.now();
  let successCount = 0;
  let retryableRemaining = false;

  for (const row of rows) {
    // SNOW-462: discard (do not replay) a row stamped for a different
    // principal than the last one the page saw. Skipped entirely when
    // storedPrincipal is undefined (meta:app unavailable) rather than
    // guessing.
    if (storedPrincipal !== undefined && row.principal !== storedPrincipal) {
      await _idbDelete(db, 'queue:mutations', row.id);
      continue;
    }

    if (!core.isRowEligible(row, now)) {
      if (row.status !== 'failed') retryableRemaining = true;
      continue;
    }

    let outcome;
    try {
      const headers = Object.assign({}, row.headers || {}, {
        'Idempotency-Key': row.idempotency_key,
      });
      const response = await fetch(row.url, {
        method: row.method,
        headers,
        body: row.body != null ? row.body : undefined,
      });
      outcome = core.classifyStatus(response.status);
    } catch (_networkErr) {
      outcome = 'retry';
    }

    if (outcome === 'success') {
      await _idbDelete(db, 'queue:mutations', row.id);
      successCount += 1;
      continue;
    }

    if (outcome === 'permanent') {
      // This attempt just happened — increment before persisting/
      // reporting so `attempts` (and the telemetry it feeds) reflects the
      // failed attempt rather than reading as "never attempted".
      row.attempts = (row.attempts || 0) + 1;
      row.status = 'failed';
      await _idbPut(db, 'queue:mutations', row);
      _postTelemetry('pwa.mutation.failed_permanent', {
        method: row.method,
        url: row.url,
        idempotency_key: row.idempotency_key,
        attempts: row.attempts,
        reason: 'permanent_4xx',
      });
      continue;
    }

    // 'retry'
    const attempts = (row.attempts || 0) + 1;
    row.attempts = attempts;
    if (attempts >= core.MAX_ATTEMPTS) {
      row.status = 'failed';
      await _idbPut(db, 'queue:mutations', row);
      _postTelemetry('pwa.mutation.failed_permanent', {
        method: row.method,
        url: row.url,
        idempotency_key: row.idempotency_key,
        reason: 'max_attempts',
      });
    } else {
      row.status = 'retry-scheduled';
      row.next_attempt_at = now + core.backoffDelayMs(attempts);
      await _idbPut(db, 'queue:mutations', row);
      retryableRemaining = true;
    }
  }

  try {
    db.close();
  } catch (_e) {
    // Non-fatal.
  }

  if (successCount > 0) {
    _postTelemetry('pwa.mutation.drained', { count: successCount, source: 'background_sync' });
  }

  if (retryableRemaining) {
    throw new Error('mutation_queue_retry_pending');
  }
}

/**
 * Dispatch one Background Sync firing for the mutation-queue tag: prefer
 * delegating to an open tab (cheaper, reuses the page's already-wired
 * ``window.pwaDb`` / ``window.pwaTelemetry``); self-drain directly against
 * IndexedDB only when no tab is open at all.
 *
 * Asymmetric retry-rescheduling by design: the no-tab path below (
 * ``_selfDrainMutations``) throws when retryable rows remain, so
 * ``event.waitUntil`` rejects and the browser reschedules the sync itself.
 * The has-open-tabs path here does NOT do that — it resolves as soon as
 * the message is posted, before the page's own ``drain()`` has even run,
 * so there's nothing yet to inspect for "does this need a Background Sync
 * reschedule?". If that drain leaves retryable rows (still-backing-off
 * 5xx/429s), the page's OWN lifecycle triggers — the 30s periodic timer,
 * ``visibilitychange``, and the next ``online`` event — are what carry it
 * forward, not another Background Sync firing. That's an acceptable gap
 * (a tab is open, so those triggers are live), not an oversight.
 *
 * @returns {Promise<void>}
 */
async function _handleMutationSync() {
  const clientsList = await self.clients.matchAll({
    includeUncontrolled: true,
    type: 'window',
  });
  if (clientsList.length > 0) {
    clientsList.forEach((client) => {
      try {
        client.postMessage({ type: 'drain-mutations' });
      } catch (_e) {
        // A torn-down client — not worth retrying for this one message.
      }
    });
    return;
  }
  await _selfDrainMutations();
}

self.addEventListener('sync', (event) => {
  const tag = self.pwaMutationQueueCore ? self.pwaMutationQueueCore.SYNC_TAG : 'mutation-queue';
  if (event.tag !== tag) return;
  event.waitUntil(_handleMutationSync());
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
  // SNOW-384: pwa.push.received is the client half of the push funnel
  // (server half: pwa.push.sent / pwa.push.gone_410 in push_service.py).
  // One 'push' event = one occurrence, so this fires exactly once per
  // delivered message — naturally idempotent.
  _postTelemetry('pwa.push.received', { url: payload.url });
  const promise = self.registration
    .showNotification(payload.title, {
      body: payload.body,
      icon: '/static/icons/pwa/icon-192.png',
      badge: '/static/icons/pwa/icon-192.png',
      data: { url: payload.url },
      tag: 'snowdesk-push',
    })
    .then(() => {
      // SNOW-384: emitted only after showNotification's promise
      // resolves, so a suppressed/failed notification (denied
      // permission, browser quirk) does not falsely report "shown".
      _postTelemetry('pwa.push.shown', { url: payload.url });
    });
  event.waitUntil(promise);
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = event.notification.data?.url || '/';
  // SNOW-384: one click = one occurrence. Emitted unconditionally on
  // click, ahead of the focus/openWindow race below, so the signal
  // isn't lost if the focus/navigate branch throws.
  _postTelemetry('pwa.push.opened', { url: target });
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
