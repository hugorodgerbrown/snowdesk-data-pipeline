/*
 * tests/js/test_map_download_content.js — a download carries the bulletins
 * and weather inside its boundary, and a downloaded area refreshes them
 * (SNOW-924).
 *
 * The core's resolver is unit-tested in `test_basemap_download_core.js` and
 * the runner's ordering in `test_basemap_download_runner.js`. This is the
 * glue between them, and the two promises neither of those can make:
 *
 *   - the four overlay feeds reach `data:map_overlays` as a side effect of
 *     a download, so a device that has downloaded an area has the
 *     favourites, routes, observations and weather to draw on it;
 *   - a tap on a DOWNLOADED area refreshes its content and does NOT
 *     re-fetch its tiles. That is the whole point of the refresh glyph, it
 *     is the most user-visible thing this ticket does, and it is invisible
 *     to every unit test — `run` and `repair` both end in a warm-cache
 *     call, and only the URL list tells them apart.
 *
 * Harness follows tests/js/test_map_region_download_cancelled.js (see its
 * header, and test_map_download_bytes.js's, for the jsdom-boot rationale).
 *
 * ONE DIFFERENCE from those, and it matters: their REGION_BLOB describes
 * tiles nowhere near their region's polygon, which is harmless when nothing
 * compares the two. Here the resolver derives the area's rectangle from the
 * blob and selects regions against it, so the fixture's blob and geometry
 * have to describe the same ground. The values below are the golden
 * vector's — bbox 7.0,46.0 → 7.2,46.2 at the micro band.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const REGION_SLUG = 'martigny-verbier';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const TODAY = '2026-01-06';

/** One region, whose polygon is the ground REGION_BLOB's tiles cover. */
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: REGION_ID,
        name: 'Martigny — Verbier',
        slug: REGION_SLUG,
        download: {
          count: 205,
          mb: 13,
          over_ceiling: false,
          centre_tile: { z: 14, x: 8515, y: 5822 },
        },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.2, 46.0], [7.2, 46.2], [7.0, 46.2], [7.0, 46.0]]],
      },
    },
  ],
};

/** The blob for that same ground — the golden vector's z14 row. */
const REGION_BLOB = {
  band: [10, 14],
  count: 205,
  mb: 13,
  over_ceiling: false,
  centre_tile: { z: 14, x: 8515, y: 5822 },
  z: { 14: [8510, 8519, 5815, 5828] },
};

/** Two weather locations: one inside the area's ground, one in Graubünden. */
const WEATHER_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { short_id: 'INSIDEaaaaa', name: 'Mont Fort' },
      geometry: { type: 'Point', coordinates: [7.1, 46.1] },
    },
    {
      type: 'Feature',
      properties: { short_id: 'OUTSIDEbbbb', name: 'Davos' },
      geometry: { type: 'Point', coordinates: [9.8, 46.8] },
    },
  ],
};

const FAVOURITES_GEOJSON = { type: 'FeatureCollection', features: [] };
const ROUTES_GEOJSON = { type: 'FeatureCollection', features: [] };
const REPORTS_GEOJSON = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — as test_map_region_download_cancelled.js's. */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: () => null,
    getFilter: () => null,
    getLayoutProperty: () => null,
    getPaintProperty: () => null,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => (id === 'basemap' ? { tiles: [TEMPLATE] } : null),
    addSource: () => {},
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
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

/** Cache Storage stub over `name -> Set<url>`. */
function installCachesStub() {
  const buckets = new Map();
  const openBucket = (name) => {
    if (!buckets.has(name)) buckets.set(name, new Set());
    return buckets.get(name);
  };
  const stub = {
    buckets,
    keys: vi.fn(async () => [...buckets.keys()]),
    open: vi.fn(async (name) => {
      const store = openBucket(name);
      return {
        keys: async () => [...store].map((url) => ({ url })),
        put: vi.fn(async (url) => {
          store.add(url);
        }),
        match: async (request) => {
          const url = typeof request === 'string' ? request : request.url;
          return store.has(url) ? { headers: { get: () => null } } : undefined;
        },
      };
    }),
    delete: vi.fn(async (name) => buckets.delete(name)),
  };
  Object.defineProperty(window, 'caches', { value: stub, configurable: true, writable: true });
  return stub;
}

