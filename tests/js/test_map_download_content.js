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
 * SNOW-932 adds the third, and it is the one this harness exists for more
 * than either of those: that a content shortfall SURVIVES. `partial` was
 * painted and never stored, so the next `renderControl()` repainted the
 * area green from a probe with no way to know it had fallen short — and
 * `renderControl()` runs on `snowdesk:connectivity-changed`, which is
 * exactly what a flapping signal fires. Only a test that drives the real
 * record, the real probe and the real listener can catch that; a unit test
 * of any one of the three passes either way.
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

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const REGION_SLUG = 'martigny-verbier';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const TODAY = '2026-01-06';
const TOMORROW = '2026-01-07';
const YESTERDAY = '2026-01-05';

// SNOW-1312: the season ratings the download reads its forward ceiling
// off (`pwaCalendarCore.latestKnownDate`) — tomorrow's bulletin is out.
const SEASON_RATINGS = {
  [YESTERDAY]: { [REGION_ID]: 2 },
  [TODAY]: { [REGION_ID]: 3 },
  [TOMORROW]: { [REGION_ID]: 3 },
};

// SNOW-1072: the terrain-class tileset, on its own host so its tiles can be
// told apart from everything else a run posts. On the fixture's `#map` from
// the start; nothing pins from it until a test opts in.
const TERRAIN_TEMPLATE = 'https://terrain.example.invalid/terrain-class/v2/{z}/{x}/{y}.png';
const isClassTile = (url) => url.startsWith('https://terrain.example.invalid/');

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

/**
 * The server's answer for this area's rectangle (SNOW-953).
 *
 * The selection is the endpoint's now, so the fixture states its RESULT
 * rather than the geometry it was made from: the region the boundary
 * covers, and the one weather location inside it. `OUTSIDEbbbb` (Davos,
 * in the weather feed above) is absent for the same reason the server
 * would leave it out — it is not in the box.
 */
const AREA_CONTENT = {
  regions: [{ id: REGION_ID, slug: REGION_SLUG }],
  weather: [{ short_id: 'INSIDEaaaaa' }],
};

/**
 * The reader's own things inside the same rectangle (SNOW-965): one trip,
 * and one pin whose borrowed forecast sits OUTSIDE the box — the case the
 * endpoint exists for — plus one borrowing the sheet the area-content plan
 * already names, which must not be posted twice.
 */
