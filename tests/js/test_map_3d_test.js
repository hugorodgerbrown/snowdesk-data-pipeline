/*
 * tests/js/test_map_3d_test.js — "Test 3D on this device" (SNOW-1456).
 *
 * static/js/map_3d_test.js against a fake map with a controllable
 * requestAnimationFrame, a reduced copy of the panel's template, and
 * window.pwaMapView and window.MapSheet stubbed: the runner's own hooks
 * are pinned in test_map_view_3d.js, the sheet controller in map_sheet's
 * tests. What is pinned here:
 *   - the passes run in order, each jumped to and turned, and the verdict
 *     is the watchdog's rule over their frames;
 *   - Stop, Escape, a hidden tab, a lost WebGL context and the map leaving
 *     3D each end the run through endTest({}), with no verdict and nothing
 *     sent, and a throw mid-run ends it the same way;
 *   - the result keeps the pixel ratio and canvas size the passes were
 *     drawn at, not the ones leaving 3D puts back;
 *   - an unmeasured run says so and cannot be sent;
 *   - offline, on a gated device and while drawing, nothing runs;
 *   - ?probe=1 starts on load with Details open, on Steep terrain and SAC
 *     tours for that run only, and a cancelled probe run hides them again;
 *   - Send posts the result once, with the overlays and the CSRF header; a
 *     204 shows it sent, a 400 or a network failure the failure line.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_3d_core.js';

const core = window.pwaMap3dCore;

const SHOW = (states) => `data-test-show="${states}" hidden`;
const MARKUP = `
  <div id="basemap-menu">
    <button data-overlay-key="weather" aria-checked="true"></button>
    <button data-overlay-key="routes" aria-checked="false"></button>
    <button data-overlay-key="country.ch" aria-checked="true"></button>
  </div>
  <div id="map-3d-test-sheet" hidden></div>
  <template id="map-3d-test-template">
    <div data-map-3d-test data-url="/api/map-3d-test/"
         data-overlay-keys="terrain ski_tours weather routes">
      <button data-action="dismiss" ${SHOW('running')}>Stop the test</button>
      <button data-action="dismiss" ${SHOW('smooth stutter slow unmeasured unavailable offline editing')}>Close</button>
      <span ${SHOW('smooth stutter slow unmeasured')}>
        <button data-test-share>Share the result</button>
        <button data-test-again>Test again</button>
      </span>
      <section ${SHOW('running')}>
        <div data-test-progress aria-valuenow="0"><div data-test-progress-bar></div></div>
        <li data-test-step="load"></li>
        <li data-test-step="0"><span data-test-left></span></li>
        <li data-test-step="1"><span data-test-left></span></li>
      </section>
      <section ${SHOW('smooth stutter slow')} data-verdict>
        <h2 ${SHOW('smooth')}>3D runs smoothly on this phone</h2>
        <h2 ${SHOW('stutter')}>3D may stutter when zoomed out</h2>
        <h2 ${SHOW('slow')}>3D is too slow on this phone</h2>
        <li data-test-pass="0"><span data-pass-status="keeps" hidden>keeps up</span><span data-pass-status="behind" hidden>falls behind</span></li>
        <li data-test-pass="1"><span data-pass-status="keeps" hidden>keeps up</span><span data-pass-status="behind" hidden>falls behind</span></li>
        <details data-test-details>
          <div data-test-figures="0"><span data-figure="median"></span><span data-figure="windows"></span></div>
          <div data-test-figures="1"><span data-figure="median"></span><span data-figure="windows"></span></div>
          <span data-test-gpu></span><span data-test-screen></span><span data-test-rule></span>
        </details>
        <div data-test-send-box>
          <p data-test-send-failed hidden>Could not send the result</p>
          <button data-action="map-3d-test-send">Send anonymously</button>
        </div>
        <p data-test-sent hidden>Sent to Snowdesk, anonymously.</p>
        <p data-test-share-status></p>
        <button data-action="dismiss">Done</button>
      </section>
      <section ${SHOW('unmeasured')}>
        <h2>The test could not time this device</h2>
        <button data-action="map-3d-test-again">Test again</button>
      </section>
      <section ${SHOW('unavailable')}>3D is not available on this device</section>
      <section ${SHOW('offline')}>The 3D test needs a connection</section>
      <section ${SHOW('editing')}>Finish drawing the route to see 3D</section>
    </div>
  </template>`;

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** The animation frames asked for and not yet run. */
const rafQueue = new Map();
let rafNow = 0;
let rafNext = 1;

