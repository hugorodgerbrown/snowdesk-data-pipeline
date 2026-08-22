/*
 * tests/js/test_map_route_tap.js — can you actually tap a saved route?
 *
 * For its whole life the answer was no. `routes-line` was in
 * MARKER_EXCLUSION_LAYERS and `activateRoute` was fully written, but
 * `markerUnderPoint` hit-tested with an EXACT POINT — correct for a pin,
 * whose glyph is ~18px across, and hopeless for a line the layer draws
 * 1.5px wide at z6 and 4px at z12. The tap missed, fell through to
 * `regions-fill`, and selected the region the track crosses. The popup
 * existed and never opened.
 *
 * So these tests are about the hit test, not the popup's contents (those
 * are tests/js/test_elevation_profile_core.js's job). The stub below
 * models a thin line honestly: it answers an exact-point query the way a
 * 1.5px line really would — with nothing — and only reports the route
 * when the query geometry is a box that actually covers it.
 *
 * Booting map.js in jsdom follows tests/js/test_map_detail_popup_exclusivity.js's
 * pattern — see its header for the rationale.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** The screen row the route's line is drawn across, in pixels. */
const LINE_Y = 16;

/** The route the tap is meant to find. */
const ROUTE_FEATURE = {
  layer: { id: 'routes-line' },
  geometry: { type: 'LineString', coordinates: [[7.0, 46.0], [7.0, 46.03]] },
  properties: {
    uuid: '11111111-2222-3333-4444-555555555555',
    name: 'Haute Route',
    distance_m: 12400,
    ascent_m: 850,
    descent_m: 1100,
    bounds: JSON.stringify([7.0, 46.0, 7.0, 46.03]),
  },
};

/** A favourite pin, for the priority test. */
const FAVOURITE_FEATURE = {
  layer: { id: 'favourites-pin' },
  geometry: { type: 'Point', coordinates: [7.0, 46.01] },
  properties: { uuid: 'fav', name: 'Hut' },
};

/** Every DOM node handed to a popup, newest last. */
const popupNodes = [];

/** Layers the stub pretends are installed; a test may narrow this. */
let installedLayers = new Set(['routes-line', 'routes-line-casing']);

/** Whether a favourite pin sits under the exact tap point. */
let favouriteUnderPoint = false;

/**
 * Minimal MapLibre stub whose hit test behaves like a 1.5px line.
 *
 * `queryRenderedFeatures` answers honestly for both geometries MapLibre
 * accepts: a single point hits only what is exactly under it, and a
 * bounding box hits whatever it covers. That asymmetry IS the bug under
 * test — a stub that returned the route for any query would pass against
 * the broken code.
 *
 * @returns {object} The map stub.
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
    getLayer: (id) => (installedLayers.has(id) ? { id } : null),
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
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 7, lat: 46 }),
    queryRenderedFeatures: (geometry, options) => {
      const layers = (options && options.layers) || [];
      const wants = (id) => layers.includes(id);

      // A bounding box: [[x1, y1], [x2, y2]].
      if (Array.isArray(geometry)) {
        const [[, y1], [, y2]] = geometry;
        const covers = Math.min(y1, y2) <= LINE_Y && LINE_Y <= Math.max(y1, y2);
        return wants('routes-line') && covers ? [ROUTE_FEATURE] : [];
      }

      // An exact point. A 1.5px line is not under it unless the tap landed
      // on the very row the line occupies — which is the case the old code
      // needed and almost never got.
      const hits = [];
      if (favouriteUnderPoint && wants('favourites-pin')) hits.push(FAVOURITE_FEATURE);
      if (wants('routes-line') && geometry.y === LINE_Y) hits.push(ROUTE_FEATURE);
      return hits;
    },
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        setHTML: () => popup,
        setDOMContent: (node) => { popupNodes.push(node); return popup; },
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

/** The DOM map.js's boot reads. */
function buildFixture() {
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
}

let mapStub;

/**
 * Tap the map at a screen position, and return the popup it opened.
 *
 * @param {number} y The tap's y in pixels.
 * @returns {HTMLElement|undefined} The popup's node, if one opened.
 */
function tapAt(y) {
  const before = popupNodes.length;
  for (const handler of mapStub.handlers.click || []) {
    handler({
      point: { x: 10, y },
      lngLat: { lng: 7.0, lat: 46.01 },
      originalEvent: { target: document.body },
    });
  }
  return popupNodes.length > before ? popupNodes.at(-1) : undefined;
}

beforeAll(async () => {
  localStorage.clear();
  buildFixture();
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
  await import('../../static/js/elevation_profile_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
});

beforeEach(() => {
  installedLayers = new Set(['routes-line', 'routes-line-casing']);
  favouriteUnderPoint = false;
});

describe('tapping a saved route', () => {
  it('opens the route popup from a tap NEAR the line, not exactly on it', () => {
    // 6px above the line: a normal, accurate tap by any human standard, and
    // a dead miss for an exact-point hit test. This is the regression.
    const node = tapAt(LINE_Y - 6);

    expect(node).toBeDefined();
    expect(node.getAttribute('data-route-detail')).toBe('');
    expect(node.textContent).toContain('Haute Route');
  });

  it('shows the route the tap found, with both vertical figures', () => {
    const node = tapAt(LINE_Y - 6);

    expect(node.textContent).toContain('12.4 km');
    expect(node.textContent).toContain('850 m ascent');
    expect(node.textContent).toContain('1100 m descent');
  });

  it('still opens for a tap exactly on the line', () => {
    expect(tapAt(LINE_Y)).toBeDefined();
  });

  it('does not claim a tap well away from any route', () => {
    // Far outside the tolerance: this tap belongs to the region underneath,
    // and the slop must not have turned the whole map into a route target.
    expect(tapAt(LINE_Y + 60)).toBeUndefined();
  });

  it('yields to a favourite pin sitting on the route', () => {
    // The priority MARKER_EXCLUSION_LAYERS encodes: a pin under the tap wins
    // outright, and the line's wider tolerance must not overturn that. The
    // tap here is exactly ON the line, so both layers genuinely have a hit
    // and the priority is what decides.
    installedLayers.add('favourites-pin');
    favouriteUnderPoint = true;

    // No route popup opens. The favourite's own popup needs favourites.js to
    // fill its container (see activateFavourite), and this suite does not
    // load it — so "nothing mounted" is exactly the favourite winning.
    expect(tapAt(LINE_Y)).toBeUndefined();
  });

  it('claims the tap again once the pin is gone', () => {
    // The other half of the pair: without the favourite the same tap is the
    // route's, so the test above is measuring priority rather than a tap
    // that never reached the route at all.
    installedLayers.add('favourites-pin');
    favouriteUnderPoint = false;

    expect(tapAt(LINE_Y)?.getAttribute('data-route-detail')).toBe('');
  });

  it('does nothing when the routes layer is not installed', () => {
    // The overlay is lazy-installed and defaults off; querying a layer that
    // does not exist throws in MapLibre, so it must be filtered out first.
    installedLayers = new Set();

    expect(() => tapAt(LINE_Y)).not.toThrow();
    expect(tapAt(LINE_Y)).toBeUndefined();
  });
});