const TRIP_UUID = '2f1c0b7e-4b7a-4c39-9a51-0d6c1f0e9a11';
const AREA_ITEMS = {
  trips: [{ uuid: TRIP_UUID }],
  pins: [
    { uuid: 'pin-1', forecast: { short_id: 'BORROWEDccc' } },
    { uuid: 'pin-2', forecast: { short_id: 'INSIDEaaaaa' } },
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
        // SNOW-1072: a re-download's prune removes entries one by one.
        delete: async (url) => store.delete(url),
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
    // SNOW-959: the stub's stand-in for db.js's one-transaction
    // read-modify-write. The serialisation that helper exists for is
    // IndexedDB's, and a Map cannot show it — so this reproduces only the
    // CONTRACT its callers depend on: the mutator is handed the current
    // row, and an `undefined` return writes nothing. The serialisation
    // itself is proved against fake-indexeddb in tests/js/test_db.js.
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
         data-area-content-url="/api/area-content/"
         data-area-items-url="/api/area-items/"
         data-trip-page-url-template="/trips/__UUID__/"
         data-trip-route-url-template="/trips/__UUID__/route.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-weather-url="/api/weather.geojson"
         data-weather-detail-url="/api/weather/__SHORTID__/detail/"
         data-region-sheet-url="/partials/region-sheet/XX-0000/"
         data-favourite-sheet-url="/partials/place-sheet/favourite/00000000-0000-0000-0000-000000000000/"
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
          data-basemap-url="https://tiles.example.invalid/liberty.json"
          aria-checked="true"
        >OpenFreeMap</button>
      </li>
    </ul>
    <div id="map-download-error-toast" class="hidden"></div>`;
}

/** Point the page's `pwa-user-id` meta tag at `id` (SNOW-1142). */
function setAccount(id) {
  document.head.querySelectorAll('meta[name="pwa-user-id"]').forEach((el) => el.remove());
  const meta = document.createElement('meta');
  meta.name = 'pwa-user-id';
  meta.content = id;
  document.head.appendChild(meta);
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

/**
 * What `/api/area-content/` answers on the next call.
 *
 * A function rather than a constant so a test can make the endpoint fail
 * or come back empty — the two states that used to be reached by emptying
 * the client's own feature set, which no longer decides anything.
 */
let areaContentAnswer = () => AREA_CONTENT;

/**
 * Whether `/api/area-content/` stalls instead of answering (SNOW-958).
 *
 * The failure mode a bare `fetch` has no defence against, and the one an
 * `ok: false` answer cannot stand in for: a connection that completes its
 * handshake and then says nothing — a captive portal, a lift. The request
 * neither resolves nor rejects, so there is no catch branch to reach and
 * no tally to come back short. Only an abort ends it.
 */
let areaContentStalls = false;

/**
 * What `/api/area-items/` answers on the next call (SNOW-965). ``null``
 * stands for an endpoint that could not be read, as for area-content.
 */
let areaItemsAnswer = () => AREA_ITEMS;

/** The `AbortSignal` the last `/api/area-content/` request carried, if any. */
let lastAreaContentSignal = null;

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
    vi.fn((url, options) => {
      const href = String(url);
      let body = {};
      if (href.includes('regions.geojson')) body = REGIONS_GEOJSON;
      if (href.includes('area-content')) {
        lastAreaContentSignal = (options && options.signal) || null;
        if (areaContentStalls) {
          // Settles only if something aborts it. An unbounded caller
          // waits here for ever, which is the whole of SNOW-958.
          return new Promise((_resolve, reject) => {
            if (!lastAreaContentSignal) return;
            lastAreaContentSignal.addEventListener('abort', () => {
              reject(new DOMException('The operation was aborted.', 'AbortError'));
            });
          });
        }
        const answer = areaContentAnswer();
        // A null answer stands for an endpoint that could not be read —
        // the state that makes a plan SHORT rather than empty.
        return Promise.resolve(
          answer
            ? { ok: true, json: () => Promise.resolve(answer) }
            : { ok: false, json: () => Promise.resolve({}) },
        );
      }
      if (href.includes('area-items')) {
        const items = areaItemsAnswer();
        return Promise.resolve(
          items
            ? { ok: true, json: () => Promise.resolve(items) }
            : { ok: false, status: 500, json: () => Promise.resolve({}) },
        );
      }
      // SNOW-1312: the season payload the download's ceiling is read off —
      // yesterday, today and tomorrow are published.
      if (href.startsWith('/api/ratings.json?country=')) body = SEASON_RATINGS;
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
  // SNOW-1142: the signed-in account, and the bridge whose
  // `stampPrincipal` every record writer stamps through. With no
  // `#map-custom-download-control` on the page the bridge's own sync is
  // off, so nothing here is pushed — only stamped.
  setAccount('u-1');
  await import('../../static/js/downloads_sync.js');
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  // SNOW-1072: the class tileset's coverage and band.
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/terrain_filter_core.js');
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

  it('takes tomorrow too, once it is published (SNOW-927)', () => {
    expect(posted).toContain(`/${REGION_ID.toLowerCase()}/${REGION_SLUG}/${TOMORROW}/`);
  });

  it('saves nothing from before today (SNOW-1312)', () => {
    // "You're looking forward out in the field, not back." Yesterday is in
    // the season payload, and is still not taken — for any kind of content.
    expect(posted.some((url) => url.includes(YESTERDAY))).toBe(false);
  });

  it('posts the region sheet for every region and forward day (SNOW-1312)', () => {
    // The map's own partial, lowercased, `d` first: the url the sheet asks
    // for, minus the `r` the worker ignores when it keys the entry.
    expect(posted).toContain(`/partials/region-sheet/ch-4115/?d=${TODAY}`);
    expect(posted).toContain(`/partials/region-sheet/ch-4115/?d=${TOMORROW}`);
  });

  it('posts the dated ratings the map boots its colours from (SNOW-1312)', () => {
    // map.js's boot leg, in its own parameter order — the worker answers
    // the feed on its exact url.
    expect(posted).toContain(`/api/ratings.json?d=${TODAY}&country=ch`);
    expect(posted).toContain(`/api/ratings.json?d=${TOMORROW}&country=ch`);
  });

  it('posts the weather sheet for a location inside, and not one outside', () => {
    // The whole feed is cached either way — it is one small request. What
    // the boundary narrows is the per-location sheets, of which there are
    // ~550 across the estate. Since SNOW-953 the narrowing is the
    // server's, so what this asserts is that the client asks for exactly
    // what it was told and invents nothing from the feed it also holds.
    //
    // SNOW-965: dated with today, the url the map's sheet asks for and the
    // only one the worker answers offline.
    expect(posted).toContain(`/api/weather/INSIDEaaaaa/detail/?date=${TODAY}`);
    expect(posted.some((url) => url.includes('OUTSIDEbbbb'))).toBe(false);
  });

  it("posts the reader's trip and the forecast a pin borrows (SNOW-965)", () => {
    expect(posted).toContain(`/trips/${TRIP_UUID}/`);
    expect(posted).toContain(`/trips/${TRIP_UUID}/route.geojson`);
    // Outside the box, and here only because a pin inside borrows it.
    expect(posted).toContain(`/api/weather/BORROWEDccc/detail/?date=${TODAY}`);
    // A sheet the area-content plan already names is not posted twice.
    const inside = posted.filter(
      (url) => url === `/api/weather/INSIDEaaaaa/detail/?date=${TODAY}`,
    );
    expect(inside).toHaveLength(1);
  });

  it("posts each pin's own place sheet for the map's day (SNOW-1313)", () => {
    expect(posted).toContain(`/partials/place-sheet/favourite/pin-1/?d=${TODAY}`);
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

describe('a content shortfall survives the next render (SNOW-932)', () => {
  /**
   * Tap the roundel, wait for the refresh it dispatches, and read where it
   * settles.
   *
   * The wait is on the warm-cache CALL, not on the roundel leaving 'busy':
   * the click handler is async, so the state is still whatever it was for
   * the first few ticks after the tap and a bare "not busy" check passes
   * before the run has begun.
   */
  async function tapAndSettle() {
    const btn = document.getElementById('map-download-control');
    window.pwaWarmCache.mockClear();
    btn.click();
    await waitFor(() => window.pwaWarmCache.mock.calls.length > 0);
    await waitFor(() => btn.dataset.downloadState !== 'busy');
    return btn.dataset.downloadState;
  }

  /** Fire the event a flapping signal fires, and let the render settle. */
  async function flapConnectivity() {
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed'));
    // `renderControl` is coalesced and probes Cache Storage, so the repaint
    // is several ticks out. Long enough for the trailing pass to land.
    await new Promise((resolve) => setTimeout(resolve, 60));
  }

  it('stays partial across a connectivity change', async () => {
    // THE test. A refresh that half-lands paints 'partial'; before this
    // ticket nothing stored that, so the very next render read whole tiles
    // off the probe and painted 'done' over a shortfall that was still
    // there. Fails against the unmodified code.
    const btn = document.getElementById('map-download-control');
    await waitFor(() => btn.dataset.downloadState === 'done');

    window.pwaWarmCache.mockImplementationOnce(async () => ({
      ok: 0,
      failed: 4,
      bytes: 0,
      cancelled: false,
    }));
    expect(await tapAndSettle()).toBe('partial');

    await flapConnectivity();

    expect(btn.dataset.downloadState).toBe('partial');
    // SNOW-1130: and it is announced as actionable — the remedy is a tap,
    // and the region sheet's proxy drops presses on a disabled roundel.
    expect(btn.getAttribute('aria-disabled')).toBe('false');
  });

  it('records the shortfall on the area, not in the DOM', async () => {
    // The same fact, read where it now lives. A roundel that is amber only
    // for as long as nothing repaints it is not a state, it is a message.
    const record = await recordedRegion();

    expect(record.contentIncomplete).toBe(true);
    // And `contentAt` is untouched: it records the last time this area's
    // content was fetched IN FULL, which a run that fell short did not
    // change.
    expect(typeof record.contentAt).toBe('string');
  });

  it('clears the flag when a refresh lands, and goes back to done', async () => {
    // Deleted, never set false — so a refreshed record is indistinguishable
    // from one that never fell short, which is what keeps the
    // absence-means-fine rule true for every reader.
    const btn = document.getElementById('map-download-control');
    expect(await tapAndSettle()).toBe('done');

    const record = await recordedRegion();
    expect('contentIncomplete' in record).toBe(false);

    await flapConnectivity();
    expect(btn.dataset.downloadState).toBe('done');
    // SNOW-1130: a tap on 'done' refreshes, so it is not announced disabled.
    expect(btn.getAttribute('aria-disabled')).toBe('false');
  });

  it('announces done as disabled while offline, where no refresh can run', async () => {
    // SNOW-1130 review: the area is still on the device, so it stays
    // painted done — but the tap's refresh needs the network.
    const btn = document.getElementById('map-download-control');
    await waitFor(() => btn.dataset.downloadState === 'done');

    const online = window.pwaConnectivity;
    window.pwaConnectivity = { isOnline: () => false };
    try {
      await flapConnectivity();

      expect(btn.dataset.downloadState).toBe('done');
      expect(btn.getAttribute('aria-disabled')).toBe('true');
    } finally {
      window.pwaConnectivity = online;
      await flapConnectivity();
    }
  });

  it('starts one refresh for two quick taps', async () => {
    // SNOW-1130 review: the refresh awaits the record and the content plan
    // before its run paints 'busy', so a second tap in that window used to
    // start a second run.
    const btn = document.getElementById('map-download-control');
    await waitFor(() => btn.dataset.downloadState === 'done');
    window.pwaWarmCache.mockClear();

    btn.click();
    btn.click();
    await waitFor(() => window.pwaWarmCache.mock.calls.length > 0);
    await waitFor(() => btn.dataset.downloadState !== 'busy');

    expect(window.pwaWarmCache).toHaveBeenCalledTimes(1);
  });

  it('reads a record with neither field as done, not as amber', async () => {
    // Backwards compatibility, and the reason the flag is absent rather
    // than false. Every area downloaded before SNOW-924 carries neither
    // `contentAt` nor `contentIncomplete`; if absence read as a shortfall,
    // the deploy that introduced this field would turn every one of them
    // amber overnight.
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    await window.pwaDb.put('meta:app', {
      key: 'basemap.regions',
      value: row.value.map((entry) => {
        if (!entry || entry.region_id !== REGION_ID) return entry;
        const { contentAt: _a, contentIncomplete: _b, ...rest } = entry;
        return rest;
      }),
    });

    await flapConnectivity();

    expect(document.getElementById('map-download-control').dataset.downloadState).toBe(
      'done',
    );
  });

  it('counts a short PLAN, even when every url in it lands', async () => {
    // SNOW-931's hanging thread, in SNOW-953's shape: the plan can no
    // longer be short of a COUNTRY, but it can still be short of
    // everything, when the endpoint that answers for it cannot be read.
    // Nothing in the tally fails either way — the run reports
    // `ok === total` over a list that was missing bulletins — so the
    // shortfall has to ride out on its own flag.
    const btn = document.getElementById('map-download-control');
    areaContentAnswer = () => null;
    try {
      btn.click();
      // No warm-cache call to wait on: a plan the endpoint could not
      // answer for is EMPTY as well as short, so nothing is dispatched.
      // That is exactly why the flag has to be written here rather than
      // by the run's `finish` — see `refreshAreaContent`.
      await new Promise((resolve) => setTimeout(resolve, 80));
      expect(btn.dataset.downloadState).toBe('partial');
    } finally {
      areaContentAnswer = () => AREA_CONTENT;
    }

    const record = await recordedRegion();
    expect(record.contentIncomplete).toBe(true);
  });

  it('counts an area-items failure as a shortfall too (SNOW-965)', async () => {
    // The reader's trips and borrowed forecasts are part of the plan. An
    // endpoint that was asked and answered 500 leaves them unnamed, and
    // no tally over the urls that DID land can see that — so the area is
    // recorded short and never painted done.
    const btn = document.getElementById('map-download-control');
    await tapAndSettle();
    expect(btn.dataset.downloadState).toBe('done');

    areaItemsAnswer = () => null;
    try {
      expect(await tapAndSettle()).toBe('partial');
      const posted = warmedUrls(0);
      // What could be named still went: the area's own content.
      expect(posted).toContain(`/api/weather/INSIDEaaaaa/detail/?date=${TODAY}`);
      expect(posted.some((url) => url.startsWith('/trips/'))).toBe(false);
    } finally {
      areaItemsAnswer = () => AREA_ITEMS;
    }

    // Left flagged: the next test clears it through an empty plan.
    expect((await recordedRegion()).contentIncomplete).toBe(true);
  });

  /**
   * Run `body` with this area's boundary resolving to NOTHING to fetch.
   *
   * SNOW-953: an empty plan is an empty ANSWER now — both lists, because
   * either one left populated still yields urls. It used to be produced by
   * emptying the client's own `featureByRegionId` and its weather feed,
   * which no longer decide anything.
   */
  async function withEmptyPlan(body) {
    areaContentAnswer = () => ({ regions: [], weather: [] });
    // SNOW-965: and no items of the reader's own, or they would be urls.
    areaItemsAnswer = () => ({ trips: [], pins: [] });
    try {
      const btn = document.getElementById('map-download-control');
      btn.click();
      // No warm-cache call to wait on — an empty plan never dispatches a
      // run — so this settles on the repaint instead.
      await new Promise((resolve) => setTimeout(resolve, 80));
      await body(btn);
    } finally {
      areaContentAnswer = () => AREA_CONTENT;
      areaItemsAnswer = () => AREA_ITEMS;
    }
  }

  it('clears a stale flag when a WHOLE plan resolves to nothing to fetch', async () => {
    // Codex review on PR #908. An empty plan is not automatically a failed
    // one: a boundary holding no bulletin region and no weather location
    // resolves to zero urls, and that is a complete answer about this area.
    // Both refresh paths used to return early WITHOUT stamping, so an area
    // flagged by an earlier short plan could never be cleared — every tap
    // re-resolved the same empty list and re-reported the same failure,
    // leaving the roundel amber with a remedy that did nothing.
    expect((await recordedRegion()).contentIncomplete).toBe(true);

    await withEmptyPlan(async (btn) => {
      expect(btn.dataset.downloadState).toBe('done');
    });

    expect('contentIncomplete' in (await recordedRegion())).toBe(false);
  });

  it('leaves the flag alone when an empty plan was itself SHORT', async () => {
    // The other half of the same rule, and why `short` cannot be collapsed
    // into "the list was empty". A plan whose endpoint never answered says
    // nothing about what the boundary holds, so an empty one is not
    // evidence of completeness and must not clear anything.
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    await window.pwaDb.put('meta:app', {
      key: 'basemap.regions',
      value: row.value.map((entry) =>
        entry && entry.region_id === REGION_ID
          ? { ...entry, contentIncomplete: true }
          : entry,
      ),
    });

    areaContentAnswer = () => null;
    try {
      const btn = document.getElementById('map-download-control');
      btn.click();
      await new Promise((resolve) => setTimeout(resolve, 80));
      const record = await recordedRegion();
      expect(record.contentIncomplete).toBe(true);
    } finally {
      areaContentAnswer = () => AREA_CONTENT;
    }
  });
});

describe('the roundel dates bulletins from an earlier day (SNOW-1139)', () => {
  /** Rewrite this region's record with `fields`, then let the render settle. */
  async function patchRecord(fields) {
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    await window.pwaDb.put('meta:app', {
      key: 'basemap.regions',
      value: row.value.map((entry) => {
        if (!entry || entry.region_id !== REGION_ID) return entry;
        const { contentIncomplete: _c, ...rest } = entry;
        return { ...rest, ...fields };
      }),
    });
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed'));
    await new Promise((resolve) => setTimeout(resolve, 60));
  }

  /** An ISO instant three local days ago. */
  function threeDaysAgo() {
    const d = new Date();
    d.setDate(d.getDate() - 3);
    d.setHours(9, 0, 0, 0);
    return d.toISOString();
  }

  afterAll(async () => {
    await patchRecord({ contentAt: new Date().toISOString() });
  });

  it('names the day on a done area whose bulletins are not today\'s', async () => {
    const btn = document.getElementById('map-download-control');
    const contentAt = threeDaysAgo();
    await patchRecord({ contentAt });

    expect(btn.dataset.downloadState).toBe('done');
    const day = window.pwaCachedStampCore.formatDay(contentAt);
    expect(btn.getAttribute('aria-label')).toContain(`bulletins from ${day}`);
    expect(btn.title).toBe(btn.getAttribute('aria-label'));
  });

  it('names the day on a partial area too', async () => {
    const btn = document.getElementById('map-download-control');
    const contentAt = threeDaysAgo();
    await patchRecord({ contentAt, contentIncomplete: true });

    expect(btn.dataset.downloadState).toBe('partial');
    const day = window.pwaCachedStampCore.formatDay(contentAt);
    expect(btn.getAttribute('aria-label')).toContain(`bulletins are from ${day}`);
  });

  it('keeps the undated label for today\'s bulletins', async () => {
    const btn = document.getElementById('map-download-control');
    await patchRecord({ contentAt: new Date().toISOString() });

    expect(btn.dataset.downloadState).toBe('done');
    expect(btn.getAttribute('aria-label')).toBe(
      'This region is available offline — tap to update its bulletins and weather',
    );
  });
});

describe('a custom area catches up through the sheet (SNOW-932)', () => {
  // The CUSTOM half of the same fact. A custom area has no roundel once
  // its framing overlay closes, so `refreshAreaContent` — the bridge
  // member the Manage downloads sheet's Refresh reaches — is the only way
  // out of a shortfall it has. The sheet's own wiring is covered in
  // tests/js/test_map_downloads_manager.js; what is asserted here is the
  // thing only a real record and a real boundary can show.
  const AREA_ID = 'custom-a1';

  /** Seed one custom area over the fixture's ground, and read it back. */
  async function seedCustomArea(extra) {
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
            bytes: 4096,
            savedAt: '2026-01-05T10:00:00.000Z',
            contentIncomplete: true,
          },
          extra || {},
        ),
      ],
    });
  }

  /** The stored custom-area record, or undefined. */
  async function storedCustomArea() {
    const row = await window.pwaDb.get('meta:app', 'basemap.customAreas');
    const list = Array.isArray(row && row.value) ? row.value : [];
    return list.find((entry) => entry && entry.id === AREA_ID);
  }

  it('refetches the bulletins inside its box and not one tile', async () => {
    // The user-visible promise, and the reason this goes through `repair`
    // rather than `run`: kilobytes of HTML over the connection the download
    // existed for, not megabytes of tiles — and no eviction confirm that
    // could destroy another area for the sake of a day-old bulletin.
    await seedCustomArea();
    window.pwaWarmCache.mockClear();

    const ok = await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(ok).toBe(true);
    const urls = warmedUrls(0);
    expect(urls).toContain(`/${REGION_ID.toLowerCase()}/${REGION_SLUG}/${TODAY}/`);
    expect(urls).toContain(`/api/weather/INSIDEaaaaa/detail/?date=${TODAY}`);
    expect(urls.some((url) => url.includes('tiles.example.invalid'))).toBe(false);
  });

  it('deletes the flag on success rather than setting it false', async () => {
    // The rule the whole ticket rests on. A `false` here would make this
    // record disagree with every record written before the field existed,
    // and the next reader to ask `'contentIncomplete' in record` would get
    // a different answer for two areas in the same condition.
    const record = await storedCustomArea();

    expect('contentIncomplete' in record).toBe(false);
    expect(typeof record.contentAt).toBe('string');
  });

  it('writes the flag back when the refetch falls over', async () => {
    await seedCustomArea({ contentAt: '2026-01-05T10:00:00.000Z' });
    window.pwaWarmCache.mockImplementationOnce(async () => ({
      ok: 0,
      failed: 6,
      bytes: 0,
      cancelled: false,
    }));

    const ok = await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(ok).toBe(false);
    const record = await storedCustomArea();
    expect(record.contentIncomplete).toBe(true);
    // `contentAt` is left alone: it records the last time this area's
    // content was fetched IN FULL, and this run did not change that.
    expect(record.contentAt).toBe('2026-01-05T10:00:00.000Z');
  });

  it('declines an id nothing on this device carries', async () => {
    // An orphaned bucket, or an area the account knows about and this
    // device has never held. Nothing to refresh and nothing to write.
    window.pwaWarmCache.mockClear();

    const ok = await window.pwaBasemapDownloads.refreshAreaContent('custom-nope');

    expect(ok).toBe(false);
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });

  it('resolves a REGION id through the same one control', async () => {
    // The sheet lists both kinds and offers one Refresh, so this member
    // has to reach `basemap.regions` as readily as `basemap.customAreas`.
    window.pwaWarmCache.mockClear();

    const ok = await window.pwaBasemapDownloads.refreshAreaContent(
      `region-${REGION_ID}`,
    );

    expect(ok).toBe(true);
    expect(warmedUrls(0).some((url) => url.includes('tiles.example.invalid'))).toBe(
      false,
    );
  });
});

