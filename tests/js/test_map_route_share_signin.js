/*
 * tests/js/test_map_route_share_signin.js — a SIGNED-OUT reader of a route
 * share link is sent to sign in and back to the map as it is when they tap
 * (SNOW-903).
 *
 * The harness is tests/js/test_map_route_share.js's, booted with
 * data-routes-upload-eligible="false": map.js reads that attribute once at
 * boot, so the signed-out viewer needs its own module instance — this file.
 * See that file's header for the MapLibre stub's hit-test model.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import '../../static/js/share.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

const SHARE_TOKEN = 'tok123abc';

/** The routes feed: one owned route and one pending share.
 *
 * The pending feature carries `token` and `pending` and NO `uuid` — the
 * server's contract (apps/routes/views.py), because a non-owner must never
 * be handed the identifier the owner-scoped endpoints are addressed by.
 */
const ROUTES_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.0, 46.0, 1500], [7.0, 46.03, 1800]],
      },
      properties: { uuid: '11111111-2222-3333-4444-555555555555', name: 'Mine' },
    },
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.2, 46.0, 1200], [7.2, 46.02, 1900], [7.2, 46.04, 1400]],
      },
      properties: {
        token: SHARE_TOKEN,
        pending: true,
        name: 'Col de Balme',
        distance_m: 8200,
        ascent_m: 700,
        descent_m: 500,
        duration_s: 14400,
        bounds: [7.2, 46.0, 7.2, 46.04],
      },
    },
  ],
};

/** The screen row the pending line is drawn across, in pixels. */
const LINE_Y = 16;

/** The pending route as a tapped (tile-clipped) feature. */
const PENDING_FEATURE = {
  layer: { id: 'routes-line' },
  geometry: { type: 'LineString', coordinates: [[7.2, 46.0], [7.2, 46.04]] },
  properties: {
    token: SHARE_TOKEN,
    pending: true,
    name: 'Col de Balme',
    distance_m: 8200,
    ascent_m: 700,
    descent_m: 500,
    duration_s: 14400,
    bounds: JSON.stringify([7.2, 46.0, 7.2, 46.04]),
  },
};

/** Every addLayer spec map.js asked for, by id. */
const addedLayers = {};

/** Layers the stub pretends are installed. */
let installedLayers = new Set();

/**
 * Minimal MapLibre stub. See test_map_route_tap.js for the hit-test model.
 *
 * @returns {object} The map stub.
 */
function stubMapLibre() {
  const handlers = {};
  const sources = {};
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
      if (typeof layerOrHandler === 'function') {
        (handlers[event] ||= []).push(layerOrHandler);
      } else if (typeof maybeHandler === 'function') {
        (handlers[`${event}:${layerOrHandler}`] ||= []).push(maybeHandler);
      }
    },
    once: () => {},
    off: (event, handler) => {
      const list = handlers[event] || [];
      const at = list.indexOf(handler);
      if (at !== -1) list.splice(at, 1);
    },
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (installedLayers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources[id] || null,
    addSource: (id, spec) => { sources[id] = Object.assign({ setData: () => {} }, spec); },
    addLayer: (spec) => {
      addedLayers[spec.id] = spec;
      installedLayers.add(spec.id);
    },
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
    fitBounds: vi.fn(),
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
      if (!layers.includes('routes-line')) return [];
      if (Array.isArray(geometry)) {
        const [[, y1], [, y2]] = geometry;
        const covers = Math.min(y1, y2) <= LINE_Y && LINE_Y <= Math.max(y1, y2);
        return covers ? [PENDING_FEATURE] : [];
      }
      return geometry.y === LINE_Y ? [PENDING_FEATURE] : [];
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
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-routes-upload-eligible="false"
         data-routes-signin-url="/account/sign-in/"
         data-route-claim-url-template="/routes/partials/share/__TOKEN__/claim/"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <form hidden><input type="hidden" name="csrfmiddlewaretoken" value="tok"></form>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

let mapStub;
let rail;

/** The fits made while the share link was being honoured at boot. */
let arrivalFits = [];

/**
 * Tap the map at a screen position, and return what it handed the rail.
 *
 * The rail's record is cleared first, so what comes back belongs to THIS
 * tap.
 *
 * @param {number} y The tap's y in pixels.
 * @returns {{feature: object, options: object}|undefined} The rail's
 *   open call, if the tap opened it.
 */
function tapAt(y) {
  rail.reset();
  for (const handler of mapStub.handlers.click || []) {
    handler({
      point: { x: 10, y },
      lngLat: { lng: 7.2, lat: 46.02 },
      originalEvent: { target: document.body },
    });
  }
  return rail.last() || undefined;
}

/**
 * Tap the pending line and return the Save control handed to the rail.
 *
 * @returns {HTMLElement} The claim control.
 */
function claimControl() {
  return tapAt(LINE_Y).options.claim;
}

beforeAll(async () => {
  localStorage.clear();
  buildFixture();
  // The deep link is consumed inside the `load` handler, so the parameter
  // has to be in the address bar before the bundle boots.
  window.history.replaceState(null, '', `/?route_share=${SHARE_TOKEN}&keep=1`);
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
      text: () => Promise.resolve('<li>claimed</li>'),
    })),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/elevation_profile_core.js');
  // The sheet controller, before the bundle, as the page loads it.
  await import('../../static/js/map_sheet.js');
  rail = installRouteRailStub();
  // Real boxes for #map and the panel, so the arrival's fit can measure
  // the room the panel takes (jsdom lays nothing out).
  document.getElementById('map').getBoundingClientRect = () => ({
    top: 0, left: 0, width: 1600, height: 900, right: 1600, bottom: 900,
  });
  rail.element.getBoundingClientRect = () => ({
    top: 60, left: 12, width: 400, height: 192, right: 412, bottom: 252,
  });
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  // Populates routesGeojsonCache and installs the line layers.
  await window.pwaRoutesOverlay.show();
  // The share link waits for the routes source to load; the stub's source
  // loads silently, so say so the way MapLibre would.
  // The boot's own load of the overlay (the share link switches it on)
  // is still settling its fetch.
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (const handler of [...(mapStub.handlers.sourcedata || [])]) {
    handler({ sourceId: 'routes' });
  }
  arrivalFits = mapStub.fitBounds.mock.calls.slice();
  delete document.getElementById('map').getBoundingClientRect;
  delete rail.element.getBoundingClientRect;
});

describe("the signed-out reader's Save link (SNOW-903)", () => {
  it('is a sign-in link that returns to the map url as it is when tapped', () => {
    const link = /** @type {HTMLAnchorElement} */ (claimControl());
    expect(link.tagName).toBe('A');
    window.history.replaceState(null, '', '/map/?d=2026-01-07&keep=1');
    link.addEventListener('click', (event) => event.preventDefault());

    link.click();

    const href = new URL(link.getAttribute('href'), window.location.origin);
    expect(href.pathname).toBe('/account/sign-in/');
    expect(href.searchParams.get('next')).toBe('/map/?d=2026-01-07&keep=1');
  });
});
