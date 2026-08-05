/*
 * tests/js/test_basemap_custom_areas.js — Vitest DOM tests for SNOW-635's
 * storage layer in static/js/map.js: the lazy migration of the legacy
 * single-row `basemap.customArea` into the array-shaped
 * `basemap.customAreas`, and the array's own read/evict/rename operations.
 *
 * Booting map.js in jsdom follows the same pattern as
 * test_map_download_eviction.js (see its header for why: one script of
 * top-level IIFEs, module-private top-level bindings reachable only
 * through the frozen globals map.js exposes). `window.pwaBasemapDownloads`
 * — the surface under test here — is assigned unconditionally at the top
 * level, so unlike FEATURE_BY_REGION_ID none of this needs the stubbed
 * `map.on('load')` handler to fire.
 *
 * The migration runs inside `basemapDownloadedAreas()`, which is on the
 * BOOT PATH (the roundel's own probe, post-SNOW-634) — so it must be
 * best-effort like everything else that sits there: a failed write
 * degrades to reading the legacy row, never throws. That is asserted
 * directly here by making `pwaDb.put` reject.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MB = 1024 * 1024;

/** Minimal MapLibre stub — see test_map_download_eviction.js for the full rationale. */
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

/** Minimal Cache Storage stub — only what evictBasemapAreas touches. */
function installCachesStub() {
  const buckets = new Map();
  const stub = {
    buckets,
    keys: vi.fn(async () => [...buckets.keys()]),
    open: vi.fn(async (name) => {
      if (!buckets.has(name)) buckets.set(name, new Set());
      return {
        keys: async () => [...buckets.get(name)].map((url) => ({ url })),
        put: async () => {},
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

/** In-memory `meta:app`, seeded with whatever a test wants already on disk. */
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

/** The DOM map.js's boot reads: just the map div, nothing this suite exercises. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="standard"
         data-season-end="2026-05-31"></div>`;
}

let cachesStub;

beforeEach(async () => {
  buildFixture();
  stubMapLibre();
  cachesStub = installCachesStub();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * MB, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
  );

  vi.resetModules();
  await import('../../static/js/i18n_strings.js');
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/map.js');
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete globalThis.maplibregl;
  Reflect.deleteProperty(window, 'caches');
});

const LEGACY_AREA = {
  bbox: [7.9, 46.4, 8.1, 46.6],
  band: [10, 14],
  centre_tile: { z: 14, x: 8580, y: 5810 },
  template: 'https://tiles.example.invalid/{z}/{x}/{y}.pbf',
  bytes: 62 * MB,
  savedAt: '2026-08-01T10:00:00.000Z',
};

describe('lazy migration of basemap.customArea -> basemap.customAreas', () => {
  it('wraps a legacy row into a one-entry array on first read', async () => {
    const rows = installDbStub({ 'basemap.customArea': LEGACY_AREA });

    const areas = await window.pwaBasemapDownloads.areas();

    const custom = areas.find((a) => a.id === 'custom');
    expect(custom).toBeTruthy();
    expect(custom.bytes).toBe(62 * MB);
    expect(rows.get('basemap.customAreas')).toEqual([{ ...LEGACY_AREA, id: 'custom', ordinal: 1 }]);
  });

  it('keeps the legacy id so the existing pinned bucket still resolves', async () => {
    installDbStub({ 'basemap.customArea': LEGACY_AREA });

    const areas = await window.pwaBasemapDownloads.areas();

    // The bucket 'snowdesk-basemap-pinned-custom' has no rename — the id
    // has to survive unchanged for it to keep resolving.
    expect(areas.find((a) => a.id === 'custom')).toBeTruthy();
  });

  it('deletes the old key once migrated', async () => {
    const rows = installDbStub({ 'basemap.customArea': LEGACY_AREA });

    await window.pwaBasemapDownloads.areas();

    expect(rows.has('basemap.customArea')).toBe(false);
    expect(rows.has('basemap.customAreas')).toBe(true);
  });

  it('does not re-migrate (or duplicate) on a second read', async () => {
    const rows = installDbStub({ 'basemap.customArea': LEGACY_AREA });

    await window.pwaBasemapDownloads.areas();
    const areas = await window.pwaBasemapDownloads.areas();

    expect(areas.filter((a) => a.id === 'custom')).toHaveLength(1);
    expect(rows.get('basemap.customAreas')).toHaveLength(1);
  });

  it('degrades to reading the legacy row when the migration write fails', async () => {
    // SNOW-635: this runs inside basemapDownloadedAreas(), the boot-path
    // probe the roundel calls on every load — a device that cannot write
    // must still see its own area, not have the roundel/sheet break.
    installDbStub({ 'basemap.customArea': LEGACY_AREA });
    window.pwaDb.put = vi.fn(async () => {
      throw new Error('write failed');
    });

    const areas = await window.pwaBasemapDownloads.areas();

    const custom = areas.find((a) => a.id === 'custom');
    expect(custom).toBeTruthy();
    expect(custom.bytes).toBe(62 * MB);
  });

  it('never throws when the legacy read itself fails', async () => {
    installDbStub({});
    window.pwaDb.get = vi.fn(async () => {
      throw new Error('read failed');
    });

    await expect(window.pwaBasemapDownloads.areas()).resolves.toEqual([]);
  });

  it('has nothing to migrate on a fresh device', async () => {
    installDbStub({});

    const areas = await window.pwaBasemapDownloads.areas();

    expect(areas).toEqual([]);
  });

  it('leaves an already-array-shaped record untouched', async () => {
    const existing = [
      { id: 'custom-a1', ordinal: 1, bbox: [1, 2, 3, 4], bytes: 5 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
    ];
    const rows = installDbStub({ 'basemap.customAreas': existing });

    const areas = await window.pwaBasemapDownloads.areas();

    expect(areas.map((a) => a.id)).toEqual(['custom-a1']);
    expect(rows.get('basemap.customAreas')).toBe(existing);
  });
});

describe('deleting a custom area', () => {
  it('leaves basemap.customAreas as an empty array, not a deleted key', async () => {
    // SNOW-635: an empty ARRAY reads as "already migrated, nothing left" —
    // a deleted KEY would instead make the next read try to re-migrate
    // from a legacy row that, by then, no longer exists.
    const rows = installDbStub({
      'basemap.customAreas': [
        { id: 'custom-a1', ordinal: 1, bbox: [1, 2, 3, 4], bytes: 5 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
      ],
    });
    await cachesStub.open('snowdesk-basemap-pinned-custom-a1');

    await window.pwaBasemapDownloads.evict(['custom-a1']);

    expect(rows.has('basemap.customAreas')).toBe(true);
    expect(rows.get('basemap.customAreas')).toEqual([]);
    expect(await window.pwaBasemapDownloads.areas()).toEqual([]);
  });

  it('removes only the targeted area, leaving the others untouched', async () => {
    const rows = installDbStub({
      'basemap.customAreas': [
        { id: 'custom-a1', ordinal: 1, bbox: [1, 2, 3, 4], bytes: 5 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
        { id: 'custom-b2', ordinal: 2, bbox: [1, 2, 3, 4], bytes: 9 * MB, savedAt: '2026-08-02T00:00:00.000Z' },
      ],
    });

    await window.pwaBasemapDownloads.evict(['custom-a1']);

    expect(rows.get('basemap.customAreas').map((a) => a.id)).toEqual(['custom-b2']);
  });

  it("deletes the area's own pinned bucket, not the whole prefix", async () => {
    installDbStub({
      'basemap.customAreas': [
        { id: 'custom-a1', ordinal: 1, bbox: [1, 2, 3, 4], bytes: 5 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
      ],
    });
    await cachesStub.open('snowdesk-basemap-pinned-custom-a1');
    await cachesStub.open('snowdesk-basemap-pinned-region-CH-9999');

    await window.pwaBasemapDownloads.evict(['custom-a1']);

    expect(cachesStub.delete).toHaveBeenCalledWith('snowdesk-basemap-pinned-custom-a1');
    expect(cachesStub.buckets.has('snowdesk-basemap-pinned-region-CH-9999')).toBe(true);
  });
});

describe('renaming a custom area', () => {
  it('writes the name back onto the matching entry', async () => {
    const rows = installDbStub({
      'basemap.customAreas': [
        { id: 'custom-a1', ordinal: 1, bbox: [1, 2, 3, 4], bytes: 5 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
      ],
    });

    const ok = await window.pwaBasemapDownloads.rename('custom-a1', 'Home run');

    expect(ok).toBe(true);
    expect(rows.get('basemap.customAreas')[0].name).toBe('Home run');
  });

  it('survives being read back through basemapDownloadedAreas', async () => {
    installDbStub({
      'basemap.customAreas': [
        { id: 'custom-a1', ordinal: 1, bbox: [1, 2, 3, 4], bytes: 5 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
      ],
    });

    await window.pwaBasemapDownloads.rename('custom-a1', 'Home run');
    const areas = await window.pwaBasemapDownloads.areas();

    expect(areas.find((a) => a.id === 'custom-a1').name).toBe('Home run');
  });

  it('is a no-op for a region id — regions are never renameable', async () => {
    const rows = installDbStub({
      'basemap.regions': [
        { region_id: 'CH-4115', name: 'Aletsch', bytes: 5 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
      ],
    });

    const ok = await window.pwaBasemapDownloads.rename('region-CH-4115', 'Nope');

    expect(ok).toBe(false);
    expect(rows.get('basemap.regions')[0].name).toBe('Aletsch');
  });

  it('is a no-op for an id with no matching record', async () => {
    installDbStub({ 'basemap.customAreas': [] });

    const ok = await window.pwaBasemapDownloads.rename('custom-ghost', 'Nope');

    expect(ok).toBe(false);
  });
});