describe('both stampers write through one transaction (SNOW-959)', () => {
  // The rows these two write are whole ARRAYS rewritten to change one
  // entry, and since SNOW-950/953 three functions across two
  // simultaneously-open pages write them. A `get` and a later `put` loses
  // whichever change lands first, so the pair has to ride one IndexedDB
  // transaction — see `readModifyWrite` in static/js/db.js.
  //
  // What is asserted here is the ROUTING, because that is what can
  // regress: someone reaching for the familiar `get`/`put` pair in a
  // later ticket. That the helper actually serialises is proved against
  // fake-indexeddb in tests/js/test_db.js, with the lost update
  // reproduced — a stub over a Map could not show it.

  /** Every key `put` was called with since the last clear. */
  function putKeys() {
    return window.pwaDb.put.mock.calls.map((call) => call[1] && call[1].key);
  }

  /** Every key `readModifyWrite` was called with since the last clear. */
  function rmwKeys() {
    return window.pwaDb.readModifyWrite.mock.calls.map((call) => call[1]);
  }

  it('the region roundel stamps through readModifyWrite, not put', async () => {
    await window.pwaDb.put('meta:app', {
      key: 'basemap.regions',
      value: [{ region_id: REGION_ID, bytes: 4096, savedAt: '2026-01-05T10:00:00.000Z' }],
    });
    window.pwaDb.put.mockClear();
    window.pwaDb.readModifyWrite.mockClear();

    await window.pwaBasemapDownloads.refreshAreaContent(`region-${REGION_ID}`);

    expect(rmwKeys()).toContain('basemap.regions');
    expect(putKeys()).not.toContain('basemap.regions');
  });

  it('the custom-area sheet stamps through readModifyWrite, not put', async () => {
    await window.pwaDb.put('meta:app', {
      key: 'basemap.customAreas',
      value: [
        {
          id: 'custom-rmw',
          ordinal: 1,
          bbox: [7.0, 46.0, 7.2, 46.2],
          band: [10, 14],
          template: TEMPLATE,
          basemapKey: 'openfreemap_liberty',
          bytes: 4096,
          savedAt: '2026-01-05T10:00:00.000Z',
        },
      ],
    });
    window.pwaDb.put.mockClear();
    window.pwaDb.readModifyWrite.mockClear();

    await window.pwaBasemapDownloads.refreshAreaContent('custom-rmw');

    expect(rmwKeys()).toContain('basemap.customAreas');
    expect(putKeys()).not.toContain('basemap.customAreas');
  });
});

