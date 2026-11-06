/*
 * tests/js/test_map_view_3d.js — /map/'s 3D view against a fake map
 * (SNOW-1449, SNOW-1455, SNOW-1452).
 *
 * Pinned: entering (swisstopo winter, the relief, the tilt, the floor, the
 * URL, one line) and leaving (the reader's basemap back, ESRI awaited,
 * none at all for a reader on swisstopo winter; pitch 0, bearing kept);
 * the relief put back after a setStyle; a reader's tilt entering 3D in
 * either mode; a mode change keeping 3D (SNOW-1452); leaving offline and
 * outside Switzerland; a route being drawn; why the roundel is disabled; the z9
 * line once per attempt; and the basemap picker's rows disabled in 3D.
 *
 * SNOW-1455, with a controllable requestAnimationFrame and visibility: a
 * lost WebGL context lays the map flat and its restore lets 3D back in;
 * three slow frame windows lay it flat for the visit; frames are never timed
 * while flat, between moves, during the entering ease or in a hidden tab;
 * a bad window then a good one stays in 3D; and the device gate keeps
 * 3D off at boot.
 *
 * SNOW-1452: the timelapse plays in 3D with no setStyle, and the watchdog
 * times its frames with the camera still.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/slope_classes_core.js';
import '../../static/js/terrain_filter_core.js';
import '../../static/js/map_view_core.js';
import '../../static/js/map_3d_core.js';
import '../../static/js/map_3d_terrain.js';
import { loadMapBundle } from './_load_map_bundle.js';

const core3d = window.pwaMap3dCore;
const terrain = window.pwaMap3dTerrain;
const ZERMATT = { lng: 7.75, lat: 46.02 };
const PARIS = { lng: 2.35, lat: 48.86 };
const URLS = {
  openfreemap_liberty: 'https://tiles.example/liberty.json',
  swisstopo_winter: 'https://tiles.example/winter.json',
  basemap_at: 'https://tiles.example/at.json',
};

/** A MapLibre double that keeps its camera, style, sources and listeners. */
function fakeMap({ webgl = true } = {}) {
  const handlers = {};
  let sources = {};
  let layers = [{ id: 'relief', type: 'raster' }, { id: 'labels', type: 'symbol' }];
  let terrainSpec = null;
  const container = document.createElement('div');
  // MapLibre's canvas: getContext hands back the context it made, or null.
  const canvas = { getContext: vi.fn(() => (webgl ? {} : null)) };
  const map = {
    zoom: 12,
    centre: { ...ZERMATT },
    pitch: 0,
    bearing: 30,
    minZoom: 4,
    maxPitch: 0,
    pixelRatio: 3,
    on: (type, fn) => { (handlers[type] = handlers[type] || []).push(fn); },
    once: (type, fn) => {
      const wrapped = (e) => {
        handlers[type] = handlers[type].filter((h) => h !== wrapped);
        fn(e);
      };
      map.on(type, wrapped);
    },
    fire: (type, event = {}) => { for (const fn of handlers[type] || []) fn(event); },
    getZoom: () => map.zoom,
    getCenter: () => map.centre,
    getPitch: () => map.pitch,
    getBearing: () => map.bearing,
    getMinZoom: () => map.minZoom,
    setMinZoom: vi.fn((z) => { map.minZoom = z; map.zoom = Math.max(map.zoom, z); }),
    getMaxPitch: () => map.maxPitch,
    setMaxPitch: vi.fn((p) => { map.maxPitch = p; map.pitch = Math.min(map.pitch, p); }),
    getPixelRatio: () => map.pixelRatio,
    setPixelRatio: vi.fn((r) => { map.pixelRatio = r; }),
    easeTo: vi.fn((o) => { if ('pitch' in o) map.pitch = Math.min(o.pitch, map.maxPitch); }),
    jumpTo: vi.fn((o) => { if ('pitch' in o) map.pitch = Math.min(o.pitch, map.maxPitch); }),
    setStyle: vi.fn(() => {
      sources = {};
      layers = [{ id: 'labels', type: 'symbol' }];
      terrainSpec = null;
      // A setStyle stops a camera ease where it stands, as in Chromium.
      map.pitch = Math.min(map.pitch, 20);
      map.fire('styledata');
      map.fire('style.load');
    }),
    addSource: (id, s) => { sources[id] = s; },
    getSource: (id) => sources[id],
    addLayer: (layer) => { layers.unshift(layer); },
    getLayer: (id) => layers.find((l) => l.id === id),
    removeLayer: (id) => { layers = layers.filter((l) => l.id !== id); },
    getStyle: () => ({ layers }),
    setTerrain: (t) => { terrainSpec = t; },
    getTerrain: () => terrainSpec,
    setSky: vi.fn(),
    getContainer: () => container,
    getCanvas: () => canvas,
  };
  return map;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** The animation frames asked for and not yet run, by request id. */
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

/** Set document.hidden. */
const setHidden = (hidden) => {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true });
};

