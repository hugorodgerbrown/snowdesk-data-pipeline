/*
 * tests/js/test_map_area_sync.js — one press makes one downloaded area
 * wholly current (SNOW-951).
 *
 * `window.pwaBasemapDownloads.syncArea` is the entry point every "sync
 * now" control reaches — the Manage downloads sheet's menu item and the
 * network menu's per-area button — and it owns a SEQUENCE rather than a
 * fetch of its own: mend the tiles if any are missing, then refetch the
 * content whatever happened to the tiles and whatever the record claims
 * about freshness.
 *
 * That sequence is invisible to every unit test underneath it. `repair`
 * and `refreshAreaContent` both end in a warm-cache call, so only the
 * posted URL LISTS tell the two halves apart — which is why this suite
 * boots the real bundle over a stubbed Cache Storage and reads
 * `pwaWarmCache.mock.calls` rather than stubbing either half away. The
 * three things it exists to catch are all orderings: a sync that repairs
 * tiles that are already on disk (megabytes, over the connection the whole
 * feature exists for), a sync that skips the content half because the
 * record says it is fresh (the guarantee is the point — see `syncArea`'s
 * own docstring), and a tile failure taking the bulletins down with it.
 *
 * Harness follows tests/js/test_map_download_content.js — see its header
 * for the jsdom-boot rationale, and for why the fixture's blob and
 * geometry have to describe the same ground.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const REGION_SLUG = 'martigny-verbier';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const TODAY = '2026-01-06';
const AREA_ID = 'custom-a1';

// The three render dependencies a record names. Whether each is on disk is
// what decides the tile half, so they are moved in and out of the stubbed
// bucket per test rather than being fixed here.
const STYLE_URL = 'https://tiles.example.invalid/liberty.json';
const TILEJSON_URL = 'https://tiles.example.invalid/base/tiles.json';
const SPRITE_URL = 'https://tiles.example.invalid/sprites/liberty.json';
const DEPS = [STYLE_URL, TILEJSON_URL, SPRITE_URL];

/** One region, whose polygon is the ground the seeded area covers. */
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

/** One weather location inside that ground, so the content plan is not empty. */
const WEATHER_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { short_id: 'INSIDEaaaaa', name: 'Mont Fort' },
      geometry: { type: 'Point', coordinates: [7.1, 46.1] },
    },
  ],
};

const EMPTY_GEOJSON = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — as test_map_download_content.js's. */
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
    openBucket,
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

