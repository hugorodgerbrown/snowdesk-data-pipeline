/*
 * tests/js/test_map_routes_overlay_bridge.js — Vitest DOM test for SNOW-687's
 * panel-driven overlay bridge, window.pwaRoutesOverlay (static/js/map.js).
 *
 * The fourth bridge of the shape SNOW-658 settled on, and the first added
 * since — which is the thing worth proving here. The routes layer was built
 * by joining the existing machinery rather than growing a parallel one, so
 * `showPanelOverlay`/`hidePanelOverlay` took no edit at all: this suite is
 * how we know that claim holds, because every assertion below is about
 * behaviour those two shared functions produce.
 * tests/js/test_map_favourites_overlay_bridge.js is the reference and this
 * file deliberately tracks it; where the two differ, the difference is noted.
 *
 * Four things have to hold:
 *
 *   1. show()/hide() persist the preference under the routes storage key, so
 *      an enabled overlay comes back at the next boot.
 *   2. show() reaches the lazy-load path and ends with both line layers
 *      drawn; hide() drops them.
 *   3. Both edges announce, so the roundel ring
 *      (static/js/map_roundel_overlay_state.js) repaints. The announcement is
 *      detail-free, so this suite counts events rather than reading them.
 *   4. isVisible() and isEnabled() answer DIFFERENT questions and are not
 *      confused (SNOW-658 review): the first is what MapLibre is drawing, the
 *      second is what the user asked for. They are allowed to disagree, and
 *      the case they disagree in is the one that matters — see the last test.
 *
 * Unlike the favourites bridge there is no resort-exclusion recompute to
 * check on each edge: a route is a line, not a pin standing in for a resort
 * dot, so `showPanelOverlay`'s only key-specific branch does not fire for
 * this key. That absence is asserted too — it is what "took no edit" means.
 *
 * Booting map.js in jsdom follows test_map_favourites_overlay_bridge.js's
 * pattern — see its header for the general rationale.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const ROUTES_KEY = 'snowdesk.map.overlay.routes';
const FAVOURITES_KEY = 'snowdesk.map.overlay.favourites';

/*
 * Both ids, in the order OVERLAY_LAYER_IDS_MAIN lists them — principal
 * layer first, because panelOverlayPainted() reads [0] to answer for the
 * whole group. That is deliberately the INVERSE of the addLayer order in
 * installRoutesLayer, where the casing goes down first so it paints
 * underneath. Asserting on both ids catches a group that half-flips.
 */
const ROUTE_LAYERS = ['routes-line', 'routes-line-casing', 'routes-endpoints'];

const ROUTES_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [7.5, 46.1, 1500],
          [7.52, 46.12, 1800],
          [7.54, 46.14, 2100],
        ],
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

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub tracking per-layer `layout`, the thing this suite
 * reads back. Lifted from test_map_favourites_overlay_bridge.js; see its
 * note on `moveLayer` being load-bearing rather than filler.
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
      sources.set(id, { ...def, setData: () => {} });
    },
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => layouts.delete(id),
    removeSource: (id) => sources.delete(id),
    // Load-bearing: installRoutesLayer raises its line layers above the
    // choropleth on every install, and a stub without this throws INSIDE
    // the lazy-load promise — which map.js swallows, so the overlay
    // silently never becomes visible and every assertion here would fail
    // for the wrong reason.
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

/** The DOM map.js's boot reads, with the routes surface eligible. */
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