/** Run `n` animation frames, `ms` apart. */
const frames = (ms, n) => {
  for (let i = 0; i < n; i += 1) {
    rafNow += ms;
    const due = [...rafQueue.values()];
    rafQueue.clear();
    for (const fn of due) fn(rafNow);
  }
};

/** A MapLibre double: listeners, a camera, a canvas. */
function fakeMap() {
  const handlers = {};
  const map = {
    bearing: 30,
    on: (type, fn) => { (handlers[type] = handlers[type] || []).push(fn); },
    once: (type, fn) => {
      const wrapped = (e) => {
        handlers[type] = handlers[type].filter((h) => h !== wrapped);
        fn(e);
      };
      map.on(type, wrapped);
    },
    fire: (type, event = {}) => { for (const fn of [...(handlers[type] || [])]) fn(event); },
    loaded: vi.fn(() => true),
    triggerRepaint: vi.fn(),
    jumpTo: vi.fn(),
    rotateTo: vi.fn(),
    stop: vi.fn(),
    getBearing: () => map.bearing,
    getPixelRatio: () => 2,
    getCanvas: () => ({ width: 824, height: 1830 }),
  };
  return map;
}

let env;
let added = [];
const realAdd = document.addEventListener.bind(document);

/** A stand-in for MapSheet.attach: shows, hides, Escape and dismiss close it. */
function stubMapSheet() {
  window.MapSheet = {
    attach: (el, opts) => {
      const controller = {
        open: vi.fn(() => el.removeAttribute('hidden')),
        close: vi.fn(() => {
          el.setAttribute('hidden', '');
          el.innerHTML = '';
          opts.onTeardown?.();
        }),
        isOpen: () => !el.hasAttribute('hidden'),
      };
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && controller.isOpen()) controller.close();
      });
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-action="dismiss"]')) controller.close();
      });
      env.sheet = controller;
      return controller;
    },
  };
}

/**
 * Load a fresh copy of the module.
 *
 * @param {{search?: string, can?: object, fetchImpl?: Function, loaded?: boolean}} [options]
 */
async function load({ search = '', can = { ok: true, reason: null }, fetchImpl, loaded = true } = {}) {
  document.body.innerHTML = MARKUP;
  document.cookie = 'csrftoken=tok-123';
  window.history.replaceState(null, '', `/map/${search}`);
  rafQueue.clear();
  window.requestAnimationFrame = vi.fn((fn) => {
    const id = rafNext;
    rafNext += 1;
    rafQueue.set(id, fn);
    return id;
  });
  env = { map: fakeMap() };
  env.map.loaded.mockReturnValue(loaded);
  window.snowdeskMapState = { map: env.map };
  window.pwaMapView = {
    canTest: vi.fn(() => can),
    beginTest: vi.fn(() => true),
    endTest: vi.fn(),
  };
  window.pwaTerrainFilter = { on: false, isEnabled() { return this.on; }, show: vi.fn(function () { this.on = true; }), hide: vi.fn(function () { this.on = false; }) };
  window.pwaSkiRoutes = { tours: { on: true, isEnabled() { return this.on; }, show: vi.fn(), hide: vi.fn() } };
  window.pwaShare = { shareOrCopy: vi.fn(() => Promise.resolve('copied')) };
  globalThis.fetch = vi.fn(fetchImpl || (() => Promise.resolve({ status: 204 })));
  stubMapSheet();
  document.addEventListener = (...args) => {
    added.push(args);
    realAdd(...args);
  };
  vi.resetModules();
  await import('../../static/js/map_3d_test.js');
  document.addEventListener = realAdd;
}

const panel = () => document.getElementById('map-3d-test-sheet');
const state = () => panel().querySelector('[data-map-3d-test]')?.dataset.testState;
const visible = (selector) => {
  const el = panel().querySelector(selector);
  return !!el && !el.hidden;
};
const request = () => document.dispatchEvent(new CustomEvent('snowdesk:map-3d-test-requested'));

const FAST = 16;
const SLOW = 50;
/** One pass's frames: about 12 s of them. */
const perPass = (ms) => Math.ceil(12000 / ms) + 1;

