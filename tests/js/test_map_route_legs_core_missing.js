/*
 * tests/js/test_map_route_legs_core_missing.js — what a legged route looks
 * like when `route_legs_core.js` is not there (SNOW-1017, replacing
 * SNOW-910's slope-core version of the same guard).
 *
 * Scenario: none — a layer filter computed from a global that may not have
 * arrived. No browser is needed to prove it, and no manual test script
 * could arrange it.
 *
 * `routeLegsFor` guards on the core and falls back to an empty collection,
 * so a load failure costs the LEGS and not the overlay. That promise is
 * only kept if the flat line stays behind to be fallen back TO: the
 * exclusion that takes a legged route off `routes-line` and its casing has
 * to be conditional on the same global. Unconditional, it hides every
 * legged route from the one layer still capable of drawing it, and the
 * track renders as nothing at all — with no error anywhere to say why.
 *
 * The core is in home.html's DEFERRED call-site script group, which is why
 * the filter is computed at layer-install time rather than at parse time;
 * a module-level const would bake in "no core" on a page where the core is
 * a moment behind.
 *
 * The core-PRESENT half of this pair lives in
 * tests/js/test_map_route_leg_layers.js ("drop a legged route, so it is
 * not painted flat underneath"), which boots the bundle with the core
 * loaded. Keeping the two in separate files is deliberate: each boot binds
 * its own document listeners, so one suite cannot boot the bundle twice
 * and still count anything.
 *
 * Booting map.js in jsdom follows tests/js/test_map_route_leg_layers.js's
 * pattern; see its header for the general rationale.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** One legged route and one with no legs. */
const ROUTES_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.0, 46.0, 1500], [7.0, 46.015, 2100]],
      },
      properties: {
        uuid: 'sampled-route',
        name: 'Coloured',
        bounds: [7.0, 46.0, 7.0, 46.015],
        slope: {
          points: [[7.0, 46.0], [7.0, 46.005], [7.0, 46.01]],
          angles: [12.0, 41.0],
        },
        legs: [{ i: 1, from: 0, to: 1, climbing: true, point_from: 0, point_to: 1 }],
      },
    },
    {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[8.0, 47.0], [8.0, 47.02]] },
      properties: { uuid: 'flat-route', name: 'No legs' },
    },
  ],
};

/** Layer definitions as map.js added them, by id. */
const layers = new Map();
/** Source definitions as map.js added them, by id. */
const sources = new Map();

/** MapLibre stub — only what map.js's boot and the routes layer touch. */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: (id) => (layers.get(id) || {}).filter || null,
    getLayoutProperty: (id, prop) => ((layers.get(id) || {}).layout || {})[prop],
    getPaintProperty: (id, prop) => ((layers.get(id) || {}).paint || {})[prop],
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => {
      sources.set(id, { ...def, setData: (data) => { sources.get(id).data = data; } });
    },
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
        String(url).includes('routes.geojson') ? ROUTES_FC : EMPTY_FC,
      ),
    })),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  await import('../../static/js/route_slope_core.js');
  // route_legs_core.js is deliberately NOT imported — this whole suite is
  // the page it failed to load on.
  delete globalThis.pwaRouteLegsCore;
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  await window.pwaRoutesOverlay.show();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('the routes layer with no legs core', () => {
  it('still draws a legged route, flat, over its casing', () => {
    // Owned-only, and nothing about `legs`: with no core to paint the legs,
    // the flat fuchsia line is the only thing that can draw this track.
    expect(layers.get('routes-line').filter)
      .toEqual(['!=', ['get', 'pending'], true]);
    expect(layers.get('routes-line-casing').filter).toEqual([
      'any', ['==', ['get', 'pending'], true], ['!=', ['get', 'pending'], true],
    ]);
  });

  it('leaves the pending line alone, as it does with the core present', () => {
    expect(layers.get('routes-line-pending').filter)
      .toEqual(['==', ['get', 'pending'], true]);
  });

  it('paints empty leg, transition and passage sources rather than throwing', () => {
    // `setData` throws on a null, which is why the guarded fallback is a
    // collection with no features rather than nothing at all.
    expect(sources.get('route-legs').data).toEqual(EMPTY_FC);
    expect(sources.get('route-transitions').data).toEqual(EMPTY_FC);
    expect(sources.get('route-passages').data).toEqual(EMPTY_FC);
  });
});
