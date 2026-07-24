/*
 * tests/js/test_map_layer_sync_status.js — Vitest unit tests for
 * static/js/map_layer_sync_status.js (SNOW-505).
 *
 * `window.pwaLayerSyncStatus` is a stateless, side-effect-free module
 * (every `refresh()` call just re-reads the DOM and re-probes Cache
 * Storage / IndexedDB) — unlike `db.js`'s one-way latches, there's no
 * per-test module-reset hazard, so the module is imported once for its
 * side effects and every test builds its own fixture DOM plus fresh
 * `global.caches` / `window.pwaDb` stubs.
 *
 * `global.caches` isn't part of jsdom (same gap as `indexedDB` — see
 * tests/js/setup.js), so it's absent unless a test opts in via
 * `vi.stubGlobal('caches', ...)`; that absence is exactly what the
 * "Cache Storage unsupported" case below relies on.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/map_layer_sync_status.js';

const MAJOR_REGIONS_PATH = '/api/major-regions.geojson';
const SUB_REGIONS_PATH = '/api/sub-regions.geojson';
const MICRO_REGIONS_PATH = '/api/regions.geojson';
const RESORTS_PATH = '/api/resorts.geojson';

/**
 * Build the #basemap-menu fixture: one row per overlay key (mirroring
 * public/templates/public/partials/_map_embed.html) plus the
 * #basemap-sync-status caption, each carrying a `.sync-dot` starting at
 * `data-sync-state="unknown"`.
 */
function buildFixture({ includeFavourites = true, includeCommunityReports = true } = {}) {
  const overlayRow = (key) => `
    <li role="none">
      <button type="button" data-overlay-key="${key}">
        <span class="sync-dot" data-sync-state="unknown" aria-hidden="true"></span>
        ${key}
      </button>
    </li>`;

  document.body.innerHTML = `
    <ul id="basemap-menu">
      ${overlayRow('l1')}
      ${overlayRow('l2')}
      ${overlayRow('l4')}
      ${overlayRow('l3')}
      ${overlayRow('resorts')}
      ${includeFavourites ? overlayRow('favourites') : ''}
      ${includeCommunityReports ? overlayRow('community_reports') : ''}
      <li id="basemap-sync-status" class="basemap-menu-caption" role="none">
        <span class="sync-dot" data-sync-state="unknown" aria-hidden="true"></span>
        Browsed areas only
      </li>
    </ul>
  `;
}

function dotState(key) {
  const dot = document.querySelector(`[data-overlay-key="${key}"] .sync-dot`);
  return dot ? dot.dataset.syncState : undefined;
}

function basemapDotState() {
  return document.querySelector('#basemap-sync-status .sync-dot').dataset.syncState;
}

/**
 * A fake `CacheStorage`: `match` resolves truthy for any request whose
 * pathname is in `hitPaths`; `keys` reports `basemapCache.name` (if any);
 * `open` returns an object exposing `keys()` for `basemapCache.entries`.
 * Each method optionally throws when the matching `throws.<method>` flag
 * is set, for the "a probe that throws" cases.
 */
function fakeCaches({ hitPaths = [], basemapCache = null, throws = {} } = {}) {
  return {
    match: vi.fn(async (request) => {
      if (throws.match) throw new Error('match failed');
      const path = new URL(request.url).pathname;
      return hitPaths.includes(path) ? new Response('{}') : undefined;
    }),
    keys: vi.fn(async () => {
      if (throws.keys) throw new Error('keys failed');
      return basemapCache ? [basemapCache.name] : [];
    }),
    open: vi.fn(async (name) => {
      if (throws.open) throw new Error('open failed');
      return {
        keys: async () => (basemapCache && basemapCache.name === name ? basemapCache.entries : []),
      };
    }),
  };
}

beforeEach(() => {
  buildFixture();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
});