/** Let the map go idle, then run each pass at its frame time. */
async function runPasses(msPerPass) {
  await flush();
  env.map.fire('idle');
  for (const ms of msPerPass) {
    await flush();
    env.map.fire('idle');
    await flush();
    frames(ms, ms ? perPass(ms) : 0);
    env.map.fire('moveend');
  }
  await flush();
}

beforeEach(() => {
  added = [];
});

afterEach(() => {
  added.forEach((args) => document.removeEventListener(...args));
  added = [];
  vi.restoreAllMocks();
  delete window.pwaMapView;
});

describe('a run', () => {
  it('runs the passes in order, then shows the verdict', async () => {
    await load();
    request();
    expect(panel().hidden).toBe(false);
    expect(state()).toBe('running');
    expect(window.pwaMapView.beginTest).toHaveBeenCalledTimes(1);
    await runPasses([FAST, FAST]);
    expect(env.map.jumpTo.mock.calls.map((c) => c[0].zoom)).toEqual(core.PROBE_PASSES.map((p) => p.zoom));
    expect(env.map.jumpTo.mock.calls[0][0].center).toEqual([...core.OPENING_CAMERA.center]);
    expect(env.map.rotateTo).toHaveBeenCalledTimes(2);
    expect(env.map.rotateTo.mock.calls[0][1]).toMatchObject({ duration: 12000, essential: true });
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({ verdict: 'smooth' });
    expect(state()).toBe('smooth');
    expect(visible('[data-verdict] h2[data-test-show="smooth"]')).toBe(true);
    expect(visible('[data-test-pass="1"] [data-pass-status="keeps"]')).toBe(true);
    expect(visible('[data-test-pass="1"] [data-pass-status="behind"]')).toBe(false);
    expect(panel().querySelector('[data-test-figures="0"] [data-figure="median"]').textContent).toBe('median 16.0 ms');
    expect(panel().querySelector('[data-test-details]').open).toBe(false);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('says stutter when only the zoomed-out pass falls behind', async () => {
    await load();
    request();
    await runPasses([FAST, SLOW]);
    expect(state()).toBe('stutter');
    expect(visible('[data-test-pass="0"] [data-pass-status="keeps"]')).toBe(true);
    expect(visible('[data-test-pass="1"] [data-pass-status="behind"]')).toBe(true);
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({ verdict: 'stutter' });
  });

  it('records the known switches that are on', async () => {
    await load();
    request();
    await runPasses([FAST, FAST]);
    visibleClick('[data-action="map-3d-test-send"]');
    await flush();
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body.overlays).toEqual(['ski_tours', 'weather']);
  });

  it('keeps the pixel ratio and canvas the passes were drawn at in 3D', async () => {
    await load();
    // A DPR-3 phone capped at 2 in 3D: leaving puts 3 back and the canvas grows.
    let ratio = 2;
    let canvas = { width: 824, height: 1830 };
    env.map.getPixelRatio = () => ratio;
    env.map.getCanvas = () => canvas;
    window.pwaMapView.endTest.mockImplementation(() => {
      ratio = 3;
      canvas = { width: 1236, height: 2745 };
    });
    request();
    await runPasses([FAST, FAST]);
    expect(panel().querySelector('[data-test-screen]').textContent).toContain('412 × 915');
    expect(panel().querySelector('[data-test-screen]').textContent).toContain('drawn at 2×');
    visibleClick('[data-action="map-3d-test-send"]');
    await flush();
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body).toMatchObject({ pixel_ratio: 2, canvas_width: 824, canvas_height: 1830 });
  });
});

/** Click the first visible element matching `selector` in the panel. */
function visibleClick(selector) {
  const el = [...panel().querySelectorAll(selector)].find((node) => !node.closest('[hidden]'));
  el.click();
}

