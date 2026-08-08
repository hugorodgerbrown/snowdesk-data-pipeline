/*
 * tests/js/test_map_downloaded_overlay_colour.js — Vitest DOM test for
 * SNOW-645 (review item — "every basemap at once"): the "downloaded areas"
 * map overlay (`cached-tiles-fill` / `cached-tiles-line`) paints EACH tile
 * in the identity colour of the basemap it was downloaded UNDER, not the
 * one active on screen right now.
 *
 * This supersedes an earlier version of this file, which covered a
 * single-colour-tied-to-the-active-basemap mechanism — Hugo's own
 * follow-up call, after the overlay itself was rebuilt to show downloads
 * across every basemap at once rather than emptying out on a basemap
 * switch (see refreshDownloadedOverlay's own "EVERY BASEMAP AT ONCE"
 * comment in map.js). The mechanism is now a MapLibre `match` PAINT
 * EXPRESSION keyed on each tile feature's own `basemapKey` property
 * (`downloadedTilesColourExpression`, map.js), not a flat colour
 * re-resolved on a basemap-changed event — this file tests that shape.
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern —
 * see its header for the general rationale. This file's stub additionally
 * TRACKS added layers and setPaintProperty calls (most other harnesses'
 * stubs are pure no-ops for these, since nothing before this ticket needed
 * to assert on paint state), which is the whole point here.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const TEMPLATE_LIBERTY = 'https://tiles-liberty.example.invalid/{z}/{x}/{y}.pbf';
const TEMPLATE_SWISSTOPO = 'https://tiles-swisstopo.example.invalid/{z}/{x}/{y}.pbf';

const LIBERTY_COLOUR = 'rgb(1, 2, 3)';
const SWISSTOPO_COLOUR = 'rgb(9, 8, 7)';
const SYNC_OK_COLOUR = 'rgb(4, 5, 6)';

const CACHED_TILES_ZOOM = 14;

/** Empty regions collection — this suite only cares about the overlay's colour. */
const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub, mirroring test_map_download_bytes.js's, but with
 * `addLayer` / `getLayer` / `setPaintProperty` backed by a real `layers`
 * Map (id -> paint object) and a `sources` Map (id -> data) so a test can
 * read back both the paint expression AND the feature data
 * refreshDownloadedOverlay builds from them.
 */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  let cachedTilesData = null;
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: (ev, cb) => {
      (handlers[ev] ||= []).push(cb);
    },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => null,
    getPaintProperty: (id, prop) => {
      const paint = layers.get(id);
      return paint ? paint[prop] : undefined;
    },
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) =>
      id === 'cached-tiles'
        ? { setData: (data) => { cachedTilesData = data; } }
        : id === 'basemap'
          ? { tiles: [TEMPLATE_LIBERTY] }
          : null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, { ...(def.paint || {}) });
    },
    removeLayer: (id) => {
      layers.delete(id);
    },
    removeSource: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: (id, prop, value) => {
      const paint = layers.get(id);
      if (paint) paint[prop] = value;
    },
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: { basemap: { type: 'vector' } } }),
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
      getWest: () => 5,
      getSouth: () => 45,
      getEast: () => 10,
      getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
    layers,
    getCachedTilesData: () => cachedTilesData,
  };
  globalThis.maplibregl = {
    Map: function () {
      return map;
    },
    Popup: function () {
      return {
        setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }),
        remove: () => {},
      };
    },
    GeolocateControl: function () {
      return { on: () => {} };
    },
    AttributionControl: function () {
      return {};
    },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/**
 * Cache Storage stub with one pinned bucket per `{name, urls}` entry —
 * mirrors SNOW-586's one-bucket-per-downloaded-area shape closely enough
 * for `pinnedBasemapCacheURLs` (static/js/map_basemap_downloads.js) to
 * union across them, which is all this suite needs from it.
 */
function installCachesStub(buckets) {
  const stub = {
    keys: vi.fn(async () => buckets.map((b) => b.name)),
    open: vi.fn(async (name) => {
      const bucket = buckets.find((b) => b.name === name);
      const urls = bucket ? bucket.urls : [];
      return {
        keys: async () => urls.map((url) => ({ url })),
        put: async () => {},
        match: async () => undefined,
      };
    }),
    delete: vi.fn(async () => {}),
  };
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  return stub;
}

/**
 * `window.pwaDb` stub seeded with the raw `basemap.regions` /
 * `basemap.customAreas` records `basemapDownloadedTemplates()` reads
 * directly (see that function's own docstring — it is a sibling of
 * `basemapDownloadedAreas()`, not a wrapper, so this suite has to write
 * the SAME record shape map_region_download.js's `_recordRegionDownload`
 * does, not the normalised `areas()` shape).
 */
