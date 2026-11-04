/*
 * tests/js/test_map_area_sync.js — one press makes one downloaded area
 * wholly current (SNOW-951).
 *
 * `window.pwaBasemapDownloads.syncArea` is the entry point every "sync
 * now" control reaches — the Manage downloads sheet's menu item and the
 * network menu's per-area button — and it owns a SEQUENCE rather than a
 * fetch of its own: mend the tiles if any are missing, then fetch the
 * content whatever happened to the tiles. SNOW-1416: only the content that
 * changed — what the bucket lacks, plus its held bulletins once the area
 * is more than an hour stale.
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
 * SNOW-1072 adds the terrain-class tiles to the tile half: an area whose
 * record carries `terrain` is judged on them, a sync refetches the missing
 * ones into its own bucket, and an area without the flag is never judged
 * on them at all. The fixture's `#map` carries a class-tile template on
 * its own host for that reason — every area here without the flag must
 * fetch nothing from it.
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
// The worker's shell cache, where a warm puts same-origin content.
const SHELL_CACHE = 'snowdesk-shell-test';

/** A url made absolute against the page, as the cache keys compare. */
function absolute(url) {
  return new URL(url, window.location.origin).href;
}
const TODAY = '2026-01-06';
const AREA_ID = 'custom-a1';

// The three render dependencies a record names. Whether each is on disk is
// what decides the tile half, so they are moved in and out of the stubbed
// bucket per test rather than being fixed here.
const STYLE_URL = 'https://tiles.example.invalid/liberty.json';
const TILEJSON_URL = 'https://tiles.example.invalid/base/tiles.json';
const SPRITE_URL = 'https://tiles.example.invalid/sprites/liberty.json';
const DEPS = [STYLE_URL, TILEJSON_URL, SPRITE_URL];

// SNOW-1072: the terrain-class tileset, on its own host so the assertions
// below can tell its tiles apart from everything else a run posts.
const TERRAIN_TEMPLATE = 'https://terrain.example.invalid/terrain-class/v2/{z}/{x}/{y}.png';
const isClassTile = (url) => url.startsWith('https://terrain.example.invalid/');

/**
 * What `/api/area-content/` answers for the seeded area.
 *
 * SNOW-953 moved the SELECTION of what a boundary contains to the server,
 * so this — not the client's own region features — is what decides the
 * content plan. A function so a test can make the endpoint fail, which is
 * the one state that separates an empty plan from a short one.
 */
let areaContentAnswer = () => ({
  regions: [{ id: REGION_ID, slug: REGION_SLUG }],
  weather: [],
  resorts: [],
});

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

/**
 * The worker in control, answering the page's `held-content` question as
 * `sw.js`'s `_heldForOffline` does for public content: held means in the
 * live shell cache, and no other. `silent` makes it never answer.
 */
