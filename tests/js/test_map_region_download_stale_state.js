/*
 * tests/js/test_map_region_download_stale_state.js — the region download
 * roundel describes the region on screen, and what this device holds now
 * (SNOW-1134).
 *
 * The roundel (#map-download-control) kept state that belonged to a region
 * no longer selected, or to an area that no longer existed:
 *
 *   - a run's outcome painted whichever region was showing when it landed,
 *     so one region's green 'done' sat on another;
 *   - a removal from the Manage downloads sheet never repainted it, nor did
 *     a page restored from the back/forward cache;
 *   - "Download here" read the coalescer's interim 'idle' rather than the
 *     settled probe, and so downloaded a region already on the device;
 *   - a probe that hung held the render coalescer for good.
 *
 * Harness follows tests/js/test_map_region_download_cancelled.js (whose
 * stubs are copied here rather than shared, as every suite in this family
 * does), with TWO regions so the selection can move while a run is going.
 * The warm-cache stub can be held open by a test, which is how a run is
 * caught mid-flight.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_A = 'CH-4115';
const REGION_B = 'CH-4116';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const ERROR_TOAST_ID = 'map-download-error-toast';

/** One region feature carrying a precomputed download summary. */
function regionFeature(id, name, x) {
  return {
    type: 'Feature',
    properties: {
      id: id,
      name: name,
      download: { count: 1, mb: 1, over_ceiling: false, centre_tile: { z: 14, x: x, y: 5811 } },
    },
    geometry: {
      type: 'Polygon',
      coordinates: [[[7.0, 46.0], [7.01, 46.0], [7.01, 46.01], [7.0, 46.01], [7.0, 46.0]]],
    },
  };
}

const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [regionFeature(REGION_A, 'Martigny — Verbier', 8577), regionFeature(REGION_B, 'Zermatt', 8590)],
};

/** The one-tile blob /api/region-basemap-tiles/ answers with — a different tile per region. */
function regionBlob(regionId) {
  const x = regionId === REGION_A ? 8577 : 8590;
  return {
    band: [10, 14],
    count: 1,
    mb: 1,
    over_ceiling: false,
    centre_tile: { z: 14, x: x, y: 5811 },
    z: { 14: [x, x, 5811, 5811] },
  };
}

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

/** Cache Storage stub over `name -> Set<url>` — contents are never asserted here. */
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
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  return stub;
}

