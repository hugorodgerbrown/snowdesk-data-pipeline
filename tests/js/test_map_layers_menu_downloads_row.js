/*
 * tests/js/test_map_layers_menu_downloads_row.js — the layers menu's
 * "Display downloaded areas" row seeds itself without writing anything
 * (SNOW-904).
 *
 * This is the SNOW-857 tri-state guard, and it is here rather than beside
 * the other layers-menu tests because it is `map.js`'s boot that seeds the
 * row, not the picker.
 *
 * `snowdesk.map.overlay.downloads` is the ONLY overlay key with three
 * states rather than two: `'true'` and `'false'` are the reader's own
 * answer, and ABSENT means untouched — which derives to "on while
 * offline", the state the overlay exists for. `map.js` reads it with
 * `readStorage`, never `readBoolStorage`, precisely so `null` and
 * `'false'` stay distinguishable.
 *
 * SNOW-904 gave that overlay a menu row again, and the easy way to ship
 * that is a seeding pass that writes each row's resolved state back. For
 * this row that would convert "untouched" into "explicitly off" on the
 * next page load and kill the auto-on for good — silently, and only for
 * readers who had never touched the control, which is exactly the group
 * it is for. So: seed the tick from `pwaDownloadedOverlay.isEnabled()`,
 * write on a real click and never otherwise.
 *
 * jsdom boot follows test_map_downloaded_overlay_boot.js's harness; see its
 * header. The difference here is that localStorage holds NOTHING for this
 * key, and `navigator.onLine` is false.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const STORAGE_KEY = 'snowdesk.map.overlay.downloads';

/** Empty regions collection — this suite only cares about the row. */
const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub. Narrower than the boot suite's: nothing here
 * asserts on paint, only on the row and on localStorage.
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
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div id="basemap-menu">
      <ul role="menu">
        <li role="none">
          <button
            type="button"
            role="menuitemcheckbox"
            class="basemap-menu-item basemap-menu-item--overlay"
            data-overlay-key="downloads"
            aria-checked="false"
          >Display downloaded areas</button>
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
      </ul>
    </div>`;
}

/** The row the whole file is about. */
function downloadsRow() {
  return document.querySelector('[data-overlay-key="downloads"]');
}

beforeAll(async () => {
  buildFixture();
  // The state under test: nothing stored for this key, ever.
  window.localStorage.removeItem(STORAGE_KEY);
  // Offline, which is what makes the untouched preference resolve to ON.
  Object.defineProperty(window.navigator, 'onLine', {
    value: false,
    configurable: true,
  });
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
  loadMapBundle();
  // MapLibre's `load` handler is deliberately NOT fired. The seed under
  // test runs at parse time, inside map.js's boot IIFE, and the only thing
  // `load` would add here is `installRegionsLayers` — which needs a canvas
  // 2D context jsdom does not ship, for a hatch image nothing below reads.
});

afterAll(() => {
  vi.unstubAllGlobals();
  window.localStorage.removeItem(STORAGE_KEY);
  Object.defineProperty(window.navigator, 'onLine', {
    value: true,
    configurable: true,
  });
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('the downloaded-areas row, untouched and offline', () => {
  it('seeds its tick from the bridge rather than from storage', () => {
    // Nothing has been stored, so "on while offline" is what isEnabled()
    // resolves to — and the row has to agree with it, or the map draws
    // squares under an unticked row.
    expect(window.pwaDownloadedOverlay.isEnabled()).toBe(true);
    expect(downloadsRow().getAttribute('aria-checked')).toBe('true');
  });

  it('writes nothing to localStorage at boot', () => {
    // The regression this file exists for. A seeding pass that persisted
    // what it read would turn "untouched" into "explicitly off" on the very
    // next load, and the offline auto-on would never fire again — for
    // precisely the readers who have never touched the control.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('writes only when the row is actually clicked', () => {
    window.pwaDownloadedOverlay.hide();

    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('false');
    expect(window.pwaDownloadedOverlay.isEnabled()).toBe(false);
  });
});
