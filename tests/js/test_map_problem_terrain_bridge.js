/*
 * tests/js/test_map_problem_terrain_bridge.js — window.pwaBulletinTerrain
 * and the layers menu's "Highlight problem terrain" row (2026-10-23).
 *
 * Problem terrain was the terrain filter's third colour, "By bulletin"
 * (SNOW-1194), until Hugo found it hidden there; it is now a row of its own
 * under Conditions with its own stored switch. What is pinned, because each
 * is the way this goes wrong:
 *   - a device that left the filter on in "By bulletin" comes back with
 *     problem terrain on and the filter off, its own settings kept, and the
 *     carry-over runs once;
 *   - the row toggles the layer through the bridge, which persists it and
 *     announces snowdesk:problem-terrain-changed;
 *   - the layer and the terrain filter are independent switches.
 *
 * Harness follows test_map_terrain_filter_bridge.js. The MapLibre stub has
 * no addProtocol, so neither layer is installed and the bridges' own
 * behaviour is what is under test.
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
  // The state SNOW-1194 left behind: the filter switched on, in "By bulletin".
  window.localStorage.removeItem(SWITCH_KEY);
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

describe('carrying "By bulletin" over to its own row', () => {
  it('turns problem terrain on and the filter off', () => {
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(true);
    expect(window.pwaTerrainFilter.isEnabled()).toBe(false);
    expect(window.localStorage.getItem(SWITCH_KEY)).toBe('true');
    expect(window.localStorage.getItem(FILTER_SWITCH_KEY)).toBe('false');
    expect(row().getAttribute('aria-checked')).toBe('true');
    expect(filterRow().getAttribute('aria-checked')).toBe('false');
  });

  it('keeps the reader\'s own filter settings, in one tint, so it runs once', () => {
    expect(JSON.parse(window.localStorage.getItem(FILTER_KEY)).mode).toBe('match');
    expect(window.pwaTerrainFilter.getFilter()).toMatchObject({
      mode: 'match', aspects: [0, 1], minSlope: 35,
    });
  });
});

describe('the row and the bridge', () => {
  it('switches the layer off and on, remembering it and announcing it', () => {
    announced.length = 0;
    row().click();
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(false);
    expect(window.localStorage.getItem(SWITCH_KEY)).toBe('false');
    expect(row().getAttribute('aria-checked')).toBe('false');
    expect(announced.at(-1)).toMatchObject({ enabled: false });
    window.pwaBulletinTerrain.show();
    expect(window.localStorage.getItem(SWITCH_KEY)).toBe('true');
    expect(row().getAttribute('aria-checked')).toBe('true');
    expect(announced.at(-1)).toMatchObject({ enabled: true });
  });

  it('is independent of the terrain filter', () => {
    window.pwaTerrainFilter.show();
    expect(window.pwaBulletinTerrain.isEnabled()).toBe(true);
    window.pwaBulletinTerrain.setEnabled(false);
    expect(window.pwaTerrainFilter.isEnabled()).toBe(true);
    window.pwaTerrainFilter.hide();
  });
});
