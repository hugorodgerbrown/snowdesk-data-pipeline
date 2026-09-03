/*
 * tests/js/test_map_favourites_overlay_bridge.js — Vitest DOM test for
 * SNOW-658's two panel-driven overlay bridges, window.pwaFavouritesOverlay
 * and window.pwaCommunityReportsOverlay (static/js/map.js).
 *
 * Both overlays lost their layers-menu row this ticket: the switch that
 * drives them now lives in the panel the matching roundel opens, which is a
 * different IIFE (favourites.js / report.js) reaching map.js through a
 * frozen bridge — the shape window.pwaDownloadedOverlay already had, and
 * covered by tests/js/test_map_downloaded_overlay_colour.js's own
 * show()/hide()/isVisible() block, whose structure this file follows.
 *
 * Four things have to hold, and only one of them is obvious:
 *
 *   1. show()/hide() persist the preference under the SAME localStorage key
 *      the menu row used, so a device carrying a stored preference from
 *      before this ticket keeps it.
 *   2. hide() actually drops the layers (the picker's old off-path).
 *   3. Both edges recompute the favourited-resort exclusion. That is the
 *      non-obvious one: a favourited resort hides its plain dot while its
 *      star is showing, so switching the star off without recomputing
 *      leaves the resort invisible — no star, no dot (SNOW-499). The
 *      picker got this right via a CustomEvent; the bridge has to do it
 *      directly.
 *   4. The bridge answers two different questions and does not confuse
 *      them (SNOW-658 review): isVisible() is what the map is drawing,
 *      isEnabled() is what the user asked for. The roundel ring reads the
 *      first, the panel switch the second, and they are allowed to
 *      disagree — an overlay enabled with nothing to draw is precisely the
 *      state the user has to be able to see.
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern —
 * see its header for the general rationale.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const FAVOURITES_KEY = 'snowdesk.map.overlay.favourites';
const COMMUNITY_REPORTS_KEY = 'snowdesk.map.overlay.community_reports';

const FAVOURITE_LAYERS = ['favourites-pin', 'favourites-label'];
const COMMUNITY_REPORT_LAYERS = [
  'community-reports-clusters',
  'community-reports-cluster-count',
  'community-reports-point',
];

/** One resort, favourited, so the exclusion filter has something to exclude. */
const RESORTS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.5, 46.1] },
      properties: { id: 'verbier', name: 'Verbier', region_id: 'CH-2101' },
    },
  ],
};

const FAVOURITES_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.5, 46.1] },
      properties: { uuid: 'f-1', name: 'Verbier', resort_slug: 'verbier' },
    },
  ],
};

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub tracking per-layer `layout` and per-layer `filter`,
 * the two things this suite reads back.
 */
function stubMapLibre() {
  const handlers = {};
  const layouts = new Map();
  const filters = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layouts.has(id) ? { id } : null),
    getFilter: (id) => filters.get(id) ?? null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
      if (def.filter !== undefined) filters.set(def.id, def.filter);
    },
    removeLayer: (id) => {
      layouts.delete(id);
      filters.delete(id);
    },
    removeSource: () => {},
    // Load-bearing, not filler: installFavouritesLayer/installResortsLayer
    // raise their marker layers above the choropleth on every install, and a
    // stub without this throws INSIDE the lazy-load promise — which map.js
    // swallows, so the overlay silently never becomes visible.
    moveLayer: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: () => {},
    setFilter: (id, filter) => {
      filters.set(id, filter);
    },
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

/** The DOM map.js's boot reads — no layers menu, which is this ticket's point. */
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

