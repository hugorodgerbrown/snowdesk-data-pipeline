/*
 * tests/js/test_sw_register_warm_cache.js — Vitest unit tests for
 * `warmCache()`'s controller handling in static/js/sw_register.js (SNOW-605).
 *
 * The bug these cover: `warmCache()` read `navigator.serviceWorker.controller`
 * once and resolved `null` if it was absent, which the download controls
 * report as "Download failed. Check your connection and try again." having
 * dispatched nothing. A page is uncontrolled during an SW update's activation
 * window, and for its whole life after a shift-reload — neither of which is a
 * connection problem, and neither of which made a single request.
 *
 * The module is a load-time IIFE that defines `window.pwaWarmCache` with
 * `configurable: false`, so it can only be imported ONCE per jsdom window —
 * a second import would throw on the redefine. Hence one import for the file,
 * with each test driving behaviour by mutating the service-worker stub, which
 * `_activeWorker()` reads live on every call.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// SNOW-620 marshalled the module's user-facing strings through
// `self.pwaStrings`, which `sw_register.js` reads at load — importing it
// first is the convention the sibling sw_register tests use.
import '../../static/js/i18n_strings.js';

/** The `postMessage`d payloads seen by the stubbed active worker. */
let posted = [];
/** Stands in for `navigator.serviceWorker`; an EventTarget for controllerchange. */
let swStub;

/** A stubbed active worker that records what `warmCache` posts to it. */
function makeWorker() {
  return { postMessage: (msg) => posted.push(msg) };
}

/**
 * Let every pending microtask run.
 *
 * `warmCache` awaits the queue (SNOW-951 review) and then `_activeWorker`
 * before it posts, so the number of hops between the call and the message
 * is an implementation detail no test should be counting. Drain instead.
 *
 * @returns {Promise<void>}
 */
async function flush() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

/**
 * Answer the worker's side of a run so it settles.
 *
 * Every test that starts a run must end it. Runs are SERIALISED now, so a
 * leaked one is not merely an untidy slot — it holds the queue, and every
 * later call in the file waits behind it for a 30-second silence timeout
 * that fake timers in a later test will never advance.
 *
 * @param {string} requestId
 * @param {Object} [extra]
 * @returns {void}
 */
function settle(requestId, extra) {
  swStub.dispatchEvent(
    new MessageEvent('message', {
      data: Object.assign(
        { type: 'warm-cache-done', ok: 0, failed: 0, reason: null, bytes: 0, requestId },
        extra || {},
      ),
    }),
  );
}

beforeAll(async () => {
  swStub = new EventTarget();
  swStub.controller = null;
  swStub.ready = Promise.resolve({ active: makeWorker() });
  swStub.register = () =>
    Promise.resolve({ waiting: null, installing: null, addEventListener: () => {} });
  swStub.getRegistrations = () => Promise.resolve([]);
  Object.defineProperty(navigator, 'serviceWorker', {
    value: swStub,
    configurable: true,
  });
  // The IIFE fetches /api/sw-config before registering.
  vi.stubGlobal('fetch', () =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ enabled: true, sw_url: '/sw.js', kill: false }),
    }),
  );
  // SNOW-623: the module resolves #sw-update-banner once at load and
  // synthesises an inline-styled fallback if it finds none — give it the
  // public partial's element so it takes the same path the site does.
  document.body.innerHTML = '<div id="sw-update-banner" class="hidden"></div>';
  await import('../../static/js/sw_register.js');
});

beforeEach(() => {
  posted = [];
  swStub.controller = null;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('pwaWarmCacheCancel() with no run ever started (SNOW-632)', () => {
  it('is a safe no-op', async () => {
    // Runs before any other describe block in this file has called
    // window.pwaWarmCache() — the module's `_warmCacheSlot` is still at
    // its load-time `null`, which is the state this test means to cover.
    await window.pwaWarmCacheCancel();

    expect(posted).toHaveLength(0);
  });
});

describe('warmCache() with a controller present', () => {
  it('posts the warm-cache message to the active worker', async () => {
    swStub.controller = makeWorker();
    const promise = window.pwaWarmCache(['/a.mvt', '/b.mvt'], {
      pinned: true,
      areaId: 'region-CH-4115',
    });
    await flush();
    expect(posted).toHaveLength(1);
    expect(posted[0].type).toBe('warm-cache');
    expect(posted[0].urls).toEqual(['/a.mvt', '/b.mvt']);
    expect(posted[0].pinned).toBe(true);
    expect(posted[0].areaId).toBe('region-CH-4115');

    settle(posted[0].requestId);
    await promise;
  });
});

describe('two warmCache() calls at once (SNOW-951 review)', () => {
  it('runs the second only once the first has settled', async () => {
    swStub.controller = makeWorker();
    const first = window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'region-CH-4115' });
    const second = window.pwaWarmCache(['/b.mvt'], { pinned: true, areaId: 'custom-a1' });
    await flush();

    // The bug: both dispatched, the second overwrote the slot's
    // requestId, and the worker's reply to the first no longer matched
    // anything — so the first caller sat out its whole silence timeout
    // and reported a failure while its documents were landing on disk.
    expect(posted).toHaveLength(1);
    expect(posted[0].areaId).toBe('region-CH-4115');

    settle(posted[0].requestId, { ok: 1 });
    const firstResult = await first;
    // The first gets the worker's ACTUAL answer, not a timeout.
    expect(firstResult.ok).toBe(1);
    expect(firstResult.reason).toBeNull();

    await flush();
    expect(posted).toHaveLength(2);
    expect(posted[1].areaId).toBe('custom-a1');
    expect(posted[1].requestId).not.toBe(posted[0].requestId);

    settle(posted[1].requestId, { ok: 1 });
    expect((await second).ok).toBe(1);
  });

  it('does not let one caller wedge the queue for every later one', async () => {
    swStub.controller = makeWorker();
    // A rejection cannot come out of `_warmCacheRun`, but the chain must
    // survive one anyway: a wedged queue is every download on the device
    // silently doing nothing until a reload.
    const first = window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'x' });
    await flush();
    settle(posted[0].requestId);
    await first;

    const second = window.pwaWarmCache(['/b.mvt'], { pinned: true, areaId: 'y' });
    await flush();
    expect(posted).toHaveLength(2);
    settle(posted[1].requestId);
    await second;
  });
});

