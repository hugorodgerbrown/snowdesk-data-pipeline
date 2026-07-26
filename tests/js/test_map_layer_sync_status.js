/*
 * tests/js/test_map_layer_sync_status.js — Vitest unit tests for
 * static/js/map_layer_sync_status.js (SNOW-505 + offline-integrity rework).
 *
 * `window.pwaLayerSyncStatus` is a stateless, side-effect-free module
 * (every `refresh()` call just re-reads the DOM and re-probes Cache
 * Storage / IndexedDB) — so it's imported once for its side effects and
 * every test builds its own fixture DOM plus fresh `global.caches` /
 * `window.pwaDb` stubs.
 *
 * `global.caches` isn't part of jsdom (same gap as `indexedDB` — see
 * tests/js/setup.js), so it's absent unless a test opts in via
 * `vi.stubGlobal('caches', ...)`.
 *
 * Offline gating: the module reads `navigator.onLine` live, and jsdom
 * defaults it to `true`. `setOnline(false)` overrides it per-test (reset in
 * afterEach) to exercise the offline red-dot + row-disable behaviour.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/map_layer_sync_status.js';

const MAJOR_REGIONS_PATH = '/api/major-regions.geojson';
const SUB_REGIONS_PATH = '/api/sub-regions.geojson';
const MICRO_REGIONS_PATH = '/api/regions.geojson';
const RESORTS_PATH = '/api/resorts.geojson';
const RATINGS_PATH = '/api/ratings/';

// Basemap style URLs (cross-origin, as the picker's data-basemap-url is).
const STANDARD_STYLE = 'https://tiles.example/standard/style.json';
const SWISSTOPO_STYLE = 'https://tiles.example/swisstopo/style.json';

/**
 * Build the #basemap-menu fixture: one row per overlay key plus the two
 * basemap radio rows (Standard active, Swisstopo not) — mirroring
 * public/templates/public/partials/_map_embed.html — each carrying a
 * `.sync-dot` starting at `data-sync-state="unknown"`.
 */
function buildFixture({
  includeFavourites = true,
  includeCommunityReports = true,
  // SNOW-524: country rows are opt-in so the pre-existing tier tests keep
  // exercising the country-blind fallback (no country enabled ⟹ ignoreSearch).
  countries = null,
} = {}) {
  const overlayRow = (key) => `
    <li role="none">
      <button type="button" class="basemap-menu-item basemap-menu-item--overlay" data-overlay-key="${key}">
        <span class="sync-dot" data-sync-state="unknown" aria-hidden="true"></span>
        ${key}
      </button>
    </li>`;

  // SNOW-524: one row per country, `checked` mirroring the picker's
  // aria-checked (which is what _enabledCountryCodes reads).
  const countryRow = ({ code, checked }) => `
    <li role="none">
      <button type="button" class="basemap-menu-item basemap-menu-item--overlay"
              data-overlay-key="country.${code}" aria-checked="${checked ? 'true' : 'false'}">
        <span class="sync-dot" data-sync-state="unknown" aria-hidden="true"></span>
        ${code}
      </button>
    </li>`;

  const basemapRow = (key, url, active) => `
    <li role="none">
      <button type="button" class="basemap-menu-item" role="menuitemradio"
              data-basemap-key="${key}" data-basemap-url="${url}"
              aria-checked="${active ? 'true' : 'false'}">
        <span class="sync-dot" data-sync-state="unknown" aria-hidden="true"></span>
        ${key}
      </button>
    </li>`;

  document.body.innerHTML = `
    <ul id="basemap-menu">
      ${(countries || []).map(countryRow).join('')}
      ${overlayRow('l1')}
      ${overlayRow('l2')}
      ${overlayRow('l4')}
      ${overlayRow('l3')}
      ${overlayRow('resorts')}
      ${includeFavourites ? overlayRow('favourites') : ''}
      ${includeCommunityReports ? overlayRow('community_reports') : ''}
      ${basemapRow('standard', STANDARD_STYLE, true)}
      ${basemapRow('swisstopo', SWISSTOPO_STYLE, false)}
    </ul>
  `;
}

