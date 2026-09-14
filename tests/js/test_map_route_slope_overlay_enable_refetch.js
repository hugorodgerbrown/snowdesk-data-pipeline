/*
 * tests/js/test_map_route_slope_overlay_enable_refetch.js — the delayed
 * re-read has to survive the routes overlay being OFF when the write
 * happened (SNOW-910).
 *
 * Scenario: none — a timer scheduled off an event payload and an overlay
 * enable, asserted by counting fetches. No browser is needed to prove it,
 * and no manual test script could observe it.
 *
 * `refreshPanelOverlay` is a no-op while an overlay has never been loaded,
 * so a claim or an upload made with the routes switch OFF leaves
 * `routesGeojsonCache` untouched: there is no payload to find unsampled,
 * and the write's own call to `scheduleSlopeRefetch` had nothing to arm
 * off. Enabling the overlay afterwards then drew the null-slope route and
 * scheduled nothing, leaving it flat for the rest of the session — which
 * is exactly the state the timer exists to prevent, reached by a different
 * door.
 *
 * This file is a SEPARATE module from test_map_route_slope_upload_refetch.js
 * because the two need opposite boots: that one shows the routes overlay in
 * `beforeAll`, and `overlayLoaded` is never reset by hiding it again (hidden
 * means nothing is on screen to be wrong, not that the payload was
 * discarded). The only way to test the unloaded path is to never load it.
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

describe('a claim made while the routes overlay is off', () => {
  it('re-reads the feed once the overlay is enabled', async () => {
    announce({ claimed: true });
    await vi.advanceTimersByTimeAsync(1);
    // The write's own refresh is a no-op: the overlay has never loaded, so
    // there is no cache to update and no fetch to make.
    expect(routesFetchCount()).toBe(0);

    // Nothing is armed off a payload nobody has read.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(0);

    // The user turns routes on. TWO requests follow: the overlay's own
    // load, and the confirming read the outstanding write asks for. The
    // load's payload is deliberately not judged — it can be the offline
    // cache, or a fetch that started before the write.
    await window.pwaRoutesOverlay.show();
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(2);

    // That confirming payload carries the unsampled route, so the delayed
    // re-read is armed off it.
    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(3);

    // One shot, here as everywhere.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(3);
  });
});
