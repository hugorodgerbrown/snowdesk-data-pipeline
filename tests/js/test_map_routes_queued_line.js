/*
 * tests/js/test_map_routes_queued_line.js — the plain line a route drawn
 * offline draws on the map while it waits to sync (SNOW-1262, map.js's
 * refreshQueuedRoutes and routesSourceData).
 *
 * The routes source is the server's routes:geojson plus, merged in, one
 * flat LineString per route-create row still in window.pwaMutationQueue,
 * flagged `queued` — never written to the offline cache or to
 * routesGeojsonCache, because the server has not got it. Three things:
 *
 *   1. offline with nothing cached, enabling the overlay still installs the
 *      layer so the queued line is drawn;
 *   2. the line is the row's own points, flagged `queued`, with no legs;
 *   3. a queue change re-reads the rows: a replayed or refused save's line
 *      comes off, and a refused one is never drawn.
 *
 * Booted like test_map_routes_overlay_bridge.js — see its header.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const POINTS = [[7.5, 46.1], [7.52, 46.12]];

let queued = [
  {
    idempotency_key: 'k-1',
    kind: 'route-create',
    status: 'queued',
    body: JSON.stringify({ points: POINTS, name: 'Mont Fort' }),
  },
];

/**
 * Minimal MapLibre stub tracking per-layer `layout`, the thing this suite
 * reads back. Lifted from test_map_favourites_overlay_bridge.js; see its
 * note on `moveLayer` being load-bearing rather than filler.
 */
function stubMapLibre() {
  const handlers = {};
  const layouts = new Map();
  const sources = new Map();
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
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => {
      const source = { ...def, setData: (data) => { source.data = data; } };
      sources.set(id, source);
    },
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => layouts.delete(id),
    removeSource: (id) => sources.delete(id),
    // Load-bearing: installRoutesLayer raises its line layers above the
    // choropleth on every install, and a stub without this throws INSIDE
    // the lazy-load promise — which map.js swallows, so the overlay
    // silently never becomes visible and every assertion here would fail
    // for the wrong reason.
    moveLayer: () => {},
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

/** The DOM map.js's boot reads, with the routes surface eligible. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="true"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

let mapStub;

/** The features the routes source was last given. */
function routeFeatures() {
  const source = mapStub.getSource('routes');
  return source ? (source.data || {}).features || [] : [];
}

beforeAll(async () => {
  localStorage.clear();
  buildFixture();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
    configurable: true,
  });
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  // Offline: the routes feed cannot be read, and nothing is cached.
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      if (String(url).includes('routes')) return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ type: 'FeatureCollection', features: [] }) });
    }),
  );
  window.pwaMutationQueue = { list: vi.fn(async () => queued) };

  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/route_slope_core.js');
  await import('../../static/js/route_legs_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
  delete window.pwaMutationQueue;
});

describe('a route waiting to sync', () => {
  it('is drawn as its plain line even offline with nothing cached', async () => {
    window.pwaRoutesOverlay.show();

    expect(await waitFor(() => routeFeatures().length === 1)).toBe(true);
    const [feature] = routeFeatures();
    expect(feature.geometry).toEqual({ type: 'LineString', coordinates: POINTS });
    expect(feature.properties).toMatchObject({ queued: true, queued_key: 'k-1', name: 'Mont Fort' });
    expect(feature.properties.legs).toBeUndefined();
  });

  it('comes off once the queue no longer holds it', async () => {
    queued = [];

    document.dispatchEvent(new CustomEvent('pwa:mutation-queue-changed'));

    expect(await waitFor(() => routeFeatures().length === 0)).toBe(true);
  });

  it('is never drawn once the server has refused it', async () => {
    queued = [
      {
        idempotency_key: 'k-2',
        kind: 'route-create',
        status: 'failed',
        body: JSON.stringify({ points: POINTS, name: 'Refused' }),
      },
    ];

    document.dispatchEvent(new CustomEvent('pwa:mutation-queue-changed'));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(routeFeatures()).toEqual([]);
  });
});