function dotState(key) {
  const dot = document.querySelector(`[data-overlay-key="${key}"] .sync-dot`);
  return dot ? dot.dataset.syncState : undefined;
}

function rowDisabled(key) {
  const row = document.querySelector(`[data-overlay-key="${key}"]`);
  return row ? row.getAttribute('aria-disabled') === 'true' : undefined;
}

function basemapDotState(key) {
  const dot = document.querySelector(`[data-basemap-key="${key}"] .sync-dot`);
  return dot ? dot.dataset.syncState : undefined;
}

function basemapRowDisabled(key) {
  const row = document.querySelector(`[data-basemap-key="${key}"]`);
  return row ? row.getAttribute('aria-disabled') === 'true' : undefined;
}

function setOnline(value) {
  Object.defineProperty(window.navigator, 'onLine', { value, configurable: true });
}

/**
 * A fake `CacheStorage`: `match` resolves truthy for any request whose URL
 * (compared by pathname for same-origin GeoJSON, or by full URL for the
 * cross-origin basemap style URLs) is in `hitUrls`. Optionally throws when
 * `throws.match` is set.
 */
function fakeCaches({ hitPaths = [], hitUrls = [], hitQueries = [], throws = {} } = {}) {
  return {
    match: vi.fn(async (request) => {
      if (throws.match) throw new Error('match failed');
      const url = request.url;
      if (hitUrls.includes(url)) return new Response('{}');
      const parsed = new URL(url);
      // SNOW-524: `hitQueries` entries are exact `path + search` strings, so a
      // test can cache `?country=ch` while leaving `?country=at` a miss —
      // the distinction the real per-URL SW cache makes and the old
      // `ignoreSearch` probe erased.
      if (hitQueries.includes(parsed.pathname + parsed.search)) return new Response('{}');
      return hitPaths.includes(parsed.pathname) ? new Response('{}') : undefined;
    }),
  };
}

/**
 * COUNTRY_FEED_PATHS scoped to `code` — all four feeds a country load fetches.
 * Switzerland is no exception: boot runs the same load for it.
 */
function countryFeeds(code) {
  return [MAJOR_REGIONS_PATH, SUB_REGIONS_PATH, MICRO_REGIONS_PATH, RATINGS_PATH].map(
    (path) => `${path}?country=${code}`,
  );
}

