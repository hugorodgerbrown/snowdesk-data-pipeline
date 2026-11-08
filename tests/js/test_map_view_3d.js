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
 * five slow frame windows ask whether to keep 3D (SNOW-1463: Switch to 2D
 * lays it flat for the visit; Keep 3D, the ×, or another line silence the
 * watchdog); frames are never timed while flat, between moves, during the
 * entering ease, in a hidden tab or while tiles load; a bad window then a
 * good one stays in 3D; each closed window reaches the debug trace while it
 * records; and the device gate keeps 3D off at boot.
 *
 * SNOW-1452: the timelapse plays in 3D with no setStyle, and the watchdog
 * times its frames with the camera still; a trip during playback still
 * goes flat at once, with no prompt.
 *
 * SNOW-1456: the 3D test's hooks. beginTest enters 3D even after the
 * watchdog laid it flat, with the watchdog resting; endTest puts the camera
 * and the view back, and a smooth verdict lifts the latch where a slow one
 * keeps it; the why-slow line carries the "Test this device" link.
 *
 * SNOW-1450: a ?view=3d boot keeps the tilt a link's `?at=` asked for
 * (snowdeskMapState.linkedPitch); a pitch of 0, none, and every later
 * toggle tilt to the opening pitch.
 *
 * SNOW-1460: with the map_beyond_switzerland flag the coverage is handed
 * over (setCoverage, the Alpine gate): a ?view=3d boot waits for it under
 * the two-second cap, and a pan to Innsbruck stays in 3D where off the flag
 * it lays the map flat.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/slope_classes_core.js';
import '../../static/js/terrain_filter_core.js';
import '../../static/js/map_view_core.js';
import '../../static/js/map_3d_core.js';
import '../../static/js/map_3d_terrain.js';
import '../../static/js/basemap_download_core.js';
import '../../static/js/beyond_switzerland_core.js';
import { loadMapBundle } from './_load_map_bundle.js';

const core3d = window.pwaMap3dCore;
const terrain = window.pwaMap3dTerrain;
const ZERMATT = { lng: 7.75, lat: 46.02 };
const PARIS = { lng: 2.35, lat: 48.86 };
const INNSBRUCK = { lng: 11.39, lat: 47.26 };
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
    jumpTo: vi.fn((o) => {
      if (o.center) map.centre = { lng: o.center[0], lat: o.center[1] };
      if ('zoom' in o) map.zoom = Math.max(o.zoom, map.minZoom);
      if ('bearing' in o) map.bearing = o.bearing;
      if ('pitch' in o) map.pitch = Math.min(o.pitch, map.maxPitch);
    }),
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
  areas = [], holdAreas = false, linkedPitch = null, modes = true, beyond = false,
} = {}) {
  const rows = Object.entries(URLS).map(([key, url]) => `
    <button class="basemap-menu-item" role="menuitemradio" data-basemap-key="${key}"
      data-basemap-url="${url}" aria-checked="false">${key}</button>`).join('');
  document.body.innerHTML = `
    <div id="map" data-map-view-enabled="true" data-default-basemap-key="openfreemap_liberty"${beyond ? ' data-map-beyond-switzerland="true"' : ''}></div>
    <div id="basemap-pill"><button id="basemap-toggle"></button></div>
    <div id="basemap-menu" hidden>
      <ul><li id="basemap-view-note" hidden>note</li>${rows}</ul>
    </div>
    <button id="map-view-toggle" data-dem-tile-url="https://dem.example/{z}/{x}/{y}.webp" aria-pressed="false">
      <span data-view-face="3d">3D</span><span data-view-face="flat">2D</span>
    </button>
    <div id="map-sheet-toast" class="hidden"><span data-toast-body></span><button data-action="dismiss">×</button></div>`;
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
    // MapSheet.toast as the page has it: the line goes on the toast's body.
    toast: vi.fn((text) => {
      const el = document.getElementById('map-sheet-toast');
      el.querySelector('[data-toast-body]').textContent = text;
      el.classList.remove('hidden');
    }),
    addProtocol: vi.fn(),
    resolveBasemapStyle: vi.fn((key, url) => Promise.resolve(key === 'basemap_at' ? { rewritten: url } : url)),
    // SNOW-1453: the device's downloaded areas, read from IndexedDB in the
    // page, so asynchronously; `holdAreas` keeps the first read pending
    // until `releaseAreas()`.
    areas,
    releaseAreas: () => {},
  };

  window.MapSheet = { toast: env.toast };
  // SNOW-1451: a reader without map_modes has no map_mode.js on the page.
  if (modes) window.pwaMapMode = { get: () => env.mode };
  else delete window.pwaMapMode;
  window.pwaConnectivity = { isOnline: () => env.online };
  window.maplibregl = { addProtocol: env.addProtocol };
  window.resolveBasemapStyle = env.resolveBasemapStyle;
  window.fetch = vi.fn(() => Promise.resolve({ ok: true }));
  // The picker reads its globals from map_state.js and map_shared.js.
  loadMapBundle(['map_state.js', 'map_shared.js', 'map_basemap_picker.js']);
  // After map_state.js, which publishes its own (empty) one.
  window.snowdeskMapState = { map, linkedPitch };
  // After the bundle too, which loads the real reader.
  let held = holdAreas;
  window.pwaBasemapAreas = {
    downloadedAreas: vi.fn(() => {
      if (!held) return Promise.resolve(env.areas);
      held = false;
      return new Promise((resolve) => { env.releaseAreas = () => resolve(env.areas); });
    }),
  };
  vi.resetModules();
  await import('../../static/js/map_view_3d.js');
  return { map, button: document.getElementById('map-view-toggle') };
}