describe('ending a run early', () => {
  it.each([
    ['Stop', () => visibleClick('[data-action="dismiss"]')],
    ['Escape', () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
    ['a hidden tab', () => {
      Object.defineProperty(document, 'hidden', { value: true, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
      Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    }],
    ['a lost WebGL context', () => env.map.fire('webglcontextlost')],
  ])('%s ends it through endTest, with no verdict and nothing sent', async (_label, end) => {
    await load();
    request();
    await flush();
    env.map.fire('idle');
    await flush();
    env.map.fire('idle');
    await flush();
    frames(FAST, 50);
    end();
    expect(window.pwaMapView.endTest).toHaveBeenCalledTimes(1);
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({});
    expect(env.map.stop).toHaveBeenCalled();
    expect(panel().hidden).toBe(true);
    // What would have been the rest of the run changes nothing.
    await runPasses([FAST, FAST]);
    expect(window.pwaMapView.endTest).toHaveBeenCalledTimes(1);
    expect(rafQueue.size).toBe(0);
    expect(panel().querySelector('[data-map-3d-test]')).toBeNull();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe('leaving 3D mid-run', () => {
  it('cancels the run, gives no verdict, keeps the latch and sends nothing', async () => {
    await load();
    request();
    await flush();
    env.map.fire('idle');
    await flush();
    env.map.fire('idle');
    await flush();
    frames(FAST, 50);
    // The reader taps 2D: the runner leaves 3D and says so.
    document.dispatchEvent(new CustomEvent('snowdesk:map-view-changed', { detail: { view: 'flat' } }));
    expect(window.pwaMapView.endTest).toHaveBeenCalledTimes(1);
    // No verdict, so the runner's slow latch stays as it was.
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({});
    expect(env.map.stop).toHaveBeenCalled();
    await runPasses([FAST, FAST]);
    expect(window.pwaMapView.endTest).toHaveBeenCalledTimes(1);
    expect(panel().querySelector('[data-map-3d-test]')).toBeNull();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('is not ended by the view entering 3D', async () => {
    await load();
    request();
    document.dispatchEvent(new CustomEvent('snowdesk:map-view-changed', { detail: { view: '3d' } }));
    await runPasses([FAST, FAST]);
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({ verdict: 'smooth' });
  });
});

describe('a run that throws', () => {
  it('still ends the test and puts the probe layers back', async () => {
    await load({ search: '?probe=1' });
    expect(window.pwaTerrainFilter.show).toHaveBeenCalledTimes(1);
    const failure = new Error('jump failed');
    env.map.jumpTo.mockImplementation(() => { throw failure; });
    const rejected = new Promise((resolve) => {
      const onRejection = (event) => {
        event.preventDefault?.();
        resolve(event.reason);
      };
      window.addEventListener('unhandledrejection', onRejection, { once: true });
      process.once('unhandledRejection', (reason) => resolve(reason));
    });
    await flush();
    env.map.fire('idle');
    expect(await rejected).toBe(failure);
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({});
    expect(window.pwaTerrainFilter.hide).toHaveBeenCalledTimes(1);
    expect(state()).toBe('unmeasured');
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe('an unmeasured run', () => {
  it('says so, offers Test again and cannot be sent', async () => {
    await load();
    request();
    await runPasses([FAST, 0]);
    expect(state()).toBe('unmeasured');
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({ verdict: 'unmeasured' });
    expect(visible('[data-verdict]')).toBe(false);
    expect(panel().querySelector('[data-action="map-3d-test-send"]').closest('[hidden]')).not.toBeNull();
    visibleClick('[data-action="map-3d-test-send"], [data-action="map-3d-test-again"]');
    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(state()).toBe('running');
    expect(window.pwaMapView.beginTest).toHaveBeenCalledTimes(2);
  });
});

describe('where the test does not run', () => {
  it.each([
    ['offline', 'offline', 'offline'],
    ['on a gated device', 'device', 'unavailable'],
    ['with the WebGL context lost', 'webgl', 'unavailable'],
    ['while a route is drawn', 'editing', 'editing'],
  ])('%s it says why and runs nothing', async (_label, reason, shown) => {
    await load({ can: { ok: false, reason } });
    request();
    expect(state()).toBe(shown);
    expect(window.pwaMapView.beginTest).not.toHaveBeenCalled();
    expect(env.map.jumpTo).not.toHaveBeenCalled();
  });

  it('says 3D is not available when the view refuses to enter', async () => {
    await load();
    window.pwaMapView.beginTest.mockReturnValue(false);
    request();
    expect(state()).toBe('unavailable');
    expect(env.map.jumpTo).not.toHaveBeenCalled();
  });
});

describe('?probe=1', () => {
  it('starts on load with Details open, on the budget\'s layers for that run only', async () => {
    await load({ search: '?probe=1' });
    expect(state()).toBe('running');
    expect(window.pwaTerrainFilter.show).toHaveBeenCalledTimes(1);
    // SAC tours were on already: nothing to turn on, nothing to turn off.
    expect(window.pwaSkiRoutes.tours.show).not.toHaveBeenCalled();
    await runPasses([FAST, FAST]);
    expect(panel().querySelector('[data-test-details]').open).toBe(true);
    expect(window.pwaTerrainFilter.hide).toHaveBeenCalledTimes(1);
    expect(window.pwaSkiRoutes.tours.hide).not.toHaveBeenCalled();
    visibleClick('[data-test-share]');
    await flush();
    const [text, , kind] = window.pwaShare.shareOrCopy.mock.calls[0];
    expect(kind).toBe('text');
    const shared = JSON.parse(text);
    expect(shared).toMatchObject({ verdict: 'smooth', probe: true, budgetMs: core.FRAME_BUDGET_MS });
    expect(shared.overlays).toEqual(['terrain', 'ski_tours', 'weather']);
    expect(shared.passes).toHaveLength(2);
    expect(panel().querySelector('[data-test-share-status]').textContent).toBe('Result copied');
  });

  it('hides the layers it turned on when the run is cancelled', async () => {
    await load({ search: '?probe=1' });
    expect(window.pwaTerrainFilter.show).toHaveBeenCalledTimes(1);
    await flush();
    env.map.fire('idle');
    await flush();
    visibleClick('[data-action="dismiss"]');
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({});
    expect(window.pwaTerrainFilter.hide).toHaveBeenCalledTimes(1);
    // SAC tours were on before the run, so the cancel leaves them alone.
    expect(window.pwaSkiRoutes.tours.hide).not.toHaveBeenCalled();
  });

  it('waits for a map still loading', async () => {
    await load({ search: '?probe=1&view=3d', loaded: false });
    expect(window.pwaMapView.beginTest).not.toHaveBeenCalled();
    env.map.fire('load');
    expect(window.pwaMapView.beginTest).toHaveBeenCalledTimes(1);
  });
});

describe('Send anonymously', () => {
  it('posts the result once, with the CSRF header; a 204 shows it sent', async () => {
    let answer;
    await load({ fetchImpl: () => new Promise((resolve) => { answer = resolve; }) });
    request();
    await runPasses([FAST, SLOW]);
    visibleClick('[data-action="map-3d-test-send"]');
    visibleClick('[data-action="map-3d-test-send"]');
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe('/api/map-3d-test/');
    expect(init.method).toBe('POST');
    expect(init.headers['X-CSRFToken']).toBe('tok-123');
    expect(init.headers['Content-Type']).toBe('application/json');
    const body = JSON.parse(init.body);
    expect(body).toMatchObject({
      verdict: 'stutter', probe: false, canvas_width: 824, canvas_height: 1830, pixel_ratio: 2,
      overlays: ['ski_tours', 'weather'],
    });
    expect(body.passes.map((p) => p.label)).toEqual(core.PROBE_PASSES.map((p) => p.label));
    expect(body.passes[1].slow_windows).toBeGreaterThan(0);
    expect(Object.keys(body.passes[0]).sort()).toEqual([
      'frames', 'label', 'median_ms', 'over_budget', 'p95_ms', 'settle_ms', 'slow_windows', 'worst_ms',
    ]);
    answer({ status: 204 });
    await flush();
    expect(visible('[data-test-sent]')).toBe(true);
    expect(visible('[data-test-send-box]')).toBe(false);
  });

  it.each([
    ['a 400', () => Promise.resolve({ status: 400 })],
    ['a network failure', () => Promise.reject(new TypeError('offline'))],
  ])('shows the failure line on %s, and leaves Send to try again', async (_label, fetchImpl) => {
    await load({ fetchImpl });
    request();
    await runPasses([FAST, FAST]);
    visibleClick('[data-action="map-3d-test-send"]');
    await flush();
    expect(visible('[data-test-send-failed]')).toBe(true);
    expect(visible('[data-test-sent]')).toBe(false);
    expect(visible('[data-test-send-box]')).toBe(true);
    visibleClick('[data-action="map-3d-test-send"]');
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  });
});