beforeEach(() => {
  buildFixture();
  setOnline(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setOnline(true);
  delete window.pwaDb;
});

describe('GeoJSON overlay rows (l1/l2/l4/resorts) — online', () => {
  it('resolves cached on a Cache Storage hit, uncached on a miss', async () => {
    vi.stubGlobal('caches', fakeCaches({ hitPaths: [MAJOR_REGIONS_PATH, RESORTS_PATH] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l1')).toBe('cached');
    expect(dotState('resorts')).toBe('cached');
    expect(dotState('l2')).toBe('uncached');
    expect(dotState('l4')).toBe('uncached');
    // Online: an uncached row is advisory only — never disabled.
    expect(rowDisabled('l4')).toBe(false);
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

describe('country rows (SNOW-524)', () => {
  const CH_AT = [
    { code: 'ch', checked: true },
    { code: 'at', checked: false },
  ];

  it('greens a country when all four of its feeds are cached', async () => {
    buildFixture({ countries: CH_AT });
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('ch') }));

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('country.ch')).toBe('cached');
    expect(dotState('country.at')).toBe('uncached');
  });

  it('leaves a country uncached when only some of its feeds are cached', async () => {
    buildFixture({ countries: CH_AT });
    // Geometry + ratings but neither region tier — a partial country isn't
    // offline-ready.
    vi.stubGlobal(
      'caches',
      fakeCaches({ hitQueries: [`${MICRO_REGIONS_PATH}?country=ch`, `${RATINGS_PATH}?country=ch`] }),
    );

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('country.ch')).toBe('uncached');
  });

  it('offline: an uncached country is red AND its row disabled', async () => {
    buildFixture({ countries: CH_AT });
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('ch') }));

    await window.pwaLayerSyncStatus.refresh();

    // The reported bug: Austria was offered while offline and uncached, and
    // toggling it fired four failing fetches behind green dots.
    expect(dotState('country.at')).toBe('unavailable-offline');
    expect(rowDisabled('country.at')).toBe(true);
    // Switzerland is cached, so it stays available and interactive.
    expect(dotState('country.ch')).toBe('cached');
    expect(rowDisabled('country.ch')).toBe(false);
  });

  it('probes country feeds exactly, without ignoreSearch', async () => {
    buildFixture({ countries: [{ code: 'at', checked: true }] });
    // Switzerland cached, Austria not — under ignoreSearch this would have
    // reported Austria cached off Switzerland's entry.
    const caches = fakeCaches({ hitQueries: countryFeeds('ch') });
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('country.at')).toBe('uncached');
    const countryCalls = caches.match.mock.calls.filter((call) =>
      call[0].url.includes('country=at'),
    );
    // All four feeds probed for AT (the tier rows re-probe three of them under
    // their own country scoping, hence duplicates) — and never with
    // ignoreSearch, or CH's cached entries would have answered for AT.
    const probed = new Set(countryCalls.map((call) => new URL(call[0].url).pathname));
    expect(probed).toEqual(
      new Set([MAJOR_REGIONS_PATH, SUB_REGIONS_PATH, MICRO_REGIONS_PATH, RATINGS_PATH]),
    );
    for (const call of countryCalls) expect(call[1]).toBeUndefined();
  });

  it('scopes the l1/l2/l4 dots to the enabled countries', async () => {
    buildFixture({
      countries: [
        { code: 'ch', checked: true },
        { code: 'at', checked: true },
      ],
    });
    // Every tier cached for CH, none for AT — with both enabled, no tier is
    // honestly available offline, so no tier dot may sit green above AT's red.
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('ch') }));

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l1')).toBe('uncached');
    expect(dotState('l2')).toBe('uncached');
    expect(dotState('l4')).toBe('uncached');
  });

  it('greens the tiers when every enabled country has them', async () => {
    buildFixture({
      countries: [
        { code: 'ch', checked: true },
        { code: 'at', checked: false },
      ],
    });
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('ch') }));

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l1')).toBe('cached');
    expect(dotState('l4')).toBe('cached');
  });

  it('treats Switzerland like any other country — no default-country exemption', async () => {
    // CH must clear the same four-feed bar as everyone else. Boot runs the
    // same country load for it (map.js's restore loop), so the two feeds it
    // fetches on the critical path are not sufficient on their own.
    buildFixture({ countries: [{ code: 'ch', checked: true }] });
    vi.stubGlobal(
      'caches',
      fakeCaches({ hitQueries: [`${MICRO_REGIONS_PATH}?country=ch`, `${RATINGS_PATH}?country=ch`] }),
    );

    await window.pwaLayerSyncStatus.refresh();
    expect(dotState('country.ch')).toBe('uncached');

    // With its L1/L2 fetched too — what the boot country load now does — it
    // reaches the same cached state a toggled-on country reaches.
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('ch') }));
    await window.pwaLayerSyncStatus.refresh();
    expect(dotState('country.ch')).toBe('cached');
  });

  it('markSyncing paints the pending state synchronously, without a probe', async () => {
    buildFixture({ countries: CH_AT });
    const caches = fakeCaches({ hitQueries: countryFeeds('ch') });
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();
    caches.match.mockClear();

    window.pwaLayerSyncStatus.markSyncing('country.at');
    window.pwaLayerSyncStatus.markSyncing('l4');

    // Synchronous — no await, and no probe issued.
    expect(dotState('country.at')).toBe('syncing');
    expect(dotState('l4')).toBe('syncing');
    expect(caches.match).not.toHaveBeenCalled();
    // A fetch in flight is not a reason to lock the control.
    expect(rowDisabled('country.at')).toBe(false);
  });

  it('holds the pending state for a minimum dwell before greening', async () => {
    vi.useFakeTimers();
    try {
      buildFixture({ countries: CH_AT });
      vi.stubGlobal('caches', fakeCaches());

      window.pwaLayerSyncStatus.markSyncing('country.at');
      expect(dotState('country.at')).toBe('syncing');

      // A fetch that resolves almost immediately must not skip the transition.
      vi.advanceTimersByTime(20);
      window.pwaLayerSyncStatus.markCached('country.at');
      expect(dotState('country.at')).toBe('syncing');

      // ...but it does land once the dwell has elapsed.
      await vi.advanceTimersByTimeAsync(500);
      expect(dotState('country.at')).toBe('cached');
    } finally {
      vi.useRealTimers();
    }
  });

  it('greens immediately when the row was never marked syncing', async () => {
    buildFixture({ countries: CH_AT });
    vi.stubGlobal('caches', fakeCaches());

    window.pwaLayerSyncStatus.markCached('country.at');

    expect(dotState('country.at')).toBe('cached');
  });

  it('refresh() clears pending state so a failed load cannot impose a later dwell', async () => {
    buildFixture({ countries: CH_AT });
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('ch') }));

    // Load starts, then fails — markCached never arrives; refresh() supersedes.
    window.pwaLayerSyncStatus.markSyncing('country.at');
    await window.pwaLayerSyncStatus.refresh();
    expect(dotState('country.at')).toBe('uncached');

    // A later markCached must paint at once, not inherit the stale dwell.
    window.pwaLayerSyncStatus.markCached('country.at');
    expect(dotState('country.at')).toBe('cached');
  });

  it('markCached greens a country row optimistically', async () => {
    buildFixture({ countries: CH_AT });
    vi.stubGlobal('caches', fakeCaches());

    await window.pwaLayerSyncStatus.refresh();
    expect(dotState('country.fr')).toBeUndefined();
    expect(dotState('country.at')).toBe('uncached');

    window.pwaLayerSyncStatus.markCached('country.at');

    expect(dotState('country.at')).toBe('cached');
  });
});