/** In-memory `meta:app`. */
function installDbStub() {
  const rows = new Map();
  window.pwaDb = {
    rows,
    get: vi.fn(async (_store, key) => (rows.has(key) ? { key, value: rows.get(key) } : undefined)),
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

/** The overlay cache the download writes its feeds through. */
function installOverlayCacheStub() {
  const stored = new Map();
  Object.defineProperty(window, 'pwaMapOverlayCache', {
    value: {
      stored,
      putOverlay: vi.fn(async (resource, geojson) => {
        stored.set(resource, geojson);
      }),
      getOverlay: vi.fn(async (resource) => stored.get(resource) || null),
    },
    configurable: true,
    writable: true,
  });
  return stored;
}

/**
 * The DOM the boot needs.
 *
 * `#season-scrubber`'s `data-today` is load-bearing here rather than
 * decoration: `downloadContentDays` reads today through map_shared.js's
 * `readTodayDateParam`, and without it there is no day to take a bulletin
 * for and the content list comes back empty.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-weather-url="/api/weather.geojson"
         data-weather-detail-url="/api/weather/__SHORTID__/detail/"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
         data-favourites-url="/api/favourites.geojson"
         data-favourites-eligible="true"
         data-routes-url="/api/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="season-scrubber" data-today="${TODAY}" data-today-pct="50"
         data-season-start="2025-11-01" data-season-end="2026-05-31" data-state="ready">
      <div class="season-scrubber-track"><div class="season-scrubber-thumb"></div></div>
      <div class="season-scrubber-loading"></div>
    </div>
    <button id="map-download-control" type="button"></button>
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
        >OpenFreeMap</button>
      </li>
    </ul>
    <div id="map-download-error-toast" class="hidden"></div>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/** The recorded `basemap.regions` entry, or undefined. */
async function recordedRegion() {
  const row = await window.pwaDb.get('meta:app', 'basemap.regions');
  const list = Array.isArray(row && row.value) ? row.value : [];
  return list.find((entry) => entry && entry.region_id === REGION_ID);
}

/** Every url posted to the warm-cache on the Nth run. */
function warmedUrls(callIndex) {
  return window.pwaWarmCache.mock.calls[callIndex][0];
}

let mapStub;
let overlayStore;

/** Select the region and click the control, settling out of 'busy'. */
async function clickControl() {
  const btn = document.getElementById('map-download-control');
  document.dispatchEvent(
    new CustomEvent('snowdesk:region-selected', {
      detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
    }),
  );
  await waitFor(() => btn.dataset.downloadState && btn.dataset.downloadState !== 'busy');
  btn.click();
  await waitFor(() => btn.dataset.downloadState !== 'busy');
  return btn;
}

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  installCachesStub();
  installDbStub();
  overlayStore = installOverlayCacheStub();
  window.pwaConnectivity = { isOnline: () => true };
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
      if (href.includes('region-basemap-tiles')) body = REGION_BLOB;
      if (href.includes('weather.geojson')) body = WEATHER_GEOJSON;
      if (href.includes('favourites.geojson')) body = FAVOURITES_GEOJSON;
      if (href.includes('routes.geojson')) body = ROUTES_GEOJSON;
      if (href.includes('community-reports.geojson')) body = REPORTS_GEOJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url, {});
    // Every url reported as settled, so the content tally reads complete.
    if (options.onProgress) {
      options.onProgress(urls.length, urls.length, urls.map((_u, i) => i), 4096);
    }
    return { ok: urls.length, failed: 0, bytes: 4096, cancelled: false };
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/calendar_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete window.pwaConnectivity;
  delete window.pwaMapOverlayCache;
  delete globalThis.maplibregl;
});

describe('a download takes the content inside its boundary', () => {
  // One download, several questions about it. Run once here rather than
  // per test: the whole point is what a SINGLE run posts, and re-clicking
  // between assertions would be asking about a different run each time.
  let posted;

  beforeAll(async () => {
    await clickControl();
    posted = warmedUrls(0);
  });

  it('posts the bulletin for the region the area covers', () => {
    expect(posted).toContain(`/${REGION_ID.toLowerCase()}/${REGION_SLUG}/${TODAY}/`);
  });

  it('posts the weather sheet for a location inside, and not one outside', () => {
    // The whole feed is cached either way — it is one small request. What
    // the boundary narrows is the per-location sheets, of which there are
    // ~550 across the estate.
    expect(posted).toContain('/api/weather/INSIDEaaaaa/detail/');
    expect(posted).not.toContain('/api/weather/OUTSIDEbbbb/detail/');
  });

  it('puts content ahead of the tiles in the posted list', () => {
    const firstTile = posted.findIndex((url) => url.includes('tiles.example.invalid'));
    const bulletin = posted.indexOf(`/${REGION_ID.toLowerCase()}/${REGION_SLUG}/${TODAY}/`);

    expect(bulletin).toBeGreaterThanOrEqual(0);
    expect(firstTile).toBeGreaterThan(bulletin);
  });

  it('caches all four overlay feeds on the way past', () => {
    // Whole and unfiltered — the boundary decides what must VERIFY
    // present, not what gets stored.
    expect([...overlayStore.keys()].sort()).toEqual([
      'community_reports',
      'favourites',
      'routes',
      'weather',
    ]);
  });

  it('stamps the record with when its content was fetched', async () => {
    const record = await recordedRegion();

    expect(record).toBeTruthy();
    expect(typeof record.contentAt).toBe('string');
    // Separate from savedAt because the two halves age differently — the
    // tiles never do, the bulletins do daily.
    expect(record.savedAt).toBeTruthy();
  });
});

describe('a downloaded area refreshes rather than re-downloads', () => {
  it('re-fetches the content and not one tile', async () => {
    // THE promise of the refresh glyph. `run` and `repair` both end in a
    // warm-cache call, so only the posted list tells them apart — and a
    // refresh that quietly re-fetched the tiles would cost megabytes over
    // the connection this whole feature exists for.
    const btn = document.getElementById('map-download-control');
    await waitFor(() => btn.dataset.downloadState === 'done');
    window.pwaWarmCache.mockClear();

    btn.click();
    await waitFor(() => window.pwaWarmCache.mock.calls.length > 0);

    const urls = warmedUrls(0);
    expect(urls).toContain(`/${REGION_ID.toLowerCase()}/${REGION_SLUG}/${TODAY}/`);
    expect(urls.some((url) => url.includes('tiles.example.invalid'))).toBe(false);
  });
});
