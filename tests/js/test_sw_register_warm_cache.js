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

describe('warmCache() with a controller present', () => {
  it('posts the warm-cache message to the active worker', async () => {
    swStub.controller = makeWorker();
    window.pwaWarmCache(['/a.mvt', '/b.mvt'], { pinned: true, areaId: 'region-CH-4115' });
    // Let the awaited _activeWorker() microtask settle.
    await Promise.resolve();
    await Promise.resolve();
    expect(posted).toHaveLength(1);
    expect(posted[0].type).toBe('warm-cache');
    expect(posted[0].urls).toEqual(['/a.mvt', '/b.mvt']);
    expect(posted[0].pinned).toBe(true);
    expect(posted[0].areaId).toBe('region-CH-4115');
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
    // The run is now in flight and waits on the worker's reply, so the
    // promise stays pending — the point is that it dispatched at all.
    expect(promise).toBeInstanceOf(Promise);
  });

  it('does not wait when a controller is already there', async () => {
    vi.useFakeTimers();
    swStub.controller = makeWorker();
    window.pwaWarmCache(['/a.mvt'], { pinned: true, areaId: 'x' });
    // No timer advance at all — a present controller must not be delayed
    // behind the uncontrolled-page wait.
    await vi.advanceTimersByTimeAsync(0);
    expect(posted).toHaveLength(1);
  });
});