describe('l3 (bulletin groupings)', () => {
  it('online: hollow "unavailable" state, row enabled', async () => {
    vi.stubGlobal('caches', fakeCaches());

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l3')).toBe('unavailable');
    expect(rowDisabled('l3')).toBe(false);
  });

  it('offline: red "unavailable-offline" state, row disabled', async () => {
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches());

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l3')).toBe('unavailable-offline');
    expect(rowDisabled('l3')).toBe(true);
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

describe('per-basemap rows', () => {
  it('online: style cached → green; not cached → grey; both selectable', async () => {
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [STANDARD_STYLE] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('standard')).toBe('cached');
    expect(basemapDotState('swisstopo')).toBe('uncached');
    expect(basemapRowDisabled('standard')).toBe(false);
    expect(basemapRowDisabled('swisstopo')).toBe(false);
  });

  it('offline: a basemap whose style is not cached is red + disabled', async () => {
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [] }));

    await window.pwaLayerSyncStatus.refresh();

    // Standard is the active basemap (aria-checked="true") → always available
    // even with no cache hit; you can't be stranded on a map you can't leave.
    expect(basemapDotState('standard')).toBe('cached');
    expect(basemapRowDisabled('standard')).toBe(false);
    // Swisstopo isn't active and isn't cached → unavailable offline.
    expect(basemapDotState('swisstopo')).toBe('unavailable-offline');
    expect(basemapRowDisabled('swisstopo')).toBe(true);
  });

  it('offline: a downloaded (style-cached) non-active basemap stays selectable', async () => {
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [SWISSTOPO_STYLE] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('swisstopo')).toBe('cached');
    expect(basemapRowDisabled('swisstopo')).toBe(false);
  });
});

