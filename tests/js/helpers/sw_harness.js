/*
 * tests/js/helpers/sw_harness.js — the sandbox static/js/sw.js is tested in.
 *
 * SNOW-1314 lifted this out of tests/js/test_sw.js so a second file —
 * tests/js/test_offline_warm_serves.js — runs on the same harness rather
 * than a copy of it. Not a test module: vitest.config.mjs only collects
 * ``tests/js/<any depth>/test_*.js``, and this file's name does not match.
 *
 * Loading strategy
 * ----------------
 * ``sw.js`` is a classic worker script, not a module: its helpers are
 * function declarations in script scope with no exports, and importing it
 * as an ES module would put them in module scope where nothing can reach
 * them. So the source is read off disk and evaluated inside a
 * ``new Function`` sandbox whose ``self``, ``caches`` and ``fetch`` are
 * supplied as parameters — the same real code that ships, with the worker
 * globals jsdom does not provide passed in rather than stubbed onto the
 * page's global. ``importScripts`` is left undefined on purpose: the
 * resulting ``ReferenceError`` is caught by ``sw.js``'s own try/catch and
 * puts it on the inline-fallback path. Passing a ``core`` takes the
 * delegating path instead.
 *
 * ``indexedDB`` is NOT passed in — the sandbox resolves it to the
 * fake-indexeddb instance ``tests/js/setup.js`` registers, so
 * ``_currentPrincipal()`` reads a real ``meta:app`` row.
 *
 * Every listener ``sw.js`` registers is captured on ``__listeners`` (the
 * ``fetch``, ``install``, ``activate``, ``message`` … handlers), so a test
 * dispatches a synthetic event at the real code.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { vi } from 'vitest';


// Resolved off ``process.cwd()`` rather than ``import.meta.url``: under the
// jsdom environment that URL is an ``http:`` one Vitest serves the module
// from, which ``fileURLToPath`` rejects. Vitest's root is the repo root
// (vitest.config.mjs sits there), so cwd is stable.
export const SW_SOURCE = readFileSync(join(process.cwd(), 'static', 'js', 'sw.js'), 'utf8');

export const ORIGIN = 'https://snowdesk.example';
export const DB_NAME = 'snowdesk-pwa-v1';

// The helpers the sandbox hands back. Everything else in sw.js stays
// private to it, as it is in the shipped worker.
export const SW_EXPORTS = [
  '_warmCacheWorseReason',
  '_networkFirst',
  '_principalFromHtml',
  '_isNoStore',
  '_pinnedCacheNames',
  '_invalidatePinnedCacheNames',
  '_basemapStaleWhileRevalidate',
  '_warmCache',
  '_isHtmlResponse',
  // SNOW-912: the shell re-warm — what a page needs to open, and the
  // activation-time call that puts it back after the old cache is reaped.
  '_shellSubresources',
  '_warmShellSubresources',
  '_rewarmShell',
  'SHELL_PAGE',
  // SNOW-1047: the legacy `/?<map state>` redirect the worker answers itself.
  '_legacyRootMapUrl',
  // SNOW-1235: navigations left to the browser so they keep Sec-Fetch-Dest.
  '_isSessionRedirect',
  'SHELL_SUBRESOURCE_LIMIT',
  // SNOW-930: the shell pages the activation walks.
  'SHELL_PAGES',
  // SNOW-912: the feeds the warmed page's own boot will ask for. Warmed
  // with it, or the map opens grey.
  '_shellPageDay',
  '_shellBootFeeds',
  '_warmShellFeeds',
  'BASEMAP_CACHE_TRIM_INTERVAL',
  'BASEMAP_CACHE_MAX_ENTRIES',
  // SNOW-1060: the backstop for the documents the tile-only trim exempts.
  'BASEMAP_CACHE_MAX_DOCUMENTS',
  '_INLINE_MUTATION_QUEUE_CORE',
  'PRINCIPAL_ANONYMOUS',
  'PRINCIPAL_UNKNOWN',
  'PRINCIPAL_HEADER',
  // SNOW-632: cancellation-protocol state and helpers.
  '_warmCacheCancelledIds',
  '_markWarmCacheCancelled',
  '_clearWarmCacheCancelled',
  '_handleWarmCacheCancelMessage',
  'WARM_CACHE_CANCEL_SET_MAX',
  // SNOW-748: the in-flight half of that protocol — what there is to cancel
  // when the user forces offline mode mid-download.
  '_warmCacheActiveIds',
  '_markWarmCacheActive',
  '_clearWarmCacheActive',
  // SNOW-722: the cross-origin routing fix — classification, its
  // IndexedDB rehydration, and the two cache searches behind it.
  '_classifyCrossOriginGet',
  '_hydrateBasemapOrigins',
  '_searchPinnedBuckets',
  '_readOnlyBasemapCacheProbe',
  'BASEMAP_CACHE',
  'BASEMAP_PINNED_CACHE_PREFIX',
  'BASEMAP_HYDRATION_MAX_ATTEMPTS',
  // SNOW-742: the read-path budgets and the offline latch. ``_networkMode``
  // itself is deliberately NOT exported — it is a ``let``, so a test would
  // capture its value at load time and never see a change. ``_shouldUseNetwork``
  // is the real predicate every read path consults, so asserting on it tests
  // the thing that actually gates behaviour rather than a mirror of it.
  '_staleWhileRevalidate',
  // SNOW-846: the request ledger. ``_flushDebugLog`` is handed back so a
  // test can drain the buffer deterministically rather than waiting out
  // ``DEBUG_LOG_FLUSH_MS``; ``_debugLogPending`` deliberately is NOT, for
  // the same reason ``_networkMode`` is not — it is a ``let`` the flush
  // reassigns, so a captured reference would go stale on the first drain.
  '_flushDebugLog',
  '_boundedFetch',
  '_shouldUseNetwork',
  '_latchOffline',
  // SNOW-748: the user-forced offline mode, which is the one that is never
  // probed. ``_shouldUseNetwork`` cannot tell it apart from an auto-latch (both
  // are false), so the mode itself is read back through the ``network-mode``
  // message handler's direct reply — see ``readNetworkMode`` below.
  '_forceOffline',
  '_unlatchOffline',
  // SNOW-748: the worker recovering that mode for itself after a restart, the
  // fix for a forced mode being lost when Chrome recycles an idle worker.
  '_hydrateNetworkMode',
  '_probeNetwork',
  // SNOW-922: "would a navigation open with no network?", which the page
  // side asks before it lets the Offline mode switch strand the device.
  // Answered here rather than on the page because this worker is the thing
  // that will or will not serve that navigation.
  '_canOpenOffline',
  // SNOW-852: the synchronous fast-path predicate the fetch handler's
  // network-only branch consults. A `function`, not a `let`, so handing it
  // back is safe where `_networkMode` is not — it reads the live value on
  // every call rather than capturing one at load time.
  '_mayPassThrough',
  // SNOW-852: the Background-Sync drain, which needs its own offline guard
  // because a worker's own fetch() never fires its own fetch event.
  '_selfDrainMutations',
  // SNOW-859: the wrapper's own recovery re-fetch — the last fetch call site
  // in the file that did not consult the mode.
  '_guardedRespond',
  'NAVIGATION_FETCH_BUDGET_MS',
  'SHELL_FETCH_BUDGET_MS',
  'BASEMAP_FETCH_BUDGET_MS',
  'OFFLINE_LATCH_THRESHOLD',
  'OFFLINE_PROBE_URL',
  'OFFLINE_PROBE_BACKOFF_MS',
  // SNOW-965: the two network-first JSON classes and the principal read
  // the private one stamps with.
  '_classifySync',
  '_signedInPrincipal',
  '_privateJsonNetworkFirst',
  '_publicJsonNetworkFirst',
  // SNOW-1312: the map's region sheet, read offline on path + `d`.
  '_publicPartialNetworkFirst',
  '_publicPartialKey',
  // SNOW-1313: a saved pin's place sheet, kept for its owner on path + `d`.
  '_privatePartialNetworkFirst',
  // SNOW-1311: the activation-time carry of the user's content out of the
  // old shell caches, and its budget.
  '_carryShellContent',
  'SHELL_CARRY_BUDGET_MS',
];

/**
 * Evaluate sw.js in a sandbox and return its internals.
 *
 * @param {object} [options]
 * @param {object|null} [options.core] - value for ``self.pwaBasemapCacheCore``;
 *   omit to exercise the inline fallbacks.
 * @param {object} [options.caches] - CacheStorage stub.
 * @param {Function} [options.fetch] - fetch stub.
 * @param {object[]} [options.clients] - client stubs ``self.clients.matchAll``
 *   resolves with, so a test can assert on what the worker broadcasts
 *   (SNOW-748's hydration publishes the mode it recovered). Defaults to none,
 *   which is the ordinary case of a worker with every tab closed.
 * @param {Function} [options.showNotification] - stub for
 *   ``self.registration.showNotification``, so a test can assert on the
 *   options the ``push`` handler passes it (SNOW-874).
 * @returns {object} The helpers named in ``SW_EXPORTS``.
 */
