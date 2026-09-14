/*
 * tests/js/test_map_route_slope_upload_refetch.js — the one delayed re-read
 * that lets an uploaded route become coloured without a page reload
 * (SNOW-910).
 *
 * Scenario: none — a timer scheduled off an event payload, asserted by
 * counting fetches. No browser is needed to prove it, and no manual test
 * script could observe it.
 *
 * In production terrain sampling is a QUEUED task: `create_route` returns
 * while `slope_samples` is still null, the upload's own refresh reads that
 * record, and the flat line is drawn. The worker's later save reaches no
 * client, so the route stays uncoloured until a full page reload — on the
 * one path every new user takes first. One delayed re-read fixes that, and
 * three things about it have to hold or the cure is worse:
 *
 *   1. it fires ONCE. A retry ladder would turn a worker that is merely
 *      busy into a stream of requests from every device that uploaded;
 *   2. it is armed by the writes that PUT A ROUTE ON THE SERVER — an
 *      upload and a claim — and by neither of the two that do not. A
 *      legacy route the backfill never reached is unsampled on every
 *      load, and arming off a rename or a delete would cost a pointless
 *      refetch on every visit for the rest of its life;
 *   3. it is inert if there is nothing left to paint — twenty seconds is
 *      long enough for the overlay to have been torn down.
 *
 * Booting map.js in jsdom follows tests/js/test_map_panel_overlay_refresh.js's
 * pattern; see its header for the general rationale.
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
  mapStub = await boot({ showRoutes: true });
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

describe('an upload of a route the server has not sampled yet', () => {
  it('re-reads the feed once, after the delay, and then stops', async () => {
    announce({ uploaded: true });
    // The upload's own refresh — the one that reads the null record.
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    // Well short of the delay: nothing yet.
    await vi.advanceTimersByTimeAsync(5000);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(2);

    // ONE shot. A poll would keep going here, from every device that has
    // ever uploaded a route.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(2);
  });

  it('schedules nothing when the record is already there', async () => {
    // ImmediateBackend — dev, test and staging — stores the samples before
    // the upload's response returns, so this is what those environments
    // see on every upload.
    harness.payload = ROUTES_SAMPLED;

    announce({ uploaded: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);
  });

  it('does nothing once the overlay it would repaint has gone', async () => {
    announce({ uploaded: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    // A basemap swap or a teardown between the schedule and the fire: the
    // sources are gone, so there is nothing to write the payload to.
    const removed = mapStub.sources.get('route-slopes');
    mapStub.sources.delete('route-slopes');

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);

    // RESTORED, because the bundle is booted once for the whole file and
    // this is the only test that takes a source away. Leaving it deleted
    // makes every later test read as "the overlay is gone" and pass by
    // asserting the wrong reason — which is how the claim case below
    // could have looked green while doing nothing.
    mapStub.sources.set('route-slopes', removed);
  });
});

describe('a claim that beat the sharer\'s sampling task (SNOW-910)', () => {
  it('re-reads the feed once, exactly as an upload does', async () => {
    // A claim copies the sharer's record, so it USUALLY arrives coloured.
    // But the link works from the moment it is minted, so a claim can beat
    // the sharer's own sampling task: the copy inherits null and
    // ``claim_route_share`` samples it. Same race an upload runs, and
    // until SNOW-910 the claim was excluded from the cure.
    announce({ claimed: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(2);

    // One shot here too.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(2);
  });

  it('schedules nothing when the copy inherited a record', async () => {
    // The common case: the sharer's row was already sampled, so the copy
    // carries it and there is nothing to wait for.
    harness.payload = ROUTES_SAMPLED;

    announce({ claimed: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);
  });
});

describe('two writes whose refreshes overlap', () => {
  it('does not let the first response answer for the second', async () => {
    // A claim that inherited the sharer's record and an upload that has
    // not been sampled can be in flight together. The claim's payload
    // carries nothing unsampled; the upload's does. While the two shared
    // one pending flag, whichever response landed first consumed it for
    // both, and the upload stayed flat until a reload.
    // The claim's refresh lands FIRST and finds nothing to wait for; the
    // upload's lands later carrying the unsampled route. That order is the
    // whole point — it is the one in which a shared flag is consumed by
    // the wrong response.
    harness.delays = [10, 50];

    harness.payload = ROUTES_SAMPLED;
    announce({ claimed: true });

    harness.payload = ROUTES_UNSAMPLED;
    announce({ uploaded: true });

    await vi.advanceTimersByTimeAsync(20);
    expect(routesFetchCount()).toBe(2);

    // The upload's response arrives now, and it is the one that must arm.
    await vi.advanceTimersByTimeAsync(40);
    expect(routesFetchCount()).toBe(2);

    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(3);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(3);
  });
});

describe('two loaded-overlay writes whose reads resolve out of turn', () => {
  it('does not let an earlier read confirm a later write', async () => {
    // The earlier write's GET went out BEFORE the later write, so it
    // cannot have seen it. While a single boolean carried the signal, that
    // earlier read succeeding cleared it for both — and when the later
    // write's own read then failed, nothing was left to retry from, so its
    // route could stay absent until a reload.
    //
    // A succeeds slowly; B is issued after it and fails.
    harness.delays = [40];
    harness.payload = ROUTES_SAMPLED;
    announce({ claimed: true });

    harness.offline = true;
    announce({ uploaded: true });

    // A returns and credits only itself; B's read never landed.
    await vi.advanceTimersByTimeAsync(60);
    const afterWrites = routesFetchCount();

    // So the write is still outstanding, and reconnecting spends it.
    harness.offline = false;
    harness.payload = ROUTES_UNSAMPLED;
    window.dispatchEvent(new Event('online'));
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(afterWrites + 1);

    // That payload is unsampled, so the delayed re-read follows.
    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(afterWrites + 2);
  });
});

describe('a rename or a delete', () => {
  it('leaves a legacy unsampled route alone', async () => {
    // Neither can put a route on the server, so neither can produce one
    // that is about to gain a record. The payload here is unsampled — the
    // state a route the one-shot backfill never reached is in for good —
    // and arming off that would cost a refetch on every page load for the
    // rest of its life.
    announce();
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);
  });
});
