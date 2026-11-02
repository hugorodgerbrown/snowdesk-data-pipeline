/*
 * tests/js/test_map_problem_terrain_flag.js — the problem-terrain layer
 * outside its waffle flag (SNOW-1374).
 *
 * While the layer's controls are redesigned, the server renders none of
 * them for a reader outside the problem_terrain flag and tells map.js so on
 * #map (data-problem-terrain-enabled="false"). What is pinned, because each
 * is the way this goes wrong:
 *   - a device that stored the layer switched on does not paint it, and the
 *     stored choice is kept rather than overwritten, so it comes back when
 *     the flag is turned on;
 *   - a stray show() through the bridge does nothing;
 *   - a filter left in the retired "By bulletin" colour stays on, in one
 *     tint, instead of being carried over to a layer that cannot be drawn.
 *
 * Harness copied from test_map_problem_terrain_bridge.js; only the flag
 * attribute and the stored state differ.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const FILTER_SWITCH_KEY = 'snowdesk.map.overlay.terrain_filter';
const FILTER_KEY = 'snowdesk.map.terrain_filter';
const SWITCH_KEY = 'snowdesk.map.overlay.problem_terrain';
const TILE_URL = 'https://tiles.example.invalid/terrain-class/v1/{z}/{x}/{y}.png';
const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub — the bridge needs only the camera.
 *
 * @returns {object} The stub map.
 */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, def);
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => { layers.delete(id); layouts.delete(id); },
    removeSource: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: () => {},
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    loaded: () => true,
    areTilesLoaded: () => true,
    listImages: () => [],
    hasImage: () => false,
    addImage: () => {},
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
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
  };
  return map;
}

/** The DOM map.js's boot reads, carrying the row under test. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-terrain-filter-eligible="true"
         data-problem-terrain-enabled="false"
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
            class="basemap-menu-item basemap-menu-item--overlay"
            data-overlay-key="problem_terrain"
            aria-checked="false"
          ><span data-row-label>Highlight problem terrain</span></button>
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
          ><span data-row-label>Terrain filter</span></button>
        </li>
      </ul>
    </div>`;
}

const row = () => document.querySelector('[data-overlay-key="problem_terrain"]');
const filterRow = () => document.querySelector('[data-overlay-key="terrain"]');
const announced = [];

beforeAll(async () => {
  // The layer stored on, and the filter left on in "By bulletin".
  window.localStorage.setItem(SWITCH_KEY, 'true');
  window.localStorage.setItem(FILTER_SWITCH_KEY, 'true');
  window.localStorage.setItem(
    FILTER_KEY,
    JSON.stringify({ mode: 'bulletin', aspects: [0, 1], minSlope: 35 }),
  );
  buildFixture();
  document.addEventListener('snowdesk:problem-terrain-changed', (e) => announced.push(e.detail));
  window.pwaDb = {
    get: vi.fn(async () => undefined),
    put: vi.fn(async () => {}),
    delete: vi.fn(async () => {}),
  };
  stubMapLibre();
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const body = String(url).includes('regions.geojson') ? REGIONS_GEOJSON : {};
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
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
});

afterAll(() => {
  vi.unstubAllGlobals();
  for (const key of [SWITCH_KEY, FILTER_SWITCH_KEY, FILTER_KEY]) window.localStorage.removeItem(key);
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('outside the problem_terrain flag', () => {
  it('does not draw a layer the device stored on, and keeps the choice', () => {
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(false);
    expect(window.localStorage.getItem(SWITCH_KEY)).toBe('true');
  });

  it('ignores a stray show()', () => {
    announced.length = 0;
    window.pwaBulletinTerrain.show();
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(false);
    expect(announced).toEqual([]);
  });

  it('leaves a "By bulletin" filter on, in one tint', () => {
    expect(window.pwaTerrainFilter.isEnabled()).toBe(true);
    expect(window.localStorage.getItem(FILTER_SWITCH_KEY)).toBe('true');
    expect(JSON.parse(window.localStorage.getItem(FILTER_KEY)).mode).toBe('match');
  });
});