/** Set what navigator says about the device. */
const setDevice = ({ deviceMemory, userAgent }) => {
  Object.defineProperty(navigator, 'deviceMemory', { value: deviceMemory, configurable: true });
  Object.defineProperty(navigator, 'userAgent', { value: userAgent, configurable: true });
};

const DESKTOP_UA = 'Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0 Safari/537.36';
const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 6a) Chrome/131.0 Mobile Safari/537.36';

let env;

/**
 * Render the roundel and the picker's rows, set the page's globals, and
 * run the picker and then the runner, in the page's order.
 */
async function boot({
  search = '?mode=terrain', mode = 'terrain', stored = null, online = true,
  webgl = true, deviceMemory = 8, userAgent = DESKTOP_UA, pitch = 0,
} = {}) {
  const rows = Object.entries(URLS).map(([key, url]) => `
    <button class="basemap-menu-item" role="menuitemradio" data-basemap-key="${key}"
      data-basemap-url="${url}" aria-checked="false">${key}</button>`).join('');
  document.body.innerHTML = `
    <div id="map" data-map-view-enabled="true" data-default-basemap-key="openfreemap_liberty"></div>
    <div id="basemap-pill"><button id="basemap-toggle"></button></div>
    <div id="basemap-menu" hidden>
      <ul><li id="basemap-view-note" hidden>note</li>${rows}</ul>
    </div>
    <button id="map-view-toggle" data-dem-tile-url="https://dem.example/{z}/{x}/{y}.webp" aria-pressed="false">
      <span data-view-face="3d">3D</span><span data-view-face="flat">2D</span>
    </button>`;
  window.history.replaceState(null, '', `/map/${search}`);
  window.localStorage.clear();
  if (stored) window.localStorage.setItem('snowdesk.map.basemap', stored);
  Object.defineProperty(window, 'devicePixelRatio', { value: 3, configurable: true });
  const map = fakeMap({ webgl });
  map.pitch = pitch;
  map.maxPitch = pitch ? 85 : 0;
  setDevice({ deviceMemory, userAgent });
  setHidden(false);
  rafQueue.clear();
  window.requestAnimationFrame = vi.fn((fn) => {
    const id = rafNext;
    rafNext += 1;
    rafQueue.set(id, fn);
    return id;
  });
  window.cancelAnimationFrame = vi.fn((id) => { rafQueue.delete(id); });
  env = {
    map,
    mode,
    online,
    toast: vi.fn(),
    addProtocol: vi.fn(),
    resolveBasemapStyle: vi.fn((key, url) => Promise.resolve(key === 'basemap_at' ? { rewritten: url } : url)),
  };
  window.MapSheet = { toast: env.toast };
  window.pwaMapMode = { get: () => env.mode };
  window.pwaConnectivity = { isOnline: () => env.online };
  window.maplibregl = { addProtocol: env.addProtocol };
  window.resolveBasemapStyle = env.resolveBasemapStyle;
  window.fetch = vi.fn(() => Promise.resolve({ ok: true }));
  // The picker reads its globals from map_state.js and map_shared.js.
  loadMapBundle(['map_state.js', 'map_shared.js', 'map_basemap_picker.js']);
  // After map_state.js, which publishes its own (empty) one.
  window.snowdeskMapState = { map };
  vi.resetModules();
  await import('../../static/js/map_view_3d.js');
  return { map, button: document.getElementById('map-view-toggle') };
}

