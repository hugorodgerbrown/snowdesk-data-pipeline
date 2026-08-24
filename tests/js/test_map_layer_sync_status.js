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
 * SNOW-658: the `favourites` and `community_reports` rows this file used to
 * cover are gone from OVERLAY_RESOURCES — both overlays moved into the panel
 * their own roundel opens, so there is no row here to hang a dot on (see
 * tests/js/test_favourites_panel.js and tests/js/test_report_panel.js). The
 * same ticket merged the Austria and Italy country rows into one ALBINA
 * provider row, which is why `countryRow` now takes a `codes` argument: the
 * row carries its own `data-country-codes`, and a merged row's dot may only
 * go green once EVERY code it switches is cached.
 *
 * SNOW-645: the `downloaded` (pinned-tiles) row this file used to cover
 * (SNOW-586's multi-bucket union describe block, plus the `includeDownloaded`
 * fixture flag, the `pinnedBuckets`/`keys()`/`open()` CacheStorage fake
 * scaffolding, and the `hideWindowMap()` helper it alone needed) is gone —
 * the row was removed from ROWS entirely (map_layer_sync_status.js's own
 * comment there explains why: a per-active-template probe can't answer a
 * togglable-row question once downloads span every basemap at once). The
 * overlay it reported on lives in the "Manage downloads" sheet now — see
 * map_downloads_manager.js and window.pwaDownloadedOverlay in map.js, and
 * tests/js/test_map_downloaded_overlay_colour.js for that overlay's own
 * coverage.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
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
  // SNOW-524: country rows are opt-in so the pre-existing tier tests keep
  // exercising the country-blind fallback (no country enabled ⟹ ignoreSearch).
  countries = null,
  // SNOW-573: opt-in for the same reason `countries` is above — the
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

  // SNOW-524: one row per PROVIDER, `checked` mirroring the picker's
  // aria-checked (which is what _enabledCountryCodes reads).
  //
  // SNOW-658: `codes` is the row's own `data-country-codes`, the DOM
  // projection of COUNTRY_GROUPS in static/js/map_state.js. It defaults to
  // the key's suffix, so every pre-existing single-country fixture below is
  // unchanged; the ALBINA row passes ['at', 'it'] explicitly.
  const countryRow = ({ code, checked, codes }) => `
    <li role="none">
      <button type="button" class="basemap-menu-item basemap-menu-item--overlay"
              data-overlay-key="country.${code}"
              data-country-codes="${(codes || [code]).join(' ')}"
              aria-checked="${checked ? 'true' : 'false'}">
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

function basemapDotLabel(key) {
  const dot = document.querySelector(`[data-basemap-key="${key}"] .sync-dot`);
  return dot ? dot.getAttribute('aria-label') : undefined;
}

/**
 * SNOW-722: a fake `window.pwaBasemapDownloads` exposing the one method
 * this module uses. `areas()` is the real thing's normalised record list —
 * only `basemapKey` matters here, and it is `null` on a record written
 * before SNOW-645 (or on a reconciled orphan), meaning "downloaded,
 * basemap unknown".
 *
 * Absent from every test that doesn't call this, which is deliberate: with
 * no downloads module there is no coverage to read, and the module falls
 * back to the pre-SNOW-722 style-only signal — which is what the
 * `per-basemap rows` block above still exercises.
 *
 * @param {Array<Object>} areas
 * @param {{rejects?: boolean}} [options]
 */
function fakeDownloads(areas, { rejects = false } = {}) {
  const downloads = {
    areas: vi.fn(async () => {
      if (rejects) throw new Error('areas() failed');
      return areas;
    }),
  };
  window.pwaBasemapDownloads = downloads;
  return downloads;
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
  delete window.pwaBasemapDownloads;
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

  it('greens a MERGED provider row only when BOTH countries are cached', async () => {
    // SNOW-658: ALBINA publishes for Austria and Italy, so its single row
    // switches both. A green dot with only Austria cached would promise an
    // offline map that comes up missing Italy — the class of lie these dots
    // exist to stop.
    const albina = [{ code: 'albina', checked: true, codes: ['at', 'it'] }];

    buildFixture({ countries: albina });
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('at') }));
    await window.pwaLayerSyncStatus.refresh();
    expect(dotState('country.albina')).toBe('uncached');

    buildFixture({ countries: albina });
    vi.stubGlobal(
      'caches',
      fakeCaches({ hitQueries: [...countryFeeds('at'), ...countryFeeds('it')] }),
    );
    await window.pwaLayerSyncStatus.refresh();
    expect(dotState('country.albina')).toBe('cached');
  });

  it('scopes the tier dots to EVERY code a checked provider row switches', async () => {
    // One checked ALBINA row means two enabled countries, so a tier is only
    // honestly available offline when it is cached for both.
    buildFixture({ countries: [{ code: 'albina', checked: true, codes: ['at', 'it'] }] });
    vi.stubGlobal('caches', fakeCaches({ hitQueries: countryFeeds('at') }));

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('l1')).toBe('uncached');
    expect(dotState('l4')).toBe('uncached');
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

describe('IndexedDB overlay rows (weather)', () => {
  it('resolves cached when window.pwaDb holds a row with .geojson, uncached when absent', async () => {
    buildFixture({ includeWeather: true });
    vi.stubGlobal('caches', fakeCaches());
    window.pwaDb = {
      get: vi.fn(async (_store, key) => {
        if (key === 'weather') return { key: 'weather', geojson: { type: 'FeatureCollection' } };
        return undefined;
      }),
    };

    await window.pwaLayerSyncStatus.refresh();

    expect(dotState('weather')).toBe('cached');
    expect(window.pwaDb.get).toHaveBeenCalledWith('data:map_overlays', 'weather');
  });

  it('resolves uncached, not throw, when window.pwaDb is unavailable', async () => {
    buildFixture({ includeWeather: true });
    vi.stubGlobal('caches', fakeCaches());

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    expect(dotState('weather')).toBe('uncached');
  });

  it('skips rows absent from the DOM (conditionally rendered)', async () => {
    // The weather row is flag-gated, so this is the live case. SNOW-658 also
    // removed the favourites and community_reports rows outright — map.js
    // still calls markCached('favourites') on its lazy-load path, so a
    // missing row has to stay a no-op rather than a throw.
    buildFixture();
    vi.stubGlobal('caches', fakeCaches());
    window.pwaDb = { get: vi.fn(async () => ({ geojson: {} })) };

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    expect(document.querySelector('[data-overlay-key="weather"]')).toBeNull();
    expect(document.querySelector('[data-overlay-key="favourites"]')).toBeNull();
    expect(document.querySelector('[data-overlay-key="community_reports"]')).toBeNull();
    expect(() => window.pwaLayerSyncStatus.markCached('favourites')).not.toThrow();
  });
});

// SNOW-722: a basemap dot's green means real downloaded area coverage; a
// style cached by merely browsing means the grey "partly cached" advisory.
// Both answers hold ONLINE as well as offline — the journey this ticket
// exists for is the pre-flight check (open the layers menu at home on wifi,
// then get on a plane), so a dot that only turns honest once the user is
// offline lies at the one moment they could still act on it by downloading
// the area. Only the red state and the row-disabling are offline-only.
//
// Every test here stubs `window.pwaBasemapDownloads`, because that is the
// primary path. The style-only fallback, for when coverage is unknowable,
// is the small block after this one.
describe('per-basemap rows', () => {
  it('online: coverage → green; a merely-browsed basemap → grey "partly cached"', async () => {
    // The regression this ticket is about, and it bites ONLINE: Swisstopo's
    // style is cached because the user opened the picker and looked at it,
    // which used to be the whole green signal.
    fakeDownloads([{ id: 'area-1', basemapKey: 'standard' }]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [STANDARD_STYLE, SWISSTOPO_STYLE] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('standard')).toBe('cached');
    expect(basemapDotState('swisstopo')).toBe('uncached');
    expect(basemapDotLabel('swisstopo')).toBe('Partly cached — may not load everywhere');
    // Online nothing is ever disabled — every basemap is one fetch away.
    expect(basemapRowDisabled('standard')).toBe(false);
    expect(basemapRowDisabled('swisstopo')).toBe(false);
  });

  it('online: neither → the unchanged grey "view online first"; never red', async () => {
    fakeDownloads([]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('standard')).toBe('uncached');
    expect(basemapDotState('swisstopo')).toBe('uncached');
    expect(basemapDotLabel('swisstopo')).toBe('Not cached — view online first');
    expect(basemapRowDisabled('swisstopo')).toBe(false);
  });

  it('offline: the active basemap reads green once it HAS downloaded coverage', async () => {
    setOnline(false);
    fakeDownloads([{ id: 'area-1', basemapKey: 'standard' }]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [STANDARD_STYLE] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('standard')).toBe('cached');
    expect(basemapRowDisabled('standard')).toBe(false);
  });

  it('offline: a downloaded non-active basemap stays selectable', async () => {
    setOnline(false);
    fakeDownloads([{ id: 'area-1', basemapKey: 'swisstopo' }]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [SWISSTOPO_STYLE] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('swisstopo')).toBe('cached');
    expect(basemapDotLabel('swisstopo')).toBe('Available offline');
    expect(basemapRowDisabled('swisstopo')).toBe(false);
  });

  it('offline: style cached but nothing downloaded → grey advisory, still selectable', async () => {
    setOnline(false);
    fakeDownloads([]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [SWISSTOPO_STYLE] }));

    await window.pwaLayerSyncStatus.refresh();

    // The point of the middle state: a basemap that renders in some places
    // must stay choosable. Disabling it would take away a map that works.
    expect(basemapDotState('swisstopo')).toBe('uncached');
    expect(basemapRowDisabled('swisstopo')).toBe(false);
    expect(basemapDotLabel('swisstopo')).toBe('Partly cached — may not load everywhere');
  });

  // SNOW-722 (a3a5e3f): the dot and the row answer two different questions.
  // Keeping the active basemap's row usable used to require claiming its
  // style was cached. These two pin the split in both directions.
  it('offline + uncached ACTIVE basemap: red dot, row still enabled', async () => {
    setOnline(false);
    fakeDownloads([]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('standard')).toBe('unavailable-offline');
    expect(basemapRowDisabled('standard')).toBe(false);
  });

  it('offline + uncached INACTIVE basemap: red dot and a disabled row', async () => {
    setOnline(false);
    fakeDownloads([]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('swisstopo')).toBe('unavailable-offline');
    expect(basemapRowDisabled('swisstopo')).toBe(true);
  });

  it('a legacy keyless area counts for the ACTIVE basemap only', async () => {
    setOnline(false);
    // basemapKey null = "downloaded, basemap unknown" (a pre-SNOW-645
    // record, or a reconciled orphan). Attributing it to every basemap
    // would restore the very over-claim this ticket removes; attributing it
    // to none would regress the basemap the user is actually on.
    fakeDownloads([{ id: 'area-1', basemapKey: null }]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [STANDARD_STYLE, SWISSTOPO_STYLE] }));

    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('standard')).toBe('cached');
    // Swisstopo's style is cached too, so it lands in the middle state —
    // NOT green off someone else's keyless download.
    expect(basemapDotState('swisstopo')).toBe('uncached');
    expect(basemapRowDisabled('swisstopo')).toBe(false);
  });

  it('reads areas() once per pass, not once per basemap row', async () => {
    setOnline(false);
    const downloads = fakeDownloads([]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [] }));

    await window.pwaLayerSyncStatus.refresh();

    // Two basemap rows in the fixture; an IndexedDB read plus a possible
    // Cache Storage walk is not something to repeat per row.
    expect(downloads.areas).toHaveBeenCalledTimes(1);
  });

  it('coming back online re-enables a basemap row this module disabled', async () => {
    setOnline(false);
    fakeDownloads([]);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [] }));
    await window.pwaLayerSyncStatus.refresh();
    expect(basemapRowDisabled('swisstopo')).toBe(true);

    setOnline(true);
    await window.pwaLayerSyncStatus.refresh();

    expect(basemapDotState('swisstopo')).toBe('uncached');
    expect(basemapRowDisabled('swisstopo')).toBe(false);
    expect(
      document.querySelector('[data-basemap-key="swisstopo"]').hasAttribute('aria-disabled'),
    ).toBe(false);
  });
});

// SNOW-722: with no readable downloads module there is no coverage to ask
// about, so a basemap row degrades to the style-only signal this module
// used before the ticket — the alternative, reporting everything red,
// would break a page that simply loads this module without the map bundle.
// Deliberately a small, named block: the primary path above is the default.
describe('per-basemap rows (no downloads module — style-only fallback)', () => {
  it('offline: a cached style alone still reads green', async () => {
    setOnline(false);
    expect(window.pwaBasemapDownloads).toBeUndefined();
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [SWISSTOPO_STYLE] }));

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    expect(basemapDotState('swisstopo')).toBe('cached');
    expect(basemapRowDisabled('swisstopo')).toBe(false);
  });

  it('offline: a basemap whose style is not cached is red + disabled', async () => {
    setOnline(false);
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [] }));

    await window.pwaLayerSyncStatus.refresh();

    // Standard is the ACTIVE basemap (aria-checked="true"), so its row stays
    // selectable — you can't be stranded on a map you can't leave — but its
    // dot reports the truth: nothing is cached.
    expect(basemapDotState('standard')).toBe('unavailable-offline');
    expect(basemapRowDisabled('standard')).toBe(false);
    expect(basemapDotState('swisstopo')).toBe('unavailable-offline');
    expect(basemapRowDisabled('swisstopo')).toBe(true);
  });

  it('degrades the same way, without throwing, when areas() rejects', async () => {
    setOnline(false);
    fakeDownloads([], { rejects: true });
    vi.stubGlobal('caches', fakeCaches({ hitUrls: [SWISSTOPO_STYLE] }));

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

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
    expect(basemapDotState('swisstopo')).toBe('uncached');
    // SNOW-722: a probe that threw tells us nothing was found, so the active
    // basemap's dot reads like any other uncached one. Its ROW stays
    // selectable regardless of the probe outcome.
    expect(basemapDotState('standard')).toBe('uncached');
    expect(basemapRowDisabled('standard')).toBe(false);
  });
});

describe('Cache Storage unsupported', () => {
  it('leaves every dot at "unknown" and resolves without probing', async () => {
    expect('caches' in window).toBe(false);

    await expect(window.pwaLayerSyncStatus.refresh()).resolves.toBeUndefined();

    for (const key of ['l1', 'l2', 'l4', 'resorts']) {
      expect(dotState(key)).toBe('unknown');
    }
    expect(basemapDotState('standard')).toBe('unknown');
  });
});

describe('markCached (optimistic live update)', () => {
  it('flips a cacheable row to "cached" with no probe', () => {
    expect('caches' in window).toBe(false);

    window.pwaLayerSyncStatus.markCached('l1');
    window.pwaLayerSyncStatus.markCached('resorts');

    expect(dotState('l1')).toBe('cached');
    expect(dotState('resorts')).toBe('cached');
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
