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
    // SNOW-931's hanging thread. A country the client could not fetch is
    // never listed, so there is nothing in the tally to fail — the run
    // reports `ok === total` over a list that was missing bulletins. Before
    // this the shortfall reached the debug log and nothing else.
    const countries = window.pwaMapCountries;
    window.pwaMapCountries = {
      ensureAllLoaded: async () => ({ loaded: ['ch', 'at', 'it'], failed: ['fr'] }),
    };
    try {
      expect(await tapAndSettle()).toBe('partial');
    } finally {
      window.pwaMapCountries = countries;
    }

    const record = await recordedRegion();
    expect(record.contentIncomplete).toBe(true);
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
    expect(urls).toContain('/api/weather/INSIDEaaaaa/detail/');
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
