/*
 * tests/js/test_map_view_3d.js — /map/'s 3-D view against a fake map
 * (SNOW-1449).
 *
 * Pinned: entering (swisstopo winter, the relief, the tilt, the floor, the
 * URL, one line) and leaving (the reader's basemap back, ESRI awaited,
 * none at all for a reader on swisstopo winter; pitch 0, bearing kept);
 * the relief put back after a setStyle; a reader's tilt entering 3-D in
 * Terrain and nowhere else; leaving for Conditions, offline and outside
 * Switzerland; a route being drawn; why the roundel is disabled; the z9
 * line once per attempt; and the basemap picker's rows disabled in 3-D.
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
function fakeMap() {
  const handlers = {};
  let sources = {};
  let layers = [{ id: 'relief', type: 'raster' }, { id: 'labels', type: 'symbol' }];
  let terrainSpec = null;
  const container = document.createElement('div');
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
  };
  return map;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

let env;

/**
 * Render the roundel and the picker's rows, set the page's globals, and
 * run the picker and then the runner, in the page's order.
 */
async function boot({ search = '?mode=terrain', mode = 'terrain', stored = null, online = true } = {}) {
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
      <span data-view-face="flat">3D</span><span data-view-face="3d">2D</span>
    </button>`;
  window.history.replaceState(null, '', `/map/${search}`);
  window.localStorage.clear();
  if (stored) window.localStorage.setItem('snowdesk.map.basemap', stored);
  Object.defineProperty(window, 'devicePixelRatio', { value: 3, configurable: true });
  const map = fakeMap();
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

describe('entering 3-D', () => {
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
    expect(toasts()).toEqual(['3-D uses the swisstopo winter map; yours comes back in 2D.']);
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

describe('leaving 3-D', () => {
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
    button.click(); // back to 3-D before it lands
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
  it('is put back on the next styledata while in 3-D, and not when flat', async () => {
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

  it('holds the flat map flat outside Terrain and below z9', async () => {
    const { map } = await boot({ mode: 'dated', search: '' });
    expect(map.maxPitch).toBe(0);
    env.mode = 'terrain';
    map.zoom = 7;
    document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode: 'terrain' } }));
    expect(map.maxPitch).toBe(0);
    map.zoom = 10;
    map.fire('moveend');
    expect(map.maxPitch).toBe(core3d.MAX_PITCH);
  });
});

describe('what lays 3-D flat', () => {
  it('a change to Conditions, at once and with a line', async () => {
    const { map, button } = await boot();
    button.click();
    await flush();
    env.mode = 'dated';
    document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode: 'dated' } }));
    expect(window.pwaMapView.get()).toBe('flat');
    expect(map.getTerrain()).toBeNull();
    expect(map.pitch).toBe(0);
    expect(map.maxPitch).toBe(0);
    expect(toasts().at(-1)).toBe('Back in 2D: 3-D is part of Terrain.');
  });

  it('going offline', async () => {
    const { button } = await boot();
    button.click();
    env.online = false;
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed', { detail: { online: false } }));
    expect(window.pwaMapView.get()).toBe('flat');
    expect(toasts().at(-1)).toBe('Back in 2D: 3-D needs a connection.');
  });

  it('a pan out of Switzerland', async () => {
    const { map, button } = await boot();
    button.click();
    map.centre = { ...PARIS };
    map.fire('moveend');
    expect(window.pwaMapView.get()).toBe('flat');
    expect(view()).toBeNull();
    expect(toasts().at(-1)).toBe('Back in 2D: 3-D covers Switzerland for now.');
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

  it('lays the map flat at the end when 3-D can no longer be drawn', async () => {
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
    ['zoomed out', { zoom: 7 }, {}, 'Zoom in to see 3-D'],
    ['outside Switzerland', { centre: PARIS }, {}, '3-D covers Switzerland for now'],
    ['offline', {}, { online: false }, '3-D needs a connection'],
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
  it('says where 3-D stops once per attempt, not once per wheel tick', async () => {
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
    expect(toasts()).toEqual(['3-D stops here. Switch to 2D to zoom out.']);
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

describe('the basemap picker in 3-D', () => {
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
