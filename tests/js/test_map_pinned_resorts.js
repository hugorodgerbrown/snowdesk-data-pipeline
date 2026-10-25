/*
 * tests/js/test_map_pinned_resorts.js — Vitest for how map.js draws and
 * lists pinned (favourited) resorts with the favourites overlay OFF
 * (SNOW-1229).
 *
 * A pinned resort is a blue resort dot and a "Pinned resorts" row in
 * search, and neither depends on the favourites switch — but the feed both
 * read is the one the favourites layer loads, and the layer loads only
 * while that switch is on. So this suite boots with it off and the network
 * down, and checks the three things that must still hold:
 *
 *   1. Installing the resorts layer reads the feed itself, so a pinned
 *      resort is blue without the user first opening search.
 *   2. With the network down it reads the overlay cache the layer writes
 *      through to, so a device that has read the feed before keeps them.
 *   3. A resort starred offline stays blue when the favourites layer is
 *      (re)installed from the older cached feed — a basemap swap does that.
 *
 * Boots map.js in jsdom the way test_map_favourites_overlay_bridge.js does,
 * whose stub and fixture this copies.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const RESORTS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.5, 46.1] },
      properties: { id: 'verbier', name: 'Verbier', region_id: 'CH-4115' },
    },
  ],
};

/** What the device cached the last time it read the feed. */
const CACHED_FAVOURITES = {
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
  const paints = new Map();
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
    setPaintProperty: (id, prop, value) => {
      paints.set(`${id}:${prop}`, value);
    },
    getPaintProperty: (id, prop) => paints.get(`${id}:${prop}`),
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

/** The resort dot paint, as JSON, for `toContain` checks. */
function resortPaint(map) {
  return JSON.stringify(map.getPaintProperty('resorts-pin', 'circle-color') ?? null);
}

let mapStub;
const getOverlay = vi.fn(async (key) => (key === 'favourites' ? CACHED_FAVOURITES : null));

beforeAll(async () => {
  localStorage.clear();
  localStorage.setItem('snowdesk.map.overlay.favourites', 'false');
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
  window.pwaMapOverlayCache = { getOverlay, putOverlay: vi.fn(async () => {}) };
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      // Offline for the favourites feed; everything else answers.
      if (href.includes('favourites')) return Promise.reject(new TypeError('offline'));
      const body = href.includes('resorts') ? RESORTS_GEOJSON : EMPTY_FC;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  document.dispatchEvent(
    new CustomEvent('snowdesk:overlay-load', { detail: { key: 'resorts' } }),
  );
  await waitFor(() => !!mapStub.getLayer('resorts-pin'));
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete window.pwaMapOverlayCache;
  delete globalThis.maplibregl;
});

describe('pinned resorts with the favourites overlay off', () => {
  it('paints them blue from the cached feed when the resorts install', async () => {
    expect(mapStub.getLayer('favourites-pin')).toBeNull();
    await waitFor(() => resortPaint(mapStub).includes('verbier'));

    expect(resortPaint(mapStub)).toContain('verbier');
    expect(resortPaint(mapStub)).toContain('#1a73e8');
    expect(getOverlay).toHaveBeenCalledWith('favourites');
  });

  it('keeps a resort starred offline blue across a favourites re-install', async () => {
    document.dispatchEvent(new CustomEvent('snowdesk:favourite-pending', {
      detail: { lat: 46.0, lon: 7.7, name: 'Zermatt', resortSlug: 'zermatt' },
    }));
    expect(resortPaint(mapStub)).toContain('zermatt');

    // The layer installs from the cached feed, which predates the star.
    window.pwaFavouritesOverlay.show();
    await waitFor(() => !!mapStub.getLayer('favourites-pin'));

    expect(resortPaint(mapStub)).toContain('zermatt');
    expect(resortPaint(mapStub)).toContain('verbier');
  });
});