/** `visibility` for every layer id, as one array. */
function visibilities(map, ids) {
  return ids.map((id) => map.getLayoutProperty(id, 'visibility'));
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
    vi.fn((url) => {
      const href = String(url);
      let body = EMPTY_FC;
      if (href.includes('routes')) body = ROUTES_GEOJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  // Assigned before the bundle boots — map.js constructs the map at parse
  // time, so the stub has to be in place first.
  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom, and installRegionsLayers hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('window.pwaRoutesOverlay', () => {
  it('defaults OFF — nothing is drawn until the user asks', () => {
    // Asserted before any show(), and before the beforeEach below arms the
    // rest of the suite. Routes follow community_reports/weather rather than
    // favourites (which defaults ON): a GPX track is visually far heavier
    // than a pin, and a first boot should not paint every saved route.
    expect(window.pwaRoutesOverlay.isEnabled()).toBe(false);
    expect(window.pwaRoutesOverlay.isVisible()).toBe(false);
    expect(localStorage.getItem(ROUTES_KEY)).toBe(null);
  });

  describe('once enabled', () => {
    beforeEach(async () => {
      window.pwaRoutesOverlay.show();
      await waitFor(() => window.pwaRoutesOverlay.isVisible());
    });

    it('is a frozen bridge, like every other map.js export', () => {
      expect(Object.isFrozen(window.pwaRoutesOverlay)).toBe(true);
    });

    it('show() installs both line layers and draws them', () => {
      expect(window.pwaRoutesOverlay.isVisible()).toBe(true);
      expect(visibilities(mapStub, ROUTE_LAYERS)).toEqual(['visible', 'visible', 'visible']);
    });

    it('hide() drops every routes layer and flips isVisible()', () => {
      window.pwaRoutesOverlay.hide();

      expect(window.pwaRoutesOverlay.isVisible()).toBe(false);
      expect(visibilities(mapStub, ROUTE_LAYERS)).toEqual(['none', 'none', 'none']);
    });

    it('show() makes them visible again', async () => {
      window.pwaRoutesOverlay.hide();
      window.pwaRoutesOverlay.show();

      await waitFor(
        () => visibilities(mapStub, ROUTE_LAYERS).every((v) => v === 'visible'),
      );
      expect(visibilities(mapStub, ROUTE_LAYERS)).toEqual(['visible', 'visible', 'visible']);
    });

    it('persists the preference, so an enabled overlay survives a reload', () => {
      window.pwaRoutesOverlay.hide();
      expect(localStorage.getItem(ROUTES_KEY)).toBe('false');

      window.pwaRoutesOverlay.show();
      expect(localStorage.getItem(ROUTES_KEY)).toBe('true');
    });

    it('announces on BOTH transitions, so the roundel ring repaints', async () => {
      // The event is deliberately detail-free — map_roundel_overlay_state.js
      // re-reads every bridge — so this counts announcements rather than
      // reading them. Both edges matter: an overlay that goes dark without
      // announcing leaves a ring claiming it is still drawn.
      const seen = vi.fn();
      document.addEventListener('snowdesk:overlay-visibility-changed', seen);

      window.pwaRoutesOverlay.hide();
      expect(seen).toHaveBeenCalled();

      const afterHide = seen.mock.calls.length;
      window.pwaRoutesOverlay.show();
      await waitFor(() => seen.mock.calls.length > afterHide);
      expect(seen.mock.calls.length).toBeGreaterThan(afterHide);

      document.removeEventListener('snowdesk:overlay-visibility-changed', seen);
    });

    it('emits telemetry for each edge, like the other panel overlays', () => {
      const emit = vi.fn();
      window.pwaTelemetry = { emit };

      window.pwaRoutesOverlay.hide();
      window.pwaRoutesOverlay.show();

      expect(emit.mock.calls.map((call) => [call[0], call[1].visible])).toEqual([
        ['map.route.overlay_toggled', false],
        ['map.route.overlay_toggled', true],
      ]);
      delete window.pwaTelemetry;
    });

    it('leaves the favourites overlay alone on both edges', async () => {
      // showPanelOverlay's one key-specific branch is the favourites
      // resort-exclusion recompute. Routes must not trip it, and must not
      // disturb a sibling overlay's preference — the whole point of adding a
      // key to the shared machinery rather than forking it.
      window.pwaFavouritesOverlay.show();
      await waitFor(() => window.pwaFavouritesOverlay.isVisible());
      const favouritesBefore = localStorage.getItem(FAVOURITES_KEY);

      window.pwaRoutesOverlay.hide();
      window.pwaRoutesOverlay.show();

      expect(window.pwaFavouritesOverlay.isVisible()).toBe(true);
      expect(localStorage.getItem(FAVOURITES_KEY)).toBe(favouritesBefore);
    });

    it('separates what is DRAWN from what was ASKED FOR', () => {
      // The case this distinction exists for. Something else clears the
      // layers off the map without going near the preference — a placement
      // flow does exactly this, and so does enabling the overlay offline
      // with nothing cached, where the fetch fails and nothing is ever
      // installed. isVisible() follows the map, so the roundel's ring goes
      // off; isEnabled() and the stored preference stay where the user left
      // them, so the panel switch keeps reading ON.
      //
      // That disagreement is the feature: it is the only way the user can
      // see that their request never reached the map. A ring claiming
      // "shown" over a blank map would be the defect SNOW-658's review
      // removed, reintroduced by the fourth overlay.
      expect(window.pwaRoutesOverlay.isVisible()).toBe(true);

      for (const id of ROUTE_LAYERS) {
        mapStub.setLayoutProperty(id, 'visibility', 'none');
      }

      expect(window.pwaRoutesOverlay.isVisible()).toBe(false);
      expect(window.pwaRoutesOverlay.isEnabled()).toBe(true);
      expect(localStorage.getItem(ROUTES_KEY)).toBe('true');
    });
  });
});
