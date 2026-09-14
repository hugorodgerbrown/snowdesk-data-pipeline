/*
 * tests/js/test_map_route_slope_loading_refetch.js — a write that lands while
 * the routes overlay's FIRST load is still in flight (SNOW-910).
 *
 * Scenario: none — a fetch ordering question, asserted by counting requests.
 * No browser is needed to prove it.
 *
 * The trap between the other two suites' cases. `overlayLoaded.routes` is
 * false here, exactly as it is when the overlay was never enabled — but a GET
 * is already in flight, and it was issued BEFORE the write. Treating the two
 * the same let that older response answer for the write: `_loadOverlay`
 * consumed the pending signal and judged a pre-write payload, and since the
 * new route was not in it there was nothing unsampled to arm off. The route
 * was then missing from the map entirely, not merely flat, until a reload.
 *
 * A boot restore of a persisted routes overlay is the ordinary way in.
 *
 * Its own module because it needs a boot in which the routes overlay has
 * never loaded and is then loaded slowly — see `_route_slope_harness.js`.
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

describe('a claim made while the overlay is still loading', () => {
  it('fetches again rather than trusting the in-flight payload', async () => {
    harness.delays = [50];
    harness.payload = ROUTES_SAMPLED;

    // Started, deliberately NOT awaited: its GET is in flight.
    const loading = window.pwaRoutesOverlay.show();
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    // The write lands mid-load, and the server now holds an unsampled route
    // that the in-flight request cannot know about.
    harness.payload = ROUTES_UNSAMPLED;
    announce({ claimed: true });

    // The load settles on its pre-write payload, which is never judged; a
    // confirming request goes out instead — the first that can see the
    // write.
    await vi.advanceTimersByTimeAsync(60);
    await loading;
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(2);

    // That payload is unsampled, so the delayed re-read is armed off it.
    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(3);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(3);
  });
});
