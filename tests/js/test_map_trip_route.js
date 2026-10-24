/*
 * tests/js/test_map_trip_route.js — the trip a `?trip=` arrival draws on
 * the map, and what a tap on it opens (SNOW-1213).
 *
 * SNOW-828 drew the trip as a flat line nothing could tap, so the route
 * panel and its point card were out of reach for anyone who followed a
 * trip to the map. Now the line is the shared route drawing under the
 * trip's own prefix, and a tap opens the panel read-only. Three things
 * here fail silently if they regress:
 *
 *   - THE DRAWING. The trip's layers are the shared drawing's, kept out
 *     of the routes overlay (no `routes-*` ids, no overlay switch).
 *   - THE TAP. The trip's line layers are in the tap set, and a tap opens
 *     the panel on the trip's whole feature with no uuid — which is what
 *     keeps the panel's "…" menu and terrain fetch, both owner-only, off.
 *   - THE POINT. The first tap places the point where it landed, as on a
 *     saved route, though the routes overlay is off.
 *
 * Booting map.js in jsdom follows tests/js/test_map_route_leg_layers.js's
 * pattern — see its header.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** The trips endpoint's answer: the route, sampled and legged, and the meeting point. */
const TRIP_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.0, 46.0, 1500], [7.0, 46.005, 2100], [7.0, 46.01, 1900]],
      },
      properties: {
        kind: 'route',
        name: 'Saturday tour',
        page_url: '/trips/abc/',
        bounds: [7.0, 46.0, 7.0, 46.01],
        distance_m: 1100,
        ascent_m: 600,
        descent_m: 200,
        slope: { points: [[7.0, 46.0], [7.0, 46.005], [7.0, 46.01]], angles: [12, 34] },
        legs: [
          { i: 1, from: 0, to: 0, climbing: true, point_from: 0, point_to: 1 },
          { i: 2, from: 1, to: 1, climbing: false, point_from: 1, point_to: 2 },
        ],
      },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.0, 46.0] },
      properties: { kind: 'meeting' },
    },
  ],
};

const layers = new Map();
const sources = new Map();
let queryAnswer = () => [];
let projectLngLat = () => ({ x: 0, y: 0 });
let mapStub;
let rail;

/**
 * MapLibre stub that records what map.js builds.
 *
 * @returns {object}
 */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
      if (typeof layerOrHandler === 'function') {
        (handlers[event] ||= []).push(layerOrHandler);
      } else if (typeof maybeHandler === 'function') {
        (handlers[`${event}:${layerOrHandler}`] ||= []).push(maybeHandler);
      }
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    panBy: () => {},
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
    getZoom: () => 12,
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: (lngLat) => projectLngLat(lngLat),
    unproject: () => ({ lng: 7, lat: 46 }),
    queryRenderedFeatures: (point, options) => queryAnswer(options),
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

/**
 * Fire the map's click dispatcher with one layer answering the hit test.
 *
 * @param {string} layerId
 * @param {object} properties
 * @param {{x: number, y: number}} [point]
 */
function tapLayer(layerId, properties, point = { x: 10, y: 10 }) {
  queryAnswer = (options) => (
    (options.layers || []).includes(layerId) ? [{ layer: { id: layerId }, properties }] : []
  );
  for (const handler of mapStub.handlers.click || []) {
    handler({ point, lngLat: { lng: 7, lat: 46.005 } });
  }
  queryAnswer = () => [];
}

beforeAll(async () => {
  localStorage.clear();
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return original ? original.call(this, type) : null;
    return {
      fillStyle: '#000000',
      fillRect: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from([0, 0, 0, 255]) }),
    };
  };
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-community-reports-url="/api/community-reports.geojson"
         data-trip-route-url-template="/trips/__UUID__/route.geojson"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  history.replaceState(null, '', '/map/?trip=0b6c6a3e-4f43-4a0e-9a54-5d7d2f6b1c11');
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(String(url).includes('/trips/') ? TRIP_FC : EMPTY_FC),
    })),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/route_slope_core.js');
  await import('../../static/js/route_legs_core.js');
  await import('../../static/js/route_cursor_core.js');
  await import('../../static/js/route_cursor_map_core.js');
  await import('../../static/js/trip_deeplink_core.js');
  await import('../../static/js/map_sheet.js');
  rail = installRouteRailStub();
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  // The arrival fetches without being awaited; let it settle.
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe('the trip an arrival draws', () => {
  it('is the shared route drawing, under the trip\'s own prefix', () => {
    for (const id of [
      'trip-route-leg-climb',
      'trip-route-leg-descent',
      'trip-route-slope-line',
      'trip-route-transitions',
      'trip-route-endpoints',
      'trip-route-meeting',
    ]) {
      expect(layers.has(id)).toBe(true);
    }
    expect(sources.get('trip-route-legs').data.features).toHaveLength(2);
  });

  it('stays out of the routes overlay', () => {
    expect([...layers.keys()].some((id) => id.startsWith('routes-'))).toBe(false);
  });

  it('keeps the meeting point in a source of its own', () => {
    expect(sources.get('trip-meeting').data.features.map((f) => f.properties.kind))
      .toEqual(['meeting']);
    expect(sources.get('trip-route').data.features.map((f) => f.properties.kind))
      .toEqual(['route']);
  });
});

describe('a tap on the trip\'s line', () => {
  it('opens the route panel on the whole trip feature, with no uuid', () => {
    rail.reset();

    tapLayer('trip-route-leg-descent', { i: 2, climbing: false });

    const call = rail.last();
    expect(call.feature.properties.name).toBe('Saturday tour');
    expect(call.feature.properties.legs).toHaveLength(2);
    // No uuid is what keeps the panel's owner-only menu and terrain off.
    expect(call.feature.properties).not.toHaveProperty('uuid');
  });

  it('places the point where the tap landed, with the routes overlay off', () => {
    projectLngLat = ([, lat]) => ({ x: 0, y: (46.01 - lat) * 10000 });
    rail.reset();
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(2);
    rail.state.cursor = cursor;

    // y = 70 is nearest the first segment's middle (y = 75); the second's is 25.
    tapLayer('trip-route-leg-climb', { i: 1, climbing: true }, { x: 0, y: 70 });

    expect(rail.state.calls).toHaveLength(1);
    expect(cursor.state().index).toBe(0);

    // A second tap on the open trip moves the point without reopening.
    tapLayer('trip-route-leg-descent', { i: 2, climbing: false }, { x: 0, y: 30 });

    expect(rail.state.calls).toHaveLength(1);
    expect(cursor.state().index).toBe(1);
  });
});