function installWorkerStub(cachesStub) {
  const worker = {
    silent: false,
    asked: [],
    postMessage: vi.fn((data, ports) => {
      if (!data || data.type !== 'held-content') return;
      worker.asked.push(data.urls);
      if (worker.silent) return;
      const live = cachesStub.openBucket(SHELL_CACHE);
      const hrefs = new Set([...live].map(absolute));
      const held = data.urls.filter((url) => hrefs.has(absolute(url)));
      queueMicrotask(() => ports[0].onmessage({ data: { type: 'held-content', held } }));
    }),
  };
  Object.defineProperty(navigator, 'serviceWorker', {
    value: {
      controller: worker,
      // Never settles: the boot's own worker waits are not this suite's.
      ready: new Promise(() => {}),
      addEventListener: () => {},
      removeEventListener: () => {},
    },
    configurable: true,
  });
  // jsdom has no MessageChannel the stub can answer through synchronously.
  vi.stubGlobal(
    'MessageChannel',
    class {
      constructor() {
        this.port1 = { onmessage: null, close: () => {} };
        this.port2 = this.port1;
      }
    },
  );
  return worker;
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
    // SNOW-959: the content stamp rides ONE IndexedDB transaction rather
    // than a `get` and a later `put` — three functions across two
    // simultaneously-open pages rewrite these whole-array rows, and the
    // gap between the pair was a lost update. Stubbed to the CONTRACT its
    // callers depend on: the mutator is handed the current row, and an
    // `undefined` return writes nothing. That it actually serialises is
    // proved against fake-indexeddb in tests/js/test_db.js.
    readModifyWrite: vi.fn(async (_store, key, mutate) => {
      const current = rows.has(key) ? { key, value: rows.get(key) } : undefined;
      const next = mutate(current);
      if (next === undefined) return current;
      rows.set(next.key, next.value);
      return next;
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
         data-area-content-url="/api/area-content/"
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
         data-terrain-class-tile-url="${TERRAIN_TEMPLATE}"
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
let workerStub;

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

/**
 * Poll `predicate` until it holds or the budget runs out.
 *
 * Mirrors `tests/js/test_map_download_content.js`'s helper of the same
 * name — this suite follows that file's harness, and this is the part of
 * it that was missed.
 *
 * @param {() => boolean} predicate
 * @param {number} [timeoutMs]
 * @returns {Promise<boolean>}
 */
async function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
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
  workerStub = installWorkerStub(cachesStub);
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
      if (href.includes('area-content')) {
        const answer = areaContentAnswer();
        if (!answer) return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
        body = answer;
      }
      if (href.includes('weather.geojson')) body = WEATHER_GEOJSON;
      if (href.includes('favourites.geojson')) body = EMPTY_GEOJSON;
      if (href.includes('routes.geojson')) body = EMPTY_GEOJSON;
      if (href.includes('community-reports.geojson')) body = EMPTY_GEOJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  // As the worker does: same-origin content into the shell cache, and
  // only the cross-origin tiles into the area's pinned bucket.
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const pinned = await window.caches.open(PINNED_PREFIX + options.areaId);
    const shell = await window.caches.open(SHELL_CACHE);
    for (const url of urls) {
      const sameOrigin = new URL(url, window.location.origin).origin === window.location.origin;
      await (sameOrigin ? shell : pinned).put(url, {});
    }
    if (options.onProgress) {
      options.onProgress(urls.length, urls.length, urls.map((_u, i) => i), 4096);
    }
    return { ok: urls.length, failed: 0, bytes: 4096, cancelled: false };
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  // SNOW-1072: the class tileset's coverage and band come from here.
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/terrain_filter_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/calendar_core.js');
  // SNOW-1415: the `verified` stamp's shape, loaded globally by base.html.
  await import('../../static/js/offline_journey_core.js');
  // SNOW-1416: what a sync fetches — only what changed.
  await import('../../static/js/offline_autosync_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  // The load handler starts the country load and does not await it, so the
  // regions this area's boundary contains arrive on a floating promise.
  // Every content assertion below depends on them: an empty
  // `featureByRegionId` yields an empty content plan, which warms nothing
  // and stamps nothing — so without this the whole suite is a race the
  // machine wins on a fast box and loses on a two-core runner, which is
  // exactly how it went red on CI while passing locally.
  await waitFor(() => !!window.snowdeskMapState?.featureByRegionId?.[REGION_ID]);
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete window.pwaConnectivity;
  delete window.pwaMapOverlayCache;
  delete globalThis.maplibregl;
});

// SNOW-1415 review: the tile-grid reading `syncArea` takes before it may
// stamp a map Yes. The reading itself is `offline_journey_core.js`'s
// `tileReading`, tested there; here it is a seam, so each test says what
// the bucket's grid holds. A full grid unless a test says otherwise.
let realJourneyCore;
let tilesHeld;

beforeEach(async () => {
  window.pwaWarmCache.mockClear();
  await seedArea();
  holdDependencies(DEPS);
  realJourneyCore = realJourneyCore || window.pwaOfflineJourneyCore;
  tilesHeld = vi.fn(() => true);
  window.pwaOfflineJourneyCore = Object.assign({}, realJourneyCore, { tileReading: tilesHeld });
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
    //
    // It reports `'unknown'`, NOT `'none'`. The two used to be the same
    // answer, and that let a press report "Synced" for an area whose
    // drawing dependencies had never been looked at — the false all-clear
    // this control exists to rule out.
    holdDependencies([]);
    await seedArea({ deps: [], basemapKey: 'swisstopo_winter' });

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('unknown');
    expect(result.content).toBe(true);
    expect(window.pwaWarmCache).toHaveBeenCalledTimes(1);
  });
});