const inThreeD = (map) => terrain.hasTerrain(map) && !!map.getLayer(terrain.SUNLIGHT_LAYER);
const toasts = () => env.toast.mock.calls.map((c) => c[0]);
const view = () => new URLSearchParams(window.location.search).get('view');

describe('entering 3D', () => {
  afterEach(() => vi.restoreAllMocks());

  it('swaps to swisstopo winter, lifts the relief and tilts in', async () => {
    const { map, button } = await boot();
    button.click();
    await flush();
    expect(map.setStyle).toHaveBeenCalledWith(URLS.swisstopo_winter, { diff: false });
    expect(inThreeD(map)).toBe(true);
    expect(map.setSky).toHaveBeenCalledWith(core3d.skySpec());
    expect(env.addProtocol).toHaveBeenCalledTimes(1);
    expect(map.minZoom).toBe(9);
    expect(map.maxPitch).toBe(core3d.MAX_PITCH);
    expect(map.pitch).toBe(core3d.OPENING_CAMERA.pitch);
    expect(map.pixelRatio).toBe(core3d.MAX_PIXEL_RATIO);
    expect(view()).toBe('3d');
    expect(new URLSearchParams(window.location.search).get('mode')).toBe('terrain');
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(window.pwaMapView.get()).toBe('3d');
    expect(toasts()).toEqual(['3D uses the swisstopo winter map; yours comes back in 2D.']);
    expect(window.localStorage.getItem('snowdesk.map.basemap')).toBeNull();
  });

  it('draws on a reader\'s own swisstopo winter without a setStyle or a line', async () => {
    const { map, button } = await boot({ stored: 'swisstopo_winter' });
    button.click();
    expect(map.setStyle).not.toHaveBeenCalled();
    expect(inThreeD(map)).toBe(true);
    expect(toasts()).toEqual([]);
  });

  it('opens on ?view=3d in Terrain without a swap, as map.js booted swisstopo winter', async () => {
    const { map } = await boot({ search: '?mode=terrain&view=3d' });
    expect(map.setStyle).not.toHaveBeenCalled();
    expect(inThreeD(map)).toBe(true);
    expect(toasts()).toEqual([]);
  });

  it('lays a ?view=3d boot flat on the reader\'s basemap when offline', async () => {
    const { map } = await boot({ search: '?mode=terrain&view=3d', online: false });
    await flush();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(map.setStyle).toHaveBeenCalledWith(URLS.openfreemap_liberty, { diff: false });
    expect(view()).toBeNull();
  });
});

describe('the 3D | 2D switch', () => {
  it('keeps one name and says the view by aria-pressed', async () => {
    const { button } = await boot();
    button.setAttribute('aria-label', '3D view');
    button.click();
    await flush();
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.getAttribute('aria-label')).toBe('3D view');
  });

  it('does nothing when the raised segment is tapped, and switches on the other', async () => {
    const { button } = await boot();
    button.querySelector('[data-view-face="flat"]').click();
    await flush();
    expect(window.pwaMapView.get()).toBe('flat');
    button.querySelector('[data-view-face="3d"]').click();
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    button.querySelector('[data-view-face="3d"]').click();
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    button.querySelector('[data-view-face="flat"]').click();
    await flush();
    expect(window.pwaMapView.get()).toBe('flat');
  });
});

