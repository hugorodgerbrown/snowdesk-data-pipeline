/*
 * tests/js/test_map_route_share.js — a shared route on the map (SNOW-764).
 *
 * Three things the map has to get right about a route somebody sent you,
 * none of which needs a browser:
 *
 *   THE LINE LOOKS DIFFERENT. A pending route is drawn by its own layer,
 *   dashed and in a different hue, and the two line layers carry
 *   complementary filters so one route is never drawn twice. The filters
 *   are the part worth pinning: an owned feature omits `pending` entirely,
 *   so `['==', ..., false]` would match nothing and every owned route
 *   would vanish.
 *
 *   THE POPUP OFFERS SAVE. The deep link lands on the map, so the popup is
 *   where the recipient meets the action — the routes panel is a thing
 *   they would have to know to open.
 *
 *   THE DEEP LINK IS CONSUMED. `?route_share=<token>` is stripped from the
 *   address bar on arrival, for the same reason `?favourite=` is: the
 *   collection is refetched on every routes change, and a parameter left
 *   in the URL is a standing instruction to fly back.
 *
 * Booting map.js in jsdom follows tests/js/test_map_route_tap.js's pattern —
 * see its header for the rationale, and for why the hit-test stub answers a
 * point query and a box query differently.
 *
 * The viewer here is SIGNED IN (data-routes-upload-eligible="true"), which
 * is the state with an action in it. map.js reads that attribute once at
 * boot, so the signed-out variant would need a second module instance.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import '../../static/js/share.js';
import { loadMapBundle } from './_load_map_bundle.js';

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
  layer: { id: 'routes-line-pending' },
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

/** Every DOM node handed to a popup, newest last. */
const popupNodes = [];

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
      if (!layers.includes('routes-line-pending')) return [];
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
         data-routes-upload-eligible="true"
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
      lngLat: { lng: 7.2, lat: 46.02 },
      originalEvent: { target: document.body },
    });
  }
  return popupNodes.length > before ? popupNodes.at(-1) : undefined;
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
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  // Populates routesGeojsonCache and installs the three line layers.
  await window.pwaRoutesOverlay.show();
});

beforeEach(() => {
  window.pwaTelemetry = { emit: vi.fn() };
});

describe('the pending route layer', () => {
  it('is installed as a third line layer', () => {
    expect(addedLayers['routes-line-pending']).toBeDefined();
    expect(addedLayers['routes-line-pending'].source).toBe('routes');
  });

  it('draws it dashed, which is the colour-blind-safe half of the cue', () => {
    expect(addedLayers['routes-line-pending'].paint['line-dasharray']).toBeDefined();
    expect(addedLayers['routes-line'].paint['line-dasharray']).toBeUndefined();
  });

  it('draws it in a different colour from an owned route', () => {
    expect(addedLayers['routes-line-pending'].paint['line-color']).not.toBe(
      addedLayers['routes-line'].paint['line-color'],
    );
  });

  it('gives the two layers complementary filters', () => {
    // The bug this guards: an OWNED feature omits `pending` entirely, so
    // ['==', ['get','pending'], false] matches nothing and every owned
    // route disappears. The owned filter has to be a NOT-equal test.
    expect(addedLayers['routes-line'].filter).toEqual([
      '!=', ['get', 'pending'], true,
    ]);
    expect(addedLayers['routes-line-pending'].filter).toEqual([
      '==', ['get', 'pending'], true,
    ]);
  });

  it('adds it after the owned line, so a pending route draws on top', () => {
    const ids = Object.keys(addedLayers);
    expect(ids.indexOf('routes-line-pending')).toBeGreaterThan(
      ids.indexOf('routes-line'),
    );
  });
});

describe('the shared-route deep link', () => {
  it('strips the token from the address bar', () => {
    // Left in place it would be a standing instruction: the collection is
    // refetched on every routes change, and each refetch would fly the map
    // back to a route the user may have claimed an hour ago.
    expect(window.location.search).not.toContain('route_share');
  });

  it('leaves every other query parameter alone', () => {
    expect(window.location.search).toContain('keep=1');
  });
});

describe('the pending route popup', () => {
  it('opens on a tap and names the route', () => {
    const node = tapAt(LINE_Y);

    expect(node).toBeDefined();
    expect(node.textContent).toContain('Col de Balme');
  });

  it('does not repeat the qualifier above the control', () => {
    // The popup shipped with a "Shared with you" line above the Save
    // control and it said nothing the control did not: both its labels —
    // "Save route" and "Sign in to save this route" — already state that
    // this is somebody else's route being offered. The pending PANEL ROW
    // keeps its own prefix, because a row sits in a list beside owned ones
    // and has only its actions to tell them apart; a popup has no such
    // neighbour. Asserted as an absence so the line cannot creep back.
    expect(tapAt(LINE_Y).textContent).not.toContain('Shared with you');
  });

  it('offers Save', () => {
    const node = tapAt(LINE_Y);
    const button = node.querySelector('button');

    expect(button).not.toBeNull();
    expect(button.textContent).toContain('Save route');
  });

  it('draws the profile from the TOKEN-keyed cache entry', () => {
    // A pending feature carries no uuid, so a lookup keyed on uuid alone
    // would find nothing and silently draw no chart — and the tapped
    // feature's own geometry is the tile's clipped copy, which is why the
    // cache is the source in the first place. 1200–1900 is the full
    // track's range; the tapped copy has two points and no elevations.
    expect(tapAt(LINE_Y).textContent).toContain('1200–1900 m');
  });

  it('posts the claim to the token-templated endpoint', async () => {
    const button = tapAt(LINE_Y).querySelector('button');

    button.click();
    await vi.waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(
        `/routes/partials/share/${SHARE_TOKEN}/claim/`,
        expect.objectContaining({ method: 'POST' }),
      ),
    );
  });

  it('announces the change so the layer repaints', async () => {
    const heard = vi.fn();
    document.addEventListener('snowdesk:routes-changed', heard);
    const button = tapAt(LINE_Y).querySelector('button');

    button.click();
    await vi.waitFor(() => expect(heard).toHaveBeenCalled());

    document.removeEventListener('snowdesk:routes-changed', heard);
  });

  it('names the remedy when the claimer is at the route cap', async () => {
    // "That couldn't be saved" would send a user at the cap round the same
    // loop; the remedy is the only thing separating the two failures.
    globalThis.fetch.mockImplementationOnce(() =>
      Promise.resolve({ ok: false, status: 409 }),
    );
    const button = tapAt(LINE_Y).querySelector('button');

    button.click();
    await vi.waitFor(() => expect(button.textContent).toContain('limit'));

    expect(button.disabled).toBe(false);
  });

  it('re-enables the button after any failure', async () => {
    globalThis.fetch.mockImplementationOnce(() =>
      Promise.resolve({ ok: false, status: 500 }),
    );
    const button = tapAt(LINE_Y).querySelector('button');

    button.click();
    await vi.waitFor(() => expect(button.textContent).toContain("couldn't be saved"));

    expect(button.disabled).toBe(false);
  });
});
