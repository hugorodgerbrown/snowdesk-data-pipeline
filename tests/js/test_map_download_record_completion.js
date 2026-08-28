/*
 * tests/js/test_map_download_record_completion.js — the download roundel
 * and the downloaded-areas map overlay must agree about a download that is
 * genuinely on disk, even when its stored `basemap.regions` record predates
 * the fields both surfaces key off.
 *
 * Two records go incomplete and never get rewritten, because the record is
 * only ever written by a fresh download: one from before SNOW-632 (no
 * `template`) and one from before SNOW-645 (no `basemapKey`). Each made the
 * two surfaces contradict each other on screen:
 *
 *   - no `template` — `_probeDone` (map_region_download.js) reads a missing
 *     template as "the active basemap's" and resolves `done` off real
 *     cached tiles, so the roundel showed a solid disc; while
 *     `basemapDownloadedTemplates()` (map_basemap_downloads.js), the
 *     overlay's ONLY source of templates, skipped the record outright and
 *     the overlay drew no squares at all for that region.
 *   - no `basemapKey` — the same download read as its own basemap's
 *     identity colour when that basemap was active (`done` takes the active
 *     key) but as the "basemap unknown" green ring from any other basemap
 *     (`other-basemap` takes the record's key, which was absent).
 *
 * Both were reported from staging with screenshots: a solid blue Kandersteg
 * roundel over a map with no Kandersteg squares, and one region drawing a
 * rust disc on Swisstopo but a green ring on Standard.
 *
 * The fixture therefore seeds BOTH shapes at once and asserts the two
 * surfaces agree, rather than testing either function's return value in
 * isolation — agreement between them is the actual requirement, and it is
 * what a unit test of either half would have kept missing.
 *
 * Harness follows test_map_download_eviction.js (roundel) and
 * test_map_downloaded_overlay_colour.js (overlay paint + cached-tiles
 * data); see their headers for the general jsdom-boot rationale.
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

// The region whose record has no `template` (pre-SNOW-632) — "Kandersteg"
// in the staging report. Its tiles ARE cached, under the ACTIVE basemap.
const REGION_NO_TEMPLATE = 'CH-4115';
const TILE_A = { x: 8577, y: 5811 };

// The region whose record names a template but no `basemapKey`
// (pre-SNOW-645), downloaded under the basemap that is NOT active.
const REGION_NO_KEY = 'CH-2101';
const TILE_B = { x: 8600, y: 5820 };

const tileURL = (template, tile) =>
  template
    .replace('{z}', String(CACHED_TILES_ZOOM))
    .replace('{x}', String(tile.x))
    .replace('{y}', String(tile.y));

/** Both regions, each carrying the precomputed summary /api/regions.geojson emits. */
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: REGION_NO_TEMPLATE,
        name: 'Kandersteg',
        download: { count: 1, mb: 5, over_ceiling: false, centre_tile: { z: 14, ...TILE_A } },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.01, 46.0], [7.01, 46.01], [7.0, 46.01], [7.0, 46.0]]],
      },
    },
    {
      type: 'Feature',
      properties: {
        id: REGION_NO_KEY,
        name: 'Östliche Berner Voralpen',
        download: { count: 1, mb: 16, over_ceiling: false, centre_tile: { z: 14, ...TILE_B } },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.5, 46.7], [7.51, 46.7], [7.51, 46.71], [7.5, 46.71], [7.5, 46.7]]],
      },
    },
  ],
};

/** Minimal MapLibre stub — cached-tiles source data and layer paint are readable back. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
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
    getLayoutProperty: (id, prop) => {
      const layout = layouts.get(id);
      return layout ? layout[prop] : undefined;
    },
    getPaintProperty: (id, prop) => {
      const paint = layers.get(id);
      return paint ? paint[prop] : undefined;
    },
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) =>
      id === 'cached-tiles'
        ? {
            setData: (data) => {
              cachedTilesData = data;
            },
          }
        : id === 'basemap'
          ? { tiles: [TEMPLATE_LIBERTY] }
          : null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, { ...(def.paint || {}) });
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => {
      layers.delete(id);
      layouts.delete(id);
    },
    removeSource: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
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

/** One pinned bucket per downloaded area, unioned by `pinnedBasemapCacheURLs`. */
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

/** In-memory `meta:app`; `rows` is returned so a test can read what was healed. */
function installDbStub(initial) {
  const rows = new Map(Object.entries(initial || {}));
  window.pwaDb = {
    rows,
    get: vi.fn(async (_store, key) =>
      rows.has(key) ? { key, value: rows.get(key) } : undefined,
    ),
    put: vi.fn(async (_store, row) => {
      rows.set(row.key, row.value);
      return row.key;
    }),
    delete: vi.fn(async (_store, key) => {
      rows.delete(key);
    }),
  };
  return rows;
}

/** The DOM map.js's boot, the roundel and the basemap picker read. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <button id="map-download-control" type="button"></button>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <ul id="basemap-menu">
      <li role="none">
        <button type="button" class="basemap-menu-item"
                data-basemap-key="openfreemap_liberty"
                data-basemap-url="https://tiles.example.invalid/liberty.json"
                aria-checked="true">Standard</button>
      </li>
      <li role="none">
        <button type="button" class="basemap-menu-item"
                data-basemap-key="swisstopo_winter"
                data-basemap-url="https://tiles.example.invalid/swisstopo.json"
                aria-checked="false">Swisstopo (CH)</button>
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

/** Select `regionId` the way the map's own click handler does. */
function selectRegion(regionId, name) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:region-selected', {
      detail: { region_id: regionId, region_name: name },
    }),
  );
}