describe('leaving 3D', () => {
  it('lays the map flat, keeps the bearing and brings the reader\'s basemap back', async () => {
    const { map, button } = await boot({ stored: 'openfreemap_liberty' });
    button.click();
    await flush();
    map.setStyle.mockClear();
    button.click();
    await flush();
    expect(map.getTerrain()).toBeNull();
    expect(map.pitch).toBe(0);
    expect(map.bearing).toBe(30);
    expect(map.minZoom).toBe(4);
    expect(map.pixelRatio).toBe(3);
    expect(map.setStyle).toHaveBeenCalledWith(URLS.openfreemap_liberty, { diff: false });
    expect(view()).toBeNull();
    expect(button.getAttribute('aria-pressed')).toBe('false');
  });

  it('awaits an ESRI basemap through resolveBasemapStyle', async () => {
    const { map, button } = await boot({ stored: 'basemap_at' });
    button.click();
    await flush();
    button.click();
    await flush();
    expect(env.resolveBasemapStyle).toHaveBeenLastCalledWith('basemap_at', URLS.basemap_at);
    expect(map.setStyle).toHaveBeenLastCalledWith({ rewritten: URLS.basemap_at }, { diff: false });
  });

  it('lets a later swap win over an ESRI fetch still in flight', async () => {
    const { map, button } = await boot({ stored: 'basemap_at' });
    button.click();
    await flush();
    /** @type {(value: unknown) => void} */
    let release = () => {};
    env.resolveBasemapStyle.mockImplementationOnce(() => new Promise((r) => { release = r; }));
    button.click(); // 2D: the ESRI fetch hangs
    button.click(); // back to 3D before it lands
    await flush();
    release({ rewritten: URLS.basemap_at });
    await flush();
    expect(map.setStyle).toHaveBeenLastCalledWith(URLS.swisstopo_winter, { diff: false });
    expect(window.pwaMapView.get()).toBe('3d');
  });

  it('keeps the elevation source and swaps nothing for a reader on swisstopo winter', async () => {
    const { map, button } = await boot({ stored: 'swisstopo_winter' });
    button.click();
    button.click();
    expect(map.setStyle).not.toHaveBeenCalled();
    expect(map.getTerrain()).toBeNull();
    expect(map.getSource(core3d.DEM_SOURCE_ID)).toBeDefined();
  });
});

describe('the relief survives a setStyle', () => {
  it('is put back on the next styledata while in 3D, and not when flat', async () => {
    const { map, button } = await boot();
    button.click();
    await flush();
    map.setStyle('elsewhere');
    expect(inThreeD(map)).toBe(true);
    button.click();
    await flush();
    map.setStyle('elsewhere');
    expect(map.getSource(core3d.DEM_SOURCE_ID)).toBeUndefined();
  });
});

describe('the tilt gesture', () => {
  it('keeps the ceiling open in Terrain and enters on a reader\'s own tilt', async () => {
    const { map } = await boot({ stored: 'swisstopo_winter' });
    expect(map.maxPitch).toBe(core3d.MAX_PITCH);
    map.pitch = 12;
    map.fire('pitch', {});
    expect(window.pwaMapView.get()).toBe('flat');
    map.fire('pitch', { originalEvent: new Event('touchmove') });
    expect(window.pwaMapView.get()).toBe('3d');
    expect(map.pitch).toBe(12);
  });

  it('enters on a reader\'s own tilt in Conditions too', async () => {
    const { map } = await boot({ mode: 'dated', search: '', stored: 'swisstopo_winter' });
    expect(map.maxPitch).toBe(core3d.MAX_PITCH);
    map.pitch = 12;
    map.fire('pitch', { originalEvent: new Event('touchmove') });
    expect(window.pwaMapView.get()).toBe('3d');
  });

  it('holds the flat map flat below z9, in either mode', async () => {
    const { map } = await boot({ mode: 'dated', search: '' });
    map.zoom = 7;
    map.fire('moveend');
    expect(map.maxPitch).toBe(0);
    env.mode = 'terrain';
    document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode: 'terrain' } }));
    expect(map.maxPitch).toBe(0);
    map.zoom = 10;
    map.fire('moveend');
    expect(map.maxPitch).toBe(core3d.MAX_PITCH);
  });
});

