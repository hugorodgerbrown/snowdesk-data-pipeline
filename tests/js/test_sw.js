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

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/basemap_cache_core.js';

const core = self.pwaBasemapCacheCore;

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
  'PRINCIPAL_ANONYMOUS',
  'PRINCIPAL_UNKNOWN',
  'PRINCIPAL_HEADER',
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
  const selfStub = {
    location: { origin: ORIGIN },
    addEventListener: () => {},
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
  return factory(
    selfStub,
    options.caches || makeCaches(),
    options.fetch || (() => Promise.reject(new TypeError('Failed to fetch'))),
  );
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
  const counters = { keys: 0 };
  const entriesFor = (name) => {
    if (!buckets.has(name)) buckets.set(name, new Map());
    return buckets.get(name);
  };
  return {
    open: async (name) => {
      const entries = entriesFor(name);
      return {
        async match(request, matchOptions) {
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

    await sw._networkFirst(navRequest('/account/manage/'));
    await flush();

    expect(caches.size('snowdesk-shell-UNSUBSTITUTED')).toBe(0);
  });

  it('caches an ordinary navigation, stamped with the rendering principal', async () => {
    const caches = makeCaches();
    const online = basicResponse(pageHtml('acct-uuid-a', 'account dashboard'));
    const sw = loadSw({ caches, fetch: () => Promise.resolve(online) });

    await sw._networkFirst(navRequest('/account/manage/'));
    await flush();

    const cache = await caches.open('snowdesk-shell-UNSUBSTITUTED');
    const stored = await cache.match(navRequest('/account/manage/'));
    expect(stored.headers.get(sw.PRINCIPAL_HEADER)).toBe('acct-uuid-a');
  });

  it('refuses a cached page rendered for a different principal', async () => {
    const caches = makeCaches();
    const request = navRequest('/account/manage/');
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
    const request = navRequest('/account/manage/');
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

    await sw._networkFirst(navRequest('/account/manage/'));
    await flush();
    await setStoredPrincipal(null);
    online = null;

    const offline = await sw._networkFirst(navRequest('/account/manage/?tab=passkeys'));
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
