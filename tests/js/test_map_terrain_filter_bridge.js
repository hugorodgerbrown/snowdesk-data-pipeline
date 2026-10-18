/*
 * tests/js/test_map_terrain_filter_bridge.js — window.pwaTerrainFilter and
 * the layers menu's "Terrain filter" row (SNOW-978, SNOW-1109).
 *
 * The bridge is the filter's one owner of state: it persists the switch and
 * the criteria, and announces every change. Since SNOW-1109 the row is a
 * checkbox that toggles the filter through the bridge, and the adjust
 * button beside it opens the filter's sheet; the menu header counts the
 * filter as a layer while it is on.
 *
 * Harness follows test_map_layers_menu_anonymous_favourites.js. The MapLibre
 * stub has no addProtocol, so the layer itself is never installed — which
 * is the eligibility path the bundle's other suites take, and leaves the
 * bridge's own behaviour as the thing under test.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const SWITCH_KEY = 'snowdesk.map.overlay.terrain_filter';
const FILTER_KEY = 'snowdesk.map.terrain_filter';
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
          <button
            type="button"
            role="menuitem"
            class="basemap-menu-adjust"
            data-terrain-filter-open
            aria-label="Adjust terrain filter"
          ></button>
        </li>
      </ul>
    </div>`;
}

const row = () => document.querySelector('[data-overlay-key="terrain"]');
const adjust = () => document.querySelector('[data-terrain-filter-open]');
const count = () => document.querySelector('[data-layers-count]').textContent;

beforeAll(async () => {
  window.localStorage.removeItem(SWITCH_KEY);
  window.localStorage.setItem(FILTER_KEY, 'not json');
  buildFixture();
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
  loadMapBundle();
});

afterAll(() => {
  vi.unstubAllGlobals();
  window.localStorage.removeItem(SWITCH_KEY);
  window.localStorage.removeItem(FILTER_KEY);
  delete globalThis.maplibregl;
  delete window.pwaDb;
  delete window.pwaTerrainFilterSheet;
});

describe('window.pwaTerrainFilter', () => {
  it('opens off, with the default filter when storage holds garbage', () => {
    expect(window.pwaTerrainFilter.isEnabled()).toBe(false);
    expect(window.pwaTerrainFilter.getFilter()).toEqual({
      mode: 'match',
      aspects: [0, 1, 2, 3, 4, 5, 6, 7],
      minSlope: 30,
      maxSlope: null,
      minElevation: null,
      maxElevation: null,
    });
  });

  it('reports where the camera is: zoomed out to z8, it asks for a zoom-in', () => {
    expect(window.pwaTerrainFilter.availability()).toBe('zoom-in');
  });

  it('persists the switch and announces it', () => {
    const changed = vi.fn();
    const visibility = vi.fn();
    document.addEventListener('snowdesk:terrain-filter-changed', changed);
    document.addEventListener('snowdesk:overlay-visibility-changed', visibility);
    window.pwaTerrainFilter.show();
    expect(window.localStorage.getItem(SWITCH_KEY)).toBe('true');
    expect(window.pwaTerrainFilter.isEnabled()).toBe(true);
    expect(changed).toHaveBeenCalled();
    expect(changed.mock.calls.at(-1)[0].detail.enabled).toBe(true);
    expect(visibility).toHaveBeenCalled();
    document.removeEventListener('snowdesk:terrain-filter-changed', changed);
    document.removeEventListener('snowdesk:overlay-visibility-changed', visibility);
  });

  it('persists the filter, normalised', () => {
    window.pwaTerrainFilter.setFilter({ aspects: [7, 0], minSlope: 35, minElevation: 2449 });
    expect(JSON.parse(window.localStorage.getItem(FILTER_KEY))).toEqual({
      mode: 'match',
      aspects: [0, 7],
      minSlope: 35,
      maxSlope: null,
      minElevation: 2400,
      maxElevation: null,
    });
    expect(window.pwaTerrainFilter.getFilter().aspects).toEqual([0, 7]);
  });

  it('hands out a copy, so a caller cannot edit the live filter', () => {
    window.pwaTerrainFilter.getFilter().aspects.push(3);
    expect(window.pwaTerrainFilter.getFilter().aspects).toEqual([0, 7]);
  });
});

describe('the layers menu row', () => {
  it('counts the filter as a layer while it is on', () => {
    window.pwaTerrainFilter.show();
    expect(count()).toBe('1 layer on');
    window.pwaTerrainFilter.hide();
    expect(count()).toBe('No layers on');
    expect(window.localStorage.getItem(SWITCH_KEY)).toBe('false');
  });

  it('follows the bridge when something else turns the filter on', () => {
    window.pwaTerrainFilter.show();
    expect(row().getAttribute('aria-checked')).toBe('true');
    window.pwaTerrainFilter.hide();
    expect(row().getAttribute('aria-checked')).toBe('false');
  });

  it('toggles the filter through the bridge (SNOW-1109)', () => {
    row().click();
    expect(window.pwaTerrainFilter.isEnabled()).toBe(true);
    expect(row().getAttribute('aria-checked')).toBe('true');
    expect(window.localStorage.getItem(SWITCH_KEY)).toBe('true');
    expect(count()).toBe('1 layer on');
    row().click();
    expect(window.pwaTerrainFilter.isEnabled()).toBe(false);
    expect(row().getAttribute('aria-checked')).toBe('false');
  });

  it('leaves the sheet to the adjust button', () => {
    window.pwaTerrainFilterSheet = { open: vi.fn() };
    row().click();
    expect(window.pwaTerrainFilterSheet.open).not.toHaveBeenCalled();
    row().click();
    adjust().click();
    expect(window.pwaTerrainFilterSheet.open).toHaveBeenCalledTimes(1);
  });
});

describe('window.pwaZoomGates (SNOW-1077)', () => {
  /**
   * One gate from the registry, by key.
   *
   * @param {string} key The gate's key.
   * @returns {{key: string, zoom: number, active: boolean}}
   */
  const gate = (key) => window.pwaZoomGates.list().find((g) => g.key === key);

  it('lists the terrain filter at z12 and route slopes at z14', () => {
    expect(gate('terrain_filter').zoom).toBe(12);
    expect(gate('route_slopes').zoom).toBe(14);
  });

  it('turns the terrain-filter gate on with the filter', () => {
    window.pwaTerrainFilter.show();
    expect(gate('terrain_filter').active).toBe(true);
    window.pwaTerrainFilter.hide();
    expect(gate('terrain_filter').active).toBe(false);
  });

  it('leaves the route-slopes gate off with no routes drawn', () => {
    expect(gate('route_slopes').active).toBe(false);
  });

  it('names the z12 gate for the mode the filter is in (SNOW-1073)', () => {
    // The two gates share a zoom and a switch, so the scale lists exactly
    // one of them at z12, named for what the filter draws — and keeps it
    // listed, marked off, while the filter is off.
    const atTwelve = () => window.pwaZoomGates.list()
      .filter((g) => g.zoom === 12).map((g) => [g.key, g.active]);
    expect(atTwelve()).toEqual([['terrain_filter', false]]);
    window.pwaTerrainFilter.setFilter({ mode: 'slope' });
    window.pwaTerrainFilter.show();
    expect(atTwelve()).toEqual([['slope_classes', true]]);
    window.pwaTerrainFilter.setFilter({ mode: 'match' });
    expect(atTwelve()).toEqual([['terrain_filter', true]]);
    window.pwaTerrainFilter.hide();
    expect(atTwelve()).toEqual([['terrain_filter', false]]);
  });
});

describe('the slope mode (SNOW-1073)', () => {
  it('persists the mode and reads it back', () => {
    window.pwaTerrainFilter.setFilter({ mode: 'slope', minSlope: 35 });
    expect(JSON.parse(window.localStorage.getItem(FILTER_KEY))).toMatchObject({ mode: 'slope', minSlope: 35 });
    expect(window.pwaTerrainFilter.getFilter().mode).toBe('slope');
    window.pwaTerrainFilter.setFilter({});
    expect(window.pwaTerrainFilter.getFilter().mode).toBe('match');
  });
});