/** In-memory `meta:app`, so the control can read and write its own records. */
function installDbStub() {
  const rows = new Map();
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

/** The DOM map.js's boot, this control, and the shared failure toast need. */
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
    <ul id="basemap-menu">
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="https://tiles.example.invalid/liberty.json"
          aria-checked="false"
        >OpenFreeMap</button>
      </li>
    </ul>
    <div id="${ERROR_TOAST_ID}" class="hidden"></div>
    <div id="map-download-evict-confirm" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-evict-confirm-title">Free up space?</p>
      <p id="map-download-evict-confirm-body"></p>
      <button id="map-download-evict-confirm-cta" type="button">Delete and download</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>`;
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

/** A promise with its resolver held outside. */
function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

let mapStub;
let cachesStub;
let dbRows;
/** When set, the warm-cache stub waits on it before replying — a run held mid-flight. */
let hold;
/** Resolved by the warm-cache stub once a run has reached the worker. */
let reachedWorker;
/** The hold the current test's run is waiting on. */
let held;

/** Let the held run's worker reply. */
const gateRelease = () => held.resolve();

const btn = () => document.getElementById('map-download-control');
const state = () => btn().dataset.downloadState;
const areaId = (regionId) => window.pwaBasemapDownloadCore.areaIdForRegion(regionId);

/** Select `regionId` (null deselects), as the map's region tap does. */
function select(regionId) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:region-selected', { detail: { region_id: regionId } }),
  );
}

/** The recorded `basemap.regions` entry for `regionId`, or undefined. */
function recorded(regionId) {
  const list = dbRows.get('basemap.regions') || [];
  return list.find((entry) => entry && entry.region_id === regionId);
}

/** Download `regionId` to completion, leaving it painted 'done'. */
async function downloadToDone(regionId) {
  select(regionId);
  await waitFor(() => state() === 'idle');
  btn().click();
  await waitFor(() => state() === 'done');
  expect(state()).toBe('done');
}

/** Select A, tap it, and return once the run is held inside the worker. */
async function startHeldRun() {
  held = hold = deferred();
  reachedWorker = deferred();
  select(REGION_A);
  await waitFor(() => state() === 'idle');
  btn().click();
  await reachedWorker.promise;
  expect(state()).toBe('busy');
}

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  cachesStub = installCachesStub();
  dbRows = installDbStub();
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
      if (href.includes('area-content')) body = { regions: [], weather: [] };
      if (href.includes('region-basemap-tiles')) {
        body = regionBlob(new URL(href, 'https://x.invalid').searchParams.get('id'));
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url, {});
    if (hold) {
      const gate = hold;
      hold = null;
      reachedWorker.resolve();
      await gate.promise;
    }
    return { ok: urls.length, failed: 0, bytes: 1, cancelled: false };
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete window.pwaConnectivity;
  delete globalThis.maplibregl;
});

beforeEach(async () => {
  hold = null;
  // Each test starts from an empty device with nothing selected.
  dbRows.clear();
  cachesStub.buckets.clear();
  window.pwaWarmCache.mockClear();
  select(null);
  await waitFor(() => state() === 'no-region');
});

describe('a run paints only its own region (SNOW-1134)', () => {
  it('shows the newly selected region, not the run, and never paints the outcome on it', async () => {
    await startHeldRun();

    select(REGION_B);
    await waitFor(() => state() === 'idle');
    // B's own state, not A's progress fill — and not startable while A runs.
    expect(state()).toBe('idle');
    expect(btn().getAttribute('aria-disabled')).toBe('true');

    // Release A's run and let it record.
    gateRelease();
    await waitFor(() => !!recorded(REGION_A));
    await waitFor(() => btn().getAttribute('aria-disabled') === 'false');

    // A finished 'done'; B must still read as B — undownloaded, and
    // actionable again now nothing is running.
    expect(state()).toBe('idle');
    expect(btn().getAttribute('aria-disabled')).toBe('false');

    select(REGION_A);
    await waitFor(() => state() === 'done');
    expect(state()).toBe('done');
  });

  it('shows the run again when its region is reselected mid-run', async () => {
    await startHeldRun();
    select(REGION_B);
    await waitFor(() => state() === 'idle');
    select(REGION_A);
    await waitFor(() => state() === 'busy');
    expect(state()).toBe('busy');
    gateRelease();
    await waitFor(() => state() === 'done');
    expect(state()).toBe('done');
  });

  it('refuses a tap on another region while a run is in flight', async () => {
    await startHeldRun();
    select(REGION_B);
    await waitFor(() => state() === 'idle');
    const calls = window.pwaWarmCache.mock.calls.length;
    btn().click();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(state()).toBe('idle');
    expect(window.pwaWarmCache.mock.calls.length).toBe(calls);
    gateRelease();
    await waitFor(() => !!recorded(REGION_A));
    expect(recorded(REGION_B)).toBeUndefined();
  });

  it('ends on the no-region state when the region is deselected mid-run', async () => {
    await startHeldRun();
    select(null);
    await waitFor(() => state() === 'no-region');
    gateRelease();
    await waitFor(() => !!recorded(REGION_A));
    // Let the outcome's paint land, then check it went nowhere.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(state()).toBe('no-region');
  });
});

describe('the roundel follows what the device holds (SNOW-1134)', () => {
  it('repaints from done to idle when the area is removed elsewhere', async () => {
    await downloadToDone(REGION_A);
    const changed = vi.fn();
    document.addEventListener('snowdesk:downloads-changed', changed);
    // The Manage downloads sheet's removal goes through this bridge.
    await window.pwaBasemapDownloads.evict([areaId(REGION_A)]);
    document.removeEventListener('snowdesk:downloads-changed', changed);
    expect(changed).toHaveBeenCalled();
    await waitFor(() => state() === 'idle');
    expect(state()).toBe('idle');
  });

  it('re-probes on a back/forward-cache restore and on becoming visible', async () => {
    await downloadToDone(REGION_A);
    // Another tab removes the area: nothing on this page hears about it.
    dbRows.clear();
    cachesStub.buckets.clear();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(state()).toBe('done');

    const restored = new Event('pageshow');
    Object.defineProperty(restored, 'persisted', { value: true });
    window.dispatchEvent(restored);
    await waitFor(() => state() === 'idle');
    expect(state()).toBe('idle');

    await downloadToDone(REGION_A);
    dbRows.clear();
    cachesStub.buckets.clear();
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => state() === 'idle');
    expect(state()).toBe('idle');
  });
});

describe('"Download here" (SNOW-1134)', () => {
  it('refuses a region already downloaded, and hands the control back', async () => {
    await downloadToDone(REGION_A);
    const calls = window.pwaWarmCache.mock.calls.length;
    // B is selected and its render is still running when the sheet asks
    // for A — the race that read the coalescer's interim 'idle'.
    select(REGION_B);
    const outcome = await window.pwaRegionDownload.start(REGION_A);
    expect(outcome).toEqual({ started: false, reason: 'done' });
    expect(window.pwaWarmCache.mock.calls.length).toBe(calls);
    // The control describes B again — the region actually selected.
    expect(state()).toBe('idle');
    select(REGION_A);
    await waitFor(() => state() === 'done');
    expect(state()).toBe('done');
  });

  it('starts an unselected region and leaves the control on the selected one', async () => {
    select(REGION_B);
    await waitFor(() => state() === 'idle');
    const outcome = await window.pwaRegionDownload.start(REGION_A);
    expect(outcome).toEqual({ started: true, reason: '' });
    await waitFor(() => !!recorded(REGION_A));
    await waitFor(() => btn().getAttribute('aria-disabled') === 'false');
    // B never borrowed A's outcome.
    expect(state()).toBe('idle');
    expect(recorded(REGION_B)).toBeUndefined();
  });
});

describe('"Download here" keeps the region it was asked for (SNOW-1134 review)', () => {
  it('downloads A even when B is selected while A is being probed', async () => {
    select(REGION_B);
    await waitFor(() => state() === 'idle');
    const pending = window.pwaRegionDownload.start(REGION_A);
    // The selection lands while A's probe is still being awaited.
    select(REGION_B);
    const outcome = await pending;
    expect(outcome).toEqual({ started: true, reason: '' });
    await waitFor(() => !!recorded(REGION_A));
    expect(recorded(REGION_B)).toBeUndefined();
  });
});

describe('a hung probe (SNOW-1134)', () => {
  it('gives up at the budget, never paints done, and leaves later renders free', async () => {
    await downloadToDone(REGION_A);
    select(null);
    await waitFor(() => state() === 'no-region');

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const realKeys = cachesStub.keys;
    // The Cache Storage walk neither resolves nor rejects.
    cachesStub.keys = vi.fn(() => new Promise(() => {}));
    try {
      select(REGION_A);
      await vi.advanceTimersByTimeAsync(4100);
      // Downloaded, but unprovable: the neutral state, never 'done'.
      expect(state()).toBe('idle');
    } finally {
      cachesStub.keys = realKeys;
      vi.useRealTimers();
    }
    // The coalescer was released: the next trigger probes, and lands.
    document.dispatchEvent(new CustomEvent('snowdesk:downloads-changed'));
    await waitFor(() => state() === 'done');
    expect(state()).toBe('done');
  });
});
