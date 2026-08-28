/*
 * tests/js/test_map_region_download_cancelled.js — a cancelled region
 * download must return the roundel to rest, not paint a failure (SNOW-748).
 *
 * `map_custom_download.js` has had a `cancelled` branch in its `finish`
 * since SNOW-632, because that control owns a Cancel button. This one did
 * not, on the reasoning that it offered no way to cancel — so a cancelled
 * result fell through to the `else`, painted 'error' and raised the shared
 * failure toast.
 *
 * SNOW-748 makes that path reachable: toggling the header's network switch
 * into a forced offline mode aborts an in-flight download. The user stopping
 * a run is not a fault, and reporting it as one is the same defect in
 * reverse as the download that ran under a forced offline mode in the first
 * place.
 *
 * The trap the runner's own header names is what makes this worth a test
 * rather than a read: a cancelled run always reports `failed: 0`, so it is
 * indistinguishable from a clean success on the failure count alone and
 * `finish` has to check `cancelled` FIRST. The third case below is the
 * regression guard in the other direction — a genuinely failed run must
 * still paint 'error' and still raise the toast.
 *
 * Harness follows tests/js/test_map_download_bytes.js (see its header for
 * the jsdom-boot rationale); the warm-cache stub returns whatever result
 * the test under way has asked for rather than always succeeding.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const ERROR_TOAST_ID = 'map-download-error-toast';

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

/** Minimal MapLibre stub — as test_map_download_bytes.js's, with a fixed template. */
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
        >Standard</button>
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

/** True while the shared basemap-download failure toast is on screen. */
function failureToastVisible() {
  return !document.getElementById(ERROR_TOAST_ID).classList.contains('hidden');
}

/** The recorded `basemap.regions` entry for REGION_ID, or undefined. */
async function recordedRegion() {
  const row = await window.pwaDb.get('meta:app', 'basemap.regions');
  const list = Array.isArray(row && row.value) ? row.value : [];
  return list.find((entry) => entry && entry.region_id === REGION_ID);
}

let mapStub;
/** The warm-cache worker's reply for the NEXT run. */
let nextResult;
/** Ran before each run's reply is returned — how a test aborts mid-flight. */
let duringRun;
/** Set by the warm-cache stub so a test can tell a run happened at all. */
let warmed;

/**
 * Select REGION_ID, click the control, and wait for the run to settle out
 * of 'busy'. Unlike test_map_download_bytes.js's helper this cannot wait
 * for 'done' — no run here reaches it.
 *
 * @returns {Promise<void>}
 */
async function runDownload() {
  const btn = document.getElementById('map-download-control');
  warmed = false;
  document.dispatchEvent(
    new CustomEvent('snowdesk:region-selected', {
      detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
    }),
  );
  await waitFor(() => {
    const state = btn.dataset.downloadState;
    return state === 'idle' || state === 'error' || state === 'other-basemap';
  });
  btn.click();
  await waitFor(() => warmed && btn.dataset.downloadState !== 'busy');
}

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  installCachesStub();
  installDbStub();
  // The header toggle's answer. Online at boot, so the control is actionable
  // and `handleClick`'s own pre-flight lets the run start; a test that wants
  // a forced mode flips it from inside the run.
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
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url, {});
    warmed = true;
    if (duringRun) await duringRun();
    return nextResult;
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom; the data load hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete window.pwaConnectivity;
  delete globalThis.maplibregl;
});

beforeEach(() => {
  duringRun = null;
  window.pwaConnectivity = { isOnline: () => true };
  document.getElementById(ERROR_TOAST_ID).classList.add('hidden');
});

describe('region download — a cancelled run is not a failure (SNOW-748)', () => {
  it('returns the roundel to rest and stays silent when the run is cancelled', async () => {
    // The shape the worker replies with on an abort: nothing succeeded, and
    // nothing FAILED either — which is precisely why `finish` has to read
    // `cancelled` before it reads the failure count.
    nextResult = { ok: 0, failed: 0, bytes: 0, cancelled: true, reason: 'offline-forced' };

    await runDownload();

    const btn = document.getElementById('map-download-control');
    expect(btn.dataset.downloadState).toBe('idle');
    expect(failureToastVisible()).toBe(false);
    // Partial tiles may have landed before the worker honoured the cancel,
    // so the one thing this must never do is claim the region is available
    // offline.
    expect(btn.dataset.downloadState).not.toBe('done');
    expect(await recordedRegion()).toBeUndefined();
  });

  it('rests on the offline state when the cancel came from the header toggle', async () => {
    // The path that made this branch reachable: the user forces offline mode
    // mid-download, the worker abandons the run, and by the time `finish`
    // runs the app is no longer using the network. 'idle' would invite a tap
    // that cannot start a run.
    nextResult = { ok: 0, failed: 0, bytes: 0, cancelled: true, reason: 'offline-forced' };
    duringRun = () => {
      window.pwaConnectivity = { isOnline: () => false };
    };

    await runDownload();

    const btn = document.getElementById('map-download-control');
    expect(btn.dataset.downloadState).toBe('offline');
    expect(failureToastVisible()).toBe(false);
    expect(await recordedRegion()).toBeUndefined();
  });

  it('still paints error and raises the toast when the run genuinely fails', async () => {
    // The regression guard: the `cancelled` branch must not swallow a real
    // failure, which the user does need told about.
    nextResult = { ok: 0, failed: 3, bytes: 0, cancelled: false };

    await runDownload();

    const btn = document.getElementById('map-download-control');
    expect(btn.dataset.downloadState).toBe('error');
    expect(failureToastVisible()).toBe(true);
    expect(await recordedRegion()).toBeUndefined();
  });
});
