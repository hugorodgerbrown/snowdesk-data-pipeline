/*
 * tests/js/test_map_3d_flight.js — a 3D fly-past on /map/ (SNOW-1466).
 *
 * static/js/map_3d_flight.js against a MapLibre double, with
 * window.pwaMapView and window.pwaRouteDrawingCore stubbed. The camera path
 * is pinned in test_map_3d_flight_core.js. What is pinned here:
 *   - load enters 3D through pwaMapView.beginTest, adds the source and its
 *     three layers in the shared route colours, and throws naming the
 *     reason when 3D cannot be entered;
 *   - load(geojson) leaves the page alone; {chrome: false} sets
 *     body.map-recording and unload clears it;
 *   - frame sets the data and the camera and resolves once idle with every
 *     tile loaded, waits through an idle with tiles still loading, jumps
 *     again when MapLibre lowered the camera before the heights were in,
 *     and rejects when the cap passes first;
 *   - a reveal frame blends towards cameraForBounds by the fit's weight;
 *   - unload removes the layers and source and hands back to endTest.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/map_3d_flight_core.js';

const core = window.pwaMap3dFlightCore;

const ROUTE = {
  type: 'Feature',
  properties: { name: 'Breithorn' },
  geometry: {
    type: 'MultiLineString',
    coordinates: [[[7.729238, 45.93749], [7.735196, 45.931446], [7.744714, 45.933907], [7.74775, 45.940993]]],
  },
};

/** A MapLibre double: listeners, sources, layers, a camera. */
function fakeMap() {
  const handlers = {};
  const sources = {};
  const layers = [];
  const map = {
    tilesLoaded: true,
    sources,
    layers,
    on: (type, fn) => { (handlers[type] = handlers[type] || []).push(fn); },
    once: (type, fn) => {
      const wrapped = (e) => {
        handlers[type] = handlers[type].filter((h) => h !== wrapped);
        fn(e);
      };
      map.on(type, wrapped);
    },
    fire: (type, event = {}) => { for (const fn of [...(handlers[type] || [])]) fn(event); },
    listeners: (type) => (handlers[type] || []).length,
    addSource: vi.fn((id, spec) => { sources[id] = { spec, setData: vi.fn() }; }),
    getSource: (id) => sources[id],
    removeSource: vi.fn((id) => { delete sources[id]; }),
    addLayer: vi.fn((layer) => { layers.push(layer); }),
    getLayer: (id) => layers.find((l) => l.id === id),
    removeLayer: vi.fn((id) => { layers.splice(layers.findIndex((l) => l.id === id), 1); }),
    camera: { pitch: 0, zoom: 0 },
    // MapLibre's camera: where the last jump sent it, unless a test moves it.
    jumpTo: vi.fn((c) => { map.camera = { pitch: c.pitch, zoom: c.zoom }; }),
    getPitch: () => map.camera.pitch,
    getZoom: () => map.camera.zoom,
    triggerRepaint: vi.fn(),
    resize: vi.fn(),
    areTilesLoaded: vi.fn(() => map.tilesLoaded),
    cameraForBounds: vi.fn(() => ({ center: { lng: 7.74, lat: 45.94 }, zoom: 13, bearing: 0 })),
  };
  return map;
}

let env;

/** Load a fresh copy of the module over a fresh map and view. */
async function load({ beginTest = true, reason = null } = {}) {
  vi.resetModules();
  document.body.className = '';
  env = { map: fakeMap() };
  window.snowdeskMapState = { map: env.map };
  window.pwaMapView = {
    canTest: vi.fn(() => ({ ok: beginTest, reason })),
    beginTest: vi.fn(() => beginTest),
    endTest: vi.fn(),
  };
  window.pwaRouteDrawingCore = { ROUTE_LINE_COLOUR: '#c0ffee', ROUTE_CASING_COLOUR: '#123456' };
  await import('../../static/js/map_3d_flight.js?t=' + Math.random());
  return window.pwaMap3dFlight;
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.useRealTimers();
  delete window.pwaMap3dFlight;
});