describe('both modes in 3D', () => {
  it('opens ?view=3d in Conditions', async () => {
    const { map } = await boot({ mode: 'dated', search: '?view=3d', stored: 'swisstopo_winter' });
    expect(window.pwaMapView.get()).toBe('3d');
    expect(inThreeD(map)).toBe(true);
  });

  it('keeps 3D, the URL and the tilt across a mode change, with no line', async () => {
    const { map, button } = await boot();
    button.click();
    await flush();
    const pitch = map.pitch;
    env.toast.mockClear();
    for (const next of ['dated', 'terrain', 'dated']) {
      env.mode = next;
      document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode: next } }));
      expect(window.pwaMapView.get()).toBe('3d');
      expect(inThreeD(map)).toBe(true);
      expect(map.pitch).toBe(pitch);
      expect(map.maxPitch).toBe(core3d.MAX_PITCH);
      expect(view()).toBe('3d');
      expect(button.getAttribute('aria-pressed')).toBe('true');
    }
    expect(toasts()).toEqual([]);
  });
});

describe('what lays 3D flat', () => {
  it('going offline', async () => {
    const { button } = await boot();
    button.click();
    env.online = false;
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed', { detail: { online: false } }));
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts().at(-1)).toBe('Back in 2D: 3D needs a connection.');
  });

  it('a pan out of Switzerland', async () => {
    const { map, button } = await boot();
    button.click();
    map.centre = { ...PARIS };
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('flat');
    expect(view()).toBeNull();
    expect(toasts().at(-1)).toBe('Back in 2D: 3D covers Switzerland for now.');
  });
});

describe('drawing a route', () => {
  it('takes the relief and the tilt away on swisstopo winter, and gives them back', async () => {
    const { map, button } = await boot();
    button.click();
    await flush();
    map.setStyle.mockClear();
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: true } }));
    expect(map.getTerrain()).toBeNull();
    expect(map.pitch).toBe(0);
    expect(map.maxPitch).toBe(0);
    expect(map.setStyle).not.toHaveBeenCalled();
    expect(window.pwaMapView.get()).toBe('3d');
    map.fire('pitch', { originalEvent: new Event('mousemove') });
    expect(map.pitch).toBe(0);
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: false } }));
    expect(inThreeD(map)).toBe(true);
    expect(map.pitch).toBe(core3d.OPENING_CAMERA.pitch);
    expect(map.setStyle).not.toHaveBeenCalled();
  });

  it('lays the map flat at the end when 3D can no longer be drawn', async () => {
    const { map, button } = await boot();
    button.click();
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: true } }));
    map.centre = { ...PARIS };
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: false } }));
    expect(window.pwaMapView.get()).toBe('flat');
  });
});

describe('the disabled roundel', () => {
  it.each([
    ['zoomed out', { zoom: 7 }, {}, 'Zoom in to see 3D'],
    ['outside Switzerland', { centre: PARIS }, {}, '3D covers Switzerland for now'],
    ['offline', {}, { online: false }, '3D needs a connection'],
  ])('says why when %s', async (_case, camera, options, line) => {
    const { map, button } = await boot(options);
    Object.assign(map, camera);
    map.fire('moveend');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(map.getTerrain()).toBeNull();
    expect(toasts()).toEqual([line]);
  });
});