const inThreeD = (map) => terrain.hasTerrain(map) && !!map.getLayer(terrain.SUNLIGHT_LAYER);
const toasts = () => env.toast.mock.calls.map((c) => c[0]);
const view = () => new URLSearchParams(window.location.search).get('view');

describe('entering 3D', () => {
  afterEach(() => vi.restoreAllMocks());

  it('runs without the modes, setting the view on :root (SNOW-1451)', async () => {
    const { map, button } = await boot({ modes: false, search: '' });
    expect(window.pwaMapMode).toBeUndefined();
    expect(document.documentElement.dataset.mapView).toBe('flat');
    button.click();
    await flush();
    expect(inThreeD(map)).toBe(true);
    expect(document.documentElement.dataset.mapView).toBe('3d');
  });

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

  // SNOW-1467: a boot tilts in before the style's styledata installs the
  // terrain. MapLibre follows the ground's height on each ease frame only
  // when terrain was on as the ease began, so terrain arriving mid-ease
  // threw in the render queue and froze the map blank. The tilt freezes it.
  it('tilts in with the elevation frozen, on a boot and on a tap', async () => {
    const { map, button } = await boot({ search: '?mode=terrain&view=3d' });
    expect(map.easeTo).toHaveBeenCalledWith(expect.objectContaining({
      pitch: core3d.OPENING_CAMERA.pitch, freezeElevation: true,
    }));
    button.click();
    map.easeTo.mockClear();
    button.click();
    await flush();
    expect(map.easeTo).toHaveBeenCalled();
    for (const [options] of map.easeTo.mock.calls) {
      if ('pitch' in options && options.pitch > 0) expect(options.freezeElevation).toBe(true);
    }
  });

  // SNOW-1450: map.js publishes the pitch a link's `?at=` asked for.
  it('keeps a linked pitch on a ?view=3d boot, with the ceiling open', async () => {
    const { map } = await boot({ search: '?mode=terrain&view=3d', pitch: 30, linkedPitch: 30 });
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(map.maxPitch).toBe(core3d.MAX_PITCH);
    expect(map.pitch).toBe(30);
    expect(map.easeTo).not.toHaveBeenCalledWith(expect.objectContaining({ pitch: core3d.OPENING_CAMERA.pitch }));
  });

  it.each([
    ['a linked pitch of 0', 0],
    ['no linked pitch', null],
  ])('tilts a ?view=3d boot to the opening pitch with %s', async (_label, linkedPitch) => {
    const { map } = await boot({ search: '?mode=terrain&view=3d', linkedPitch });
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(map.pitch).toBe(core3d.OPENING_CAMERA.pitch);
  });

  it('tilts a later toggle to the opening pitch, whatever the link asked for', async () => {
    const { map, button } = await boot({ search: '?mode=terrain&view=3d', pitch: 30, linkedPitch: 30 });
    await flush();
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
    button.click();
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(map.pitch).toBe(core3d.OPENING_CAMERA.pitch);
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
    // Decided once the boxes are read again.
    await flush();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts().at(-1)).toBe('Back in 2D: offline, 3D works only in a downloaded area.');
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

// SNOW-1453: a download with terrain data pins the elevation tiles and
// stamps its record with their template, so 3D works offline inside it.
describe('offline, in a downloaded area', () => {
  const DEM = 'https://dem.example/{z}/{x}/{y}.webp';
  // Round Zermatt, stamped with the template the switch carries.
  // On the 3D basemap: only an area downloaded on it can draw 3D offline.
  const AREA = {
    id: 'custom-1',
    terrain: true,
    elevation: DEM,
    basemapKey: 'swisstopo_winter',
    bbox: [7.6, 45.95, 7.9, 46.1],
  };
  const VERBIER = { lng: 7.23, lat: 46.1 };
  const goOffline = () => {
    env.online = false;
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed', { detail: { online: false } }));
  };

  it('reads the device\'s own areas only, never the account\'s', async () => {
    await boot({ areas: [AREA] });
    await flush();
    expect(window.pwaBasemapAreas.downloadedAreas).toHaveBeenCalledWith({ deviceOnly: true });
  });

  it('stays in 3D going offline inside the area, and leaves it with a line on a pan out', async () => {
    const { map, button } = await boot({ areas: [AREA] });
    await flush();
    button.click();
    await flush();
    goOffline();
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');

    map.centre = { ...VERBIER };
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('flat');
    expect(view()).toBeNull();
    expect(toasts().at(-1)).toBe('Back in 2D: offline, 3D works only in a downloaded area.');

    // Back in, 3D is available again but not entered.
    map.centre = { ...ZERMATT };
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('flat');
    expect(button.hasAttribute('aria-disabled')).toBe(false);
  });

  it('judges going offline on the areas read afresh, not the last list', async () => {
    const { button } = await boot({ areas: [] });
    await flush();
    button.click();
    await flush();
    // Downloaded since the last read, with no event to say so.
    env.areas = [AREA];
    goOffline();
    expect(window.pwaMapView.get()).toBe('3d');
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(toasts()).not.toContain('Back in 2D: offline, 3D works only in a downloaded area.');
  });

  it('leaves out a stamped area downloaded on another basemap (SNOW-1453 review)', async () => {
    const { button } = await boot({ areas: [{ ...AREA, basemapKey: 'openfreemap_liberty' }], online: false });
    await flush();
    expect(button.getAttribute('aria-disabled')).toBe('true');
  });

  it('leaves out an area stamped under another template', async () => {
    const stale = { ...AREA, elevation: 'https://dem.example/2025/{z}/{x}/{y}.webp' };
    const { button } = await boot({ areas: [stale], online: false });
    await flush();
    expect(button.getAttribute('aria-disabled')).toBe('true');
  });

  it('enters a ?view=3d boot inside the area once the read of the areas lands', async () => {
    const { map, button } = await boot({
      search: '?mode=terrain&view=3d', online: false, areas: [AREA], holdAreas: true,
    });
    // Until the areas are known the switch is off, for `offline`.
    expect(window.pwaMapView.get()).toBe('flat');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts()).toEqual(['3D needs a connection or a downloaded area']);

    env.releaseAreas();
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(inThreeD(map)).toBe(true);
    expect(view()).toBe('3d');
  });

  it('lays the same boot flat on the reader\'s basemap outside every area', async () => {
    const { map } = await boot({
      search: '?mode=terrain&view=3d', online: false, areas: [{ ...AREA, bbox: [7.2, 46.05, 7.3, 46.12] }],
      holdAreas: true,
    });
    env.releaseAreas();
    await flush();
    await flush();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(map.setStyle).toHaveBeenCalledWith(URLS.openfreemap_liberty, { diff: false });
    expect(view()).toBeNull();
    expect(map.pitch).toBe(0);
  });

  it('lays the boot flat after two seconds when the read of the areas never lands', async () => {
    vi.useFakeTimers();
    try {
      const { map, button } = await boot({
        search: '?mode=terrain&view=3d', online: false, areas: [AREA], holdAreas: true,
      });
      await vi.advanceTimersByTimeAsync(1900);
      expect(map.setStyle).not.toHaveBeenCalled();
      expect(view()).toBe('3d');

      await vi.advanceTimersByTimeAsync(200);
      expect(window.pwaMapView.get()).toBe('flat');
      expect(map.setStyle).toHaveBeenCalledWith(URLS.openfreemap_liberty, { diff: false });
      expect(view()).toBeNull();
      expect(map.pitch).toBe(0);
      expect(button.getAttribute('aria-disabled')).toBe('true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('decides the offline boot once: the timer after an early read does nothing', async () => {
    vi.useFakeTimers();
    try {
      const { map } = await boot({
        search: '?mode=terrain&view=3d', online: false, areas: [{ ...AREA, bbox: [7.2, 46.05, 7.3, 46.12] }],
        holdAreas: true,
      });
      env.releaseAreas();
      await vi.advanceTimersByTimeAsync(50);
      expect(window.pwaMapView.get()).toBe('flat');
      const swaps = map.setStyle.mock.calls.length;
      // The reader pans into an area before the cap would have fired.
      env.areas = [AREA];
      document.dispatchEvent(new CustomEvent('snowdesk:downloads-changed'));
      map.centre = { ...ZERMATT };
      map.fire('moveend');
      await vi.advanceTimersByTimeAsync(2500);
      expect(window.pwaMapView.get()).toBe('flat');
      expect(map.setStyle.mock.calls.length).toBe(swaps);
    } finally {
      vi.useRealTimers();
    }
  });

  it('reads the areas again on snowdesk:downloads-changed', async () => {
    const { button } = await boot({ online: false, areas: [] });
    await flush();
    expect(button.getAttribute('aria-disabled')).toBe('true');

    env.areas = [AREA];
    document.dispatchEvent(new CustomEvent('snowdesk:downloads-changed'));
    await flush();
    expect(button.hasAttribute('aria-disabled')).toBe(false);
    button.click();
    expect(window.pwaMapView.get()).toBe('3d');

    // And a removal lays it flat, offline.
    env.areas = [];
    document.dispatchEvent(new CustomEvent('snowdesk:downloads-changed'));
    await flush();
    expect(window.pwaMapView.get()).toBe('flat');
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
    ['offline', {}, { online: false }, '3D needs a connection or a downloaded area'],
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
async function enterAndSettle(options = {}) {
  const booted = await boot(options);
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

const SLOW_MS = 55;
const FAST_MS = 20;
/** Intervals of `ms` that fill one watchdog window exactly. */
const perWindow = (ms) => Math.ceil(core3d.FRAME_WINDOW_MS / ms);
/** Slow windows in a row that trip the watchdog. */
const TRIP = core3d.FRAME_WINDOWS_TO_TRIP;

const toastEl = () => document.getElementById('map-sheet-toast');
const promptShown = () => !toastEl().classList.contains('hidden')
  && !!toastEl().querySelector('[data-map-3d-slow-prompt]');
/** Trip the watchdog in one gesture: the prompt asks. */
const tripWatchdog = (map) => gesture(map, SLOW_MS, perWindow(SLOW_MS) * TRIP);
/** Trip it and answer Switch to 2D: flat for the visit. */
const switchTo2d = (map) => {
  tripWatchdog(map);
  toastEl().querySelector('[data-map-3d-switch-2d]').click();
};

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

describe('the pixel ratio on a large canvas (SNOW-1464)', () => {
  /** Give the map's container a CSS size. */
  const sizeTo = (map, width, height) => {
    const container = map.getContainer();
    Object.defineProperty(container, 'clientWidth', { value: width, configurable: true });
    Object.defineProperty(container, 'clientHeight', { value: height, configurable: true });
  };

  it('draws a large canvas at 1.5x and gives the ratio back on leaving', async () => {
    const { map, button } = await boot();
    sizeTo(map, 744, 1011);
    button.click();
    await flush();
    expect(map.pixelRatio).toBe(core3d.LARGE_CANVAS_PIXEL_RATIO);
    button.click();
    expect(map.pixelRatio).toBe(3);
  });

  it('goes back to 2x when a resize in 3D makes the canvas small', async () => {
    const { map, button } = await boot();
    sizeTo(map, 744, 1011);
    button.click();
    await flush();
    sizeTo(map, 390, 844);
    map.fire('resize');
    expect(map.pixelRatio).toBe(core3d.MAX_PIXEL_RATIO);
  });

  it('leaves the ratio alone on a resize while flat', async () => {
    const { map } = await boot();
    sizeTo(map, 744, 1011);
    map.fire('resize');
    expect(map.setPixelRatio).not.toHaveBeenCalled();
  });
});

describe('the frame watchdog', () => {
  it('asks after five slow windows, staying in 3D with timing stopped', async () => {
    const { map } = await enterAndSettle();
    env.toast.mockClear();
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    expect(promptShown()).toBe(false);
    map.fire('movestart');
    frames(SLOW_MS, perWindow(SLOW_MS) + 2);
    expect(window.pwaMapView.get()).toBe('3d');
    expect(inThreeD(map)).toBe(true);
    expect(promptShown()).toBe(true);
    expect(rafQueue.size).toBe(0);
    expect(env.toast).toHaveBeenCalledWith('3D is running slowly here', { sticky: true });
    const prompt = toastEl().querySelector('[data-map-3d-slow-prompt]');
    expect([...prompt.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Switch to 2D', 'Keep 3D']);
    map.fire('moveend');
  });

  it('Switch to 2D lays the map flat and keeps 3D off for the visit', async () => {
    const { map, button } = await enterAndSettle();
    env.toast.mockClear();
    switchTo2d(map);
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts().at(-1)).toBe('Back in 2D: 3D is too slow on this device.');
    expect(toastEl().querySelector('[data-map-3d-slow-prompt]')).toBeNull();
    expect(rafQueue.size).toBe(0);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts().at(-1)).toBe('3D is too slow on this device');
    expect(toastEl().querySelector('[data-map-3d-test-open]').textContent).toBe('Test this device');
    expect(map.maxPitch).toBe(0);
    map.fire('webglcontextrestored');
    expect(button.getAttribute('aria-disabled')).toBe('true');
  });

  it('Keep 3D hides the prompt and times nothing more this visit', async () => {
    const { map, button } = await enterAndSettle();
    tripWatchdog(map);
    toastEl().querySelector('[data-map-3d-keep]').click();
    expect(toastEl().classList.contains('hidden')).toBe(true);
    expect(window.pwaMapView.get()).toBe('3d');
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    map.fire('moveend');
    // Out and back in: still silenced, and the switch is not disabled.
    button.click();
    button.click();
    await flush();
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('3d');
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    map.fire('moveend');
  });

  it('counts the × as Keep 3D', async () => {
    const { map } = await enterAndSettle();
    tripWatchdog(map);
    // overlays.js hides the toast on its ×.
    toastEl().classList.add('hidden');
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    expect(window.pwaMapView.get()).toBe('3d');
  });

  it('counts another line taking the toast\'s place as Keep 3D', async () => {
    const { map } = await enterAndSettle();
    tripWatchdog(map);
    window.MapSheet.toast('Could not load the regions.');
    expect(toastEl().querySelector('[data-map-3d-slow-prompt]')).toBeNull();
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    map.fire('moveend');
    // Leaving 3D now leaves the other line where it is.
    window.pwaMapView.exit();
    expect(toastEl().classList.contains('hidden')).toBe(false);
    expect(toastEl().textContent).toContain('Could not load the regions.');
  });

  it.each([
    ['the switch', ({ button }) => button.click()],
    ['a coverage exit', ({ map }) => { map.centre = { ...PARIS }; map.fire('moveend'); }],
    ['a lost WebGL context', ({ map }) => map.fire('webglcontextlost')],
  ])('takes the prompt away when 3D is left by %s', async (_case, leave) => {
    const booted = await enterAndSettle();
    tripWatchdog(booted.map);
    expect(promptShown()).toBe(true);
    leave(booted);
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toastEl().querySelector('[data-map-3d-slow-prompt]')).toBeNull();
  });

  it('counts failing windows across separate gestures', async () => {
    const { map } = await enterAndSettle();
    for (let i = 1; i < TRIP; i += 1) {
      gesture(map, SLOW_MS, perWindow(SLOW_MS));
      expect(promptShown()).toBe(false);
    }
    gesture(map, SLOW_MS, perWindow(SLOW_MS));
    expect(promptShown()).toBe(true);
  });

  it('stays quiet when a good window breaks the bad ones', async () => {
    const { map } = await enterAndSettle();
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    gesture(map, FAST_MS, perWindow(FAST_MS));
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    expect(promptShown()).toBe(false);
    expect(rafQueue.size).toBe(0);
  });

  it('never trips on a steady 30 fps', async () => {
    const { map } = await enterAndSettle();
    gesture(map, 1000 / 30, perWindow(1000 / 30) * TRIP * 4);
    expect(promptShown()).toBe(false);
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
    expect(promptShown()).toBe(false);
  });

  it('does not time frames while tiles load, nor the interval after', async () => {
    const { map } = await enterAndSettle();
    let loaded = false;
    map.areTilesLoaded = () => loaded;
    gesture(map, 200, perWindow(SLOW_MS) * TRIP * 3);
    expect(promptShown()).toBe(false);
    // The interval that ends on the first loaded frame began on a loading
    // one, so a stall there is not counted either.
    map.fire('movestart');
    frames(FAST_MS, 2);
    loaded = true;
    frames(5000, 1);
    frames(SLOW_MS, perWindow(SLOW_MS) * (TRIP - 1));
    map.fire('moveend');
    expect(promptShown()).toBe(false);
    gesture(map, SLOW_MS, perWindow(SLOW_MS));
    expect(promptShown()).toBe(true);
  });

  it('records each closed window on the debug trace while it records, and nothing otherwise', async () => {
    const record = vi.fn();
    window.pwaDebugLog = { on: true, record };
    const { map } = await enterAndSettle();
    gesture(map, FAST_MS, perWindow(FAST_MS) * 2);
    expect(record).toHaveBeenCalledTimes(2);
    expect(record).toHaveBeenCalledWith('map', '3d.window', {
      medianMs: FAST_MS, frames: perWindow(FAST_MS), skippedMs: 0, slow: false, failing: 0, tripped: false,
    });
    record.mockClear();
    window.pwaDebugLog.on = false;
    gesture(map, FAST_MS, perWindow(FAST_MS) * 2);
    expect(record).not.toHaveBeenCalled();
    delete window.pwaDebugLog;
    expect(() => gesture(map, FAST_MS, perWindow(FAST_MS) * 2)).not.toThrow();
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
    tripWatchdog(map);
    expect(promptShown()).toBe(true);
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

  it('times its frames with the camera still, and slow windows go flat with no prompt', async () => {
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
    expect(toastEl().querySelector('[data-map-3d-slow-prompt]')).toBeNull();
    // The way back to the reader's basemap is what stops the timelapse.
    expect(swaps.n).toBe(1);
    expect(rafQueue.size).toBe(0);
  });

  it('is not timed after Keep 3D', async () => {
    const { map } = await enterAndSettle();
    tripWatchdog(map);
    toastEl().querySelector('[data-map-3d-keep]').click();
    timelapse(true);
    expect(rafQueue.size).toBe(0);
    frames(SLOW_MS, perWindow(SLOW_MS) * TRIP * 2);
    expect(window.pwaMapView.get()).toBe('3d');
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

  it('goes flat with the reason when slow on swisstopo winter, where no swap fires', async () => {
    const { map } = await enterAndSettle({ stored: 'swisstopo_winter' });
    const swaps = countSwaps();
    const changes = [];
    const record = (e) => changes.push(e.detail);
    document.addEventListener('snowdesk:map-view-changed', record);
    timelapse(true);
    frames(SLOW_MS, perWindow(SLOW_MS) * TRIP + 2);
    document.removeEventListener('snowdesk:map-view-changed', record);
    expect(window.pwaMapView.get()).toBe('flat');
    expect(swaps.n).toBe(0);
    // map_timelapse.js stops on this, swap or no swap.
    expect(changes).toEqual([{ view: 'flat', reason: 'slow' }]);
    expect(map.getTerrain()).toBeNull();
  });

  it('resumes timing when a hidden tab comes back mid-playback', async () => {
    await enterAndSettle();
    timelapse(true);
    setHidden(true);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(rafQueue.size).toBe(0);
    setHidden(false);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(rafQueue.size).toBe(1);
    frames(SLOW_MS, perWindow(SLOW_MS) * TRIP + 2);
    expect(window.pwaMapView.get()).toBe('flat');
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

describe('the 3D test hooks (SNOW-1456)', () => {
  /** Enter, then trip the watchdog: flat for the visit. */
  const tooSlow = async (options = {}) => {
    const booted = await enterAndSettle(options);
    switchTo2d(booted.map);
    expect(window.pwaMapView.get()).toBe('flat');
    return booted;
  };

  it('beginTest enters 3D despite the latch, with the watchdog off', async () => {
    const { map } = await tooSlow();
    expect(window.pwaMapView.canTest()).toEqual({ ok: true, reason: null });
    expect(window.pwaMapView.beginTest()).toBe(true);
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(inThreeD(map)).toBe(true);
    map.fire('moveend');
    gesture(map, SLOW_MS, perWindow(SLOW_MS) * TRIP * 2);
    expect(window.pwaMapView.get()).toBe('3d');
    expect(rafQueue.size).toBe(0);
  });

  it('endTest with smooth lifts the latch and puts the flat view and camera back', async () => {
    const { map, button } = await tooSlow();
    map.centre = { lng: 7.6, lat: 46.1 };
    map.zoom = 10.5;
    map.bearing = 12;
    window.pwaMapView.beginTest();
    await flush();
    map.jumpTo({ center: [7.87, 46.07], zoom: 9, bearing: 200 });
    env.toast.mockClear();
    window.pwaMapView.endTest({ verdict: 'smooth' });
    expect(window.pwaMapView.get()).toBe('flat');
    expect(map.centre).toEqual({ lng: 7.6, lat: 46.1 });
    expect(map.zoom).toBe(10.5);
    expect(map.bearing).toBe(12);
    expect(map.pitch).toBe(0);
    expect(toasts()).toEqual([]);
    expect(button.hasAttribute('aria-disabled')).toBe(false);
    button.click();
    expect(window.pwaMapView.get()).toBe('3d');
  });

  it('endTest with stutter lifts the latch too', async () => {
    const { button } = await tooSlow();
    window.pwaMapView.beginTest();
    window.pwaMapView.endTest({ verdict: 'stutter' });
    expect(button.hasAttribute('aria-disabled')).toBe(false);
  });

  it('endTest with slow, or with no verdict, keeps the latch', async () => {
    const { map, button } = await tooSlow();
    for (const result of [{ verdict: 'slow' }, {}]) {
      window.pwaMapView.beginTest();
      await flush();
      window.pwaMapView.endTest(result);
      expect(window.pwaMapView.get()).toBe('flat');
      expect(button.getAttribute('aria-disabled')).toBe('true');
      expect(map.maxPitch).toBe(0);
    }
    button.click();
    expect(window.pwaMapView.get()).toBe('flat');
  });

  it('keeps a reader who was in 3D in 3D, on their own camera', async () => {
    const { map } = await enterAndSettle();
    map.bearing = 77;
    const pitch = map.pitch;
    window.pwaMapView.beginTest();
    map.jumpTo({ center: [7.87, 46.07], zoom: 9, bearing: 215 });
    window.pwaMapView.endTest({ verdict: 'slow' });
    expect(window.pwaMapView.get()).toBe('3d');
    expect(map.centre).toEqual(ZERMATT);
    expect(map.bearing).toBe(77);
    expect(map.pitch).toBe(pitch);
  });

  it('says where the test cannot run, and beginTest refuses there', async () => {
    const offline = await boot({ online: false });
    expect(window.pwaMapView.canTest()).toEqual({ ok: false, reason: 'offline' });
    expect(window.pwaMapView.beginTest()).toBe(false);
    expect(offline.map.getTerrain()).toBeNull();
    await boot({ webgl: false });
    expect(window.pwaMapView.canTest()).toEqual({ ok: false, reason: 'device' });
    const lost = await boot();
    lost.map.fire('webglcontextlost');
    expect(window.pwaMapView.canTest()).toEqual({ ok: false, reason: 'webgl' });
  });

  it('puts a "Test this device" link on the why-slow line', async () => {
    const { button } = await tooSlow();
    const asked = vi.fn();
    document.addEventListener('snowdesk:map-3d-test-requested', asked);
    button.click();
    const link = toastEl().querySelector('[data-map-3d-test-open]');
    expect(toastEl().textContent).toContain('3D is too slow on this device');
    expect(link.textContent).toBe('Test this device');
    link.click();
    document.removeEventListener('snowdesk:map-3d-test-requested', asked);
    expect(asked).toHaveBeenCalledTimes(1);
    expect(toastEl().classList.contains('hidden')).toBe(true);
  });

  it('leaves Keep 3D\'s silence alone across a test', async () => {
    const { map } = await enterAndSettle();
    tripWatchdog(map);
    toastEl().querySelector('[data-map-3d-keep]').click();
    window.pwaMapView.beginTest();
    window.pwaMapView.endTest({ verdict: 'smooth' });
    map.fire('movestart');
    expect(rafQueue.size).toBe(0);
    map.fire('moveend');
  });
});

describe('the coverage beyond Switzerland (SNOW-1460)', () => {
  const GATE = window.pwaBeyondSwitzerlandCore.ALPINE_GATE;

  it('off the flag, a pan to Innsbruck lays 3D flat', async () => {
    const { map, button } = await boot();
    button.click();
    map.centre = { ...INNSBRUCK };
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('flat');
  });

  it('with the flag, a pan to Innsbruck stays in 3D once the Alpine gate is handed over, and Paris still lays it flat', async () => {
    const { map, button } = await boot({ beyond: true });
    window.pwaMapView.setCoverage(GATE);
    button.click();
    map.centre = { ...INNSBRUCK };
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('3d');
    map.centre = { ...PARIS };
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('flat');
  });

  it('with the flag, a ?view=3d boot at Innsbruck waits for the gate, then enters', async () => {
    const { map } = await boot({ beyond: true, search: '?mode=terrain&view=3d' });
    map.centre = { ...INNSBRUCK };
    await flush();
    expect(inThreeD(map)).toBe(false);
    window.pwaMapView.setCoverage(GATE);
    await flush();
    expect(window.pwaMapView.get()).toBe('3d');
    expect(inThreeD(map)).toBe(true);
    expect(view()).toBe('3d');
  });

  it('with the flag, a ?view=3d boot at Innsbruck lays flat after two seconds when no gate comes', async () => {
    vi.useFakeTimers();
    try {
      const { map } = await boot({ beyond: true, search: '?mode=terrain&view=3d' });
      map.centre = { ...INNSBRUCK };
      await vi.advanceTimersByTimeAsync(1900);
      expect(view()).toBe('3d');
      await vi.advanceTimersByTimeAsync(200);
      expect(window.pwaMapView.get()).toBe('flat');
      expect(view()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