describe('a stalled content endpoint gives up rather than hanging (SNOW-958)', () => {
  // `fetchAreaContent` is the single funnel every content request goes
  // through — the download runner's `assembleAreaContentURLs`, the region
  // roundel's tap-to-refresh, and the Manage-downloads sheet's Refresh all
  // await it — so bounding it here is what bounds all three. It is driven
  // through `refreshAreaContent` because that is the one caller whose
  // promise a test can hold: the other two settle into DOM state, and a
  // run that never settles has no state to poll for.
  const AREA_ID = 'custom-stall';

  async function seedStallArea() {
    await window.pwaDb.put('meta:app', {
      key: 'basemap.customAreas',
      value: [
        {
          id: AREA_ID,
          ordinal: 1,
          bbox: [7.0, 46.0, 7.2, 46.2],
          band: [10, 14],
          template: TEMPLATE,
          basemapKey: 'openfreemap_liberty',
          bytes: 4096,
          savedAt: '2026-01-05T10:00:00.000Z',
        },
      ],
    });
  }

  afterEach(() => {
    areaContentStalls = false;
    lastAreaContentSignal = null;
    vi.useRealTimers();
  });

  it('carries an abort signal on every request', async () => {
    // The precondition for the bound existing at all. Asserted on the
    // HAPPY path deliberately: a signal that only appears when something
    // has already gone wrong is not a signal the timer can reach.
    await seedStallArea();

    await window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);

    expect(lastAreaContentSignal).toBeInstanceOf(AbortSignal);
    expect(lastAreaContentSignal.aborted).toBe(false);
  });

  it('aborts the request once the budget runs out', async () => {
    await seedStallArea();
    areaContentStalls = true;
    vi.useFakeTimers();

    const pending = window.pwaBasemapDownloads.refreshAreaContent(AREA_ID);
    // Let the request be issued before the clock moves; the timer is armed
    // inside `fetchAreaContent`, not before it.
    await vi.advanceTimersByTimeAsync(0);
    expect(lastAreaContentSignal.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(15000);

    expect(lastAreaContentSignal.aborted).toBe(true);
    // And the whole operation settles rather than sitting pending — an
    // unbounded `fetch` never reaches this line, which is the regression
    // this test exists to catch. `false` because an endpoint that could
    // not be read leaves the plan short, and a short plan is a failed
    // refresh: the honest answer, not a completion stamped over a gap.
    vi.useRealTimers();
    await expect(pending).resolves.toBe(false);
  });
});