describe('the zoom floor', () => {
  it('says where 3D stops once per attempt, not once per wheel tick', async () => {
    const { map, button } = await boot();
    button.click();
    map.zoom = 9;
    let now = 1_000_000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    env.toast.mockClear();
    const container = map.getContainer();
    for (let i = 0; i < 6; i += 1) {
      now += 40;
      container.dispatchEvent(new WheelEvent('wheel', { deltaY: 100 }));
    }
    expect(toasts()).toEqual(['3D stops here. Switch to 2D to zoom out.']);
    now += 2000;
    container.dispatchEvent(new WheelEvent('wheel', { deltaY: 100 }));
    expect(toasts()).toHaveLength(2);
    container.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }));
    map.zoom = 11;
    now += 2000;
    container.dispatchEvent(new WheelEvent('wheel', { deltaY: 100 }));
    expect(toasts()).toHaveLength(2);
    vi.restoreAllMocks();
  });
});

describe('the basemap picker in 3D', () => {
  it('disables its rows and shows the line, and gives back only what it took', async () => {
    const { button } = await boot();
    const rows = () => Array.from(document.querySelectorAll('[data-basemap-key]'));
    const austria = document.querySelector('[data-basemap-key="basemap_at"]');
    // As map_layer_sync_status.js disables an offline, uncached row.
    austria.setAttribute('aria-disabled', 'true');
    button.click();
    expect(rows().every((r) => r.getAttribute('aria-disabled') === 'true')).toBe(true);
    expect(document.getElementById('basemap-view-note').hidden).toBe(false);
    button.click();
    expect(document.getElementById('basemap-view-note').hidden).toBe(true);
    expect(document.querySelector('[data-basemap-key="ign_plan"]')).toBeNull();
    expect(document.querySelector('[data-basemap-key="openfreemap_liberty"]').hasAttribute('aria-disabled')).toBe(false);
    expect(austria.getAttribute('aria-disabled')).toBe('true');
  });
});

beforeEach(() => {
  document.documentElement.removeAttribute('data-map-view');
});

// Each boot imports a fresh runner, and the earlier ones still listen on the
// document; laid flat, they answer no timelapse or route event of a later test.
afterEach(() => {
  if (window.pwaMapView) window.pwaMapView.exit();
});

/** Enter 3D from the switch and let the entering ease end. */
async function enterAndSettle() {
  const booted = await boot();
  booted.button.click();
  await flush();
  booted.map.fire('moveend');
  return booted;
}

/** One gesture: movestart, `intervals` timed intervals of `ms`, moveend. */
const gesture = (map, ms, intervals) => {
  map.fire('movestart');
  // The run's first frame has no interval, and its first interval is dropped.
  frames(ms, intervals + 2);
  map.fire('moveend');
};

const SLOW_MS = 50;
const FAST_MS = 20;
const perWindow = (ms) => core3d.FRAME_WINDOW_MS / ms;
/** Slow windows in a row that lay 3D flat. */
const TRIP = core3d.FRAME_WINDOWS_TO_TRIP;

describe('a lost WebGL context', () => {
  it('lays the map flat with one line, and its restore lets 3D back in', async () => {
    const { map, button } = await enterAndSettle();
    env.toast.mockClear();
    map.fire('webglcontextlost');
    expect(window.pwaMapView.get()).toBe('flat');
    expect(map.getTerrain()).toBeNull();
    expect(map.pitch).toBe(0);
    expect(toasts()).toEqual(['The map\'s graphics reset, so it is flat again.']);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(map.maxPitch).toBe(0);
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts().at(-1)).toBe('3D comes back once the map\'s graphics have reset');
    env.toast.mockClear();
    map.fire('webglcontextrestored');
    expect(toasts()).toEqual([]);
    expect(button.hasAttribute('aria-disabled')).toBe(false);
    expect(map.pitch).toBe(0);
    button.click();
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(inThreeD(map)).toBe(true);
  });

  it('holds the pixel ratio back until the context returns', async () => {
    const { map } = await enterAndSettle();
    expect(map.pixelRatio).toBe(core3d.MAX_PIXEL_RATIO);
    map.setPixelRatio.mockClear();
    // A resize on a lost context would read its 0x0 drawing buffer as the
    // canvas limit, so nothing resizes until the restore.
    map.fire('webglcontextlost');
    expect(map.setPixelRatio).not.toHaveBeenCalled();
    map.fire('webglcontextrestored');
    expect(map.setPixelRatio).toHaveBeenCalledTimes(1);
    expect(map.pixelRatio).toBe(3);
  });
});