describe('GeoJSON overlay rows (l1/l2/l4/resorts)', () => {
  it('resolves cached on a Cache Storage hit, uncached on a miss', async () => {
    vi.stubGlobal('caches', fakeCaches({ hitPaths: [MAJOR_REGIONS_PATH, RESORTS_PATH] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l1')).toBe('cached');
    expect(dotState('resorts')).toBe('cached');
    expect(dotState('l2')).toBe('uncached');
    expect(dotState('l4')).toBe('uncached');
  });

  it('probes every GeoJSON row with ignoreSearch (the app appends ?country=ch)', async () => {
    const caches = fakeCaches({ hitPaths: [SUB_REGIONS_PATH] });
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l2')).toBe('cached');
    for (const call of caches.match.mock.calls) {
      expect(call[1]).toEqual({ ignoreSearch: true });
    }
  });
});

describe('l3 (bulletin groupings)', () => {
  it('always resolves to the hollow "unavailable" state without being probed', async () => {
    const caches = fakeCaches({
      hitPaths: [MAJOR_REGIONS_PATH, SUB_REGIONS_PATH, MICRO_REGIONS_PATH, RESORTS_PATH],
    });
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();

    // Network-only in sw.js — a distinct "never cacheable" state (hollow
    // dot), not the grey "uncached" fill.
    expect(dotState('l3')).toBe('unavailable');
    // Exactly the 4 GeoJSON rows are probed — l3 contributes no 5th call.
    expect(caches.match).toHaveBeenCalledTimes(4);
  });
});

describe('IndexedDB overlay rows (favourites/community_reports)', () => {
  it('resolves cached when window.pwaDb holds a row with .geojson, uncached when absent', async () => {
    vi.stubGlobal('caches', fakeCaches());
    window.pwaDb = {
      get: vi.fn(async (_store, key) => {
        if (key === 'favourites') return { key: 'favourites', geojson: { type: 'FeatureCollection' } };
        return undefined;
      }),
    };

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('favourites')).toBe('cached');
    expect(dotState('community_reports')).toBe('uncached');
    expect(window.pwaDb.get).toHaveBeenCalledWith('data:map_overlays', 'favourites');
  });

  it('resolves uncached, not throw, when window.pwaDb is unavailable', async () => {
    vi.stubGlobal('caches', fakeCaches());
    // No window.pwaDb at all — the "DB probe path unavailable" case.

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    expect(dotState('favourites')).toBe('uncached');
    expect(dotState('community_reports')).toBe('uncached');
  });

  it('skips rows absent from the DOM (conditionally rendered)', async () => {
    buildFixture({ includeFavourites: false, includeCommunityReports: false });
    vi.stubGlobal('caches', fakeCaches());
    window.pwaDb = { get: vi.fn(async () => ({ geojson: {} })) };

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    expect(document.querySelector('[data-overlay-key="favourites"]')).toBeNull();
    expect(document.querySelector('[data-overlay-key="community_reports"]')).toBeNull();
  });
});

describe('basemap indicator', () => {
  it('resolves cached when a snowdesk-basemap-* cache is non-empty', async () => {
    vi.stubGlobal(
      'caches',
      fakeCaches({
        basemapCache: { name: 'snowdesk-basemap-v1', entries: [{}] },
      }),
    );

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState()).toBe('cached');
  });

  it('resolves uncached when the basemap cache is empty', async () => {
    vi.stubGlobal(
      'caches',
      fakeCaches({
        basemapCache: { name: 'snowdesk-basemap-v1', entries: [] },
      }),
    );

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState()).toBe('uncached');
  });

  it('resolves uncached when no snowdesk-basemap-* cache exists at all', async () => {
    vi.stubGlobal('caches', fakeCaches());

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState()).toBe('uncached');
  });
});

describe('probes that throw', () => {
  it('resolves the affected dot to uncached and refresh() still resolves', async () => {
    vi.stubGlobal(
      'caches',
      fakeCaches({ throws: { match: true, keys: true, open: true } }),
    );
    window.pwaDb = {
      get: vi.fn(async () => {
        throw new Error('idb boom');
      }),
    };

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    expect(dotState('l1')).toBe('uncached');
    expect(dotState('favourites')).toBe('uncached');
    expect(basemapDotState()).toBe('uncached');
  });
});

describe('Cache Storage unsupported', () => {
  it('leaves every dot at "unknown" and resolves without probing', async () => {
    // No vi.stubGlobal('caches', ...) here — jsdom has no `caches` global
    // by default, matching a real browser without Cache Storage support.
    expect('caches' in window).toBe(false);

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    for (const key of ['l1', 'l2', 'l4', 'l3', 'resorts', 'favourites', 'community_reports']) {
      expect(dotState(key)).toBe('unknown');
    }
    expect(basemapDotState()).toBe('unknown');
  });
});

describe('markCached (optimistic live update)', () => {
  it('flips a cacheable row to "cached" with no probe', () => {
    // No caches / pwaDb stubbed — markCached is probe-free by design.
    expect('caches' in window).toBe(false);

    window.pwaLayerSyncStatus.markCached('l1');
    window.pwaLayerSyncStatus.markCached('favourites');

    expect(dotState('l1')).toBe('cached');
    expect(dotState('favourites')).toBe('cached');
  });

  it('no-ops for l3 (network-only, never cached) so its dot stays unknown', () => {
    window.pwaLayerSyncStatus.markCached('l3');
    expect(dotState('l3')).toBe('unknown');
  });

  it('no-ops for a key absent from OVERLAY_RESOURCES', () => {
    expect(() => window.pwaLayerSyncStatus.markCached('country.ch')).not.toThrow();
    expect(() => window.pwaLayerSyncStatus.markCached('nonsense')).not.toThrow();
  });

  it('is corrected by a later refresh() that probes a real miss', async () => {
    // Optimistic green, then a popover-open refresh finds no cache entry —
    // the row reverts to uncached (the self-correcting re-verify contract).
    window.pwaLayerSyncStatus.markCached('l1');
    expect(dotState('l1')).toBe('cached');

    vi.stubGlobal('caches', fakeCaches({ hitPaths: [] }));
    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l1')).toBe('uncached');
  });
});
