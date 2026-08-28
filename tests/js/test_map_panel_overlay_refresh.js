/*
 * tests/js/test_map_panel_overlay_refresh.js — Vitest DOM test for the
 * panel-write → map-repaint path in static/js/map.js.
 *
 * Scenario: none — a layer's GeoJSON source is repainted from a document
 * event, which no manual test script can observe and no browser is needed
 * to prove.
 *
 * The three UGC overlays draw the user's own data, and the panel that
 * lists it is not the only surface it appears on. Deleting a route or a
 * field observation used to empty its row and leave the line or the flag
 * standing on the map until the page was reloaded; uploading a route
 * listed it and drew nothing. ``snowdesk:favourites-changed`` had solved
 * this for the third overlay years earlier, so the fix is that same shape
 * twice — ``snowdesk:routes-changed`` and ``snowdesk:reports-changed`` —
 * and this suite is what says the two new ones behave like the one that
 * already worked.
 *
 * Four things have to hold:
 *
 *   1. the announcement refetches and calls setData with the SERVER's new
 *      payload, so a deleted feature actually leaves the map;
 *   2. routes repaints BOTH of its sources — the lines and the derived
 *      start/finish points — because a route deleted from one and left in
 *      the other is a flag standing on nothing;
 *   3. the refreshed payload is written through to the offline overlay
 *      cache, or the deletion comes back the next time the map reads that
 *      key with no connection;
 *   4. an overlay the user has never enabled is left alone. It has no
 *      source to write to, and installing one here would paint an overlay
 *      nobody asked for — the next enable fetches the same URL anyway.
 *
 * Booting map.js in jsdom follows tests/js/test_map_routes_overlay_bridge.js's
 * pattern; see its header for the general rationale.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** One saved route — the state the map starts this suite in. */
const ROUTES_BEFORE = {
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
        distance_m: 12400.0,
        ascent_m: 850.0,
        bounds: [7.5, 46.1, 7.54, 46.14],
      },
    },
  ],
};

/** The same user after deleting it — what the server answers next. */
const ROUTES_AFTER = EMPTY_FC;

const REPORTS_BEFORE = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.5, 46.1] },
      properties: { observation_type: 'AVALANCHE', observed_at: new Date().toISOString() },
    },
  ],
};

const REPORTS_AFTER = EMPTY_FC;

/*
 * What each URL answers, swapped between tests. Keyed by the substring the
 * fetch stub matches on rather than by overlay name, so the mapping reads
 * the way the stub does.
 */
const responses = {
  routes: ROUTES_BEFORE,
  'community-reports': REPORTS_BEFORE,
};

/**
 * MapLibre stub whose sources record every setData call.
 *
 * That recording is the whole point of this suite: the bridge suites read
 * layer `layout` back, and a refresh changes no layout at all — only the
 * data behind layers that were already drawn.
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
      sources.set(id, { ...def, setData: vi.fn() });
    },
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => layouts.delete(id),
    removeSource: (id) => sources.delete(id),
    // Load-bearing — see test_map_routes_overlay_bridge.js's note: without
    // it installRoutesLayer throws inside a swallowed promise and the
    // overlay silently never installs.
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
    sources,
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

/** The DOM map.js's boot reads, with all three UGC surfaces eligible. */
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

/** The last payload written to one of the stub's sources, or undefined. */
function lastSetData(map, sourceId) {
  const source = map.sources.get(sourceId);
  if (!source || !source.setData.mock.calls.length) return undefined;
  return source.setData.mock.calls.at(-1)[0];
}

let mapStub;
let putOverlay;

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
    vi.fn((url) => {
      const href = String(url);
      let body = EMPTY_FC;
      for (const [needle, payload] of Object.entries(responses)) {
        if (href.includes(needle)) body = payload;
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  putOverlay = vi.fn();
  window.pwaMapOverlayCache = {
    putOverlay,
    getOverlay: async () => null,
  };

  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom, and installRegionsLayers hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete window.pwaMapOverlayCache;
  delete globalThis.maplibregl;
});

describe('a panel announces a write, so the map re-reads its layer', () => {
  it('leaves an overlay the user has never enabled alone', async () => {
    // Asserted FIRST, before either describe below arms its overlay: routes
    // default OFF, so at this point nothing has fetched or installed them.
    // The announcement must be inert — there is no source to write to, and
    // installing one here would draw an overlay nobody asked for. The next
    // enable fetches this same URL anyway.
    globalThis.fetch.mockClear();

    document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(mapStub.sources.has('routes')).toBe(false);
    expect(
      globalThis.fetch.mock.calls.some(([url]) => String(url).includes('routes')),
    ).toBe(false);
  });

  describe('routes', () => {
    beforeEach(async () => {
      responses.routes = ROUTES_BEFORE;
      window.pwaRoutesOverlay.show();
      await waitFor(() => window.pwaRoutesOverlay.isVisible());
      putOverlay.mockClear();
      mapStub.sources.get('routes')?.setData.mockClear();
      mapStub.sources.get('route-endpoints')?.setData.mockClear();
    });

    it('repaints the lines from the server, dropping a deleted route', async () => {
      responses.routes = ROUTES_AFTER;

      document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));

      await waitFor(() => lastSetData(mapStub, 'routes') !== undefined);
      expect(lastSetData(mapStub, 'routes').features).toEqual([]);
    });

    it('repaints the endpoint markers too, not only the lines', async () => {
      // Two sources, one payload. A refresh that wrote only the lines would
      // leave a deleted route's start dot and finish flag on the map — the
      // half-fixed version of the bug this path exists for.
      responses.routes = ROUTES_AFTER;

      document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));

      await waitFor(() => lastSetData(mapStub, 'route-endpoints') !== undefined);
      expect(lastSetData(mapStub, 'route-endpoints').features).toEqual([]);
    });

    it('writes the fresh payload through to the offline overlay cache', async () => {
      responses.routes = ROUTES_AFTER;

      document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));

      await waitFor(() => putOverlay.mock.calls.length > 0);
      expect(putOverlay).toHaveBeenCalledWith('routes', ROUTES_AFTER);
    });
  });

  describe('community reports', () => {
    beforeEach(async () => {
      responses['community-reports'] = REPORTS_BEFORE;
      window.pwaCommunityReportsOverlay.show();
      await waitFor(() => window.pwaCommunityReportsOverlay.isVisible());
      putOverlay.mockClear();
      mapStub.sources.get('community-reports')?.setData.mockClear();
    });

    it('repaints from the server, dropping a deleted report', async () => {
      responses['community-reports'] = REPORTS_AFTER;

      document.dispatchEvent(new CustomEvent('snowdesk:reports-changed'));

      await waitFor(() => lastSetData(mapStub, 'community-reports') !== undefined);
      expect(lastSetData(mapStub, 'community-reports').features).toEqual([]);
    });

    it('caches the pristine payload, not the age-faded copy it draws', async () => {
      // The drawn collection carries the age-opacity property this map adds
      // client-side; the cached one must be the server's, so an offline
      // read-back re-derives the fade against the time it is READ.
      document.dispatchEvent(new CustomEvent('snowdesk:reports-changed'));

      await waitFor(() => putOverlay.mock.calls.length > 0);
      expect(putOverlay).toHaveBeenCalledWith('community_reports', REPORTS_BEFORE);
    });
  });

});