function installDbStub(regions, customAreas) {
  const rows = new Map([
    ['basemap.regions', regions || []],
    ['basemap.customAreas', customAreas || []],
  ]);
  window.pwaDb = {
    get: vi.fn(async (_store, key) =>
      rows.has(key) ? { key, value: rows.get(key) } : undefined,
    ),
    put: vi.fn(async (_store, row) => {
      rows.set(row.key, row.value);
    }),
    delete: vi.fn(async () => {}),
  };
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <ul id="basemap-menu">
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="https://tiles.example.invalid/liberty.json"
          aria-checked="true"
        >Standard</button>
      </li>
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="swisstopo_winter"
          data-basemap-url="https://tiles.example.invalid/swisstopo.json"
          aria-checked="false"
        >Swisstopo (CH)</button>
      </li>
    </ul>`;
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

/** Read a `['match', ['get', 'basemapKey'], key, colour, …, fallback]` expression as a plain object. */
function matchArms(expr) {
  if (!Array.isArray(expr)) return null;
  const arms = {};
  for (let i = 2; i + 1 < expr.length; i += 2) arms[expr[i]] = expr[i + 1];
  return { arms, fallback: expr[expr.length - 1] };
}

let mapStub;

beforeAll(async () => {
  buildFixture();
  document.documentElement.style.setProperty(
    '--color-basemap-openfreemap-liberty',
    LIBERTY_COLOUR,
  );
  document.documentElement.style.setProperty(
    '--color-basemap-swisstopo-winter',
    SWISSTOPO_COLOUR,
  );
  document.documentElement.style.setProperty('--color-sync-ok', SYNC_OK_COLOUR);

  installDbStub(
    [
      // A region downloaded under Standard (openfreemap_liberty).
      {
        region_id: 'CH-2101',
        name: 'Aletsch',
        template: TEMPLATE_LIBERTY,
        basemapKey: 'openfreemap_liberty',
        bytes: 1000,
        savedAt: '2026-08-01T10:00:00.000Z',
      },
    ],
    [
      // A custom area downloaded under Swisstopo Winter.
      {
        id: 'custom-a1',
        ordinal: 1,
        bbox: [7.9, 46.4, 8.1, 46.6],
        template: TEMPLATE_SWISSTOPO,
        basemapKey: 'swisstopo_winter',
        bytes: 2000,
        savedAt: '2026-08-01T10:00:00.000Z',
      },
    ],
  );

  installCachesStub([
    {
      name: 'snowdesk-basemap-pinned-CH-2101',
      urls: [`https://tiles-liberty.example.invalid/${CACHED_TILES_ZOOM}/100/200.pbf`],
    },
    {
      name: 'snowdesk-basemap-pinned-custom-a1',
      urls: [`https://tiles-swisstopo.example.invalid/${CACHED_TILES_ZOOM}/300/400.pbf`],
    },
  ]);

  mapStub = stubMapLibre();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      let body = {};
      if (href.includes('regions.geojson')) body = REGIONS_GEOJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom; installRegionsLayers (and so the
  // two cached-tiles-* layers) hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
  // downloadedOverlayVisible is session-scoped, off by default — only
  // window.pwaDownloadedOverlay.show()/hide() (called from the sheet's
  // toggle) ever flip it, so a test must call show() itself rather than
  // relying on any persisted flag.
  await window.pwaDownloadedOverlay.show();
});

afterAll(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute('style');
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('downloaded-areas overlay colour (SNOW-645 review — every basemap at once)', () => {
  it('paints a match expression with one arm per basemap actually downloaded', async () => {
    await waitFor(() => {
      const expr = mapStub.layers.get('cached-tiles-fill')?.['fill-color'];
      return Array.isArray(expr) && expr[0] === 'match';
    });

    const fillExpr = mapStub.layers.get('cached-tiles-fill')['fill-color'];
    const lineExpr = mapStub.layers.get('cached-tiles-line')['line-color'];
    expect(fillExpr[0]).toBe('match');
    expect(fillExpr[1]).toEqual(['get', 'basemapKey']);

    const { arms, fallback } = matchArms(fillExpr);
    expect(arms.openfreemap_liberty).toBe(LIBERTY_COLOUR);
    expect(arms.swisstopo_winter).toBe(SWISSTOPO_COLOUR);
    expect(fallback).toBe(SYNC_OK_COLOUR);

    expect(matchArms(lineExpr).arms).toEqual(arms);
  });

  it('tags each feature with the basemapKey it was downloaded under', async () => {
    const data = mapStub.getCachedTilesData();
    const keys = new Set(data.features.map((f) => f.properties.basemapKey));
    expect(keys).toEqual(new Set(['openfreemap_liberty', 'swisstopo_winter']));
  });
});

describe('downloaded-areas overlay colour — nothing downloaded', () => {
  it('falls back to a flat colour when no basemap keys are present at all', async () => {
    installDbStub([], []);
    installCachesStub([]);
    window.pwaDownloadedOverlay.hide();
    await window.pwaDownloadedOverlay.show();

    const fillExpr = mapStub.layers.get('cached-tiles-fill')['fill-color'];
    expect(fillExpr).toBe(SYNC_OK_COLOUR);
  });
});
