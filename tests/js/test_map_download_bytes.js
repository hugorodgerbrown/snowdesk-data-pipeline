/*
 * tests/js/test_map_download_bytes.js — Vitest DOM test for SNOW-632: the
 * region download control's ``_recordRegionDownload`` (static/js/map.js)
 * records each run's own reported byte total, replacing any previous
 * figure — never accumulating onto it, and never re-measuring the pinned
 * bucket.
 *
 * The bug this covers (twice, in two directions):
 *
 *   1. A re-download of an already-downloaded region under the SAME
 *      basemap must not inflate the recorded total. The original bug
 *      accumulated the run's reported ``bytes`` onto whatever was already
 *      recorded, doubling the figure on every repeat even though a
 *      same-template retry fetches identical URLs into the identical
 *      bucket (``cache.put`` OVERWRITES each key, so the bucket does not
 *      grow). A later attempt fixed this by MEASURING the bucket instead
 *      of trusting the reported figure — which is what this suite used to
 *      assert — but that measurement reads a Response's ``Content-Length``
 *      header only, and a real tile response under the gzip encoding every
 *      browser requests carries no such header at all, so it read 0 in
 *      production and silently fell back to the run's own reported figure
 *      anyway (see docs/decisions/per-area-pinned-basemap-caches.md for
 *      the curl evidence). The fix under test here is simpler: record the
 *      run's own reported bytes directly, replacing the previous record.
 *   2. A re-download of the SAME region under a DIFFERENT basemap must
 *      evict the old basemap's tiles from the bucket FIRST — otherwise
 *      they sit alongside the new run's tiles, and "record the run's own
 *      bytes" under-counts what the bucket actually holds. This drives the
 *      real ``mapDownloadControlInit`` click flow through the real
 *      ``basemap_download_runner.js`` with the stub map's active tile
 *      template switched between runs, and asserts both that the bucket
 *      was cleared before the second run wrote to it and that the
 *      recorded total is the second run's own figure, not a sum.
 *
 * Booting map.js in jsdom follows the same pattern as
 * ``test_map_download_eviction.js`` (see its header for why: one script of
 * top-level IIFEs, ``FEATURE_BY_REGION_ID`` populated off the stubbed
 * ``map.on('load')`` handler, module-private top-level bindings). This file
 * additionally imports ``basemap_download_runner.js`` — the eviction test
 * deliberately leaves it unloaded so `runPinnedDownload` aborts before
 * doing anything, but a run that has to actually WRITE bytes needs the real
 * runner — and gives the stub map a working, MUTABLE vector source, so
 * ``activeBasemapTileTemplate`` can be switched between downloads to
 * exercise the template-change eviction.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const TEMPLATE_A = 'https://tiles-a.example.invalid/{z}/{x}/{y}.pbf';
const TEMPLATE_B = 'https://tiles-b.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
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
          aria-checked="false"
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
    </ul>
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
      if (href.includes('region-basemap-tiles')) body = REGION_BLOB;
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
  await waitFor(() => btn.dataset.downloadState === 'done', 5000);
  expect(btn.dataset.downloadState).toBe('done');
}

describe('region download byte recording (SNOW-632)', () => {
  it("records the run's own reported bytes, not a bucket measurement", async () => {
    nextReportedBytes = 999;
    await downloadRegion();

    const areaId = core.areaIdForRegion(REGION_ID);
    const keys = bucketKeys(cachesStub, areaId);
    // Every URL this run wrote (feed URLs + the one tile) carries
    // TILE_BYTES, so the bucket's real measured size is
    // `keys.length * TILE_BYTES` — deliberately NOT what was reported
    // (999), so an assertion that happened to match the measured figure
    // rather than the reported one would be exposed rather than
    // accidentally satisfied.
    const measured = keys.length * TILE_BYTES;
    expect(measured).not.toBe(nextReportedBytes);

    const recorded = await recordedRegion();
    expect(recorded).toBeDefined();
    expect(recorded.bytes).toBe(nextReportedBytes);
    expect(recorded.bytes).not.toBe(measured);
  });

  it("records the picker's active basemap key, and paints it onto the roundel (SNOW-645)", async () => {
    nextReportedBytes = 111;
    await downloadRegion();

    const recorded = await recordedRegion();
    expect(recorded).toBeDefined();
    expect(recorded.basemapKey).toBe('openfreemap_liberty');

    const btn = document.getElementById('map-download-control');
    expect(btn.dataset.basemapKey).toBe('openfreemap_liberty');
  });

  it('does not inflate the total on a same-template repeat', async () => {
    const before = await recordedRegion();
    expect(before).toBeDefined();

    // Re-download the SAME region at the SAME active template — the exact
    // same URLs land in the exact same bucket. A `cache.put` overwrite
    // doesn't change the bucket's size, and this run reports the SAME
    // figure as before, so the pre-fix accumulation (previous + this run's
    // reported bytes) would have doubled `before.bytes`; the fix must not
    // move it.
    nextReportedBytes = before.bytes;
    await downloadRegion();

    const after = await recordedRegion();
    expect(after).toBeDefined();
    expect(after.bytes).toBe(before.bytes);
    expect(after.bytes).not.toBe(2 * before.bytes);
  });

  it('evicts the bucket and records only the new run when the active template changes', async () => {
    const areaId = core.areaIdForRegion(REGION_ID);
    const before = await recordedRegion();
    expect(before).toBeDefined();
    const beforeKeys = bucketKeys(cachesStub, areaId);
    // At least the one tile URL (plus feed URLs) was fetched against
    // TEMPLATE_A.
    const beforeTileKeysA = beforeKeys.filter((k) => k.includes('tiles-a.example.invalid'));
    expect(beforeTileKeysA.length).toBeGreaterThan(0);

    // Switch the active basemap — same region, same bbox, different
    // template — and report a DIFFERENT bytes figure for this run, so an
    // accumulation bug (`before.bytes + this run's bytes`) is
    // distinguishable from a correct replacement.
    mapStub.setActiveTemplate(TEMPLATE_B);
    nextReportedBytes = before.bytes + 12345;
    await downloadRegion();

    const after = await recordedRegion();
    expect(after).toBeDefined();
    expect(after.bytes).toBe(nextReportedBytes);
    expect(after.template).toBe(TEMPLATE_B);
    expect(after.bytes).not.toBe(before.bytes + nextReportedBytes);

    // The bucket was evicted before the second run wrote to it: TEMPLATE_B's
    // tile URLs are present, and none of TEMPLATE_A's survive alongside
    // them — proving eviction happened rather than the bucket simply
    // accumulating a second basemap's tiles on top of the first.
    const afterKeys = bucketKeys(cachesStub, areaId);
    const afterTileKeysA = afterKeys.filter((k) => k.includes('tiles-a.example.invalid'));
    const afterTileKeysB = afterKeys.filter((k) => k.includes('tiles-b.example.invalid'));
    expect(afterTileKeysB.length).toBeGreaterThan(0);
    expect(afterTileKeysA.length).toBe(0);
  });
});

/**
 * Click the control and wait for a settled 'done' — tolerant of every
 * ACTIONABLE starting state ('idle', 'error', SNOW-645's 'other-basemap'),
 * unlike `downloadRegion()` above, which only waits for 'idle'/'done' and
 * so cannot be reused once 'other-basemap' is in play.
 *
 * @returns {Promise<void>}
 */
