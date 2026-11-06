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
 *   - (SNOW-844) no `deps` — the same record again, one field further out:
 *     nothing named the style/sprite/TileJSON documents the area needs to
 *     RENDER, so a probe could not tell an area missing its sprite from one
 *     that never listed it. Healed on the same terms as the two below —
 *     only from URLs the cache has just been found to hold.
 *   - no `basemapKey` — the same download read as its own basemap's
 *     identity colour when that basemap was active (`done` takes the active
 *     key) but as the "basemap unknown" green ring from any other basemap
 *     (`other-basemap` takes the record's key, which was absent).
 *
 * Both were reported from staging with screenshots: a solid blue Kandersteg
 * roundel over a map with no Kandersteg squares, and one region drawing a
 * rust disc on Swisstopo but a green ring on OpenFreeMap.
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

// SNOW-844: the active basemap's style document — a render dependency the
// probe now checks alongside the tiles. Matches the `data-basemap-url` the
// fixture's OpenFreeMap picker entry carries, because that attribute is
// where `activeBasemapRenderDependencyURLs` reads it from.
const STYLE_URL_LIBERTY = 'https://tiles.example.invalid/liberty.json';

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

// SNOW-1453: two regions carrying the terrain flag, seeded only by the
// elevation tests below (so the overlay count above stays two). Each holds
// a z13 row as well as its z14 tile, because elevation tiles derive from
// the z10-13 rows.
const DEM_TEMPLATE = 'https://dem.example.invalid/dem/2026-11/{z}/{x}/{y}.webp';
const REGION_ELEVATION_HELD = 'CH-4211';
const REGION_ELEVATION_MISSING = 'CH-4212';
const Z_HELD = { 13: [4290, 4290, 2906, 2906], 14: [8580, 8580, 5812, 5812] };
const Z_MISSING = { 13: [4295, 4295, 2908, 2908], 14: [8590, 8590, 5816, 5816] };

/** Every `{z}/{x}/{y}` URL a record's `z` rows name under `template`. */
function zRowURLs(template, z) {
  const urls = [];
  for (const [zoom, [x0, x1, y0, y1]] of Object.entries(z)) {
    for (let x = x0; x <= x1; x += 1) {
      for (let y = y0; y <= y1; y += 1) {
        urls.push(template.replace('{z}', zoom).replace('{x}', String(x)).replace('{y}', String(y)));
      }
    }
  }
  return urls;
}

/** A small square region feature, centred on nothing in particular. */
function regionFeature(id, name, lng) {
  return {
    type: 'Feature',
    properties: {
      id: id,
      name: name,
      download: { count: 2, mb: 1, over_ceiling: false, centre_tile: { z: 14, x: 0, y: 0 } },
    },
    geometry: {
      type: 'Polygon',
      coordinates: [[[lng, 46.4], [lng + 0.01, 46.4], [lng + 0.01, 46.41], [lng, 46.41], [lng, 46.4]]],
    },
  };
}

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
    regionFeature(REGION_ELEVATION_HELD, 'Wildstrubel', 8.5),
    regionFeature(REGION_ELEVATION_MISSING, 'Gstaad', 8.7),
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
                aria-checked="true">OpenFreeMap</button>
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
let cacheBuckets;

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

  cacheBuckets = [
    {
      name: 'snowdesk-basemap-pinned-region-' + REGION_NO_TEMPLATE,
      // SNOW-844: the style document as well as the tile. "Downloaded"
      // means renderable now, not "the tiles are here" — an area holding
      // tiles and nothing else paints 'incomplete', which is that
      // ticket's whole point and is asserted in
      // test_map_multi_source_basemap.js. This fixture is about record
      // COMPLETION, so it seeds an area that genuinely is complete.
      urls: [tileURL(TEMPLATE_LIBERTY, TILE_A), STYLE_URL_LIBERTY],
    },
    {
      name: 'snowdesk-basemap-pinned-region-' + REGION_NO_KEY,
      urls: [tileURL(TEMPLATE_SWISSTOPO, TILE_B)],
    },
  ];
  installCachesStub(cacheBuckets);

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
  // SNOW-1453: the elevation coverage comes from here.
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/terrain_filter_core.js');
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
    // TWO features since SNOW-857: the Swisstopo record's tile is on the map
    // as well now, as an OUTLINE. What this test is about is the templateless
    // one, and what matters for it is that it lands on the `here` side —
    // falling back to the active basemap's template has to mean falling back
    // to the active basemap's MARK, or the fallback would draw it as
    // somebody else's download.
    expect(features).toHaveLength(2);
    const here = features.filter((f) => f.properties.here);
    expect(here).toHaveLength(1);
    expect(here[0].properties.basemapKey).toBe('openfreemap_liberty');
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
    expect(record.template).toEqual([[TEMPLATE_LIBERTY]]);
    expect(record.basemapKey).toBe('openfreemap_liberty');
    // SNOW-844: and the render dependencies, on the same terms — the probe
    // found every one of them in the cache before writing them, so the
    // record now names what this area needs to draw rather than leaving a
    // later probe unable to tell "missing its sprite" from "never said".
    expect(record.deps).toEqual([STYLE_URL_LIBERTY]);
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
    // Display-only: only a value the cache has proven is ever persisted,
    // and this one is an inference from a sibling record.
    expect(record.basemapKey).toBeUndefined();
  });
});

