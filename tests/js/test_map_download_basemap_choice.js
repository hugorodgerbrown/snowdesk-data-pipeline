/*
 * tests/js/test_map_download_basemap_choice.js — a Swiss region saves the
 * Swisstopo map whatever map is on screen (SNOW-1478).
 *
 * Hugo, airplane mode, 8 Nov 2026: Martigny-Verbier was saved on
 * OpenFreeMap while Swisstopo was on screen, so offline the Swisstopo map
 * drew only what browsing had left. What is pinned here, through the real
 * region roundel, runner and record writer:
 *
 *   - with OpenFreeMap on screen, a Swiss region's download fetches the
 *     Swisstopo map, read from its style and TileJSON, and none of
 *     OpenFreeMap's tiles;
 *   - the record names Swisstopo, top-level and in `basemaps`;
 *   - the roundel then reads done, not "downloaded under another basemap".
 *
 * Harness: test_map_download_bytes.js's, verbatim but for the Swiss region
 * and the Swisstopo documents.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const TEMPLATE_A = 'https://tiles-a.example.invalid/{z}/{x}/{y}.pbf';
const TEMPLATE_B = 'https://tiles-b.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const TILE_BYTES = 4096;

// SNOW-1478: the Swisstopo map, as the documents a download reads when it is
// not the map on screen. One vector source, no sprite, no glyphs.
const SWISS_STYLE_URL = 'https://swiss.example.invalid/styles/winter.json';
const SWISS_TILEJSON_URL = 'https://swiss.example.invalid/tiles/base/tiles.json';
const SWISS_TILES = ['https://vt.swiss.example.invalid/base/{z}/{x}/{y}.pbf'];
const SWISS_STYLE = {
  version: 8,
  sources: { base: { type: 'vector', url: SWISS_TILEJSON_URL } },
  layers: [],
};
const SWISS_TILEJSON = { tiles: SWISS_TILES };

/** One region carrying a precomputed download summary, as /api/regions.geojson emits. */
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: REGION_ID,
        name: 'Martigny — Verbier',
        download: {
          count: 1,
          mb: 1,
          over_ceiling: false,
          centre_tile: { z: 14, x: 8577, y: 5811 },
        },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.01, 46.0], [7.01, 46.01], [7.0, 46.01], [7.0, 46.0]]],
      },
    },
  ],
};

/** The blob /api/region-basemap-tiles/ would answer with for REGION_ID — one tile. */
const REGION_BLOB = {
  band: [10, 14],
  count: 1,
  mb: 1,
  over_ceiling: false,
  centre_tile: { z: 14, x: 8577, y: 5811 },
  z: { 14: [8577, 8577, 5811, 5811] },
};

/**
 * Minimal MapLibre stub, as ``test_map_download_eviction.js``'s, but with a
 * MUTABLE vector source — ``setActiveTemplate`` lets a test switch which
 * template ``activeBasemapTileTemplate`` resolves, to exercise the
 * template-change eviction between two downloads of the same region.
 */
