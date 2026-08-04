/*
 * tests/js/test_map_download_bytes.js — Vitest DOM test for SNOW-632: the
 * region download control's ``_recordRegionDownload`` (static/js/map.js)
 * measures its pinned bucket instead of accumulating a reported byte total.
 *
 * The bug: a re-download of an already-downloaded region at the SAME
 * basemap fetches the identical tile URLs into the SAME pinned bucket —
 * ``cache.put`` OVERWRITES each key, so the bucket does not grow — but the
 * pre-fix code added the run's reported ``bytes`` onto whatever was already
 * recorded regardless, doubling the figure on every repeat with nothing new
 * on disk. This drives the real ``mapDownloadControlInit`` click flow
 * through the real ``basemap_download_runner.js`` twice against the SAME
 * region and asserts the recorded total does not move, and that it comes
 * from measuring the bucket (not from the run's own reported figure, which
 * this harness deliberately sets to a different, wrong number).
 *
 * Booting map.js in jsdom follows the same pattern as
 * ``test_map_download_eviction.js`` (see its header for why: one script of
 * top-level IIFEs, ``FEATURE_BY_REGION_ID`` populated off the stubbed
 * ``map.on('load')`` handler, module-private top-level bindings). This file
 * additionally imports ``basemap_download_runner.js`` — the eviction test
 * deliberately leaves it unloaded so `runPinnedDownload` aborts before
 * doing anything, but a run that has to actually WRITE bytes needs the real
 * runner — and gives the stub map a working vector source, so
 * ``activeBasemapTileTemplate`` returns a template instead of the
 * eviction test's deliberate "style still settling" null.
 *
 * ``caches`` here is a richer stub than the eviction test's: entries carry
 * an actual byte size, retrievable via ``response.headers.get
 * ('Content-Length')`` — what ``measurePinnedBucketBytes`` reads — so a
 * measurement-based assertion is genuinely exercisable, not merely a
 * fallback path.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const REGION_ID = 'CH-4115';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
// Deliberately NOT what the measured bucket total will be — every
// assertion below is against the MEASURED figure, so a test that
// accidentally matched the reported one would prove nothing.
const REPORTED_BYTES = 999;
const TILE_BYTES = 4096;

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
 * working vector source so ``activeBasemapTileTemplate`` answers
 * ``TEMPLATE`` instead of null — this suite needs the run to actually
 * reach the warm-cache step, not abort before it.
 */
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

/**
 * Cache Storage stub over ``name -> Map<url, byteSize|null>``. Unlike
 * ``test_map_download_eviction.js``'s (URLs only, no sizes), ``put``
 * records a real byte figure off the response's ``Content-Length`` header
 * and ``match`` hands it back the same way ``measurePinnedBucketBytes``
 * reads a real Cache Storage entry — the point of this file is that the
 * measurement path is genuinely exercised, not stood in for.
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
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="standard"
         data-season-end="2026-05-31"></div>
    <button id="map-download-control" type="button"></button>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div id="map-download-evict-confirm" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-evict-confirm-title">Free up space?</p>
      <p id="map-download-evict-confirm-body"></p>
      <button id="map-download-evict-confirm-cta" type="button">Delete and download</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>`;
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

/** The recorded ``basemap.regions`` entry for REGION_ID, or undefined. */
async function recordedRegion() {
  const row = await window.pwaDb.get('meta:app', 'basemap.regions');
  const list = Array.isArray(row && row.value) ? row.value : [];
  return list.find((entry) => entry && entry.region_id === REGION_ID);
}

/** Sum of every byte size the pinned bucket for `areaId` actually holds. */
function measuredBucketTotal(cachesStub, areaId) {
  const store = cachesStub.buckets.get(PINNED_PREFIX + areaId) || new Map();
  let total = 0;
  for (const size of store.values()) if (Number.isFinite(size)) total += size;
  return total;
}

let mapStub;
let cachesStub;
let core;

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
      if (href.includes('region-basemap-tiles')) body = REGION_BLOB;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  // The stub `_warmCache` a real page reaches via a postMessage round trip
  // to sw.js: writes every URL into the run's real pinned bucket (mirroring
  // what the SW's own warm-cache handler does) and reports a `bytes` figure
  // deliberately wrong (`REPORTED_BYTES`), so an assertion that happens to
  // pass against the REPORTED figure rather than the MEASURED one is
  // exposed rather than accidentally satisfied.
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) {
      await cache.put(url, {
        headers: { get: (h) => (h.toLowerCase() === 'content-length' ? String(TILE_BYTES) : null) },
      });
    }
    return { ok: urls.length, failed: 0, bytes: REPORTED_BYTES, cancelled: false };
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  // SNOW-618: map.js's search box delegates to this; home.html loads it
  // immediately before map.js, and map.js does not guard for its absence.
  await import('../../static/js/search_core.js');
  // SNOW-623: map.js's choropleth paint delegates to this.
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/map.js');
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
  await waitFor(() => btn.dataset.downloadState === 'done', 5000);
  expect(btn.dataset.downloadState).toBe('done');
}

describe('region download byte recording (SNOW-632)', () => {
  it('records the measured bucket size, not the run-reported figure', async () => {
    await downloadRegion();

    const areaId = core.areaIdForRegion(REGION_ID);
    const measured = measuredBucketTotal(cachesStub, areaId);
    // Every URL this run wrote (feed URLs + the one tile) carries
    // TILE_BYTES, and there is at least the tile itself — a non-trivial,
    // non-zero total to compare the record against.
    expect(measured).toBeGreaterThan(0);
    expect(measured).not.toBe(REPORTED_BYTES);

    const recorded = await recordedRegion();
    expect(recorded).toBeDefined();
    expect(recorded.bytes).toBe(measured);
  });

  it('does not inflate the total on a same-basemap repeat', async () => {
    const before = await recordedRegion();
    expect(before).toBeDefined();

    // Re-download the SAME region — the exact same URLs land in the exact
    // same bucket. A `cache.put` overwrite doesn't change the bucket's
    // size, so the pre-fix accumulation (previous + this run's reported
    // bytes) would have doubled `before.bytes`; the fix must not move it.
    await downloadRegion();

    const after = await recordedRegion();
    expect(after).toBeDefined();
    expect(after.bytes).toBe(before.bytes);
    expect(after.bytes).not.toBe(before.bytes + REPORTED_BYTES);
    expect(after.bytes).not.toBe(2 * before.bytes);
  });
});