async function runDownloadAndAwaitDone() {
  const btn = document.getElementById('map-download-control');
  await waitFor(() => {
    const s = btn.dataset.downloadState;
    return s === 'idle' || s === 'done' || s === 'other-basemap' || s === 'error';
  });
  if (btn.dataset.downloadState !== 'done') {
    btn.click();
    await waitFor(() => btn.dataset.downloadState === 'done', 5000);
  }
  expect(btn.dataset.downloadState).toBe('done');
}

describe('the other-basemap roundel state (SNOW-645)', () => {
  const btn = () => document.getElementById('map-download-control');

  it('reads other-basemap, with the OTHER (recorded) key — not the active one — when its tiles are still cached', async () => {
    // Clean baseline: a real download under openfreemap_liberty/TEMPLATE_A.
    switchBasemap('openfreemap_liberty', TEMPLATE_A);
    nextReportedBytes = 5001;
    await runDownloadAndAwaitDone();
    expect((await recordedRegion()).basemapKey).toBe('openfreemap_liberty');

    // Switch to a DIFFERENT basemap. SNOW-632's beforeWarm only evicts a
    // region's bucket on a SAME-region RE-download; switching basemap
    // alone deletes nothing, so the tiles just fetched are still on disk
    // — the region genuinely IS downloaded, just not for what's showing.
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await waitFor(() => btn().dataset.downloadState === 'other-basemap');

    expect(btn().dataset.downloadState).toBe('other-basemap');
    expect(btn().dataset.basemapKey).toBe('openfreemap_liberty');
    // Actionable, unlike 'done'/'busy' — see the next test for the click.
    expect(btn().getAttribute('aria-disabled')).toBe('false');
  });

  it('is actionable — a click downloads the region under the ACTIVE basemap', async () => {
    expect(btn().dataset.downloadState).toBe('other-basemap');

    nextReportedBytes = 5002;
    btn().click();
    await waitFor(() => btn().dataset.downloadState === 'busy');
    await waitFor(() => btn().dataset.downloadState === 'done', 5000);

    const recorded = await recordedRegion();
    expect(recorded.template).toBe(TEMPLATE_B);
    expect(recorded.basemapKey).toBe('swisstopo_winter');
    expect(btn().dataset.basemapKey).toBe('swisstopo_winter');
  });

  it('reads done, unchanged, once the record matches the active basemap and is fully cached', () => {
    // The previous test's own download already put the control here —
    // regression coverage that the ordinary "same basemap" path still
    // reads done after _probeDone's SNOW-645 rework.
    expect(btn().dataset.downloadState).toBe('done');
    expect(btn().dataset.basemapKey).toBe('swisstopo_winter');
  });

  it("reads idle, not other-basemap, once the OTHER basemap's bucket has been evicted out-of-band (a stale record)", async () => {
    const areaId = core.areaIdForRegion(REGION_ID);

    // Switch away — the record currently on disk (swisstopo_winter, from
    // the previous test) is still fully cached, so this reads
    // other-basemap first, confirming the setup before it goes stale.
    switchBasemap('openfreemap_liberty', TEMPLATE_A);
    await waitFor(() => btn().dataset.downloadState === 'other-basemap');
    expect(btn().dataset.basemapKey).toBe('swisstopo_winter');

    // Simulate an OUT-OF-BAND eviction — the browser reclaiming Cache
    // Storage under pressure, say — that drops the bucket WITHOUT going
    // through evictBasemapAreas (which deletes the IndexedDB record right
    // alongside the bucket, leaving nothing to read stale in the first
    // place). This is exactly the gap the cache check in _probeDone
    // exists to catch: the record still claims swisstopo_winter, but its
    // tiles are gone — a stale record must not promise them back.
    cachesStub.buckets.delete('snowdesk-basemap-pinned-' + areaId);
    switchBasemap('openfreemap_liberty', TEMPLATE_A);
    await waitFor(() => btn().dataset.downloadState !== 'busy' && btn().dataset.downloadState !== 'other-basemap');

    // 'idle' paints the ACTIVE basemap's own colour, same as every state
    // except 'other-basemap' (setState's documented default) — it is the
    // MISSING swisstopo_winter ring, not the presence of any key, that
    // proves the stale record was rejected rather than trusted.
    expect(btn().dataset.downloadState).toBe('idle');
    expect(btn().dataset.basemapKey).toBe('openfreemap_liberty');
  });

  it('reads other-basemap with the name-less label, and no key attribute, for a pre-SNOW-645 record with no basemapKey', async () => {
    // A real, valid record + real cached tiles under
    // openfreemap_liberty/TEMPLATE_A — the previous test left the control
    // reading idle at exactly that basemap, so this is a genuine new
    // download rather than a stale leftover.
    nextReportedBytes = 5003;
    await runDownloadAndAwaitDone();
    expect((await recordedRegion()).basemapKey).toBe('openfreemap_liberty');

    // Simulate a record written BEFORE SNOW-645 shipped: strip
    // basemapKey in place, leaving `template` and the bucket's real tiles
    // untouched — exactly what a pre-existing record looks like today.
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    const next = row.value.map((entry) =>
      entry.region_id === REGION_ID ? { ...entry, basemapKey: undefined } : entry,
    );
    await window.pwaDb.put('meta:app', { key: 'basemap.regions', value: next });

    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await waitFor(() => btn().dataset.downloadState === 'other-basemap');

    expect(btn().dataset.downloadState).toBe('other-basemap');
    expect(btn().dataset.basemapKey).toBeUndefined();
    // The name-less variant — never interpolated with an empty name.
    expect(btn().getAttribute('aria-label')).toBe(
      "This region's basemap is downloaded for another basemap — tap to download it for this basemap too",
    );
  });
});