describe('a record whose bucket this device no longer holds', () => {
  it('refuses both halves rather than half-mending it', async () => {
    // A record outliving its pinned bucket is a supported state — the
    // account keeps what you chose, the device keeps what it can. The
    // network menu's list is built from records alone (pwa_offline.js
    // does not load the download core), so such a row reaches here.
    //
    // Neither half of a sync fetches a tile grid, so running it would
    // write a handful of documents into a fresh bucket, satisfy a render
    // check that never inspected the tiles, and report an area as current
    // that cannot draw one. The remedy is a re-download.
    cachesStub.buckets.clear();

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result).toEqual({ tiles: 'absent', content: false });
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });

  it('leaves the content stamp exactly as it found it', async () => {
    // The stamp is what every other surface reads for the area's age. A
    // refusal must not move it, or the report would say the area was
    // brought up to date by the press that declined to touch it.
    await seedArea({ contentAt: '2026-01-02T10:00:00.000Z' });
    cachesStub.buckets.clear();

    await window.pwaBasemapDownloads.syncArea(AREA_ID);

    const record = await storedArea();
    expect(record.contentAt).toBe('2026-01-02T10:00:00.000Z');
  });
});

describe('the content half', () => {
  it('fetches what the bucket lacks, however fresh the record says it is', async () => {
    // SNOW-1416: freshness spares only what is HELD. A bulletin the
    // bucket does not have is fetched whatever the stamp says.
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

describe('a sync is not a change of owner (SNOW-1142)', () => {
  // The record carries the principal it was downloaded under. A sync
  // rewrites the record to move its content stamp, and must carry that
  // principal through untouched — it never re-stamps another account's
  // area, and never adds a stamp to a legacy record, whose owner the
  // claim rule in downloads_sync.js decides.
  it('keeps another account\'s stamp', async () => {
    await seedArea({ principal: 'u-2' });

    await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect((await storedArea()).principal).toBe('u-2');
  });

  it('keeps an anonymous download\'s empty stamp', async () => {
    await seedArea({ principal: '' });

    await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect((await storedArea()).principal).toBe('');
  });

  it('adds no stamp to a legacy record', async () => {
    await seedArea();

    await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect('principal' in (await storedArea())).toBe(false);
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

describe('terrain data (SNOW-1072)', () => {
  /** The class tiles the seeded area's ground implies, derived as the probe does. */
  function expectedClassTiles() {
    const core = self.pwaBasemapDownloadCore;
    const terrain = self.pwaTerrainFilterCore;
    return core.terrainClassTileURLs(
      TERRAIN_TEMPLATE,
      core.buildBlob([7.0, 46.0, 7.2, 46.2], 10, 14),
      terrain.COVERAGE_BOUNDS,
      terrain.MIN_ZOOM,
      terrain.MAX_ZOOM,
    );
  }

  it('derives the class tiles from the record only when it carries the flag', () => {
    const bridge = window.pwaBasemapDownloads;
    const record = { bbox: [7.0, 46.0, 7.2, 46.2], band: [10, 14] };

    expect(bridge.areaTerrainClassTileUrls(record)).toEqual([]);
    const urls = bridge.areaTerrainClassTileUrls({ ...record, terrain: true });
    expect(urls).toEqual(expectedClassTiles());
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.every(isClassTile)).toBe(true);
  });

  it('refetches the missing class tiles of an opted-in area into its own bucket', async () => {
    // A missing class tile leaves the area incomplete, and the sync's tile
    // half is what repairs it — the same short path a missing sprite takes.
    await seedArea({ terrain: true });

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('ok');
    expect(warmedUrls(0)).toEqual(expectedClassTiles());
    for (const url of expectedClassTiles()) {
      expect(cachesStub.buckets.get(PINNED_PREFIX + AREA_ID).has(url)).toBe(true);
    }
  });

  it('fetches only the class tiles that are missing', async () => {
    await seedArea({ terrain: true });
    const all = expectedClassTiles();
    holdDependencies([...DEPS, ...all.slice(1)]);

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('ok');
    expect(warmedUrls(0)).toEqual([all[0]]);
  });

  it('reads an opted-in area holding every class tile as complete', async () => {
    await seedArea({ terrain: true });
    holdDependencies([...DEPS, ...expectedClassTiles()]);

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('none');
    expect(warmedUrls(0).some(isClassTile)).toBe(false);
  });

  it('never judges an area without the flag on class tiles', async () => {
    // The default, and every area downloaded before the ticket: no class
    // tiles on disk, nothing missing, nothing fetched.
    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('none');
    expect(warmedUrls(0).some(isClassTile)).toBe(false);
  });
});

describe('terrain data in every download (SNOW-1072)', () => {
  it('is available here, and on', () => {
    expect(window.pwaBasemapDownloads.terrainDataAvailable()).toBe(true);
    expect(window.pwaBasemapDownloads.terrainDataPreferred()).toBe(true);
  });

  it('reads as off on a page whose terrain filter is not configured', () => {
    const mapEl = document.getElementById('map');
    delete mapEl.dataset.terrainClassTileUrl;

    expect(window.pwaBasemapDownloads.terrainDataAvailable()).toBe(false);
    expect(window.pwaBasemapDownloads.terrainDataPreferred()).toBe(false);
    expect(
      window.pwaBasemapDownloads.areaTerrainClassTileUrls({
        bbox: [7.0, 46.0, 7.2, 46.2],
        band: [10, 14],
        terrain: true,
      }),
    ).toEqual([]);

    mapEl.dataset.terrainClassTileUrl = TERRAIN_TEMPLATE;
  });
});

describe('what a sync tells the connection panel (SNOW-1415)', () => {
  it('names each stage as it reaches it, in order', async () => {
    const stages = [];
    await window.pwaBasemapDownloads.syncArea(AREA_ID, (name) => stages.push(name));
    const order = [...new Set(stages)];
    expect(order[0]).toBe('map');
    expect(order).toContain('bulletins');
    expect(order.indexOf('map')).toBeLessThan(order.indexOf('bulletins'));
  });

  it('stamps the map and content answers it learned on the record', async () => {
    const meta = document.createElement('meta');
    meta.name = 'pwa-user-id';
    meta.content = '42';
    document.head.appendChild(meta);
    try {
      await window.pwaBasemapDownloads.syncArea(AREA_ID);
    } finally {
      meta.remove();
    }
    const record = await storedArea();
    expect(record.verified).toMatchObject({ map: 'yes', content: 'yes', principal: '42' });
    expect(typeof record.verified.at).toBe('string');
  });

  it('stamps no content answer for a signed-out sync', async () => {
    // A content answer is shown back only to the account it was read for,
    // and a signed-out sync has none.
    await window.pwaBasemapDownloads.syncArea(AREA_ID);
    const record = await storedArea();
    expect(record.verified).toMatchObject({ map: 'yes' });
    expect(record.verified.content).toBeUndefined();
  });

  it('reads the tile grid over this bucket before it says the map is here', async () => {
    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result).toMatchObject({ tiles: 'none', tilesHeld: true });
    const [area, core] = tilesHeld.mock.calls[0];
    expect(area).toMatchObject({ template: TEMPLATE, bbox: [7.0, 46.0, 7.2, 46.2], band: [10, 14] });
    expect(area.entries).toEqual(expect.arrayContaining(DEPS));
    expect(core).toBe(window.pwaBasemapDownloadCore);
  });

  it('stamps a map No when the dependencies are here but the tile grid is short', async () => {
    // The Codex review case: a bucket holding its style, sprites and
    // glyphs but missing tiles passes the render check and still draws
    // nothing. The panel must not say "Works offline" for it.
    tilesHeld.mockReturnValue(false);
    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('incomplete');
    const record = await storedArea();
    expect(record.verified).toMatchObject({ map: 'no', reason: 'tiles' });
  });

  it('leaves the map answer alone when the grid cannot be read', async () => {
    await seedArea({ verified: { map: 'no', reason: 'tiles', at: '2026-01-01T00:00:00.000Z' } });
    tilesHeld.mockReturnValue(null);
    await window.pwaBasemapDownloads.syncArea(AREA_ID);

    const record = await storedArea();
    expect(record.verified).toMatchObject({ map: 'no', reason: 'tiles' });
  });

  it('stamps a map No for an area whose bucket is gone', async () => {
    await window.caches.delete(PINNED_PREFIX + AREA_ID);
    await window.pwaBasemapDownloads.syncArea(AREA_ID);
    const record = await storedArea();
    expect(record.verified).toMatchObject({ map: 'no', reason: 'absent' });
    expect(record.verified.content).toBeUndefined();
  });
});

describe('only what changed (SNOW-1416)', () => {

  /** Fill the bucket with a first sync's documents, then forget the call. */
  async function syncOnce() {
    await seedArea({ contentAt: undefined });
    await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);
    window.pwaWarmCache.mockClear();
  }

  it('fetches nothing for a fresh area that holds its whole plan, and keeps its stamp', async () => {
    await syncOnce();
    const justNow = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    await seedArea({ contentAt: justNow });

    const ok = await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(ok).toBe(true);
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
    expect((await storedArea()).contentAt).toBe(justNow);
  });

  it('records a short plan on a fresh area that holds all it resolved, and keeps its stamp', async () => {
    // Codex review: the trips-and-pins endpoint fails, the bulletins are all
    // held, so the delta is empty; the shortfall must still reach the record.
    await syncOnce();
    const justNow = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    await seedArea({ contentAt: justNow });
    const mapEl = document.getElementById('map');
    mapEl.dataset.areaItemsUrl = '/api/area-items/';
    const fetchStub = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn((url) =>
        String(url).includes('area-items')
          ? Promise.resolve({ ok: false, json: () => Promise.resolve({}) })
          : fetchStub(url),
      ),
    );
    try {
      const ok = await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

      expect(ok).toBe(false);
      expect(window.pwaWarmCache).not.toHaveBeenCalled();
      const stored = await storedArea();
      expect(stored.contentIncomplete).toBe(true);
      expect(stored.contentAt).toBe(justNow);
    } finally {
      delete mapEl.dataset.areaItemsUrl;
      vi.stubGlobal('fetch', fetchStub);
    }
  });

  it('refetches the held bulletins of an area more than an hour stale', async () => {
    await syncOnce();
    await seedArea({ contentAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() });

    await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(warmedUrls(0).map(absolute)).toContain(absolute(BULLETIN_URL));
  });

  it('refetches the held bulletins of an area whose last content run fell short', async () => {
    await syncOnce();
    await seedArea({ contentAt: new Date().toISOString(), contentIncomplete: true });

    await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(warmedUrls(0).map(absolute)).toContain(absolute(BULLETIN_URL));
  });

  it('sends only the missing url to a fresh area, and keeps its stamp', async () => {
    await syncOnce();
    // The worker keeps same-origin content in its shell cache.
    const bucket = cachesStub.openBucket(SHELL_CACHE);
    const held = [...bucket].find((url) => absolute(url) === absolute(BULLETIN_URL));
    bucket.delete(held);
    const justNow = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    await seedArea({ contentAt: justNow });

    await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(warmedUrls(0).map(absolute)).toEqual([absolute(BULLETIN_URL)]);
    expect((await storedArea()).contentAt).toBe(justNow);
  });

  it('refetches what only a retired shell cache holds', async () => {
    // Codex review: after an update that could not carry every entry, the
    // old cache is kept, but the worker serves only from its own.
    await syncOnce();
    const live = cachesStub.openBucket(SHELL_CACHE);
    const retired = cachesStub.openBucket('snowdesk-shell-retired');
    for (const url of live) retired.add(url);
    live.clear();
    await seedArea({ contentAt: new Date(Date.now() - 5 * 60 * 1000).toISOString() });

    await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(warmedUrls(0).map(absolute)).toContain(absolute(BULLETIN_URL));
  });

  it('asks the worker in control, and fetches the whole plan when it does not answer', async () => {
    await syncOnce();
    await seedArea({ contentAt: new Date(Date.now() - 5 * 60 * 1000).toISOString() });
    workerStub.silent = true;
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      const run = window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);
      await vi.waitFor(() => expect(workerStub.asked.length).toBeGreaterThan(0));
      await vi.advanceTimersByTimeAsync(5000);
      await run;
    } finally {
      vi.useRealTimers();
      workerStub.silent = false;
    }

    expect(workerStub.asked.at(-1).map(absolute)).toContain(absolute(BULLETIN_URL));
    expect(warmedUrls(0).map(absolute)).toContain(absolute(BULLETIN_URL));
  });

  it('leaves the tile half as it was: a whole map fetches no tiles', async () => {
    await syncOnce();
    await seedArea({ contentAt: new Date().toISOString() });

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('none');
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });

  it('still mends a missing map, then fetches no held content', async () => {
    await syncOnce();
    const bucket = cachesStub.openBucket(PINNED_PREFIX + AREA_ID);
    bucket.delete(TILEJSON_URL);
    await seedArea({ contentAt: new Date().toISOString() });

    const result = await window.pwaBasemapDownloads.syncArea(AREA_ID);

    expect(result.tiles).toBe('ok');
    expect(window.pwaWarmCache).toHaveBeenCalledTimes(1);
    expect(warmedUrls(0)).toEqual([TILEJSON_URL]);
  });
});

describe('SAC tours in an area (SNOW-1417)', () => {
  const TOURS_URL = '/api/ski-tours.geojson';
  const NIL = '00000000-0000-0000-0000-000000000000';
  const BOX_URL = `${TOURS_URL}?bbox=7,46,7.2,46.2`;
  const tour = (uuid) => ({ type: 'Feature', properties: { kind: 'tour', uuid, name: uuid } });

  /** Run one content sync with the tours endpoint answering `answer`. */
  async function syncWithTours(answer) {
    const mapEl = document.getElementById('map');
    mapEl.dataset.skiToursUrl = TOURS_URL;
    mapEl.dataset.skiTourFeatureUrl = `/routes/ski-tours/${NIL}/feature.json`;
    mapEl.dataset.skiTourTerrainUrl = `/routes/ski-tours/${NIL}/terrain.json`;
    const fetchStub = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn((url) =>
        String(url).startsWith(TOURS_URL)
          ? Promise.resolve({ ok: true, json: () => Promise.resolve(answer) })
          : fetchStub(url),
      ),
    );
    try {
      await seedArea({ contentAt: undefined });
      window.pwaWarmCache.mockClear();
      return await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);
    } finally {
      delete mapEl.dataset.skiToursUrl;
      delete mapEl.dataset.skiTourFeatureUrl;
      delete mapEl.dataset.skiTourTerrainUrl;
      vi.stubGlobal('fetch', fetchStub);
    }
  }

  it("warms the area's tours box, the overview and each tour's two files, and stamps them", async () => {
    const ok = await syncWithTours({ features: [tour('u1'), tour('u1'), tour('u2')] });

    expect(ok).toBe(true);
    const warmed = warmedUrls(0);
    expect(warmed).toEqual(
      expect.arrayContaining([
        `${TOURS_URL}?overview=1`,
        BOX_URL,
        '/routes/ski-tours/u1/feature.json',
        '/routes/ski-tours/u1/terrain.json',
        '/routes/ski-tours/u2/feature.json',
        '/routes/ski-tours/u2/terrain.json',
      ]),
    );
    const stored = await storedArea();
    expect(stored.items.tours.map((t) => t.uuid)).toEqual(['u1', 'u2']);
    expect(stored.items.tourBoxes).toEqual([`${TOURS_URL}?overview=1`, BOX_URL]);
    expect(stored.contentIncomplete).toBeUndefined();
  });

  it('records the run as short when the box came back truncated', async () => {
    const ok = await syncWithTours({ features: [tour('u1')], truncated: true });

    expect(ok).toBe(false);
    expect((await storedArea()).contentIncomplete).toBe(true);
  });
});