export function loadSw(options = {}) {
  const listeners = {};
  const selfStub = {
    location: { origin: ORIGIN },
    // SNOW-722: captured rather than discarded, so a test can dispatch a
    // synthetic ``fetch`` event at the real listener and exercise the
    // cross-origin routing decision end to end.
    addEventListener: (type, handler) => {
      listeners[type] = handler;
    },
    clients: {
      get: () => Promise.resolve(null),
      matchAll: () => Promise.resolve(options.clients || []),
      // SNOW-1311: ``activate`` claims, and the carry tests dispatch it.
      claim: () => Promise.resolve(),
    },
    registration: {
      showNotification: options.showNotification || (() => Promise.resolve()),
    },
  };
  if (options.core) selfStub.pwaBasemapCacheCore = options.core;
  const factory = new Function(
    'self',
    'caches',
    'fetch',
    `${SW_SOURCE}\nreturn { ${SW_EXPORTS.join(', ')} };`,
  );
  const api = factory(
    selfStub,
    options.caches || makeCaches(),
    options.fetch || (() => Promise.reject(new TypeError('Failed to fetch'))),
  );
  api.__listeners = listeners;
  return api;
}

/**
 * A ``Response``-alike whose ``type`` reads ``'basic'``. A constructed
 * ``Response`` reports ``'default'``, and ``type`` is a read-only getter,
 * so ``_networkFirst``'s same-origin test would reject every fixture
 * without this wrapper. Each ``clone()`` builds a fresh ``Response`` over
 * the same body string, which is what a real clone gives the caller.
 *
 * @param {string} body
 * @param {object} [init]
 * @returns {object}
 */
