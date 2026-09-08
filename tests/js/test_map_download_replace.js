/*
 * tests/js/test_map_download_replace.js — SNOW-871: downloading a region
 * under a second basemap REPLACES the copy this device already holds for
 * the first, so it must ask before doing it and must not do it until the
 * replacement has actually landed.
 *
 * A region's pinned bucket is keyed on the region id alone
 * (`areaIdForRegion` → `region-<id>`), so the two basemaps cannot both
 * live in it. SNOW-632 resolved that in the region control's `beforeWarm`,
 * by deleting the whole bucket before the warm run started, without asking.
 * Two defects followed, and this suite is one test per corner of them:
 *
 *   - it was SILENT, while every other destructive control on the map
 *     confirms first; and
 *   - a run that then FAILED — the ordinary outcome on the connection this
 *     whole feature exists for — left the user with neither the copy they
 *     had nor the one they asked for.
 *
 * Every test here starts from a SEEDED PRIOR RECORD plus the tiles that
 * record claims, never from an empty store: two byte/state bugs on this
 * surface have shipped past review and green CI because the tests only
 * ever downloaded once into an empty bucket, and "what happens to what was
 * already there" is the entire subject of this ticket.
 *
 * Harness follows tests/js/test_map_download_bytes.js — see its header for
 * the jsdom boot (one script of top-level IIFEs, `FEATURE_BY_REGION_ID`
 * populated off the stubbed `map.on('load')` handler, a MUTABLE vector
 * source so the active basemap can be switched between runs). What is
 * different here: the warm-cache stub's RESULT is settable per test, so a
 * failed and a cancelled replacement can be driven as easily as a
 * successful one; and `seedPriorDownload` rewrites the record and the
 * bucket before each test, so the tests do not depend on each other's
 * leftovers.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const REGION_NAME = 'Martigny — Verbier';
const AREA_ID = 'region-' + REGION_ID;
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const BUCKET = PINNED_PREFIX + AREA_ID;

const TEMPLATE_A = 'https://tiles-a.example.invalid/{z}/{x}/{y}.pbf';
const TEMPLATE_B = 'https://tiles-b.example.invalid/{z}/{x}/{y}.pbf';
const TILE_A = 'https://tiles-a.example.invalid/14/8577/5811.pbf';
const TILE_B = 'https://tiles-b.example.invalid/14/8577/5811.pbf';

// The two basemaps' own style documents — one per picker row, which is
// where `activeBasemapRenderDependencyURLs` reads them from.
const STYLE_A = 'https://tiles.example.invalid/liberty.json';
const STYLE_B = 'https://tiles.example.invalid/swisstopo.json';

// The documents both basemaps share in this fixture: the stub style
// declares one sprite and one vector source for either basemap, so these
// urls appear in the OLD record's `deps` AND in the new run's. They are
// the case the prune's skip rule exists for — a url the replacement also
// fetched must survive, or the area would read 'incomplete' the moment it
// finished downloading.
const SPRITE_BASE = 'https://sprites.example.invalid/shared';
const SPRITE_URLS = [
  SPRITE_BASE + '.json',
  SPRITE_BASE + '.png',
  SPRITE_BASE + '@2x.json',
  SPRITE_BASE + '@2x.png',
];
const TILEJSON = 'https://tiles.example.invalid/source.json';

// 12.4 MB, so the confirm's size reads as a real figure rather than a
// rounded-away zero.
const PRIOR_BYTES = Math.round(12.4 * 1024 * 1024);

/** One region carrying a precomputed download summary, as /api/regions.geojson emits. */
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: REGION_ID,
        name: REGION_NAME,
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

/** The blob /api/region-basemap-tiles/ answers with — one tile, so the urls are nameable. */
const REGION_BLOB = {
  band: [10, 14],
  count: 1,
  mb: 1,
  over_ceiling: false,
  centre_tile: { z: 14, x: 8577, y: 5811 },
  z: { 14: [8577, 8577, 5811, 5811] },
};

/**
 * Minimal MapLibre stub with a MUTABLE vector source, as
 * test_map_download_bytes.js's — plus a `sprite` and a source `url`, so
 * the run resolves real render dependencies and the prune has shared
 * documents to leave alone.
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
    getStyle: () => ({
      layers: [],
      sources: { basemap: { type: 'vector', url: TILEJSON } },
      sprite: SPRITE_BASE,
    }),
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
 * Cache Storage stub over `name -> Set<url>`, with the per-entry `delete`
 * SNOW-871's prune uses — the point of this suite is WHICH entries go, so
 * a stub whose only removal is `caches.delete(name)` could not tell a
 * prune from the whole-bucket eviction it replaced.
 */
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
        put: async (url) => {
          store.add(url);
        },
        delete: async (url) => store.delete(url),
        match: async () => undefined,
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