describe('terrain data on a region download (SNOW-1072)', () => {
  /** The class tiles REGION_BLOB's ground implies, derived as the run does. */
  function expectedClassTiles() {
    const terrain = self.pwaTerrainFilterCore;
    return self.pwaBasemapDownloadCore.terrainClassTileURLs(
      TERRAIN_TEMPLATE,
      REGION_BLOB,
      terrain.COVERAGE_BOUNDS,
      terrain.MIN_ZOOM,
      terrain.MAX_ZOOM,
    );
  }

  /** Forget the region on this device, so the next click downloads it. */
  async function forgetRegion() {
    await window.pwaDb.delete('meta:app', 'basemap.regions');
    window.caches.buckets.clear();
  }

  afterEach(() => {
    window.localStorage.clear();
  });

  it('pins no class tiles without the opt-in, and records none', async () => {
    await forgetRegion();
    window.pwaWarmCache.mockClear();

    await clickControl();

    expect(warmedUrls(0).some(isClassTile)).toBe(false);
    expect('terrain' in (await recordedRegion())).toBe(false);
  });

  it('pins the class tiles over the area when opted in, and records the flag', async () => {
    await forgetRegion();
    window.pwaBasemapDownloads.setTerrainDataPreferred(true);
    window.pwaWarmCache.mockClear();

    await clickControl();

    const posted = warmedUrls(0);
    const expected = expectedClassTiles();
    expect(expected.length).toBeGreaterThan(0);
    expect(posted.filter(isClassTile)).toEqual(expected);
    // Appended after every other tile family, never ahead of the grid.
    expect(posted.slice(-expected.length)).toEqual(expected);
    const record = await recordedRegion();
    expect(record.terrain).toBe(true);
    // The flag, never the URLs: the record carries no class tile.
    expect(JSON.stringify(record).includes('terrain.example.invalid')).toBe(false);
  });

  it('reads an opted-in region missing a class tile as incomplete, and the tap repairs it', async () => {
    // Downloaded by the test above, flag and all.
    const btn = document.getElementById('map-download-control');
    const missing = expectedClassTiles()[0];
    window.caches.buckets.get(`${PINNED_PREFIX}region-${REGION_ID}`).delete(missing);
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed'));
    await waitFor(() => btn.dataset.downloadState === 'incomplete');
    expect(btn.dataset.downloadState).toBe('incomplete');
    window.pwaWarmCache.mockClear();

    btn.click();
    await waitFor(() => window.pwaWarmCache.mock.calls.length > 0);

    expect(warmedUrls(0)).toEqual([missing]);
  });

  it('prunes the class tiles a re-download without the opt-in no longer holds', async () => {
    // Downloaded with terrain by the tests above; the preference is now off
    // (afterEach cleared it). Losing a basemap tile reads the region as not
    // downloaded, so the tap is a fresh run over the same bucket.
    const btn = document.getElementById('map-download-control');
    const bucket = window.caches.buckets.get(`${PINNED_PREFIX}region-${REGION_ID}`);
    expect((await recordedRegion()).terrain).toBe(true);
    for (const url of expectedClassTiles()) bucket.add(url);
    const basemapTile = [...bucket].find((url) => !isClassTile(url) && url.endsWith('.pbf'));
    bucket.delete(basemapTile);
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed'));
    await waitFor(() => btn.dataset.downloadState === 'idle');
    window.pwaWarmCache.mockClear();

    btn.click();
    await waitFor(() => btn.dataset.downloadState === 'done');

    expect(warmedUrls(0).some(isClassTile)).toBe(false);
    // Recorded without the flag, nothing would ever count or evict the
    // class tiles, so the run removes them.
    expect('terrain' in (await recordedRegion())).toBe(false);
    const held = [...window.caches.buckets.get(`${PINNED_PREFIX}region-${REGION_ID}`)];
    expect(held.some(isClassTile)).toBe(false);
  });

  it('states what terrain data adds on the idle label', async () => {
    await forgetRegion();
    window.pwaBasemapDownloads.setTerrainDataPreferred(true);
    const btn = document.getElementById('map-download-control');
    document.dispatchEvent(
      new CustomEvent('snowdesk:region-selected', {
        detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
      }),
    );
    await waitFor(() => btn.dataset.downloadState === 'idle');

    expect(btn.getAttribute('aria-label')).toMatch(/terrain data/);

    window.pwaBasemapDownloads.setTerrainDataPreferred(false);
    await waitFor(() => !/terrain data/.test(btn.getAttribute('aria-label')));
    expect(btn.getAttribute('aria-label')).not.toMatch(/terrain data/);
  });
});