describe('load', () => {
  it('enters 3D and adds the source and three layers in the route colours', async () => {
    const flight = await load();
    const result = flight.load(ROUTE);
    expect(window.pwaMapView.beginTest).toHaveBeenCalledTimes(1);
    expect(env.map.sources['map-3d-flight']).toBeTruthy();
    expect(env.map.layers.map((l) => l.id)).toEqual([
      'map-3d-flight-casing', 'map-3d-flight-line', 'map-3d-flight-head',
    ]);
    // Added with no beforeId, so on top of the routes and everything else.
    for (const call of env.map.addLayer.mock.calls) expect(call[1]).toBeUndefined();
    expect(env.map.layers[0].paint['line-color']).toBe('#123456');
    expect(env.map.layers[1].paint['line-color']).toBe('#c0ffee');
    expect(env.map.layers[2].paint['circle-color']).toBe('#c0ffee');
    expect(result.length).toBeGreaterThan(1000);
    expect(result.phases).toEqual(core.PHASES);
  });

  it('leaves the page alone by default and lays the map bare with chrome: false', async () => {
    const flight = await load();
    flight.load(ROUTE);
    expect(document.body.classList.contains('map-recording')).toBe(false);
    expect(env.map.resize).not.toHaveBeenCalled();
    flight.load(ROUTE, { chrome: false });
    expect(document.body.classList.contains('map-recording')).toBe(true);
    expect(env.map.resize).toHaveBeenCalledTimes(1);
    // A second load replaces the layers rather than doubling them, and
    // enters 3D only once.
    expect(env.map.layers).toHaveLength(3);
    expect(window.pwaMapView.beginTest).toHaveBeenCalledTimes(1);
    flight.unload();
    expect(document.body.classList.contains('map-recording')).toBe(false);
  });

  it('throws, naming the reason, when 3D cannot be entered', async () => {
    const flight = await load({ beginTest: false, reason: 'offline' });
    expect(() => flight.load(ROUTE)).toThrow('3D is not available here: offline');
    expect(env.map.addSource).not.toHaveBeenCalled();
  });

  it('throws on GeoJSON with no line', async () => {
    const flight = await load();
    expect(() => flight.load({ type: 'Point', coordinates: [7.7, 45.9] })).toThrow(/No LineString/);
  });

  it('takes the core options from options.flight, else the feature’s properties', async () => {
    const spy = vi.fn(core.buildFlight);
    window.pwaMap3dFlightCore = { ...core, buildFlight: spy };
    try {
      const flight = await load();
      flight.load({ ...ROUTE, properties: { flight: { seconds: 12 } } });
      expect(spy.mock.calls.at(-1)[1]).toEqual({ seconds: 12 });
      flight.load(ROUTE, { flight: { seconds: 8 } });
      expect(spy.mock.calls.at(-1)[1]).toEqual({ seconds: 8 });
    } finally {
      window.pwaMap3dFlightCore = core;
    }
  });
});