export function basicResponse(body, init = {}) {
  const build = () => {
    const real = new Response(body, {
      status: init.status || 200,
      headers: init.headers || {},
    });
    return {
      ok: real.ok,
      status: real.status,
      statusText: real.statusText,
      headers: real.headers,
      type: 'basic',
      get body() {
        return real.body;
      },
      text: () => real.text(),
      arrayBuffer: () => real.arrayBuffer(),
      clone: () => build(),
    };
  };
  return build();
}

/**
 * An in-memory CacheStorage covering the surface ``_networkFirst`` uses.
 * ``put`` buffers the body so a stored entry can be matched more than
 * once, mirroring a real Cache.
 *
 * @returns {object}
 */
export function makeCaches() {
  const buckets = new Map();
  // SNOW-722: ``open`` and ``match`` are counted per bucket name as well,
  // because the read-only cross-origin probe's cost bound is a claim about
  // exactly those two — "one BASEMAP_CACHE lookup, and the pinned walk only
  // when something is pinned". Counting only ``keys`` let the docstring
  // overstate the guard for a release without a test noticing.
  const counters = { keys: 0, open: 0, match: 0, openedNames: [], matchedNames: [] };
  const entriesFor = (name) => {
    if (!buckets.has(name)) buckets.set(name, new Map());
    return buckets.get(name);
  };
  return {
    open: async (name) => {
      counters.open += 1;
      counters.openedNames.push(name);
      const entries = entriesFor(name);
      return {
        async match(request, matchOptions) {
          counters.match += 1;
          counters.matchedNames.push(name);
          const url = typeof request === 'string' ? request : request.url;
          if (entries.has(url)) return entries.get(url).clone();
          if (matchOptions && matchOptions.ignoreSearch) {
            const bare = url.split('?')[0];
            for (const [key, value] of entries) {
              if (key.split('?')[0] === bare) return value.clone();
            }
          }
          return undefined;
        },
        // SNOW-1311: the carry walks an old shell cache's entries. A
        // Request-alike per url, as a real Cache's ``keys()`` answers.
        async keys() {
          return [...entries.keys()].map((url) => ({ url, method: 'GET' }));
        },
        // SNOW-965: a trip route the server says is no longer yours is
        // dropped from the shell cache.
        async delete(request) {
          const url = typeof request === 'string' ? request : request.url;
          return entries.delete(url);
        },
        async put(request, response) {
          const url = typeof request === 'string' ? request : request.url;
          // SNOW-1072: a null-body status (the class tileset's 204) cannot
          // be rebuilt around a body — the Response constructor throws —
          // and a real Cache stores it without one.
          const nullBody = [101, 204, 205, 304].includes(response.status);
          entries.set(
            url,
            new Response(nullBody ? null : await response.arrayBuffer(), {
              status: response.status,
              headers: response.headers,
            }),
          );
        },
      };
    },
    // SNOW-613: counted, because "how many times was the bucket list
    // enumerated?" is the property the memo exists to change.
    async keys() {
      counters.keys += 1;
      return [...buckets.keys()];
    },
    async delete(name) {
      return buckets.delete(name);
    },
    seed(name, url, response) {
      entriesFor(name).set(url, response);
    },
    size(name) {
      return entriesFor(name).size;
    },
    counters,
    has(name) {
      return buckets.has(name);
    },
  };
}