describe('a region record carries its principal (SNOW-1142)', () => {
  /** Forget the region on this device, so the next click downloads it. */
  async function forgetRegion() {
    await window.pwaDb.delete('meta:app', 'basemap.regions');
    window.caches.buckets.clear();
  }

  /** Rewrite the stored record's stamp — `undefined` removes it (legacy). */
  async function restamp(principal) {
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    const value = row.value.map((entry) => {
      if (entry.region_id !== REGION_ID) return entry;
      const { principal: _old, ...rest } = entry;
      return principal === undefined ? rest : { ...rest, principal };
    });
    await window.pwaDb.put('meta:app', { key: 'basemap.regions', value });
  }

  /** Lose one basemap tile, so the next tap is a fresh run over the bucket. */
  async function loseATile() {
    const btn = document.getElementById('map-download-control');
    const bucket = window.caches.buckets.get(`${PINNED_PREFIX}region-${REGION_ID}`);
    bucket.delete([...bucket].find((url) => url.endsWith('.pbf')));
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed'));
    await waitFor(() => btn.dataset.downloadState === 'idle');
    return btn;
  }

  afterEach(() => {
    setAccount('u-1');
  });

  it('stamps a new download with the signed-in account', async () => {
    await forgetRegion();
    await clickControl();

    expect((await recordedRegion()).principal).toBe('u-1');
  });

  it('stamps an anonymous download with the empty string', async () => {
    await forgetRegion();
    setAccount('');
    await clickControl();

    const record = await recordedRegion();
    expect(record).toHaveProperty('principal', '');
  });

  it('keeps the stamp it had through a content refresh', async () => {
    await forgetRegion();
    await clickControl();
    await restamp('u-2');

    await window.pwaBasemapDownloads.refreshAreaContent(`region-${REGION_ID}`);

    expect((await recordedRegion()).principal).toBe('u-2');
  });

  it('keeps another account\'s stamp through a re-download', async () => {
    await forgetRegion();
    await clickControl();
    await restamp('u-2');
    const btn = await loseATile();

    btn.click();
    await waitFor(() => btn.dataset.downloadState === 'done');

    expect((await recordedRegion()).principal).toBe('u-2');
  });

  it('records the download in one read-modify-write, never a get-then-put', async () => {
    // SNOW-1142 review: the record used to be written from a `get`
    // snapshot with a whole-array `put`. A claim another tab committed in
    // between (its `recordHeld` stamping a legacy area) was erased by that
    // put, leaving the area claimable by a second account. IndexedDB's
    // serialisation itself is proved in test_db.js; this pins that the
    // writer goes through it.
    await forgetRegion();
    window.pwaDb.put.mockClear();
    window.pwaDb.readModifyWrite.mockClear();

    await clickControl();

    const regionPuts = window.pwaDb.put.mock.calls.filter(
      ([, row]) => row && row.key === 'basemap.regions',
    );
    expect(regionPuts).toEqual([]);
    expect(
      window.pwaDb.readModifyWrite.mock.calls.some(([, key]) => key === 'basemap.regions'),
    ).toBe(true);
    expect((await recordedRegion()).principal).toBe('u-1');
  });

  it('adds no stamp to a legacy record through a re-download', async () => {
    await forgetRegion();
    await clickControl();
    await restamp(undefined);
    const btn = await loseATile();

    btn.click();
    await waitFor(() => btn.dataset.downloadState === 'done');

    expect('principal' in (await recordedRegion())).toBe(false);
  });
});

