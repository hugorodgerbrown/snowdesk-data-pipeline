/*
 * tests/js/test_map_route_slope_offline_enable_refetch.js — an overlay enable
 * that could not reach the network must not spend the write's signal
 * (SNOW-910).
 *
 * Scenario: none — a fetch-failure path asserted by counting requests.
 *
 * `_loadOverlay` falls back to the offline cache when its fetch rejects, and
 * that payload predates the write by definition — it may not hold the new
 * route at all. Consuming the signal against it left the route absent until a
 * reload, with nothing left to retry. The signal is now cleared only by a
 * network read that actually returned, so a failed one keeps it up for the
 * next write or overlay load.
 *
 * Its own module because it needs a boot in which the routes overlay has never
 * loaded, and is then enabled while every routes request rejects — see
 * `_route_slope_harness.js`.
 */

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import {
  ROUTES_SAMPLED,
  ROUTES_UNSAMPLED,
  announce,
  boot,
  harness,
  resetHarnessState,
  routesFetchCount,
  teardown,
} from './_route_slope_harness.js';

let mapStub;

beforeAll(async () => {
  mapStub = await boot({});
});

afterAll(teardown);

beforeEach(() => {
  resetHarnessState();
  // Fake timers are armed AFTER the boot above, which schedules work of
  // its own that has nothing to do with these suites.
  vi.useFakeTimers();
  globalThis.fetch.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});


describe('an enable that could not reach the network', () => {
  it('keeps the signal rather than spending it on a cached payload', async () => {
    // The overlay can install from the offline cache, and that payload
    // predates the write by definition — it may not hold the new route at
    // all. Consuming the signal against it left the route absent until a
    // reload, with nothing left to retry.
    announce({ claimed: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(0);

    // The enable's own load is served from the offline cache — a payload
    // written before the write, so it does not hold the new route — and
    // the confirming read fails too.
    harness.cached = ROUTES_SAMPLED;
    harness.offline = true;
    await window.pwaRoutesOverlay.show();
    await vi.advanceTimersByTimeAsync(120000);
    const offlineCalls = routesFetchCount();

    // The SIGNAL survives, and reconnecting spends it: no further write is
    // needed, which is the whole point — nothing refreshes an
    // already-loaded overlay, so the route would otherwise stay missing
    // until a page reload.
    harness.offline = false;
    harness.payload = ROUTES_UNSAMPLED;
    window.dispatchEvent(new Event('online'));
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(offlineCalls + 1);

    // And that payload is unsampled, so the delayed re-read follows.
    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(offlineCalls + 2);
  });
});
