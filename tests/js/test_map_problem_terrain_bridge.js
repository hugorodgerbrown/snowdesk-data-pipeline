/*
 * tests/js/test_map_problem_terrain_bridge.js — window.pwaBulletinTerrain
 * and the layers menu's "Sync with map terrain" row (SNOW-1377; it was
 * "Highlight problem terrain", its own row since 2026-10-23).
 *
 * SNOW-1377 made the layer on by default for everyone and gave Steep
 * terrain precedence over it. What is pinned, because each is the way this
 * goes wrong:
 *   - a device that never touched the switch gets the layer on; one that
 *     switched it off keeps it off;
 *   - while Steep terrain (the terrain filter) is on, the bulletin's raster
 *     is hidden and no region's `terrain` feature state is set, so the
 *     choropleth's edge band shows — WITHOUT the stored switch being
 *     rewritten, so turning Steep terrain off brings both back;
 *   - switching "Sync with map terrain" on turns Steep terrain off;
 *   - a device that left the filter on in the retired "By bulletin" colour
 *     comes back with problem terrain on and the filter off, its own
 *     settings kept, and the carry-over runs once.
 *
 * Each case boots the bundle afresh from its own stored state. The MapLibre
 * stub has addProtocol, a regions source and a z12 camera, so the layer is
 * installed and the day's one region is painted, and it records layouts and
 * feature state for the assertions.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const FILTER_SWITCH_KEY = 'snowdesk.map.overlay.terrain_filter';
const FILTER_KEY = 'snowdesk.map.terrain_filter';
const SWITCH_KEY = 'snowdesk.map.overlay.problem_terrain';
const TILE_URL = 'https://tiles.example.invalid/terrain-class/v1/{z}/{x}/{y}.png';
const DAY = '2026-03-15';
const RASTER = 'bulletin-terrain-raster';
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    properties: { id: 'CH-4115', name: 'CH-4115', covered: true },
    geometry: { type: 'Polygon', coordinates: [[[7, 46], [7, 47], [8, 47], [7, 46]]] },
  }],
};
// The day's one region, painted by a bulletin that describes no ground.
const PROBLEM_TERRAIN = { date: DAY, bulletins: { b1: [] }, regions: { 'CH-4115': 'b1' } };

/**
 * MapLibre stub recording layouts and feature state, with the sources the
 * layer reads and a camera at the tiles' zoom.
 *
 * @returns {object} The stub map.
 */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
  const sources = new Map();
  const featureState = new Map();
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    layerDef: (id) => layers.get(id) || null,
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: (target) => featureState.get(target.id) || {},
    isSourceLoaded: () => true,
    // Only the two sources this layer reads: every other overlay stays
    // uninstalled, as in the sibling bridge suites.
    getSource: (id) => sources.get(id) || null,
    addSource: (id) => {
      if (id === 'regions' || id === 'bulletin-terrain') {
        sources.set(id, { setData: () => {}, setTiles: () => {} });
      }
    },
    addLayer: (def) => {
      layers.set(def.id, def);
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => { layers.delete(id); layouts.delete(id); },
    removeSource: (id) => { sources.delete(id); },
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: () => {},
    setFilter: () => {},
    setFeatureState: (target, state) => {
      if (target.source !== 'regions') return;
      featureState.set(target.id, { ...(featureState.get(target.id) || {}), ...state });
    },
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    loaded: () => true,
    areTilesLoaded: () => true,
    listImages: () => [],
    hasImage: () => true,
    addImage: () => {},
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 12,
    getCenter: () => ({ lng: 7.5, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 7, getSouth: () => 46, getEast: () => 8, getNorth: () => 47,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 7.5, lat: 46.5 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      return { setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }), remove: () => {} };
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
    addProtocol: () => {},
  };
  return map;
}