// SNOW-1308: every sync stamps the reader's own things it expected onto
// the area record, by name with their urls, so the /offline/ panels can
// list them offline whichever surface synced the area. The map's three
// entry points — the first download, the region roundel's refresh and the
// Manage downloads sheet's Sync now — all stamp the composition
// `areaItemExpectation` makes, which is the one /offline/'s Sync now
// stamps (tests/js/test_offline_audit.js) and the one `areaItemURLs`
// fetches from.
describe("the map's syncs stamp what they expected of your things (SNOW-1308)", () => {
  /** What the map's templates compose `AREA_ITEMS` into. */
  function expected() {
    return window.pwaBasemapDownloadCore.areaItemExpectation(AREA_ITEMS, {
      tripPage: '/trips/__UUID__/',
      tripRoute: '/trips/__UUID__/route.geojson',
      weatherDetail: '/api/weather/__SHORTID__/detail/',
      weatherDay: TODAY,
      favouriteSheet: '/partials/place-sheet/favourite/00000000-0000-0000-0000-000000000000/',
    });
  }

  it('stamps it on the first download', async () => {
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    await window.pwaDb.put('meta:app', {
      key: 'basemap.regions',
      value: row.value.filter((entry) => entry && entry.region_id !== REGION_ID),
    });
    await clickControl();

    const record = await recordedRegion();
    expect(record.items).toEqual(expected());
    expect(record.items.trips[0].urls).toEqual([
      `/trips/${TRIP_UUID}/`,
      `/trips/${TRIP_UUID}/route.geojson`,
    ]);
  });

  it("stamps it on the region roundel's refresh", async () => {
    const btn = document.getElementById('map-download-control');
    await waitFor(() => btn.dataset.downloadState === 'done');
    await window.pwaDb.readModifyWrite('meta:app', 'basemap.regions', (stored) => ({
      key: 'basemap.regions',
      value: stored.value.map((entry) => {
        if (!entry || entry.region_id !== REGION_ID) return entry;
        const { items: _gone, ...rest } = entry;
        return rest;
      }),
    }));
    window.pwaWarmCache.mockClear();

    btn.click();
    await waitFor(() => window.pwaWarmCache.mock.calls.length > 0);
    await waitFor(() => btn.dataset.downloadState !== 'busy');

    expect((await recordedRegion()).items).toEqual(expected());
  });

  it("stamps it on the Manage downloads sheet's Sync now", async () => {
    await window.pwaDb.put('meta:app', {
      key: 'basemap.customAreas',
      value: [
        {
          id: 'custom-a9',
          ordinal: 9,
          bbox: [7.0, 46.0, 7.2, 46.2],
          band: [10, 14],
          template: TEMPLATE,
          basemapKey: 'openfreemap_liberty',
          bytes: 4096,
          savedAt: '2026-01-05T10:00:00.000Z',
        },
      ],
    });

    expect(await window.pwaBasemapDownloads.refreshAreaContent('custom-a9')).toBe(true);

    const row = await window.pwaDb.get('meta:app', 'basemap.customAreas');
    expect(row.value[0].items).toEqual(expected());
  });

  it('leaves the old expectation alone when the items could not be asked', async () => {
    const before = (await recordedRegion()).items;
    areaItemsAnswer = () => null;
    try {
      await window.pwaBasemapDownloads.refreshAreaContent(`region-${REGION_ID}`);
    } finally {
      areaItemsAnswer = () => AREA_ITEMS;
    }

    const record = await recordedRegion();
    expect(record.contentIncomplete).toBe(true);
    expect(record.items).toEqual(before);
  });
});
