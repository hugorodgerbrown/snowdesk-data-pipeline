/*
 * tests/js/test_sw.js — Vitest unit tests for static/js/sw.js.
 *
 * Two findings from docs/code-reviews/2026-08-03-js-review.md:
 *
 *   C1 — ``_networkFirst`` cached authenticated navigations with nothing
 *        recording who they were rendered for, so an offline navigation
 *        after a sign-out replayed the previous user's page. Covered here
 *        at the strategy level (the ``no-store`` skip, the
 *        ``X-SW-Principal`` stamp, and the principal check on the two
 *        request-matched reads). The browser journey lived in
 *        tests/e2e/test_offline_account_principal.py until SNOW-649
 *        retired it, so this file is the whole of the automated coverage.
 *   D3 — ``_warmCacheWorseReason``'s inline fallback ranked by argument
 *        order rather than by ``basemap_cache_core.js``'s
 *        ``REASON_PRECEDENCE``. The table below runs both implementations
 *        over the same inputs, so a future drift in either fails here.
 *
 * Plus SNOW-613's pinned-bucket memoisation: the enumeration is now cached
 * with an explicit invalidation contract, and the offline lookup searches
 * the buckets in parallel. Both are covered here, including the invariant
 * that matters most — an invalidated list is re-read, because a stale name
 * handed to ``caches.open`` would recreate a bucket the user deleted.
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
 * puts it on the inline-fallback path, which is exactly the condition D3
 * is about. Passing a ``core`` takes the delegating path instead.
 *
 * ``indexedDB`` is NOT passed in — the sandbox resolves it to the
 * fake-indexeddb instance ``tests/js/setup.js`` registers, so
 * ``_currentPrincipal()`` reads a real ``meta:app`` row.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/basemap_cache_core.js';
import '../../static/js/mutation_queue_core.js';
// SNOW-912: the audit core's own subresource extractor, held to the same
// answers as sw.js's — see 'agrees with the audit core' below.
import '../../static/js/offline_audit_core.js';

const core = self.pwaBasemapCacheCore;
const coreQueue = self.pwaMutationQueueCore;

// Resolved off ``process.cwd()`` rather than ``import.meta.url``: under the
// jsdom environment that URL is an ``http:`` one Vitest serves the module
// from, which ``fileURLToPath`` rejects. Vitest's root is the repo root
// (vitest.config.mjs sits there), so cwd is stable.
const SW_SOURCE = readFileSync(join(process.cwd(), 'static', 'js', 'sw.js'), 'utf8');

const ORIGIN = 'https://snowdesk.example';
const DB_NAME = 'snowdesk-pwa-v1';

// The helpers the sandbox hands back. Everything else in sw.js stays
// private to it, as it is in the shipped worker.
const SW_EXPORTS = [
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
  'SHELL_SUBRESOURCE_LIMIT',
  // SNOW-912: the feeds the warmed page's own boot will ask for. Warmed
  // with it, or the map opens grey.
  '_shellPageDay',
  '_shellBootFeeds',
  '_warmShellFeeds',
  'BASEMAP_CACHE_TRIM_INTERVAL',
  'BASEMAP_CACHE_MAX_ENTRIES',
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
function loadSw(options = {}) {
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
function basicResponse(body, init = {}) {
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
function makeCaches() {
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
        async put(request, response) {
          const url = typeof request === 'string' ? request : request.url;
          entries.set(
            url,
            new Response(await response.arrayBuffer(), {
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
function navRequest(path) {
  return { url: ORIGIN + path, method: 'GET', mode: 'navigate', destination: 'document' };
}

/** Page HTML carrying the ``pwa-user-id`` meta tag base.html renders. */
function pageHtml(userId, marker) {
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
function useTimersLeavingIndexedDb() {
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
function settleHydration(sw) {
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
function readNetworkMode(sw) {
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
function flush() {
  return new Promise((resolve) => setTimeout(resolve, 10));
}

/** Recreate the PWA IndexedDB with the two stores sw.js reads. */
function resetDb() {
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
function setStoredPrincipal(value) {
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
function setStoredNetworkMode(value) {
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
function resetDbWithoutMetaStore() {
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

// ---------------------------------------------------------------------------
// D3 — the inline worseReason fallback must rank like the core module
// ---------------------------------------------------------------------------

describe('_warmCacheWorseReason inline fallback (D3)', () => {
  // Every ordered pair over the three known reasons plus the two
  // not-a-reason inputs worseReason has to tolerate. Includes both
  // argument orders, which is where the old ``a || b || null`` fallback
  // disagreed with the core.
  const REASONS = ['quota', 'network', 'other', null, undefined];
  const PAIRS = REASONS.flatMap((a) => REASONS.map((b) => [a, b]));

  it('agrees with basemap_cache_core.worseReason on every input pair', () => {
    const sw = loadSw();
    const disagreements = PAIRS.filter(
      ([a, b]) => sw._warmCacheWorseReason(a, b) !== core.worseReason(a, b),
    ).map(([a, b]) => `(${String(a)}, ${String(b)})`);
    expect(disagreements).toEqual([]);
  });

  it('keeps quota over network whichever side it arrives on', () => {
    const sw = loadSw();
    expect(sw._warmCacheWorseReason('network', 'quota')).toBe('quota');
    expect(sw._warmCacheWorseReason('quota', 'network')).toBe('quota');
  });

  it('still delegates to the core module when importScripts succeeded', () => {
    const sw = loadSw({ core });
    expect(sw._warmCacheWorseReason('network', 'quota')).toBe('quota');
  });
});

// ---------------------------------------------------------------------------
// C1 — no authenticated page HTML in the shell cache without a principal
// ---------------------------------------------------------------------------

describe('_principalFromHtml (C1)', () => {
  const sw = loadSw();

  it('reads the account uuid out of the pwa-user-id meta tag', () => {
    expect(sw._principalFromHtml(pageHtml('acct-uuid-a', 'x'))).toBe('acct-uuid-a');
  });

  it('reads an empty meta tag as anonymous', () => {
    expect(sw._principalFromHtml(pageHtml('', 'x'))).toBe(sw.PRINCIPAL_ANONYMOUS);
  });

  it('reads a page with no meta tag at all as unknown', () => {
    expect(sw._principalFromHtml('<html><body>admin</body></html>')).toBe(
      sw.PRINCIPAL_UNKNOWN,
    );
  });
});

describe('_isNoStore (C1)', () => {
  const sw = loadSw();

  it('matches a no-store directive among others', () => {
    expect(
      sw._isNoStore(basicResponse('', { headers: { 'Cache-Control': 'private, no-store' } })),
    ).toBe(true);
    expect(
      sw._isNoStore(
        basicResponse('', {
          headers: { 'Cache-Control': 'max-age=0, no-cache, no-store, must-revalidate, private' },
        }),
      ),
    ).toBe(true);
  });

  it('does not match a directive that merely contains the word', () => {
    expect(
      sw._isNoStore(basicResponse('', { headers: { 'Cache-Control': 'no-store-ish' } })),
    ).toBe(false);
    expect(sw._isNoStore(basicResponse('', { headers: { 'Cache-Control': 'no-cache' } }))).toBe(
      false,
    );
    expect(sw._isNoStore(basicResponse(''))).toBe(false);
  });
});

describe('_networkFirst principal partitioning (C1)', () => {
  beforeEach(async () => {
    await resetDb();
  });

  it('does not cache a response declaring Cache-Control: no-store', async () => {
    const caches = makeCaches();
    const online = basicResponse(pageHtml('acct-uuid-a', 'account dashboard'), {
      headers: { 'Cache-Control': 'private, no-store' },
    });
    const sw = loadSw({ caches, fetch: () => Promise.resolve(online) });

    await sw._networkFirst(navRequest('/account/'));
    await flush();

    expect(caches.size('snowdesk-shell-UNSUBSTITUTED')).toBe(0);
  });

  it('caches an ordinary navigation, stamped with the rendering principal', async () => {
    const caches = makeCaches();
    const online = basicResponse(pageHtml('acct-uuid-a', 'account dashboard'));
    const sw = loadSw({ caches, fetch: () => Promise.resolve(online) });

    await sw._networkFirst(navRequest('/account/'));
    await flush();

    const cache = await caches.open('snowdesk-shell-UNSUBSTITUTED');
    const stored = await cache.match(navRequest('/account/'));
    expect(stored.headers.get(sw.PRINCIPAL_HEADER)).toBe('acct-uuid-a');
  });

  it('refuses a cached page rendered for a different principal', async () => {
    const caches = makeCaches();
    const request = navRequest('/account/');
    let online = basicResponse(pageHtml('acct-uuid-a', 'a@example.com'));
    const sw = loadSw({
      caches,
      fetch: () => (online ? Promise.resolve(online) : Promise.reject(new TypeError('offline'))),
    });
    caches.seed(
      'snowdesk-shell-UNSUBSTITUTED',
      '/static/offline.html',
      new Response("<h1>This page isn't available offline</h1>"),
    );

    // Signed in as A: the page is fetched, cached, and the page's own
    // reconcile persists A as the principal.
    await sw._networkFirst(request);
    await flush();
    await setStoredPrincipal('acct-uuid-a');

    // Sign out, then go offline. The reconcile has run on the sign-out
    // redirect, so the stored principal is anonymous again.
    await setStoredPrincipal(null);
    online = null;

    const offline = await sw._networkFirst(request);
    const body = await offline.text();
    expect(body).not.toContain('a@example.com');
    expect(body).toContain("This page isn't available offline");
  });

  it('serves a cached page back to the principal it was rendered for', async () => {
    const caches = makeCaches();
    const request = navRequest('/account/');
    let online = basicResponse(pageHtml('acct-uuid-a', 'a@example.com'));
    const sw = loadSw({
      caches,
      fetch: () => (online ? Promise.resolve(online) : Promise.reject(new TypeError('offline'))),
    });

    await sw._networkFirst(request);
    await flush();
    await setStoredPrincipal('acct-uuid-a');
    online = null;

    const offline = await sw._networkFirst(request);
    expect(await offline.text()).toContain('a@example.com');
    expect(offline.headers.get('X-SW-Cache')).toBe('hit');
  });

  it('serves an anonymous page offline when no principal row exists', async () => {
    // The ordinary public PWA case: nothing has ever written
    // mutations.principal, so the read side falls back to anonymous —
    // which is what a public page stamps.
    const caches = makeCaches();
    const request = navRequest('/');
    let online = basicResponse(pageHtml('', 'season scrubber'));
    const sw = loadSw({
      caches,
      fetch: () => (online ? Promise.resolve(online) : Promise.reject(new TypeError('offline'))),
    });

    await sw._networkFirst(request);
    await flush();
    online = null;

    const offline = await sw._networkFirst(request);
    expect(await offline.text()).toContain('season scrubber');
  });

  it('applies the same check to the ignoreSearch fallback match', async () => {
    // SNOW-347's ``/?d=YYYY-MM-DD`` path: the exact URL was never fetched,
    // so the match comes from the searchless lookup — which must not be a
    // way around the principal check.
    const caches = makeCaches();
    let online = basicResponse(pageHtml('acct-uuid-a', 'a@example.com'));
    const sw = loadSw({
      caches,
      fetch: () => (online ? Promise.resolve(online) : Promise.reject(new TypeError('offline'))),
    });
    caches.seed(
      'snowdesk-shell-UNSUBSTITUTED',
      '/static/offline.html',
      new Response("<h1>This page isn't available offline</h1>"),
    );

    await sw._networkFirst(navRequest('/account/'));
    await flush();
    await setStoredPrincipal(null);
    online = null;

    const offline = await sw._networkFirst(navRequest('/account/?tab=passkeys'));
    const body = await offline.text();
    expect(body).not.toContain('a@example.com');
    expect(body).toContain("This page isn't available offline");
  });
});

describe('pinned-bucket enumeration (SNOW-613)', () => {
  const PINNED = 'snowdesk-basemap-pinned-';

  /**
   * A CacheStorage stub pre-populated with `n` pinned buckets plus one
   * unrelated cache, so the prefix filter has something to reject.
   *
   * @param {number} n
   * @returns {object}
   */
  function cachesWithBuckets(n) {
    const stub = makeCaches();
    stub.seed('snowdesk-shell-abc', `${ORIGIN}/`, new Response('shell'));
    for (let i = 0; i < n; i += 1) {
      stub.seed(`${PINNED}region-${i}`, `https://tiles/${i}.png`, new Response('tile'));
    }
    stub.counters.keys = 0;
    return stub;
  }

  it('enumerates once across many lookups', async () => {
    const stub = cachesWithBuckets(3);
    const sw = loadSw({ caches: stub });

    for (let i = 0; i < 10; i += 1) await sw._pinnedCacheNames();

    // The whole point: an offline pan is thousands of tile reads, and each
    // one used to pay a full caches.keys().
    expect(stub.counters.keys).toBe(1);
  });

  it('shares one enumeration between concurrent callers', async () => {
    const stub = cachesWithBuckets(2);
    const sw = loadSw({ caches: stub });

    await Promise.all([
      sw._pinnedCacheNames(),
      sw._pinnedCacheNames(),
      sw._pinnedCacheNames(),
    ]);

    expect(stub.counters.keys).toBe(1);
  });

  it('re-enumerates after an invalidation', async () => {
    const stub = cachesWithBuckets(1);
    const sw = loadSw({ caches: stub });

    expect(await sw._pinnedCacheNames()).toHaveLength(1);
    stub.seed(`${PINNED}region-new`, 'https://tiles/new.png', new Response('tile'));

    // Still the memo — the worker has not been told anything changed.
    expect(await sw._pinnedCacheNames()).toHaveLength(1);

    sw._invalidatePinnedCacheNames();
    expect(await sw._pinnedCacheNames()).toHaveLength(2);
    expect(stub.counters.keys).toBe(2);
  });

  it('drops a bucket the page deleted, so it is never reopened', async () => {
    const stub = cachesWithBuckets(2);
    const sw = loadSw({ caches: stub });
    await sw._pinnedCacheNames();

    await stub.delete(`${PINNED}region-0`);
    sw._invalidatePinnedCacheNames();

    const names = await sw._pinnedCacheNames();
    // Handing a deleted name to caches.open would CREATE it — resurrecting
    // a bucket the user just evicted, and one SNOW-612's reconciliation
    // would then report back to them as an orphaned download.
    expect(names).not.toContain(`${PINNED}region-0`);
    expect(stub.has(`${PINNED}region-0`)).toBe(false);
  });

  it('excludes the legacy shared bucket and any non-pinned cache', async () => {
    const stub = makeCaches();
    stub.seed('snowdesk-shell-abc', `${ORIGIN}/`, new Response('shell'));
    stub.seed('snowdesk-basemap-tiles', 'https://tiles/x.png', new Response('tile'));
    stub.seed(`${PINNED}region-1`, 'https://tiles/1.png', new Response('tile'));
    const sw = loadSw({ caches: stub });

    expect(await sw._pinnedCacheNames()).toEqual([`${PINNED}region-1`]);
  });

  it('does not memoise a result an invalidation raced past', async () => {
    const stub = cachesWithBuckets(1);
    const sw = loadSw({ caches: stub });

    const inFlight = sw._pinnedCacheNames();
    // Landing mid-enumeration: the answer about to resolve predates it, so
    // writing it into the memo would silently undo the invalidation.
    sw._invalidatePinnedCacheNames();
    await inFlight;

    stub.seed(`${PINNED}region-late`, 'https://tiles/late.png', new Response('tile'));
    expect(await sw._pinnedCacheNames()).toHaveLength(2);
  });
});

describe('offline pinned lookup (SNOW-613)', () => {
  const PINNED = 'snowdesk-basemap-pinned-';
  const TILE = 'https://tiles.example/9/1/2.png';

  it('serves a tile held in any bucket, whichever answers first', async () => {
    const stub = makeCaches();
    stub.seed(`${PINNED}region-a`, 'https://tiles.example/other.png', new Response('other'));
    stub.seed(`${PINNED}region-b`, TILE, new Response('the tile'));
    const sw = loadSw({ caches: stub });

    const response = await sw._basemapStaleWhileRevalidate(new Request(TILE));

    expect(await response.text()).toBe('the tile');
  });

  it('serves the same bytes when two overlapping areas both hold it', async () => {
    const stub = makeCaches();
    stub.seed(`${PINNED}region-a`, TILE, new Response('shared tile'));
    stub.seed(`${PINNED}region-b`, TILE, new Response('shared tile'));
    const sw = loadSw({ caches: stub });

    const response = await sw._basemapStaleWhileRevalidate(new Request(TILE));

    // Parallel search means the winner is no longer the first bucket in
    // enumeration order — which is fine precisely because a tile in two
    // areas is identical bytes in either.
    expect(await response.text()).toBe('shared tile');
  });

  it('falls through to the 504 when no bucket holds it and the network is down', async () => {
    const stub = makeCaches();
    stub.seed(`${PINNED}region-a`, 'https://tiles.example/other.png', new Response('other'));
    const sw = loadSw({ caches: stub });

    const response = await sw._basemapStaleWhileRevalidate(new Request(TILE));

    expect(response.status).toBe(504);
    expect(response.headers.get('X-SW-Cache')).toBe('miss');
  });
});

describe('pinned-bucket miss recheck (SNOW-613)', () => {
  const PINNED = 'snowdesk-basemap-pinned-';
  const TILE = 'https://tiles.example/9/1/2.png';

  it('finds a bucket created without an invalidation', async () => {
    const stub = makeCaches();
    const sw = loadSw({ caches: stub });

    // Warm the memo while nothing is pinned.
    await sw._basemapStaleWhileRevalidate(new Request(TILE));

    // A bucket appears with nothing announcing it — what an e2e fixture
    // does, and what any future caller reaching for `caches.open` directly
    // would do. The memo alone would never see it, and the symptom would
    // be a downloaded tile silently failing to serve offline.
    stub.seed(`${PINNED}region-late`, TILE, new Response('late tile'));

    const response = await sw._basemapStaleWhileRevalidate(new Request(TILE));

    expect(await response.text()).toBe('late tile');
  });

  it('shares one enumeration across a run of hits', async () => {
    const stub = makeCaches();
    for (let i = 0; i < 20; i += 1) {
      stub.seed(`${PINNED}region-a`, `${TILE}?i=${i}`, new Response('tile'));
    }
    const sw = loadSw({ caches: stub });
    stub.counters.keys = 0;

    // The hit path is where the memo earns its keep: a device panning
    // offline over ground it has downloaded, thousands of reads deep.
    for (let i = 0; i < 20; i += 1) {
      await sw._basemapStaleWhileRevalidate(new Request(`${TILE}?i=${i}`));
    }

    expect(stub.counters.keys).toBe(1);
  });
});

describe('basemap cache trim batching (SNOW-614)', () => {
  const TILE_ORIGIN = 'https://tiles.example';

  /**
   * A CacheStorage stub that counts `keys()` per cache name, so the number
   * of trim walks can be asserted separately from the number of puts.
   *
   * @returns {object}
   */
  function countingCaches() {
    const stub = makeCaches();
    const openReal = stub.open;
    stub.perCacheKeys = {};
    stub.open = async (name) => {
      const cache = await openReal(name);
      const wrapped = Object.create(cache);
      wrapped.keys = async () => {
        stub.perCacheKeys[name] = (stub.perCacheKeys[name] || 0) + 1;
        return [];
      };
      wrapped.delete = async () => true;
      return wrapped;
    };
    return stub;
  }

  /**
   * Drive `n` successful basemap tile fetches through the revalidate path.
   *
   * @param {object} sw
   * @param {number} n
   * @returns {Promise<void>}
   */
  async function fetchTiles(sw, n) {
    for (let i = 0; i < n; i += 1) {
      await sw._basemapStaleWhileRevalidate(new Request(`${TILE_ORIGIN}/9/1/${i}.png`));
    }
  }

  /**
   * A fetch stub answering every request with a cacheable cross-origin
   * tile — `type: 'cors'` is what the revalidate path requires before it
   * will write.
   *
   * @returns {Function}
   */
  function corsFetch() {
    return async () => {
      const real = new Response('tile');
      return {
        ok: true,
        status: 200,
        type: 'cors',
        headers: real.headers,
        arrayBuffer: () => real.clone().arrayBuffer(),
        clone() {
          return this;
        },
      };
    };
  }

  it('walks the cache once per batch, not once per tile', async () => {
    const stub = countingCaches();
    const sw = loadSw({ caches: stub, fetch: corsFetch() });
    const n = sw.BASEMAP_CACHE_TRIM_INTERVAL;

    await fetchTiles(sw, n);

    // Before SNOW-614 this was one full keys() walk per tile response.
    expect(stub.perCacheKeys['snowdesk-basemap-v1']).toBe(1);
  });

  it('does not walk at all before the batch is full', async () => {
    const stub = countingCaches();
    const sw = loadSw({ caches: stub, fetch: corsFetch() });

    await fetchTiles(sw, sw.BASEMAP_CACHE_TRIM_INTERVAL - 1);

    expect(stub.perCacheKeys['snowdesk-basemap-v1']).toBeUndefined();
  });

  it('keeps walking on every subsequent batch', async () => {
    const stub = countingCaches();
    const sw = loadSw({ caches: stub, fetch: corsFetch() });

    await fetchTiles(sw, sw.BASEMAP_CACHE_TRIM_INTERVAL * 3);

    // Not a one-shot: the cap still holds over a long browsing session.
    expect(stub.perCacheKeys['snowdesk-basemap-v1']).toBe(3);
  });

  it('bounds the overshoot to one batch', () => {
    // The cap is a soft, insertion-order approximation of LRU, so batching
    // costs at most this much headroom between trims — the property that
    // makes the interval safe to choose freely.
    expect(sw614Overshoot()).toBeLessThan(0.1);

    /** @returns {number} Overshoot as a fraction of the entry cap. */
    function sw614Overshoot() {
      const sw = loadSw();
      return sw.BASEMAP_CACHE_TRIM_INTERVAL / sw.BASEMAP_CACHE_MAX_ENTRIES;
    }
  });
});

describe('inline mutation-queue core agrees with the real one (SNOW-617)', () => {
  // The same treatment D3 gave `worseReason` after it drifted: run both
  // implementations over one shared table, so a change to either fails
  // here. sw.js's copy is reached only when the startup `importScripts`
  // failed, which is exactly the path nothing else exercises.
  const NOW = 1_000_000;

  const ROWS = [
    { label: 'never attempted', row: { id: 1, attempts: 0, status: 'queued' } },
    { label: 'no attempts field', row: { id: 2, status: 'queued' } },
    { label: 'mid-backoff', row: { id: 3, attempts: 5, status: 'retry-scheduled' } },
    { label: 'one short of the ceiling', row: { id: 4, attempts: 18, status: 'retry-scheduled' } },
    { label: 'at the ceiling', row: { id: 5, attempts: 19, status: 'retry-scheduled' } },
    { label: 'past the ceiling', row: { id: 6, attempts: 25, status: 'retry-scheduled' } },
  ];
  const OUTCOMES = ['success', 'permanent', 'retry'];

  it('returns identical transitions across every row and outcome', () => {
    const inline = loadSw()._INLINE_MUTATION_QUEUE_CORE;

    for (const { label, row } of ROWS) {
      for (const outcome of OUTCOMES) {
        expect(
          inline.nextRowState(row, outcome, NOW),
          `${label} / ${outcome}`,
        ).toEqual(coreQueue.nextRowState(row, outcome, NOW));
      }
    }
  });

  it('agrees on MAX_ATTEMPTS and the backoff schedule', () => {
    const inline = loadSw()._INLINE_MUTATION_QUEUE_CORE;

    expect(inline.MAX_ATTEMPTS).toBe(coreQueue.MAX_ATTEMPTS);
    for (let attempts = 1; attempts <= 12; attempts += 1) {
      expect(inline.backoffDelayMs(attempts)).toBe(coreQueue.backoffDelayMs(attempts));
    }
  });
});

describe('_warmCache stamps the page HTML it writes (SNOW-624)', () => {
  const PAGE_URL = `${ORIGIN}/ch-4115/martigny-verbier/`;
  const FEED_URL = `${ORIGIN}/api/ratings/`;

  /**
   * A same-origin `Response`-alike with a chosen content type and body.
   *
   * `type: 'basic'` is what `_warmCache` requires of a same-origin
   * response before it will cache anything.
   *
   * @param {string} body
   * @param {string} contentType
   * @returns {object}
   */
  function sameOriginResponse(body, contentType) {
    return basicResponse(body, { headers: { 'Content-Type': contentType } });
  }

  it('stamps a warmed page with the principal its HTML was rendered for', async () => {
    const stub = makeCaches();
    const sw = loadSw({
      caches: stub,
      fetch: async () =>
        sameOriginResponse(
          '<meta name="pwa-user-id" content="acct-77">',
          'text/html; charset=utf-8',
        ),
    });

    await sw._warmCache([PAGE_URL]);

    const cache = await stub.open('snowdesk-shell-UNSUBSTITUTED');
    const hit = await cache.match(PAGE_URL);
    // Unstamped, this entry would sit in the cache and be refused on every
    // offline read — the user seeing offline.html for a page the device
    // demonstrably holds.
    expect(hit).toBeTruthy();
    expect(hit.headers.get(sw.PRINCIPAL_HEADER)).toBe('acct-77');
  });

  it('stamps a warmed anonymous page as anonymous, not unknown', async () => {
    const stub = makeCaches();
    const sw = loadSw({
      caches: stub,
      fetch: async () =>
        sameOriginResponse('<meta name="pwa-user-id" content="">', 'text/html'),
    });

    await sw._warmCache([PAGE_URL]);

    const cache = await stub.open('snowdesk-shell-UNSUBSTITUTED');
    const hit = await cache.match(PAGE_URL);
    // PRINCIPAL_UNKNOWN never matches anything, so getting this wrong
    // would make a public page unservable offline.
    expect(hit.headers.get(sw.PRINCIPAL_HEADER)).toBe(sw.PRINCIPAL_ANONYMOUS);
  });

  it('leaves a data response alone', async () => {
    const stub = makeCaches();
    const sw = loadSw({
      caches: stub,
      fetch: async () => sameOriginResponse('{"ok":true}', 'application/json'),
    });

    await sw._warmCache([FEED_URL]);

    const cache = await stub.open('snowdesk-shell-UNSUBSTITUTED');
    const hit = await cache.match(FEED_URL);
    expect(hit).toBeTruthy();
    // Feeds are read back by _staleWhileRevalidate, which checks no
    // principal — stamping them would cost a body buffer for nothing.
    expect(hit.headers.get(sw.PRINCIPAL_HEADER)).toBeNull();
  });

  it('reports the warmed page as a success', async () => {
    const stub = makeCaches();
    const sw = loadSw({
      caches: stub,
      fetch: async () =>
        sameOriginResponse('<meta name="pwa-user-id" content="a">', 'text/html'),
    });

    const result = await sw._warmCache([PAGE_URL]);

    expect(result.ok).toBe(1);
    expect(result.failed).toBe(0);
  });

  it('classifies content types the way the offline read does', () => {
    const sw = loadSw();
    const withType = (t) => ({ headers: new Headers({ 'Content-Type': t }) });

    expect(sw._isHtmlResponse(withType('text/html'))).toBe(true);
    expect(sw._isHtmlResponse(withType('text/html; charset=utf-8'))).toBe(true);
    expect(sw._isHtmlResponse(withType('TEXT/HTML'))).toBe(true);
    expect(sw._isHtmlResponse(withType('application/json'))).toBe(false);
    expect(sw._isHtmlResponse(withType(''))).toBe(false);
    expect(sw._isHtmlResponse({ headers: new Headers() })).toBe(false);
  });
});

describe('_warmCache reports a running byte total (SNOW-632)', () => {
  const ORIGIN_URL = (n) => `${ORIGIN}/api/feed-${n}.json`;

  it('hands onProgress the running bytes as a fourth argument', async () => {
    const stub = makeCaches();
    const sw = loadSw({
      caches: stub,
      // Content-Length is what _warmCacheResponseBytes reads first — the
      // fixture's Response-alike has no `.blob()` for the fallback chain
      // to fall through to.
      fetch: async () =>
        basicResponse('{"a":1}', {
          headers: { 'Content-Type': 'application/json', 'Content-Length': '7' },
        }),
    });
    const calls = [];

    await sw._warmCache([ORIGIN_URL(1), ORIGIN_URL(2)], {
      onProgress: (done, total, settled, bytes) => calls.push({ done, total, bytes }),
    });

    expect(calls.length).toBeGreaterThan(0);
    // Every response wrote the same body, so bytes only ever grows —
    // never resets between reports.
    const last = calls[calls.length - 1];
    expect(last.done).toBe(2);
    expect(last.bytes).toBeGreaterThan(0);
    for (let i = 1; i < calls.length; i += 1) {
      expect(calls[i].bytes).toBeGreaterThanOrEqual(calls[i - 1].bytes);
    }
  });
});

describe('_warmCache cancellation (SNOW-632)', () => {
  const urls = Array.from({ length: 40 }, (_, i) => `${ORIGIN}/api/tile-${i}.json`);

  /**
   * A same-origin JSON response — cheap for `_warmCache` to accept and
   * write, with nothing HTML-specific to trip the principal-stamping path.
   *
   * @returns {object}
   */
  function jsonResponse() {
    return basicResponse('{}', { headers: { 'Content-Type': 'application/json' } });
  }

  it('stops dispatching once shouldCancel answers true, well short of the full list', async () => {
    let fetchCount = 0;
    const sw = loadSw({
      caches: makeCaches(),
      fetch: async () => {
        fetchCount += 1;
        return jsonResponse();
      },
    });
    let cancelled = false;

    const result = await sw._warmCache(urls, {
      shouldCancel: () => cancelled,
      // The first settled URL is the signal the page's Cancel click would
      // send — everything still queued behind it in the pool must never
      // reach a fetch.
      onProgress: () => {
        cancelled = true;
      },
    });

    // WARM_CACHE_CONCURRENCY workers can already be mid-fetch when the
    // first one reports and flips the flag, so a handful more than one is
    // expected — but nowhere near the full 40-URL list.
    expect(fetchCount).toBeGreaterThan(0);
    expect(fetchCount).toBeLessThan(urls.length / 2);
    expect(result.cancelled).toBe(true);
  });

  it('reports cancelled: false on a run that simply finishes', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: async () => jsonResponse() });

    const result = await sw._warmCache(urls, { shouldCancel: () => false });

    expect(result.cancelled).toBe(false);
    expect(result.ok).toBe(urls.length);
  });

  it('never fetches at all when already cancelled before the first URL', async () => {
    let fetchCount = 0;
    const sw = loadSw({
      caches: makeCaches(),
      fetch: async () => {
        fetchCount += 1;
        return jsonResponse();
      },
    });

    const result = await sw._warmCache(urls, { shouldCancel: () => true });

    expect(fetchCount).toBe(0);
    expect(result.ok).toBe(0);
    expect(result.failed).toBe(0);
    expect(result.cancelled).toBe(true);
  });
});

describe('the cancelled-requestId set stays bounded (SNOW-632)', () => {
  it('does not retain an id once its run settles', () => {
    const sw = loadSw();

    sw._markWarmCacheCancelled('req-1');
    expect(sw._warmCacheCancelledIds.has('req-1')).toBe(true);

    // What the 'warm-cache' handler does once _warmCache's promise
    // resolves, on every exit path — including the pinned-without-areaId
    // guard, which never calls _warmCache at all.
    sw._clearWarmCacheCancelled('req-1');

    expect(sw._warmCacheCancelledIds.has('req-1')).toBe(false);
  });

  it('evicts the oldest id rather than growing without limit', () => {
    const sw = loadSw();

    // One more than the cap: a mix of duplicate clicks and cancels for
    // requestIds whose run already settled (and so was never re-cleared)
    // — the failure mode _markWarmCacheCancelled exists to bound.
    for (let i = 0; i < sw.WARM_CACHE_CANCEL_SET_MAX + 1; i += 1) {
      sw._markWarmCacheCancelled(`stray-${i}`);
    }

    expect(sw._warmCacheCancelledIds.size).toBe(sw.WARM_CACHE_CANCEL_SET_MAX);
    // Oldest-first eviction: the very first id inserted is the one that
    // fell off.
    expect(sw._warmCacheCancelledIds.has('stray-0')).toBe(false);
    expect(sw._warmCacheCancelledIds.has(`stray-${sw.WARM_CACHE_CANCEL_SET_MAX}`)).toBe(true);
  });

  it('ignores an undefined or null requestId rather than polluting the set', () => {
    const sw = loadSw();

    sw._markWarmCacheCancelled(undefined);
    sw._markWarmCacheCancelled(null);

    expect(sw._warmCacheCancelledIds.size).toBe(0);
  });

  // The sandbox records the worker's listeners but dispatches nothing on
  // its own, and the 'message' listener is not wired up here. This
  // exercises the extracted handler body directly, proving the field name it
  // reads off the message ('requestId') is the one _markWarmCacheCancelled
  // ends up keyed on — a wrong field name here would leave every unit test
  // above green while cancelling nothing in production.
  it('wires a warm-cache-cancel message through to the cancelled set by requestId', () => {
    const sw = loadSw();

    sw._handleWarmCacheCancelMessage({ type: 'warm-cache-cancel', requestId: 'req-live' });

    expect(sw._warmCacheCancelledIds.has('req-live')).toBe(true);
    expect(sw._warmCacheCancelledIds.has('req-other')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// SNOW-722 — a restarted worker must still serve a downloaded basemap
// ---------------------------------------------------------------------------

describe('cross-origin routing when the basemap allowlist is empty (SNOW-722)', () => {
  /*
   * Offline on a plane, the basemap rendered blank inside an area the app
   * itself had marked as downloaded. ``_basemapStaleWhileRevalidate`` was
   * never at fault — it already searches BASEMAP_CACHE and every pinned
   * bucket. The request never reached it: ``_classifyCrossOriginGet``
   * answers 'basemap' only for an origin in the in-memory
   * ``_basemapOrigins`` allowlist, that Set does not survive the browser
   * terminating an idle worker, and its recovery read froze an empty
   * result for the worker's lifetime the first time it failed.
   */

  const TILE_ORIGIN = 'https://tiles.example';
  const TILE_URL = `${TILE_ORIGIN}/12/2145/1436.pbf`;
  const PINNED_BUCKET = 'snowdesk-basemap-pinned-area-alps';

  /** A cross-origin tile GET, as the fetch listener receives one. */
  function tileRequest(url) {
    return { url, method: 'GET', mode: 'cors', destination: 'image' };
  }

  /**
   * Dispatch a synthetic ``fetch`` event at the worker's real listener and
   * resolve whatever it responded with (``undefined`` when it declined to
   * respond, which is the browser-handles-it-natively case).
   *
   * @param {object} sw
   * @param {string} url
   * @returns {Promise<object|undefined>}
   */
  async function dispatchFetch(sw, url) {
    let responded;
    sw.__listeners.fetch({
      request: tileRequest(url),
      clientId: '',
      respondWith(promise) {
        responded = promise;
      },
    });
    return responded === undefined ? undefined : responded;
  }

  /** Recreate the DB with ONLY queue:mutations — no meta:app to read. */
  function resetDbWithoutMetaStore() {
    return new Promise((resolve, reject) => {
      const del = indexedDB.deleteDatabase(DB_NAME);
      del.onerror = () => reject(del.error);
      del.onsuccess = () => {
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => {
          req.result.createObjectStore('queue:mutations', {
            keyPath: 'id',
            autoIncrement: true,
          });
        };
        req.onerror = () => reject(req.error);
        req.onsuccess = () => {
          req.result.close();
          resolve();
        };
      };
    });
  }

  /** Write the basemap.origins mirror static/js/map.js maintains. */
  function setStoredBasemapOrigins(origins) {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        const db = req.result;
        const tx = db.transaction('meta:app', 'readwrite');
        tx.objectStore('meta:app').put({ key: 'basemap.origins', value: origins });
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

  it('rehydrates the allowlist from meta:app and classifies the tile as basemap', async () => {
    await resetDb();
    await setStoredBasemapOrigins([TILE_ORIGIN]);
    // A fresh sandbox is a freshly-restarted worker: _basemapOrigins empty.
    const sw = loadSw();

    expect(await sw._classifyCrossOriginGet(new URL(TILE_URL))).toBe('basemap');
  });

  it('still serves a pinned tile when the meta:app read fails, and retries the read', async () => {
    // No meta:app store at all — the read rejects, so classification can
    // only ever answer 'network' for this tile.
    await resetDbWithoutMetaStore();
    const cachesStub = makeCaches();
    cachesStub.seed(PINNED_BUCKET, TILE_URL, basicResponse('pinned tile bytes'));
    const sw = loadSw({
      caches: cachesStub,
      fetch: () => Promise.reject(new TypeError('Failed to fetch')),
    });

    expect(await sw._classifyCrossOriginGet(new URL(TILE_URL))).toBe('network');

    // 2a: the read-only probe finds it in the pinned bucket regardless.
    const response = await dispatchFetch(sw, TILE_URL);
    expect(await response.text()).toBe('pinned tile bytes');

    // 2b: the failed read was not memoised — once meta:app is readable
    // again the next classification picks the allowlist up.
    await resetDb();
    await setStoredBasemapOrigins([TILE_ORIGIN]);
    expect(await sw._classifyCrossOriginGet(new URL(TILE_URL))).toBe('basemap');
  });

  it('stops retrying a persistently failing read after the attempt cap', async () => {
    // The same catch covers the permanent "worker-created DB has no
    // meta:app store" case, so the retry is bounded: an unbounded one
    // would buy a DB open on every cross-origin request, forever.
    await resetDbWithoutMetaStore();
    const sw = loadSw();

    for (let i = 0; i < sw.BASEMAP_HYDRATION_MAX_ATTEMPTS; i += 1) {
      await sw._hydrateBasemapOrigins();
    }

    // Cap reached: the empty result is now memoised, so a readable DB no
    // longer changes the answer without a register-basemap-origins message.
    await resetDb();
    await setStoredBasemapOrigins([TILE_ORIGIN]);
    expect(await sw._classifyCrossOriginGet(new URL(TILE_URL))).toBe('network');
  });

  it('does not let a superseded attempt spend a freshly-reset retry budget', async () => {
    // A register-basemap-origins message resets both the memo and the retry
    // budget. An attempt that was already in flight when it landed is news
    // about a worker state that no longer applies, so its eventual failure
    // must not be charged against the new budget — otherwise a rapid
    // register/idle-terminate sequence hits the cap an attempt early.
    await resetDbWithoutMetaStore();
    const sw = loadSw();

    const superseded = sw._hydrateBasemapOrigins();
    // An empty list keeps _basemapOrigins empty (so later calls still try to
    // hydrate) while resetting the memo and the failure count, exactly as a
    // live page's registration does.
    sw.__listeners.message({ data: { type: 'register-basemap-origins', origins: [] } });
    await superseded;

    // The full budget must still be there: one short of the cap, the memo is
    // still being dropped, so a DB that becomes readable is picked up.
    for (let i = 0; i < sw.BASEMAP_HYDRATION_MAX_ATTEMPTS - 1; i += 1) {
      await sw._hydrateBasemapOrigins();
    }
    await resetDb();
    await setStoredBasemapOrigins([TILE_ORIGIN]);

    expect(await sw._classifyCrossOriginGet(new URL(TILE_URL))).toBe('basemap');
  });

  it('serves a tile left in BASEMAP_CACHE by an earlier session', async () => {
    // Nothing pinned at all — but the passive cache holds tiles written
    // when the allowlist WAS populated, and they must still read back.
    await resetDbWithoutMetaStore();
    const cachesStub = makeCaches();
    const sw = loadSw({
      caches: cachesStub,
      fetch: () => Promise.reject(new TypeError('Failed to fetch')),
    });
    cachesStub.seed(sw.BASEMAP_CACHE, TILE_URL, basicResponse('passive tile bytes'));

    const response = await dispatchFetch(sw, TILE_URL);

    expect(await response.text()).toBe('passive tile bytes');
  });

  it('never writes on the read-only path', async () => {
    await resetDbWithoutMetaStore();
    const cachesStub = makeCaches();
    cachesStub.seed(PINNED_BUCKET, TILE_URL, basicResponse('pinned tile bytes'));
    const sw = loadSw({
      caches: cachesStub,
      fetch: async () => basicResponse('network bytes'),
    });

    // Both a hit (served from the pinned bucket) and a miss (fetched).
    await dispatchFetch(sw, TILE_URL);
    await dispatchFetch(sw, `${TILE_ORIGIN}/12/9999/9999.pbf`);
    await flush();

    // The allowlist still governs what may be CACHED: an origin that is not
    // on it may read from these buckets but must never populate one.
    expect(cachesStub.size(sw.BASEMAP_CACHE)).toBe(0);
    expect(cachesStub.size(PINNED_BUCKET)).toBe(1);
  });

  it('goes straight to the network, with no pinned walk, when nothing is pinned', async () => {
    // The regression guard for 2a's blast radius: EVERY cross-origin GET
    // now reaches the probe, so what it costs a user with no downloads is
    // the whole question. The bound is asserted here in the same terms the
    // docstring states it, so the two cannot drift apart silently — the
    // first draft of that docstring claimed the guard made the probe free,
    // and nothing here could tell that it doesn't.
    await resetDbWithoutMetaStore();
    const cachesStub = makeCaches();
    const fetched = [];
    const sw = loadSw({
      caches: cachesStub,
      fetch: async (request) => {
        fetched.push(request.url);
        return basicResponse('network bytes');
      },
    });
    const keysBefore = cachesStub.counters.keys;

    await dispatchFetch(sw, 'https://cdn.example/analytics.js');
    await dispatchFetch(sw, 'https://cdn.example/pixel.gif');
    await dispatchFetch(sw, 'https://other.example/thing.json');

    expect(fetched).toEqual([
      'https://cdn.example/analytics.js',
      'https://cdn.example/pixel.gif',
      'https://other.example/thing.json',
    ]);
    // Exactly one BASEMAP_CACHE lookup per request — a keyed match in one
    // bucket, no enumeration. It is deliberately NOT skipped when nothing
    // is pinned: BASEMAP_CACHE is the passive cache ordinary browsing
    // fills, so a user who has panned a basemap but never downloaded an
    // area has tiles there and no pinned buckets at all.
    expect(cachesStub.counters.matchedNames).toEqual([
      sw.BASEMAP_CACHE,
      sw.BASEMAP_CACHE,
      sw.BASEMAP_CACHE,
    ]);
    // And nothing beyond it: no pinned bucket is opened, let alone walked.
    expect(
      cachesStub.counters.openedNames.filter((name) =>
        name.startsWith(sw.BASEMAP_PINNED_CACHE_PREFIX),
      ),
    ).toEqual([]);
    // One enumeration for all three — the memo — and no per-request
    // re-enumeration from the after-miss retry, which is skipped entirely
    // while the bucket list is empty.
    expect(cachesStub.counters.keys - keysBefore).toBe(1);
  });

  it('does walk the pinned buckets once something IS pinned', async () => {
    // The other half of the bound: the short-circuit must be about there
    // being nothing to search, not about skipping the search.
    await resetDbWithoutMetaStore();
    const cachesStub = makeCaches();
    cachesStub.seed(PINNED_BUCKET, TILE_URL, basicResponse('pinned tile bytes'));
    const sw = loadSw({
      caches: cachesStub,
      fetch: async () => basicResponse('network bytes'),
    });

    // A miss, so the walk runs to completion rather than stopping at a hit.
    await dispatchFetch(sw, `${TILE_ORIGIN}/12/9999/9999.pbf`);

    expect(cachesStub.counters.matchedNames).toContain(PINNED_BUCKET);
    expect(cachesStub.counters.matchedNames[0]).toBe(sw.BASEMAP_CACHE);
  });

  // -- SNOW-854: offline mode reaches this branch too ------------------------
  //
  // Found on staging with SNOW-852 already shipped and demonstrably working:
  // the same capture showed a synthesized 504 for an /api/ GET and half a
  // megabyte of swisstopo relief tiles going out beside it. Three of the
  // worker's four network paths consulted the mode; this one, the branch
  // SNOW-722 added, went straight to fetch.
  //
  // The condition is an empty allowlist, which is what the tests above
  // already construct — the whole reason the leak survived the SNOW-852 fix
  // and the offline suite's own camera-move phase is that a REGISTERED
  // basemap origin never comes down here. It takes the guarded strategy
  // instead, and everything anyone looked at was registered.

  it('refuses the network under a forced offline mode, rather than fetching', async () => {
    // No basemap.origins row, so hydration succeeds and finds nothing: the
    // allowlist is empty and every tile classifies as 'network'. Written
    // this way rather than with resetDbWithoutMetaStore() because the mode
    // lives in meta:app too — a DB with no such store would leave the
    // worker in 'auto' and quietly test nothing.
    await resetDb();
    const cachesStub = makeCaches();
    const fetchSpy = vi.fn(async () => basicResponse('network bytes'));
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    const response = await dispatchFetch(sw, TILE_URL);

    expect(response.status).toBe(504);
    // The assertion that is actually about the user's bill.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('still reads the pinned bucket in that mode — the guard is below the probe', async () => {
    // The direction this fix could have been got wrong. Refusing to READ a
    // cache saves nothing and costs the user the map: SNOW-722's whole
    // subject is a device that holds the tiles and cannot classify the
    // origin, and offline mode is when it needs them most.
    await resetDb();
    const cachesStub = makeCaches();
    const fetchSpy = vi.fn(async () => basicResponse('network bytes'));
    cachesStub.seed(PINNED_BUCKET, TILE_URL, basicResponse('pinned tile bytes'));
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    const response = await dispatchFetch(sw, TILE_URL);

    expect(await response.text()).toBe('pinned tile bytes');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('blocks it under an auto-latch as well as a forced mode', async () => {
    // The latch means the worker has concluded there is no route at all.
    // Tiles are the highest-volume thing it fetches, so this is the path
    // where an unbounded retry costs the most.
    await resetDb();
    const fetchSpy = vi.fn(async () => basicResponse('network bytes'));
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._latchOffline();

    const response = await dispatchFetch(sw, TILE_URL);

    expect(response.status).toBe(504);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('goes back to the network as soon as offline mode is switched off', async () => {
    // Reversibility, asserted on this branch too: the guard must be the
    // mode, not a latch of its own.
    await resetDb();
    const fetchSpy = vi.fn(async () => basicResponse('network bytes'));
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._forceOffline();
    await dispatchFetch(sw, TILE_URL);

    sw._unlatchOffline();
    const response = await dispatchFetch(sw, TILE_URL);

    expect(await response.text()).toBe('network bytes');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// SNOW-742 — read-path budgets and the offline latch
// ---------------------------------------------------------------------------
//
// The failure these cover is specifically NOT "the network is down". A refused
// fetch has always worked: it rejects, and every fallback below is a ``catch``
// branch waiting for exactly that. What broke on the Underground is a radio
// that is attached but has no route, where ``fetch`` neither resolves nor
// rejects — it hangs on TCP retries for minutes, the catch never runs, and the
// app sits blank on top of data already on disk.
//
// So every test here models a HANGING fetch (a promise that never settles),
// not a failing one, and drives the clock with fake timers. A stub that
// rejects would pass against the old code and prove nothing.

/**
 * A ``fetch`` stub that models the dead-but-attached radio: it never resolves
 * and never rejects on its own, but it DOES honour an ``AbortSignal``, which
 * is the contract real ``fetch`` has and the entire mechanism under test.
 *
 * A stub that ignored the signal would hang these tests rather than fail them,
 * and a stub that rejected immediately would pass against the unfixed code.
 *
 * @returns {import('vitest').Mock}
 */
function hangingFetch() {
  return vi.fn(
    (_input, init) =>
      new Promise((_resolve, reject) => {
        const signal = init && init.signal;
        if (!signal) return;
        if (signal.aborted) {
          reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
          return;
        }
        signal.addEventListener('abort', () => {
          reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
        });
      }),
  );
}

describe('read-path budgets (SNOW-742)', () => {
  beforeEach(() => {
    useTimersLeavingIndexedDb();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('serves the cached shell when a navigation hangs, within the budget', async () => {
    const cachesStub = makeCaches();
    const url = `${ORIGIN}/map/`;
    const fetchSpy = hangingFetch();
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });
    // Stamped anonymous: an entry with no ``X-SW-Principal`` is fail-closed by
    // design (C1) and would never be served, which is a different test.
    cachesStub.seed(
      'snowdesk-shell-UNSUBSTITUTED',
      url,
      basicResponse('<html><meta name="pwa-user-id" content=""></html>', {
        headers: { [sw.PRINCIPAL_HEADER]: sw.PRINCIPAL_ANONYMOUS },
      }),
    );

    await settleHydration(sw);

    const pending = sw._networkFirst(new Request(url));
    await vi.advanceTimersByTimeAsync(sw.NAVIGATION_FETCH_BUDGET_MS + 1);
    const response = await pending;

    // The assertion that fails against the old code: before the budget the
    // promise above simply never settled, so this line hung with the test.
    expect(response.status).toBe(200);
    expect(response.headers.get('X-SW-Cache')).toBe('hit');
  });

  it('returns the synthesized 504 when a basemap tile hangs, within the budget', async () => {
    const cachesStub = makeCaches();
    const fetchSpy = hangingFetch();
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });

    await settleHydration(sw);

    const pending = sw._basemapStaleWhileRevalidate(new Request('https://tiles.example/1/2/3.pbf'));
    await vi.advanceTimersByTimeAsync(sw.BASEMAP_FETCH_BUDGET_MS + 1);
    const response = await pending;

    expect(response.status).toBe(504);
    expect(response.headers.get('X-SW-Cache')).toBe('miss');
  });

  // The ordering pair. A cache HIT always resolved, even under the old
  // fetch-first implementation, so "the hit is served" proves nothing on its
  // own — the bug was that a doomed request went out ANYWAY, several hundred
  // of them, each holding a connection slot until the OS gave up. So the
  // assertion that matters is about the call count, not the response.

  it('serves a basemap cache hit with zero network calls when there is no route', async () => {
    const cachesStub = makeCaches();
    const url = 'https://tiles.example/1/2/3.pbf';
    const fetchSpy = hangingFetch();
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });
    cachesStub.seed(sw.BASEMAP_CACHE, url, basicResponse('tile'));
    sw._latchOffline();
    fetchSpy.mockClear();

    const response = await sw._basemapStaleWhileRevalidate(new Request(url));

    expect(await response.text()).toBe('tile');
    // Fails against the old implementation, which started its fetch on the
    // function's first line — before the cache was even read.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('still revalidates a hit in the background when there IS a route', async () => {
    const cachesStub = makeCaches();
    const url = 'https://tiles.example/4/5/6.pbf';
    const fetchSpy = hangingFetch();
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });
    cachesStub.seed(sw.BASEMAP_CACHE, url, basicResponse('tile'));

    const response = await sw._basemapStaleWhileRevalidate(new Request(url));

    // Stale-while-revalidate is intact: the hit is answered from disk without
    // waiting (no timer is advanced here), and the refresh still goes out.
    expect(await response.text()).toBe('tile');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('does not touch the network for a shell asset once latched', async () => {
    const cachesStub = makeCaches();
    const url = `${ORIGIN}/static/css/output.css`;
    const fetchSpy = hangingFetch();
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });
    cachesStub.seed('snowdesk-shell-UNSUBSTITUTED', url, basicResponse('body{}'));

    sw._latchOffline();
    const response = await sw._staleWhileRevalidate(new Request(url));

    expect(await response.text()).toBe('body{}');
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe('the offline latch (SNOW-742)', () => {
  beforeEach(() => {
    useTimersLeavingIndexedDb();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Drive one hanging basemap read to its budget expiry. */
  async function timeOutOneRead(sw) {
    // Memoised, so this is a settled promise for every read after the first —
    // but the first read must not be raced past its own hydration.
    await settleHydration(sw);
    const pending = sw._basemapStaleWhileRevalidate(
      new Request(`https://tiles.example/${Math.random()}.pbf`),
    );
    await vi.advanceTimersByTimeAsync(sw.BASEMAP_FETCH_BUDGET_MS + 1);
    await pending;
  }

  it('latches after OFFLINE_LATCH_THRESHOLD consecutive timeouts, not before', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: hangingFetch() });

    for (let i = 0; i < sw.OFFLINE_LATCH_THRESHOLD - 1; i++) {
      await timeOutOneRead(sw);
      expect(await sw._shouldUseNetwork()).toBe(true);
    }
    await timeOutOneRead(sw);

    expect(await sw._shouldUseNetwork()).toBe(false);
  });

  it('a success part-way through resets the run, so a slow request never latches alone', async () => {
    const cachesStub = makeCaches();
    let hang = true;
    const hanging = hangingFetch();
    const fetchSpy = vi.fn((input, init) =>
      hang ? hanging(input, init) : Promise.resolve(basicResponse('ok')),
    );
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });

    await timeOutOneRead(sw);
    await timeOutOneRead(sw);
    // One good answer in the middle of the run — the route is alive after all.
    hang = false;
    await sw._boundedFetch(`${ORIGIN}/anything`, 1000);
    hang = true;
    await timeOutOneRead(sw);

    // Three timeouts have now happened in total, but not three in a ROW.
    expect(await sw._shouldUseNetwork()).toBe(true);
  });

  it('makes zero network calls while latched, and 504s a miss immediately', async () => {
    const fetchSpy = hangingFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._latchOffline();
    fetchSpy.mockClear();

    // No timer advance: a latched miss must answer now, not after a budget.
    const response = await sw._basemapStaleWhileRevalidate(
      new Request('https://tiles.example/9/9/9.pbf'),
    );

    expect(response.status).toBe(504);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('unlatches when the probe finds a route, and probes the no-op endpoint', async () => {
    let answer = false;
    const fetchSpy = vi.fn((input) => {
      const url = typeof input === 'string' ? input : input.url;
      if (url.includes('/livez') && answer) return Promise.resolve(basicResponse('ok'));
      return new Promise(() => {});
    });
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._latchOffline();
    expect(await sw._shouldUseNetwork()).toBe(false);

    answer = true;
    await sw._probeNetwork();

    expect(await sw._shouldUseNetwork()).toBe(true);
    expect(fetchSpy.mock.calls.some(([input]) => String(input).includes(sw.OFFLINE_PROBE_URL))).toBe(
      true,
    );
  });

  it('backs off rather than retrying tightly when the probe finds nothing', async () => {
    const fetchSpy = hangingFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._latchOffline();
    fetchSpy.mockClear();

    // Nothing should fire before the first backoff step elapses.
    await vi.advanceTimersByTimeAsync(sw.OFFLINE_PROBE_BACKOFF_MS[0] - 1);
    expect(fetchSpy).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(2);
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // That probe hangs and times out, which must reschedule rather than spin.
    await vi.advanceTimersByTimeAsync(sw.OFFLINE_PROBE_BACKOFF_MS[1] + 10);
    expect(fetchSpy.mock.calls.length).toBeLessThanOrEqual(3);
    expect(await sw._shouldUseNetwork()).toBe(false);
  });

  it('honours an explicit latch with no timeouts, and an explicit unlatch', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: hangingFetch() });

    // Pre-arming before a tunnel: no failed request has happened at all.
    sw._latchOffline();
    expect(await sw._shouldUseNetwork()).toBe(false);

    sw._unlatchOffline();
    expect(await sw._shouldUseNetwork()).toBe(true);
  });

  it('leaves _warmCache unbounded — a download is not a read path', async () => {
    const cachesStub = makeCaches();
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const fetchSpy = vi.fn(() => gate.then(() => basicResponse('tile')));
    const sw = loadSw({ caches: cachesStub, fetch: fetchSpy });

    const run = sw._warmCache([`${ORIGIN}/api/regions.geojson`]);
    // Well past every read budget in the worker. A read would have been
    // aborted by now; a download must still be waiting.
    await vi.advanceTimersByTimeAsync(sw.NAVIGATION_FETCH_BUDGET_MS * 4);
    release();
    const summary = await run;

    expect(summary.ok).toBe(1);
    expect(summary.failed).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// SNOW-748 — the user-forced offline mode
// ---------------------------------------------------------------------------
//
// SNOW-742 had two modes, so a user asking for offline mode was routed into
// ``_latchOffline()`` — which schedules the unlatch probe. Pressed on a live
// connection the probe succeeds, being online is the premise, and the user is
// back in ``'auto'`` within thirty seconds. That was hidden by the control
// living in a banner which only appears once the network is already failing.
// The account menu's "Offline mode" row (SNOW-748) is reachable from every
// page, so the third mode is what makes the control mean what it says.
//
// The distinction these tests pin: ``'offline'`` is probed and comes back on
// its own; ``'offline-forced'`` is not probed at all and is left alone until
// the user changes it.

describe('the user-forced offline mode (SNOW-748)', () => {
  beforeEach(() => {
    useTimersLeavingIndexedDb();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** A fetch that always answers, so any probe that runs would succeed. */
  function answeringFetch() {
    return vi.fn(() => Promise.resolve(basicResponse('ok')));
  }

  it('survives a probe that would succeed, where an auto-latch does not', async () => {
    const forced = loadSw({ caches: makeCaches(), fetch: answeringFetch() });
    forced._forceOffline();
    await forced._probeNetwork();

    expect(await forced._shouldUseNetwork()).toBe(false);
    expect(await readNetworkMode(forced)).toBe('offline-forced');

    // The contrast case, on the same premise: an auto-latch is exactly the
    // mode a successful probe is meant to end.
    const latched = loadSw({ caches: makeCaches(), fetch: answeringFetch() });
    latched._latchOffline();
    await latched._probeNetwork();

    expect(await latched._shouldUseNetwork()).toBe(true);
  });

  it('schedules no probe at all, so the mode holds past every backoff step', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._forceOffline();
    fetchSpy.mockClear();

    const total = sw.OFFLINE_PROBE_BACKOFF_MS.reduce((a, b) => a + b, 0);
    await vi.advanceTimersByTimeAsync(total * 2);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(await sw._shouldUseNetwork()).toBe(false);
    expect(await readNetworkMode(sw)).toBe('offline-forced');
  });

  it('cancels a pending probe left over from an auto-latch', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._latchOffline();
    sw._forceOffline();
    fetchSpy.mockClear();

    await vi.advanceTimersByTimeAsync(sw.OFFLINE_PROBE_BACKOFF_MS[0] * 2);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(await readNetworkMode(sw)).toBe('offline-forced');
  });

  it('is not downgraded to an auto-latch, and stays unprobed when one is attempted', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._forceOffline();
    sw._latchOffline();
    fetchSpy.mockClear();

    // Not just the label: a downgrade would also schedule the probe that ends
    // the mode, so the absence of a fetch is the half that matters.
    await vi.advanceTimersByTimeAsync(sw.OFFLINE_PROBE_BACKOFF_MS[0] * 2);

    expect(await readNetworkMode(sw)).toBe('offline-forced');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('survives in-flight reads timing out after the user forced the mode', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: hangingFetch() });

    // A tile burst already on the wire when the user presses the toggle. Those
    // requests cannot be recalled, so their timeouts arrive under the forced
    // mode — and there are enough of them to reach OFFLINE_LATCH_THRESHOLD,
    // which is the only way ``_latchOffline`` is reached from a mode where
    // reads no longer touch the network.
    const inFlight = [];
    for (let i = 0; i < sw.OFFLINE_LATCH_THRESHOLD; i += 1) {
      inFlight.push(sw._basemapStaleWhileRevalidate(new Request(`https://tiles.example/${i}.pbf`)));
    }
    // Let each read get past its (async) cache lookups and onto the wire
    // before the mode changes — otherwise they answer 504 from the new mode
    // and never time out at all, which would make this assertion vacuous.
    await vi.advanceTimersByTimeAsync(0);
    sw._forceOffline();
    await vi.advanceTimersByTimeAsync(sw.BASEMAP_FETCH_BUDGET_MS + 1);
    await Promise.all(inFlight);

    expect(await readNetworkMode(sw)).toBe('offline-forced');
  });

  it('blocks the network under both offline values, and only those', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });

    expect(await sw._shouldUseNetwork()).toBe(true);
    sw._latchOffline();
    expect(await sw._shouldUseNetwork()).toBe(false);
    sw._unlatchOffline();
    expect(await sw._shouldUseNetwork()).toBe(true);
    sw._forceOffline();
    expect(await sw._shouldUseNetwork()).toBe(false);
  });

  it('reaches all three modes through the network-mode message', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });
    const send = (mode) => sw.__listeners.message({ data: { type: 'network-mode', mode } });

    send('offline');
    expect(await readNetworkMode(sw)).toBe('offline');

    send('auto');
    expect(await readNetworkMode(sw)).toBe('auto');

    send('offline-forced');
    expect(await readNetworkMode(sw)).toBe('offline-forced');

    // And back, which is the toggle's other direction.
    send('auto');
    expect(await readNetworkMode(sw)).toBe('auto');
  });
});

// ---------------------------------------------------------------------------
// SNOW-748 — a forced mode stops downloads; a latch still does not
// ---------------------------------------------------------------------------
//
// The distinction the block above pins for READS holds for DOWNLOADS too, in
// the opposite direction. ``_warmCache`` ignores the auto-latch on purpose: a
// latch is the worker's guess that there is no route, and a download the user
// explicitly asked for should still be attempted in case the guess is wrong.
// A forced mode is not a guess — the user has said not to spend this
// connection — so it refuses a new run and cancels one in flight.
//
// The first test in each pair is the fix; the second is the regression guard
// for the shipped latch behaviour (SNOW-568/SNOW-742), which a blanket
// ``!== 'auto'`` here would silently break.

describe('warm-cache under the two offline modes (SNOW-748)', () => {
  const urls = Array.from({ length: 40 }, (_, i) => `${ORIGIN}/api/tile-${i}.json`);

  /** A same-origin JSON response `_warmCache` accepts and writes. */
  function jsonResponse() {
    return basicResponse('{}', { headers: { 'Content-Type': 'application/json' } });
  }

  /**
   * A fetch that answers only once ``release()`` is called, so a run can be
   * held open long enough for a mode change to land mid-flight.
   *
   * @returns {{fetch: Function, release: Function, count: () => number}}
   */
  function gatedFetch() {
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    let count = 0;
    return {
      fetch: async () => {
        count += 1;
        await gate;
        return jsonResponse();
      },
      release: () => release(),
      count: () => count,
    };
  }

  /**
   * Dispatch a ``warm-cache`` message at the real listener and return the
   * ``warm-cache-done`` reply it eventually posts back — the same path the
   * page's ``pwaWarmCache()`` takes, so the in-flight bookkeeping under test
   * is the bookkeeping that actually runs.
   *
   * @param {object} sw
   * @param {string} requestId
   * @param {string[]} list
   * @returns {Promise<object>}
   */
  function dispatchWarmCache(sw, requestId, list) {
    return new Promise((resolve) => {
      sw.__listeners.message({
        data: { type: 'warm-cache', urls: list, requestId },
        source: {
          postMessage: (data) => {
            if (data.type === 'warm-cache-done') resolve(data);
          },
        },
      });
    });
  }

  it('refuses a new run outright while the mode is forced', async () => {
    const fetchSpy = vi.fn(async () => jsonResponse());
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._forceOffline();
    const result = await sw._warmCache(urls);

    expect(fetchSpy).not.toHaveBeenCalled();
    // The shape of a cancelled run, not a failed one: nothing was attempted,
    // so nothing can have failed, and basemap_download_runner.js's callers
    // read `cancelled` before they read a short `ok`.
    expect(result).toEqual({
      ok: 0,
      failed: 0,
      reason: 'offline-forced',
      bytes: 0,
      cancelled: true,
    });
  });

  it('still runs one under an auto-latch — the guess may be wrong', async () => {
    const fetchSpy = vi.fn(async () => jsonResponse());
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    sw._latchOffline();
    const result = await sw._warmCache(urls);

    // Shipped behaviour since SNOW-568: a download is a long operation the
    // user explicitly asked for, on a connection they believe they have.
    expect(result.ok).toBe(urls.length);
    expect(result.cancelled).toBe(false);
    expect(fetchSpy).toHaveBeenCalled();
  });

  it('cancels a run in flight when the user forces the mode', async () => {
    const gate = gatedFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: gate.fetch });

    const done = dispatchWarmCache(sw, 'req-live', urls);
    // Let the run hydrate the mode (SNOW-748 put an IndexedDB read ahead of
    // the pool), open its caches and fill the pool before the toggle lands. A
    // macrotask rather than a few microtasks, because the read is one: with
    // microtasks alone the cancel would land before a single URL was
    // dispatched, and the test would assert nothing about a run in flight.
    await flush();

    sw._forceOffline();
    gate.release();
    const result = await done;

    expect(result.cancelled).toBe(true);
    // The invariant basemap_download_runner.js documents: a cancelled run has
    // no failures, so a `finish` that checks `cancelled` first never writes it
    // up as a partial or failed download.
    expect(result.failed).toBe(0);
    expect(result.ok).toBeLessThan(urls.length);
    // The run really was in flight — otherwise "cancelled a run in flight"
    // would be passing on a run that had not started.
    expect(gate.count()).toBeGreaterThan(0);
    // Only the workers already dispatched when the cancel landed reached the
    // network; the rest of the list never did.
    expect(gate.count()).toBeLessThan(urls.length / 2);
  });

  it('leaves a run in flight alone when the worker merely latches', async () => {
    const gate = gatedFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: gate.fetch });

    const done = dispatchWarmCache(sw, 'req-live', urls);
    // The write is decided after the body is read, so let the microtasks
    // and the pending put settle before asking what landed.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await Promise.resolve();

    sw._latchOffline();
    gate.release();
    const result = await done;

    expect(result.cancelled).toBe(false);
    expect(result.ok).toBe(urls.length);
  });

  it('forgets a settled run, so a later force cancels nothing', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: async () => jsonResponse() });

    const result = await dispatchWarmCache(sw, 'req-done', [`${ORIGIN}/api/one.json`]);
    expect(result.cancelled).toBe(false);
    expect(sw._warmCacheActiveIds.size).toBe(0);

    sw._forceOffline();

    // A finished run's id must not be marked cancelled — nothing would ever
    // clear it again, and the cancelled set is consulted by requestId.
    expect(sw._warmCacheCancelledIds.has('req-done')).toBe(false);
  });

  it('bounds the in-flight set the same way the cancelled set is bounded', () => {
    const sw = loadSw();

    sw._markWarmCacheActive(undefined);
    sw._markWarmCacheActive(null);
    expect(sw._warmCacheActiveIds.size).toBe(0);

    for (let i = 0; i < sw.WARM_CACHE_CANCEL_SET_MAX + 1; i += 1) {
      sw._markWarmCacheActive(`run-${i}`);
    }

    expect(sw._warmCacheActiveIds.size).toBe(sw.WARM_CACHE_CANCEL_SET_MAX);
    expect(sw._warmCacheActiveIds.has('run-0')).toBe(false);

    sw._clearWarmCacheActive(`run-${sw.WARM_CACHE_CANCEL_SET_MAX}`);
    expect(sw._warmCacheActiveIds.has(`run-${sw.WARM_CACHE_CANCEL_SET_MAX}`)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// SNOW-748 — the worker recovers a forced mode after being recycled
// ---------------------------------------------------------------------------
//
// ``_networkMode`` is module scope, so it dies with the worker, and Chrome
// terminates an idle one after about thirty seconds. Until this fix nothing
// restored it but a page's boot re-assert: a worker recycled with no page
// loading came back in ``'auto'``, quietly resumed using the network, and
// fired no event — so the header's offline symbol went on showing an app that
// was in fact back on the wire. It escaped notice for the reason such
// bugs do: polling the worker to check on it is exactly what keeps it alive.
//
// The reads below all start from a FRESH sandbox with a row already on disk —
// a restarted worker, with nothing having pushed it a mode. That is the state
// the bug lived in, and the state every one of these assertions is about.

describe('recovering the network mode after a restart (SNOW-748)', () => {
  beforeEach(async () => {
    await resetDb();
  });

  afterEach(async () => {
    // The DB is shared by every describe in this file, and a leftover
    // ``network.mode`` row would silently force an unrelated worker offline.
    await resetDb();
  });

  /** A fetch that answers, so an unhydrated worker would visibly use it. */
  function answeringFetch() {
    return vi.fn(async () => basicResponse('tile'));
  }

  /** A client stub of the shape ``self.clients.matchAll`` resolves with. */
  function makeClient() {
    const seen = [];
    return { postMessage: (data) => seen.push(data), seen };
  }

  it('answers a bare mode query with the forced mode, having served nothing', async () => {
    await setStoredNetworkMode('offline-forced');
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    // The exact repro, at the level it happens: a recycled worker, a single
    // ``postMessage({type: 'network-mode'})`` from a page, and NOTHING else —
    // no fetch, no warm-cache, nothing that consults a read path. Hydrating
    // lazily from the read paths left this route answering ``'auto'`` from the
    // startup default, and the answer is acted on rather than ignored:
    // pwa_offline.js takes any ``network-mode`` announcement as authoritative
    // and repainted the user's toggle OFF.
    expect(await readNetworkMode(sw)).toBe('offline-forced');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('starts the read at script evaluation, before any wake reason asks', async () => {
    await setStoredNetworkMode('offline-forced');
    const opened = vi.spyOn(indexedDB, 'open');

    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });

    // Asserted before a single await: the read is in flight by the time the
    // worker's listeners are registered. A worker wakes for four reasons —
    // fetch, message, push, sync — and hydrating from the read paths alone
    // covered only the first, which is how the message path went on answering
    // from the unhydrated default. ``activate`` would not have covered it
    // either: it fires on install and update, not on an idle restart.
    expect(opened).toHaveBeenCalledTimes(1);

    opened.mockRestore();
    await sw._hydrateNetworkMode();
  });

  it('comes up in the forced mode with nothing having pushed one to it', async () => {
    await setStoredNetworkMode('offline-forced');
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });

    // The regression guard. No ``network-mode`` message, no ``_forceOffline``
    // call — just the worker and the row the user's choice left behind.
    expect(await sw._shouldUseNetwork()).toBe(false);
    expect(await readNetworkMode(sw)).toBe('offline-forced');
  });

  it('answers a read from cache without a single request going out', async () => {
    await setStoredNetworkMode('offline-forced');
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    const response = await sw._basemapStaleWhileRevalidate(
      new Request('https://tiles.example/7/7/7.pbf'),
    );

    // Against the unfixed worker this fetch succeeds and the tile comes back
    // 200 — the app using the network while the header symbol says it is
    // offline,
    // which is the whole bug rather than a proxy for it.
    expect(response.status).toBe(504);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('publishes the recovered mode so an open page resyncs its toggle', async () => {
    await setStoredNetworkMode('offline-forced');
    const client = makeClient();
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch(), clients: [client] });

    await sw._hydrateNetworkMode();
    await flush();

    // A page that was open across the restart holds a toggle drawn from the
    // mode it last heard. Recovering the mode silently would leave the two
    // agreeing only by luck.
    expect(client.seen).toContainEqual({ type: 'network-mode', mode: 'offline-forced' });
  });

  it('does NOT restore a persisted auto-latch', async () => {
    await setStoredNetworkMode('offline');
    const client = makeClient();
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch(), clients: [client] });

    await sw._hydrateNetworkMode();
    await flush();

    // The decision, pinned. A latch is the worker's own inference from three
    // read-path timeouts, and by the time a recycled worker reads it back the
    // radio it was drawn from may be long since alive; restoring it would
    // strand the user offline on expired evidence, with only a probe on a
    // backoff that reaches five minutes to clear it. Declining costs a
    // re-latch in about nine seconds if the radio really is still dead. A
    // FORCED mode has no evidence to expire — it is the user's standing
    // instruction — which is why the two are treated differently here.
    expect(await sw._shouldUseNetwork()).toBe(true);
    expect(await readNetworkMode(sw)).toBe('auto');
    expect(client.seen).toEqual([]);
  });

  it('stays in auto when no mode has ever been persisted', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });

    expect(await sw._shouldUseNetwork()).toBe(true);
    expect(await readNetworkMode(sw)).toBe('auto');
  });

  it('stays in auto when the row cannot be read at all', async () => {
    await resetDbWithoutMetaStore();
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });

    // Fail safe, like ``_currentPrincipal``: a DB the worker cannot read must
    // never wedge it offline, because nothing but a page load would free it.
    expect(await sw._shouldUseNetwork()).toBe(true);
    expect(await readNetworkMode(sw)).toBe('auto');
  });

  it('reads the row once, however many requests arrive', async () => {
    await setStoredNetworkMode('offline-forced');
    const opened = vi.spyOn(indexedDB, 'open');
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });

    // A tile burst on a freshly restarted worker, plus a mode query, which is
    // when this runs. ``_basemapStaleWhileRevalidate`` touches no other
    // IndexedDB row, so every open counted here belongs to the hydration —
    // including the one ``loadSw`` itself starts, since the read now begins at
    // script evaluation rather than on the first read path.
    await Promise.all([
      sw._basemapStaleWhileRevalidate(new Request('https://tiles.example/1.pbf')),
      sw._basemapStaleWhileRevalidate(new Request('https://tiles.example/2.pbf')),
      sw._basemapStaleWhileRevalidate(new Request('https://tiles.example/3.pbf')),
    ]);
    await sw._shouldUseNetwork();
    await readNetworkMode(sw);
    await sw._hydrateNetworkMode();

    expect(opened).toHaveBeenCalledTimes(1);
    opened.mockRestore();
  });

  it('lets a page that names a mode outrank a read already in flight', async () => {
    await setStoredNetworkMode('offline-forced');
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });

    // The user pressing "go back online" while the restarted worker is still
    // reading the row — ``loadSw`` starts that read, and it cannot have
    // finished, because an IndexedDB read takes a macrotask and this line does
    // not yield. The row is only as fresh as its last write, so the message is
    // newer by construction and must win; otherwise the read lands and forces
    // the user offline again for no reason they can see.
    sw.__listeners.message({ data: { type: 'network-mode', mode: 'auto' } });
    await sw._hydrateNetworkMode();

    expect(await readNetworkMode(sw)).toBe('auto');
    expect(await sw._shouldUseNetwork()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// SNOW-846: the request ledger
// ---------------------------------------------------------------------------

describe('the request ledger (SNOW-846)', () => {
  const TILE = 'https://tiles.example/7/7/7.pbf';
  const PINNED_BUCKET = 'snowdesk-basemap-pinned-region-CH-4115';

  /** A client stub, so the worker's debug flush has somewhere to go. */
  function makeClient() {
    const seen = [];
    return { postMessage: (data) => seen.push(data), seen };
  }

  /** A cross-origin tile request, as the fetch listener hands one on. */
  function tileRequest(url) {
    return { url, method: 'GET', mode: 'cors', destination: 'empty' };
  }

  /**
   * A fetch that answers with a cacheable cross-origin tile — `type: 'cors'`
   * is what the basemap write path requires, and a 200 is what the ledger's
   * `status` should read back.
   */
  function answeringTileFetch() {
    return vi.fn(async () => {
      const real = new Response('tile');
      return {
        ok: true,
        status: 200,
        type: 'cors',
        headers: real.headers,
        arrayBuffer: () => real.clone().arrayBuffer(),
        clone() {
          return this;
        },
      };
    });
  }

  /**
   * A worker with the trace already recording — the same message
   * `static/js/debug_log.js` sends when the panel's Record box is ticked.
   */
  function recordingSw(options) {
    const sw = loadSw(options);
    sw.__listeners.message({ data: { type: 'debug-log-enabled', enabled: true } });
    return sw;
  }

  /**
   * Every `req serve` line the worker has broadcast, oldest first.
   *
   * Async because the flush hands its batch to `clients.matchAll().then()`
   * — the postMessage lands a microtask later, so reading `seen` on the
   * same tick returns nothing.
   */
  async function ledger(sw, client) {
    sw._flushDebugLog();
    await flush();
    return client.seen
      .filter((message) => message && message.type === 'debug-log')
      .flatMap((message) => message.entries)
      .filter((entry) => entry.src === 'req' && entry.evt === 'serve');
  }

  /** Every `sw` line, for the adjacent detail a serve line points at. */
  async function swLines(sw, client, evt) {
    sw._flushDebugLog();
    await flush();
    return client.seen
      .filter((message) => message && message.type === 'debug-log')
      .flatMap((message) => message.entries)
      .filter((entry) => entry.src === 'sw' && entry.evt === evt);
  }

  beforeEach(async () => {
    await resetDb();
  });

  it('names the passive cache when a browsed tile answers', async () => {
    const caches = makeCaches();
    const client = makeClient();
    const sw = recordingSw({ caches, fetch: answeringTileFetch(), clients: [client] });
    caches.seed(sw.BASEMAP_CACHE, TILE, new Response('tile'));

    await sw._basemapStaleWhileRevalidate(tileRequest(TILE));

    // The gap this ticket closes: before it, a tile served from the passive
    // cache and one served from the network produced the same trace, which
    // is to say no trace at all.
    expect(await ledger(sw, client)).toContainEqual(
      expect.objectContaining({
        detail: expect.objectContaining({ strategy: 'basemap', source: 'passive' }),
      }),
    );
  });

  it('names the pinned partition, and which bucket, when a download answers', async () => {
    const caches = makeCaches();
    caches.seed(PINNED_BUCKET, TILE, new Response('tile'));
    const client = makeClient();
    const sw = recordingSw({ caches, fetch: answeringTileFetch(), clients: [client] });

    await sw._basemapStaleWhileRevalidate(tileRequest(TILE));


    expect(await ledger(sw, client)).toContainEqual(
      expect.objectContaining({
        detail: expect.objectContaining({ strategy: 'basemap', source: 'pinned' }),
      }),
    );
    // "Which of my downloads is covering this ground" — the hit line could
    // not answer that before, and it is the first thing you want when two
    // areas overlap.
    expect(await swLines(sw, client, 'pinned.search')).toContainEqual(
      expect.objectContaining({
        detail: expect.objectContaining({ result: 'hit', bucket: PINNED_BUCKET }),
      }),
    );
  });

  it('records the network round trip and its status', async () => {
    const client = makeClient();
    const sw = recordingSw({
      caches: makeCaches(),
      fetch: answeringTileFetch(),
      clients: [client],
    });

    await sw._basemapStaleWhileRevalidate(tileRequest(TILE));

    const line = (await ledger(sw, client)).find((entry) => entry.detail.source === 'network');
    expect(line).toBeTruthy();
    expect(line.detail.status).toBe(200);
    // Elapsed ms rides along, so a budget expiry reads differently from a
    // fast refusal even when both end in a 504.
    expect(typeof line.detail.ms).toBe('number');
  });

  it('records the synthesized 504 when every partition missed offline', async () => {
    await setStoredNetworkMode('offline-forced');
    const client = makeClient();
    const sw = recordingSw({
      caches: makeCaches(),
      fetch: answeringTileFetch(),
      clients: [client],
    });

    const response = await sw._basemapStaleWhileRevalidate(tileRequest(TILE));

    expect(response.status).toBe(504);
    // The response the page gets is synthesized, so nothing outside the
    // worker can tell it apart from a real gateway timeout.
    expect(await ledger(sw, client)).toContainEqual(
      expect.objectContaining({
        detail: expect.objectContaining({ source: 'timeout-504', status: 504 }),
      }),
    );
  });

  it('names the shell cache for a static asset served from disk', async () => {
    const caches = makeCaches();
    caches.seed('snowdesk-shell-UNSUBSTITUTED', `${ORIGIN}/static/css/output.css`, new Response('css'));
    const client = makeClient();
    const sw = recordingSw({
      caches,
      fetch: () => Promise.reject(new TypeError('Failed to fetch')),
      clients: [client],
    });

    await sw._staleWhileRevalidate({
      url: `${ORIGIN}/static/css/output.css`,
      method: 'GET',
      mode: 'no-cors',
      destination: 'style',
    });

    expect(await ledger(sw, client)).toContainEqual(
      expect.objectContaining({
        detail: expect.objectContaining({ strategy: 'static', source: 'shell' }),
      }),
    );
  });

  it('tells the offline fallback page apart from the page you asked for', async () => {
    const caches = makeCaches();
    caches.seed(
      'snowdesk-shell-UNSUBSTITUTED',
      '/static/offline.html',
      new Response("<h1>This page isn't available offline</h1>"),
    );
    const client = makeClient();
    const sw = recordingSw({
      caches,
      fetch: () => Promise.reject(new TypeError('offline')),
      clients: [client],
    });

    await sw._networkFirst(navRequest('/map/'));

    // Two different answers to "where did this come from", and the old
    // trace distinguished neither: the page itself from cache, or
    // offline.html standing in for it.
    expect(await ledger(sw, client)).toContainEqual(
      expect.objectContaining({
        detail: expect.objectContaining({ strategy: 'navigate', source: 'offline-html' }),
      }),
    );
  });

  it('records a same-origin API call the worker deliberately never sees', async () => {
    const client = makeClient();
    const sw = recordingSw({
      caches: makeCaches(),
      fetch: answeringTileFetch(),
      clients: [client],
    });
    // SNOW-852: hydrate first. The worker only passes a request through
    // once it KNOWS it is in 'auto', and a freshly-loaded worker knows
    // nothing until this read lands — see the sibling test below, which is
    // about that window specifically.
    await sw._hydrateNetworkMode();

    let responded = false;
    sw.__listeners.fetch({
      request: { url: `${ORIGIN}/api/ratings.json?country=CH`, method: 'GET', mode: 'cors', destination: 'empty' },
      clientId: '',
      respondWith() {
        responded = true;
      },
    });

    // The worker must NOT start intercepting these while it is online —
    // observability is not a good enough reason to put it on the path of
    // every API call, and SNOW-852's fix deliberately kept that true for
    // the common case.
    expect(responded).toBe(false);
    // But "the worker never saw this, it went straight to the network" is
    // itself the answer when a feed comes up empty offline, and the trace
    // said nothing whatsoever about same-origin requests before.
    expect(await ledger(sw, client)).toContainEqual(
      expect.objectContaining({
        detail: expect.objectContaining({ strategy: 'network', source: 'passthrough' }),
      }),
    );
  });

  it('records nothing at all while the trace is off', async () => {
    const caches = makeCaches();
    const client = makeClient();
    // No 'debug-log-enabled' message: the default state for every user.
    const sw = loadSw({ caches, fetch: answeringTileFetch(), clients: [client] });
    caches.seed(sw.BASEMAP_CACHE, TILE, new Response('tile'));

    await sw._basemapStaleWhileRevalidate(tileRequest(TILE));

    expect(await ledger(sw, client)).toEqual([]);
  });
});

describe('offline mode silences the network-only path (SNOW-852)', () => {
  beforeEach(async () => {
    await resetDb();
  });

  afterEach(async () => {
    // Shared DB — a leftover ``network.mode`` row would force an unrelated
    // worker offline in whichever describe happens to run next.
    await resetDb();
  });

  /** A fetch that answers, so a leak is visible as a call rather than a hang. */
  function answeringFetch() {
    return vi.fn(async () => basicResponse('{}'));
  }

  /** One same-origin API GET, of the shape ``_classifySync`` calls 'network'. */
  function apiRequest() {
    return {
      url: `${ORIGIN}/api/ratings.json?country=CH`,
      method: 'GET',
      mode: 'cors',
      destination: 'empty',
    };
  }

  /**
   * Dispatch one fetch event and return what the worker did with it.
   *
   * @param {object} sw Loaded sandbox.
   * @param {object} request Request stub.
   * @returns {Promise<{responded: boolean, response: Response|null}>}
   */
  async function dispatchFetch(sw, request) {
    let promise = null;
    sw.__listeners.fetch({
      request,
      clientId: '',
      respondWith(value) {
        promise = value;
      },
    });
    if (promise === null) return { responded: false, response: null };
    return { responded: true, response: await promise };
  }

  it('answers an API GET with a 504 instead of letting it out, once forced', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    const { responded, response } = await dispatchFetch(sw, apiRequest());

    // The defect was that this request simply left. Now the worker owns it.
    expect(responded).toBe(true);
    expect(response.status).toBe(504);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('blocks a mutation POST the same way, so its row stays queued', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    const { response } = await dispatchFetch(sw, {
      url: `${ORIGIN}/partials/report/`,
      method: 'POST',
      mode: 'cors',
      destination: 'empty',
    });

    // 5xx specifically: the mutation queue treats it as retryable and leaves
    // the row in place, which is the state the queue is built around. A 4xx
    // would read as a permanent rejection and discard the user's report.
    expect(response.status).toBe(504);
    expect(response.status).toBeGreaterThanOrEqual(500);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('blocks it under an auto-latch too, not only a forced mode', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._latchOffline();

    const { response } = await dispatchFetch(sw, apiRequest());

    // The latch means the worker has concluded there is no route. Sending an
    // API call down it would not reach anyone either way, and would hold a
    // connection slot while it failed.
    expect(response.status).toBe(504);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('intercepts before hydration lands, then stops once it says auto', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });

    // The race: a worker Chrome has just restarted holds a DEFAULT mode, not
    // a known one, and a request arriving now must not be waved through on
    // the strength of a value nothing has read yet. This is the window that
    // made a forced mode leak on every worker recycle.
    const during = await dispatchFetch(sw, apiRequest());
    expect(during.responded).toBe(true);

    // Once the read lands on 'auto' the fast path returns, and the worker is
    // off the path of every API call again — which is the cost the original
    // passthrough existed to avoid, and is still avoided.
    await sw._hydrateNetworkMode();
    expect(sw._mayPassThrough()).toBe(true);
    const after = await dispatchFetch(sw, apiRequest());
    expect(after.responded).toBe(false);
  });

  it('recovers a persisted forced mode before answering anything', async () => {
    await setStoredNetworkMode('offline-forced');
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();

    // The whole point of the hydration flag: a restarted worker must come
    // back into the user's mode rather than into 'auto'.
    expect(sw._mayPassThrough()).toBe(false);
    const { response } = await dispatchFetch(sw, apiRequest());
    expect(response.status).toBe(504);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('passes through again as soon as the user switches offline mode off', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });
    await sw._hydrateNetworkMode();
    sw._forceOffline();
    expect(sw._mayPassThrough()).toBe(false);

    sw._unlatchOffline();

    // Reversibility is the safety-critical half: a user stuck offline is
    // reading cached avalanche ratings with no way to refresh them.
    expect(sw._mayPassThrough()).toBe(true);
    const { responded } = await dispatchFetch(sw, apiRequest());
    expect(responded).toBe(false);
  });

  // -- SNOW-862: the radio, not just the switch --------------------------
  //
  // ``_shouldUseNetwork`` has always been false when the interface is down.
  // ``_mayPassThrough`` was not, so with a dead radio and the switch
  // untouched every read path refused while API GETs, fragments and mutation
  // POSTs went to the browser anyway. Nothing else covered it: the latch is
  // evidence from three read-path TIMEOUTS, and a dead radio rejects rather
  // than hangs, so it never fires.

  /**
   * Run ``fn`` with ``navigator.onLine`` forced to ``value``.
   *
   * jsdom defines ``onLine`` as a prototype getter that always answers true,
   * so it is replaced for the duration rather than assigned — an assignment
   * silently no-ops against an accessor with no setter, and the test would
   * pass for the wrong reason.
   *
   * @param {boolean} value
   * @param {Function} fn
   */
  async function withOnLine(value, fn) {
    const spy = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(value);
    try {
      await fn();
    } finally {
      spy.mockRestore();
    }
  }

  it('does not pass a request through while the interface is down', async () => {
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();

    // No offline mode at all: the mode is 'auto' and hydrated, which is
    // precisely the state that used to wave this straight past the worker.
    await withOnLine(false, async () => {
      expect(sw._mayPassThrough()).toBe(false);
      const { responded, response } = await dispatchFetch(sw, apiRequest());
      expect(responded).toBe(true);
      expect(response.status).toBe(504);
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  it('blocks a mutation POST on a dead radio, leaving its row queued', async () => {
    // 5xx rather than a rejection changes what the queue SEES, so the shape
    // matters: mutation_queue_core classifies status >= 500 as 'retry', which
    // is the same outcome a network error produced. A 4xx here would discard
    // the user's report the moment they walked into a tunnel.
    const fetchSpy = answeringFetch();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();

    await withOnLine(false, async () => {
      const { response } = await dispatchFetch(sw, {
        url: `${ORIGIN}/partials/report/`,
        method: 'POST',
        mode: 'cors',
        destination: 'empty',
      });
      expect(response.status).toBeGreaterThanOrEqual(500);
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  it('agrees with _shouldUseNetwork about a dead radio', async () => {
    // The invariant the defect broke. These two answer the same question —
    // one synchronously for the respondWith decision, one after hydration —
    // and a state where they disagree is a hole by construction, whatever
    // that state happens to be.
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });
    await sw._hydrateNetworkMode();

    await withOnLine(false, async () => {
      expect(sw._mayPassThrough()).toBe(false);
      expect(await sw._shouldUseNetwork()).toBe(false);
    });
  });

  it('passes through again the moment the interface comes back', async () => {
    // ``onLine`` is trusted in the negative only. Once it stops saying false
    // the worker must get off the path of every API call — that passthrough
    // is the ordinary case, and paying for interception in it is the cost
    // SNOW-852 went to some trouble to avoid.
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });
    await sw._hydrateNetworkMode();

    await withOnLine(false, async () => {
      expect(sw._mayPassThrough()).toBe(false);
    });

    expect(sw._mayPassThrough()).toBe(true);
    const { responded } = await dispatchFetch(sw, apiRequest());
    expect(responded).toBe(false);
  });

  it('treats an unreadable mode row as a settled answer, not a permanent unknown', async () => {
    await resetDbWithoutMetaStore();
    const sw = loadSw({ caches: makeCaches(), fetch: answeringFetch() });
    await sw._hydrateNetworkMode();

    // A worker-created DB has no ``meta:app`` store, so the read throws and
    // the worker stays in 'auto' by design. That is an answer. Leaving the
    // hydration flag false would put the worker on the intercepting path for
    // the rest of its life over one DB it could not open.
    expect(sw._mayPassThrough()).toBe(true);
  });
});

describe("_guardedRespond's recovery re-fetch respects offline mode (SNOW-859)", () => {
  /*
   * The last fetch call site in sw.js that did not consult the mode, and the
   * only one that is unreachable today: _guardedRespond re-fetches when a
   * strategy resolves to something that is not a Response, and all three
   * wrapped strategies always resolve to one — the synthesized 504s included.
   *
   * Covered anyway. "It cannot happen" is the reasoning that left SNOW-854's
   * branch unguarded through two tickets, and the failure here is the worse
   * shape: a recovery path spends the network exactly when something else has
   * already broken, so the offline promise goes with it and the blame lands on
   * the regression rather than on the recovery.
   */

  beforeEach(async () => {
    await resetDb();
  });

  afterEach(async () => {
    await resetDb();
  });

  const ASSET = `${ORIGIN}/static/css/output.css`;

  /** A request of the shape the fetch handler hands the wrapper. */
  function assetRequest() {
    return { url: ASSET, method: 'GET', mode: 'cors', destination: 'style' };
  }

  /** The client stub _postTelemetry broadcasts to via matchAll. */
  function makeClient() {
    const seen = [];
    return { postMessage: (data) => seen.push(data), seen };
  }

  it('answers 504 rather than re-fetching, once offline mode is in force', async () => {
    const fetchSpy = vi.fn(async () => basicResponse('recovered bytes'));
    const client = makeClient();
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy, clients: [client] });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    // A strategy that has gone wrong: the one precondition of this branch.
    const response = await sw._guardedRespond(
      Promise.resolve(undefined),
      assetRequest(),
    );

    expect(response.status).toBe(504);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('still reports the anomaly it was refused permission to recover from', async () => {
    // The ordering that matters inside the branch. Refusing the re-fetch must
    // not also swallow the telemetry: a strategy resolving to a non-Response
    // is a defect worth knowing about whether or not the app is allowed to
    // paper over it, and offline is when it is hardest to notice otherwise.
    const client = makeClient();
    const sw = loadSw({
      caches: makeCaches(),
      fetch: vi.fn(async () => basicResponse('recovered bytes')),
      clients: [client],
    });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    await sw._guardedRespond(Promise.resolve(undefined), assetRequest());
    await flush();

    expect(client.seen).toContainEqual(
      expect.objectContaining({ type: 'pwa-telemetry', event: 'pwa.sw.fetch_undefined' }),
    );
  });

  it('still recovers normally while the worker is online', async () => {
    // The guard is the mode, not a disabling of the recovery. A page whose
    // strategy misfired on a live connection should still get its asset.
    const fetchSpy = vi.fn(async () => basicResponse('recovered bytes'));
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy, clients: [makeClient()] });
    await sw._hydrateNetworkMode();

    const response = await sw._guardedRespond(
      Promise.resolve(undefined),
      assetRequest(),
    );

    expect(await response.text()).toBe('recovered bytes');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('passes a real Response through untouched in either mode', async () => {
    // The wrapper must not start answering 504 for responses that were fine.
    // Offline mode reaches it constantly — every guarded strategy returns its
    // own synthesized 504 through here — and turning those into a second,
    // freshly-minted 504 would lose whichever partition actually answered.
    const sw = loadSw({ caches: makeCaches(), fetch: vi.fn() });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    const served = basicResponse('cached bytes');
    const response = await sw._guardedRespond(Promise.resolve(served), assetRequest());

    expect(response).toBe(served);
  });
});

describe('the Background-Sync drain respects offline mode (SNOW-852)', () => {
  beforeEach(async () => {
    await resetDb();
  });

  afterEach(async () => {
    await resetDb();
  });

  it('replays nothing while a forced mode is in force', async () => {
    const fetchSpy = vi.fn(async () => basicResponse('{}'));
    const sw = loadSw({ caches: makeCaches(), fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    await sw._selfDrainMutations();

    // This path needs its own guard and could not inherit the fetch
    // handler's: a worker's own fetch() never fires its own fetch event, so
    // the requests it makes never reach the branch SNOW-852 fixed. It is
    // also the harder half to notice — it runs only when Background Sync
    // fires with no window open, so there is no surface left to show the
    // user their connection being spent.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('still replays when the app is online', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: vi.fn(async () => basicResponse('{}')) });
    await sw._hydrateNetworkMode();

    // The guard must not be a permanent off switch: with no offline mode in
    // force the drain has to reach the rows as it always did. Asserting it
    // gets past the guard at all is the point here — the replay itself is
    // covered by the existing Background-Sync tests above.
    await expect(sw._selfDrainMutations()).resolves.not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// SNOW-874 — the push handler's notification options
// ---------------------------------------------------------------------------

describe('push handler', () => {
  /**
   * Fire the real ``push`` listener and resolve whatever it holds the event
   * open for, so an assertion runs after ``showNotification`` was called.
   *
   * @param {object} sw The loaded worker.
   * @param {object} payload The JSON body the push service delivered.
   */
  async function dispatchPush(sw, payload) {
    let held;
    sw.__listeners.push({
      data: { json: () => payload, text: () => JSON.stringify(payload) },
      waitUntil: (promise) => {
        held = promise;
      },
    });
    await held;
  }

  it('asks for a re-alert so a replacement is not silent', async () => {
    const shown = vi.fn(() => Promise.resolve());
    const sw = loadSw({ showNotification: shown });

    await dispatchPush(sw, { title: 'Snowdesk', body: 'Fresh bulletin', url: '/x/' });

    // The tag and renotify are a pair, and the pair is the point: the tag
    // collapses repeat pushes onto one entry, and without renotify the
    // browser swaps that entry in without alerting. On /_push-demo/ that
    // makes the second and every later test send look like a dead pipeline
    // — which is exactly how an hour went on 2026-09-08.
    expect(shown).toHaveBeenCalledWith(
      'Snowdesk',
      expect.objectContaining({ tag: 'snowdesk-push', renotify: true }),
    );
  });

  it('badges with the alpha silhouette, not the tile', async () => {
    const shown = vi.fn(() => Promise.resolve());
    const sw = loadSw({ showNotification: shown });

    await dispatchPush(sw, { title: 'Snowdesk', body: 'b', url: '/' });

    // Android throws away the badge's colours and keeps its alpha, so
    // handing it icon-192 — opaque across its whole rounded rect — put a
    // solid white square in the status bar. The distinction is invisible
    // in every other surface, which is why it needs asserting here rather
    // than left to whoever next reads the handler.
    // tests/public/test_pwa_icons.py asserts the file itself has an alpha
    // channel and is a genuine silhouette; this pins the reference to it.
    const [, options] = shown.mock.calls[0];
    expect(options.badge).toBe('/static/icons/pwa/badge-96.png');
    expect(options.icon).toBe('/static/icons/pwa/icon-192.png');
  });

  it('carries the payload through to the notification', async () => {
    const shown = vi.fn(() => Promise.resolve());
    const sw = loadSw({ showNotification: shown });

    await dispatchPush(sw, { title: 'Verbier', body: 'Level 3', url: '/ch-4115/' });

    const [title, options] = shown.mock.calls[0];
    expect(title).toBe('Verbier');
    expect(options.body).toBe('Level 3');
    // notificationclick reads the URL back off data, so a drop here would
    // send every tap to the map root instead of the bulletin.
    expect(options.data).toEqual({ url: '/ch-4115/' });
  });
});

describe('an unpopulated region tier is never written to the shell cache (SNOW-902)', () => {
  it('skips the put when the FeatureCollection is empty', async () => {
    // An empty answer from these paths means this deployment has no geometry
    // for that country YET. The server refuses to give it a stale window or a
    // day-long memo, but neither reaches here: this strategy serves the cached
    // entry first and stores every ok response whatever its headers say, so an
    // empty one, once written, was replayed on every load.
    const cachesStub = makeCaches();
    const url = `${ORIGIN}/api/regions.geojson?country=it`;
    const empty = JSON.stringify({ type: 'FeatureCollection', features: [] });
    const sw = loadSw({
      caches: cachesStub,
      fetch: () => Promise.resolve(basicResponse(empty)),
    });

    const response = await sw._staleWhileRevalidate(new Request(url));
    // The write is decided after the body is read, so let the microtasks
    // and the pending put settle before asking what landed.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(JSON.parse(await response.text()).features).toEqual([]);
    expect(cachesStub.size('snowdesk-shell-UNSUBSTITUTED')).toBe(0);
  });

  it('still writes the same path once it has geometry', async () => {
    const cachesStub = makeCaches();
    const url = `${ORIGIN}/api/regions.geojson?country=it`;
    const populated = JSON.stringify({
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: { id: 'IT-21' }, geometry: null }],
    });
    const sw = loadSw({
      caches: cachesStub,
      fetch: () => Promise.resolve(basicResponse(populated)),
    });

    await sw._staleWhileRevalidate(new Request(url));
    // The write is decided after the body is read, so let the microtasks
    // and the pending put settle before asking what landed.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(cachesStub.size('snowdesk-shell-UNSUBSTITUTED')).toBe(1);
  });
});

describe('re-warming the shell after an activation (SNOW-912)', () => {
  // `activate` deletes every shell cache that is not the version now live,
  // and until this ticket nothing put the map page back. Between a deploy
  // activating and the user's next connected visit to `/`, the app could
  // not open offline at all — silently, on every deploy. These tests are
  // about the two halves of the repair: what a page needs in order to be
  // more than a blank frame, and the activation-time call that fetches it.
  const SHELL_CACHE = 'snowdesk-shell-UNSUBSTITUTED';
  const MAP_URL = `${ORIGIN}/`;
  const SCRIPT_URL = `${ORIGIN}/static/js/map.abc123.js`;
  const STYLE_URL = `${ORIGIN}/static/css/output.def456.css`;
  const PAGE_DAY = '2026-09-11';
  const MAP_HTML = [
    '<meta name="pwa-user-id" content="acct-9">',
    `<link rel="stylesheet" href="${STYLE_URL}">`,
    '<script src="/static/js/map.abc123.js"></script>',
    '<script src="https://cdn.example/vendor.js"></script>',
    '<link rel="icon" href="/static/img/icon.png">',
    // SNOW-912: the day this page will open on, whatever the date is when
    // somebody does. Its boot puts this in the ratings URL.
    `<div id="season-scrubber" data-today="${PAGE_DAY}"></div>`,
  ].join('\n');
  const DATED_RATINGS = `${ORIGIN}/api/ratings/?d=${PAGE_DAY}&country=ch`;
  const SEASON_RATINGS = `${ORIGIN}/api/ratings/?country=ch`;
  const REGIONS = `${ORIGIN}/api/regions.geojson?country=ch`;

  /** A same-origin response of a chosen type, which `_warmCache` requires. */
  function typed(body, contentType) {
    return basicResponse(body, { headers: { 'Content-Type': contentType } });
  }

  /** A fetch stub answering the map page with HTML and everything else with bytes. */
  function shellFetch(seen) {
    return async (request) => {
      const url = typeof request === 'string' ? request : request.url;
      if (seen) seen.push(url);
      if (url === MAP_URL) return typed(MAP_HTML, 'text/html; charset=utf-8');
      if (url.indexOf('/api/') >= 0) return typed('{"ok":true}', 'application/json');
      return typed('asset bytes', 'text/javascript');
    };
  }

  it('names the same-origin scripts and styles the page asks for', () => {
    const sw = loadSw();

    expect(sw._shellSubresources(MAP_HTML)).toEqual([STYLE_URL, SCRIPT_URL]);
  });

  it('drops the cross-origin ones rather than fetching somebody else’s cache policy', () => {
    const sw = loadSw();

    expect(sw._shellSubresources(MAP_HTML)).not.toContain('https://cdn.example/vendor.js');
  });

  it('reads the same page the same way twice', () => {
    // The shared /g literal carries `lastIndex` between calls, so a reused
    // regex would start the second page halfway down its own HTML — and
    // the second page is every activation after the first in a worker's life.
    const sw = loadSw();

    expect(sw._shellSubresources(MAP_HTML)).toEqual(sw._shellSubresources(MAP_HTML));
  });

  it('caps what one page can send the worker after', () => {
    const sw = loadSw();
    const many = Array.from(
      { length: sw.SHELL_SUBRESOURCE_LIMIT + 40 },
      (_unused, i) => `<script src="/static/js/mod-${i}.js"></script>`,
    ).join('\n');

    expect(sw._shellSubresources(many)).toHaveLength(sw.SHELL_SUBRESOURCE_LIMIT);
  });

  it('agrees with the audit core about what a page needs', async () => {
    // Two implementations on purpose: the worker is a classic script and
    // would have to importScripts the whole report module to share one.
    // So they are held together here instead, over the same inputs — the
    // shape test_sw.js already uses for sw.js's inline core fallbacks. A
    // drift means the report verifies a page against a different list
    // from the one the warm fetches, which is how a row goes green over a
    // page that will not open.
    const { pageDependencies } = self.pwaOfflineAuditCore;
    const sw = loadSw();
    const CASES = [
      MAP_HTML,
      '',
      '<script src="/a.js"></script><script src="/a.js"></script>',
      '<link rel="preload" href="/static/css/x.css"><img src="/static/img/y.png">',
      '<script src="https://cdn.example/v.js"></script>',
      "<script src='/single.js'></script>",
      '<link rel="stylesheet" href="/static/css/q.css?v=2">',
      '<script src="/static/js/a.js"></script><link href="/static/css/a.css">',
    ];

    for (const html of CASES) {
      expect(sw._shellSubresources(html), html.slice(0, 40)).toEqual(
        pageDependencies(html, ORIGIN),
      );
    }
  });

  it('warms the page AND what it needs to open', async () => {
    const stub = makeCaches();
    const sw = loadSw({ caches: stub, fetch: shellFetch() });

    await sw._warmCache([MAP_URL]);

    const cache = await stub.open(SHELL_CACHE);
    // The page alone is the failure this ticket is about in miniature:
    // HTML with no JavaScript paints a blank frame, which to the person
    // holding the phone is a page that did not open.
    expect(await cache.match(MAP_URL)).toBeTruthy();
    expect(await cache.match(SCRIPT_URL)).toBeTruthy();
    expect(await cache.match(STYLE_URL)).toBeTruthy();
  });

  it('leaves the warmed page’s principal stamp alone', async () => {
    const stub = makeCaches();
    const sw = loadSw({ caches: stub, fetch: shellFetch() });

    await sw._warmCache([MAP_URL]);

    const cache = await stub.open(SHELL_CACHE);
    const hit = await cache.match(MAP_URL);
    expect(hit.headers.get(sw.PRINCIPAL_HEADER)).toBe('acct-9');
  });

  it('does not re-fetch what the cache already holds', async () => {
    const stub = makeCaches();
    stub.seed(SHELL_CACHE, SCRIPT_URL, basicResponse('already here'));
    stub.seed(SHELL_CACHE, STYLE_URL, basicResponse('already here'));
    const seen = [];
    const sw = loadSw({ caches: stub, fetch: shellFetch(seen) });

    await sw._warmCache([MAP_URL]);

    // On any device that has simply opened the app these are all present,
    // and the run costs one `match` each and no network at all. The feeds
    // are still fetched — they are this ticket's whole point, and the
    // fixture seeds only the assets.
    expect(seen.filter((url) => url.indexOf('/api/') === -1)).toEqual([MAP_URL]);
  });

  it('leaves a feed warm untouched — only HTML pulls subresources', async () => {
    const stub = makeCaches();
    const seen = [];
    const sw = loadSw({
      caches: stub,
      fetch: async (request) => {
        const url = typeof request === 'string' ? request : request.url;
        seen.push(url);
        return typed('{"ok":true}', 'application/json');
      },
    });

    await sw._warmCache([`${ORIGIN}/api/ratings/`]);

    expect(seen).toEqual([`${ORIGIN}/api/ratings/`]);
  });

  it('warms the feeds the page’s own boot will ask for', async () => {
    // The invariant: a cached page and the feeds its boot asks for are
    // cached together, or the map opens grey. `activate` deletes the feeds
    // with the rest of the old shell, and SNOW-912's re-warm put only the
    // PAGE back — so an offline open would have drawn a map with no danger
    // ratings and no region outlines.
    const stub = makeCaches();
    const sw = loadSw({ caches: stub, fetch: shellFetch() });

    await sw._warmCache([MAP_URL]);

    const cache = await stub.open(SHELL_CACHE);
    expect(await cache.match(DATED_RATINGS)).toBeTruthy();
    expect(await cache.match(SEASON_RATINGS)).toBeTruthy();
    expect(await cache.match(REGIONS)).toBeTruthy();
  });

  it('asks for the day the PAGE names, not the day it is warmed on', async () => {
    // `data-today` is server-rendered per request, so a cached page carries
    // the day it was fetched on for as long as it sits there — and that is
    // the day its boot will put in the ratings URL.
    const sw = loadSw();

    expect(sw._shellBootFeeds(MAP_HTML)).toContain(DATED_RATINGS);
  });

  it('skips the dated feed for a page that names no day', async () => {
    // `readDisplayDate()` returns null there and the map paints nothing
    // whatever is cached, so there is no dated feed worth fetching.
    const sw = loadSw();
    const feeds = sw._shellBootFeeds('<html><body>no scrubber</body></html>');

    expect(feeds).toEqual([REGIONS, SEASON_RATINGS]);
  });

  it('agrees with the audit core about which day a page opens on', async () => {
    // The report verifies the feed this warm fetches. A drift between the
    // two readings means the row goes green over a day nothing warmed.
    const { pageDay } = self.pwaOfflineAuditCore;
    const sw = loadSw();
    const CASES = [
      MAP_HTML,
      '',
      '<div id="season-scrubber" data-today="2026-01-02"></div>',
      "<div data-today='2026-01-02' id='season-scrubber'></div>",
      '<div id="other" data-today="2026-01-02"></div>',
      '<div id="season-scrubber" data-today="not-a-date"></div>',
    ];

    for (const html of CASES) {
      expect(sw._shellPageDay(html), html.slice(0, 40)).toEqual(pageDay(html));
    }
  });

  it('does not re-fetch a feed the cache already holds', async () => {
    const stub = makeCaches();
    stub.seed(SHELL_CACHE, DATED_RATINGS, basicResponse('already here'));
    const seen = [];
    const sw = loadSw({ caches: stub, fetch: shellFetch(seen) });

    await sw._warmCache([MAP_URL]);

    expect(seen).not.toContain(DATED_RATINGS);
  });

  it('spends nothing on a device the user has switched offline', async () => {
    await resetDb();
    const stub = makeCaches();
    const fetchSpy = vi.fn(async () => typed(MAP_HTML, 'text/html'));
    const sw = loadSw({ caches: stub, fetch: fetchSpy });
    await sw._hydrateNetworkMode();
    sw._forceOffline();

    await sw._rewarmShell();

    // The user said not to use this connection. The shell is re-warmed on
    // the next activation, or by the audit panel's own Save control.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('stops waiting on a radio that hangs rather than rejecting', async () => {
    // SNOW-742's subject, reached from a lifecycle handler: `_warmCache`'s
    // own fetches are deliberately unbounded (a thousand-tile download
    // must not fail at the read paths' budget), so a fetch that never
    // settles would hold `activate`'s waitUntil open for as long as the
    // browser allows.
    await resetDb();
    const stub = makeCaches();
    const sw = loadSw({ caches: stub, fetch: () => new Promise(() => {}) });
    // Hydrated BEFORE the clock is faked: the hydration is memoised, and
    // fake-indexeddb schedules its own work on real timers, so a database
    // read taken under a fake clock never settles.
    await sw._hydrateNetworkMode();
    vi.useFakeTimers();
    try {
      const rewarming = sw._rewarmShell();
      await vi.advanceTimersByTimeAsync(60000);

      await expect(rewarming).resolves.toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it('puts the map page back when there is a connection to do it on', async () => {
    await resetDb();
    const stub = makeCaches();
    const sw = loadSw({ caches: stub, fetch: shellFetch() });
    await sw._hydrateNetworkMode();

    await sw._rewarmShell();

    const cache = await stub.open(SHELL_CACHE);
    expect(new URL(sw.SHELL_PAGE, ORIGIN).toString()).toBe(MAP_URL);
    expect(await cache.match(MAP_URL)).toBeTruthy();
    expect(await cache.match(SCRIPT_URL)).toBeTruthy();
  });
});

describe('answering whether the app would open offline (SNOW-922)', () => {
  /*
   * The guard behind the Offline mode switch. Under 'offline-forced' this
   * worker refuses every navigation and answers from cache, so a device
   * with no shell page cached for the current principal reaches
   * static/offline.html and nothing else — and the mode is persisted and
   * re-hydrated on every boot, so it survives restarts. Until SNOW-922 the
   * only control that ended that state lived inside the app that would not
   * open.
   *
   * This answers the question `_networkFirstFallback` would answer for a
   * navigation to `/`, and the tests below are the same four cases that
   * function has: a hit, a searchless hit, a wrong principal, and nothing
   * at all. Keeping them here rather than on the page side is the point —
   * one implementation of the rule, not two that drift.
   */

  beforeEach(async () => {
    await resetDb();
  });

  it('says yes when the map page is cached for the principal signed in now', async () => {
    const caches = makeCaches();
    const online = basicResponse(pageHtml('acct-uuid-a', 'the map'));
    const sw = loadSw({ caches, fetch: () => Promise.resolve(online) });

    await sw._networkFirst(navRequest('/'));
    await flush();
    await setStoredPrincipal('acct-uuid-a');

    expect(await sw._canOpenOffline()).toBe(true);
  });

  it('says no when the cached page belongs to another account', async () => {
    // Exactly the refusal `_networkFirstFallback` makes, and the reason
    // this cannot be answered by "is there an entry for /".
    const caches = makeCaches();
    const online = basicResponse(pageHtml('acct-uuid-a', 'the map'));
    const sw = loadSw({ caches, fetch: () => Promise.resolve(online) });

    await sw._networkFirst(navRequest('/'));
    await flush();
    await setStoredPrincipal('acct-uuid-b');

    expect(await sw._canOpenOffline()).toBe(false);
  });

  it('says no on a device with nothing cached at all', async () => {
    const sw = loadSw({ caches: makeCaches() });

    expect(await sw._canOpenOffline()).toBe(false);
  });

  it('does not count the offline fallback page as the app opening', async () => {
    // offline.html is precached, carries no principal, and is the page
    // whose appearance means the app did NOT open. Counting it would
    // answer yes on every device that has ever installed the worker —
    // which is every device that can reach this question.
    const caches = makeCaches();
    caches.seed(
      'snowdesk-shell-UNSUBSTITUTED',
      '/static/offline.html',
      new Response("<h1>This page isn't available offline</h1>"),
    );
    const sw = loadSw({ caches });

    expect(await sw._canOpenOffline()).toBe(false);
  });

  it('accepts a shell cached under a dated URL, as the fallback does', async () => {
    // `/?d=2026-01-23` and `/` share one cached shell — the date is read
    // back off location.search by page JS. A searchless match is what
    // `_networkFirstFallback` uses, so this has to agree.
    const caches = makeCaches();
    const online = basicResponse(pageHtml('anonymous', 'the map'));
    const sw = loadSw({ caches, fetch: () => Promise.resolve(online) });

    await sw._networkFirst(navRequest('/?d=2026-01-23'));
    await flush();

    expect(await sw._canOpenOffline()).toBe(true);
  });

  it('answers false rather than throwing when Cache Storage is unusable', async () => {
    // A false costs one extra confirmation press; a thrown promise would
    // leave the switch mid-flight.
    const sw = loadSw({
      caches: {
        open: () => Promise.reject(new Error('no storage')),
        keys: () => Promise.resolve([]),
      },
    });

    expect(await sw._canOpenOffline()).toBe(false);
  });

  it('replies down the port the asker transferred, not to every client', async () => {
    // One page's question about its own next action, not worker state
    // anybody else needs — so it does not go through `_publishNetworkMode`.
    const sw = loadSw({ caches: makeCaches() });
    const replies = [];

    sw.__listeners.message({
      data: { type: 'can-open-offline' },
      ports: [{ postMessage: (data) => replies.push(data) }],
      waitUntil: () => {},
    });
    await flush();

    expect(replies).toEqual([{ type: 'can-open-offline', canOpen: false }]);
  });

  it('does not throw when the asker transferred no port', async () => {
    // A caller with no port gets no reply and falls back to its own
    // budget, which answers false — the safe direction.
    const sw = loadSw({ caches: makeCaches() });

    expect(() =>
      sw.__listeners.message({ data: { type: 'can-open-offline' }, waitUntil: () => {} }),
    ).not.toThrow();
    await flush();
  });
});