describe('offline gating of overlay rows', () => {
  it('offline + uncached → red dot and disabled row; cached → green and enabled', async () => {
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches({ hitPaths: [MICRO_REGIONS_PATH] }));

    await window.pwaLayerSyncStatus.refresh();

    // l4 (regions.geojson) is cached — available offline, enabled.
    expect(dotState('l4')).toBe('cached');
    expect(rowDisabled('l4')).toBe(false);
    // l1/l2/resorts uncached — red and locked.
    expect(dotState('l1')).toBe('unavailable-offline');
    expect(rowDisabled('l1')).toBe(true);
    expect(dotState('resorts')).toBe('unavailable-offline');
    expect(rowDisabled('resorts')).toBe(true);
  });

  it('coming back online re-enables a row this module disabled', async () => {
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches({ hitPaths: [] }));
    await window.pwaLayerSyncStatus.refresh();
    expect(rowDisabled('l1')).toBe(true);

    setOnline(true);
    await window.pwaLayerSyncStatus.refresh();
    expect(dotState('l1')).toBe('uncached');
    expect(rowDisabled('l1')).toBe(false);
    expect(document.querySelector('[data-overlay-key="l1"]').hasAttribute('aria-disabled')).toBe(false);
  });
});

describe('connectivity-change listener', () => {
  it('re-runs refresh (reds-out uncached rows) on snowdesk:connectivity-changed', async () => {
    vi.stubGlobal('caches', fakeCaches({ hitPaths: [] }));
    setOnline(false);

    document.dispatchEvent(
      new CustomEvent('snowdesk:connectivity-changed', { detail: { online: false } }),
    );

    // The listener calls refresh() without awaiting; give the async probes a
    // few microtask turns to settle.
    for (let i = 0; i < 5; i += 1) await Promise.resolve();

    expect(dotState('l1')).toBe('unavailable-offline');
    expect(rowDisabled('l1')).toBe(true);
  });
});

describe('probes that throw', () => {
  it('resolves the affected dot to uncached (online) and refresh() still resolves', async () => {
    vi.stubGlobal('caches', fakeCaches({ throws: { match: true } }));
    window.pwaDb = {
      get: vi.fn(async () => {
        throw new Error('idb boom');
      }),
    };

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    expect(dotState('l1')).toBe('uncached');
    expect(dotState('favourites')).toBe('uncached');
    expect(basemapDotState('swisstopo')).toBe('uncached');
    // The active basemap is available regardless of the probe outcome.
    expect(basemapDotState('standard')).toBe('cached');
  });
});

describe('Cache Storage unsupported', () => {
  it('leaves every dot at "unknown" and resolves without probing', async () => {
    expect('caches' in window).toBe(false);

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    for (const key of ['l1', 'l2', 'l4', 'l3', 'resorts', 'favourites', 'community_reports']) {
      expect(dotState(key)).toBe('unknown');
    }
    expect(basemapDotState('standard')).toBe('unknown');
  });
});

describe('markCached (optimistic live update)', () => {
  it('flips a cacheable row to "cached" with no probe', () => {
    expect('caches' in window).toBe(false);

    window.pwaLayerSyncStatus.markCached('l1');
    window.pwaLayerSyncStatus.markCached('favourites');

    expect(dotState('l1')).toBe('cached');
    expect(dotState('favourites')).toBe('cached');
  });

  it('clears an offline-disabled marker when it flips a row green', async () => {
    // Row starts offline-disabled (red), then a successful load marks it
    // cached — it must re-enable, not stay locked.
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches({ hitPaths: [] }));
    await window.pwaLayerSyncStatus.refresh();
    expect(rowDisabled('l1')).toBe(true);

    window.pwaLayerSyncStatus.markCached('l1');

    expect(dotState('l1')).toBe('cached');
    expect(rowDisabled('l1')).toBe(false);
  });

  it('no-ops for l3 (network-only, never cached) so its dot stays unknown', () => {
    window.pwaLayerSyncStatus.markCached('l3');
    expect(dotState('l3')).toBe('unknown');
  });

  it('no-ops for a key absent from OVERLAY_RESOURCES', () => {
    expect(() => window.pwaLayerSyncStatus.markCached('country.ch')).not.toThrow();
    expect(() => window.pwaLayerSyncStatus.markCached('nonsense')).not.toThrow();
  });
});