/** A navigation request, as the fetch listener hands one to _networkFirst. */
export function navRequest(path) {
  return { url: ORIGIN + path, method: 'GET', mode: 'navigate', destination: 'document' };
}

/** Page HTML carrying the ``pwa-user-id`` meta tag base.html renders. */
export function pageHtml(userId, marker) {
  return (
    '<!doctype html><html><head>' +
    `<meta name="pwa-user-id" content="${userId}">` +
    `</head><body>${marker}</body></html>`
  );
}

/**
 * Install fake timers that leave ``setImmediate`` alone.
 *
 * SNOW-748: ``_shouldUseNetwork`` and ``_warmCache`` now await an IndexedDB
 * read (``_hydrateNetworkMode``), and fake-indexeddb schedules its requests on
 * ``setImmediate`` — which Vitest's default ``toFake`` list replaces. Under the
 * full set no IDB request ever completes, so every one of those awaits hangs
 * until the test times out. Faking the four timer functions the read budgets
 * and the probe backoff actually use, plus ``Date``, keeps every existing
 * ``advanceTimersByTimeAsync`` assertion working while letting the database
 * run.
 */
export function useTimersLeavingIndexedDb() {
  vi.useFakeTimers({
    toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'],
  });
}

/**
 * Settle the SNOW-748 network-mode hydration before a test drives the clock.
 *
 * Every read path now awaits that IndexedDB read BEFORE it starts its fetch,
 * so a test that advances straight to a budget expiry would advance past a
 * timer that has not been scheduled yet, and then hang waiting for a clock
 * that has already moved. Awaiting the memoised read first puts the sandbox in
 * the state a worker that has served one request is already in.
 *
 * @param {object} sw
 * @returns {Promise<void>}
 */