function stubMapLibre() {
  const handlers = {};
  let activeTemplate = TEMPLATE_A;
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
    getSource: (id) => (id === 'basemap' ? { tiles: [activeTemplate] } : null),
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
    setActiveTemplate: (template) => {
      activeTemplate = template;
    },
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
 * Cache Storage stub over ``name -> Map<url, byteSize|null>``. Unlike
 * ``test_map_download_eviction.js``'s (URLs only, no sizes), ``put``
 * records a real byte figure off the response's ``Content-Length`` header
 * and ``match`` hands it back the same way — so a test can inspect exactly
 * what a bucket holds after eviction, even though the fix under test no
 * longer reads these sizes itself.
 */
function installCachesStub() {
  const buckets = new Map();
  const openBucket = (name) => {
    if (!buckets.has(name)) buckets.set(name, new Map());
    return buckets.get(name);
  };
  const stub = {
    buckets,
    keys: vi.fn(async () => [...buckets.keys()]),
    open: vi.fn(async (name) => {
      const store = openBucket(name);
      return {
        keys: async () => [...store.keys()].map((url) => ({ url })),
        // SNOW-871: the per-entry delete the post-success prune uses. The
        // real Cache Storage API returns whether the entry was there.
        delete: vi.fn(async (url) => store.delete(url)),
        put: vi.fn(async (url, response) => {
          const raw = response && response.headers && response.headers.get('Content-Length');
          const length = Number(raw);
          store.set(url, Number.isFinite(length) ? length : null);
        }),
        match: async (request) => {
          const url = typeof request === 'string' ? request : request.url;
          if (!store.has(url)) return undefined;
          const size = store.get(url);
          return {
            headers: {
              get: (h) =>
                h.toLowerCase() === 'content-length' && size !== null ? String(size) : null,
            },
          };
        },
      };
    }),
    delete: vi.fn(async (name) => buckets.delete(name)),
  };
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  return stub;
}

/** In-memory `meta:app`, seeded with the records the download controls write. */
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

/** The DOM map.js's boot and this control read: map, roundel, search pill. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-area-content-url="/api/area-content/"
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
    <!-- SNOW-645: activeBasemapKey() reads the checked radio here. The
         boot IIFE (map.js:150-159) is what actually sets aria-checked on
         first paint, from data-default-basemap-key above matching this
         row's data-basemap-key — not this markup's own initial value. A
         second row lets the "other-basemap" tests below switch between
         two real keys via switchBasemap(). -->
    <ul id="basemap-menu">
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="https://tiles.example.invalid/liberty.json"
          data-basemap-countries="ch fr at it"
          aria-checked="false"
        >OpenFreeMap</button>
      </li>
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="swisstopo_winter"
          data-basemap-url="${SWISS_STYLE_URL}"
          data-basemap-countries="ch"
          aria-checked="false"
        >Swisstopo (CH)</button>
      </li>
    </ul>
    <div id="map-download-evict-confirm" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-evict-confirm-title">Free up space?</p>
      <p id="map-download-evict-confirm-body"></p>
      <button id="map-download-evict-confirm-cta" type="button">Delete and download</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>
    <!-- SNOW-871: the REPLACE confirm. A download of a region already held
         under another basemap now asks before replacing it, so every
         template-change run below has to answer this banner to get as far
         as a warm — see answerReplaceConfirm(). -->
    <div id="map-download-replace-confirm" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-replace-confirm-title">Replace your earlier download?</p>
      <p id="map-download-replace-confirm-body"></p>
      <button id="map-download-replace-confirm-cta" type="button">Replace and download</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>
    <!-- Hugo, 12 Nov 2026: the confirm a Swiss region held on another map
         raises instead of the replace one. -->
    <div id="map-download-sync-instead" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-sync-instead-title">You already have this area.</p>
      <p id="map-download-sync-instead-body"></p>
      <button id="map-download-sync-instead-cta" type="button">Sync</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>`;
}

/**
 * Answer SNOW-871's replace confirm with "yes", if it appears.
 *
 * Every run in this file that switches basemap is replacing a copy this
 * device already holds, which is exactly the case that now raises the
 * banner. The tests here are about what gets RECORDED and what is left in
 * the bucket, so they confirm and carry on; the confirm's own behaviour —
 * when it is raised, and what declining does — is
 * tests/js/test_map_download_replace.js.
 *
 * @returns {Promise<void>}
 */
async function answerReplaceConfirm() {
  const banner = document.getElementById('map-download-replace-confirm');
  await waitFor(() => !banner.classList.contains('hidden'), 500);
  if (!banner.classList.contains('hidden')) {
    document.getElementById('map-download-replace-confirm-cta').click();
  }
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/**
 * Flip the picker's checked radio to `key` and switch the stub map's
 * active template, then dispatch the same event map.js's own `styledata`
 * handler fires once a real basemap switch has finished re-installing
 * everything (map.js:4012's `snowdesk:basemap-changed`) — the signal
 * map_region_download.js's `renderControl` listens for.
 *
 * @param {string} key
 * @param {string} template
 * @returns {void}
 */
function switchBasemap(key, template) {
  for (const btn of document.querySelectorAll('#basemap-menu [data-basemap-key]')) {
    btn.setAttribute('aria-checked', btn.dataset.basemapKey === key ? 'true' : 'false');
  }
  mapStub.setActiveTemplate(template);
  document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
}

/** The recorded ``basemap.regions`` entry for REGION_ID, or undefined. */
async function recordedRegion() {
  const row = await window.pwaDb.get('meta:app', 'basemap.regions');
  const list = Array.isArray(row && row.value) ? row.value : [];
  return list.find((entry) => entry && entry.region_id === REGION_ID);
}

/** Every URL key currently held in the pinned bucket for `areaId`. */
function bucketKeys(cachesStub, areaId) {
  const store = cachesStub.buckets.get(PINNED_PREFIX + areaId) || new Map();
  return [...store.keys()];
}

let mapStub;
let cachesStub;
let core;
/** The `bytes` figure `pwaWarmCache`'s stub reports for the NEXT run. */
let nextReportedBytes;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  cachesStub = installCachesStub();
  installDbStub({});
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
      // SNOW-953: a download asks the server what its boundary contains.
      // This fixture is about tiles and bytes, so the answer is empty —
      // but it has to BE an answer: an endpoint that cannot be read makes
      // the plan short, which is a failed run rather than a quiet one.
      if (href.includes('area-content')) body = { regions: [], weather: [], resorts: [] };
      if (href.includes('region-basemap-tiles')) body = REGION_BLOB;
      // SNOW-1478: the Swisstopo map's documents, read when it is not on screen.
      if (href === SWISS_STYLE_URL) body = SWISS_STYLE;
      if (href === SWISS_TILEJSON_URL) body = SWISS_TILEJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  // The stub `_warmCache` a real page reaches via a postMessage round trip
  // to sw.js: writes every URL into the run's real pinned bucket (mirroring
  // what the SW's own warm-cache handler does) and reports whatever
  // `nextReportedBytes` currently holds, so each test controls exactly what
  // figure this run claims independently of what actually landed on disk.
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) {
      await cache.put(url, {
        headers: { get: (h) => (h.toLowerCase() === 'content-length' ? String(TILE_BYTES) : null) },
      });
    }
    return { ok: urls.length, failed: 0, bytes: nextReportedBytes, cancelled: false };
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/swisstopo_extent.js');
  await import('../../static/js/basemap_download_runner.js');
  // SNOW-618: map.js's search box delegates to this; home.html loads it
  // immediately before map.js, and map.js does not guard for its absence.
  await import('../../static/js/search_core.js');
  // SNOW-623: map.js's choropleth paint delegates to this.
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  core = window.pwaBasemapDownloadCore;
  // MapLibre never fires 'load' in jsdom; the main IIFE's data load (and so
  // FEATURE_BY_REGION_ID) hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete globalThis.maplibregl;
});

