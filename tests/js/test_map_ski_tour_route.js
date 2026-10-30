/*
 * tests/js/test_map_ski_tour_route.js — the SAC tour the route panel has
 * open, drawn in map.js's tour slot (SNOW-1278), and taken off the map
 * when the panel closes (SNOW-1305).
 *
 * `map.isStyleLoaded()` is false whenever any source is still fetching
 * tiles. The tour slot waited on it before every update, so a panel closed
 * mid-load left the tour drawn with nothing left to clear it. Only the
 * first install may wait for the style; an update or an emptying may not.
 *
 * Booting map.js in jsdom follows tests/js/test_map_trip_route.js.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** routes:ski_tour_feature's answer, cut down: one leg, no terrain yet. */
const TOUR = {
  type: 'Feature',
  geometry: { type: 'LineString', coordinates: [[7.3, 45.97, null], [7.32, 45.99, null]] },
  properties: {
    tour: true,
    tour_uuid: 'c227bc2e-2078-4def-aaaa-61940547db35',
    name: 'Tournelon Blanc · Par le versant SE',
    bounds: [7.3, 45.97, 7.32, 45.99],
    distance_m: 2700,
    legs: [{ i: 1, from: 0, to: 0, climbing: false, point_from: 0, point_to: 1 }],
  },
};

const layers = new Map();
const sources = new Map();
let styleLoaded = true;
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
    isStyleLoaded: () => styleLoaded,
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

/** @returns {number} How many features the tour's line source holds. */
const drawn = () => sources.get('ski-tour-route').data.features.length;

/** Close the panel as its × does: the rail hides, then announces it. */
function closePanel() {
  window.pwaRouteRail.close();
  document.dispatchEvent(new CustomEvent('snowdesk:route-rail-changed', { detail: null }));
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
  history.replaceState(null, '', '/map/');
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(EMPTY_FC),
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
  await import('../../static/js/map_sheet.js');
  rail = installRouteRailStub();
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe('the open SAC tour', () => {
  it('waits for the style before its layers are first installed', () => {
    styleLoaded = false;
    window.pwaSkiTourRoute.open(TOUR);
    expect(sources.has('ski-tour-route')).toBe(false);
    closePanel();

    styleLoaded = true;
    window.pwaSkiTourRoute.open(TOUR);
    expect(drawn()).toBe(1);
    expect(sources.get('ski-tour-route-legs').data.features).toHaveLength(1);
  });

  it('is taken off the map when the panel closes while tiles load', () => {
    styleLoaded = true;
    window.pwaSkiTourRoute.open(TOUR);
    expect(drawn()).toBe(1);

    styleLoaded = false;
    closePanel();

    expect(window.pwaSkiTourRoute.openUuid()).toBeNull();
    expect(drawn()).toBe(0);
    expect(sources.get('ski-tour-route-legs').data.features).toHaveLength(0);
  });

  it('is drawn in SAC blue, the way up undashed (SNOW-1307)', () => {
    styleLoaded = true;
    window.pwaSkiTourRoute.open(TOUR);
    const legs = self.pwaRouteLegsCore;
    const climb = layers.get('ski-tour-route-leg-climb');

    expect(climb.paint['line-color']).toBe(legs.SAC_CLIMB_COLOUR);
    expect(climb.paint).not.toHaveProperty('line-dasharray');
    expect(layers.get('ski-tour-route-leg-descent').paint['line-color'])
      .toBe(legs.SAC_DESCENT_COLOUR);
    closePanel();
  });

  it('is redrawn while tiles load once its layers exist', () => {
    styleLoaded = false;
    window.pwaSkiTourRoute.open(TOUR);
    expect(drawn()).toBe(1);
    closePanel();
  });
});
