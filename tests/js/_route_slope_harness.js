/*
 * tests/js/_route_slope_harness.js — the shared jsdom harness for the three
 * route-slope refetch suites (SNOW-910).
 *
 * Each suite needs a DIFFERENT boot state, and `overlayLoaded` is private to
 * map.js with no way back once set — hiding an overlay does not unload it. So
 * "the overlay is on", "it was never loaded" and "its first load is still in
 * flight" cannot be three describes in one module; they are three modules,
 * and this is what they share.
 *
 * `boot()` takes the one thing that differs: whether to show the routes
 * overlay before the tests run.
 *
 * The fetch stub captures each response body WHEN THE REQUEST IS MADE and can
 * delay it per request. Both matter. Reading the payload lazily inside
 * `json()` gave every in-flight request the latest value, which let an
 * overlapping-writes test pass against the bug it was written to catch; and
 * without per-request delays two refreshes settle in one microtask flush and
 * interleave, which is not what two real round trips do.
 */

import { vi } from 'vitest';

import { loadMapBundle } from './_load_map_bundle.js';

export const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * A route the server has not sampled yet — no `slope` property at all.
 *
 * It still carries `legs` (SNOW-1017): a leg is a fact about the geometry,
 * so the server cuts an unsampled route too, and the map draws it as legs
 * either way.
 */
export const ROUTES_UNSAMPLED = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.5, 46.1, 1500], [7.54, 46.14, 2100]],
      },
      properties: {
        uuid: 'r-1',
        name: 'Rosablanche',
        bounds: [7.5, 46.1, 7.54, 46.14],
        legs: [{ i: 1, from: 0, to: 1, climbing: true, point_from: 0, point_to: 1 }],
      },
    },
  ],
};

/** The same route once the worker has stored its record. */
export const ROUTES_SAMPLED = {
  type: 'FeatureCollection',
  features: [
    {
      ...ROUTES_UNSAMPLED.features[0],
      properties: {
        ...ROUTES_UNSAMPLED.features[0].properties,
        slope: {
          points: [[7.5, 46.1], [7.52, 46.12], [7.54, 46.14]],
          angles: [22.0, 38.0],
        },
      },
    },
  ],
};

/** What the routes feed answers next. Swapped between tests. */

/** MapLibre stub — only what map.js's boot and the routes layer touch. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const sources = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => ((layers.get(id) || {}).layout || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => { sources.set(id, { ...def, setData: vi.fn() }); },
    addLayer: (def) => { layers.set(def.id, def); },
    removeLayer: (id) => layers.delete(id),
    removeSource: (id) => sources.delete(id),
    moveLayer: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layer = layers.get(id);
      if (layer) (layer.layout ||= {})[prop] = value;
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
    hasImage: () => true,
    addImage: () => {},
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 7, lat: 46 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
    layers,
    sources,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        setHTML: () => popup,
        setDOMContent: () => popup,
        setLngLat: () => popup,
        addTo: () => popup,
        getElement: () => document.createElement('div'),
        on: () => {},
        remove: () => {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** The narrowest 2D-canvas double the route marker colours need. */
function stubCanvas2D() {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return original ? original.call(this, type) : null;
    return {
      fillStyle: '#000000',
      fillRect: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from([0, 0, 0, 255]) }),
    };
  };
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-community-reports-url="/api/community-reports.geojson"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <section id="map-route-legs-section" hidden></section>`;
}




/** Mutable per-test state, reset by `resetHarnessState`. */
export const harness = {
  // Seeded here rather than only in `resetHarnessState`, which runs in
  // `beforeEach` — after `boot`. A null payload makes the overlay's own
  // load bail into its offline branch and never mark itself loaded, so
  // every later test sees an unloaded overlay and no fetches at all.
  /** The body the next routes.geojson request is answered with. */
  payload: ROUTES_UNSAMPLED,
  /** Per-request response delays in ms, consumed in call order. */
  delays: [],
  /** When true, every routes.geojson request REJECTS, as offline does. */
  offline: false,
  /** What the offline overlay cache hands back when a fetch rejects. */
  cached: null,
};

/** Restore the defaults every test starts from. */
export function resetHarnessState() {
  harness.payload = ROUTES_UNSAMPLED;
  harness.delays = [];
  harness.offline = false;
  harness.cached = null;
}

/** How many times the routes feed has been asked for. */
export function routesFetchCount() {
  return globalThis.fetch.mock.calls.filter(
    ([url]) => String(url).includes('routes.geojson'),
  ).length;
}

/** Announce a write, optionally as the upload or claim path does. */
export function announce(detail) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:routes-changed', { detail: detail || null }),
  );
}

/**
 * Boot map.js in jsdom.
 *
 * @param {{showRoutes?: boolean}} [options] `showRoutes` enables the routes
 *   overlay before the suite runs; leave it off to test an unloaded one.
 * @returns {Promise<object>} The MapLibre stub, for tests that poke sources.
 */
export async function boot(options) {
  localStorage.clear();
  stubCanvas2D();
  buildFixture();
  // The offline overlay cache map.js falls back to when a fetch rejects.
  // Without it a failed load installs nothing and never marks itself
  // loaded, which is a DIFFERENT path from the cache-served one.
  window.pwaMapOverlayCache = {
    putOverlay: async () => {},
    getOverlay: async () => harness.cached,
  };
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const isRoutes = String(url).includes('routes.geojson');
      // A rejected fetch, which is what map.js sees offline — and the
      // difference that matters here, because `refreshPanelOverlay` has no
      // catch of its own, so a rejection must not be mistaken for a read.
      if (isRoutes && harness.offline) return Promise.reject(new Error('offline'));
      const body = isRoutes ? harness.payload : EMPTY_FC;
      const delay = harness.delays.shift() || 0;
      return Promise.resolve({
        ok: true,
        json: () => (delay
          ? new Promise((resolve) => setTimeout(() => resolve(body), delay))
          : Promise.resolve(body)),
      });
    }),
  );

  const mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  await import('../../static/js/route_slope_core.js');
  await import('../../static/js/route_legs_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  if (options && options.showRoutes) await window.pwaRoutesOverlay.show();
  return mapStub;
}

/** Undo everything `boot` installed. */
export function teardown() {
  vi.unstubAllGlobals();
  delete window.pwaMapOverlayCache;
  localStorage.clear();
  delete globalThis.maplibregl;
}