/** Select REGION_ID and click the download control through to a settled state. */
async function downloadRegion() {
  const btn = document.getElementById('map-download-control');
  document.dispatchEvent(
    new CustomEvent('snowdesk:region-selected', {
      detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
    }),
  );
  await waitFor(
    () => btn.dataset.downloadState === 'idle' || btn.dataset.downloadState === 'done',
  );
  btn.click();
  await answerReplaceConfirm();
  await waitFor(() => btn.dataset.downloadState === 'done', 5000);
  expect(btn.dataset.downloadState).toBe('done');
}

describe('a Swiss region with OpenFreeMap on screen (SNOW-1478)', () => {
  it('saves the Swisstopo map, read from its documents', async () => {
    nextReportedBytes = 2048;
    switchBasemap('openfreemap_liberty', TEMPLATE_A);

    await downloadRegion();

    const urls = window.pwaWarmCache.mock.calls.at(-1)[0];
    expect(urls).toContain(SWISS_STYLE_URL);
    expect(urls).toContain(SWISS_TILEJSON_URL);
    expect(urls).toContain('https://vt.swiss.example.invalid/base/14/8577/5811.pbf');
    expect(urls.some((url) => url.startsWith('https://tiles-a.example.invalid/'))).toBe(false);
    expect(urls).not.toContain('https://tiles.example.invalid/liberty.json');
  });

  it('records Swisstopo, top-level and as its one map', async () => {
    const record = await recordedRegion();
    expect(record.basemapKey).toBe('swisstopo_winter');
    expect(record.template).toEqual([SWISS_TILES]);
    expect(record.deps).toEqual([SWISS_STYLE_URL, SWISS_TILEJSON_URL]);
    expect(record.basemaps).toEqual([
      { key: 'swisstopo_winter', template: [SWISS_TILES], deps: [SWISS_STYLE_URL, SWISS_TILEJSON_URL] },
    ]);
  });

  it('reads done with OpenFreeMap still on screen, not another basemap', async () => {
    const btn = document.getElementById('map-download-control');
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
    await waitFor(() => btn.dataset.downloadState === 'done');
    expect(btn.dataset.downloadState).toBe('done');
  });

  it('with its own map on screen, still judges the second map it holds', async () => {
    // Codex on PR #1542: a Sync-upgraded area holds Swisstopo first and its
    // old map second. Swisstopo on screen must not read done while the
    // second map's tiles are missing.
    const btn = document.getElementById('map-download-control');
    switchBasemap('swisstopo_winter', SWISS_TILES[0]);
    await waitFor(() => btn.dataset.downloadState === 'done');
    expect(btn.dataset.downloadState).toBe('done');

    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    const list = row.value.map((entry) =>
      entry.region_id === REGION_ID
        ? {
            ...entry,
            basemaps: [
              ...entry.basemaps,
              { key: 'openfreemap_liberty', template: [[TEMPLATE_B]], deps: [] },
            ],
          }
        : entry,
    );
    await window.pwaDb.put('meta:app', { key: 'basemap.regions', value: list });
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
    await new Promise((resolve) => setTimeout(resolve, 300));
    await waitFor(() => btn.dataset.downloadState === 'done', 500);
    expect(btn.dataset.downloadState).not.toBe('done');
  });
});

