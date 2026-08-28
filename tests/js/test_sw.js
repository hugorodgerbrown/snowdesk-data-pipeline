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
 *        request-matched reads); the full browser journey is
 *        tests/e2e/test_offline_account_principal.py.
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
  '_boundedFetch',
  '_shouldUseNetwork',
  '_latchOffline',
  '_unlatchOffline',
  '_probeNetwork',
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
      matchAll: () => Promise.resolve([]),
    },
    registration: { showNotification: () => Promise.resolve() },
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
    vi.useFakeTimers();
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
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Drive one hanging basemap read to its budget expiry. */
  async function timeOutOneRead(sw) {
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
      expect(sw._shouldUseNetwork()).toBe(true);
    }
    await timeOutOneRead(sw);

    expect(sw._shouldUseNetwork()).toBe(false);
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
    expect(sw._shouldUseNetwork()).toBe(true);
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
    expect(sw._shouldUseNetwork()).toBe(false);

    answer = true;
    await sw._probeNetwork();

    expect(sw._shouldUseNetwork()).toBe(true);
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
    expect(sw._shouldUseNetwork()).toBe(false);
  });

  it('honours an explicit latch with no timeouts, and an explicit unlatch', async () => {
    const sw = loadSw({ caches: makeCaches(), fetch: hangingFetch() });

    // Pre-arming before a tunnel: no failed request has happened at all.
    sw._latchOffline();
    expect(sw._shouldUseNetwork()).toBe(false);

    sw._unlatchOffline();
    expect(sw._shouldUseNetwork()).toBe(true);
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
