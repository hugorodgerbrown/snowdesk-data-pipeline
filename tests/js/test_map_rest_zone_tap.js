/*
 * tests/js/test_map_rest_zone_tap.js — where a tap on a wildlife rest zone
 * sits in map.js's click dispatcher (SNOW-1384).
 *
 * The order is markers > weather > tours > an open route > resort pin >
 * rest zone > region. A zone is a whole hillside under everything else the
 * map draws, so each thing over it must keep its tap, and a tap the zone
 * does take must not select the region under it as well.
 *
 * map.js is booted as tests/js/test_map_detail_popup_exclusivity.js boots
 * it, against a MapLibre stub whose hit-test answers per layer. The zone
 * module is a stub (window.pwaRestZones.tap, recording) — the module is
 * covered on its own in tests/js/test_rest_zones.js — and so are the tours
 * (window.pwaSkiTours.tap) and the place sheet (window.pwaPlaceSheet).
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

const ROUTE = {
  layer: { id: 'routes-line' },
  geometry: { type: 'LineString', coordinates: [[7.5, 46.1, 1500], [7.54, 46.14, 2100]] },
  properties: { uuid: 'r-1', name: 'Rosablanche', bounds: [7.5, 46.1, 7.54, 46.14] },
};
const RESORT = {
  layer: { id: 'resorts-pin' },
  geometry: { type: 'Point', coordinates: [7.52, 46.12] },
  properties: { id: 'verbier', name: 'Verbier' },
};
const REGION = {
  layer: { id: 'regions-fill' },
  id: 'CH-4224',
  geometry: { type: 'Polygon', coordinates: [] },
  properties: { region_id: 'CH-4224', name: 'Verbier' },
};

/** What the next tap lands on, every layer at once. */
let hitFeatures = [];

/** A MapLibre stub whose hit-test answers only for the layers asked about. */
function stubMapLibre() {
  const handlers = {};
  const present = new Set(['routes-line', 'resorts-pin', 'regions-fill']);
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
    getLayer: (id) => (present.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: () => {},
    moveLayer: () => {},
    setLayoutProperty: () => {},
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
    queryRenderedFeatures: (_point, options) => {
      const layers = options && options.layers;
      return layers ? hitFeatures.filter((f) => layers.includes(f.layer.id)) : hitFeatures;
    },
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

/** Tap the map once, on whatever `hitFeatures` holds. */
function tapTheMap(mapStub) {
  for (const handler of mapStub.handlers.click || []) {
    handler({
      point: { x: 10, y: 10 },
      lngLat: { lng: 7.52, lat: 46.12 },
      originalEvent: { target: document.body },
    });
  }
}

let mapStub;
let rail;
let zoneTakes = true;
/** Every snowdesk:region-selected the dispatcher announced, this test. */
let regionEvents = [];
document.addEventListener('snowdesk:region-selected', (event) => {
  regionEvents.push(event.detail);
});

beforeAll(async () => {
  localStorage.clear();
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="true"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-ski-routes-eligible="true"
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
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );
  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/map_sheet.js');
  rail = installRouteRailStub();
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await import('../../static/js/rest_zones_core.js');
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
  delete window.pwaRestZones;
  delete window.pwaSkiTours;
  delete window.pwaPlaceSheet;
});

beforeEach(() => {
  rail.reset();
  regionEvents = [];
  zoneTakes = true;
  window.pwaRestZones = { tap: vi.fn(() => zoneTakes) };
  window.pwaSkiTours = { tap: vi.fn(() => false) };
  window.pwaPlaceSheet = { open: vi.fn(() => true), close: vi.fn(), isOpen: () => false };
});

describe('a tap on a wildlife rest zone', () => {
  it('leaves a route over the zone to open the route', () => {
    hitFeatures = [ROUTE];
    tapTheMap(mapStub);
    expect(rail.last().feature.properties.uuid).toBe('r-1');
    expect(window.pwaRestZones.tap).not.toHaveBeenCalled();
  });

  it('leaves a tour over the zone to open the tour', () => {
    window.pwaSkiTours.tap = vi.fn(() => true);
    hitFeatures = [];
    tapTheMap(mapStub);
    expect(window.pwaSkiTours.tap).toHaveBeenCalled();
    expect(window.pwaRestZones.tap).not.toHaveBeenCalled();
  });

  it('leaves a resort pin over the zone to open the resort', () => {
    hitFeatures = [RESORT, REGION];
    tapTheMap(mapStub);
    expect(window.pwaPlaceSheet.open).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'resort', id: 'verbier' }),
    );
    expect(window.pwaRestZones.tap).not.toHaveBeenCalled();
  });

  it('opens the zone, and selects no region under it', () => {
    hitFeatures = [REGION];
    tapTheMap(mapStub);
    expect(window.pwaRestZones.tap).toHaveBeenCalledWith({ x: 10, y: 10 });
    // The dispatcher returned: no selection moved and no sheet was closed
    // for one.
    expect(regionEvents).toEqual([]);
    expect(window.pwaPlaceSheet.close).not.toHaveBeenCalled();
  });

  it('opens a zone outside every region', () => {
    hitFeatures = [];
    tapTheMap(mapStub);
    expect(window.pwaRestZones.tap).toHaveBeenCalled();
  });

  it('falls through when the tap is not inside a zone', () => {
    zoneTakes = false;
    hitFeatures = [];
    tapTheMap(mapStub);
    expect(window.pwaRestZones.tap).toHaveBeenCalled();
    // The empty-map tap's deselect ran, as it does with no layer at all.
    expect(regionEvents).toEqual([{ region_id: null, region_name: null }]);
  });

  it('is not asked during playback, which changes no selection', () => {
    window.snowdeskMapState.isPlaying = true;
    try {
      hitFeatures = [];
      tapTheMap(mapStub);
      expect(window.pwaRestZones.tap).not.toHaveBeenCalled();
    } finally {
      window.snowdeskMapState.isPlaying = false;
    }
  });
});

describe('the zoom gate', () => {
  it('names the rest zones at the zoom the module draws them from', () => {
    expect(window.pwaZoomGates.list()).toContainEqual(
      { key: 'rest_zones', zoom: self.pwaRestZonesCore.MINZOOM, upTo: false },
    );
  });
});
