/*
 * tests/js/test_map_favourite_pending_hydrates_cache.js — an optimistic pin
 * dropped offline brings the user's OTHER pins onto the map with it
 * (SNOW-886 review).
 *
 * Scenario: none — a one-module ordering property, and one that only shows
 * itself on a fresh page with no network. No manual script could set it up
 * reliably and a browser could not tell it from a slow load.
 *
 * The trap. `snowdesk:favourite-pending` installs the favourites layer when
 * nothing has installed it yet, and sets ``overlayLoaded.favourites`` when
 * it does. That flag is exactly what ``ensureOverlayLoaded`` short-circuits
 * on — so this install is the LAST WORD on what the layer holds for the rest
 * of the page. Online it does not matter: the mutation queue drains, the
 * handler above refetches, and the authoritative collection replaces this
 * one within a moment. Offline there is no drain, and the pins the user
 * already has are sitting in the overlay cache that only the loader reads.
 *
 * It became reachable when SNOW-886 made saving a pin switch the overlay ON.
 * Before that a create left the layer hidden for a user who had it off — the
 * defect that ticket fixed — so an incomplete install cost nothing because
 * nobody was looking at it. Now the map turns itself on and would claim to
 * be showing the user's favourites while drawing exactly one of them.
 *
 * Its own file because the assertion needs a map on which NOTHING has yet
 * installed the favourites source, and every other suite that boots the
 * bundle installs one on the way past. Same reason the report-gate suites
 * are split: the state under test is set once, at boot.
 *
 * Booting map.js in jsdom follows test_map_write_listeners_bind_without_load.js,
 * including its deliberate refusal to fire MapLibre's `load`.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

const ONE_ROUTE = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[7.5, 46.1], [7.54, 46.14]] },
      properties: { uuid: 'r-1', name: 'Rosablanche', bounds: [7.5, 46.1, 7.54, 46.14] },
    },
  ],
};

/** MapLibre stub that NEVER fires `load` — the whole point of this suite. */
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
    // Both false, as they are for a style that never finished: this is the
    // state the pane and a dead tile origin both produce.
    isStyleLoaded: () => false,
    loaded: () => false,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    areTilesLoaded: () => false,
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

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/** Every URL this bundle has fetched since the last clear. */
function fetched(needle) {
  return globalThis.fetch.mock.calls.filter(([url]) =>
    String(url).includes(needle),
  ).length;
}

let mapStub;

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
      const body = String(url).includes('routes') ? ONE_ROUTE : EMPTY_FC;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  loadMapBundle();
  // DELIBERATELY NOT run: `for (const h of mapStub.handlers.load) await h()`.
  // Every other map suite does; this one is about what still works when it
  // never happens.
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('an optimistic pin installed before anything has loaded the layer', () => {
  it('brings the cached pins with it, not just the pending one', async () => {
    window.pwaMapOverlayCache = {
      getOverlay: vi.fn(async () => ({
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [7.2, 46.0] },
            properties: { uuid: 'f-1', name: 'Saved earlier' },
          },
        ],
      })),
      putOverlay: vi.fn(),
    };

    document.dispatchEvent(
      new CustomEvent('snowdesk:favourite-pending', {
        detail: { lat: 46.1, lon: 7.5, name: 'Queued' },
      }),
    );

    await waitFor(() => mapStub.sources.has('favourites'));
    const { features } = mapStub.sources.get('favourites').data;
    expect(features.map((f) => f.properties.name)).toEqual([
      'Saved earlier',
      'Queued',
    ]);
    // Last, and marked: the pending pin draws at half opacity, and the
    // authoritative one replaces it when the queue drains.
    expect(features[1].properties.pending).toBe(true);
    expect(window.pwaMapOverlayCache.getOverlay).toHaveBeenCalledWith('favourites');
  });

  it('reads the cache once, then appends to what it has', async () => {
    // The hydrate is guarded on the in-memory collection being EMPTY. Once
    // it holds something, that is the fresher copy and re-reading the cache
    // over it would put a stale collection on the map — so a second pin in
    // the same session appends and asks IDB nothing.
    window.pwaMapOverlayCache.getOverlay.mockClear();

    document.dispatchEvent(
      new CustomEvent('snowdesk:favourite-pending', {
        detail: { lat: 46.2, lon: 7.6, name: 'Second' },
      }),
    );

    const source = mapStub.sources.get('favourites');
    await waitFor(() => source.setData.mock.calls.length > 0);
    const [collection] = source.setData.mock.calls.at(-1);
    expect(collection.features.map((f) => f.properties.name)).toEqual([
      'Saved earlier',
      'Queued',
      'Second',
    ]);
    expect(window.pwaMapOverlayCache.getOverlay).not.toHaveBeenCalled();
  });
});