/** The DOM map.js's boot reads, carrying the two rows under test. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings/"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-terrain-filter-eligible="true"
         data-problem-terrain-enabled="true"
         data-terrain-class-tile-url="${TILE_URL}"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div id="basemap-pill"><button id="basemap-toggle"></button></div>
    <div id="basemap-menu" hidden>
      <p data-layers-count></p>
      <ul role="menu">
        <li role="none">
          <button
            type="button"
            role="menuitemcheckbox"
            class="basemap-menu-item basemap-menu-item--overlay basemap-menu-layer-row group"
            data-overlay-key="problem_terrain"
            aria-checked="false"
          ><span data-row-label>Sync with map terrain</span></button>
        </li>
        <li role="none">
          <button
            type="button"
            class="basemap-menu-item"
            data-basemap-key="openfreemap_liberty"
            data-basemap-url="https://tiles.example.invalid/liberty.json"
            aria-checked="true"
          >OpenFreeMap</button>
        </li>
        <li role="none" class="basemap-menu-row">
          <button
            type="button"
            role="menuitemcheckbox"
            class="basemap-menu-item basemap-menu-item--overlay"
            data-overlay-key="terrain"
            aria-checked="false"
          ><span data-row-label>Steep terrain</span></button>
        </li>
      </ul>
    </div>`;
}

const row = () => document.querySelector('[data-overlay-key="problem_terrain"]');
const filterRow = () => document.querySelector('[data-overlay-key="terrain"]');
const settle = () => new Promise((r) => setTimeout(r, 50));

/**
 * Boot the bundle from `stored`, run the load handlers and let the day's
 * problem-terrain fetch land.
 *
 * @param {Object<string, string>} stored localStorage to start from.
 * @returns {Promise<object>} The stub map.
 */
async function boot(stored) {
  localStorage.clear();
  for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
  window.history.replaceState({}, '', `/?d=${DAY}`);
  buildFixture();
  window.pwaDb = {
    get: vi.fn(async () => undefined),
    put: vi.fn(async () => {}),
    delete: vi.fn(async () => {}),
  };
  const map = stubMapLibre();
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      let body = {};
      if (href.includes('regions.geojson')) {
        const country = (href.match(/[?&]country=([a-z]+)/) || [])[1];
        body = !country || country === 'ch' ? REGIONS_GEOJSON : { type: 'FeatureCollection', features: [] };
      } else if (href.startsWith('/api/problem-terrain/')) {
        body = PROBLEM_TERRAIN;
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    }),
  );
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/terrain_filter_core.js');
  await import('../../static/js/bulletin_terrain_core.js');
  loadMapBundle();
  for (const handler of map.handlers.load || []) await handler();
  await settle();
  return map;
}

/** Whether the day's region has its danger fill dropped for the slopes. */
const regionPainted = (map) => !!map.getFeatureState({ source: 'regions', id: 0 }).terrain;
const rasterVisibility = (map) => map.getLayoutProperty(RASTER, 'visibility');

/**
 * Count the writes to the stored switch from here on.
 *
 * @returns {Array<string>} The values written, appended as they happen.
 */
