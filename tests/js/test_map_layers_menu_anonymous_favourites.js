/*
 * tests/js/test_map_layers_menu_anonymous_favourites.js — the layers menu's
 * Favourites row does not claim to be on for a visitor who cannot load it
 * (SNOW-904 review).
 *
 * Favourites is the one overlay that defaults ON. For an anonymous visitor
 * the lazy-load path returns early without a `FAVOURITES_URL`, so nothing is
 * ever drawn — but `overlayState.favourites` still read `true` from its
 * default, the row seeded itself checked from it, and the menu header
 * counted it. The map showed no favourites while the menu said one layer was
 * on, and clicking the row only opened the sign-in sheet, leaving the wrong
 * tick behind.
 *
 * The fix gates the boot READ on eligibility rather than the render, so
 * `isEnabled()` is honest for the roundel rings too, and writes nothing —
 * a real preference survives signing out and back in.
 *
 * Harness follows test_map_layers_menu_downloads_row.js; see its header. The
 * difference here is `data-favourites-eligible="false"` on #map.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const STORAGE_KEY = 'snowdesk.map.overlay.favourites';

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
         data-favourites-eligible="false"
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
            data-overlay-key="favourites"
            aria-checked="false"
          >Favourites</button>
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
function favouritesRow() {
  return document.querySelector('[data-overlay-key="favourites"]');
}

beforeAll(async () => {
  buildFixture();
  // The state under test: nothing stored, so the ON-by-default read is
  // the one that would otherwise tick this row for a signed-out visitor.
  window.localStorage.removeItem(STORAGE_KEY);
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
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('the favourites row, signed out', () => {
  it('does not tick a layer the visitor cannot load', () => {
    // The default is ON, and nothing is stored — so an ungated read would
    // make both of these true over a map drawing no favourites at all.
    expect(window.pwaFavouritesOverlay.isEnabled()).toBe(false);
    expect(favouritesRow().getAttribute('aria-checked')).toBe('false');
  });

  it('leaves the stored preference alone', () => {
    // The gate is on the read, not on a write: a reader who had favourites
    // switched on, then signed out, must get them back on signing in.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