describe('frame', () => {
  it('sets the data and the camera, and resolves once idle with tiles loaded', async () => {
    const flight = await load();
    flight.load(ROUTE);
    const pending = flight.frame(0.6);
    const source = env.map.sources['map-3d-flight'];
    const data = source.setData.mock.calls[0][0];
    expect(data.features.map((f) => f.geometry.type)).toEqual(['LineString', 'Point']);
    const expected = core.flightFrame(core.buildFlight(ROUTE.geometry.coordinates), 0.6);
    expect(env.map.jumpTo).toHaveBeenCalledWith(expected.camera);
    expect(env.map.triggerRepaint).toHaveBeenCalled();
    env.map.fire('idle');
    await expect(pending).resolves.toEqual({ phase: 'follow' });
  });

  it('draws no line before follow, only the head', async () => {
    const flight = await load();
    flight.load(ROUTE);
    const pending = flight.frame(0);
    const data = env.map.sources['map-3d-flight'].setData.mock.calls[0][0];
    expect(data.features.map((f) => f.geometry.type)).toEqual(['Point']);
    env.map.fire('idle');
    await expect(pending).resolves.toEqual({ phase: 'establish' });
  });

  it('waits through an idle that still has tiles loading', async () => {
    const flight = await load();
    flight.load(ROUTE);
    env.map.tilesLoaded = false;
    let done = false;
    const pending = flight.frame(0.5).then(() => { done = true; });
    env.map.fire('idle');
    await Promise.resolve();
    expect(done).toBe(false);
    env.map.tilesLoaded = true;
    env.map.fire('idle');
    await pending;
    expect(done).toBe(true);
  });

  it('jumps again once settled if MapLibre lowered the camera out of the ground', async () => {
    // Before the centre's elevation tile is in, MapLibre reads its height
    // as 0 and drops the pitch and zoom to keep the camera above ground.
    const flight = await load();
    flight.load(ROUTE);
    env.map.jumpTo.mockImplementationOnce((c) => { env.map.camera = { pitch: c.pitch / 2, zoom: c.zoom - 0.8 }; });
    let done = false;
    const pending = flight.frame(0.6).then((r) => { done = true; return r; });
    env.map.fire('idle');
    await Promise.resolve();
    await Promise.resolve();
    expect(done).toBe(false);
    expect(env.map.jumpTo).toHaveBeenCalledTimes(2);
    expect(env.map.jumpTo.mock.calls[1][0]).toEqual(env.map.jumpTo.mock.calls[0][0]);
    env.map.fire('idle');
    await expect(pending).resolves.toEqual({ phase: 'follow' });
  });

  it('jumps once when the camera landed where it was sent', async () => {
    const flight = await load();
    flight.load(ROUTE);
    const pending = flight.frame(0.6);
    env.map.fire('idle');
    await pending;
    expect(env.map.jumpTo).toHaveBeenCalledTimes(1);
  });

  it('rejects when the cap passes before the map settles', async () => {
    const flight = await load();
    flight.load(ROUTE);
    vi.useFakeTimers();
    const pending = flight.frame(0.5);
    const assertion = expect(pending).rejects.toThrow('frame t=0.5 not settled after 60 s');
    vi.advanceTimersByTime(flight.FRAME_CAP_MS);
    await assertion;
  });

  it('waits as long as load\'s capMs says, for a slow renderer', async () => {
    const flight = await load();
    flight.load(ROUTE, { capMs: 300000 });
    vi.useFakeTimers();
    let failed = null;
    flight.frame(0.5).catch((e) => { failed = e; });
    vi.advanceTimersByTime(flight.FRAME_CAP_MS);
    await Promise.resolve();
    expect(failed).toBeNull();
    vi.advanceTimersByTime(300000 - flight.FRAME_CAP_MS);
    await Promise.resolve();
    await Promise.resolve();
    expect(String(failed)).toContain('not settled after 300 s');
  });

  it('blends a reveal frame towards cameraForBounds by the fit’s weight', async () => {
    const flight = await load();
    flight.load(ROUTE);
    const built = core.buildFlight(ROUTE.geometry.coordinates);
    const t = (built.cuts[3] + 1) / 2;
    const expected = core.flightFrame(built, t);
    const pending = flight.frame(t);
    const [bounds, opts] = env.map.cameraForBounds.mock.calls[0];
    expect(bounds).toEqual(expected.fit.bounds);
    expect(opts.pitch).toBe(expected.camera.pitch);
    expect(opts.bearing).toBe(expected.camera.bearing);
    const camera = env.map.jumpTo.mock.calls[0][0];
    const w = expected.fit.weight;
    expect(w).toBeGreaterThan(0);
    expect(w).toBeLessThan(1);
    expect(camera.zoom).toBeCloseTo(expected.camera.zoom + (13 - expected.camera.zoom) * w, 9);
    expect(camera.center[0]).toBeCloseTo(expected.camera.center[0] + (7.74 - expected.camera.center[0]) * w, 9);
    expect(camera.pitch).toBe(expected.camera.pitch);
    expect(camera.bearing).toBe(expected.camera.bearing);
    env.map.fire('idle');
    await expect(pending).resolves.toEqual({ phase: 'reveal' });
  });

  it('refuses a frame with no flight loaded', async () => {
    const flight = await load();
    await expect(flight.frame(0)).rejects.toThrow('No flight loaded');
  });
});

describe('unload', () => {
  it('removes the layers and source and puts the view back', async () => {
    const flight = await load();
    flight.load(ROUTE, { chrome: false });
    flight.unload();
    expect(env.map.layers).toEqual([]);
    expect(env.map.sources['map-3d-flight']).toBeUndefined();
    expect(window.pwaMapView.endTest).toHaveBeenCalledWith({});
    // A second unload does nothing more.
    flight.unload();
    expect(window.pwaMapView.endTest).toHaveBeenCalledTimes(1);
  });
});
