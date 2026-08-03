/*
 * tests/js/test_map_download_eviction.js — Vitest DOM test for the region
 * download control's pre-flight ORDER (static/js/map.js,
 * mapDownloadControlInit).
 *
 * The behaviour under test is ordering, not arithmetic: `evictBasemapAreas`
 * deletes another area's pinned Cache Storage bucket for good, so it has to
 * run after every check that can abort the run. The 2026-08-03 JS review
 * (finding D1) found the region control evicting first and only then hitting
 * the `!core || !template` guard, so a user could confirm "evict area X",
 * lose X, and watch the download never start. The custom-area control
 * (`handleConfirm`) always had the right order.
 *
 * Booting map.js in jsdom: the file is one script of top-level IIFEs, so
 * importing it runs the lot. The main IIFE needs a `#map` element and a
 * `maplibregl` global, and it registers its data-loading work as a
 * `map.on('load')` handler — MapLibre never fires that here, so the stub
 * records handlers and the harness invokes the one it wants. That is what
 * populates `FEATURE_BY_REGION_ID`, which the download control reads its
 * region summary from and which no test can reach from outside (map.js is
 * imported as a module, so its top-level bindings are module-private).
 *
 * The stub map's `getStyle()` reports no raster source, so
 * `activeBasemapTileTemplate` answers null throughout — the "style still
 * settling" case, which is one of the two aborts D1 named. The run must
 * therefore stop before touching another area's bucket.
 *
 * `caches` and `navigator.storage` do not exist in jsdom; both are stubbed
 * to the surface map.js uses. The eviction-confirm banner markup mirrors
 * templates/includes/_overlay_banner.html as rendered by
 * _map_embed.html:1163 — a hand-copy, the standing trade-off in this
 * harness (see test_map_downloads_manager.js's header); the real markup is
 * exercised by tests/e2e/test_basemap_download_budget.py.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

const MB = 1024 * 1024;

const REGION_ID = 'CH-4115';
const OTHER_REGION_ID = 'CH-9999';
const OTHER_BUCKET = 'snowdesk-basemap-pinned-region-' + OTHER_REGION_ID;

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
          count: 5,
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

/** The blob /api/region-basemap-tiles/ would answer with for REGION_ID. */
const REGION_BLOB = {
  band: [10, 14],
  count: 5,
  mb: 1,
  over_ceiling: false,
  centre_tile: { z: 14, x: 8577, y: 5811 },
  z: { 14: [8577, 8577, 5811, 5811] },
};

/**
 * Minimal MapLibre stub: every method map.js calls during boot, plus a
 * record of the event handlers it registered so the harness can invoke
 * `load` itself. `getStyle()` reports no sources, which is what makes
 * `activeBasemapTileTemplate` answer null.
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
    getSource: () => null,
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
    getStyle: () => ({ layers: [], sources: {} }),
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
 * Cache Storage stub over a name -> Set-of-urls map. `keys`/`open` back
 * `pinnedBasemapCacheURLs`; `delete` is what an eviction calls, and is a
 * spy so the test can assert it was never reached.
 */
function installCachesStub(names) {
  const buckets = new Map(names.map((name) => [name, new Set()]));
  const stub = {
    buckets,
    keys: vi.fn(async () => [...buckets.keys()]),
    open: vi.fn(async (name) => ({
      keys: async () => [...(buckets.get(name) || [])].map((url) => ({ url })),
      put: async () => {},
      match: async () => undefined,
    })),
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

/** The DOM map.js's boot and this control read: map, roundel, search pill, confirm banner. */
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

/** Resolve after `ms` of real time — the module's own work is promise- and timer-driven. */
function tick(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await tick(10);
  }
  return predicate();
}

let mapStub;
let cachesStub;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  cachesStub = installCachesStub([OTHER_BUCKET]);
  // One other area on disk, recorded at 1 MB and older than anything this
  // test downloads, so it is the one an eviction would pick.
  installDbStub({
    'basemap.regions': [
      {
        region_id: OTHER_REGION_ID,
        name: 'Somewhere else',
        band: [10, 14],
        bytes: 1 * MB,
        savedAt: '2026-07-01T10:00:00.000Z',
      },
    ],
    // A 1 MB standing budget: this region's own 1 MB estimate fits only if
    // the other area goes first, which is what raises the confirm banner.
    'basemap.budgetMb': 1,
  });
  // Plenty of quota — this test is about the budget path, not the quota one.
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * MB, usage: 0 }) },
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

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/map.js');
  // MapLibre never fires 'load' in jsdom; the main IIFE's data load (and so
  // FEATURE_BY_REGION_ID) hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete globalThis.maplibregl;
});

describe('region download pre-flight ordering', () => {
  it('evicts nothing when the run aborts before it can start', async () => {
    const btn = document.getElementById('map-download-control');
    document.dispatchEvent(
      new CustomEvent('snowdesk:region-selected', {
        detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
      }),
    );
    await waitFor(() => btn.dataset.downloadState === 'idle');
    expect(btn.dataset.downloadState).toBe('idle');

    const banner = document.getElementById('map-download-evict-confirm');
    btn.click();
    // A banner would appear within a few microtasks of the click; answer it
    // if it does, so the "user confirmed and then lost the area" path is the
    // one under test rather than an unanswered prompt.
    await waitFor(() => !banner.classList.contains('hidden'), 200);
    if (!banner.classList.contains('hidden')) {
      document.getElementById('map-download-evict-confirm-cta').click();
    }
    await waitFor(() => btn.dataset.downloadState === 'error');

    // The run could not start — no tile template — so it must read as a
    // failed download...
    expect(btn.dataset.downloadState).toBe('error');
    // ...and the other area must still be on disk, record and bucket both.
    expect(cachesStub.delete).not.toHaveBeenCalled();
    expect([...cachesStub.buckets.keys()]).toContain(OTHER_BUCKET);
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    expect(row.value.map((entry) => entry.region_id)).toContain(OTHER_REGION_ID);
    // The user was never asked, either: being prompted to give up an area
    // for a run that cannot start is the same fault one step earlier.
    expect(banner.classList.contains('hidden')).toBe(true);
  });
});