describe('the frame watchdog', () => {
  it('lays the map flat after three slow windows, and keeps 3D off', async () => {
    const { map, button } = await enterAndSettle();
    env.toast.mockClear();
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    expect(window.pwaMapView.get()).toBe('3d');
    gesture(map, SLOW_MS, perWindow(SLOW_MS));
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts()).toEqual(['Back in 2D: 3D is too slow on this device.']);
    expect(rafQueue.size).toBe(0);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts().at(-1)).toBe('3D is too slow on this device');
    expect(map.maxPitch).toBe(0);
    map.fire('webglcontextrestored');
    expect(button.getAttribute('aria-disabled')).toBe('true');
  });

  it('counts failing windows across separate gestures', async () => {
    const { map } = await enterAndSettle();
    for (let i = 1; i < TRIP; i += 1) {
      gesture(map, SLOW_MS, perWindow(SLOW_MS));
      expect(window.pwaMapView.get()).toBe('3d');
    }
    gesture(map, SLOW_MS, perWindow(SLOW_MS));
    expect(window.pwaMapView.get()).toBe('flat');
  });

  it('stays in 3D when a good window breaks the bad ones', async () => {
    const { map } = await enterAndSettle();
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    gesture(map, FAST_MS, perWindow(FAST_MS));
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    expect(window.pwaMapView.get()).toBe('3d');
    expect(rafQueue.size).toBe(0);
  });

  it('drops the first interval of each run', async () => {
    const { map } = await enterAndSettle();
    // One window short of a trip, then a run whose only slow interval is
    // its first.
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    map.fire('movestart');
    frames(FAST_MS, 1);
    frames(5000, 1);
    frames(SLOW_MS, perWindow(SLOW_MS) - 1);
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('3d');
  });
});

describe('frames that are never timed', () => {
  it('while flat', async () => {
    const { map } = await boot();
    map.fire('moveend');
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    frames(SLOW_MS, 200);
    expect(window.requestAnimationFrame).not.toHaveBeenCalled();
  });

  it('between moves', async () => {
    const { map } = await enterAndSettle();
    expect(rafQueue.size).toBe(0);
    map.fire('movestart');
    expect(rafQueue.size).toBe(1);
    map.fire('moveend');
    expect(rafQueue.size).toBe(0);
  });

  it('during the entering ease, over the fresh style too', async () => {
    const { map, button } = await boot();
    button.click();
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    // The tilt runs again on the swapped style. MapLibre's easeTo stops the
    // ease still running first, firing its moveend, then starts the next.
    map.easeTo.mockImplementationOnce((o) => {
      map.fire('moveend');
      map.fire('movestart');
      map.pitch = Math.min(o.pitch, map.maxPitch);
    });
    await flush();
    frames(SLOW_MS, perWindow(SLOW_MS) * 3);
    expect(rafQueue.size).toBe(0);
    expect(window.pwaMapView.get()).toBe('3d');
    map.fire('moveend');
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * TRIP);
    expect(window.pwaMapView.get()).toBe('flat');
  });

  it('in a hidden tab', async () => {
    const { map } = await enterAndSettle();
    setHidden(true);
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    map.fire('moveend');
    setHidden(false);
    map.fire('movestart');
    frames(SLOW_MS, perWindow(SLOW_MS) + 2);
    setHidden(true);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(rafQueue.size).toBe(0);
    frames(SLOW_MS, 200);
    expect(window.pwaMapView.get()).toBe('3d');
  });

  it('while a route is being drawn', async () => {
    const { map } = await enterAndSettle();
    map.fire('movestart');
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: true } }));
    expect(rafQueue.size).toBe(0);
    map.fire('moveend');
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
  });
});