describe('resorts and routes in an area (SNOW-1418)', () => {
  const RESORT_SHEET = '/partials/place-sheet/resort/__SLUG__/';
  const ROUTE = (uuid, coords) => ({
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: coords },
    properties: { uuid },
  });

  /** One content sync, signed in as `principal`, with the routes feed `routes`. */
  async function syncSignedIn(principal, routes) {
    const mapEl = document.getElementById('map');
    mapEl.dataset.resortSheetUrl = RESORT_SHEET;
    mapEl.dataset.areaItemsUrl = '/api/area-items/';
    const meta = document.createElement('meta');
    meta.name = 'pwa-user-id';
    meta.content = principal;
    document.head.appendChild(meta);
    const previousAnswer = areaContentAnswer;
    areaContentAnswer = () => ({ ...previousAnswer(), resorts: [{ slug: 'verbier' }] });
    const fetchStub = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        const href = String(url);
        if (href.includes('area-items')) {
          return Promise.resolve({
            ok: true,
            headers: { get: (name) => (name === 'X-Snowdesk-Principal' ? principal : null) },
            json: () => Promise.resolve({ trips: [], pins: [] }),
          });
        }
        if (href.includes('routes.geojson')) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(routes) });
        }
        return fetchStub(url);
      }),
    );
    try {
      await seedArea({ contentAt: undefined });
      return await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);
    } finally {
      areaContentAnswer = previousAnswer;
      delete mapEl.dataset.resortSheetUrl;
      delete mapEl.dataset.areaItemsUrl;
      meta.remove();
      vi.stubGlobal('fetch', fetchStub);
    }
  }

  it("stamps the area's resorts with their sheets, and the reader's routes inside it", async () => {
    const ok = await syncSignedIn('42', {
      type: 'FeatureCollection',
      features: [
        ROUTE('inside', [[7.1, 46.1], [7.3, 46.3]]),
        ROUTE('elsewhere', [[8.0, 47.0], [8.1, 47.1]]),
      ],
    });

    expect(ok).toBe(true);
    const stored = await storedArea();
    expect(stored.items.principal).toBe('42');
    expect(stored.items.routes).toEqual([{ uuid: 'inside' }]);
    expect(stored.items.resorts.map((resort) => resort.slug)).toEqual(['verbier']);
    expect(stored.items.resorts[0].urls.length).toBeGreaterThan(0);
    expect(stored.items.resorts[0].urls[0]).toContain('/partials/place-sheet/resort/verbier/');
  });

  it('stamps resorts but no routes for a signed-out sync', async () => {
    const mapEl = document.getElementById('map');
    mapEl.dataset.resortSheetUrl = RESORT_SHEET;
    const previousAnswer = areaContentAnswer;
    areaContentAnswer = () => ({ ...previousAnswer(), resorts: [{ slug: 'verbier' }] });
    try {
      await seedArea({ contentAt: undefined });
      await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);
    } finally {
      areaContentAnswer = previousAnswer;
      delete mapEl.dataset.resortSheetUrl;
    }

    const stored = await storedArea();
    expect(stored.items.resorts.map((resort) => resort.slug)).toEqual(['verbier']);
    expect(stored.items.routes).toBeUndefined();
  });
});