export function settleHydration(sw) {
  return sw._hydrateNetworkMode();
}

/**
 * Read the worker's mode back through the ``network-mode`` handler's direct
 * reply to the sender. ``_networkMode`` is a ``let`` and deliberately not
 * exported, and ``_shouldUseNetwork`` collapses the two offline values into
 * one boolean — this reply is the same channel the page itself learns the mode
 * on, so asserting on it tests what a client would actually see.
 *
 * ``'query'`` is deliberately not one of the three modes: it changes nothing,
 * and (since SNOW-748) does not count as a page asserting a mode, so reading
 * the mode cannot suppress the hydration under test.
 *
 * Awaited rather than read synchronously, because the handler now waits for
 * the persisted row before it answers — the fix for a recycled worker replying
 * ``'auto'`` over a forced mode it had not read yet.
 *
 * @param {object} sw
 * @returns {Promise<string>}
 */
export function readNetworkMode(sw) {
  return new Promise((resolve) => {
    sw.__listeners.message({
      data: { type: 'network-mode', mode: 'query' },
      source: {
        postMessage: (data) => resolve(data.mode),
      },
    });
  });
}

/** Let the fire-and-forget cache write settle before asserting on it. */
export function flush() {
  return new Promise((resolve) => setTimeout(resolve, 10));
}

/** Recreate the PWA IndexedDB with the two stores sw.js reads. */
export function resetDb() {
  return new Promise((resolve, reject) => {
    const del = indexedDB.deleteDatabase(DB_NAME);
    del.onerror = () => reject(del.error);
    del.onsuccess = () => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        db.createObjectStore('queue:mutations', { keyPath: 'id', autoIncrement: true });
        db.createObjectStore('meta:app', { keyPath: 'key' });
      };
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        req.result.close();
        resolve();
      };
    };
  });
}

/**
 * Write the ``mutations.principal`` row static/js/mutation_queue.js's
 * ``_reconcilePrincipal()`` maintains — the worker's only read-side signal
 * for who is signed in now.
 *
 * @param {string|null} value
 * @returns {Promise<void>}
 */
export function setStoredPrincipal(value) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onerror = () => reject(req.error);
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction('meta:app', 'readwrite');
      tx.objectStore('meta:app').put({ key: 'mutations.principal', value });
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error);
      };
    };
  });
}

/**
 * Write the ``network.mode`` row static/js/pwa_offline.js persists on every
 * mode change — the only record of the user's choice that outlives a recycled
 * worker (SNOW-748).
 *
 * @param {string} value
 * @returns {Promise<void>}
 */
export function setStoredNetworkMode(value) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onerror = () => reject(req.error);
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction('meta:app', 'readwrite');
      tx.objectStore('meta:app').put({ key: 'network.mode', value });
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error);
      };
    };
  });
}

/**
 * Recreate the PWA IndexedDB the way the WORKER creates it when a Background
 * Sync fires before any page has opened it: one ``queue:mutations`` store and
 * no ``meta:app`` at all (see ``_openMutationsDb()``'s ``onupgradeneeded``).
 * Every ``meta:app`` read against it throws, which is the unreadable case the
 * hydration has to survive.
 *
 * @returns {Promise<void>}
 */
export function resetDbWithoutMetaStore() {
  return new Promise((resolve, reject) => {
    const del = indexedDB.deleteDatabase(DB_NAME);
    del.onerror = () => reject(del.error);
    del.onsuccess = () => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore('queue:mutations', { keyPath: 'id', autoIncrement: true });
      };
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        req.result.close();
        resolve();
      };
    };
  });
}
