/*
 * tests/js/test_map_ski_routes_layer.js — swisstopo's ski-touring routes as
 * a map layer: the layers menu's "Ski-touring routes" row, the zoom
 * control's gate, and draw mode's hold.
 *
 * The layer is a raster installed eagerly by installSkiRoutesLayer, ON by
 * default from z10 (Hugo, 28 Oct 2026), and only on a page that carries
 * data-ski-routes-eligible="true" — the route_drawing flag, for now. Each
 * suite boots its own bundle, following test_map_layer_defaults.js,
 * because the boot seed reads localStorage once.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };
const SKI_KEY = 'snowdesk.map.overlay.ski_routes';
const LAYER = 'ski-routes';

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
 * The DOM map.js's boot reads, with the ski-touring routes row when the
 * page is eligible — the row _map_embed.html renders only then.
 *
 * @param {boolean} eligible
 */
function buildFixture(eligible) {
  const row = eligible
    ? `<li role="none">
         <button type="button" role="menuitemcheckbox"
                 class="basemap-menu-item basemap-menu-item--overlay"
                 data-overlay-key="ski_routes" aria-checked="true">Ski-touring routes</button>
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
}

/** Replace the style and fire styledata, as a basemap swap does. */
async function swapBasemap() {
  mapStub.setStyle();
  for (const handler of mapStub.handlers.styledata || []) await handler();
}

const visibility = () => mapStub.getLayoutProperty(LAYER, 'visibility');
const row = () => document.querySelector('[data-overlay-key="ski_routes"]');
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
  it('draws the routes on a first visit, from z10', async () => {
    await boot();

    expect(visibility()).toBe('visible');
    expect(mapStub.defs.get(LAYER).minzoom).toBe(10);
    expect(row().getAttribute('aria-checked')).toBe('true');
  });

  it('keeps a reader\'s "off", through a basemap swap', async () => {
    await boot({ storage: { [SKI_KEY]: 'false' } });

    expect(visibility()).toBe('none');
    expect(row().getAttribute('aria-checked')).toBe('false');
    await swapBasemap();
    expect(visibility()).toBe('none');
  });

  it('is switched by its layers-menu row, and remembers it', async () => {
    await boot();

    row().click();
    expect(visibility()).toBe('none');
    expect(localStorage.getItem(SKI_KEY)).toBe('false');
    row().click();
    expect(visibility()).toBe('visible');
  });

  it('is held on through draw mode, then handed back to the switch', async () => {
    await boot({ storage: { [SKI_KEY]: 'false' } });

    window.pwaSkiRoutes.hold(true);
    expect(visibility()).toBe('visible');
    window.pwaSkiRoutes.hold(false);
    expect(visibility()).toBe('none');
  });

  it('is named in the zoom control at z10', async () => {
    await boot();

    expect(window.pwaZoomGates.list()).toContainEqual(
      { key: 'ski_routes', zoom: 10, upTo: false },
    );
  });
});

describe('on an ineligible page', () => {
  it('installs nothing and names no gate', async () => {
    await boot({ eligible: false });

    expect(mapStub.getLayer(LAYER)).toBeNull();
    expect(gateKeys()).not.toContain('ski_routes');
    expect(window.pwaSkiRoutes.isEligible()).toBe(false);
  });
});