/** In-memory `meta:app`; `rows` is returned so a test can read the record back. */
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

/**
 * The DOM map.js's boot, the roundel, the basemap picker and both confirm
 * banners read.
 *
 * The strings `<template>` is the real thing rather than the fallbacks:
 * `confirmBasemapReplace` composes its body through
 * `window.pwaStrings.read`, and rendering the two spans here is what
 * exercises that path (the English is _map_embed.html's own).
 */
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
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="${STYLE_A}"
          aria-checked="false"
        >OpenFreeMap</button>
      </li>
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="swisstopo_winter"
          data-basemap-url="${STYLE_B}"
          aria-checked="false"
        >Swisstopo (CH)</button>
      </li>
    </ul>
    <div id="map-download-evict-confirm" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-evict-confirm-title">Free up space?</p>
      <p id="map-download-evict-confirm-body"></p>
      <button id="map-download-evict-confirm-cta" type="button">Remove and continue</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>
    <div id="map-download-replace-confirm" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-replace-confirm-title">
        Downloading this region for this basemap will replace the copy you already have. Continue?
      </p>
      <p id="map-download-replace-confirm-body"></p>
      <button id="map-download-replace-confirm-cta" type="button">Replace and download</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>
    <template id="map-strings-template">
      <span data-string="download-replace-body">Your %(basemap)s copy of %(region)s (%(size)s)</span>
      <span data-string="download-replace-body-unnamed">Your earlier copy of %(region)s (%(size)s)</span>
    </template>`;
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
 * Flip the picker's checked radio and the stub map's active template, then
 * fire the event a real basemap switch ends with.
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

/**
 * Put a PRIOR download on the device: the `basemap.regions` record, and —
 * unless `onDisk` is false — the tiles and documents that record claims.
 *
 * `onDisk: false` is the stale record: the row still names a basemap whose
 * bucket the browser has since reclaimed. It replaces nothing, and the
 * point of several tests below is that it is never dressed up as a loss.
 *
 * @param {{template: string, basemapKey: string, deps: string[],
 *   tiles: string[], onDisk?: boolean}} options
 * @returns {void}
 */
function seedPriorDownload(options) {
  const onDisk = options.onDisk !== false;
  dbRows.set('basemap.regions', [
    {
      region_id: REGION_ID,
      name: REGION_NAME,
      band: [10, 14],
      z: REGION_BLOB.z,
      template: options.template,
      basemapKey: options.basemapKey,
      deps: options.deps,
      bytes: PRIOR_BYTES,
      savedAt: '2026-08-01T10:00:00.000Z',
    },
  ]);
  cachesStub.buckets.set(
    BUCKET,
    new Set(onDisk ? [...options.tiles, ...options.deps] : []),
  );
}

/** Every url currently held in the region's own pinned bucket. */
function bucketURLs() {
  return [...(cachesStub.buckets.get(BUCKET) || new Set())];
}

/** The stored `basemap.regions` entry for REGION_ID, or undefined. */
function recordedRegion() {
  const list = dbRows.get('basemap.regions') || [];
  return list.find((entry) => entry && entry.region_id === REGION_ID);
}

/**
 * How many warm-cache runs have been dispatched for the REGION's own
 * bucket. Filtered by area id on purpose: the shared base layer warms
 * itself on every basemap change (`warmBaseLayerWideBand`), so a bare call
 * count would report work this control never asked for.
 *
 * @returns {number}
 */
function regionWarmCalls() {
  return window.pwaWarmCache.mock.calls.filter(
    (call) => call[1] && call[1].areaId === AREA_ID,
  ).length;
}

/**
 * Focus REGION_ID and wait for the roundel to settle on `expected`.
 *
 * The state is waited FOR rather than merely awaited: `_probeDone` walks
 * every pinned bucket asynchronously, so a bare "not busy" wait passes
 * instantly on whatever the previous test left painted and the assertion
 * that follows would be reading a stale attribute.
 *
 * @param {string} expected
 * @returns {Promise<void>}
 */
async function selectRegion(expected) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:region-selected', {
      detail: { region_id: REGION_ID, region_name: REGION_NAME },
    }),
  );
  await waitFor(() => btn().dataset.downloadState === expected);
}

const btn = () => document.getElementById('map-download-control');
const replaceBanner = () => document.getElementById('map-download-replace-confirm');

/** True while the replace confirm is on screen. */
const replaceShown = () => !replaceBanner().classList.contains('hidden');

/**
 * Dismiss the replace confirm the way overlays.js's "×" does — hide the
 * overlay, THEN announce it. Both halves matter: `confirmBasemapReplace`
 * listens for the event, and the hide is what stops the next test finding
 * a banner still on screen (overlays.js is not loaded in this harness, so
 * nothing else would do it).
 *
 * @returns {void}
 */
function declineReplace() {
  const banner = replaceBanner();
  banner.classList.add('hidden');
  document.dispatchEvent(
    new CustomEvent('overlay:dismissed', {
      detail: { overlay: banner },
      bubbles: true,
    }),
  );
}

let mapStub;
let cachesStub;
let dbRows;
/** The report `pwaWarmCache`'s stub hands back for the NEXT run. */
let nextResult;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  cachesStub = installCachesStub();
  dbRows = installDbStub({});
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
  // The stub a real page reaches via a postMessage round trip to sw.js: it
  // writes every url into the run's own bucket, exactly as the worker's
  // warm-cache handler does, and reports whatever `nextResult` holds. A
  // FAILED run still writes — a real one gets part-way before it gives up,
  // and "the old copy survived a partial replacement" is a stronger
  // assertion than one over a bucket nothing touched.
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url, {});
    return nextResult;
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  // The confirm's size figure goes through this module's `formatMegabytes`.
  await import('../../static/js/basemap_manage_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom; the main IIFE's data load (and so
  // FEATURE_BY_REGION_ID) hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

beforeEach(() => {
  nextResult = { ok: 3, failed: 0, bytes: 4096, cancelled: false };
  window.pwaWarmCache.mockClear();
  cachesStub.buckets.clear();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete globalThis.maplibregl;
});

describe('the replace confirm (SNOW-871)', () => {
  it('is raised, naming the basemap, the region and the size, when the earlier copy is still on disk', async () => {
    seedPriorDownload({
      template: TEMPLATE_A,
      basemapKey: 'openfreemap_liberty',
      deps: [STYLE_A, ...SPRITE_URLS, TILEJSON],
      tiles: [TILE_A],
    });
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    // The state that offers this download in the first place.
    await selectRegion('other-basemap');
    expect(btn().dataset.downloadState).toBe('other-basemap');

    btn().click();
    await waitFor(() => replaceShown());

    expect(replaceShown()).toBe(true);
    // The picker's own label, the region's own name, the record's own
    // bytes — nothing here is assembled English.
    expect(document.getElementById('map-download-replace-confirm-body').textContent).toBe(
      'Your OpenFreeMap copy of Martigny — Verbier (12.4 MB)',
    );
    // Nothing has been fetched yet: the question comes before the run, not
    // alongside it.
    expect(regionWarmCalls()).toBe(0);

    // Declining leaves everything as it was — which is the whole promise
    // of asking.
    declineReplace();
    await waitFor(() => btn().dataset.downloadState === 'other-basemap');

    expect(regionWarmCalls()).toBe(0);
    expect(bucketURLs()).toContain(TILE_A);
    expect(bucketURLs()).not.toContain(TILE_B);

    const record = recordedRegion();
    expect(record.template).toBe(TEMPLATE_A);
    expect(record.basemapKey).toBe('openfreemap_liberty');
    expect(record.bytes).toBe(PRIOR_BYTES);

    // And the roundel is back on the state it was showing, not the generic
    // 'idle' the runner repaints on a refusal — the region IS still
    // downloaded, for the other basemap.
    expect(btn().dataset.downloadState).toBe('other-basemap');
    expect(btn().dataset.basemapKey).toBe('openfreemap_liberty');
  });

  it('is not raised for a stale record whose tiles have already gone', async () => {
    // The record still claims OpenFreeMap; the bucket behind it does not
    // exist. There is nothing to lose, so there is nothing to ask about —
    // the same distinction `_probeDone` makes before painting
    // 'other-basemap' at all.
    seedPriorDownload({
      template: TEMPLATE_A,
      basemapKey: 'openfreemap_liberty',
      deps: [STYLE_A, ...SPRITE_URLS, TILEJSON],
      tiles: [TILE_A],
      onDisk: false,
    });
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await selectRegion('idle');
    expect(btn().dataset.downloadState).toBe('idle');

    btn().click();
    await waitFor(() => btn().dataset.downloadState === 'done', 5000);

    expect(replaceShown()).toBe(false);
    expect(btn().dataset.downloadState).toBe('done');
    expect(recordedRegion().template).toEqual([[TEMPLATE_B]]);
  });

  it('is not raised for a re-download under the SAME basemap', async () => {
    // A retry of a partly-evicted download: the record names the active
    // basemap, so nothing is being replaced — `cache.put` overwrites each
    // key in place.
    seedPriorDownload({
      template: [[TEMPLATE_B]],
      basemapKey: 'swisstopo_winter',
      deps: [STYLE_B, ...SPRITE_URLS, TILEJSON],
      tiles: [],
    });
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await selectRegion('idle');
    expect(btn().dataset.downloadState).toBe('idle');

    btn().click();
    await waitFor(() => btn().dataset.downloadState === 'done', 5000);

    expect(replaceShown()).toBe(false);
    expect(regionWarmCalls()).toBe(1);
  });
});

describe('warm first, prune after (SNOW-871)', () => {
  it("prunes exactly the replaced basemap's urls once the new copy has landed", async () => {
    seedPriorDownload({
      template: TEMPLATE_A,
      basemapKey: 'openfreemap_liberty',
      deps: [STYLE_A, ...SPRITE_URLS, TILEJSON],
      tiles: [TILE_A],
    });
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await selectRegion('other-basemap');

    btn().click();
    await waitFor(() => replaceShown());
    document.getElementById('map-download-replace-confirm-cta').click();
    await waitFor(() => btn().dataset.downloadState === 'done', 5000);

    const urls = bucketURLs();
    // The replacement is there...
    expect(urls).toContain(TILE_B);
    expect(urls).toContain(STYLE_B);
    // ...the copy it replaced is not...
    expect(urls).not.toContain(TILE_A);
    expect(urls).not.toContain(STYLE_A);
    // ...and the documents BOTH basemaps use survived, because this run
    // fetched them too. Deleting one would leave the area 'incomplete' the
    // instant it finished downloading.
    for (const sprite of SPRITE_URLS) expect(urls).toContain(sprite);
    expect(urls).toContain(TILEJSON);

    expect(recordedRegion().template).toEqual([[TEMPLATE_B]]);
    expect(recordedRegion().basemapKey).toBe('swisstopo_winter');
  });

  it('prunes NOTHING when the replacement run fails — the old copy and its record both survive', async () => {
    // The case the ticket exists for. Under the old ordering the bucket
    // was already gone by the time this run failed, so the user was left
    // with neither copy — offline, with no way to get either back.
    seedPriorDownload({
      template: TEMPLATE_A,
      basemapKey: 'openfreemap_liberty',
      deps: [STYLE_A, ...SPRITE_URLS, TILEJSON],
      tiles: [TILE_A],
    });
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await selectRegion('other-basemap');
    expect(btn().dataset.downloadState).toBe('other-basemap');

    nextResult = { ok: 1, failed: 2, bytes: 512, cancelled: false };
    btn().click();
    await waitFor(() => replaceShown());
    document.getElementById('map-download-replace-confirm-cta').click();
    await waitFor(() => btn().dataset.downloadState === 'error', 5000);

    expect(btn().dataset.downloadState).toBe('error');
    // The OpenFreeMap copy is untouched, tiles and documents both...
    expect(bucketURLs()).toContain(TILE_A);
    expect(bucketURLs()).toContain(STYLE_A);
    // ...and so is the record naming it, which is what lets the roundel go
    // on reporting the truth.
    const record = recordedRegion();
    expect(record.template).toBe(TEMPLATE_A);
    expect(record.basemapKey).toBe('openfreemap_liberty');
    expect(record.bytes).toBe(PRIOR_BYTES);

    // The probe agrees: still downloaded, still for the other basemap.
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await waitFor(() => btn().dataset.downloadState === 'other-basemap');
    expect(btn().dataset.downloadState).toBe('other-basemap');
    expect(btn().dataset.basemapKey).toBe('openfreemap_liberty');
  });

  it('prunes NOTHING when the replacement run is cancelled either', async () => {
    // A cancelled run reports `failed: 0`, so anything reading a short
    // `ok` count as success would prune here — which is why `finish`
    // checks `cancelled` first.
    seedPriorDownload({
      template: TEMPLATE_A,
      basemapKey: 'openfreemap_liberty',
      deps: [STYLE_A, ...SPRITE_URLS, TILEJSON],
      tiles: [TILE_A],
    });
    switchBasemap('swisstopo_winter', TEMPLATE_B);
    await selectRegion('other-basemap');

    nextResult = { ok: 1, failed: 0, bytes: 512, cancelled: true };
    btn().click();
    await waitFor(() => replaceShown());
    document.getElementById('map-download-replace-confirm-cta').click();
    await waitFor(() => btn().dataset.downloadState !== 'busy', 5000);

    // Neither 'done' nor 'error' — the user stopped it.
    expect(btn().dataset.downloadState).toBe('idle');
    expect(bucketURLs()).toContain(TILE_A);
    expect(bucketURLs()).toContain(STYLE_A);
    expect(recordedRegion().template).toBe(TEMPLATE_A);
    expect(recordedRegion().basemapKey).toBe('openfreemap_liberty');
  });
});