let mapStub;
let dbRows;

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

  dbRows = installDbStub({
    'basemap.regions': [
      // Pre-SNOW-632: no `template`, no `basemapKey`. Its tiles are cached
      // under the ACTIVE basemap, so the roundel resolves `done`.
      {
        region_id: REGION_NO_TEMPLATE,
        name: 'Kandersteg',
        band: [10, 14],
        z: { 14: [TILE_A.x, TILE_A.x, TILE_A.y, TILE_A.y] },
        bytes: 5 * 1024 * 1024,
        savedAt: '2026-07-01T10:00:00.000Z',
      },
      // Pre-SNOW-645: names a template, but no `basemapKey` to colour the
      // ring with. Downloaded under Swisstopo, which is NOT active.
      {
        region_id: REGION_NO_KEY,
        name: 'Östliche Berner Voralpen',
        band: [10, 14],
        z: { 14: [TILE_B.x, TILE_B.x, TILE_B.y, TILE_B.y] },
        template: TEMPLATE_SWISSTOPO,
        bytes: 16 * 1024 * 1024,
        savedAt: '2026-07-02T10:00:00.000Z',
      },
    ],
    // A custom area under the same Swisstopo template that DOES name its
    // basemap — the only thing on record that can name TEMPLATE_SWISSTOPO,
    // and so the source `_basemapKeyForTemplate` resolves the ring from.
    'basemap.customAreas': [
      {
        id: 'custom-a1',
        ordinal: 1,
        bbox: [7.9, 46.4, 8.1, 46.6],
        template: TEMPLATE_SWISSTOPO,
        basemapKey: 'swisstopo_winter',
        bytes: 2 * 1024 * 1024,
        savedAt: '2026-07-03T10:00:00.000Z',
      },
    ],
  });

  installCachesStub([
    {
      name: 'snowdesk-basemap-pinned-region-' + REGION_NO_TEMPLATE,
      urls: [tileURL(TEMPLATE_LIBERTY, TILE_A)],
    },
    {
      name: 'snowdesk-basemap-pinned-region-' + REGION_NO_KEY,
      urls: [tileURL(TEMPLATE_SWISSTOPO, TILE_B)],
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
  // cached-tiles-* layers) hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete globalThis.maplibregl;
});

describe('a record with no stored template', () => {
  // Runs BEFORE any region is selected, so the overlay is asserted while
  // the record is still incomplete — this is the fallback in
  // basemapDownloadedTemplates(), not the roundel's later heal.
  it('still draws its squares on the map overlay', async () => {
    await window.pwaDownloadedOverlay.show();
    await waitFor(() => (mapStub.getCachedTilesData()?.features || []).length > 0);

    const features = mapStub.getCachedTilesData().features;
    // The templateless record's tile is drawn — it falls back to the ACTIVE
    // basemap's template, which is where its tiles actually turn out to be.
    // This is the region the staging screenshot showed as a solid roundel
    // over empty map.
    //
    // ONE feature, not two: the overlay draws the active basemap's downloads
    // and only those, so the Swisstopo record's tile is not on this map (see
    // refreshDownloadedOverlay's own block comment). Its own tile is asserted
    // through the roundel below, which is the surface that still answers for
    // an area downloaded under another basemap.
    expect(features).toHaveLength(1);
  });

  it('is completed from what the probe verified, once its region is read', async () => {
    const btn = document.getElementById('map-download-control');
    selectRegion(REGION_NO_TEMPLATE, 'Kandersteg');
    await waitFor(() => btn.dataset.downloadState === 'done');

    expect(btn.dataset.downloadState).toBe('done');
    expect(btn.dataset.basemapKey).toBe('openfreemap_liberty');

    const record = dbRows
      .get('basemap.regions')
      .find((entry) => entry.region_id === REGION_NO_TEMPLATE);
    // Healed from the values the cache itself proved: these tiles were
    // found under the ACTIVE template, so that is what the record now says.
    expect(record.template).toBe(TEMPLATE_LIBERTY);
    expect(record.basemapKey).toBe('openfreemap_liberty');
  });
});

describe('a record with no stored basemapKey', () => {
  it('takes its ring colour from another record sharing its template', async () => {
    const btn = document.getElementById('map-download-control');
    selectRegion(REGION_NO_KEY, 'Östliche Berner Voralpen');
    await waitFor(() => btn.dataset.downloadState === 'other-basemap');

    expect(btn.dataset.downloadState).toBe('other-basemap');
    // Not '' (which would drop the attribute and paint the "unknown" green
    // ring) — the custom area on the same template names this basemap.
    expect(btn.dataset.basemapKey).toBe('swisstopo_winter');
  });

  it('does not write the inferred key back to the record', async () => {
    const record = dbRows
      .get('basemap.regions')
      .find((entry) => entry.region_id === REGION_NO_KEY);
    // Display-only, the same rule orphanBasemapKey sets: only a value the
    // cache has proven is ever persisted, and this one is an inference
    // from a sibling record.
    expect(record.basemapKey).toBeUndefined();
  });
});
