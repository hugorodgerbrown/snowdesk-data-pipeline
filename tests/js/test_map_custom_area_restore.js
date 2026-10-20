/*
 * tests/js/test_map_custom_area_restore.js — Vitest DOM tests for SNOW-1136:
 * `window.pwaCustomAreaDownload.restoreArea` (static/js/map_custom_download.js).
 *
 * "Download here" on an account-only custom area used to open framing over
 * the stored box, and confirming minted a NEW `custom-<uuid>`. The account
 * then held two rows for one place — the old one "Not on this device", the
 * new one "On this device" — the name was gone, and a drop zone came back
 * as a rectangle. `restoreArea` runs the STORED definition instead, under
 * the stored id, through the same pinned-download runner a framed confirm
 * uses. What this suite asserts is the outcome the ticket names: after a
 * restore there is exactly ONE row for that id, on this device and synced,
 * carrying the stored name and kind — and a drop zone is fetched as its
 * circle, not the box it is stored as.
 *
 * Harness follows test_map_download_bytes.js (see its header): the map
 * bundle booted in one shared scope, the REAL basemap_download_runner.js,
 * and a `pwaWarmCache` stub that writes every URL into the run's pinned
 * bucket. `pwaDownloadsSync` is a spy, because what crosses to the account
 * is the point — and `reconcileAreas` is then fed the account row the push
 * describes, so "one row" is checked through the same join the Manage
 * downloads sheet renders from.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const AREA_ID = 'custom-11111111-2222-3333-4444-555555555555';
// Small enough to keep the tile list short, large enough that a circle
// inside it is visibly fewer tiles than the box.
const BBOX = [7.9, 46.4, 8.0, 46.47];

/** Minimal MapLibre stub — see test_map_download_bytes.js. */
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
    fitBounds: vi.fn(),
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
        delete: async (url) => store.delete(url),
        put: async (url) => {
          store.add(url);
        },
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
function installDbStub(initial) {
  const rows = new Map(Object.entries(initial || {}));
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

/**
 * The DOM the bundle's boot and this control read. `data-downloads-eligible`
 * is the signed-in gate; a test that wants the signed-out path flips it
 * before booting.
 */
function buildFixture(eligible) {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-area-content-url="/api/area-content/"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <button id="map-custom-download-control" type="button"
            data-downloads-eligible="${eligible ? 'true' : 'false'}"
            data-signin-url=""></button>
    <div id="map-frame-overlay" hidden>
      <div id="map-frame-instruction"></div>
      <div id="map-frame-area"><div id="map-frame-rect"></div></div>
      <span id="map-frame-readout"></span>
      <button id="map-frame-cancel" type="button">Cancel</button>
      <button id="map-frame-confirm" type="button">Download</button>
    </div>
    <ul id="basemap-menu">
      <li role="none">
        <button type="button" class="basemap-menu-item"
                data-basemap-key="openfreemap_liberty"
                data-basemap-url="https://tiles.example.invalid/liberty.json"
                aria-checked="true">OpenFreeMap</button>
      </li>
    </ul>`;
}

/** Boot the bundle against a fresh DOM, store and sync spy. */
async function boot({ eligible = true, records = {} } = {}) {
  buildFixture(eligible);
  stubMapLibre();
  const cachesStub = installCachesStub();
  const rows = installDbStub(records);
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      // SNOW-953: the area asks the server what its boundary contains; an
      // empty answer is a complete one.
      const body = String(url).includes('area-content') ? { regions: [], weather: [] } : {};
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url);
    return { ok: urls.length, failed: 0, bytes: 2048, cancelled: false };
  });
  window.pwaDownloadsSync = {
    push: vi.fn(async () => true),
    isEnabled: () => true,
    // The account list the areas reader joins in. Empty: each test feeds
    // the account row it means to `reconcileAreas` itself.
    accountAreas: async () => [],
  };

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  await import('../../static/js/basemap_manage_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  return { rows, cachesStub };
}

/** The tile URLs (not feeds, not content) a run warmed. */
function warmedTileUrls() {
  const urls = window.pwaWarmCache.mock.calls.flatMap((call) => call[0]);
  return urls.filter((url) => url.startsWith('https://tiles.example.invalid/') && url.endsWith('.pbf'));
}

/**
 * The account row the push describes, as areas.json would return it — the
 * server's own mapping from `type` to `shape` is `shapeFor`'s.
 */
function accountRowFromPush(pushed) {
  return {
    area_id: pushed.areaId,
    kind: 'CUSTOM',
    region_id: '',
    bbox: pushed.bbox,
    basemap_key: pushed.basemapKey || '',
    name: pushed.name || '',
    terrain: !!pushed.terrain,
    shape: pushed.type === 'dropzone' ? 'circle' : 'box',
    created_at: '2026-10-01T00:00:00.000Z',
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete window.pwaDownloadsSync;
  delete globalThis.maplibregl;
  Reflect.deleteProperty(window, 'caches');
});

describe('restoring a custom area (SNOW-1136)', () => {
  beforeEach(async () => {
    await boot();
  });

  it('records the stored id, name and box, and pushes the same area back', async () => {
    const outcome = await window.pwaCustomAreaDownload.restoreArea({
      areaId: AREA_ID,
      bbox: BBOX,
      name: 'Ridge',
      type: 'custom',
      terrain: false,
    });

    expect(outcome).toEqual({ started: true, reason: '', ok: true });
    const records = window.pwaDb.rows.get('basemap.customAreas');
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ id: AREA_ID, name: 'Ridge', bbox: BBOX });
    expect(records[0].type).toBeUndefined();
    // Under the stored id's own bucket — no second id was minted.
    expect(window.pwaWarmCache).toHaveBeenCalledWith(
      expect.any(Array),
      expect.objectContaining({ areaId: AREA_ID }),
    );
    expect(window.pwaDownloadsSync.push).toHaveBeenCalledTimes(1);
    expect(window.pwaDownloadsSync.push).toHaveBeenCalledWith(
      expect.objectContaining({ areaId: AREA_ID, name: 'Ridge', bbox: BBOX }),
    );
  });

  it('leaves exactly one row for the area, on this device and synced', async () => {
    await window.pwaCustomAreaDownload.restoreArea({
      areaId: AREA_ID,
      bbox: BBOX,
      name: 'Ridge',
      type: 'custom',
    });

    const pushed = window.pwaDownloadsSync.push.mock.calls[0][0];
    const local = await window.pwaBasemapDownloads.areas();
    const reconciled = window.pwaBasemapManageCore.reconcileAreas(local, [], {}, [
      accountRowFromPush(pushed),
    ]);
    const rows = window.pwaBasemapManageCore.manageRows(reconciled, {
      isCustomAreaId: window.pwaBasemapDownloadCore.isCustomAreaId,
    });

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      id: AREA_ID,
      label: 'Ridge',
      kind: 'custom',
      onDevice: true,
      synced: true,
    });
  });

  it('keeps an unnamed area unnamed, so its default stays translatable', async () => {
    await window.pwaCustomAreaDownload.restoreArea({ areaId: AREA_ID, bbox: BBOX, name: '' });

    const records = window.pwaDb.rows.get('basemap.customAreas');
    expect('name' in records[0]).toBe(false);
    expect(window.pwaDownloadsSync.push.mock.calls[0][0].name).toBe('');
  });

  it('repeats the account row\'s terrain-data choice', async () => {
    await window.pwaCustomAreaDownload.restoreArea({
      areaId: AREA_ID,
      bbox: BBOX,
      terrain: true,
    });

    expect(window.pwaDb.rows.get('basemap.customAreas')[0].terrain).toBe(true);
    expect(window.pwaDownloadsSync.push.mock.calls[0][0].terrain).toBe(true);
  });

  it('restores a drop zone as a drop zone, fetching its circle', async () => {
    await window.pwaCustomAreaDownload.restoreArea({
      areaId: AREA_ID,
      bbox: BBOX,
      name: 'Drop zone 2',
      type: 'dropzone',
    });

    const record = window.pwaDb.rows.get('basemap.customAreas')[0];
    expect(record.type).toBe('dropzone');
    expect(record.name).toBe('Drop zone 2');
    expect(window.pwaDownloadsSync.push.mock.calls[0][0].type).toBe('dropzone');

    const core = window.pwaBasemapDownloadCore;
    const [minZ, maxZ] = core.MICRO_BAND;
    const circle = core.circleBlobForBBox(BBOX, minZ, maxZ);
    const box = core.buildBlob(BBOX, minZ, maxZ);
    expect(circle.count).toBeLessThan(box.count);
    expect(warmedTileUrls()).toHaveLength(circle.count);

    const reconciled = window.pwaBasemapManageCore.reconcileAreas(
      await window.pwaBasemapDownloads.areas(),
      [],
      {},
      [accountRowFromPush(window.pwaDownloadsSync.push.mock.calls[0][0])],
    );
    const rows = window.pwaBasemapManageCore.manageRows(reconciled, {
      isCustomAreaId: core.isCustomAreaId,
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe('dropzone');
  });

  it('refuses an area this device already holds', async () => {
    window.pwaDb.rows.set('basemap.customAreas', [{ id: AREA_ID, ordinal: 1, bbox: BBOX }]);

    const outcome = await window.pwaCustomAreaDownload.restoreArea({ areaId: AREA_ID, bbox: BBOX });

    expect(outcome).toEqual({ started: false, reason: 'done' });
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });

  it('refuses a second restore while one is running', async () => {
    const first = window.pwaCustomAreaDownload.restoreArea({ areaId: AREA_ID, bbox: BBOX });
    const second = await window.pwaCustomAreaDownload.restoreArea({
      areaId: 'custom-other',
      bbox: BBOX,
    });

    expect(second).toEqual({ started: false, reason: 'busy' });
    await first;
  });

  it('refuses while the app is not using the network', async () => {
    window.pwaConnectivity = { isOnline: () => false };
    try {
      const outcome = await window.pwaCustomAreaDownload.restoreArea({
        areaId: AREA_ID,
        bbox: BBOX,
      });
      expect(outcome).toEqual({ started: false, reason: 'offline' });
    } finally {
      delete window.pwaConnectivity;
    }
  });

  it('refuses a region id or a box it cannot read', async () => {
    const api = window.pwaCustomAreaDownload;
    expect(await api.restoreArea({ areaId: 'region-CH-4115', bbox: BBOX })).toEqual({
      started: false,
      reason: 'invalid',
    });
    expect(await api.restoreArea({ areaId: AREA_ID, bbox: [8, 46, 7, 47] })).toEqual({
      started: false,
      reason: 'invalid',
    });
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });
});

describe('restoring while signed out (SNOW-1136)', () => {
  it('starts nothing and reports the sign-in gate', async () => {
    await boot({ eligible: false });

    const outcome = await window.pwaCustomAreaDownload.restoreArea({
      areaId: AREA_ID,
      bbox: BBOX,
    });

    expect(outcome).toEqual({ started: false, reason: 'signin' });
    expect(window.pwaWarmCache).not.toHaveBeenCalled();
  });
});
