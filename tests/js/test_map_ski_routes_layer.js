/*
 * tests/js/test_map_ski_routes_layer.js — the ski-touring layer's switches
 * (SNOW-1285): its two parts, Tours and Summits, each a row on the layers
 * menu's "SAC ski touring" page under a master switch, and the zoom control's
 * gate.
 *
 * ski_tours.js draws both parts; map.js keeps their switches
 * (window.pwaSkiRoutes) and shows or hides the layers OVERLAY_LAYERS names
 * for each. Both are ON by default, and only on a page that carries
 * data-ski-routes-eligible="true" — the route_drawing flag, for now.
 * swisstopo's raster, which this file tested before, is no longer drawn.
 * Each suite boots its own bundle, following test_map_layer_defaults.js,
 * because the boot seed reads localStorage once.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };
// The tours keep the key the raster's switch had.
const TOURS_KEY = 'snowdesk.map.overlay.ski_routes';
const SUMMITS_KEY = 'snowdesk.map.overlay.ski_summits';
const MASTER_KEY = 'snowdesk.map.ski-touring.last-on';
const TOUR_LAYERS = ['ski-tours-line', 'ski-tours-line-down'];
const SUMMIT_LAYERS = ['ski-tours-summit', 'ski-tours-summit-label'];

/** Minimal MapLibre stub tracking per-layer layout and paint. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
  const defs = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layouts.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: (id, prop) => (layers.get(id) || {})[prop],
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      defs.set(def.id, def);
      layers.set(def.id, { ...(def.paint || {}) });
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => {
      layers.delete(id);
      layouts.delete(id);
    },
    removeSource: () => {},
    moveLayer: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: (id, prop, value) => {
      const paint = layers.get(id) || {};
      paint[prop] = value;
      layers.set(id, paint);
    },
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    // A real setStyle DROPS every source and layer the app added — which is
    // the entire reason the `styledata` handler re-seeds and re-installs. A
    // no-op stub here would leave `regions-fill` in place, `installRegions-
    // Layers` would take its "fully installed already" early return, and the
    // re-seed's effect would never reach a layer to be asserted on. That is
    // exactly how a literal in that block survives a green suite.
    setStyle: () => {
      layers.clear();
      layouts.clear();
    },
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
    defs,
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

/**
 * The DOM map.js's boot reads, with the ski-touring master and its page's
 * two rows when the page is eligible — _map_embed.html renders them only
 * then.
 *
 * @param {boolean} eligible
 */