describe('warmCache() on an uncontrolled page', () => {
  it('resolves reason "no-worker" rather than null once the wait elapses', async () => {
    vi.useFakeTimers();
    const promise = window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'x' });
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;
    // null used to be the answer here, and it is indistinguishable from a
    // run that simply had nothing to fetch — which is why the caller fell
    // through to the generic "check your connection" copy.
    expect(result).not.toBeNull();
    expect(result.reason).toBe('no-worker');
    expect(result.ok).toBe(0);
    expect(result.failed).toBe(0);
    // Nothing was dispatched, which is exactly why no request ever appeared
    // in the network panel.
    expect(posted).toHaveLength(0);
  });

  it('proceeds when a controller arrives before the wait elapses', async () => {
    vi.useFakeTimers();
    const promise = window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'x' });
    // The activation window closing: the new worker claims this page.
    swStub.controller = makeWorker();
    swStub.dispatchEvent(new Event('controllerchange'));
    await vi.advanceTimersByTimeAsync(0);
    expect(posted).toHaveLength(1);
    expect(posted[0].type).toBe('warm-cache');

    // Settled rather than left pending: a run in flight now holds the
    // QUEUE, not just the slot, so leaking one strands every later test.
    settle(posted[0].requestId);
    expect(await promise).not.toBeNull();
  });

  it('does not wait when a controller is already there', async () => {
    vi.useFakeTimers();
    swStub.controller = makeWorker();
    const promise = window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'x' });
    // No timer advance at all — a present controller must not be delayed
    // behind the uncontrolled-page wait.
    await vi.advanceTimersByTimeAsync(0);
    expect(posted).toHaveLength(1);

    settle(posted[0].requestId);
    await promise;
  });
});

describe('pwaWarmCacheCancel() with a run in flight (SNOW-632)', () => {
  it('posts a warm-cache-cancel message carrying the live requestId', async () => {
    swStub.controller = makeWorker();
    const promise = window.pwaWarmCache(['/a.mvt', '/b.mvt'], { pinned: true, areaId: 'x' });
    await flush();
    expect(posted).toHaveLength(1);
    const { requestId } = posted[0];

    await window.pwaWarmCacheCancel();

    expect(posted).toHaveLength(2);
    expect(posted[1]).toEqual({ type: 'warm-cache-cancel', requestId });

    // Settle the promise this test started, so it doesn't leave a live
    // slot behind for a later test to trip over.
    swStub.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'warm-cache-done', ok: 0, failed: 0, reason: null, bytes: 0, requestId },
      }),
    );
    await promise;
  });

  it('settles the warmCache() promise with cancelled: true on a cancelled done-reply', async () => {
    swStub.controller = makeWorker();
    const promise = window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'x' });
    await flush();
    const { requestId } = posted[0];

    swStub.dispatchEvent(
      new MessageEvent('message', {
        data: {
          type: 'warm-cache-done',
          ok: 2,
          failed: 0,
          reason: null,
          bytes: 4096,
          cancelled: true,
          requestId,
        },
      }),
    );

    const result = await promise;
    expect(result.cancelled).toBe(true);
    expect(result.ok).toBe(2);
    expect(result.failed).toBe(0);
  });

  it('forwards the running bytes total as a fourth onProgress argument', async () => {
    swStub.controller = makeWorker();
    const onProgress = vi.fn();
    const promise = window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'x', onProgress });
    await flush();
    const { requestId } = posted[0];

    swStub.dispatchEvent(
      new MessageEvent('message', {
        data: {
          type: 'warm-cache-progress',
          done: 1,
          total: 4,
          settled: [0],
          bytes: 2048,
          requestId,
        },
      }),
    );

    expect(onProgress).toHaveBeenCalledWith(1, 4, [0], 2048);

    // Settle, same hygiene reason as the earlier test in this block.
    swStub.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'warm-cache-done', ok: 1, failed: 0, reason: null, bytes: 2048, requestId },
      }),
    );
    await promise;
  });
});