function watchSwitchWrites() {
  const writes = [];
  const original = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (key, value) {
    if (key === SWITCH_KEY) writes.push(value);
    return original.call(this, key, value);
  });
  return writes;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterAll(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.history.replaceState({}, '', '/');
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('on by default', () => {
  it('is on with nothing stored, and paints the day', async () => {
    const map = await boot({});
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(true);
    expect(row().getAttribute('aria-checked')).toBe('true');
    expect(rasterVisibility(map)).toBe('visible');
    expect(regionPainted(map)).toBe(true);
  });

  it('stays off on a device that switched it off', async () => {
    const map = await boot({ [SWITCH_KEY]: 'false' });
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(false);
    expect(row().getAttribute('aria-checked')).toBe('false');
    expect(rasterVisibility(map)).toBe('none');
    expect(regionPainted(map)).toBe(false);
  });
});

describe('Steep terrain takes precedence', () => {
  it('hides the slopes while it is on, and brings them back, never rewriting the switch', async () => {
    const map = await boot({});
    const writes = watchSwitchWrites();

    window.pwaTerrainFilter.show();
    expect(rasterVisibility(map)).toBe('none');
    expect(regionPainted(map)).toBe(false);
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(false);
    expect(row().getAttribute('aria-checked')).toBe('false');
    expect(localStorage.getItem(SWITCH_KEY)).toBe(null);

    window.pwaTerrainFilter.hide();
    expect(rasterVisibility(map)).toBe('visible');
    expect(regionPainted(map)).toBe(true);
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(true);
    expect(row().getAttribute('aria-checked')).toBe('true');
    expect(writes).toEqual([]);
  });

  it('opens with the slopes hidden on a device that left Steep terrain on', async () => {
    const map = await boot({ [FILTER_SWITCH_KEY]: 'true' });
    expect(window.pwaTerrainFilter.isEnabled()).toBe(true);
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(false);
    expect(row().getAttribute('aria-checked')).toBe('false');
    expect(rasterVisibility(map)).toBe('none');
    expect(regionPainted(map)).toBe(false);
  });

  it('turns Steep terrain off when "Sync with map terrain" is switched on', async () => {
    const map = await boot({ [FILTER_SWITCH_KEY]: 'true' });
    row().click();
    expect(window.pwaTerrainFilter.isEnabled()).toBe(false);
    expect(localStorage.getItem(FILTER_SWITCH_KEY)).toBe('false');
    expect(filterRow().getAttribute('aria-checked')).toBe('false');
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(true);
    expect(localStorage.getItem(SWITCH_KEY)).toBe('true');
    expect(row().getAttribute('aria-checked')).toBe('true');
    expect(rasterVisibility(map)).toBe('visible');
    // The day was never fetched while the slopes were hidden; it is now.
    await settle();
    expect(regionPainted(map)).toBe(true);
  });

  it('leaves Steep terrain alone when the layer is switched off', async () => {
    await boot({});
    window.pwaBulletinTerrain.setEnabled(false);
    expect(localStorage.getItem(SWITCH_KEY)).toBe('false');
    window.pwaTerrainFilter.show();
    expect(window.pwaTerrainFilter.isEnabled()).toBe(true);
    expect(localStorage.getItem(SWITCH_KEY)).toBe('false');
  });
});

describe('the zoom the slopes draw from', () => {
  // A 256 px raster source with minzoom 12 still draws from about z10.5:
  // MapLibre asks for z12 tiles one map zoom early. Only a LAYER minzoom
  // sets where each layer starts.
  it('draws Steep terrain from the zoom the scale names, z11', async () => {
    const map = await boot({});
    window.pwaTerrainFilter.show();
    const gate = window.pwaZoomGates.list().find((g) => g.key === 'terrain_filter');
    expect(gate.zoom).toBe(11);
    expect(map.layerDef('terrain-filter-raster').minzoom).toBe(gate.zoom);
  });

  it('keeps the problem-terrain slopes at z12, where the region fills drop out', async () => {
    const map = await boot({});
    expect(map.layerDef(RASTER).minzoom).toBe(12);
  });
});

describe('carrying "By bulletin" over to its own row', () => {
  it('turns problem terrain on and the filter off, keeping the filter\'s settings in one tint', async () => {
    // The state SNOW-1194 left behind: the filter switched on, in "By
    // bulletin", and the problem-terrain switch stored off.
    await boot({
      [SWITCH_KEY]: 'false',
      [FILTER_SWITCH_KEY]: 'true',
      [FILTER_KEY]: JSON.stringify({ mode: 'bulletin', aspects: [0, 1], minSlope: 35 }),
    });
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(true);
    expect(window.pwaTerrainFilter.isEnabled()).toBe(false);
    expect(localStorage.getItem(SWITCH_KEY)).toBe('true');
    expect(localStorage.getItem(FILTER_SWITCH_KEY)).toBe('false');
    expect(row().getAttribute('aria-checked')).toBe('true');
    expect(filterRow().getAttribute('aria-checked')).toBe('false');
    // Its own filter settings, in one tint, so the carry-over runs once.
    expect(JSON.parse(localStorage.getItem(FILTER_KEY)).mode).toBe('match');
    expect(window.pwaTerrainFilter.getFilter()).toMatchObject({
      mode: 'match', aspects: [0, 1], minSlope: 35,
    });
  });
});