/** Whether the resorts layers currently exclude the favourited resort. */
function resortsExcludeFavourited(map) {
  return JSON.stringify(map.getFilter('resorts-pin') ?? null).includes('"literal"');
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
      if (href.includes('resorts')) body = RESORTS_GEOJSON;
      if (href.includes('favourites')) body = FAVOURITES_GEOJSON;
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
  // The resorts layer is lazy too, and the exclusion filter this suite reads
  // back only exists once it is installed.
  document.dispatchEvent(
    new CustomEvent('snowdesk:overlay-load', { detail: { key: 'resorts' } }),
  );
  await waitFor(() => !!mapStub.getLayer('resorts-pin'));
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('window.pwaFavouritesOverlay', () => {
  beforeEach(async () => {
    window.pwaFavouritesOverlay.show();
    await waitFor(() => window.pwaFavouritesOverlay.isVisible());
  });

  it('is a frozen bridge, like every other map.js export', () => {
    expect(Object.isFrozen(window.pwaFavouritesOverlay)).toBe(true);
  });

  it('hide() drops every favourites layer and flips isVisible()', async () => {
    window.pwaFavouritesOverlay.hide();

    expect(window.pwaFavouritesOverlay.isVisible()).toBe(false);
    expect(visibilities(mapStub, FAVOURITE_LAYERS)).toEqual(['none', 'none']);
  });

  it('show() makes them visible again', async () => {
    window.pwaFavouritesOverlay.hide();
    window.pwaFavouritesOverlay.show();

    await waitFor(
      () => visibilities(mapStub, FAVOURITE_LAYERS).every((v) => v === 'visible'),
    );
    expect(visibilities(mapStub, FAVOURITE_LAYERS)).toEqual(['visible', 'visible']);
  });

  it('persists the preference under the key the menu row used', () => {
    window.pwaFavouritesOverlay.hide();
    expect(localStorage.getItem(FAVOURITES_KEY)).toBe('false');

    window.pwaFavouritesOverlay.show();
    expect(localStorage.getItem(FAVOURITES_KEY)).toBe('true');
  });

  it('recomputes the favourited-resort exclusion on BOTH edges', async () => {
    // With the stars showing, a favourited resort's plain dot is excluded…
    await waitFor(() => resortsExcludeFavourited(mapStub));
    expect(resortsExcludeFavourited(mapStub)).toBe(true);

    // …and switching them off must bring it back, or the resort vanishes
    // entirely: no star, no dot (SNOW-499).
    window.pwaFavouritesOverlay.hide();
    expect(resortsExcludeFavourited(mapStub)).toBe(false);

    window.pwaFavouritesOverlay.show();
    await waitFor(() => resortsExcludeFavourited(mapStub));
    expect(resortsExcludeFavourited(mapStub)).toBe(true);
  });

  it('separates what is drawn from what was asked for', async () => {
    // Something else clears the layers off the map — a placement flow does
    // exactly this — without going near the preference. isVisible() follows
    // the map; isEnabled() and the stored preference stay where the user
    // left them, so nothing switches their overlay off behind their back.
    expect(window.pwaFavouritesOverlay.isVisible()).toBe(true);

    for (const id of FAVOURITE_LAYERS) {
      mapStub.setLayoutProperty(id, 'visibility', 'none');
    }

    expect(window.pwaFavouritesOverlay.isVisible()).toBe(false);
    expect(window.pwaFavouritesOverlay.isEnabled()).toBe(true);
    expect(localStorage.getItem(FAVOURITES_KEY)).toBe('true');
  });

  it('emits telemetry for each edge, as the menu row did', () => {
    const emit = vi.fn();
    window.pwaTelemetry = { emit };

    window.pwaFavouritesOverlay.hide();
    window.pwaFavouritesOverlay.show();

    expect(emit.mock.calls.map((call) => [call[0], call[1].visible])).toEqual([
      ['map.favourite.overlay_toggled', false],
      ['map.favourite.overlay_toggled', true],
    ]);
    delete window.pwaTelemetry;
  });
});

describe('window.pwaCommunityReportsOverlay', () => {
  it('show()/hide() drive the three cluster layers and persist the preference', async () => {
    window.pwaCommunityReportsOverlay.show();
    await waitFor(() =>
      visibilities(mapStub, COMMUNITY_REPORT_LAYERS).every((v) => v === 'visible'),
    );

    expect(window.pwaCommunityReportsOverlay.isVisible()).toBe(true);
    expect(window.pwaCommunityReportsOverlay.isEnabled()).toBe(true);
    expect(localStorage.getItem(COMMUNITY_REPORTS_KEY)).toBe('true');

    window.pwaCommunityReportsOverlay.hide();

    expect(window.pwaCommunityReportsOverlay.isVisible()).toBe(false);
    expect(window.pwaCommunityReportsOverlay.isEnabled()).toBe(false);
    expect(localStorage.getItem(COMMUNITY_REPORTS_KEY)).toBe('false');
    expect(visibilities(mapStub, COMMUNITY_REPORT_LAYERS)).toEqual([
      'none',
      'none',
      'none',
    ]);
  });

  it('never touches the favourites overlay', async () => {
    window.pwaFavouritesOverlay.show();
    await waitFor(() => window.pwaFavouritesOverlay.isVisible());

    window.pwaCommunityReportsOverlay.hide();

    expect(window.pwaFavouritesOverlay.isVisible()).toBe(true);
  });
});
