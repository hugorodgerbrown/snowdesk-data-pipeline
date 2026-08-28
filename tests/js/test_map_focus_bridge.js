/*
 * tests/js/test_map_focus_bridge.js — window.pwaMapFocus (static/js/map.js).
 *
 * The camera the three UGC panels call when a row's name is pressed. It is
 * a bridge for the same reason the four overlay bridges are: `map` is a
 * `const` inside map.js's own IIFE, so a panel in another IIFE cannot reach
 * it by name at all. Boots the bundle in jsdom the way
 * test_map_favourites_overlay_bridge.js does — see its header for the
 * general rationale.
 *
 * Three things have to hold, and two of them are asymmetries that would
 * look like bugs to anyone who did not know they were chosen:
 *
 *   1. point() zooms IN only. A viewer already closer than the floor picked
 *      that scale; pulling them back out to frame a pin they asked to be
 *      taken to would undo their own zoom.
 *   2. bounds() has no such floor and no maxZoom above 10. A bbox names an
 *      extent, and a long route can only be seen whole by zooming out.
 *   3. Both refuse a malformed argument silently. The value comes off a DOM
 *      attribute, and a NaN reaches MapLibre as a camera it cannot compute.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** The zoom map.js frames a single point at — POINT_FOCUS_ZOOM. */
const POINT_FOCUS_ZOOM = 11;

/** Minimal MapLibre stub recording the camera calls this suite reads back. */
function stubMapLibre() {
  const handlers = {};
  const layouts = new Map();
  let zoom = 8;
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
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => layouts.set(def.id, { ...(def.layout || {}) }),
    removeLayer: (id) => layouts.delete(id),
    removeSource: () => {},
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
    fitBounds: vi.fn(),
    easeTo: () => {},
    flyTo: vi.fn(),
    getZoom: () => zoom,
    setTestZoom: (value) => { zoom = value; },
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

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

let mapStub;

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
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );

  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

beforeEach(() => {
  mapStub.flyTo.mockClear();
  mapStub.fitBounds.mockClear();
  mapStub.setTestZoom(8);
});

describe('window.pwaMapFocus', () => {
  it('is a frozen bridge, like every other map.js export', () => {
    expect(Object.isFrozen(window.pwaMapFocus)).toBe(true);
  });

  it('point() frames a pin at the readable floor', () => {
    window.pwaMapFocus.point(7.5, 46.1);

    expect(mapStub.flyTo).toHaveBeenCalledWith({
      center: [7.5, 46.1],
      zoom: POINT_FOCUS_ZOOM,
    });
  });

  it('point() does not pull a closer viewer back out', () => {
    mapStub.setTestZoom(14);

    window.pwaMapFocus.point(7.5, 46.1);

    expect(mapStub.flyTo.mock.calls[0][0].zoom).toBe(14);
  });

  it('bounds() fits a bbox in MapLibre corner order', () => {
    window.pwaMapFocus.bounds([7.1, 46.0, 7.3, 46.2]);

    const [corners, options] = mapStub.fitBounds.mock.calls[0];
    // GeoJSON bbox is [west, south, east, north]; MapLibre wants
    // [[west, south], [east, north]] — an axis slip here frames the wrong
    // rectangle rather than throwing.
    expect(corners).toEqual([[7.1, 46.0], [7.3, 46.2]]);
    expect(options.maxZoom).toBe(10);
  });

  it('bounds() zooms out where a point would not', () => {
    // A route is an extent, so framing it whole may mean going wider than
    // the viewer's current zoom — the asymmetry with point() above.
    mapStub.setTestZoom(15);

    window.pwaMapFocus.bounds([7.1, 46.0, 7.3, 46.2]);

    expect(mapStub.fitBounds).toHaveBeenCalledOnce();
    expect(mapStub.fitBounds.mock.calls[0][1].maxZoom).toBe(10);
  });

  it('refuses a malformed argument rather than handing MapLibre a NaN', () => {
    window.pwaMapFocus.point(NaN, 46.1);
    window.pwaMapFocus.point(undefined, undefined);
    window.pwaMapFocus.bounds([7.1, 46.0, 7.3]);
    window.pwaMapFocus.bounds([7.1, 46.0, 'east', 46.2]);
    window.pwaMapFocus.bounds(null);

    expect(mapStub.flyTo).not.toHaveBeenCalled();
    expect(mapStub.fitBounds).not.toHaveBeenCalled();
  });
});