describe('the background sync (SNOW-1416)', () => {
  const STALE = () => new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();

  beforeEach(async () => {
    window.pwaDb.rows.delete('autosync.attempts');
    window.pwaConnectivity = { isOnline: () => true };
    await seedArea({ contentAt: STALE() });
  });

  it('refreshes a stale area through the content half alone, never its tiles', async () => {
    holdDependencies([STYLE_URL, SPRITE_URL]);

    const attempted = await window.pwaBasemapDownloads.backgroundSync();

    expect(attempted).toEqual([AREA_ID]);
    expect(window.pwaWarmCache).toHaveBeenCalledTimes(1);
    expect(warmedUrls(0)).toContain(BULLETIN_URL);
    expect(warmedUrls(0)).not.toContain(TILEJSON_URL);
  });

  it('writes the attempt before it runs, and does not retry within the hour', async () => {
    let ledgerAtWarm = null;
    window.pwaWarmCache.mockImplementationOnce(async () => {
      ledgerAtWarm = window.pwaDb.rows.get('autosync.attempts');
      return { ok: 0, failed: 1, bytes: 0, cancelled: false };
    });

    await window.pwaBasemapDownloads.backgroundSync();
    expect(ledgerAtWarm).toHaveProperty(AREA_ID);

    window.pwaWarmCache.mockClear();
    expect(await window.pwaBasemapDownloads.backgroundSync()).toEqual([]);
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });

  it('leaves a fresh area alone', async () => {
    await seedArea({ contentAt: new Date().toISOString() });

    expect(await window.pwaBasemapDownloads.backgroundSync()).toEqual([]);
  });

  it('does not run offline, latched or forced', async () => {
    window.pwaConnectivity = { isOnline: () => false };

    expect(await window.pwaBasemapDownloads.backgroundSync()).toEqual([]);
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });

  it('does not run when the device asks to save data', async () => {
    Object.defineProperty(navigator, 'connection', { value: { saveData: true }, configurable: true });
    try {
      expect(await window.pwaBasemapDownloads.backgroundSync()).toEqual([]);
    } finally {
      delete navigator.connection;
    }
  });

  it('does not start under a user run', async () => {
    const run = window.pwaBasemapDownloads.contentRun;
    expect(run.claim('user')).toBe(true);
    try {
      expect(await window.pwaBasemapDownloads.backgroundSync()).toEqual([]);
    } finally {
      run.release('user');
    }
  });

  it('runs once for a burst of triggers', async () => {
    const [first, second] = await Promise.all([
      window.pwaBasemapDownloads.backgroundSync(),
      window.pwaBasemapDownloads.backgroundSync(),
    ]);

    expect([first, second]).toContainEqual([AREA_ID]);
    expect([first, second]).toContainEqual([]);
    expect(window.pwaWarmCache).toHaveBeenCalledTimes(1);
  });

  it('names its area while it runs, and makes a user press wait for it', async () => {
    let release;
    window.pwaWarmCache.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ ok: 1, failed: 0, bytes: 1, cancelled: false });
        }),
    );
    const run = window.pwaBasemapDownloads.contentRun;

    const auto = window.pwaBasemapDownloads.backgroundSync();
    await waitFor(() => !!release);
    expect(run.current()).toBe('auto');
    expect(run.area()).toBe(AREA_ID);

    let took = false;
    const press = run.take().then((value) => {
      took = value;
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(took).toBe(false);

    release();
    await auto;
    await press;
    expect(took).toBe(true);
    expect(run.current()).toBe('user');
    run.release('user');
  });

  it('runs on the online event and when the page becomes visible', async () => {
    window.pwaBasemapDownloads.bindBackgroundSync();
    // Binding runs once at once; let it settle and clear the ledger.
    await waitFor(() => window.pwaWarmCache.mock.calls.length === 1);
    window.pwaWarmCache.mockClear();
    window.pwaDb.rows.delete('autosync.attempts');
    await seedArea({ contentAt: STALE() });

    window.dispatchEvent(new Event('online'));
    expect(await waitFor(() => window.pwaWarmCache.mock.calls.length === 1)).toBe(true);

    window.pwaWarmCache.mockClear();
    window.pwaDb.rows.delete('autosync.attempts');
    await seedArea({ contentAt: STALE() });
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(await waitFor(() => window.pwaWarmCache.mock.calls.length === 1)).toBe(true);
  });
});