/** The overlay cache the content half writes its feeds through. */
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
 * `#season-scrubber`'s `data-today` is load-bearing rather than
 * decoration: without it there is no day to take a bulletin for and the
 * content plan comes back empty, which `syncArea` would then report as a
 * complete answer rather than a refetch.
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
          data-basemap-url="${STYLE_URL}"
          aria-checked="true"
        >OpenFreeMap</button>
      </li>
    </ul>
    <div id="map-download-error-toast" class="hidden"></div>`;
}

let cachesStub;

/**
 * Seed one custom area over the fixture's ground.
 *
 * A CUSTOM area rather than a region for one reason: it is the kind with
 * no roundel of its own, so `syncArea` is the only way it has of becoming
 * current, and a bug here costs that kind everything. `contentAt` is
 * TODAY'S stamp by default — the fresh case — because the unconditional
 * refetch is the decision this ticket took and the one most likely to be
 * optimised back out by a later reader.
 */
async function seedArea(extra) {
  await window.pwaDb.put('meta:app', {
    key: 'basemap.customAreas',
    value: [
      Object.assign(
        {
          id: AREA_ID,
          ordinal: 1,
          bbox: [7.0, 46.0, 7.2, 46.2],
          band: [10, 14],
          template: TEMPLATE,
          basemapKey: 'openfreemap_liberty',
          deps: DEPS,
          bytes: 4096,
          savedAt: '2026-01-05T10:00:00.000Z',
          contentAt: `${TODAY}T06:00:00.000Z`,
        },
        extra || {},
      ),
    ],
  });
}

/** Put `urls` in the area's own pinned bucket, and nothing else. */
function holdDependencies(urls) {
  cachesStub.buckets.clear();
  const bucket = cachesStub.openBucket(PINNED_PREFIX + AREA_ID);
  for (const url of urls) bucket.add(url);
}

/** Every url posted to the warm-cache on the Nth run. */
function warmedUrls(callIndex) {
  return window.pwaWarmCache.mock.calls[callIndex][0];
}

/** The stored custom-area record, or undefined. */
async function storedArea() {
  const row = await window.pwaDb.get('meta:app', 'basemap.customAreas');
  const list = Array.isArray(row && row.value) ? row.value : [];
  return list.find((entry) => entry && entry.id === AREA_ID);
}

/** The bulletin URL for the one region inside the seeded area. */
const BULLETIN_URL = `/${REGION_ID.toLowerCase()}/${REGION_SLUG}/${TODAY}/`;

beforeAll(async () => {
  buildFixture();
  const mapStub = stubMapLibre();
  cachesStub = installCachesStub();
  installDbStub();
  installOverlayCacheStub();
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
      if (href.includes('weather.geojson')) body = WEATHER_GEOJSON;
      if (href.includes('favourites.geojson')) body = EMPTY_GEOJSON;
      if (href.includes('routes.geojson')) body = EMPTY_GEOJSON;
      if (href.includes('community-reports.geojson')) body = EMPTY_GEOJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url, {});
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

beforeEach(async () => {
  window.pwaWarmCache.mockClear();
  await seedArea();
  holdDependencies(DEPS);
});

describe('the tile half', () => {
  it('fetches nothing when every dependency is already on disk', async () => {
    // The healthy case, and it is the common one: tiles are permanent, so
    // an area that drew yesterday draws today. Re-fetching them would cost
    // megabytes over the connection this feature exists for.
    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('none');
    expect(window.pwaWarmCache).toHaveBeenCalledTimes(1);
    expect(warmedUrls(0)).not.toContain(TILEJSON_URL);
  });

  it('fetches only what is missing, before the content', async () => {
    // Not the whole dependency list, and not a re-download: the same short
    // path Repair takes, whose eviction confirm could otherwise destroy
    // another area to make room for a sprite.
    holdDependencies([STYLE_URL, SPRITE_URL]);

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('ok');
    expect(window.pwaWarmCache).toHaveBeenCalledTimes(2);
    expect(warmedUrls(0)).toEqual([TILEJSON_URL]);
    expect(warmedUrls(1)).toContain(BULLETIN_URL);
  });

  it('claims nothing for an area whose basemap is not the one on screen', async () => {
    // The third row of the resolution rule (`areaRenderDependencyURLs`): a
    // record naming no dependencies, on a style that is not loaded, cannot
    // be judged — so the tile half declines rather than accusing it, and
    // the content half still runs, which is everything this can honestly
    // do for such an area.
    holdDependencies([]);
    await seedArea({ deps: [], basemapKey: 'swisstopo_winter' });

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('none');
    expect(result.content).toBe(true);
    expect(window.pwaWarmCache).toHaveBeenCalledTimes(1);
  });
});

describe('the content half', () => {
  it('refetches even when the record says it is fresh', async () => {
    // The decision this ticket rests on. Every freshness reading is an
    // inference from a stamp, and the thing the user is about to rely on
    // is the data — so a press is a guarantee, not a saving.
    const record = await storedArea();
    expect(record.contentAt.startsWith(TODAY)).toBe(true);

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.content).toBe(true);
    expect(warmedUrls(0)).toContain(BULLETIN_URL);
  });

  it('runs even when the tile half failed', async () => {
    // They are independent remedies. A repair that fails leaves an area
    // whose map will not draw; refusing its bulletins on that account
    // would cost the user both halves for the sake of a tidy report.
    holdDependencies([STYLE_URL, SPRITE_URL]);
    window.pwaWarmCache.mockImplementationOnce(async () => ({
      ok: 0,
      failed: 1,
      bytes: 0,
      cancelled: false,
    }));

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('failed');
    expect(result.content).toBe(true);
    expect(warmedUrls(1)).toContain(BULLETIN_URL);
  });

  it('takes no tiles with it', async () => {
    // The promise the whole content path is built on — kilobytes of HTML,
    // never megabytes of tiles.
    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.content).toBe(true);
    expect(warmedUrls(0).some((url) => url.includes('tiles.example.invalid'))).toBe(false);
  });

  it('stamps the record, so every other surface reads the new age', async () => {
    await seedArea({ contentAt: '2026-01-02T10:00:00.000Z', contentIncomplete: true });

    await window.pwaBasemapDownloads.syncArea(AREA_ID);

    const record = await storedArea();
    expect(record.contentAt.startsWith(TODAY)).toBe(false);
    // The write rule is `refreshAreaContent`'s, unchanged: DELETE on
    // success, never set false.
    expect('contentIncomplete' in record).toBe(false);
    expect(typeof record.contentAt).toBe('string');
  });
});

describe('an id nothing on this device carries', () => {
  it('fetches nothing and says so in both halves', async () => {
    // An orphaned bucket, or a row the account knows about and this device
    // has never held. There is no record to name what to fetch.
    const result = await window.pwaBasemapDownloads.syncArea('custom-nope');

    expect(result).toEqual({ tiles: 'none', content: false });
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });
});