// SNOW-1453: a region carrying the terrain flag is complete only once its
// elevation tiles are held too, and the roundel's heal stamps the record
// the 3D view reads offline, telling it so.
describe('elevation tiles on a region with terrain data (SNOW-1453)', () => {
  let toggle;
  let heard;
  const onChanged = () => {
    heard += 1;
  };

  /** Seed a terrain-flagged record and its bucket; `elevation` adds those tiles. */
  function seedRegion(regionId, name, z, elevation) {
    const core = self.pwaBasemapDownloadCore;
    const record = {
      region_id: regionId,
      name: name,
      band: [13, 14],
      z: z,
      template: [[TEMPLATE_LIBERTY]],
      basemapKey: 'openfreemap_liberty',
      deps: [STYLE_URL_LIBERTY],
      terrain: true,
      bytes: 1024 * 1024,
      savedAt: '2026-11-01T10:00:00.000Z',
    };
    dbRows.get('basemap.regions').push(record);
    const dem = core.areaElevationTileURLs(record, DEM_TEMPLATE, self.pwaTerrainFilterCore.COVERAGE_BOUNDS);
    expect(dem.length).toBeGreaterThan(0);
    cacheBuckets.push({
      name: 'snowdesk-basemap-pinned-region-' + regionId,
      urls: [...zRowURLs(TEMPLATE_LIBERTY, z), STYLE_URL_LIBERTY, ...(elevation ? dem : [])],
    });
  }

  const storedRecord = (regionId) =>
    dbRows.get('basemap.regions').find((entry) => entry.region_id === regionId);

  beforeAll(() => {
    // Created after boot, as only the template it carries is read here.
    toggle = document.createElement('button');
    toggle.id = 'map-view-toggle';
    toggle.dataset.demTileUrl = DEM_TEMPLATE;
    document.body.appendChild(toggle);
    document.addEventListener('snowdesk:downloads-changed', onChanged);
    seedRegion(REGION_ELEVATION_HELD, 'Wildstrubel', Z_HELD, true);
    seedRegion(REGION_ELEVATION_MISSING, 'Gstaad', Z_MISSING, false);
  });

  afterAll(() => {
    toggle.remove();
    document.removeEventListener('snowdesk:downloads-changed', onChanged);
  });

  it('stamps a region holding every elevation tile, and tells the 3D view', async () => {
    heard = 0;
    const btn = document.getElementById('map-download-control');
    selectRegion(REGION_ELEVATION_HELD, 'Wildstrubel');
    await waitFor(() => btn.dataset.downloadState === 'done' && storedRecord(REGION_ELEVATION_HELD).elevation);

    expect(btn.dataset.downloadState).toBe('done');
    expect(storedRecord(REGION_ELEVATION_HELD).elevation).toBe(DEM_TEMPLATE);
    await waitFor(() => heard > 0);
    expect(heard).toBeGreaterThan(0);
  });

  it('reads a region missing its elevation tiles as incomplete, and stamps nothing', async () => {
    const btn = document.getElementById('map-download-control');
    selectRegion(REGION_ELEVATION_MISSING, 'Gstaad');
    await waitFor(() => btn.dataset.downloadState === 'incomplete');

    expect(btn.dataset.downloadState).toBe('incomplete');
    expect(storedRecord(REGION_ELEVATION_MISSING).elevation).toBeUndefined();
  });

  it('never overwrites a stamp from an older template', async () => {
    const old = 'https://dem.example.invalid/dem/2025-11/{z}/{x}/{y}.webp';
    storedRecord(REGION_ELEVATION_HELD).elevation = old;
    heard = 0;
    const btn = document.getElementById('map-download-control');
    selectRegion(REGION_ELEVATION_MISSING, 'Gstaad');
    await waitFor(() => btn.dataset.downloadState === 'incomplete');
    selectRegion(REGION_ELEVATION_HELD, 'Wildstrubel');
    await waitFor(() => btn.dataset.downloadState === 'done');

    expect(storedRecord(REGION_ELEVATION_HELD).elevation).toBe(old);
    expect(heard).toBe(0);
  });
});
