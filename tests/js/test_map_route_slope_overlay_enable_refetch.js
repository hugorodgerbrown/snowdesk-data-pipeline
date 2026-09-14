/*
 * tests/js/test_map_route_slope_overlay_enable_refetch.js — the delayed
 * re-read has to survive the routes overlay being OFF when the write
 * happened (SNOW-910).
 *
 * Scenario: none — a timer scheduled off an event payload and an overlay
 * enable, asserted by counting fetches. No browser is needed to prove it,
 * and no manual test script could observe it.
 *
 * `refreshPanelOverlay` is a no-op while an overlay has never been loaded,
 * so a claim or an upload made with the routes switch OFF leaves
 * `routesGeojsonCache` untouched: there is no payload to find unsampled,
 * and the write's own call to `scheduleSlopeRefetch` had nothing to arm
 * off. Enabling the overlay afterwards then drew the null-slope route and
 * scheduled nothing, leaving it flat for the rest of the session — which
 * is exactly the state the timer exists to prevent, reached by a different
 * door.
 *
 * This file is a SEPARATE module from test_map_route_slope_upload_refetch.js
 * because the two need opposite boots: that one shows the routes overlay in
 * `beforeAll`, and `overlayLoaded` is never reset by hiding it again (hidden
 * means nothing is on screen to be wrong, not that the payload was
 * discarded). The only way to test the unloaded path is to never load it.
 */

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** A route the server has not sampled yet — no `slope` property at all. */
const ROUTES_UNSAMPLED = {
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
      },
    },
  ],
};

/** The same route once the worker has stored its record. */
const ROUTES_SAMPLED = {
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
let routesPayload = ROUTES_UNSAMPLED;

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
    <section id="map-route-slope-section" hidden></section>`;
}

/** How many times the routes feed has been asked for. */
function routesFetchCount() {
  return globalThis.fetch.mock.calls.filter(
    ([url]) => String(url).includes('routes.geojson'),
  ).length;
}

/** Announce a write, optionally as the upload or claim path does. */
function announce(detail) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:routes-changed', { detail: detail || null }),
  );
}

let mapStub;

beforeAll(async () => {
  localStorage.clear();
  stubCanvas2D();
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(
        String(url).includes('routes.geojson') ? routesPayload : EMPTY_FC,
      ),
    })),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  await import('../../static/js/route_slope_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  // DELIBERATELY NOT SHOWN. That is the whole condition under test.
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

beforeEach(() => {
  routesPayload = ROUTES_UNSAMPLED;
  vi.useFakeTimers();
  globalThis.fetch.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('a claim made while the routes overlay is off', () => {
  it('re-reads the feed once the overlay is enabled', async () => {
    announce({ claimed: true });
    await vi.advanceTimersByTimeAsync(1);
    // The write's own refresh is a no-op: the overlay has never loaded, so
    // there is no cache to update and no fetch to make.
    expect(routesFetchCount()).toBe(0);

    // Nothing is armed off a payload nobody has read.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(0);

    // The user turns routes on. That fetch is the overlay's own load.
    await window.pwaRoutesOverlay.show();
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    // And the signal the write left standing is answered now there is a
    // payload to judge: the route is drawn flat, so ask again.
    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(2);

    // One shot, here as everywhere.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(2);
  });
});