describe('the device gate', () => {
  it.each([
    ['no WebGL on the canvas', { webgl: false }],
    ['2 GB on Android Chrome', { deviceMemory: 2, userAgent: ANDROID_UA }],
  ])('keeps 3D off at boot with %s: a tap says why and a tilt is ignored', async (_case, device) => {
    const { map, button } = await boot(device);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(map.maxPitch).toBe(0);
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts()).toEqual(['3D is not available on this device']);
    // With the ceiling at 0 a reader's tilt does not move the map.
    map.pitch = Math.min(12, map.maxPitch);
    map.fire('pitch', { originalEvent: new Event('touchmove') });
    expect(window.pwaMapView.get()).toBe('flat');
  });

  it('lets 2 GB on a desktop in', async () => {
    const { button } = await boot({ deviceMemory: 2 });
    expect(button.hasAttribute('aria-disabled')).toBe(false);
  });

  it('lays a ?view=3d boot flat on the reader\'s basemap', async () => {
    const { map } = await boot({ search: '?mode=terrain&view=3d', webgl: false, pitch: 64 });
    await flush();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(map.pitch).toBe(0);
    expect(map.maxPitch).toBe(0);
    expect(inThreeD(map)).toBe(false);
    expect(map.setStyle).toHaveBeenCalledWith(URLS.openfreemap_liberty, { diff: false });
    expect(view()).toBeNull();
    expect(toasts()).toEqual([]);
  });
});

describe('the timelapse plays in 3D (SNOW-1452)', () => {
  /** @param {boolean} playing */
  const timelapse = (playing) => {
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing } }));
  };

  let stopCounting = () => {};

  /** Count snowdesk:basemap-changing, which stops the timelapse (SNOW-58). */
  const countSwaps = () => {
    const seen = { n: 0 };
    const count = () => { seen.n += 1; };
    document.addEventListener('snowdesk:basemap-changing', count);
    stopCounting = () => document.removeEventListener('snowdesk:basemap-changing', count);
    return seen;
  };

  afterEach(() => {
    stopCounting();
    timelapse(false);
  });

  it('keeps the relief and the tilt, with no setStyle and no basemap swap', async () => {
    const { map } = await enterAndSettle();
    const pitch = map.pitch;
    map.setStyle.mockClear();
    const swaps = countSwaps();
    timelapse(true);
    expect(window.pwaMapView.get()).toBe('3d');
    expect(inThreeD(map)).toBe(true);
    expect(map.pitch).toBe(pitch);
    expect(map.setStyle).not.toHaveBeenCalled();
    expect(swaps.n).toBe(0);
  });

  it('times its frames with the camera still, and slow windows go flat', async () => {
    const { map } = await enterAndSettle();
    env.toast.mockClear();
    const swaps = countSwaps();
    timelapse(true);
    expect(rafQueue.size).toBe(1);
    frames(SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1) + 2);
    expect(window.pwaMapView.get()).toBe('3d');
    frames(SLOW_MS, perWindow(SLOW_MS));
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts()).toEqual(['Back in 2D: 3D is too slow on this device.']);
    // The way back to the reader's basemap is what stops the timelapse.
    expect(swaps.n).toBe(1);
    expect(rafQueue.size).toBe(0);
  });

  it('stops timing when it stops, unless the camera is still moving', async () => {
    const { map } = await enterAndSettle();
    timelapse(true);
    frames(FAST_MS, 3);
    timelapse(false);
    expect(rafQueue.size).toBe(0);
    timelapse(true);
    map.fire('movestart');
    timelapse(false);
    expect(rafQueue.size).toBe(1);
    map.fire('moveend');
    expect(rafQueue.size).toBe(0);
  });

  it('is timed from the end of the entering ease when it starts during it', async () => {
    const { map, button } = await boot();
    button.click();
    await flush();
    timelapse(true);
    expect(rafQueue.size).toBe(0);
    map.fire('moveend');
    expect(rafQueue.size).toBe(1);
  });
});