describe('a Swiss region held on OpenFreeMap offers Sync, not replace (Hugo, 12 Nov 2026)', () => {
  const AREA_ID = 'region-' + REGION_ID;
  const OLD_TILE = 'https://tiles-a.example.invalid/14/8577/5811.pbf';

  /** A copy saved on OpenFreeMap before SNOW-1478, its tile still on disk. */
  async function seedOpenFreeMapCopy() {
    await window.pwaDb.put('meta:app', {
      key: 'basemap.regions',
      value: [
        {
          region_id: REGION_ID,
          name: 'Martigny — Verbier',
          band: [10, 14],
          z: REGION_BLOB.z,
          template: TEMPLATE_A,
          basemapKey: 'openfreemap_liberty',
          deps: [],
          bytes: 1024,
          savedAt: '2026-08-01T10:00:00.000Z',
        },
      ],
    });
    cachesStub.buckets.set(PINNED_PREFIX + AREA_ID, new Map([[OLD_TILE, {}]]));
  }

  /** Focus the region and tap its download control. */
  async function tapDownload() {
    const btn = document.getElementById('map-download-control');
    switchBasemap('openfreemap_liberty', TEMPLATE_A);
    document.dispatchEvent(
      new CustomEvent('snowdesk:region-selected', {
        detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
      }),
    );
    await waitFor(() => !!btn.dataset.downloadState && btn.dataset.downloadState !== 'checking', 1000);
    btn.click();
  }

  const banner = () => document.getElementById('map-download-sync-instead');
  const regionWarms = () =>
    window.pwaWarmCache.mock.calls.filter((call) => call[1] && call[1].areaId === AREA_ID).length;

  it('asks to Sync, naming the area, and the Sync button syncs it in the Downloads sheet', async () => {
    await seedOpenFreeMapCopy();
    const sync = vi.fn(async () => {});
    window.pwaDownloadsManager = { sync: sync };
    const warmsBefore = regionWarms();

    await tapDownload();
    await waitFor(() => !banner().classList.contains('hidden'));

    expect(banner().classList.contains('hidden')).toBe(false);
    expect(document.getElementById('map-download-sync-instead-title').textContent).toBe(
      'You already have Martigny — Verbier. Sync it to add the Swiss map?',
    );
    expect(document.getElementById('map-download-sync-instead-body').textContent).toBe(
      'Nothing you have saved is removed.',
    );
    expect(document.getElementById('map-download-replace-confirm').classList.contains('hidden')).toBe(true);

    document.getElementById('map-download-sync-instead-cta').click();
    await waitFor(() => sync.mock.calls.length > 0);

    expect(sync).toHaveBeenCalledWith(AREA_ID);
    // The tap itself downloads nothing and replaces nothing.
    expect(regionWarms()).toBe(warmsBefore);
    expect(bucketKeys(cachesStub, AREA_ID)).toContain(OLD_TILE);
    expect((await recordedRegion()).basemapKey).toBe('openfreemap_liberty');
    delete window.pwaDownloadsManager;
  });

  it('closing it does nothing at all', async () => {
    await seedOpenFreeMapCopy();
    const sync = vi.fn(async () => {});
    window.pwaDownloadsManager = { sync: sync };
    const warmsBefore = regionWarms();

    await tapDownload();
    await waitFor(() => !banner().classList.contains('hidden'));
    banner().classList.add('hidden');
    document.dispatchEvent(new CustomEvent('overlay:dismissed', { detail: { overlay: banner() } }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(sync).not.toHaveBeenCalled();
    expect(regionWarms()).toBe(warmsBefore);
    expect(bucketKeys(cachesStub, AREA_ID)).toContain(OLD_TILE);
    delete window.pwaDownloadsManager;
  });
});