function buildFixture(eligible) {
  const row = eligible
    ? `<li role="none">
         <button type="button" role="menuitemcheckbox" data-layer-master="ski_touring"
                 data-layer-switch aria-checked="false"></button>
       </li>
       <li role="none">
         <ul id="basemap-menu-group-ski_touring">
           <li role="none">
             <button type="button" role="menuitemcheckbox"
                     class="basemap-menu-item basemap-menu-item--overlay"
                     data-overlay-key="ski_tours" aria-checked="false">Tours</button>
           </li>
           <li role="none">
             <button type="button" role="menuitemcheckbox"
                     class="basemap-menu-item basemap-menu-item--overlay"
                     data-overlay-key="ski_summits" aria-checked="false">Summits</button>
           </li>
         </ul>
       </li>`
    : '';
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-ski-routes-eligible="${eligible ? 'true' : 'false'}"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div id="basemap-pill" data-state="collapsed">
        <button id="basemap-toggle" aria-expanded="false"></button>
        <div id="basemap-menu" hidden>
          <p data-layers-count></p>
          <ul role="menu">
            <li role="none">
              <button class="basemap-menu-item"
                      data-basemap-key="openfreemap_liberty"
                      data-basemap-url="https://tiles.example.invalid/liberty.json"
                      aria-checked="true">OpenFreeMap</button>
            </li>
            ${row}
          </ul>
        </div>
      </div>
    </div>`;
}

let mapStub;

/**
 * Evaluate a fresh bundle against a page and a device.
 *
 * @param {{eligible?: boolean, storage?: Object<string, string>}} options
 * @returns {Promise<void>}
 */
async function boot({ eligible = true, storage = {} } = {}) {
  localStorage.clear();
  for (const [key, value] of Object.entries(storage)) localStorage.setItem(key, value);
  buildFixture(eligible);
  mapStub = stubMapLibre();
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom, and the install hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
  // The layers ski_tours.js would have added, drawn.
  for (const id of [...TOUR_LAYERS, ...SUMMIT_LAYERS]) {
    mapStub.addLayer({ id, layout: { visibility: 'visible' } });
  }
}

/** Replace the style and fire styledata, as a basemap swap does. */
async function swapBasemap() {
  mapStub.setStyle();
  for (const handler of mapStub.handlers.styledata || []) await handler();
}

/** @param {Array<string>} ids @returns {Array<string>} Each layer's visibility. */
const visibilities = (ids) => ids.map((id) => mapStub.getLayoutProperty(id, 'visibility'));
const toursDrawn = () => visibilities(TOUR_LAYERS).every((v) => v === 'visible');
const toursHidden = () => visibilities(TOUR_LAYERS).every((v) => v === 'none');
const summitsDrawn = () => visibilities(SUMMIT_LAYERS).every((v) => v === 'visible');
const summitsHidden = () => visibilities(SUMMIT_LAYERS).every((v) => v === 'none');
const row = (key) => document.querySelector(`[data-overlay-key="${key}"]`);
const master = () => document.querySelector('[data-layer-master="ski_touring"]');
const gateKeys = () => window.pwaZoomGates.list().map((gate) => gate.key);

beforeAll(() => {
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
    configurable: true,
  });
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('on an eligible page', () => {
  it('switches both parts on for a first visit, and draws no raster', async () => {
    await boot();

    expect(row('ski_tours').getAttribute('aria-checked')).toBe('true');
    expect(row('ski_summits').getAttribute('aria-checked')).toBe('true');
    expect(master().getAttribute('aria-checked')).toBe('true');
    expect(window.pwaSkiRoutes.isShown()).toBe(true);
    // SNOW-1285: swisstopo's raster of the whole network is gone.
    expect(mapStub.defs.has('ski-routes')).toBe(false);
  });

  it("keeps the old switch's \"off\" for the tours, and not the summits", async () => {
    await boot({ storage: { [TOURS_KEY]: 'false' } });

    expect(row('ski_tours').getAttribute('aria-checked')).toBe('false');
    expect(row('ski_summits').getAttribute('aria-checked')).toBe('true');
    expect(window.pwaSkiRoutes.isPartShown('ski_tours')).toBe(false);
    expect(window.pwaSkiRoutes.isShown()).toBe(true);
  });

  it('switches each part by its own row, and remembers it', async () => {
    await boot();

    row('ski_tours').click();
    expect(toursHidden()).toBe(true);
    expect(summitsDrawn()).toBe(true);
    expect(localStorage.getItem(TOURS_KEY)).toBe('false');
    row('ski_summits').click();
    expect(summitsHidden()).toBe(true);
    expect(localStorage.getItem(SUMMITS_KEY)).toBe('false');
    expect(master().getAttribute('aria-checked')).toBe('false');
    row('ski_tours').click();
    expect(toursDrawn()).toBe(true);
    expect(master().getAttribute('aria-checked')).toBe('true');
  });

  it('switches off what is on with the master, and puts it back', async () => {
    await boot({ storage: { [SUMMITS_KEY]: 'false' } });

    master().click();
    expect(toursHidden()).toBe(true);
    expect(JSON.parse(localStorage.getItem(MASTER_KEY))).toEqual(['ski_tours']);
    master().click();
    expect(toursDrawn()).toBe(true);
    expect(summitsHidden()).toBe(true);
  });

  it('switches both on for "Find a route"', async () => {
    await boot({ storage: { [TOURS_KEY]: 'false', [SUMMITS_KEY]: 'false' } });

    window.pwaSkiRoutes.show();
    expect(toursDrawn()).toBe(true);
    expect(summitsDrawn()).toBe(true);
    expect(row('ski_tours').getAttribute('aria-checked')).toBe('true');
  });

  it('is named in the zoom control at z9', async () => {
    await boot();

    expect(window.pwaZoomGates.list()).toContainEqual(
      { key: 'ski_routes', zoom: 9, upTo: false },
    );
  });
});

describe('on an ineligible page', () => {
  it('shows nothing and names no gate', async () => {
    await boot({ eligible: false });

    expect(window.pwaSkiRoutes.isShown()).toBe(false);
    expect(gateKeys()).not.toContain('ski_routes');
    expect(window.pwaSkiRoutes.isEligible()).toBe(false);
  });
});
