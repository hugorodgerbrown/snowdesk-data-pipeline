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
 *
 * SNOW-586: the `downloaded` (pinned-tiles) row's own describe block also
 * side-effect-imports `basemap_download_core.js`, so `_probeAnyPinnedTile()`
 * runs its REAL `cachedTilesFromURLs()` filtering against a real URL
 * template rather than a stand-in — the whole point of those tests is
 * proving tiles from a bucket that doesn't match the active basemap are
 * excluded and tiles from one that does are still found after it. That
 * block stubs the map handle as the bare global `MAP` and hides it from
 * `window` (`hideWindowMap()`), which is how a browser presents map.js's
 * top-level `let MAP` — see that helper's docstring.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/basemap_download_core.js';
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
 * apps/public/templates/public/partials/_map_embed.html — each carrying a
 * `.sync-dot` starting at `data-sync-state="unknown"`.
 */
function buildFixture({
  includeFavourites = true,
  includeCommunityReports = true,
  // SNOW-524: country rows are opt-in so the pre-existing tier tests keep
  // exercising the country-blind fallback (no country enabled ⟹ ignoreSearch).
  countries = null,
  // SNOW-586: the "Available offline" (pinned-tiles) row is opt-in too, for
  // the same reason — every pre-existing test builds its fixture with no
  // `downloaded` row at all, so `_overlayDot('downloaded')` returns null and
  // `refresh()` skips `_probeAnyPinnedTile()` entirely for them. Adding the
  // row unconditionally would exercise a probe none of those 29 tests were
  // written to expect.
  includeDownloaded = false,
  // SNOW-573: opt-in for the same reason as `includeDownloaded` above — the
  // weather row is the only one carrying a SECOND disable marker owned by
  // another module, and the pre-existing tests were not written to expect it.
  includeWeather = false,
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
      ${overlayRow('resorts')}
      ${includeFavourites ? overlayRow('favourites') : ''}
      ${includeCommunityReports ? overlayRow('community_reports') : ''}
      ${includeDownloaded ? overlayRow('downloaded') : ''}
      ${includeWeather ? overlayRow('weather') : ''}
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
 * Give `window` the shape a browser gives it for map.js's map handle:
 * `let MAP = null` at the top of a classic script binds in the global
 * declarative environment, so `window.MAP` is undefined while the bare
 * identifier `MAP` resolves. jsdom collapses `globalThis` and `window` onto
 * one object, which erases that distinction — the conflation that let a
 * `window.MAP` read look correct here for as long as it did.
 *
 * Installs a stand-in `window` inheriting everything from the real one
 * (so `window.pwaLayerSyncStatus`, `window.pwaDb` and the rest still
 * resolve) with an own `MAP` of `undefined`. Returns the restore function.
 *
 * @returns {function(): void}
 */
function hideWindowMap() {
  const real = globalThis.window;
  const standIn = Object.create(real);
  Object.defineProperty(standIn, 'MAP', { value: undefined });
  const install = (value) =>
    Object.defineProperty(globalThis, 'window', { value, configurable: true, writable: true });
  install(standIn);
  return () => install(real);
}

/**
 * A fake `CacheStorage`: `match` resolves truthy for any request whose URL
 * (compared by pathname for same-origin GeoJSON, or by full URL for the
 * cross-origin basemap style URLs) is in `hitUrls`. Optionally throws when
 * `throws.match` is set.
 *
 * SNOW-586: `pinnedBuckets` extends the same fake with `keys()`/`open()` —
 * the two `CacheStorage` methods `_probeAnyPinnedTile()` needs and the
 * pre-SNOW-586 version of this fake never implemented (so that probe threw,
 * was swallowed by `refresh()`'s per-task `.catch()`, and the `downloaded`
 * row had no unit coverage at all — the union fix included). Extending the
 * ONE shared fake rather than adding a second is safe here: no existing
 * test's fixture includes a `downloaded` row (see `buildFixture`'s
 * `includeDownloaded`), so `_probeAnyPinnedTile()` is never reached by any
 * of them regardless of what `keys()`/`open()` do — the other 29 tests
 * exercise exactly the same code path they always have.
 *
 * `pinnedBuckets` is `{ [cacheName]: { urls: string[], openThrows?:
 * boolean, keysThrows?: boolean } }`. `keys()` returns the configured
 * bucket names (in the object's own key order, which is what
 * `Object.keys()` — and so `caches.keys()` in a real browser — preserves);
 * `open(name)` returns a per-bucket fake `Cache` whose own `keys()`
 * resolves to `{url}`-shaped Request stand-ins, or throws per-bucket via
 * `openThrows`/`keysThrows` so a test can prove one bucket's failure never
 * loses another's tiles.
 */
function fakeCaches({
  hitPaths = [],
  hitUrls = [],
  hitQueries = [],
  throws = {},
  pinnedBuckets = {},
} = {}) {
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
    keys: vi.fn(async () => {
      if (throws.keys) throw new Error('keys failed');
      return Object.keys(pinnedBuckets);
    }),
    open: vi.fn(async (name) => {
      const bucket = pinnedBuckets[name];
      if (!bucket || bucket.openThrows) throw new Error(`open failed for ${name}`);
      return {
        keys: vi.fn(async () => {
          if (bucket.keysThrows) throw new Error(`keys failed for ${name}`);
          return bucket.urls.map((url) => ({ url }));
        }),
      };
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

  it('never probes the bulletin-boundary feed — it has no row (SNOW-532)', async () => {
    // The boundary is a companion to L4 with no menu row of its own, so the
    // dashboard makes no claim about it either way. Its responses are still
    // cached for settled dates by sw.js (SNOW-526); that is simply not
    // surfaced here, and this module must not resurrect a probe for it.
    window.history.pushState({}, '', '/map/?d=2025-12-01');
    const caches = fakeCaches();
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();

    const probed = caches.match.mock.calls.map((call) => new URL(call[0].url).pathname);
    expect(probed).not.toContain('/api/bulletin-groupings.geojson');
    window.history.pushState({}, '', '/');
  });

  // SNOW-638: the `resorts` row probes RESORTS_PATH (`/api/resorts.geojson`)
  // ONLY. map.js's boot handler separately fetches the plain-JSON sibling
  // `/api/resorts.json` (search-index metadata, not geometry) to populate
  // RESORTS_BY_REGION — that fetch is deliberately absent from sw.js's
  // STATIC_PATHS, so it always rejects offline. This dot must not claim
  // anything about that feed: don't "fix" its absence here by adding a
  // probe for it — the two paths serve different purposes and the .json
  // one is intentionally uncacheable (see map.js's boot handler comment).
  it('never probes /api/resorts.json — only the .geojson sibling has a row', async () => {
    const caches = fakeCaches({ hitPaths: [RESORTS_PATH] });
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('resorts')).toBe('cached');
    const probed = caches.match.mock.calls.map((call) => new URL(call[0].url).pathname);
    expect(probed).not.toContain('/api/resorts.json');
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

describe('downloaded row (pinned-tiles) — SNOW-586 multi-bucket union', () => {
  // A `{z}/{x}/{y}` template `cachedTilesFromURLs` (basemap_download_core.js)
  // can actually match, plus one URL that satisfies it and one that never
  // could (wrong host/shape entirely — no amount of template-matching
  // confuses the two).
  const TEMPLATE = 'https://tiles.example/{z}/{x}/{y}.pbf';
  const MATCHING_TILE_URL = 'https://tiles.example/10/1/1.pbf';
  const NON_MATCHING_URL = 'https://other.example/not-a-tile.json';
  // The furniture a download pins alongside the tiles: the style JSON and
  // its sprite sheet. Same host as the template, neither matching it.
  const STYLE_JSON_URL = 'https://tiles.example/standard/style.json';
  const SPRITE_URL = 'https://tiles.example/standard/sprite@2x.png';

  let restoreWindow = null;

  beforeEach(() => {
    buildFixture({ includeDownloaded: true });
    // `_probeAnyPinnedTile()` falls back to "any entry at all" when either
    // of these is unresolvable (see its own docstring) — stubbed here so
    // the tests below exercise the REAL per-tile filtering instead of
    // that fallback, which is the only way to actually distinguish the
    // matching bucket from the non-matching one.
    //
    // SNOW-610: the map handle now comes from `window.snowdeskMapState`,
    // map.js's one named channel to its shared state. `hideWindowMap()`
    // still runs, so `window.MAP` stays undefined throughout — that is
    // what keeps this the M1 regression test: if the module ever goes back
    // to reading `window.MAP`, `map` resolves null, the template goes with
    // it, and the probe drops onto the "any entry at all" fallback these
    // tests exist to distinguish from real per-tile filtering.
    window.snowdeskMapState = Object.freeze({ get map() { return {}; } });
    globalThis.activeBasemapTileTemplate = () => TEMPLATE;
    restoreWindow = hideWindowMap();
  });

  afterEach(() => {
    if (restoreWindow) restoreWindow();
    restoreWindow = null;
    delete window.snowdeskMapState;
    delete globalThis.activeBasemapTileTemplate;
  });

  it('a bucket holding only the style JSON and sprites reads as uncached', async () => {
    // The case the probe exists for (see its docstring): a run that pinned
    // the furniture and none of the map must not read as available
    // offline. Fails against a `window.MAP` read, which resolves to
    // undefined in a browser and drops the probe onto its "any entry at
    // all" fallback.
    vi.stubGlobal(
      'caches',
      fakeCaches({
        pinnedBuckets: {
          'snowdesk-basemap-pinned-region-a': { urls: [STYLE_JSON_URL, SPRITE_URL] },
        },
      }),
    );

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('downloaded')).toBe('uncached');
  });

  it('an earlier bucket with no tiles for this basemap does not hide a later one that has them', async () => {
    // Object key order is `keys()` order (see fakeCaches's own docstring):
    // the "region-a" bucket, which the OLD names.find() would have picked
    // and which holds nothing matching TEMPLATE, comes first; "region-b",
    // which does, comes second. Under the pre-fix find() this would have
    // resolved `false` — that is the regression this test catches.
    vi.stubGlobal(
      'caches',
      fakeCaches({
        pinnedBuckets: {
          'snowdesk-basemap-pinned-region-a': { urls: [NON_MATCHING_URL] },
          'snowdesk-basemap-pinned-region-b': { urls: [MATCHING_TILE_URL] },
        },
      }),
    );

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('downloaded')).toBe('cached');
  });

  it('one bucket failing to enumerate does not lose the others', async () => {
    // "region-a" throws on open() (mirrors a bucket deleted mid-probe, or
    // any other transient CacheStorage failure); "region-b" still holds a
    // matching tile and must still be found — pins the inner try/catch in
    // _probeAnyPinnedTile's per-bucket loop.
    vi.stubGlobal(
      'caches',
      fakeCaches({
        pinnedBuckets: {
          'snowdesk-basemap-pinned-region-a': { urls: [], openThrows: true },
          'snowdesk-basemap-pinned-region-b': { urls: [MATCHING_TILE_URL] },
        },
      }),
    );

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();
    expect(dotState('downloaded')).toBe('cached');
  });
});

describe('Cache Storage unsupported', () => {
  it('leaves every dot at "unknown" and resolves without probing', async () => {
    expect('caches' in window).toBe(false);

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    for (const key of ['l1', 'l2', 'l4', 'resorts', 'favourites', 'community_reports']) {
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

  it('no-ops for a key absent from OVERLAY_RESOURCES', () => {
    expect(() => window.pwaLayerSyncStatus.markCached('country.ch')).not.toThrow();
    expect(() => window.pwaLayerSyncStatus.markCached('nonsense')).not.toThrow();
  });
});

describe('refresh coalescing (SNOW-613)', () => {
  it('runs one pass when a single caller asks', async () => {
    const caches = fakeCaches({ hitPaths: [MAJOR_REGIONS_PATH] });
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();
    expect(caches.match.mock.calls.length).toBeGreaterThan(0);
  });

  it('collapses a burst of concurrent calls onto two passes, not five', async () => {
    const caches = fakeCaches({ hitPaths: [MAJOR_REGIONS_PATH] });
    vi.stubGlobal('caches', caches);

    // Measure one pass first, so the assertion is in passes rather than in
    // a probe count that would move whenever a row is added.
    await window.pwaLayerSyncStatus.refresh();
    const perPass = caches.match.mock.calls.length;
    caches.match.mockClear();

    // A layers-menu open, a region tap and a connectivity flip can all land
    // in the same tick.
    await Promise.all([
      window.pwaLayerSyncStatus.refresh(),
      window.pwaLayerSyncStatus.refresh(),
      window.pwaLayerSyncStatus.refresh(),
      window.pwaLayerSyncStatus.refresh(),
      window.pwaLayerSyncStatus.refresh(),
    ]);

    // The leading pass plus ONE trailing pass for everyone who arrived
    // during it — not five passes, and not one (see the next case).
    expect(caches.match.mock.calls.length).toBe(perPass * 2);
  });

  it('gives a caller arriving mid-pass a result probed after it asked', async () => {
    // Trailing, not leading, is the whole point: a download that has just
    // written its tiles calls refresh(), and handing back the pass already
    // running would settle its dots against state from before the write.
    const caches = fakeCaches({ hitPaths: [] });
    vi.stubGlobal('caches', caches);

    const first = window.pwaLayerSyncStatus.refresh();
    // Cache the resource while the first pass is in flight.
    caches.match.mockImplementation(async (request) => {
      return new URL(request.url).pathname === MAJOR_REGIONS_PATH
        ? new Response('{}')
        : undefined;
    });
    const second = window.pwaLayerSyncStatus.refresh();

    await Promise.all([first, second]);

    expect(dotState('l1')).toBe('cached');
  });

  it('starts a fresh pass once the previous burst has settled', async () => {
    const caches = fakeCaches({ hitPaths: [MAJOR_REGIONS_PATH] });
    vi.stubGlobal('caches', caches);

    await window.pwaLayerSyncStatus.refresh();
    const perPass = caches.match.mock.calls.length;
    caches.match.mockClear();

    await window.pwaLayerSyncStatus.refresh();

    // The guard is not a one-shot latch — a later menu open still re-probes.
    expect(caches.match.mock.calls.length).toBe(perPass);
  });
});

describe('weather row — two independent disables on one row (SNOW-573)', () => {
  // The weather row is disabled by TWO modules for two unrelated reasons:
  // this one, while offline and uncached, and map.js, while the scrubbed date
  // sits outside the stored forecast window. Each owns a marker attribute and
  // must clear `aria-disabled` only once the OTHER's marker is gone, or
  // whichever reason lifts first silently re-enables a row the other still
  // holds down.
  const WEATHER_MARKER = 'data-weather-disabled-out-of-window';

  function weatherRow() {
    return document.querySelector('[data-overlay-key="weather"]');
  }

  /** Stand in for map.js disabling the row for an out-of-window date. */
  function disableForOutOfWindow() {
    const row = weatherRow();
    row.setAttribute('aria-disabled', 'true');
    row.setAttribute(WEATHER_MARKER, '1');
  }

  beforeEach(() => {
    buildFixture({ includeWeather: true });
    // `caches` is not part of jsdom — without the stub the whole probe pass
    // throws before it reaches the idb-backed weather row.
    vi.stubGlobal('caches', fakeCaches());
    window.pwaDb = { get: vi.fn(async () => ({ geojson: { type: 'FeatureCollection' } })) };
  });

  it('coming back online leaves the row disabled while the date is still out of window', async () => {
    // The reported sequence: out-of-window date, then offline, then online.
    disableForOutOfWindow();
    setOnline(false);
    // Uncached, so this module disables it too and tags its own marker.
    window.pwaDb = { get: vi.fn(async () => undefined) };
    await window.pwaLayerSyncStatus.refresh();
    expect(rowDisabled('weather')).toBe(true);

    // Back online and cached — this module's reason is gone, but the date's
    // is not. Before the fix, `aria-disabled` was dropped unconditionally
    // here and the row became clickable for a date with nothing to draw.
    setOnline(true);
    window.pwaDb = { get: vi.fn(async () => ({ geojson: { type: 'FeatureCollection' } })) };
    await window.pwaLayerSyncStatus.refresh();

    expect(rowDisabled('weather')).toBe(true);
    expect(weatherRow().getAttribute(WEATHER_MARKER)).toBe('1');
    // This module still drops its OWN marker — it no longer holds the row.
    expect(weatherRow().getAttribute('data-sync-disabled-offline')).toBe(null);
  });

  it('re-enables the row when neither reason is in force', async () => {
    // The plain case must not regress: with no out-of-window marker, coming
    // back online re-enables exactly as it always did.
    setOnline(false);
    window.pwaDb = { get: vi.fn(async () => undefined) };
    await window.pwaLayerSyncStatus.refresh();
    expect(rowDisabled('weather')).toBe(true);

    setOnline(true);
    window.pwaDb = { get: vi.fn(async () => ({ geojson: { type: 'FeatureCollection' } })) };
    await window.pwaLayerSyncStatus.refresh();

    expect(rowDisabled('weather')).toBe(false);
  });

  it('never removes a marker it does not own', async () => {
    // Online and cached from the start: this module never disabled the row,
    // so its re-enable branch must not touch map.js's marker at all.
    disableForOutOfWindow();
    setOnline(true);

    await window.pwaLayerSyncStatus.refresh();

    expect(weatherRow().getAttribute(WEATHER_MARKER)).toBe('1');
    expect(rowDisabled('weather')).toBe(true);
  });
});
