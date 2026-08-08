/*
 * tests/js/test_map_render_scheduling.js — Vitest unit tests for the two
 * render-scheduling primitives in static/js/map_basemap_downloads.js:
 * `coalesceRenders` (SNOW-613) and `makeStyleSettleRetry` (SNOW-611).
 *
 * Both are pure higher-order functions with no DOM or MapLibre dependency
 * of their own, and both were untestable until SNOW-649 exposed them on
 * `window.pwaBasemapDownloads` — a module-scope `function` inside the map
 * bundle cannot be reached from tests/js, so the only coverage they had was
 * a Playwright test watching a roundel settle
 * (tests/e2e/test_cache_this_area.py::test_done_state_resolves_once_the_style_settles).
 *
 * They are worth direct coverage because both fail SILENTLY and in the
 * same direction — a lost render leaves the roundel showing a state the
 * user has already moved on from, with no error anywhere. That is
 * precisely the class of bug a browser test is bad at catching, because it
 * looks like a timing flake.
 *
 * Only two files of the bundle are booted: `map_basemap_downloads.js`
 * reads `MAP` from `map_state.js` as a bare identifier (the shared lexical
 * scope home.html gives the classic scripts), so the state file has to come
 * first — but nothing here needs map.js's boot IIFE.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// map_basemap_downloads.js reads its user-facing strings through
// `window.pwaStrings` at parse time, so i18n_strings.js has to have run
// before the bundle is evaluated — same order as test_map_download_bytes.js.
import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

/** Resolve after the current microtask queue drains. */
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/**
 * A deferred whose promise a fake render returns, so a test can hold a
 * render open and issue calls while it is still running.
 *
 * @returns {{promise: Promise<void>, resolve: function(): void}}
 */
function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

let bridge;

beforeEach(() => {
  loadMapBundle(['map_state.js', 'map_basemap_downloads.js']);
  bridge = window.pwaBasemapDownloads;
});

describe('coalesceRenders — overlapping probes collapse to one extra pass', () => {
  it('runs the render once for a single call', async () => {
    const render = vi.fn(async () => {});
    await bridge.coalesceRenders(render)();
    expect(render).toHaveBeenCalledTimes(1);
  });

  it('runs sequential, non-overlapping calls each time', async () => {
    const render = vi.fn(async () => {});
    const coalesced = bridge.coalesceRenders(render);
    await coalesced();
    await coalesced();
    await coalesced();
    expect(render).toHaveBeenCalledTimes(3);
  });

  it('collapses three calls arriving mid-run into exactly one extra pass', async () => {
    // The burst SNOW-613 is about: a basemap swap, a connectivity flip and
    // a region selection all landing in the same tick, each of which used
    // to walk every pinned bucket.
    const gate = deferred();
    const render = vi.fn(() => gate.promise);
    const coalesced = bridge.coalesceRenders(render);

    const first = coalesced();
    await tick();
    expect(render).toHaveBeenCalledTimes(1);

    coalesced();
    coalesced();
    coalesced();
    expect(render).toHaveBeenCalledTimes(1);

    gate.resolve();
    await first;
    expect(render).toHaveBeenCalledTimes(2);
  });

  it('is trailing, not leading — the extra pass runs after the first finishes', async () => {
    // Load-bearing: a call arriving mid-probe carries NEWER state than the
    // one running, so a leading-edge throttle would settle the roundel
    // against state the user has already left.
    const order = [];
    const gate = deferred();
    let call = 0;
    const render = vi.fn(() => {
      call += 1;
      order.push(`start:${call}`);
      const p = call === 1 ? gate.promise : Promise.resolve();
      return p.then(() => order.push(`end:${call}`));
    });
    const coalesced = bridge.coalesceRenders(render);

    const first = coalesced();
    await tick();
    coalesced();
    gate.resolve();
    await first;

    expect(order).toEqual(['start:1', 'end:1', 'start:2', 'end:2']);
  });

  it('keeps coalescing across several waves rather than latching', async () => {
    const gates = [deferred(), deferred()];
    let call = 0;
    const render = vi.fn(() => {
      const g = gates[call];
      call += 1;
      return g ? g.promise : Promise.resolve();
    });
    const coalesced = bridge.coalesceRenders(render);

    const first = coalesced();
    await tick();
    coalesced(); // queues wave 2
    gates[0].resolve();
    await tick();
    coalesced(); // arrives during wave 2, queues wave 3
    gates[1].resolve();
    await first;

    expect(render).toHaveBeenCalledTimes(3);
  });

  it('releases the running flag when the render rejects, so the next call still runs', async () => {
    // Without the `finally`, one failed probe would wedge the control for
    // the rest of the page's life — every later call would see `running`
    // and return without rendering.
    const render = vi
      .fn()
      .mockRejectedValueOnce(new Error('probe blew up'))
      .mockResolvedValue(undefined);
    const coalesced = bridge.coalesceRenders(render);

    await expect(coalesced()).rejects.toThrow('probe blew up');
    await coalesced();

    expect(render).toHaveBeenCalledTimes(2);
  });
});

describe('makeStyleSettleRetry — re-render once MapLibre goes idle', () => {
  it('does nothing when there is no map yet', () => {
    window.snowdeskMapState.map = null;
    const render = vi.fn();
    expect(() => bridge.makeStyleSettleRetry(render)()).not.toThrow();
    expect(render).not.toHaveBeenCalled();
  });

  it('does nothing for a map object with no once() — a partially built stub', () => {
    window.snowdeskMapState.map = {};
    const render = vi.fn();
    expect(() => bridge.makeStyleSettleRetry(render)()).not.toThrow();
    expect(render).not.toHaveBeenCalled();
  });

  it('defers the render to the map going idle rather than running it now', () => {
    // The whole point: activeBasemapTileTemplate is gated on
    // isStyleLoaded(), which is false for the entire boot sequence that
    // first paints these icons.
    const listeners = [];
    window.snowdeskMapState.map = { once: (evt, fn) => listeners.push([evt, fn]) };
    const render = vi.fn();

    bridge.makeStyleSettleRetry(render)();

    expect(render).not.toHaveBeenCalled();
    expect(listeners).toHaveLength(1);
    expect(listeners[0][0]).toBe('idle');

    listeners[0][1]();
    expect(render).toHaveBeenCalledTimes(1);
  });

  it('coalesces repeated probes into a single pending idle listener', () => {
    const listeners = [];
    window.snowdeskMapState.map = { once: (evt, fn) => listeners.push([evt, fn]) };
    const retry = bridge.makeStyleSettleRetry(vi.fn());

    retry();
    retry();
    retry();

    expect(listeners).toHaveLength(1);
  });

  it('arms again after the idle has fired, so a later probe is not swallowed', () => {
    // The `pending` flag has to be cleared inside the handler. If it
    // latched, the control would settle correctly once and then never
    // re-probe for the rest of the session.
    const listeners = [];
    window.snowdeskMapState.map = { once: (evt, fn) => listeners.push([evt, fn]) };
    const render = vi.fn();
    const retry = bridge.makeStyleSettleRetry(render);

    retry();
    listeners[0][1]();
    retry();

    expect(listeners).toHaveLength(2);
    listeners[1][1]();
    expect(render).toHaveBeenCalledTimes(2);
  });
});
