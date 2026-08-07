/*
 * static/js/map.js — MapLibre client for the /map/ page.
 *
 * Extracted from DO_NOT_ADD/snowdesk_map_preview.html so the same script
 * can later be embedded on the homepage. Endpoint URLs are read from
 * data-* attributes on the #map element — Django renders them through
 * {% url %}, keeping route names as the single source of truth.
 *
 * Data flow at load time:
 *   1. Read endpoint URLs from the #map element's data-* attributes.
 *   2. Fetch regions GeoJSON, today's ratings, and resorts in parallel.
 *   3. Merge the three into per-feature rating state so the fill layer
 *      can colour each region via a MapLibre ``match`` expression.
 *   4. Wire up click interactions (region select/deselect, markers, pins).
 */

// Module-scope handles shared between this file's IIFEs (main init,
// season scrubber, timelapse). Populated by the main IIFE; sibling
// IIFEs read MAP / FEATURE_BY_REGION_ID once the user triggers them.
let MAP = null;
const FEATURE_BY_ID = {};
const FEATURE_BY_REGION_ID = {};

// SNOW-236: Country visibility state — which countries are currently shown.
// Populated by the main IIFE from localStorage + the country-toggle
// buttons; read by the scrubber IIFE for country-aware effective-last
// computation (deriveEffectiveTodayKey).
const COUNTRY_STATE = { ch: true, fr: false, at: false, it: false };

// SNOW-236: The clamped boot date (min(today, seasonEnd)) computed by the
// main IIFE and shared with the scrubber IIFE. The scrubber uses this as
// the baseline when deciding whether to snap the thumb after getSeasonRatings
// resolves — the initial paint was at bootDateKey, not necessarily at todayKey.
let BOOT_DATE_KEY = null;

// Whether a single click on a region auto-pans/zooms to fit it into view.
// Off by default; persisted in localStorage under
// 'snowdesk.map.autozoom'. The autozoomToggleInit IIFE at the bottom of
// this file owns the button wiring; selectFeature reads this flag.
let AUTOZOOM = false;

// SNOW-615: the localStorage keys the map persists its chrome state under,
// at module scope so each has exactly one owner.
//
// `OVERLAY_STORAGE_KEY` was declared three times — once in the main IIFE,
// once in `basemapPickerInit`, and (as a bare literal) in the autozoom
// toggle — with the picker's copy silently dropping the two explanatory
// comments the main one carries. Three literal copies of a key that the
// reader and the writer must agree on is drift waiting to happen: a typo
// in one is not a crash, it is a setting that no longer persists.
const OVERLAY_STORAGE_KEY = {
  l1: 'snowdesk.map.overlay.l1',
  l2: 'snowdesk.map.overlay.l2',
  l4: 'snowdesk.map.overlay.l4',
  resorts: 'snowdesk.map.overlay.resorts',
  // SNOW-414: eligible-only — the toggle only exists in the DOM (and this
  // key is only ever read/written) when data-favourites-eligible="true".
  favourites: 'snowdesk.map.overlay.favourites',
  // SNOW-419: flag-gated only — the toggle exists in the DOM (and this key
  // is only ever read/written) when data-community-reports-eligible="true".
  community_reports: 'snowdesk.map.overlay.community_reports',
  // SNOW-570: which areas are held in the pinned basemap cache.
  downloaded: 'snowdesk.map.overlay.downloaded',
};

// No ``l3`` entry above: the bulletin-boundary layer has no toggle and no
// persisted state of its own — see OVERLAY_VISIBILITY_GOVERNOR.

const BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
const AUTOZOOM_STORAGE_KEY = 'snowdesk.map.autozoom';

// SNOW-620: the strings this file writes into the DOM itself, server-
// translated into the template _map_embed.html renders and read back here.
// makemessages does not scan JavaScript, so a literal written below would
// ship as English to every locale. The literals here are the English
// fallback — see static/js/i18n_strings.js.
//
// Module scope rather than per-IIFE: the popup and the timelapse transport
// are separate IIFEs, and both need these.
const MAP_STRINGS = self.pwaStrings.read('map-strings-template', {
  'bulletin-link': 'Open bulletin for %(date)s →',
  'no-bulletin': 'No bulletin available for %(date)s',
  'season-unavailable': 'Season data unavailable',
  'timelapse-play': 'Play season timelapse',
  'timelapse-play-reverse': 'Play season timelapse in reverse',
  'timelapse-stop': 'Stop season timelapse',
  'timelapse-stop-reverse': 'Stop reverse timelapse',
  // SNOW-632: the custom-area framing overlay's CTA readout and top
  // banner. 'frame-up-to' and 'frame-over-ceiling' replace two literals
  // that used to be assembled in JS (bin/i18n-lint does not catch a
  // literal assigned to a variable before being rendered, which is a gap
  // in that check, not a licence — see mapCustomDownloadControlInit's
  // _updateReadout). 'frame-readout-busy' deliberately has no literal '%'
  // in the msgid — the caller appends it to the interpolated `pct` value
  // itself, so there is nothing here for a translation to get wrong.
  'frame-up-to': 'Up to %(mb)s MB',
  'frame-over-ceiling': 'Area too large to download (over %(mb)s MB)',
  'frame-readout-busy': '%(pct)s · %(mb)s',
  'frame-readout-done': '%(mb)s downloaded',
  'frame-budget-banner': '%(used)s / %(budget)s downloaded',
  'action-close': 'Close',
  // SNOW-634: the custom-area roundel's own two labels — it now opens the
  // downloads sheet rather than framing directly (see
  // mapCustomDownloadControlInit's `_renderControl`), so its copy is about
  // what is on THIS DEVICE generally, not this one area.
  'custom-control-idle': 'Manage offline downloads',
  'custom-control-done': 'Offline downloads available',
  // SNOW-635: an unrenamed custom area's default display name, filled in
  // by basemapDownloadedAreas() itself — see that function's own comment.
  'default-custom-name': 'Custom area %(n)s',
});

// basemap.at ships an ESRI ArcGIS VectorTileServer style whose vector source
// uses a relative ``tile/{z}/{y}/{x}.pbf`` path that MapLibre cannot resolve
// (it throws "Failed to construct 'Request': Failed to parse URL from tile/…"),
// so nothing paints. Such styles must be fetched and their sources rewritten to
// absolute tile URLs before ``setStyle`` sees them. IGN and swisstopo publish
// MapLibre-native styles that load straight from their URL, so this only kicks
// in for the keys listed here.
const ESRI_BASEMAP_KEYS = new Set(['basemap_at']);

// Resolve a basemap (key, url) to a value ``setStyle``/the Map constructor
// accepts. Native basemaps pass through as the URL string. ESRI basemaps are
// fetched and each vector source's relative ``url`` is swapped for an absolute
// ``tiles`` template following the ArcGIS VectorTileServer convention
// (``<service>/tile/{z}/{y}/{x}.pbf``). Returns a Promise in the ESRI case.
async function resolveBasemapStyle(key, url) {
  if (!ESRI_BASEMAP_KEYS.has(key)) return url;
  const style = await (await fetch(url)).json();
  for (const src of Object.values(style.sources || {})) {
    if (src && src.type === 'vector' && src.url && !src.tiles) {
      src.tiles = [src.url.replace(/\/?$/, '/') + 'tile/{z}/{y}/{x}.pbf'];
      delete src.url;
    }
  }
  return style;
}

// SNOW-483: build the inline fallback style swapped in when the native
// basemap style JSON can't be fetched (offline — the SW deliberately treats
// the third-party style URL as network-only). An empty ``sources``/``layers``
// pair plus a single background layer is enough to make MapLibre fire
// ``load`` so the existing overlay-install path runs against the SW-cached
// regions GeoJSON + ratings — a plain coloured map beats a blank one.
// ``background-color`` is read from the ``--color-bg`` design token at
// runtime because MapLibre paint properties can't reference CSS ``@theme``
// tokens directly (see the favourites ``icon-color`` comment above); the
// literal hex fallback matters because the e2e environment doesn't compile
// CSS, so the token can resolve to an empty string there.
//
// ``glyphs`` must be declared even though nothing will resolve it offline:
// the overlay install path adds symbol layers with a ``text-field`` (region
// labels), and MapLibre's style validator rejects any ``text-field`` layer
// on a style with no ``glyphs`` template — the layer is silently dropped and
// every subsequent read of it (e.g. ``getFilter``) throws. A same-origin
// placeholder satisfies the validator; the label glyphs then simply 404
// while degraded, the same harmless failure mode SNOW-478 already accepts
// for a basemap whose glyph server doesn't serve a requested font.
function buildFallbackStyle() {
  const bg = getComputedStyle(document.documentElement)
    .getPropertyValue('--color-bg')
    .trim() || '#f2f0ec';
  return {
    version: 8,
    name: 'snowdesk-offline-fallback',
    glyphs: `${window.location.origin}/static/fonts/{fontstack}/{range}.pbf`,
    sources: {},
    layers: [
      { id: 'snowdesk-offline-fallback-bg', type: 'background', paint: { 'background-color': bg } },
    ],
  };
}

// SNOW-521: resolve the active basemap's vector-tile URL template — the
// same lookup `computeBasemapTileURLs` used to do before per-region
// download replaced viewport tile enumeration. Reads the *resolved*
// tile URL template off the first vector source's runtime instance
// (`map.getSource(id).tiles`) rather than the static style JSON — a
// TileJSON-backed source only populates `tiles` once its tilejson fetch
// resolves. Returns null for a style with no vector sources (the offline
// fallback style, SNOW-483) or before the style has finished loading.
// `mapDownloadControlInit` substitutes this template into a region's stored
// tile-index ranges (`pwaBasemapDownloadCore.rangesToTileURLs`) rather
// than enumerating anything itself.
function activeBasemapTileTemplate(map) {
  if (!map || !map.isStyleLoaded()) return null;
  const style = map.getStyle();
  if (!style || !style.sources) return null;
  for (const sourceId of Object.keys(style.sources)) {
    if (style.sources[sourceId].type !== 'vector') continue;
    const runtime = map.getSource(sourceId);
    const template = runtime && Array.isArray(runtime.tiles) && runtime.tiles[0];
    if (template) return template;
  }
  return null;
}

// SNOW-492: sprite JSON/PNG URLs (1x and 2x) for `map`'s current style, if
// any. MapLibre's `sprite` style property is either a single base URL
// string or (multi-sprite styles) an array of `{id, url}` entries; both
// shapes are handled. Returns [] for a style with no sprite (the offline
// fallback style). Deliberately does not attempt to warm glyph PBFs —
// MapLibre only requests the specific unicode ranges the current labels
// use, and by the time a user reaches for "Cache this area" those ranges
// have almost always already been fetched (and cached, via the existing
// basemap stale-while-revalidate strategy) as a side effect of ordinary
// browsing; enumerating them ourselves would mean re-deriving MapLibre's
// own glyph-range logic for marginal benefit. Documented as a known gap
// in docs/offline-map.md rather than reverse-engineered here.
function computeBasemapSpriteURLs(map) {
  if (!map) return [];
  const style = map.getStyle && map.getStyle();
  const sprite = style && style.sprite;
  if (!sprite) return [];
  const bases = Array.isArray(sprite) ? sprite.map((s) => s.url) : [sprite];
  const urls = [];
  for (const base of bases) {
    if (typeof base !== 'string') continue;
    urls.push(`${base}.json`, `${base}.png`, `${base}@2x.json`, `${base}@2x.png`);
  }
  return urls;
}

// SNOW-521: same-origin data-feed + active-basemap-style URL list —
// everything a basemap download warms besides its own tile ranges.
// Mirrors SNOW-492/493's assembly (see the removed cacheNowInit for the
// full exclusion rationale re: favourites/community-reports) minus tile
// enumeration, which comes from the caller's own blob instead.
//
// SNOW-522: lifted out of mapDownloadControlInit's closure (where it
// started as a private helper) to module scope so the new
// mapCustomDownloadControlInit can share this one copy rather than
// duplicating it — everything it touches (COUNTRY_STATE, RATINGS_URL,
// computeBasemapSpriteURLs) is already module-scope.
//
// @returns {string[]}
function assembleBasemapDownloadFeedURLs() {
  const mapEl = document.getElementById('map');
  const urls = [];
  const enabledCountries = Object.keys(COUNTRY_STATE).filter((code) => COUNTRY_STATE[code]);
  const addCountryFeeds = (base) => {
    if (!base) return;
    for (const code of enabledCountries) {
      urls.push(base + '?country=' + code);
    }
  };
  addCountryFeeds(mapEl.dataset.regionsUrl);
  addCountryFeeds(mapEl.dataset.majorRegionsUrl);
  addCountryFeeds(mapEl.dataset.subRegionsUrl);
  if (mapEl.dataset.resortsGeojsonUrl) urls.push(mapEl.dataset.resortsGeojsonUrl);
  if (RATINGS_URL) {
    for (const code of enabledCountries) {
      urls.push(RATINGS_URL + '?country=' + code);
    }
  }
  const activeBasemap = document.querySelector(
    '#basemap-menu .basemap-menu-item[data-basemap-url][aria-checked="true"]',
  );
  if (activeBasemap) urls.push(activeBasemap.dataset.basemapUrl);
  urls.push(...computeBasemapSpriteURLs(MAP));
  return urls;
}

// SNOW-586: the Cache Storage name prefix every per-area pinned basemap
// bucket shares. FOUR literals hold this value, one per script-loading
// context: this one, static/js/sw.js's BASEMAP_PINNED_CACHE_PREFIX,
// basemap_download_core.js's PINNED_CACHE_PREFIX, and
// map_layer_sync_status.js's PINNED_BASEMAP_CACHE_PREFIX.
//
// SNOW-615: this comment used to say three literals "kept honest against
// each other by tests/js/test_basemap_download_core.js's round-trip
// assertion". That test asserts only that basemap_download_core's
// pinnedCacheName() returns its OWN prefix plus the area id — it cannot
// see this file, sw.js or map_layer_sync_status.js, so it holds nothing
// honest against anything. Cross-file agreement here is a review
// discipline, not an enforced mechanism (the same convention
// basemap_tiles.py's shared golden vector documents for the Python↔JS
// tile math): changing one copy means checking the other three.
//
// Module scope because it used to be copied verbatim into both download
// controls' own closures — see pinnedBasemapCacheURLs's own comment for
// why that duplication is gone.
const BASEMAP_PINNED_CACHE_PREFIX = 'snowdesk-basemap-pinned-';

/**
 * Every URL held across EVERY pinned basemap bucket, as one Set.
 *
 * SNOW-586 replaced the single shared pinned cache with one bucket per
 * downloaded area (`snowdesk-basemap-pinned-<areaId>`), so "is this tile
 * cached?" now means unioning across all of them. This one module-scope
 * reader replaces three near-identical copies of a single-cache lookup
 * (the downloaded-areas overlay, the region control, the custom-area
 * control) that each assumed exactly one pinned cache existed — three
 * copies of the OLD one-liner was defensible repetition; three copies of
 * a union-across-buckets read is drift waiting to happen, so it is lifted
 * here the same way `assembleBasemapDownloadFeedURLs` above was.
 *
 * Never throws. Cache Storage being unavailable, or one bucket failing to
 * enumerate (a concurrent eviction racing this read), both read as "no
 * more URLs from that bucket" rather than aborting the whole union — a
 * caller asking "is X downloaded?" mid-eviction should see the state as
 * it settles, not blow up over the race.
 *
 * @returns {Promise<Set<string>>}
 */
async function pinnedBasemapCacheURLs() {
  const urls = new Set();
  if (!('caches' in window)) return urls;
  try {
    const names = await caches.keys();
    const pinnedNames = names.filter((name) => name.startsWith(BASEMAP_PINNED_CACHE_PREFIX));
    await Promise.all(
      pinnedNames.map(async (name) => {
        try {
          const cache = await caches.open(name);
          const requests = await cache.keys();
          for (const request of requests) urls.add(request.url);
        } catch (_e) {
          // One bucket failing to enumerate must not lose the others.
        }
      }),
    );
  } catch (_e) {
    // Cache Storage unavailable — empty Set, as before this ticket.
  }
  return urls;
}

/**
 * Every area id with a pinned bucket present in Cache Storage (SNOW-612).
 *
 * The bucket is the ground truth for what is actually stored; the
 * `basemap.regions` / `basemap.customAreas` records are only what COMPLETED
 * runs left behind. A download that failed partway leaves the former
 * without the latter, which is exactly the stranded quota this reader
 * exists to surface — see `basemapDownloadedAreas` below.
 *
 * Never throws: Cache Storage being unavailable reads as "no buckets",
 * which degrades to the pre-SNOW-612 behaviour of trusting the records
 * alone rather than blocking anything.
 *
 * @returns {Promise<string[]>}
 */
async function pinnedBucketAreaIds() {
  if (!('caches' in window)) return [];
  try {
    const names = await caches.keys();
    return names
      .filter((name) => name.startsWith(BASEMAP_PINNED_CACHE_PREFIX))
      .map((name) => name.slice(BASEMAP_PINNED_CACHE_PREFIX.length))
      .filter(Boolean);
  } catch (_e) {
    return [];
  }
}

// SNOW-612: measured sizes for orphaned buckets, keyed by area id. Held
// for the page's lifetime only — a reload re-measures, which is cheap
// enough given an orphan is by definition a rare leftover, and avoids a
// persisted record that would itself need invalidating when the bucket is
// finally deleted.
//
// Only two things can change a bucket's size — a download run writing into
// it, and an eviction deleting it — and both call
// `forgetPinnedBucketMeasurement` below. Without that, a run that failed
// twice in one page session would report the first attempt's size for the
// bucket the second attempt had since grown.
const ORPHAN_BUCKET_BYTES = new Map();

/**
 * Drop the cached measurement for `areaId` (SNOW-612).
 *
 * @param {string} areaId
 * @returns {void}
 */
function forgetPinnedBucketMeasurement(areaId) {
  ORPHAN_BUCKET_BYTES.delete(areaId);
}

/**
 * Measure one pinned bucket by summing its entries' `Content-Length`
 * (SNOW-612).
 *
 * Only ever called for an ORPHANED bucket — one with no stored record to
 * read a byte total off. Every other area's size comes from the figure its
 * completed run recorded (SNOW-632: the run's own reported total, not a
 * re-measurement — see `_recordRegionDownload` and
 * `mapCustomDownloadControlInit`'s `finish`), because an area is thousands
 * of entries and measuring them all on every render is precisely what
 * `basemap_manage_core.js`'s header rules out.
 *
 * `Content-Length` rather than the body: `cache.match()` hands back a
 * Response without reading it, so a header sum is N cheap lookups where a
 * `blob()` sum would be N decompressions. An entry with no such header
 * contributes nothing — under-reporting a stranded bucket is better than
 * paying to decode it, and the row is deletable either way. In production
 * this is not a rare edge case for a tile entry specifically: the browser
 * always sends `Accept-Encoding: gzip`, so a live tile response carries NO
 * `Content-Length` at all (curl against the origin confirms it — the
 * header only appears with compression explicitly disabled), and this
 * function has no blob fallback the way `responseBytes`
 * (`basemap_cache_core.js`) does. An orphaned bucket therefore reads ~0
 * bytes here even when it holds real tiles — acceptable for what this is
 * used for (a deletable orphan still needs deleting at 0 MB as much as at
 * its true size), but not a general-purpose measurement. Not fixed here;
 * see this ticket's decision doc for why.
 *
 * @param {string} areaId
 * @returns {Promise<number>} Bytes, or 0 if the bucket cannot be read.
 */
async function measurePinnedBucketBytes(areaId) {
  if (ORPHAN_BUCKET_BYTES.has(areaId)) return ORPHAN_BUCKET_BYTES.get(areaId);
  let total = 0;
  try {
    const cache = await caches.open(BASEMAP_PINNED_CACHE_PREFIX + areaId);
    const requests = await cache.keys();
    for (const request of requests) {
      const response = await cache.match(request);
      const length = response && Number(response.headers.get('Content-Length'));
      if (Number.isFinite(length) && length > 0) total += length;
    }
  } catch (_e) {
    // A bucket that cannot be read is still worth listing at 0 bytes —
    // the user can delete it, which is the point.
  }
  ORPHAN_BUCKET_BYTES.set(areaId, total);
  return total;
}

// SNOW-586: reads-through to meta:app's `basemap.budgetMb` device-local
// override, falling back to pwaBasemapDownloadCore.DOWNLOAD_BUDGET_MB
// (500) when no row is present — nothing writes that row yet; SNOW-588's
// managed-downloads UI is what will ever change it, this ticket only
// reads it. Best-effort throughout: a failed read is the default budget,
// never a thrown error blocking a download.
//
// @returns {Promise<number>} The budget in BYTES (planEviction's unit).
async function basemapDownloadBudgetBytes() {
  const core = self.pwaBasemapDownloadCore;
  let mb = core ? core.DOWNLOAD_BUDGET_MB : 500;
  try {
    const row = await window.pwaDb?.get('meta:app', 'basemap.budgetMb');
    const value = row && row.value;
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) mb = value;
  } catch (_e) {
    // Best-effort — the default budget.
  }
  return mb * 1024 * 1024;
}

// SNOW-635: the array-shaped record replacing the old single-row
// `basemap.customArea` — see `_readCustomAreas`'s docstring for the lazy
// migration between the two. Top-level constants because `basemapDownloadedAreas`,
// `evictBasemapAreas` and `mapCustomDownloadControlInit`'s IIFE (all of
// which read or write this record) need to agree on the same key.
const CUSTOM_AREAS_KEY = 'basemap.customAreas';
const LEGACY_CUSTOM_AREA_KEY = 'basemap.customArea';

/**
 * SNOW-635: read `basemap.customAreas`, migrating the legacy single-row
 * `basemap.customArea` into it on first read if the new key is absent.
 *
 * Lazy rather than a one-off migration command, because this runs inside
 * `basemapDownloadedAreas()` — the boot-path probe the roundel calls on
 * every page load (post-SNOW-634) — so every existing device reaches it
 * without a separate step. Best-effort throughout, and this MUST degrade
 * to "read the legacy row as a one-entry list" rather than throw: a device
 * that cannot write here would otherwise take both the roundel and the
 * manage sheet down with it, since both sit on this same boot-path read.
 *
 * The legacy area keeps id `CUSTOM_AREA_ID` ('custom') and ordinal `1` —
 * its existing `snowdesk-basemap-pinned-custom` Cache Storage bucket has
 * no rename, so the id has to survive unchanged for that bucket to keep
 * resolving (docs/decisions/per-area-pinned-basemap-caches.md).
 *
 * @returns {Promise<Array<Object>>} `[]` when nothing is stored and there
 *   is no legacy row to migrate.
 */
async function _readCustomAreas() {
  if (!window.pwaDb) return [];
  let row;
  try {
    row = await window.pwaDb.get('meta:app', CUSTOM_AREAS_KEY);
  } catch (_e) {
    row = undefined;
  }
  // An empty array is a legitimate "already migrated, nothing left" —
  // return it as-is rather than falling through to the legacy read, or a
  // device that deleted its last custom area would have it re-created
  // from a legacy row that (by then) no longer exists anyway.
  if (Array.isArray(row && row.value)) return row.value;

  let legacyRow;
  try {
    legacyRow = await window.pwaDb.get('meta:app', LEGACY_CUSTOM_AREA_KEY);
  } catch (_e) {
    legacyRow = undefined;
  }
  const legacy = legacyRow && legacyRow.value;
  if (!legacy || !Array.isArray(legacy.bbox)) return [];

  const core = self.pwaBasemapDownloadCore;
  const migrated = [{ ...legacy, id: core ? core.CUSTOM_AREA_ID : 'custom', ordinal: 1 }];
  try {
    await window.pwaDb.put('meta:app', { key: CUSTOM_AREAS_KEY, value: migrated });
    await window.pwaDb.delete('meta:app', LEGACY_CUSTOM_AREA_KEY);
  } catch (_e) {
    // Best-effort — see docstring above. The legacy row is untouched, so
    // the next read tries the migration again; this call still returns the
    // migrated shape for ITS OWN caller even though the write didn't land.
  }
  return migrated;
}

/**
 * SNOW-635: persist `areas` to `basemap.customAreas`. Best-effort — see
 * `_readCustomAreas`'s docstring for why this must never throw.
 *
 * Always a `put`, even for an empty array — never a `delete` — so removing
 * the last custom area leaves the key present with value `[]`, not absent.
 * An absent key is exactly what `_readCustomAreas` treats as "try the
 * legacy migration", and that legacy row is long gone by the time a device
 * has ever HAD a custom area to delete.
 *
 * @param {Array<Object>} areas
 * @returns {Promise<boolean>} Whether the write landed.
 */
async function _writeCustomAreas(areas) {
  if (!window.pwaDb) return false;
  try {
    await window.pwaDb.put('meta:app', { key: CUSTOM_AREAS_KEY, value: areas });
    return true;
  } catch (_e) {
    return false;
  }
}

/**
 * SNOW-635: the ordinal for a NEW custom area — one above the highest
 * currently stored (`0` when there are none, so the first area is `1`).
 *
 * No persisted counter to keep in sync: deriving it fresh from what is
 * actually on disk means two areas can never collide on it. Gappy after a
 * delete (deleting 1 of {1, 2} leaves the next add at 3, not 2) is the
 * correct trade — reusing a freed number would put two different areas
 * under the same default name in the user's memory across sessions.
 *
 * @returns {Promise<number>}
 */
async function _nextCustomAreaOrdinal() {
  const areas = await _readCustomAreas();
  let max = 0;
  for (const entry of areas) {
    const n = Number(entry && entry.ordinal);
    if (Number.isFinite(n) && n > max) max = n;
  }
  return max + 1;
}

/**
 * SNOW-635: append `area` to `basemap.customAreas`, replacing any existing
 * entry with the same id — mirrors `_recordRegionDownload`'s
 * filter-then-push. A collision is not expected (every add mints a fresh
 * id via `generateCustomAreaId`), but this keeps the write idempotent
 * rather than assuming it.
 *
 * @param {{id: string, ordinal: number, name?: string, bbox: number[],
 *   band: number[], centre_tile: Object, template: string, bytes: number,
 *   savedAt: string}} area
 * @returns {Promise<void>}
 */
async function _appendCustomArea(area) {
  const existing = await _readCustomAreas();
  const next = existing.filter((entry) => !entry || entry.id !== area.id);
  next.push(area);
  await _writeCustomAreas(next);
}

/**
 * SNOW-635: rename a custom area, writing `name` onto its
 * `basemap.customAreas` entry.
 *
 * Regions are never renameable (see `basemap_manage_core.js`'s
 * `manageRows` — a region's name is its real name), so this only ever
 * touches a custom-area entry, identified by the SAME id its pinned
 * bucket uses. Best-effort: the manage sheet's own re-render, not a
 * return value here, is what tells the user whether it landed.
 *
 * @param {string} areaId
 * @param {string} name
 * @returns {Promise<boolean>} Whether the write landed.
 */
async function renameCustomArea(areaId, name) {
  const core = self.pwaBasemapDownloadCore;
  if (!core || !core.isCustomAreaId(areaId)) return false;
  const existing = await _readCustomAreas();
  if (!existing.some((entry) => entry && entry.id === areaId)) return false;
  const next = existing.map((entry) =>
    entry && entry.id === areaId ? { ...entry, name: name } : entry,
  );
  return _writeCustomAreas(next);
}

// SNOW-586: every area currently recorded as downloaded, normalised into
// planEviction's `[{id, name, bytes, savedAt}]` shape — the union of
// `basemap.regions` (mapDownloadControlInit's record, one entry per
// downloaded region) and (SNOW-635) `basemap.customAreas`
// (mapCustomDownloadControlInit's record, now an array — see
// `_readCustomAreas`). `name` is always populated for a non-orphaned area
// — stored for a region, stored-or-defaulted-from-ordinal for a custom
// area (see the inline comment below) — so every downstream reader can
// treat it uniformly; only `reconcileAreas`' orphan entries (no record at
// all) ever leave it unset. Best-effort: a failed read contributes
// nothing rather than throwing — eviction planning degrades to "nothing
// recorded, so nothing to evict", never to blocking a download outright
// over a transient IndexedDB error.
//
// @returns {Promise<Array<{id: string, name?: string, bytes: number, savedAt: string}>>}
async function basemapDownloadedAreas() {
  const core = self.pwaBasemapDownloadCore;
  const areas = [];
  if (!core || !window.pwaDb) return areas;
  try {
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    const regions = Array.isArray(row && row.value) ? row.value : [];
    for (const entry of regions) {
      if (!entry || !entry.region_id) continue;
      areas.push({
        id: core.areaIdForRegion(entry.region_id),
        name: entry.name || entry.region_id,
        bytes: Number(entry.bytes) || 0,
        savedAt: entry.savedAt,
      });
    }
  } catch (_e) {
    // Best-effort — see docstring.
  }
  try {
    const customAreas = await _readCustomAreas();
    for (const entry of customAreas) {
      if (!entry || !entry.id || !Array.isArray(entry.bbox)) continue;
      areas.push({
        id: entry.id,
        // SNOW-635 (review): `name` is set by a rename
        // (map_downloads_manager.js's Rename control) when present; an
        // unrenamed area's default display name ("Custom area N") is
        // filled in HERE, from `ordinal`, in memory only — never
        // persisted, so it stays translatable rather than freezing in
        // whatever language was active at download time. Filling it at
        // THIS single normalising layer, rather than at every
        // downstream reader, is what let the eviction confirm banner's
        // fallback regress to a raw id: the banner (and the sheet, and
        // the rename prompt's pre-fill) can all just read `area.name`
        // uniformly now, with nothing left to distinguish "stored" from
        // "defaulted".
        name:
          entry.name ||
          (Number.isFinite(entry.ordinal)
            ? self.pwaStrings.interpolate(MAP_STRINGS['default-custom-name'], {
                n: entry.ordinal,
              })
            : entry.id),
        bytes: Number(entry.bytes) || 0,
        savedAt: entry.savedAt,
      });
    }
  } catch (_e) {
    // Best-effort — see docstring.
  }

  // SNOW-612: union in the pinned buckets actually on disk. A record is
  // only written when a run COMPLETES, so a download that failed partway
  // left a bucket the budget never counted and the manage sheet could not
  // delete — quota that accumulated silently across failed attempts.
  // Without the manage core there is no reconciliation to run, so this
  // degrades to the records alone rather than to nothing.
  const manage = self.pwaBasemapManageCore;
  if (!manage || typeof manage.reconcileAreas !== 'function') return areas;
  const storedIds = await pinnedBucketAreaIds();
  const recordedIds = new Set(areas.map((area) => area.id));
  const orphanIds = storedIds.filter((id) => !recordedIds.has(id));
  // Measured one bucket at a time rather than in parallel: an orphan is
  // rare, and a concurrent walk of several thousand cache entries each is
  // the kind of burst that makes a slow device feel broken.
  const bytesById = {};
  for (const id of orphanIds) {
    bytesById[id] = await measurePinnedBucketBytes(id);
  }
  return manage.reconcileAreas(areas, storedIds, bytesById);
}

/**
 * SNOW-586: pre-flight an incoming `areaId` download of `mb` megabytes
 * against the standing byte budget — the page-side half of the eviction
 * plan (basemap_download_core.js's `planEviction` does the arithmetic;
 * this just gathers its inputs). Uses the worst-case `mb` ESTIMATE, not
 * an actual byte count — the real size is only known after the run
 * completes (`_warmCache`'s reported `bytes`), so pre-flight budgets on
 * the same upper-bound estimate `DOWNLOAD_CEILING_MB` already uses.
 *
 * @param {string} areaId The id of the area about to be (re)downloaded —
 *   `planEviction` excludes its own existing record from the standing
 *   total, so a re-download never counts its own earlier copy against
 *   itself.
 * @param {number} mb
 * @returns {Promise<{fits: boolean, impossible: boolean, evict: string[],
 *   projectedBytes: number, areasById: Map<string, Object>} | null>}
 *   ``null`` when `pwaBasemapDownloadCore` isn't loaded — callers treat
 *   that the same as "nothing to evict, proceed" (the pre-SNOW-586
 *   behaviour), since there is no budget arithmetic to run without it.
 */
async function planBasemapDownloadBudget(areaId, mb) {
  const core = self.pwaBasemapDownloadCore;
  if (!core) return null;
  const [areas, budgetBytes] = await Promise.all([
    basemapDownloadedAreas(),
    basemapDownloadBudgetBytes(),
  ]);
  const incomingBytes = Math.max(0, Number(mb) || 0) * 1024 * 1024;
  const plan = core.planEviction(areas, { id: areaId, bytes: incomingBytes }, budgetBytes);
  const areasById = new Map(areas.map((a) => [a.id, a]));
  return { fits: plan.fits, impossible: plan.impossible, evict: plan.evict, projectedBytes: plan.projectedBytes, areasById };
}

/**
 * SNOW-586: delete whole areas — each one's pinned Cache Storage bucket
 * AND its meta:app record — so an eviction can never leave a stale
 * "downloaded" ring or a budget entry with nothing behind it. Best-effort
 * per area: one failure doesn't abort the rest, and a record whose bucket
 * is already gone (or vice versa) still gets its other half cleaned up.
 *
 * The bucket deletes are independent per id and still run in parallel.
 * The RECORD writes do NOT — SNOW-635 review: `basemap.customAreas` (and,
 * latently, `basemap.regions`) is one shared row, so a per-id
 * read-filter-write run inside `Promise.all` is a read-modify-write race
 * the moment two ids of the SAME record type are evicted in one call.
 * Both tasks read the identical snapshot, each writes back a record
 * missing only its OWN id, and whichever write lands last wins — leaving
 * the other "evicted" id's entry alive in the record with no bucket
 * behind it. This was unreachable before this ticket (there was only
 * ever one custom area, so `planEviction` could never return two custom
 * ids); it is reachable now. Read once per record type, filter out every
 * targeted id from THAT type in one pass, write once.
 *
 * @param {string[]} areaIds
 * @returns {Promise<void>}
 */
async function evictBasemapAreas(areaIds) {
  const core = self.pwaBasemapDownloadCore;
  const ids = Array.isArray(areaIds) ? areaIds : [];
  if (!core || !ids.length) return;

  await Promise.all(
    ids.map(async (areaId) => {
      try {
        await caches.delete(core.pinnedCacheName(areaId));
      } catch (_e) {
        // Best-effort.
      }
      // SNOW-612: the bucket is gone, so any measurement of it is too.
      forgetPinnedBucketMeasurement(areaId);
    }),
  );

  // SNOW-635: `core.isCustomAreaId` — a custom area's own bucket-id
  // FAMILY, not the single legacy `CUSTOM_AREA_ID` — see that predicate's
  // own comment.
  const customIds = new Set(ids.filter((id) => core.isCustomAreaId(id)));
  const regionIds = new Set(ids.filter((id) => !core.isCustomAreaId(id)));

  try {
    if (customIds.size) {
      const existing = await _readCustomAreas();
      const next = existing.filter((entry) => !entry || !customIds.has(entry.id));
      if (next.length !== existing.length) {
        // Always a `put`, even when `next` is `[]` — see
        // `_writeCustomAreas`'s docstring for why deleting the LAST
        // custom area must not delete the key itself.
        await _writeCustomAreas(next);
      }
    }
  } catch (_e) {
    // Best-effort — a stale record with no bucket behind it is treated as
    // evictable-first the next time budget planning runs (see the "byte
    // totals are page-recorded" risk note in
    // docs/decisions/per-area-pinned-basemap-caches.md).
  }

  try {
    if (regionIds.size) {
      const row = await window.pwaDb?.get('meta:app', 'basemap.regions');
      const existing = Array.isArray(row && row.value) ? row.value : [];
      const next = existing.filter(
        (entry) => !(entry && regionIds.has(core.areaIdForRegion(entry.region_id))),
      );
      if (next.length !== existing.length) {
        await window.pwaDb?.put('meta:app', { key: 'basemap.regions', value: next });
      }
    }
  } catch (_e) {
    // Best-effort — see the comment above.
  }

  // SNOW-613: tell the worker its memoised pinned-bucket list is stale.
  // It has no other way to learn about a page-side deletion, and a stale
  // name there would be handed to `caches.open`, recreating the bucket the
  // user just deleted as an empty one.
  navigator.serviceWorker?.controller?.postMessage({
    type: 'pinned-buckets-changed',
  });
  // SNOW-570: an evicted area's ring must disappear immediately, not at
  // the next refresh trigger.
  window.pwaDownloadedOverlay?.refresh();
}

// SNOW-588: the two functions above, for modules OUTSIDE this file — the
// "Manage downloads" sheet (static/js/map_downloads_manager.js), which
// lists every downloaded area and deletes the ones the user picks.
//
// Both are module scope, so the sheet cannot reach them directly, and
// both are exactly what it needs — which is why it delegates rather than
// reading `basemap.regions` / `basemap.customAreas` for itself. Downloads
// live in TWO records (an array of regions, and — SNOW-635 — an array of
// custom areas), each keyed differently from the Cache Storage bucket it
// owns, and `evictBasemapAreas` already knows how to take an area id back
// to the right half of the right record. A second reader would have to
// re-derive all of that and would be free to drift from the eviction
// path, which is the same state seen from the other side: the budget
// this sheet edits is spent by the planner these functions feed.
//
// Exposed as one frozen object beside pwaDownloadedOverlay, the bridge
// this file already uses for its sibling IIFEs.
window.pwaBasemapDownloads = Object.freeze({
  /**
   * Every recorded area, normalised to `{id, name, bytes, savedAt}` and
   * keyed by the id that also names its pinned Cache Storage bucket.
   *
   * @returns {Promise<Array<Object>>} Empty when nothing is recorded or
   *   the read fails — never rejects.
   */
  areas: () => basemapDownloadedAreas(),

  /**
   * Delete whole areas — bucket and record entry both.
   *
   * @param {string[]} areaIds
   * @returns {Promise<void>} Resolves whether or not every area went;
   *   it is best-effort per area, so callers that need to know verify by
   *   re-reading `areas()` rather than trusting this to report.
   */
  evict: (areaIds) => evictBasemapAreas(areaIds),

  /**
   * SNOW-635: rename a custom area. A no-op (resolving `false`) for a
   * region id — regions are never renameable.
   *
   * @param {string} areaId
   * @param {string} name
   * @returns {Promise<boolean>} Whether the write landed.
   */
  rename: (areaId, name) => renameCustomArea(areaId, name),
});

/**
 * SNOW-586: reveal the whole-area-eviction confirm banner naming
 * `evictAreas` and resolve once the user answers.
 *
 * Degrades to `false` (treated as "cancelled") when the banner markup
 * isn't present — an older cached shell mid-rollout, say — because
 * silently proceeding to evict without ever having asked is exactly the
 * silence this ticket exists to remove; refusing the run is the safe
 * direction, not evicting anyway.
 *
 * @param {Array<{id: string, name?: string}>} evictAreas
 * @returns {Promise<boolean>} `true` = proceed (the caller still has to
 *   call `evictBasemapAreas` itself — this only asks), `false` = cancel.
 */
function confirmBasemapEviction(evictAreas) {
  return new Promise((resolve) => {
    const banner = document.getElementById('map-download-evict-confirm');
    const body = document.getElementById('map-download-evict-confirm-body');
    const cta = document.getElementById('map-download-evict-confirm-cta');
    if (!banner || !cta) {
      resolve(false);
      return;
    }
    // SNOW-635 review: `name` is populated for every non-orphaned area —
    // stored for a region, stored-or-defaulted-from-ordinal for a custom
    // area (see `basemapDownloadedAreas`'s own comment) — so this banner
    // never has to know how to build a default itself. The `|| a.id`
    // fallback exists only for the one case that still has no name at
    // all: an orphaned bucket (SNOW-612) with no record behind it, which
    // `planEviction` can legitimately pick (its missing `savedAt` sorts
    // it as the oldest thing on disk).
    if (body) {
      body.textContent = (evictAreas || []).map((a) => a.name || a.id).join(', ');
    }
    let settled = false;
    const onConfirm = () => {
      if (settled) return;
      settled = true;
      cleanup();
      banner.classList.add('hidden');
      resolve(true);
    };
    const onDismiss = (e) => {
      if (settled || !(e.detail && e.detail.overlay === banner)) return;
      settled = true;
      cleanup();
      resolve(false);
    };
    const cleanup = () => {
      cta.removeEventListener('click', onConfirm);
      document.removeEventListener('overlay:dismissed', onDismiss);
    };
    cta.addEventListener('click', onConfirm);
    document.addEventListener('overlay:dismissed', onDismiss);
    banner.classList.remove('hidden');
  });
}

// SNOW-568: the basemap-download failure toasts in _map_embed.html. Only
// 'quota' and (SNOW-586) 'budget' have a remedy of their own (free space
// / frame a smaller area; refuse a run larger than the whole standing
// budget) — every other cause — an unreachable network, a worker that
// went silent, a server answering 4xx/5xx — leads to the same
// instruction, so they share the generic toast rather than leaking a
// classification the user can do nothing with.
const BASEMAP_DOWNLOAD_ERROR_TOAST_IDS = {
  quota: 'map-download-error-toast-quota',
  budget: 'map-download-error-toast-budget',
  // SNOW-605: the page has no service worker controlling it, so there was
  // nothing to dispatch the download to — a state a shift-reload leaves the
  // document in permanently, and an SW update leaves it in briefly. The
  // fallback copy ("check your connection") actively misleads here: the
  // network is fine and no request was ever made.
  'no-worker': 'map-download-error-toast-no-worker',
};
const BASEMAP_DOWNLOAD_ERROR_TOAST_FALLBACK_ID = 'map-download-error-toast';

// SNOW-568: reveal the basemap-download failure toast matching ``reason``,
// and hide the other one.
//
// The copy lives in the templates (where {% trans %} can reach it), not
// here — hence two elements rather than one whose text this rewrites.
// Both download controls (per-region and custom-area) share them: the two
// runs are mutually exclusive in practice, and hiding the sibling means a
// second failure of a different kind replaces the first message rather
// than stacking a contradictory one beside it.
//
// Uses the same hidden/flex toggle idiom as the map's own
// revealOfflineToast — see its comment for why ``flex`` is added rather
// than baked into the partial's class list. Best-effort throughout: a
// missing element (an older cached shell that predates the partials) is a
// silent no-op, never a thrown error inside a download's finish handler.
//
// @param {string|null} reason
// @returns {void}
function revealBasemapDownloadError(reason) {
  try {
    const showId =
      BASEMAP_DOWNLOAD_ERROR_TOAST_IDS[reason] || BASEMAP_DOWNLOAD_ERROR_TOAST_FALLBACK_ID;
    const ids = [
      ...Object.values(BASEMAP_DOWNLOAD_ERROR_TOAST_IDS),
      BASEMAP_DOWNLOAD_ERROR_TOAST_FALLBACK_ID,
    ];
    // The toasts dock at the foot of the viewport, which is exactly where
    // the framing overlay's CTA sheet sits — and a custom-area failure
    // leaves that overlay open, so the default position would cover the
    // Cancel/Download buttons the message is telling the user to use.
    // Measured rather than assumed: the sheet wraps to two rows on a
    // narrow viewport, and a hardcoded offset would be wrong there.
    const offset = _framingToastOffset();
    for (const id of ids) {
      const el = document.getElementById(id);
      if (!el) continue;
      const show = id === showId;
      el.classList.toggle('hidden', !show);
      el.classList.toggle('flex', show);
      if (offset === null) {
        el.style.removeProperty('bottom');
      } else {
        el.style.bottom = `${offset}px`;
      }
    }
  } catch (_e) {
    // Non-fatal — the roundel's error state still carries the outcome.
  }
}

// SNOW-568: the `bottom` a download toast needs to clear the framing
// overlay's CTA sheet, or null when framing isn't open (leave the
// stylesheet's own docking alone).
//
// Derived from the sheet's distance to the viewport's bottom edge, NOT
// from its height: the toast is position:fixed against the viewport while
// the overlay is positioned inside #map, and the map does not run to the
// bottom of the window. Offsetting by the sheet's height alone left the
// toast overlapping it by exactly the gap below the map.
//
// @returns {number|null}
function _framingToastOffset() {
  const overlay = document.getElementById('map-frame-overlay');
  const cta = document.getElementById('map-frame-cta');
  if (!overlay || !cta || overlay.hasAttribute('hidden')) return null;
  const rect = cta.getBoundingClientRect();
  if (!rect.height) return null;
  return Math.round(window.innerHeight - rect.top) + 16;
}

// SNOW-568: hide both basemap-download failure toasts — called when a run
// starts, so a previous failure's message can't sit next to a download
// that is now succeeding.
//
// @returns {void}
function clearBasemapDownloadError() {
  try {
    const ids = [
      ...Object.values(BASEMAP_DOWNLOAD_ERROR_TOAST_IDS),
      BASEMAP_DOWNLOAD_ERROR_TOAST_FALLBACK_ID,
    ];
    for (const id of ids) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.classList.add('hidden');
      el.classList.remove('flex');
      // Drop the framing-aware offset with the toast itself, so a later
      // reveal with the overlay closed docks where the stylesheet says.
      el.style.removeProperty('bottom');
    }
  } catch (_e) {
    // Non-fatal — a stale toast is still dismissible by its own "×".
  }
}

// SNOW-568: pre-flight a download of ``mb`` megabytes against the origin's
// remaining storage quota.
//
// Resolves true when the download should go ahead, including every case
// where the answer is unknowable (no Storage API, an estimate() that
// rejects) — an unknown quota must not block a download that would have
// worked, and _warmCache's own QuotaExceededError handling is the backstop.
//
// @param {number} mb
// @returns {Promise<boolean>}
async function basemapDownloadFitsQuota(mb) {
  const core = self.pwaBasemapDownloadCore;
  if (!core || typeof core.hasStorageHeadroom !== 'function') return true;
  if (!('storage' in navigator) || typeof navigator.storage.estimate !== 'function') {
    return true;
  }
  try {
    return core.hasStorageHeadroom(await navigator.storage.estimate(), mb);
  } catch (_e) {
    return true;
  }
}

// SNOW-569, reworked as a tile grid: ids for the on-map download progress
// grid. One source and two layers, created on demand and torn down when the run settles — there
// is never more than one download in flight (both controls refuse a click
// while their own state is 'busy', and they can't both be running because
// the custom-area control's framing overlay covers the region control).
const DOWNLOAD_PROGRESS_SOURCE_ID = 'download-progress';
const DOWNLOAD_PROGRESS_FILL_LAYER_ID = 'download-progress-fill';
const DOWNLOAD_PROGRESS_LINE_LAYER_ID = 'download-progress-line';

// Fallback for --color-sync-ok (src/css/main.css @theme) — the SAME green
// the layers-menu "available offline" dot and the download roundel's own
// fill use, so the map, the roundel, and the cache dashboard speak one
// visual language. MapLibre paint values can't reference a CSS variable, so
// the live value is read off the document at the start of each run (the
// theme has a lighter green in dark mode) and this is only the floor.
const DOWNLOAD_PROGRESS_COLOUR_FALLBACK = '#16a34a';

// Opacity a landed square sits at, and the peak of the completion pulse.
// The fill lands ABOVE the choropleth, so it has to stay translucent
// enough to read the region's danger colour through it while a download
// runs; the pulse then swells past that for one beat before fading out.
const DOWNLOAD_PROGRESS_OPACITY = 0.45;
const DOWNLOAD_PROGRESS_PULSE_OPACITY = 0.85;
const DOWNLOAD_PROGRESS_PULSE_RISE_MS = 180;
const DOWNLOAD_PROGRESS_PULSE_FADE_MS = 440;

// The empty grid — every square is drawn from the first frame, so the user
// sees the shape of what they asked for and then watches it fill.
//
// A square that hasn't landed is washed in at PENDING opacity rather than
// left fully transparent. The grid is drawn at the band's detail floor, so
// a large region is several thousand squares; at that density the outlines
// alone read as a mesh, and zoomed out far enough they stop resolving as
// squares at all. The wash keeps the download's extent legible as a block
// whatever the scale, with the landed squares reading against it.
const DOWNLOAD_PROGRESS_PENDING_OPACITY = 0.12;
const DOWNLOAD_PROGRESS_GRID_OPACITY = 0.5;
const DOWNLOAD_PROGRESS_GRID_WIDTH = 0.75;

// Gridlines fade out as the squares shrink on screen. A tile spans roughly
// the whole viewport-tile width when the map sits at its own zoom, halving
// with every level out — so a few levels below the grid's zoom the
// outlines are sub-pixel and turn into noise. These are offsets FROM the
// grid's zoom: invisible at gridZ + FADE_START, full strength by
// gridZ + FADE_END.
const DOWNLOAD_PROGRESS_GRID_FADE_START = -4;
const DOWNLOAD_PROGRESS_GRID_FADE_END = -2;

/**
 * The insertion point for an overlay that belongs above every region
 * layer but below the region labels: ``regions-label``, or undefined.
 *
 * Deliberately this one named layer rather than "the style's first
 * ``symbol`` layer". That generic rule reads whatever the BASEMAP happens
 * to provide, and a basemap's own labels sit below the region tiers — so
 * it could return an anchor UNDER ``regions-fill`` and push the overlay
 * below the very layers it is supposed to cover. It also varied with how
 * far the style had parsed when the overlay was built, which made the
 * ordering depend on timing.
 *
 * @returns {string | undefined} ``'regions-label'`` when it is installed,
 *   otherwise undefined — the caller then adds on top, which is the right
 *   answer for a style with no region labels to protect.
 */
function _aboveRegionsBeforeId() {
  try {
    return MAP.getLayer('regions-label') ? 'regions-label' : undefined;
  } catch (_e) {
    // Style mid-reload — the caller falls back to adding on top.
    return undefined;
  }
}

/**
 * A download's on-map progress grid: the tiles being fetched are drawn as
 * an empty grid of squares over the area, and each square fills in as its
 * own tiles land. The whole grid pulses once on success, then is removed.
 *
 * Why squares and not a rising fill (which this replaces): the squares
 * ARE the download. Each one is a real Web Mercator tile footprint at
 * ``plan.gridZ``, so what the user watches is the actual unit of work
 * completing, rather than a percentage re-expressed as a water level. It
 * also removes the old version's one dishonesty — a region's boundary
 * filling up, when what a run actually fetches is the tiles covering its
 * bounding box.
 *
 * Cells complete one at a time because ``tileGridPlan`` hands the service
 * worker its URLs grouped by cell (see that function). Fetch order is the
 * only thing making this legible; nothing here reorders anything.
 *
 * The grid is anchored to the ground, so it stays put as the map is
 * panned and zoomed under it — the squares are geometry, not screen-space
 * decoration. It draws above every region layer (see ``_ensure``): what is
 * filling up is the tile cache, not a region, and a tile is cached whole
 * whether or not a boundary happens to cross it.
 *
 * Ticks arrive in batches from the service worker (~8 a second for a fast
 * run, roughly per-tile for a slow one). A completed square is lit with
 * `setFeatureState` rather than by rewriting the source: at the band's
 * detail floor a large region is several thousand cells, and re-serialising
 * that collection on every batch would be megabytes of JSON a second.
 *
 * @param {{gridZ: number, cells: Array<{bbox: number[], total: number}>,
 *   cellOfURL: number[]} | null} plan The grid plan from
 *   ``pwaBasemapDownloadCore.tileGridPlan``.
 * @param {number} [urlOffset] How many non-tile URLs (the feed warm-up
 *   list) sit in front of the plan's tile URLs in the list handed to the
 *   worker, so reported indices can be mapped back onto ``cellOfURL``.
 * @returns {{update: function(number, number, number[]=): void, finish:
 *   function(boolean): Promise<void>}} ``update`` takes the worker's
 *   ``(done, total, settled)`` progress report; ``finish`` takes whether
 *   the run succeeded and resolves once the pulse (success only) has
 *   played and the layers are gone. Both are no-ops on a map or plan the
 *   grid can't be built for, so callers never have to branch.
 */
function createDownloadProgressGrid(plan, urlOffset) {
  const cells = plan && Array.isArray(plan.cells) ? plan.cells : null;
  // No map, or nothing to draw: hand back the same shape doing nothing, so
  // the download path itself stays branch-free.
  if (!MAP || !cells || !cells.length) {
    return { update: () => {}, finish: () => Promise.resolve() };
  }

  const offset = typeof urlOffset === 'number' ? urlOffset : 0;
  const cellOfURL = Array.isArray(plan.cellOfURL) ? plan.cellOfURL : [];
  const colour =
    getComputedStyle(document.documentElement).getPropertyValue('--color-sync-ok').trim() ||
    DOWNLOAD_PROGRESS_COLOUR_FALLBACK;
  const reducedMotion =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // One Feature per cell, built once and pushed to the source once. The
  // `id` is what lets a completed square be lit with `setFeatureState`
  // instead of re-serialising the whole collection — at the band's detail
  // floor a large region is several thousand cells, so a per-tick setData
  // would be megabytes of JSON several times a second.
  const features = cells.map((cell, index) => {
    const [west, south, east, north] = cell.bbox;
    return {
      type: 'Feature',
      id: index,
      properties: {},
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
          ],
        ],
      },
    };
  });
  // Tiles still outstanding per cell. A cell completes when its count
  // reaches zero — which is why a FAILED tile never decrements it (the
  // worker reports successes only): a square must not light up over
  // ground that isn't cached.
  const outstanding = cells.map((cell) => cell.total);
  // Which cells have completed. Kept alongside the feature states because
  // a mid-run basemap swap takes the source with it, and feature state
  // does not survive that — `_ensure` replays this set onto the rebuilt
  // source so the grid picks up where it left off rather than emptying.
  const doneCells = new Set();
  let frame = 0;
  let removed = false;

  /**
   * Add the source and layers if they aren't on the style, and return
   * the source. Called before every paint rather than once at the start
   * because a basemap swap mid-run replaces the whole style, taking every
   * custom source with it — this quietly rebuilds on the next tick
   * instead of throwing for the rest of the run.
   *
   * @returns {Object | null} The geojson source, or null while the style
   *   is in no state to take one.
   */
  function _ensure() {
    if (removed) return null;
    try {
      const existing = MAP.getSource(DOWNLOAD_PROGRESS_SOURCE_ID);
      if (existing) return existing;
      // Deliberately NOT gated on map.isStyleLoaded(). That reports
      // Style.loaded(), which additionally requires every SOURCE to have
      // loaded — so a basemap whose tiles are slow, or whose origin is
      // unreachable, holds it false indefinitely and would suppress the
      // grid for the whole run. addSource needs only a parsed style, and
      // the catch below already covers a style that can't take one.
      MAP.addSource(DOWNLOAD_PROGRESS_SOURCE_ID, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: features },
      });
      // Above every region layer, below the labels.
      //
      // It used to sit between the choropleth and the region outline, to
      // read as "this region filling up". That was the wrong model and it
      // showed: the grid is translucent, so the danger colour beneath it
      // tinted the part of each square inside the region's boundary and
      // left the part outside untinted — one square rendered as two
      // shades, which reads as the square being CUT along the boundary.
      // Nothing was ever clipped; the whole tile is fetched and the whole
      // tile is cached. What the user is watching is the tile cache
      // filling, and a cache does not stop at a region edge, so the grid
      // is drawn as its own overlay: uniform, whatever is underneath.
      //
      // Below ``regions-label`` rather than flat on top, so region names
      // stay readable through a run.
      const beforeId = _aboveRegionsBeforeId();
      // Every cell is in the fill layer from the start; only the ones
      // whose feature state says `done` are actually painted. Opacity
      // rather than a filter, because a filter is re-evaluated against
      // the source data (which never changes here) while feature state is
      // designed for exactly this — cheap per-feature updates on a source
      // that stays put.
      MAP.addLayer(
        {
          id: DOWNLOAD_PROGRESS_FILL_LAYER_ID,
          type: 'fill',
          source: DOWNLOAD_PROGRESS_SOURCE_ID,
          paint: {
            'fill-color': colour,
            'fill-opacity': [
              'case',
              ['boolean', ['feature-state', 'done'], false],
              DOWNLOAD_PROGRESS_OPACITY,
              DOWNLOAD_PROGRESS_PENDING_OPACITY,
            ],
          },
        },
        beforeId,
      );
      // Every cell's outline, landed or not — the empty grid the run
      // starts from, and the gridlines between the squares once they
      // begin filling.
      MAP.addLayer(
        {
          id: DOWNLOAD_PROGRESS_LINE_LAYER_ID,
          type: 'line',
          source: DOWNLOAD_PROGRESS_SOURCE_ID,
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: {
            'line-color': colour,
            'line-width': DOWNLOAD_PROGRESS_GRID_WIDTH,
            'line-opacity': [
              'interpolate',
              ['linear'],
              ['zoom'],
              plan.gridZ + DOWNLOAD_PROGRESS_GRID_FADE_START,
              0,
              plan.gridZ + DOWNLOAD_PROGRESS_GRID_FADE_END,
              DOWNLOAD_PROGRESS_GRID_OPACITY,
            ],
          },
        },
        beforeId,
      );
      // A freshly-built source has no feature state, so anything already
      // completed has to be replayed onto it — otherwise a basemap swap
      // mid-run would empty a half-filled grid.
      for (const index of doneCells) _light(index);
      return MAP.getSource(DOWNLOAD_PROGRESS_SOURCE_ID);
    } catch (_e) {
      // Style mid-reload. The next tick tries again.
      return null;
    }
  }

  /**
   * Set cell `index`'s feature state to done, so its square paints.
   *
   * @param {number} index Index into `cells`.
   * @returns {void}
   */
  function _light(index) {
    try {
      MAP.setFeatureState(
        { source: DOWNLOAD_PROGRESS_SOURCE_ID, id: index },
        { done: true },
      );
    } catch (_e) {
      // Source went away with a style reload. `_ensure` replays
      // `doneCells` onto its replacement.
    }
  }

  /**
   * Mark cell `index` complete, if it isn't already.
   *
   * @param {number} index Index into `cells`.
   * @returns {void}
   */
  function _complete(index) {
    if (index < 0 || index >= features.length) return;
    if (doneCells.has(index)) return;
    doneCells.add(index);
    _light(index);
  }

  /**
   * Take down the source and its layers. Idempotent, and safe against a
   * style that has already dropped them.
   *
   * @returns {void}
   */
  function _remove() {
    removed = true;
    if (frame) {
      cancelAnimationFrame(frame);
      frame = 0;
    }
    try {
      for (const id of [DOWNLOAD_PROGRESS_FILL_LAYER_ID, DOWNLOAD_PROGRESS_LINE_LAYER_ID]) {
        if (MAP.getLayer(id)) MAP.removeLayer(id);
      }
      if (MAP.getSource(DOWNLOAD_PROGRESS_SOURCE_ID)) {
        MAP.removeSource(DOWNLOAD_PROGRESS_SOURCE_ID);
      }
    } catch (_e) {
      // Already gone with the style. Nothing to do.
    }
  }

  /**
   * One swell and fade of the completed grid — the "this is finished"
   * beat before the roundel flips to its green done state.
   *
   * @returns {Promise<void>} Resolves when the pulse has played out.
   */
  function _pulse() {
    return new Promise((resolve) => {
      if (removed || !MAP.getLayer(DOWNLOAD_PROGRESS_FILL_LAYER_ID)) {
        resolve();
        return;
      }
      const total = DOWNLOAD_PROGRESS_PULSE_RISE_MS + DOWNLOAD_PROGRESS_PULSE_FADE_MS;
      const started = performance.now();
      const step = (now) => {
        const elapsed = now - started;
        // Rise from the working opacity to the pulse peak, then fade the
        // whole thing out — one beat, not a repeating throb.
        let opacity;
        if (elapsed < DOWNLOAD_PROGRESS_PULSE_RISE_MS) {
          const t = elapsed / DOWNLOAD_PROGRESS_PULSE_RISE_MS;
          opacity =
            DOWNLOAD_PROGRESS_OPACITY +
            (DOWNLOAD_PROGRESS_PULSE_OPACITY - DOWNLOAD_PROGRESS_OPACITY) * t;
        } else {
          const t = Math.min(
            1,
            (elapsed - DOWNLOAD_PROGRESS_PULSE_RISE_MS) / DOWNLOAD_PROGRESS_PULSE_FADE_MS,
          );
          opacity = DOWNLOAD_PROGRESS_PULSE_OPACITY * (1 - t);
        }
        try {
          // A flat opacity, replacing the feature-state expression — safe
          // only because `finish` completes every cell before pulsing, so
          // there is no longer a dark square for it to reveal.
          MAP.setPaintProperty(DOWNLOAD_PROGRESS_FILL_LAYER_ID, 'fill-opacity', opacity);
          // The gridlines fade with the fill rather than at their own
          // fainter level, so the whole grid leaves as one object.
          MAP.setPaintProperty(DOWNLOAD_PROGRESS_LINE_LAYER_ID, 'line-opacity', opacity);
        } catch (_e) {
          resolve();
          return;
        }
        if (elapsed >= total) {
          resolve();
          return;
        }
        frame = requestAnimationFrame(step);
      };
      frame = requestAnimationFrame(step);
    });
  }

  // Draw the empty grid straight away: the squares are up before the first
  // tile lands, so the user sees the extent of what they asked for and
  // then watches it fill.
  _ensure();

  return {
    /**
     * Take one progress report from the worker and light up any square it
     * completed.
     *
     * @param {number} done Tiles settled so far.
     * @param {number} total Tiles in the run.
     * @param {number[]} [settled] Indices into the posted URL list that
     *   succeeded since the last report. Absent when an older service
     *   worker is still serving the cached shell — the grid then falls
     *   back to filling cells in plan order at the reported percentage,
     *   which is the same information the pre-tile-grid fill had.
     * @returns {void}
     */
    update(done, total, settled) {
      if (removed) return;
      if (Array.isArray(settled)) {
        for (const urlIndex of settled) {
          // Feed URLs sit in front of the tiles and belong to no cell.
          const tileIndex = urlIndex - offset;
          if (tileIndex < 0 || tileIndex >= cellOfURL.length) continue;
          const cellIndex = cellOfURL[tileIndex];
          outstanding[cellIndex] -= 1;
          if (outstanding[cellIndex] <= 0) _complete(cellIndex);
        }
      } else if (total > 0) {
        // Proportional fallback: no per-tile detail to place, so fill in
        // plan order to the fraction reported.
        const target = Math.floor(cells.length * (done / total));
        for (let i = 0; i < target; i++) _complete(i);
      }
      // No repaint to schedule: `_complete` already lit each new square
      // through feature state, and MapLibre coalesces those onto its own
      // next frame.
    },

    /**
     * Settle the grid: a whole-grid pulse on success, an immediate
     * removal otherwise (a failed run must not leave a green area
     * behind, however briefly).
     *
     * @param {boolean} ok Whether the run succeeded.
     * @returns {Promise<void>}
     */
    async finish(ok) {
      if (removed) return;
      if (frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
      if (!ok) {
        _remove();
        return;
      }
      // A success means every tile landed — pulse a complete grid, not a
      // 99% one. Cells can legitimately still be dark here: a tile that
      // succeeded in the worker's final batch is reported alongside the
      // done reply, and `finish` can win that race.
      for (let i = 0; i < features.length; i++) _complete(i);
      if (!reducedMotion) await _pulse();
      _remove();
    },
  };
}

// ---------------------------------------------------------------------------
// Shared pinned-download runner (SNOW-611)
// ---------------------------------------------------------------------------
//
// The ordered run itself lives in `static/js/basemap_download_runner.js`,
// so the sequence both download controls depend on can be tested against
// fakes rather than a live MapLibre instance
// (`tests/js/test_basemap_download_runner.js`). This is the thin delegator
// — the same shape `sw.js` uses for `basemap_cache_core.js`.
//
// The ordering that matters, and why it is encoded in one place rather
// than at the two call sites: `evictBasemapAreas` destroys ANOTHER area's
// pinned bucket and its meta:app record for good, so it has to be the LAST
// step before the run. Splitting the sequence across call sites is how the
// two copies drifted; SNOW-607 (D1) fixed the ordering in one of them.

// The helpers `run` needs, bound once. Every one of them reaches for the
// live map, Cache Storage, `navigator.storage` or the service worker,
// which is exactly why they are passed in rather than imported.
const PINNED_DOWNLOAD_DEPS = {
  clearError: () => clearBasemapDownloadError(),
  revealError: (reason) => revealBasemapDownloadError(reason),
  fitsQuota: (mb) => basemapDownloadFitsQuota(mb),
  core: () => self.pwaBasemapDownloadCore,
  tileTemplate: () => activeBasemapTileTemplate(MAP),
  planBudget: (areaId, mb) => planBasemapDownloadBudget(areaId, mb),
  confirmEviction: (areas) => confirmBasemapEviction(areas),
  evict: (areaIds) => evictBasemapAreas(areaIds),
  feedUrls: () => assembleBasemapDownloadFeedURLs(),
  progressGrid: (plan, offset) => createDownloadProgressGrid(plan, offset),
  warmCache: (urls, opts) =>
    typeof window.pwaWarmCache === 'function' ? window.pwaWarmCache(urls, opts) : null,
  isOnline: () => navigator.onLine,
};

/**
 * Run one pinned basemap download — see `basemap_download_runner.js` for
 * the sequence and the argument contract.
 *
 * A missing runner module fails the run rather than silently doing
 * nothing: from the user's side a click that quietly returns to idle is
 * indistinguishable from the download never having been offered, which is
 * the silence SNOW-568 exists to remove.
 *
 * @param {Object} options
 * @returns {Promise<void>}
 */
async function runPinnedDownload(options) {
  // SNOW-612: whatever this run writes changes the bucket's size, so a
  // measurement taken before it is stale from here on.
  forgetPinnedBucketMeasurement(options.areaId);
  const runner = self.pwaBasemapDownloadRunner;
  if (!runner) {
    options.paint('error');
    revealBasemapDownloadError(null);
    return;
  }
  return runner.run(PINNED_DOWNLOAD_DEPS, options);
}

/**
 * Wrap an async, idempotent render so overlapping calls coalesce
 * (SNOW-613).
 *
 * Both download controls' `renderControl` probes Cache Storage, and its
 * triggers arrive in bursts — a basemap swap, a connectivity flip and a
 * region selection can all land in the same tick. Each probe now walks
 * every pinned bucket, so a burst issued that walk several times over for
 * one answer.
 *
 * Trailing, not leading: a call arriving mid-probe carries NEWER state
 * than the one running (a different focused region, a connection that has
 * since dropped), so dropping it would settle the roundel against state
 * the user has already moved on from. One extra pass runs after the
 * current one, however many calls arrive during it.
 *
 * @param {function(): Promise<void>} render
 * @returns {function(): Promise<void>}
 */
function coalesceRenders(render) {
  let running = false;
  let again = false;
  return async function coalesced() {
    if (running) {
      again = true;
      return;
    }
    running = true;
    try {
      do {
        again = false;
        await render();
      } while (again);
    } finally {
      running = false;
    }
  };
}

/**
 * Build a "re-run `render` once MapLibre next goes idle" callback
 * (SNOW-611). Both download controls had a byte-identical copy of this,
 * each with its own coalescing flag.
 *
 * Needed because `activeBasemapTileTemplate` is gated on
 * `map.isStyleLoaded()`, which is false for the whole of the boot sequence
 * that first paints these icons: the region/overlay sources are added
 * inside `map.on('load')` itself, leaving the style dirty when
 * MAP_READY_PROMISE resolves. The first done-probe therefore couldn't see
 * the pinned cache at all, and a reload of an already-downloaded area
 * always painted 'idle' until the user reselected it.
 *
 * @param {function(): void} render The control's own `renderControl`.
 * @returns {function(): void} Idempotent while a retry is already queued —
 *   repeated unresolved probes coalesce into one pending listener.
 */
function makeStyleSettleRetry(render) {
  let pending = false;
  return function retryWhenStyleSettles() {
    if (pending) return;
    if (!MAP || typeof MAP.once !== 'function') return;
    pending = true;
    MAP.once('idle', () => {
      pending = false;
      render();
    });
  };
}


// True while timelapse playback is running. Set directly by timelapseInit()'s
// start() and stop() functions; after each mutation those functions also
// dispatch ``snowdesk:timelapse-state`` so the main IIFE can call
// clearTooltip(). The main IIFE reads IS_PLAYING to suppress redundant
// /api/region/<id>/summary/ requests on every timelapse frame advance.
let IS_PLAYING = false;

// Resolved by the main IIFE once the MapLibre style has loaded and the
// regions source has been added. Sibling IIFEs that need to call
// setFeatureState during boot (e.g. the scrubber on /map/?d=...) await
// this before painting; user-triggered IIFEs (timelapse) don't need to,
// since the user can't click before the map is up.
let resolveMapReady = null;
const MAP_READY_PROMISE = new Promise((r) => { resolveMapReady = r; });

// SNOW-610: the one explicit channel to this file's shared state.
//
// Every declaration above is a top-level `let`/`const` in a CLASSIC script.
// That puts it in the global LEXICAL scope — another classic script can read
// it as a bare identifier — but NOT on `window`. The distinction is invisible
// until you rely on the wrong half of it: `map_layer_sync_status.js` read
// `window.MAP` for its entire life and always got `undefined` (finding M1),
// while `favourites.js` and `map_edit_resorts.js` reach the same handle
// successfully through `typeof MAP !== 'undefined' ? MAP : null` — an idiom
// that works, reads like defensive noise, and is impossible to grep for.
//
// So the state gets one named, greppable owner. Consumers use this; the 86
// internal references in this file keep using the bare identifiers, because
// rewriting them would be a large diff with no reader benefit — the
// declarations are right here.
//
// `map_edit_resorts.js` is also deliberately left on the bare identifier: it
// reaches `MAP` at ~30 sites, is staff-only (`edit_mode`), and has no unit
// coverage, so a mechanical rewrite there would be churn carrying real
// regression risk and no reader benefit. The two consumers converted are the
// ones where it pays: `map_layer_sync_status.js`, which had the actual M1
// bug, and `favourites.js`, which needed one line changed.
//
// FROZEN SURFACE, MUTABLE VALUES. `Object.freeze` stops anything replacing or
// adding an accessor; the accessors themselves still read and write the live
// bindings. A frozen plain-data object would have been wrong — `map` is null
// until the style loads, and the whole point is that late writers can set it.
//
// Splitting this file (SNOW-610) needs this to exist FIRST. The review's plan
// puts the state promotion in step 2, after extracting the basemap-download
// block — but that block is where `MAP`, `FEATURE_BY_ID`, `COUNTRY_STATE`,
// `BOOT_DATE_KEY` and `AUTOZOOM` are declared, so extracting it first would
// take the state out of the file that still needs it and leave the remaining
// IIFEs reading bare identifiers that no longer exist.
window.snowdeskMapState = Object.freeze({
  /** @returns {maplibregl.Map|null} The map, or null before the style loads. */
  get map() {
    return MAP;
  },
  set map(value) {
    MAP = value;
  },

  /** @returns {Promise<void>} Resolves once the regions source is added. */
  get ready() {
    return MAP_READY_PROMISE;
  },

  /** @returns {Object} MapLibre feature id → feature, by region id. */
  get featureById() {
    return FEATURE_BY_ID;
  },
  /** @returns {Object} EAWS region id → feature. */
  get featureByRegionId() {
    return FEATURE_BY_REGION_ID;
  },

  /** @returns {Object} Country code → whether its regions are shown. */
  get countryState() {
    return COUNTRY_STATE;
  },

  /** @returns {string|null} The clamped boot date, min(today, seasonEnd). */
  get bootDateKey() {
    return BOOT_DATE_KEY;
  },
  set bootDateKey(value) {
    BOOT_DATE_KEY = value;
  },

  /** @returns {boolean} Whether a region click auto-pans to fit. */
  get autozoom() {
    return AUTOZOOM;
  },
  set autozoom(value) {
    AUTOZOOM = value;
  },

  /** @returns {boolean} Whether timelapse playback is running. */
  get isPlaying() {
    return IS_PLAYING;
  },
  set isPlaying(value) {
    IS_PLAYING = value;
  },
});

// Wire-format int → rating string. Inverse of public/api.py::_RATING_TO_INT.
// Hoisted so the timelapse and the scrubber share one definition.
const INT_TO_RATING = ['no_rating', 'low', 'moderate', 'considerable', 'high', 'very_high'];

// Canonical ISO date key (YYYY-MM-DD). Used to validate ``?d=`` params and
// hash-carried dates before they're handed to Date.parse or a URL builder.
const DATE_KEY_RE = /^\d{4}-\d{2}-\d{2}$/;

// Read the ``?d=YYYY-MM-DD`` query param from the current URL. Returns the
// validated string or null when the param is missing or malformed. Hoisted so
// every IIFE that reads it shares one definition of "valid".
const readUrlDateParam = () => {
  const d = new URL(location.href).searchParams.get('d');
  return d && DATE_KEY_RE.test(d) ? d : null;
};

// localStorage guarded by try/catch — private mode / disabled storage / quota
// throws are all silently swallowed. Boolean-typed values persist as the
// strings 'true' / 'false' (readBoolStorage handles the coercion).
const readStorage = (key) => {
  try { return localStorage.getItem(key); }
  catch (_) { return null; }
};
const writeStorage = (key, value) => {
  try { localStorage.setItem(key, value); }
  catch (_) { /* private mode — choice still applies for this session */ }
};
const readBoolStorage = (key, dflt) => {
  const v = readStorage(key);
  return v === null ? dflt : v === 'true';
};

// ``SCRUBBER_MONTHS`` / ``formatDateLong`` ("2026-04-25" → "APR 25 2026")
// went with mapDatePillInit below — that pill was their only caller.
// POPUP_MONTHS / formatDatePopup are unrelated and still live.

// SNOW-318: "2026-04-08" → "8 Apr 2026" — day-first, title-case 3-letter month.
// This mirrors the popup card's server render, where
// _region_tooltip.html formats the date with ``date:"j M Y"``, so the bulletin
// label reads identically whether the popup was just opened (server-rendered)
// or relabelled in place on a scrubber date change.
const POPUP_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const formatDatePopup = (dateKey) => {
  const [y, m, d] = dateKey.split('-');
  return `${parseInt(d, 10)} ${POPUP_MONTHS[parseInt(m, 10) - 1]} ${y}`;
};

// SNOW-419: ISO timestamp -> coarse relative-time string ("2 h ago",
// "12 min ago", "just now") for the community-reports popup. Deliberately
// coarse (minutes/hours only, no seconds) to match the server's own
// 15-minute truncation of observed_at — the popup shouldn't imply more
// precision than the wire payload actually carries.
const formatRelativeTime = (isoString) => {
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return '';
  const diffMinutes = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (diffMinutes < 1) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes} min ago`;
  return `${Math.round(diffMinutes / 60)} h ago`;
};

// Lazily-fetched, cached payload from /api/ratings/?country=ch. Shape:
// { date_iso: { region_id: rating_int } }. Both timelapse (SNOW-46) and
// the scrubber (SNOW-47) consume the same dataset; sharing one fetch
// keeps the payload off the wire twice.
// SNOW-239: RATINGS_URL replaces the legacy season-ratings and today-summaries
// URLs. The base URL is set once from data-ratings-url; each consumer appends
// its own query string (?d=...&country=... for cold-open, ?country=ch for the
// full-season scrubber/timelapse cache).
let RATINGS_URL = null;
let SEASON_RATINGS_PROMISE = null;

const getSeasonRatings = () => {
  if (SEASON_RATINGS_PROMISE !== null) return SEASON_RATINGS_PROMISE;
  if (!RATINGS_URL) {
    return Promise.reject(new Error('ratings URL not set'));
  }
  SEASON_RATINGS_PROMISE = fetch(RATINGS_URL + '?country=ch').then((resp) => {
    if (!resp.ok) throw new Error('ratings fetch failed');
    return resp.json();
  });
  return SEASON_RATINGS_PROMISE;
};

// SNOW-323: Per-date bulletin-groupings fetch from /api/bulletin-groupings.geojson.
// The endpoint is single-date (?d=YYYY-MM-DD) and returns one day's
// FeatureCollection — { type: "FeatureCollection", features: [...] }. Each
// response is memoised per date for the session, so re-visiting a date the user
// has already landed on costs no network. This replaces the former whole-season
// payload: once the historical backfill landed, serialising every day's
// dissolved geometry in one response pushed the web worker past its 512 MB
// limit. Fetching one settled day at a time keeps the response — and the
// server's peak memory — bounded regardless of how deep the archive grows.
let BULLETIN_GROUPINGS_URL_MODULE = null;
const BULLETIN_GROUPINGS_BY_DATE = new Map(); // dateKey -> Promise<FeatureCollection>
const EMPTY_FEATURE_COLLECTION = { type: 'FeatureCollection', features: [] };

const fetchBulletinGroupingsForDate = (dateKey) => {
  if (!dateKey) return Promise.reject(new Error('bulletin groupings: no date'));
  const cached = BULLETIN_GROUPINGS_BY_DATE.get(dateKey);
  if (cached) return cached;
  if (!BULLETIN_GROUPINGS_URL_MODULE) {
    return Promise.reject(new Error('bulletin groupings URL not set'));
  }
  // Fetch ALL countries (no ?country= filter) so cross-border ALBINA
  // bulletins — e.g. countries: ["AT", "IT"] with no "CH" — are present.
  // Per-country visibility is handled client-side by applyCountryFilters'
  // array-membership filter on the `countries` property, so a country toggle
  // never triggers a refetch. This deliberately differs from the L1/L2
  // overlays and getSeasonRatings, which are CH-scoped at the fetch.
  const sep = BULLETIN_GROUPINGS_URL_MODULE.includes('?') ? '&' : '?';
  const url = BULLETIN_GROUPINGS_URL_MODULE + sep + 'd=' + encodeURIComponent(dateKey);
  const promise = fetch(url)
    .then((resp) => {
      if (!resp.ok) throw new Error('bulletin groupings fetch failed');
      return resp.json();
    })
    .catch((err) => {
      // Don't poison the cache on failure — drop the entry so a later
      // settle on the same date can retry.
      BULLETIN_GROUPINGS_BY_DATE.delete(dateKey);
      throw err;
    });
  BULLETIN_GROUPINGS_BY_DATE.set(dateKey, promise);
  return promise;
};

// SNOW-623: the collaborators `choropleth_core.js`'s `paintRatingsFrame`
// needs, bound once. Keeping MapLibre on this side of the boundary is what
// lets the paint semantics be unit-tested — see that module's header for
// why the two semantics must stay distinct.
const choroplethDeps = () => ({
  featureById: FEATURE_BY_REGION_ID,
  intToRating: INT_TO_RATING,
  setRating: (featureId, rating) =>
    MAP.setFeatureState({ source: 'regions', id: featureId }, { rating }),
});

// Repaint every known region's choropleth fill for the supplied date.
// `clearMissing: true` — a region with no rating on this date must go back
// to no_rating, or the previously-scrubbed day's colour lingers on it.
const repaintRegionsForDate = (dateKey, cache) => {
  if (!MAP) return;
  self.pwaChoroplethCore.paintRatingsFrame(
    choroplethDeps(),
    (cache && cache[dateKey]) || {},
    { clearMissing: true },
  );
};

(function () {
  'use strict';

  // Debug mode. Activate with ?debug=1 in the URL, or press 'd' while
  // the page is focused. Exposes region IDs in the drawer and on the map.
  let DEBUG = new URLSearchParams(location.search).has('debug');

  const mapEl = document.getElementById('map');
  const REGIONS_URL         = mapEl.dataset.regionsUrl;
  const MAJOR_REGIONS_URL   = mapEl.dataset.majorRegionsUrl;
  const SUB_REGIONS_URL     = mapEl.dataset.subRegionsUrl;
  const RESORTS_GEOJSON_URL = mapEl.dataset.resortsGeojsonUrl;
  const RESORTS_URL       = mapEl.dataset.resortsUrl;
  // SNOW-323: Bulletin groupings URL — single-date endpoint (?d=YYYY-MM-DD).
  // Fetched one settled day at a time and memoised per date (see
  // fetchBulletinGroupingsForDate at module scope).
  const BULLETIN_GROUPINGS_URL = mapEl.dataset.bulletinGroupingsUrl || null;
  // SNOW-414: per-user favourites GeoJSON — only rendered when eligible
  // (flag active + authenticated); anonymous/ineligible requests must never
  // fetch this endpoint (it 403s, and there's nothing to show anyway).
  const FAVOURITES_URL = mapEl.dataset.favouritesUrl || null;
  const FAVOURITES_ELIGIBLE = mapEl.dataset.favouritesEligible === 'true';
  // SNOW-419: community-reports GeoJSON — public, anonymised data, so
  // "eligible" here means only "the flag is on" (no per-user auth gate,
  // unlike favourites).
  const COMMUNITY_REPORTS_URL = mapEl.dataset.communityReportsUrl || null;
  const COMMUNITY_REPORTS_ELIGIBLE = mapEl.dataset.communityReportsEligible === 'true';
  // Hoist to module scope so fetchBulletinGroupingsForDate() (defined before
  // the IIFE) can reach the URL that was read from the DOM here.
  BULLETIN_GROUPINGS_URL_MODULE = BULLETIN_GROUPINGS_URL;
  // SNOW-239: Hand the ratings URL to module scope so the timelapse and
  // scrubber IIFEs (defined further down in this file) can share one
  // full-season fetch via getSeasonRatings().
  RATINGS_URL = mapEl.dataset.ratingsUrl;
  // SNOW-318: The per-region summary URL template — the 'XX-0000' token is
  // string-replaced with the actual region id before each fetch. Django
  // renders the literal placeholder through {% url 'api:region_summary'
  // region_id='XX-0000' %} so the JS never has to reconstruct URL structure.
  const REGION_SUMMARY_URL_TEMPLATE = mapEl.dataset.regionSummaryUrl || '';
  // SNOW-499: The resort-pin popup URL template — the literal '0' resort_id
  // is string-replaced with the tapped resort's real id before each fetch.
  // Rendered via {% url 'api:resort_popup' resort_id=0 %}; public endpoint,
  // always present regardless of favourites eligibility.
  const RESORT_POPUP_URL_TEMPLATE = mapEl.dataset.resortPopupUrl || '';

  // SNOW-236: Clamp the cold-open boot date to the season end so the
  // choropleth paints at the last populated date after season end.
  // bootDateKey is hoisted to the outer IIFE scope so the country-toggle
  // paint path can reuse it without re-reading the DOM.
  const todayISO = new Date().toISOString().slice(0, 10);
  const seasonEndFromMap = mapEl.dataset.seasonEnd || todayISO;
  const bootDateKey = todayISO < seasonEndFromMap ? todayISO : seasonEndFromMap;
  // Expose to sibling IIFEs (seasonScrubberInit) via module scope.
  BOOT_DATE_KEY = bootDateKey;

  // SNOW-58: Basemap layer picker — resolve the active style URL.
  //
  // The catalogue is rendered server-side as an in-DOM <ul role="menu">
  // of menuitemradio buttons, each carrying ``data-basemap-key`` and
  // ``data-basemap-url``. The user's last choice is persisted under
  // localStorage[BASEMAP_STORAGE_KEY]; if it names a key still in the
  // catalogue we use it, otherwise we fall back to data-default-basemap-key
  // (env-resolved server-side from settings.BASEMAP). The popover wiring
  // lives in basemapPickerInit() at the bottom of this file; the
  // ``style.load`` handler inside the main IIFE re-installs the regions
  // source + layers when MAP.setStyle() loads a new style.
  const basemapMenu = document.getElementById('basemap-menu');
  const BASEMAP_OPTIONS = {};
  if (basemapMenu) {
    for (const btn of basemapMenu.querySelectorAll('.basemap-menu-item')) {
      BASEMAP_OPTIONS[btn.dataset.basemapKey] = btn.dataset.basemapUrl;
    }
  }
  const DEFAULT_BASEMAP_KEY = mapEl.dataset.defaultBasemapKey;
  const storedBasemapKey = readStorage(BASEMAP_STORAGE_KEY);
  const initialBasemapKey = (storedBasemapKey && BASEMAP_OPTIONS[storedBasemapKey])
    ? storedBasemapKey
    : DEFAULT_BASEMAP_KEY;
  const initialBasemapUrl = BASEMAP_OPTIONS[initialBasemapKey];
  // SNOW-483: true once the native basemap style has failed to load (offline)
  // and the inline fallback background (buildFallbackStyle, below) is active.
  // Shared with the ``online`` recovery listener and the ``styledata``
  // flag-clear, both registered after the Map is constructed.
  let basemapFallbackActive = false;
  // Mark the active radio so the popover renders in the right state on
  // first paint, before basemapPickerInit binds its click handlers.
  //
  // Selected POSITIVELY, by the data attribute that makes a row a basemap
  // radio. This used to exclude the SNOW-59 overlay checkboxes instead
  // (``:not(.basemap-menu-item--overlay)``), which was the same set for as
  // long as the menu held only radios and checkboxes — but SNOW-588 added a
  // third kind, a plain ``role="menuitem"`` action row, and exclusion
  // silently swept it in and gave it an ``aria-checked`` state that means
  // nothing on a menuitem. Overlay checkboxes still own their own state,
  // applied below from ``overlayState``.
  if (basemapMenu) {
    for (const btn of basemapMenu.querySelectorAll(
      '.basemap-menu-item[data-basemap-key]',
    )) {
      btn.setAttribute(
        'aria-checked',
        btn.dataset.basemapKey === initialBasemapKey ? 'true' : 'false',
      );
    }
  }

  // SNOW-484: tell the SW which cross-origin basemap origins are safe to
  // opportunistically cache (vector tiles, sprites, glyphs), so a
  // previously-browsed area still renders offline. A service worker has
  // no DOM, so it cannot read data-basemap-url itself (see
  // static/js/sw.js's 'register-basemap-origins' message handler) — this
  // is the one-way handoff. Every basemap in the picker is included, not
  // just the active one, so switching basemap mid-session is covered
  // too; the ``.basemap-menu-item`` selector above also matches the
  // overlay checkboxes, which carry no ``data-basemap-url``, hence the
  // truthy filter below.
  //
  // Guard on the truthy value, not ``'serviceWorker' in navigator``: the
  // e2e SW-stripping helpers define the property with ``value: undefined``
  // (so the key is present but the value is nullish), and dereferencing
  // ``navigator.serviceWorker.ready`` would throw and abort map init.
  if (navigator.serviceWorker) {
    const basemapOrigins = [
      ...new Set(
        Object.values(BASEMAP_OPTIONS)
          .filter((url) => typeof url === 'string' && url)
          .map((url) => new URL(url).origin),
      ),
    ];
    const registerBasemapOrigins = (registration) => {
      const target = registration && registration.active;
      if (target) {
        target.postMessage({ type: 'register-basemap-origins', origins: basemapOrigins });
      }
      // SNOW-487: also mirror the allowlist into the durable meta:app
      // store, so a service worker that gets idle-terminated and later
      // restarted for a fresh 'fetch' event (an empty in-memory
      // _basemapOrigins) can rehydrate it from IndexedDB instead of
      // wrongly falling back to network-only for a previously-cached
      // area. Best-effort and non-blocking, matching the same
      // window.pwaDb guard idiom as static/js/pwa_offline.js's
      // persistMeta() — must never throw or delay basemap registration
      // when IndexedDB is unavailable (private mode, Reset Required).
      if (window.pwaDb && typeof window.pwaDb.put === 'function') {
        try {
          window.pwaDb
            .put('meta:app', { key: 'basemap.origins', value: basemapOrigins })
            .catch(() => {});
        } catch (_err) {
          // Ignore — persistence is best-effort.
        }
      }
    };
    navigator.serviceWorker.ready.then(registerBasemapOrigins).catch(() => {});
    // .ready resolves once the registration has an *active* worker, but on
    // the very first visit that worker is not yet *controlling* this page
    // (it takes over on the next navigation). Re-send on controllerchange
    // so a freshly activated worker — the first-install case, and after any
    // update — learns the allowlist promptly, without a full page reload.
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      navigator.serviceWorker.getRegistration().then(registerBasemapOrigins).catch(() => {});
    });
  }

  // SNOW-59: EAWS region overlay layers — three tiers stacked above
  // the basemap. L1 (Major) and L2 (Sub) are outline-only line layers;
  // L4 (Micro) is the data-bearing choropleth and defaults to visible.
  // Visibility is user-driven via the basemap picker popover and
  // persisted in localStorage; the ``style.load`` handler re-applies
  // it after a basemap swap.
  // Keys: the module-scope OVERLAY_STORAGE_KEY above.
  // L4 defaults to visible: hiding it leaves only the basemap and any
  // active overlay tiers, which is intended. SNOW-78 resorts default off
  // so the map opens uncluttered.
  // SNOW-414: favourites defaults ON — a user's own saved pins should be
  // visible without an extra toggle-hunt, unlike resorts (a public dataset).
  // SNOW-419: community_reports defaults OFF — a shared layer of other
  // people's reports is an opt-in, unlike a user's own favourites.
  // SNOW-570: downloaded defaults OFF — the map already carries the
  // choropleth, the selection ring, the region tiers and the pins, and a
  // permanent extra outline over all of that is crowding for an answer most
  // sessions never ask.
  const overlayState = {
    l1: false, l2: false, l4: true, resorts: false,
    favourites: true, community_reports: false, downloaded: false,
  };

  // The bulletin-boundary layer (internal key ``l3``) is not an overlay the
  // user toggles — it is a companion to the micro-region tier, drawn whenever
  // L4 is drawn. It keeps its own key for the lazy-load machinery (its data is
  // per-date and fetched separately from the region geometry), but its
  // visibility is governed by L4's state rather than its own. This maps an
  // overlay key to the key that governs it; a key absent here governs itself.
  //
  // Rationale: the boundary answers "which of these regions share one
  // bulletin?", which is only a meaningful question while the regions it
  // subdivides are on screen. Shown alone it is a set of outlines around
  // nothing; hidden while L4 is on, the choropleth implies each micro-region
  // was judged independently when most were not.
  const OVERLAY_VISIBILITY_GOVERNOR = { l3: 'l4' };
  const governorFor = (key) => OVERLAY_VISIBILITY_GOVERNOR[key] || key;

  // SNOW-473: this seed is re-run inside the ``styledata`` handler after a
  // basemap swap (search "SNOW-473") — keep the two blocks in sync when adding
  // an overlay key.
  for (const key of ['l1', 'l2', 'resorts', 'community_reports', 'downloaded']) {
    overlayState[key] = readBoolStorage(OVERLAY_STORAGE_KEY[key], false);
  }
  overlayState.l4 = readBoolStorage(OVERLAY_STORAGE_KEY.l4, true);
  overlayState.favourites = readBoolStorage(OVERLAY_STORAGE_KEY.favourites, true);

  // SNOW-172: Country toggle state — which country's geometry is shown.
  // Default: CH on, others off. Each key maps to a boolean (visible/hidden).
  // Persisted in localStorage under snowdesk.map.overlay.country.<code>.
  const COUNTRY_KEYS = ['ch', 'fr', 'at', 'it'];
  const COUNTRY_STORAGE_KEY = (code) => `snowdesk.map.overlay.country.${code}`;
  const countryState = { ch: true, fr: false, at: false, it: false };
  for (const code of COUNTRY_KEYS) {
    countryState[code] = readBoolStorage(COUNTRY_STORAGE_KEY(code), countryState[code]);
  }
  // SNOW-236: Mirror the initial state into the module-scope COUNTRY_STATE
  // so the scrubber IIFE can read it for country-aware effective-last computation.
  Object.assign(COUNTRY_STATE, countryState);
  // loadedCountries tracks which countries' GeoJSON has been fetched already
  // so we don't re-fetch on each toggle-on.
  const loadedCountries = new Set();

  // SNOW-63: restore auto-zoom preference from localStorage.
  AUTOZOOM = readBoolStorage(AUTOZOOM_STORAGE_KEY, false);
  // Reflect the persisted overlay state on first paint so the popover
  // matches reality before the click handler at the bottom of the file
  // takes over.
  if (basemapMenu) {
    for (const btn of basemapMenu.querySelectorAll(
      '.basemap-menu-item--overlay',
    )) {
      const key = btn.dataset.overlayKey;
      // SNOW-172: country toggle buttons use countryState, not overlayState.
      let checked;
      if (key && key.startsWith('country.')) {
        const code = key.slice(8);
        checked = countryState[code];
      } else {
        checked = overlayState[key];
      }
      btn.setAttribute('aria-checked', checked ? 'true' : 'false');
    }
  }

  const RESORTS_BY_REGION  = {};

  const RATING_COLOURS = {
    low:          '#ccff66',
    moderate:     '#ffff00',
    considerable: '#ff9900',
    high:         '#ff0000',
    very_high:    '#a500a5',
    no_rating:    '#e0e0e0',
  };

  // Basemap style JSON URL is resolved above from settings.BASEMAP_STYLES
  // × localStorage × the env-resolved default. The picker (SNOW-58) lets
  // the user pick at runtime via MAP.setStyle(); the style.load handler
  // registered inside map.on('load') re-installs our source + layers
  // when the new style finishes loading.
  //
  // Initial view is framed via `bounds` around Switzerland rather than a
  // hand-tuned center/zoom pair — `bounds` adapts to viewport aspect
  // ratio automatically, which matters now that SNOW-35 made the map
  // full-bleed (previously the frame was a fixed 390px phone mock).
  const map = new maplibregl.Map({
    container: 'map',
    // ESRI basemaps (see resolveBasemapStyle) can't be handed to the
    // constructor synchronously — boot them with an empty style and swap
    // the fetched+rewritten style in once it resolves (below). Native
    // basemaps load directly from their URL.
    style: ESRI_BASEMAP_KEYS.has(initialBasemapKey)
      ? { version: 8, sources: {}, layers: [] }
      : initialBasemapUrl,
    bounds: [[5.9, 45.8], [10.5, 47.9]],
    fitBoundsOptions: { padding: 20 },
    minZoom: 4,
    // SNOW-442: raised from 12. The swisstopo base vector source
    // (ch.swisstopo.base.vt) publishes a TileJSON `maxzoom` of 14 and its
    // style authors layers up to zoom 20; MapLibre overzooms vector tiles
    // cleanly above 14, so 18 was chosen to allow close-in reading without
    // exposing genuinely blank overzoomed tiles at the extreme end.
    maxZoom: 18,
    // (Bounds taken from console.log when ?debug=true)
    // West / south / north match the original Western-European frame
    // (Atlantic buffer / French Alps min lat / Stuttgart-ish top). East
    // extended from 17° to 23° to cover the full Austrian / Slovenian /
    // northern-Balkan arc visible in the avalanche-region polygons.
    maxBounds: [[0.9482, 41.9952], [19.6674, 49.9983]],
    // SNOW-230: attribution moved to top-right so the scrubber can sit
    // flush at the bottom edge. Disable the default bottom-right slot and
    // add it explicitly at the desired corner after the Map is constructed.
    attributionControl: false,
  });
  // Expose for sibling IIFEs (timelapse, season scrubber). FEATURE_BY_ID
  // and FEATURE_BY_REGION_ID are at module scope and get populated below.
  MAP = map;

  // SNOW-483: degrade gracefully when the basemap style JSON can't be
  // fetched — offline, the SW treats the third-party style URL as
  // network-only, so the fetch fails, MapLibre never fires ``load``, and
  // every overlay installed inside ``map.on('load')`` below is skipped,
  // leaving a blank canvas even though the regions GeoJSON and ratings are
  // both SW-cached and fetch fine offline. MapLibre emits ``error`` for many
  // benign reasons too (tile/glyph 404s, SNOW-478), so the gate is
  // ``!map.isStyleLoaded()``: no style is currently up, so the error means
  // "there is no usable style right now" rather than "a loaded style had a
  // benign hiccup". That window is (rarely) also open during a user-driven
  // basemap swap via the picker (basemapPickerInit, further down this
  // file) — if that swap's style genuinely 404s, this engages the fallback
  // rather than leaving the previous basemap in place. Accepted as unlikely
  // (CDN-hosted basemap styles/sprites don't 404 in practice) rather than
  // worth a structural exclusion.
  //
  // Two distinct situations share this handler:
  //  - cold boot: the initial style URL fails before anything has loaded.
  //  - failed recovery: the ``online`` listener below retries the real
  //    basemap via ``map.setStyle(url)``. ``resolveBasemapStyle`` resolves
  //    immediately for a native (non-ESRI) basemap — it never rejects, it
  //    just hands the URL straight to ``setStyle`` — so if that URL then
  //    fails to load (still offline) with no style currently active, this
  //    reinstates the fallback rather than leaving the map with nothing to
  //    render until the *next* ``online`` event.
  // Re-engaging is idempotent and loop-safe: ``buildFallbackStyle`` returns
  // a synchronous inline style with no external fetch, so it becomes
  // "loaded" immediately and every subsequent benign error returns at the
  // ``isStyleLoaded()`` guard above.
  //
  // SNOW-492: the ``!isStyleLoaded()`` guard alone is too broad — it's
  // transiently ``false`` mid-zoom while tiles are in flight, and an
  // offline zoom to an uncached tile resolves as an HTTP 504 from the SW's
  // ``_basemapStaleWhileRevalidate`` (sw.js), which MapLibre reports as a
  // benign, source-scoped ``error`` (it overzooms/retries on its own). That
  // was wrongly swapping in the empty fallback style permanently. MapLibre
  // 4.7.1 merges the firing source's evented-parent data — ``sourceId`` — up
  // through Style to Map for every tile/source error (see
  // ``Style.addSource``'s ``setEventedParent(style, {..., sourceId})``);
  // tile-load failures also carry ``tile`` directly. A genuine
  // style-document load failure (``Style.loadURL``'s catch, the cold-boot
  // and failed-recovery cases this fallback exists for) fires on the Style
  // object itself, whose evented-parent data is only ``{style}`` — never
  // ``sourceId``/``tile``. So checking for either first, before the
  // ``isStyleLoaded()`` guard, filters out the benign case without
  // masking a real style failure.
  map.on('error', (e) => {
    if (e && (e.sourceId || e.tile)) return;
    if (map.isStyleLoaded()) return;
    basemapFallbackActive = true;
    map.setStyle(buildFallbackStyle());
  });

  // SNOW-483: only a real style load clears the fallback flag — a native
  // ``setStyle(url)`` retry that still fails offline won't reject its
  // internal fetch, so ``styledata`` (which fires for every style change,
  // including a failed one that never becomes "loaded") plus the sentinel
  // ``name`` check is what tells a real basemap apart from a retry that's
  // still degraded. Coexists with the SNOW-473 ``styledata`` listener
  // registered inside ``map.on('load')`` below — multiple listeners are fine.
  map.on('styledata', () => {
    if (!basemapFallbackActive) return;
    if (!map.isStyleLoaded()) return;
    if (map.getStyle()?.name === 'snowdesk-offline-fallback') return;
    basemapFallbackActive = false;
  });

  // SNOW-483: retry the real basemap once connectivity returns. Reuses the
  // ESRI-aware resolver (resolveBasemapStyle) so a reconnect while an ESRI
  // basemap is selected re-fetches and rewrites it exactly as boot does; the
  // ``.catch`` swallows a still-failing fetch so we stay degraded and simply
  // retry on the next ``online`` event.
  window.addEventListener('online', () => {
    if (!basemapFallbackActive) return;
    resolveBasemapStyle(initialBasemapKey, initialBasemapUrl)
      .then((style) => map.setStyle(style))
      .catch(() => {});
  });

  // SNOW-445: the on-map zoom pill was a debug artefact and has been removed.
  // Expose the live camera on the console instead — window.snowdeskMap is the
  // MapLibre instance, with a convenience read-only `zoom_level` getter so a
  // developer can just read `snowdeskMap.zoom_level` (equivalent to
  // `snowdeskMap.getZoom()`). Not used by any user-facing code.
  Object.defineProperty(map, 'zoom_level', {
    get() {
      return map.getZoom();
    },
  });
  window.snowdeskMap = map;

  // Swap in the resolved ESRI style once fetched (no-op for native
  // basemaps, which already loaded from their URL in the constructor).
  if (ESRI_BASEMAP_KEYS.has(initialBasemapKey)) {
    resolveBasemapStyle(initialBasemapKey, initialBasemapUrl)
      .then((style) => map.setStyle(style));
  }

  // SNOW-230: render tile attribution inside the unified map-info panel
  // rather than as a separate MapLibre corner control. We walk the active
  // style's sources and join their unique attribution strings, refreshing
  // on every basemap swap so the panel always reflects the current tile
  // provider. MapLibre's own attribution control is disabled via
  // ``attributionControl: false`` on the Map constructor above.
  const attributionTarget = document.getElementById('map-attribution-text');
  // SNOW-640: the whole section, so an empty union collapses the heading
  // along with the text instead of leaving "Map data" over a blank line.
  const attributionSection = document.getElementById('map-attribution-section');

  // SNOW-614: the source-id list for the current style, and the last string
  // written to the panel.
  //
  // ``sourcedata`` fires for every tile of every source — hundreds of times
  // during a pan — and this handler ran ``getStyle()`` (which serialises
  // the whole style object) and then an ``innerHTML`` write on each one,
  // for a string that only ever changes on a basemap swap. The ids come
  // from the style, so they are re-read on ``style.load`` and cached
  // between; the string is compared before the write, so the parse-and-
  // reflow only happens when the text actually differs.
  /** @type {string[]|null} */
  let attributionSourceIds = null;
  let attributionHtml = null;

  const updateMapAttribution = () => {
    if (!attributionTarget) return;
    if (!attributionSourceIds) {
      const style = map.getStyle && map.getStyle();
      if (!style || !style.sources) return;
      attributionSourceIds = Object.keys(style.sources);
    }
    const seen = new Set();
    // ``getStyle().sources`` returns the static style config, which does
    // not include the attribution string for tilejson-backed sources —
    // that arrives on the runtime ``Source`` instance after the tilejson
    // resolves. ``map.getSource(id)`` is the public path to that
    // instance, mirroring what MapLibre's own AttributionControl uses.
    // That is also why the ids can be cached but the lookup cannot: the
    // set of ids is fixed by the style, the strings on them are not.
    for (const id of attributionSourceIds) {
      const src = map.getSource(id);
      if (src && src.attribution) seen.add(src.attribution);
    }
    const html = Array.from(seen).join(' &middot; ');
    if (html === attributionHtml) return;
    attributionHtml = html;
    // Source attribution strings carry trusted HTML (provider links) — we
    // assign innerHTML rather than textContent so the same anchors that
    // MapLibre's stock AttributionControl renders stay clickable. The
    // basemap URLs are server-controlled, so the trust boundary matches.
    attributionTarget.innerHTML = html;

    // SNOW-640: no source carried an attribution, so there is nothing to
    // put under the heading. Collapse the section rather than paint an
    // empty box — which is what staging showed, because the self-hosted
    // style rewrite drops the TileJSON `url` and every field that only
    // lived there, attribution included (the same line SNOW-604 caught
    // taking minzoom/maxzoom with it).
    //
    // Collapsing is the honest presentation of "we have nothing", NOT the
    // fix: OpenFreeMap serves OSM under ODbL, which requires attribution,
    // so an empty union on the default basemap is a licence problem to be
    // fixed where the style is served (`rewrite_style.py` in the
    // snowdesk-tiles repo, with a matching check in its `verify.sh`). The
    // warning is here so collapsing the section cannot quietly hide that
    // from whoever is looking at the page — a missing panel is easier to
    // overlook than an empty one, which is exactly the risk this branch
    // introduces.
    //
    // No fallback string is invented here on purpose: the correct credit
    // depends on which basemap is active (five are offered, three of them
    // national services), and a wrong attribution is worse than none.
    if (attributionSection) attributionSection.hidden = !html;
    if (!html) {
      console.warn(
        '[map] SNOW-640: no source in the active style carries an ' +
          'attribution — the "Map data" section is hidden. If this is the ' +
          'default basemap, the served style is missing an ODbL-required ' +
          'credit and needs fixing at the origin.',
      );
    }
  };
  map.on('sourcedata', updateMapAttribution);
  map.on('style.load', () => {
    // A new style means a new source set and, usually, a new provider —
    // both caches have to go, or the panel would keep naming the basemap
    // the user just switched away from.
    attributionSourceIds = null;
    attributionHtml = null;
    updateMapAttribution();
  });

  // SNOW-68: log zoom level on each zoom gesture when debug mode is active.
  // Also logs visible bounds on every move (zoom or pan) so the current
  // viewport can be lifted straight into a `maxBounds` config.
  const logViewport = (label) => {
    if (!DEBUG) return;
    const b = map.getBounds();
    const fmt = (n) => n.toFixed(4);
    console.log(
      `[map] ${label} zoom:`, map.getZoom().toFixed(2),
      'bounds:', `[[${fmt(b.getWest())}, ${fmt(b.getSouth())}], [${fmt(b.getEast())}, ${fmt(b.getNorth())}]]`,
    );
  };
  map.on('zoomend', () => logViewport('zoomend'));
  map.on('moveend', () => logViewport('moveend'));

  // In-memory lookup from numeric feature id -> region properties.
  // Numeric because setFeatureState requires a numeric (or numeric-coerceable) id.
  const REGION_LOOKUP = {};

  // SNOW-445: single-point location markers (favourite pins and community
  // observation pins/clusters) must always render ABOVE every other layer —
  // never behind a region fill, an overlay polygon (e.g. bulletin groupings),
  // or a basemap label. MapLibre paints layers in insertion order, and these
  // overlays are lazy-installed at unpredictable times (a polygon overlay
  // toggled on after the pins already exist, or the whole style re-added after
  // a basemap swap), so without this the pins can end up buried. Every install
  // path calls raiseMarkerLayers() at its end to lift the pins back to the top.
  // Listed lowest -> highest: moveLayer() with no beforeId moves a layer to the
  // very top, so the last id in this list ends up topmost.
  const ALWAYS_ON_TOP_MARKER_LAYERS = [
    'community-reports-clusters',
    'community-reports-cluster-count',
    'community-reports-point',
    'favourites-label',
    'favourites-pin',
  ];
  const raiseMarkerLayers = () => {
    for (const id of ALWAYS_ON_TOP_MARKER_LAYERS) {
      if (map.getLayer(id)) map.moveLayer(id);
    }
  };

  // SNOW-58: source + layer install, factored out so it can be re-applied
  // after MAP.setStyle() wipes the style. Idempotent — refuses to re-add
  // if the source is still around (defensive, MapLibre normally drops
  // sources during setStyle but this lets a future ``diff`` setStyle
  // strategy land without breaking us).
  // SNOW-54: solid fill for permanently-uncovered regions (those the pipeline
  // never rates). A touch darker than no_rating (#e0e0e0) so an uncovered area
  // reads as a distinct flat grey rather than "no bulletin today" — applied in
  // the regions-fill paint below, keyed on the static ``covered`` property.
  const UNCOVERED_FILL_COLOUR = '#b5b5b5';

  // SNOW-570/SNOW-587: the cached-tiles overlay — one square per tile
  // actually in the pinned cache. Fainter than a download's live grid —
  // this is ambient state the user can leave switched on, not transient
  // feedback demanding attention. Drawn at the band's detail floor, the
  // same zoom the download grid uses, so the two describe the same
  // squares.
  const CACHED_TILES_OPACITY = 0.22;
  const CACHED_TILES_LINE_OPACITY = 0.4;
  const CACHED_TILES_ZOOM = 14;

  // Green, matching the sync dots and the download roundel's fill — the
  // overlay is a view onto the same cache they report on. Resolved from
  // the stylesheet rather than hardcoded (the theme carries a lighter
  // green in dark mode); MapLibre paint values can't reference a CSS
  // variable, hence the read here.
  const DOWNLOADED_OUTLINE_COLOUR =
    getComputedStyle(document.documentElement).getPropertyValue('--color-sync-ok').trim() ||
    '#16a34a';

  // SNOW-478: the text-font every overlay symbol layer we add uses. MapLibre
  // resolves glyphs against the *active basemap style's* single ``glyphs`` URL,
  // so an overlay label can only use a font that basemap's glyph server serves.
  // Basemaps ship different fonts (openfreemap → Noto Sans; swisstopo →
  // Frutiger Neue; IGN / basemap.at → their own), so a hardcoded font 404s on
  // every basemap that doesn't host it — the bug this fixes. Instead the font is
  // derived from the active basemap at style-load time (see deriveOverlayTextFont)
  // so overlay labels both resolve against the current glyph server AND match the
  // basemap's own typography. Seeded to the Noto Sans fallback; overwritten
  // before the first install on every style by the load / styledata handlers.
  const FALLBACK_TEXT_FONT = ['Noto Sans Regular'];
  let overlayTextFont = FALLBACK_TEXT_FONT;

  // SNOW-478: pick a font stack the active basemap style itself declares, so it
  // is guaranteed served by that basemap's glyph server. Walks every ``symbol``
  // layer's ``text-font`` and tallies the concrete font stacks it references —
  // both the common literal form (``["Frutiger Neue Condensed Regular"]``) and
  // the ``["literal", [...]]`` stacks nested inside a data-driven expression
  // (e.g. swisstopo's ``["match", ["get","class"], …]`` label fonts). Only a
  // ``literal``'s argument is treated as a stack, never an arbitrary all-string
  // array, so expression operands like ``["get","class"]`` are not misread. The
  // most-used stack (the basemap's dominant label font) wins; ``FALLBACK_TEXT_FONT``
  // only if the style declares none (openfreemap declares Noto Sans, so the
  // default basemap keeps its current look).
  //
  // Limitation: "most-used" assumes the modal font is a regular body/label
  // weight — true for all five current basemaps. A future basemap whose modal
  // font is a display/condensed variant would push that variant onto our
  // labels; harmless (it still resolves against the glyph server) but a style
  // mismatch worth revisiting if a new basemap trips it.
  const collectFontStacks = (node, out, topLevel) => {
    if (!Array.isArray(node)) return;
    if (topLevel && node.length && node.every((v) => typeof v === 'string')) {
      out.push(node);
      return;
    }
    if (
      node[0] === 'literal'
      && Array.isArray(node[1])
      && node[1].length
      && node[1].every((v) => typeof v === 'string')
    ) {
      out.push(node[1]);
      return;
    }
    for (const child of node) collectFontStacks(child, out, false);
  };
  const deriveOverlayTextFont = () => {
    let style;
    try {
      style = map.getStyle();
    } catch (_err) {
      return FALLBACK_TEXT_FONT;
    }
    const stacks = [];
    for (const layer of (style && style.layers) || []) {
      if (layer.type !== 'symbol') continue;
      const textFont = layer.layout && layer.layout['text-font'];
      if (textFont) collectFontStacks(textFont, stacks, true);
    }
    const counts = new Map();
    let best = null;
    let bestCount = 0;
    for (const stack of stacks) {
      const key = JSON.stringify(stack);
      const next = (counts.get(key) || 0) + 1;
      counts.set(key, next);
      if (next > bestCount) {
        best = stack;
        bestCount = next;
      }
    }
    return best || FALLBACK_TEXT_FONT;
  };

  const installRegionsLayers = (geojson) => {
    // Fully installed already — the choropleth fill layer is the sentinel,
    // not the source. A setStyle can (in some MapLibre paths) drop our
    // layers while leaving the 'regions' source behind; keying the guard on
    // the source alone then wrongly skips re-adding the layers, stranding
    // the micro-region overlay with no way back (the reported bug). Rebuild
    // from a lingering source-without-layers rather than early-returning.
    if (map.getLayer('regions-fill')) return;
    if (map.getSource('regions')) {
      for (const id of [
        'regions-fill', 'regions-line', 'regions-line-selected',
        // SNOW-570/SNOW-587: the cached-tiles overlay is installed below,
        // so it has to come off here — a re-install over a surviving
        // layer throws.
        'cached-tiles-fill', 'cached-tiles-line',
        'regions-label',
      ]) {
        if (map.getLayer(id)) map.removeLayer(id);
      }
      map.removeSource('regions');
    }
    map.addSource('regions', { type: 'geojson', data: geojson });

    // Fill layer — the choropleth.
    //
    // SNOW-239: colour is driven entirely via feature-state ``rating`` —
    // the coalesce(feature-state, properties) fallback has been removed.
    // Every region's rating is written via setFeatureState at boot and
    // on every scrubber/timelapse frame; unset state resolves to null,
    // which falls through the ``match`` arms to the default no_rating colour.
    //
    // SNOW-54: permanently-uncovered regions (static ``covered === false``
    // property from the API) get a distinct flat grey instead of no_rating.
    // The case checks ``covered`` first; these regions never carry a rating, so
    // ordering is safe and the rating ``match`` handles every covered region.
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      layout: {
        visibility: overlayState.l4 ? 'visible' : 'none',
      },
      paint: {
        'fill-color': [
          'case',
          ['==', ['get', 'covered'], false], UNCOVERED_FILL_COLOUR,
          [
            'match',
            ['feature-state', 'rating'],
            'low',          RATING_COLOURS.low,
            'moderate',     RATING_COLOURS.moderate,
            'considerable', RATING_COLOURS.considerable,
            'high',         RATING_COLOURS.high,
            'very_high',    RATING_COLOURS.very_high,
            RATING_COLOURS.no_rating,
          ],
        ],
        'fill-opacity': [
          'case',
          [
            'any',
            ['boolean', ['feature-state', 'selected'], false],
            ['boolean', ['feature-state', 'previewing'], false],
          ], 0.85,
          0.55,
        ],
      },
    });
    BASE_LAYER_FILTERS['regions-fill'] = map.getFilter('regions-fill') ?? null;

    // Outline — base unselected ring.
    //
    // SNOW-105: round joins and caps so the ring's start/end vertex doesn't
    // expose a butt-capped seam at high zoom — the visible "missing closing
    // edge" reported on every region past city zoom was that seam, not an
    // open ring (data is closed at every layer). The third interpolation
    // stop pins the width at 0.6 px past z9 so linear extrapolation doesn't
    // fade the line out to zero by z13.
    //
    // SNOW-174: the selected-state paint has been moved to a dedicated
    // ``regions-line-selected`` layer below. This lets us use a heavier,
    // blurred stroke without fighting interpolation nesting constraints, and
    // the dedicated layer stacks above this one in the layer order so it
    // always paints on top of the base ring.
    map.addLayer({
      id: 'regions-line',
      type: 'line',
      source: 'regions',
      layout: {
        visibility: overlayState.l4 ? 'visible' : 'none',
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': 'rgba(0,0,0,0.25)',
        // Zoom-interpolated width; third stop prevents linear extrapolation
        // from fading the line out past z9.
        'line-width': [
          'interpolate', ['linear'], ['zoom'],
          5,  1.2,
          9,  0.6,
          22, 0.6,
        ],
      },
    });
    BASE_LAYER_FILTERS['regions-line'] = map.getFilter('regions-line') ?? null;

    // SNOW-174: dedicated selection-emphasis layer. A separate layer beats
    // a case expression inside interpolate because MapLibre's style spec
    // prohibits feature-state expressions as interpolate stop values, and
    // a standalone layer lets us add line-blur (impossible inside a case).
    // Added immediately after regions-line so it sits above the base ring
    // but below the overlay tiers (sub-regions-line, major-regions-line).
    //
    // SNOW-172: MapLibre v4 rejects feature-state expressions inside layer
    // filters entirely — "feature-state data expressions are not supported
    // with filters."  Selection visibility is driven via line-opacity in
    // paint instead.  The layer renders all features but is transparent
    // (opacity 0) unless the feature-state 'selected' flag is true.  No
    // filter is set, so applyCountryFilters leaves this layer alone.
    map.addLayer({
      id: 'regions-line-selected',
      type: 'line',
      source: 'regions',
      layout: {
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': '#1a1a1a',
        'line-width': 4,
        // Soft halo so the outline reads against any choropleth fill colour.
        'line-blur': 0.5,
        // Show only selected features; opacity 0 hides unselected ones
        // without needing a filter (which cannot reference feature-state).
        'line-opacity': [
          'case',
          [
            'any',
            ['boolean', ['feature-state', 'selected'], false],
            ['boolean', ['feature-state', 'previewing'], false],
          ], 1, 0,
        ],
      },
    });
    // No BASE_LAYER_FILTERS entry for regions-line-selected: it has no filter
    // (selection is paint-driven), so applyCountryFilters skips it entirely.

    // SNOW-570/SNOW-587: "Available offline" — one square per tile actually
    // in the pinned cache, at the band's detail floor. Derived from the
    // cache ALONE — no stored record involved — so it cannot drift from
    // what is on disk: eviction, a basemap swap and Clear Site Data all
    // change the answer, and all of them show up here for free.
    //
    // The layer is installed whether or not the overlay is on — its
    // visibility is a layout property the picker flips, and building it
    // eagerly here means a style swap reinstalls it with everything else
    // rather than leaving the toggle pointing at a layer that isn't there.
    if (!map.getSource('cached-tiles')) {
      map.addSource('cached-tiles', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
    }
    map.addLayer({
      id: 'cached-tiles-fill',
      type: 'fill',
      source: 'cached-tiles',
      layout: { visibility: overlayState.downloaded ? 'visible' : 'none' },
      paint: {
        'fill-color': DOWNLOADED_OUTLINE_COLOUR,
        'fill-opacity': CACHED_TILES_OPACITY,
      },
    });
    map.addLayer({
      id: 'cached-tiles-line',
      type: 'line',
      source: 'cached-tiles',
      layout: {
        visibility: overlayState.downloaded ? 'visible' : 'none',
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': DOWNLOADED_OUTLINE_COLOUR,
        'line-width': 0.75,
        // Same reasoning as the download grid's own gridlines: a few
        // thousand sub-pixel outlines read as a mesh, so they fade out as
        // the squares shrink on screen.
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          CACHED_TILES_ZOOM + DOWNLOAD_PROGRESS_GRID_FADE_START,
          0,
          CACHED_TILES_ZOOM + DOWNLOAD_PROGRESS_GRID_FADE_END,
          CACHED_TILES_LINE_OPACITY,
        ],
      },
    });

    // Labels — only from zoom 8.5 up, to avoid clutter at country view.
    map.addLayer({
      id: 'regions-label',
      type: 'symbol',
      source: 'regions',
      minzoom: 8.5,
      layout: {
        visibility: overlayState.l4 ? 'visible' : 'none',
        'text-field': ['get', 'name'],
        'text-font': overlayTextFont,
        'text-size': 11,
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': '#2a2a2a',
        'text-halo-color': 'rgba(255,255,255,0.85)',
        'text-halo-width': 1.2,
      },
    });
    BASE_LAYER_FILTERS['regions-label'] = map.getFilter('regions-label') ?? null;
    raiseMarkerLayers();
  };

  // SNOW-59: install the L1 / L2 outline overlays plus their labels.
  //
  // Outline-only (no fill) so the L4 choropleth underneath stays
  // visible. Each tier also has a symbol layer; the three label tiers
  // (L1 / L2 / L4) hand off to each other based on map zoom so only
  // one set of names is ever painted at a time:
  //
  //   zoom 5  → 7    L1 (major) labels
  //   zoom 7  → 8.5  L2 (sub) labels
  //   zoom 8.5 → max L4 (regions-label, declared in installRegionsLayers)
  //
  // Outlines themselves don't band by zoom — once the user toggles a
  // tier on it stays drawn at all zooms. Only the labels rotate, which
  // keeps the map readable while the outlines preserve the spatial
  // hierarchy across zooms.
  //
  // Visibility on the line + label layers is controlled per-tier by
  // ``overlayState`` and applied at install time; toggle clicks call
  // ``setLayoutProperty`` on both layer ids.
  const installOverlayLayers = (majorGeojson, subGeojson) => {
    if (subGeojson && !map.getSource('sub-regions')) {
      map.addSource('sub-regions', { type: 'geojson', data: subGeojson });
      map.addLayer({
        id: 'sub-regions-line',
        type: 'line',
        source: 'sub-regions',
        layout: {
          visibility: overlayState.l2 ? 'visible' : 'none',
          // SNOW-105: rounded join/cap to hide the closing-vertex seam.
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': '#0c447c',
          'line-width': 1.4,
          'line-opacity': 0.9,
        },
      });
      BASE_LAYER_FILTERS['sub-regions-line'] = map.getFilter('sub-regions-line') ?? null;
      map.addLayer({
        id: 'sub-regions-label',
        type: 'symbol',
        source: 'sub-regions',
        minzoom: 7,
        maxzoom: 8.5,
        layout: {
          visibility: overlayState.l2 ? 'visible' : 'none',
          'text-field': ['get', 'name_en'],
          'text-font': overlayTextFont,
          'text-size': 12,
          'text-allow-overlap': false,
          'text-padding': 4,
        },
        paint: {
          'text-color': '#0c447c',
          'text-halo-color': 'rgba(255,255,255,0.92)',
          'text-halo-width': 1.4,
        },
      });
      BASE_LAYER_FILTERS['sub-regions-label'] = map.getFilter('sub-regions-label') ?? null;
    }
    if (majorGeojson && !map.getSource('major-regions')) {
      map.addSource('major-regions', { type: 'geojson', data: majorGeojson });
      map.addLayer({
        id: 'major-regions-line',
        type: 'line',
        source: 'major-regions',
        layout: {
          visibility: overlayState.l1 ? 'visible' : 'none',
          // SNOW-105: rounded join/cap to hide the closing-vertex seam.
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': '#7a1f1f',
          'line-width': 2.4,
          'line-opacity': 0.95,
        },
      });
      BASE_LAYER_FILTERS['major-regions-line'] = map.getFilter('major-regions-line') ?? null;
      map.addLayer({
        id: 'major-regions-label',
        type: 'symbol',
        source: 'major-regions',
        minzoom: 5,
        maxzoom: 7,
        layout: {
          visibility: overlayState.l1 ? 'visible' : 'none',
          'text-field': ['get', 'name_en'],
          'text-font': overlayTextFont,
          'text-size': 14,
          'text-allow-overlap': false,
          'text-padding': 6,
        },
        paint: {
          'text-color': '#7a1f1f',
          'text-halo-color': 'rgba(255,255,255,0.92)',
          'text-halo-width': 1.6,
        },
      });
      BASE_LAYER_FILTERS['major-regions-label'] = map.getFilter('major-regions-label') ?? null;
    }
    raiseMarkerLayers();
  };

  // SNOW-78: install the resorts pin layer. Filled circles above the L4
  // choropleth so the pins are readable against the colour fill, with a
  // zoom-banded label layer for resort names at higher zooms.
  //
  // Pin colour is a neutral dark token rather than an EAWS rating colour
  // so resort pins read as a separate layer of information rather than
  // implying a per-resort danger rating (which we don't have — pins
  // inherit their parent region's bulletin via click-through). Halo +
  // white stroke keep the pin readable on every basemap and rating fill.
  //
  // Visibility is owned by ``overlayState.resorts`` and applied at
  // install time; toggle clicks (handled in the basemap-picker IIFE)
  // call ``setLayoutProperty`` on the pin and label layer ids via
  // ``OVERLAY_LAYER_IDS.resorts``.
  const installResortsLayer = (geojson) => {
    if (!geojson || map.getSource('resorts')) return;
    map.addSource('resorts', { type: 'geojson', data: geojson });
    map.addLayer({
      id: 'resorts-pin',
      type: 'circle',
      source: 'resorts',
      layout: {
        visibility: overlayState.resorts ? 'visible' : 'none',
      },
      paint: {
        'circle-radius': [
          'interpolate', ['linear'], ['zoom'],
          5, 3,
          9, 5,
          12, 7,
        ],
        'circle-color': '#1a1a1a',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1.5,
        'circle-opacity': 0.95,
      },
    });
    // Resort labels read as a *quieter* layer than the region labels.
    // At the zooms they overlap, region names are the primary wayfinding
    // text (11 px, near-black), so resort labels go smaller (10 px),
    // muted grey, with widened letter-spacing so they read as
    // points-of-interest annotations rather than competing region names.
    // Raised minzoom (10) keeps them off-screen until the map is
    // genuinely zoomed in, avoiding mid-zoom clutter where region
    // labels are still doing the heavy lifting.
    map.addLayer({
      id: 'resorts-label',
      type: 'symbol',
      source: 'resorts',
      minzoom: 10,
      layout: {
        visibility: overlayState.resorts ? 'visible' : 'none',
        'text-field': ['get', 'name'],
        'text-font': overlayTextFont,
        'text-size': 10,
        'text-letter-spacing': 0.05,
        'text-allow-overlap': false,
        'text-offset': [0, 0.9],
        'text-anchor': 'top',
        'text-padding': 4,
      },
      paint: {
        'text-color': '#5a5a5a',
        'text-halo-color': 'rgba(255,255,255,0.95)',
        'text-halo-width': 1.4,
      },
    });
    // SNOW-499: snapshot each layer's pristine filter (both are unfiltered
    // at install time) so applyResortsFavouritedFilter can always compose
    // from this base rather than the filter it itself set on a previous
    // call — reading back the *current* filter would re-include an
    // exclusion that no longer applies once a resort is unfavourited.
    BASE_LAYER_FILTERS['resorts-pin'] = map.getFilter('resorts-pin') ?? null;
    BASE_LAYER_FILTERS['resorts-label'] = map.getFilter('resorts-label') ?? null;
    raiseMarkerLayers();
    // Re-apply in case favourites data (and therefore favouritedResortIds)
    // was already known before this layer installed.
    applyResortsFavouritedFilter();
  };

  // SNOW-499: ids of resorts the current user has already favourited —
  // hidden from the resorts-pin/resorts-label layers so a favourited
  // resort renders only as a favourite star (favourites-pin layer), never
  // as a plain resort dot as well. Recomputed from the favourites
  // FeatureCollection whenever it changes (installFavouritesLayer and the
  // snowdesk:favourites-changed handler).
  let favouritedResortIds = [];

  // SNOW-499: whether the favourites overlay is currently drawn. The resort
  // exclusion below is only justified while the favourite star is actually
  // visible to stand in for the hidden resort dot — with the favourites
  // overlay toggled off there is no star, so a favourited resort must fall
  // back to its plain resort dot rather than vanishing from the map
  // entirely. Reads the live layer state, so it is correct however the
  // caller reached here (boot, toggle on, toggle off).
  const favouritesLayerVisible = () =>
    !!map.getLayer('favourites-pin') &&
    map.getLayoutProperty('favourites-pin', 'visibility') !== 'none';

  // SNOW-499: apply (or clear) the resorts-layer exclusion filter for the
  // current favouritedResortIds. Composes with each layer's pristine base
  // filter (captured in installResortsLayer) rather than its current
  // filter, so repeated calls never accumulate a stale exclusion. The
  // exclusion is gated on the favourites overlay being visible (see
  // favouritesLayerVisible) so a favourited resort stays on the map as a
  // plain dot when its star is hidden.
  const applyResortsFavouritedFilter = () => {
    const exclusion = favouritedResortIds.length && favouritesLayerVisible()
      ? ['!', ['in', ['get', 'id'], ['literal', favouritedResortIds]]]
      : null;
    for (const layerId of ['resorts-pin', 'resorts-label']) {
      if (!map.getLayer(layerId)) continue;
      const base = BASE_LAYER_FILTERS[layerId] ?? null;
      const filter = exclusion
        ? (base ? ['all', base, exclusion] : exclusion)
        : base;
      map.setFilter(layerId, filter);
    }
  };

  // SNOW-499: recompute favouritedResortIds from a favourites
  // FeatureCollection and reapply the exclusion filter. Called whenever
  // favouritesGeojsonCache is set to a new authoritative payload.
  const syncFavouritedResortIds = (geojson) => {
    favouritedResortIds = [];
    if (geojson && Array.isArray(geojson.features)) {
      for (const feature of geojson.features) {
        const resortId = feature.properties && feature.properties.resort_id;
        if (resortId != null) favouritedResortIds.push(resortId);
      }
    }
    applyResortsFavouritedFilter();
  };

  // SNOW-478: the favourites pin is drawn as an SDF icon image, not a ``★``
  // text glyph. A text glyph is resolved from the active basemap's glyph
  // server, and that server may not carry the ``★`` codepoint (U+2605) —
  // swisstopo's does not, so the old glyph 404'd on every load. An icon image
  // is rendered from a canvas path here and never touches the glyph server, so
  // the star always paints regardless of basemap. Mirrors the SNOW-472
  // community-report flag icon: shared id, SDF mask recoloured per-layer via
  // ``icon-color``.
  const FAVOURITE_STAR_ICON_ID = 'favourite-star';

  // SNOW-478: Font Awesome Free v7.3.1 "star" (solid) path and its viewBox.
  // Licensed CC BY 4.0 (https://fontawesome.com/license/free), Copyright
  // 2026 Fonticons, Inc. — attribution retained here to satisfy the licence.
  // Registered ``sdf: true`` (below) so ``icon-color`` recolours the alpha mask
  // to the favourites blue, exactly as the old ``text-color`` did to the glyph.
  const FAVOURITE_STAR_VIEWBOX = { width: 576, height: 512 };
  const FAVOURITE_STAR_PATH =
    'M316.9 18C311.6 7 300.4 0 288.1 0s-23.4 7-28.8 18L195 150.3 51.4 171.5c-12 ' +
    '1.8-22 10.2-25.7 21.7s-.7 24.2 7.9 32.7L137.8 329 113.2 474.7c-2 12 3 24.2 ' +
    '12.9 31.3s23 8 33.8 2.3l128.3-68.5 128.3 68.5c10.8 5.7 23.9 4.9 33.8-2.3s14.9' +
    '-19.3 12.9-31.3L442.2 329 546.6 225.9c8.6-8.5 11.7-21.2 7.9-32.7s-13.7-19.9' +
    '-25.7-21.7L385.1 150.3 316.9 18z';

  // SNOW-478: build the favourites star as an SDF alpha mask, same technique as
  // buildCommunityReportFlagImageData — a synchronous canvas fill so the image
  // id exists before addLayer references it (an async map.loadImage would race).
  // The star is centred in a square ``size`` footprint so the default
  // ``icon-anchor: 'center'`` plants it on the feature coordinate, matching the
  // old centred ``★`` glyph.
  const buildFavouriteStarImageData = (pixelRatio) => {
    const size = 20;
    const canvas = document.createElement('canvas');
    canvas.width = size * pixelRatio;
    canvas.height = size * pixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.scale(pixelRatio, pixelRatio);
    const scale = Math.min(
      size / FAVOURITE_STAR_VIEWBOX.width,
      size / FAVOURITE_STAR_VIEWBOX.height,
    );
    ctx.translate(
      (size - FAVOURITE_STAR_VIEWBOX.width * scale) / 2,
      (size - FAVOURITE_STAR_VIEWBOX.height * scale) / 2,
    );
    ctx.scale(scale, scale);
    ctx.fillStyle = '#000000';
    ctx.fill(new Path2D(FAVOURITE_STAR_PATH));
    return ctx.getImageData(0, 0, canvas.width, canvas.height);
  };

  // SNOW-414: install the favourites (saved-pin) layer. Distinct colour + halo
  // from the resort circles so a user's own pins read as a different kind of
  // marker. Idempotent, like installResortsLayer — early-returns if the source
  // already exists so a snowdesk:favourites-changed refresh can call this safely
  // too. SNOW-478: the pin is an SDF icon image (registered here, guarded by
  // hasImage — addImage throws on a duplicate id, and setStyle wipes images on a
  // basemap swap so it must re-register in lockstep with the layer).
  const installFavouritesLayer = (geojson) => {
    if (!geojson || map.getSource('favourites')) return;
    if (!map.hasImage(FAVOURITE_STAR_ICON_ID)) {
      const pixelRatio = window.devicePixelRatio || 1;
      map.addImage(
        FAVOURITE_STAR_ICON_ID,
        buildFavouriteStarImageData(pixelRatio),
        { sdf: true, pixelRatio },
      );
    }
    favouritesGeojsonCache = geojson;
    // SNOW-499: keep the resorts-layer exclusion filter in sync with
    // whatever favourites data this install carries.
    syncFavouritedResortIds(geojson);
    map.addSource('favourites', { type: 'geojson', data: geojson });
    map.addLayer({
      id: 'favourites-pin',
      type: 'symbol',
      source: 'favourites',
      layout: {
        visibility: overlayState.favourites ? 'visible' : 'none',
        'icon-image': FAVOURITE_STAR_ICON_ID,
        'icon-size': 1,
        'icon-allow-overlap': true,
      },
      paint: {
        // Snowdesk link/brand blue — MapLibre paint props can't reference the
        // CSS ``@theme`` tokens, so the star icon and its label carry the hex
        // literal directly (kept in sync with --color-link by hand).
        'icon-color': '#1a73e8',
        'icon-halo-color': 'rgba(255,255,255,0.95)',
        'icon-halo-width': 1.6,
        // SNOW-479: an offline-created pin not yet synced carries
        // ``properties.pending: true`` and renders at half opacity so it reads
        // as provisional; server pins (no ``pending``) stay fully opaque.
        // SNOW-478: the pin is now an SDF icon, so this is icon-opacity (was
        // text-opacity when the star was a glyph).
        'icon-opacity': ['case', ['==', ['get', 'pending'], true], 0.5, 1],
      },
    });
    // Favourite labels — zoom-banded like the resort labels, but shown a
    // touch earlier (minzoom 8) since there are far fewer of them per user.
    map.addLayer({
      id: 'favourites-label',
      type: 'symbol',
      source: 'favourites',
      minzoom: 8,
      layout: {
        visibility: overlayState.favourites ? 'visible' : 'none',
        'text-field': ['get', 'name'],
        'text-font': overlayTextFont,
        'text-size': 11,
        'text-allow-overlap': false,
        'text-offset': [0, 1.1],
        'text-anchor': 'top',
        'text-padding': 4,
      },
      paint: {
        'text-color': '#1a73e8',
        'text-halo-color': 'rgba(255,255,255,0.95)',
        'text-halo-width': 1.4,
      },
    });
    raiseMarkerLayers();
  };

  // SNOW-472: shared flag-icon id for every unclustered community-report
  // pin, regardless of OBSERVATION_TYPE. Replaced the earlier SNOW-419
  // per-type text glyphs (a unicode zoo that read as inconsistent
  // weight/size across types) with one SDF flag icon — see
  // buildCommunityReportFlagImageData for the canvas build + registration.
  const COMMUNITY_REPORT_ICON_ID = 'community-report-flag';

  // SNOW-472: Font Awesome Free v7.3.1 "flag" (solid) path and its viewBox.
  // Licensed CC BY 4.0 (https://fontawesome.com/license/free), Copyright
  // 2026 Fonticons, Inc. — attribution retained here to satisfy the licence.
  //
  // This is the solid fill: the outer pole-plus-cloth contour of Font
  // Awesome's flag glyph, with the regular variant's inner cloth cutout
  // (its second subpath) omitted so the cloth fills solid. A solid
  // silhouette reads boldly at the ~18px on-map icon size where the
  // outline variant washes out to thin strokes. Rendered as a single
  // colour because the icon is registered `sdf: true` (below): SDF discards
  // colour and keeps only the alpha mask, which is what lets `icon-color`
  // recolour the flag to the amber layer tint per-layer (a multi-colour
  // asset could not be an SDF).
  const COMMUNITY_REPORT_FLAG_VIEWBOX = { width: 448, height: 512 };
  const COMMUNITY_REPORT_FLAG_PATH =
    'M48 24C48 10.7 37.3 0 24 0S0 10.7 0 24L0 488c0 13.3 10.7 24 24 24s24-10.7 ' +
    '24-24l0-100 80.3-20.1c41.1-10.3 84.6-5.5 122.5 13.4 44.2 22.1 95.5 24.8 ' +
    '141.7 7.4l34.7-13c12.5-4.7 20.8-16.6 20.8-30l0-279.7c0-23-24.2-38-44.8-27.7' +
    'l-9.6 4.8c-46.3 23.2-100.8 23.2-147.1 0-35.1-17.6-75.4-22-113.5-12.5L48 52 ' +
    '48 24z';

  // SNOW-419: age-fade constants. A report's opacity decays linearly from
  // 1 (just filed) to a floor at the edge of the 48h server-side window,
  // so the overlay reads as a "heat" of recency rather than an
  // undifferentiated pile of pins.
  const COMMUNITY_REPORTS_WINDOW_MS = 48 * 60 * 60 * 1000;
  const COMMUNITY_REPORTS_MIN_OPACITY = 0.35;

  // SNOW-419: bake a per-feature `_ageOpacity` property into the fetched
  // FeatureCollection. MapLibre paint expressions have no "now" primitive,
  // so age-based fade can't be expressed as a live paint expression the
  // way e.g. zoom-based fades can — computing it once client-side against
  // Date.now() and reading it back via ['get', '_ageOpacity'] is the
  // simplest correct approach. Mutates and returns the same object.
  const withCommunityReportsAgeOpacity = (geojson) => {
    if (!geojson || !Array.isArray(geojson.features)) return geojson;
    const now = Date.now();
    for (const feature of geojson.features) {
      const observedAt = feature.properties && feature.properties.observed_at;
      const ageMs = observedAt ? now - new Date(observedAt).getTime() : 0;
      const fraction = Math.min(Math.max(ageMs / COMMUNITY_REPORTS_WINDOW_MS, 0), 1);
      feature.properties._ageOpacity = 1 - fraction * (1 - COMMUNITY_REPORTS_MIN_OPACITY);
    }
    return geojson;
  };

  // SNOW-492: the server only ever returns reports inside the 48h window,
  // so the live fetch path never needs to drop anything — the age-fade
  // above is the only "expiry" it applies. A cached (offline read-back)
  // copy can be older than the whole window has moved by the time it's
  // reinstalled, so filter out features whose `observed_at` has aged past
  // COMMUNITY_REPORTS_WINDOW_MS *before* re-applying the same age-opacity
  // fade, so cached reports expire visually at 48h exactly as they do
  // online rather than sitting at the opacity floor forever.
  const dropExpiredCommunityReports = (geojson) => {
    if (!geojson || !Array.isArray(geojson.features)) return geojson;
    const now = Date.now();
    const features = geojson.features.filter((feature) => {
      const observedAt = feature.properties && feature.properties.observed_at;
      if (!observedAt) return true;
      return now - new Date(observedAt).getTime() < COMMUNITY_REPORTS_WINDOW_MS;
    });
    return { ...geojson, features };
  };

  // SNOW-472: draw the shared community-report flag icon on an offscreen
  // canvas and hand MapLibre its alpha mask as an SDF image. The shape is
  // the Font Awesome "flag" path (COMMUNITY_REPORT_FLAG_PATH above), filled
  // in opaque black on a transparent canvas — the solid colour is
  // irrelevant since `sdf: true` reads only the alpha channel and recolours
  // it per-layer via `icon-color`. `Path2D` fills the SVG path string
  // synchronously (no `Image`/data-URI round-trip), which keeps the whole
  // build synchronous — an async decode would let `addLayer` run before the
  // image id exists (see installCommunityReportsLayer).
  //
  // `size` is the LOGICAL (CSS-pixel) icon footprint; the canvas backing
  // store is scaled up by `pixelRatio` so the mask supersamples cleanly
  // on Retina/HiDPI displays instead of blurring when MapLibre upscales
  // a 1x bitmap. `ctx.scale(pixelRatio, pixelRatio)` lets the drawing
  // calls below stay in logical-pixel coordinates regardless of ratio —
  // the caller passes the matching `pixelRatio` to `map.addImage` so
  // MapLibre knows how to map the (now larger) physical bitmap back to
  // the logical icon size.
  //
  // The 448x512 viewBox is fitted preserving aspect ratio, flush to the
  // left and bottom edges with a little top/right padding: the flag's pole
  // runs down the left edge to y=512, so drawing it flush-left-and-bottom
  // lets `icon-anchor: 'bottom-left'` on the layer plant the pole's base on
  // the feature coordinate ("the flag is here"), with the cloth flying up
  // and to the right the way a real planted flag does.
  const buildCommunityReportFlagImageData = (pixelRatio) => {
    const size = 34;
    const padTop = 2;
    const padRight = 3;
    const canvas = document.createElement('canvas');
    canvas.width = size * pixelRatio;
    canvas.height = size * pixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.scale(pixelRatio, pixelRatio);
    // Uniform scale that fits both the padded width and the padded height,
    // so neither the cloth (width) nor the pole (height) is clipped.
    const scale = Math.min(
      (size - padRight) / COMMUNITY_REPORT_FLAG_VIEWBOX.width,
      (size - padTop) / COMMUNITY_REPORT_FLAG_VIEWBOX.height,
    );
    // Bottom-align the pole base to the canvas bottom edge; keep it flush
    // left so 'bottom-left' anchoring lands on the pole rather than dead
    // space to its left.
    ctx.translate(0, size - COMMUNITY_REPORT_FLAG_VIEWBOX.height * scale);
    ctx.scale(scale, scale);
    ctx.fillStyle = '#000000';
    ctx.fill(new Path2D(COMMUNITY_REPORT_FLAG_PATH));
    return ctx.getImageData(0, 0, canvas.width, canvas.height);
  };

  // SNOW-419: install the community-reports (shared, anonymised) overlay.
  // Unlike favourites/resorts, this source is clustered (`cluster: true`)
  // — MapLibre computes clusters client-side from the fetched
  // FeatureCollection as the map zooms, so a busy region doesn't paint as
  // an unreadable pile of pins at low zoom. Three layers: cluster
  // circles + cluster-count labels (shown while `point_count` is present)
  // and an unclustered point layer (shown once a cluster has broken apart
  // enough that a feature stands alone). Idempotent, like
  // installFavouritesLayer/installResortsLayer — early-returns if the
  // source already exists, so the styledata re-install handler can call
  // this safely on every basemap swap.
  //
  // SNOW-472: `map.addImage` is registered here too, guarded by
  // `hasImage` (addImage, unlike addSource, throws on a duplicate id
  // rather than silently no-opping). This has to live inside this
  // function rather than run once at boot: `map.setStyle()` on a basemap
  // swap wipes every registered image along with sources and layers, and
  // this is the one function re-invoked on every fresh style (from the
  // `styledata` handler below) — so the icon re-registers in lockstep
  // with the layer that references it. The mask is built synchronously
  // from canvas paths (see buildCommunityReportFlagImageData) rather than
  // via `map.loadImage(url)` — an async fetch would let `addLayer` below
  // run before the image id exists, throwing.
  const installCommunityReportsLayer = (geojson) => {
    if (!geojson || map.getSource('community-reports')) return;
    if (!map.hasImage(COMMUNITY_REPORT_ICON_ID)) {
      const pixelRatio = window.devicePixelRatio || 1;
      map.addImage(
        COMMUNITY_REPORT_ICON_ID,
        buildCommunityReportFlagImageData(pixelRatio),
        { sdf: true, pixelRatio },
      );
    }
    map.addSource('community-reports', {
      type: 'geojson',
      data: geojson,
      cluster: true,
      clusterRadius: 50,
      clusterMaxZoom: 11,
    });
    map.addLayer({
      id: 'community-reports-clusters',
      type: 'circle',
      source: 'community-reports',
      filter: ['has', 'point_count'],
      layout: {
        visibility: overlayState.community_reports ? 'visible' : 'none',
      },
      paint: {
        // Amber — distinct from the favourites blue and the resorts
        // near-black so the three pin layers read as different kinds of
        // marker at a glance.
        'circle-color': '#e8711a',
        'circle-radius': [
          'step', ['get', 'point_count'],
          14, 5, 18, 20, 24,
        ],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1.5,
        'circle-opacity': 0.85,
      },
    });
    map.addLayer({
      id: 'community-reports-cluster-count',
      type: 'symbol',
      source: 'community-reports',
      filter: ['has', 'point_count'],
      layout: {
        visibility: overlayState.community_reports ? 'visible' : 'none',
        'text-field': ['get', 'point_count_abbreviated'],
        'text-font': overlayTextFont,
        'text-size': 12,
      },
      paint: {
        'text-color': '#ffffff',
      },
    });
    map.addLayer({
      id: 'community-reports-point',
      type: 'symbol',
      source: 'community-reports',
      filter: ['!', ['has', 'point_count']],
      layout: {
        visibility: overlayState.community_reports ? 'visible' : 'none',
        // SNOW-472: one shared flag icon for every OBSERVATION_TYPE —
        // replaced the earlier per-type text glyphs. The type itself is
        // still read from `props.type`/`type_label` by the click-popup
        // handler below; only the pin's visual mark is unified.
        'icon-image': COMMUNITY_REPORT_ICON_ID,
        'icon-size': 0.6,
        'icon-allow-overlap': true,
        // The mask is drawn flush left-and-bottom with the pole down the
        // left edge (see buildCommunityReportFlagImageData) — anchoring at
        // the pole's base (bottom-left) rather than the icon's visual centre
        // (the default) makes the feature's coordinate read as "the flag is
        // planted here", matching the usual pin affordance.
        'icon-anchor': 'bottom-left',
      },
      paint: {
        // Amber — same as the cluster circles, so a lone flag reads as
        // the same "kind" of marker as a broken-apart cluster.
        'icon-color': '#e8711a',
        // SNOW-419: age fade — baked into each feature by
        // withCommunityReportsAgeOpacity before install/setData.
        'icon-opacity': ['get', '_ageOpacity'],
      },
    });
    raiseMarkerLayers();
  };

  // SNOW-323: Install the bulletin-groupings source and line layer.
  // Idempotent — early-returns when the source already exists (called on
  // basemap swap via the styledata handler and on first l3 toggle).
  //
  // SNOW-533: solid, not dashed. MapLibre dash units are multiples of the
  // line width, so a fixed [4, 3] pattern renders at a different physical
  // scale at every zoom and fragments on short or convoluted boundary
  // segments. Distinctness now comes from weight and colour instead: the
  // boundary tracks 'regions-line''s zoom curve (the L4 micro-region ring it
  // groups) scaled so it is always the heavier of the two, and keeps its
  // green against L4's neutral black — at z9+ the two differ by 0.4px, which
  // weight alone can't carry. Inserted above 'regions-line-selected' so it
  // sits between the choropleth and the selection ring in the layer stack.
  //
  // Visibility is seeded from overlayState.l4 — the boundary is a companion
  // to the micro-region tier and has no state of its own (see
  // OVERLAY_VISIBILITY_GOVERNOR).
  // SNOW-323: the FC currently drawn into the bulletin-groupings source (kept
  // so the basemap-swap handler can re-install the layer without a refetch),
  // and whether that layer is currently showing data vs blanked for scrub.
  let currentGroupingsFC = null;
  let groupingsDrawn = false;
  const installBulletinGroupingsLayer = (featureCollection) => {
    if (map.getSource('bulletin-groupings')) return;
    const data = featureCollection || { type: 'FeatureCollection', features: [] };
    map.addSource('bulletin-groupings', { type: 'geojson', data });
    map.addLayer(
      {
        id: 'bulletin-groupings-line',
        type: 'line',
        source: 'bulletin-groupings',
        layout: {
          visibility: overlayState.l4 ? 'visible' : 'none',
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': '#1a6b3c',
          // Mirrors 'regions-line''s stops (1.2 → 0.6 → 0.6) at ~1.6x, so the
          // boundary reads as a slightly heavier version of the L4 ring at
          // every zoom rather than only at one. Third stop matches L4's in
          // pinning the width past z9 against linear extrapolation.
          'line-width': [
            'interpolate', ['linear'], ['zoom'],
            5,  2.0,
            9,  1.0,
            22, 1.0,
          ],
          // Down from 0.85: a solid line lays ~75% more ink than the [4, 3]
          // dash it replaces (4/7 duty), so the same alpha would read heavier
          // than before rather than subtler.
          'line-opacity': 0.7,
        },
      },
      // Insert above regions-line-selected so grouping boundaries sit
      // between the choropleth and the selection ring.
      'regions-line-selected',
    );
    BASE_LAYER_FILTERS['bulletin-groupings-line'] =
      map.getFilter('bulletin-groupings-line') ?? null;
    raiseMarkerLayers();
  };

  // SNOW-323: L3 boundaries are fetched one day at a time, lazily, and only
  // once the scrubber has *settled*. During playback or an active drag the
  // boundary is blanked so it neither thrashes the network (one fetch per
  // intermediate frame) nor lags a frame behind the choropleth. Rapid date
  // commits — play frames, keyboard repeat — are debounced into a single
  // fetch by GROUPINGS_SETTLE_MS of quiet.
  const GROUPINGS_SETTLE_MS = 250;
  let groupingsSettleTimer = null;

  // Hide the boundary immediately (without forgetting the last good FC, which
  // the basemap-swap handler still needs). No-op when already blank.
  const blankGroupings = () => {
    if (!groupingsDrawn) return;
    const src = map.getSource('bulletin-groupings');
    if (src) src.setData(EMPTY_FEATURE_COLLECTION);
    groupingsDrawn = false;
  };

  // Draw a FeatureCollection into the source and re-apply country filters so
  // the freshly-set data respects whichever countries are currently enabled.
  const drawGroupings = (featureCollection) => {
    const src = map.getSource('bulletin-groupings');
    if (!src) return;
    currentGroupingsFC = featureCollection || EMPTY_FEATURE_COLLECTION;
    src.setData(currentGroupingsFC);
    groupingsDrawn = true;
    applyCountryFilters();
  };

  // Blank now, then (after the scrubber settles) fetch and draw the boundary
  // for `dateKey`. A new call before the timer fires cancels the pending
  // fetch, so scrubbing/playback never draws an intermediate frame.
  const scheduleGroupingsForDate = (dateKey) => {
    if (groupingsSettleTimer) clearTimeout(groupingsSettleTimer);
    blankGroupings();
    if (!dateKey) return;
    groupingsSettleTimer = setTimeout(() => {
      groupingsSettleTimer = null;
      fetchBulletinGroupingsForDate(dateKey)
        .then((fc) => {
          // Guard against a slow fetch resolving after the user has moved
          // on: only draw if this is still the displayed date.
          if ((currentDisplayedDate || bootDateKey) === dateKey) drawGroupings(fc);
        })
        .catch(() => { /* leave the boundary blank on failure */ });
    }, GROUPINGS_SETTLE_MS);
  };

  // Cached at IIFE scope so the style.load handler (registered inside
  // map.on('load') below) can re-install layers without a refetch when
  // the user picks a new basemap.
  let geojsonCache = null;
  let majorGeojsonCache = null;
  let subGeojsonCache = null;
  let resortsGeojsonCache = null;
  // SNOW-479: the current favourites FeatureCollection, retained so an
  // offline-created pin can be appended optimistically (snowdesk:favourite-
  // pending) without a network refetch — which would fail offline. Kept in
  // sync wherever favourites data is set (installFavouritesLayer and the
  // snowdesk:favourites-changed refetch). Null until the first favourite.
  let favouritesGeojsonCache = null;
  // SNOW-419: retained so the styledata re-install handler can re-add the
  // community-reports layer after a basemap swap without a refetch.
  let communityReportsGeojsonCache = null;

  // SNOW-172: Snapshot of each layer's filter expression as set during
  // installRegionsLayers / installOverlayLayers.  applyCountryFilters
  // wraps these with an 'all' expression so the country filter composes
  // with — rather than overwrites — any pre-existing layer filter.
  const BASE_LAYER_FILTERS = {};

  // SNOW-172: Compute the MapLibre filter expression that shows only
  // enabled countries on all region layers.  Any layer that was given a
  // filter at install time has its base filter preserved by composing
  // ['all', baseFilter, countryFilter]; layers with no base filter
  // receive the country filter alone.
  //
  // Note: 'regions-line-selected' is intentionally excluded from this list.
  // It has no filter — selection visibility is driven entirely via paint
  // (line-opacity with a feature-state case expression), since MapLibre v4
  // does not support feature-state expressions inside layer filters.  The
  // selection ring only appears on features the user has actually clicked
  // (which must already be visible through regions-fill), so skipping the
  // country filter here is safe — a user cannot click a hidden fill feature.
  //
  // 'match' is used instead of 'in' for the country filter because MapLibre's
  // 'in' expression requires a literal keyword as its first argument; passing
  // ['get', 'country'] (an expression) as the keyword causes the filter to
  // evaluate incorrectly in MapLibre v4, hiding all features.
  // SNOW-524: append ``incoming``'s features onto ``existing``, skipping any
  // whose ``properties.prefix`` is already present.
  //
  // The L1/L2 caches are merged from two directions now: the country load
  // (which fetches all three tiers for every enabled country, CH included
  // since boot stopped special-casing it) and a tier's own lazy enable, which
  // fetches ``?country=ch``. Both used to be append-only and disjoint; now
  // they overlap on CH, and a plain concat would draw every Swiss outline
  // twice. Keyed on ``prefix`` because that is what the L1/L2 API emits as
  // the stable per-feature identifier (public/api.py).
  const mergeRegionFeatures = (existing, incoming) => {
    if (!incoming || !incoming.features) return existing;
    if (!existing) return incoming;
    const seen = new Set(existing.features.map(f => f.properties && f.properties.prefix));
    const fresh = incoming.features.filter(
      f => !seen.has(f.properties && f.properties.prefix),
    );
    if (fresh.length === 0) return existing;
    return { ...existing, features: [...existing.features, ...fresh] };
  };

  const applyCountryFilters = () => {
    const enabled = COUNTRY_KEYS
      .filter(code => countryState[code])
      .map(code => code.toUpperCase());
    // ['match', input, [values...], true, false] evaluates to true when the
    // feature's country property is in the enabled list, false otherwise.
    // When no countries are enabled use an always-false expression so every
    // layer empties cleanly rather than showing stale data.
    //
    // That always-false form must be a real EXPRESSION. It used to be
    // ``['==', false, true]``, which MapLibre parses as the LEGACY ``==``
    // filter — whose second element has to be a property name — so it
    // rejected the whole style with "layers.regions-fill.filter[1]: string
    // expected, boolean found" and the map rendered blank. Untick every
    // country and the map never came back. ``['in', x, ['literal', []]]`` is
    // unambiguously an expression and is always false.
    const countryFilter = enabled.length > 0
      ? ['match', ['get', 'country'], enabled, true, false]
      : ['in', ['get', 'country'], ['literal', []]];
    const layerIds = [
      'regions-fill', 'regions-line', 'regions-label',
      'sub-regions-line', 'sub-regions-label',
      'major-regions-line', 'major-regions-label',
    ];
    for (const layerId of layerIds) {
      if (!map.getLayer(layerId)) continue;
      const base = BASE_LAYER_FILTERS[layerId];
      const composed = base ? ['all', base, countryFilter] : countryFilter;
      map.setFilter(layerId, composed);
    }
    // regions-line-selected is intentionally absent from layerIds above.
    // It has no filter — selection visibility is paint-driven via line-opacity
    // and feature-state, which cannot appear in filter expressions (MapLibre v4).
    // Country filtering is implicit: only features visible through regions-fill
    // (which does carry the country filter) can be clicked and selected.

    // SNOW-323: bulletin-groupings-line carries a ``countries`` JSON *array*
    // rather than a scalar ``country`` string, so the scalar 'match' filter
    // above cannot be reused.  Build a membership filter using MapLibre's
    // 'in' expression (value, array form — first arg is the needle, second
    // is ['get', 'countries'] which resolves to the feature's array).
    // Compose with its base filter if one was snapshotted at install time.
    if (map.getLayer('bulletin-groupings-line')) {
      const arrayFilter = enabled.length > 0
        ? ['any', ...enabled.map(c => ['in', c, ['get', 'countries']])]
        : ['in', ['get', 'country'], ['literal', []]];
      const base = BASE_LAYER_FILTERS['bulletin-groupings-line'];
      const composed = base ? ['all', base, arrayFilter] : arrayFilter;
      map.setFilter('bulletin-groupings-line', composed);
    }
  };

  // SNOW-172: Lazy-fetch a country's L1 + L2 + L4 GeoJSON and merge it
  // into the existing MapLibre sources. loadedCountries prevents re-fetching.
  //
  // SNOW-524: make sure a country's season-ratings feed is present in Cache
  // Storage, fetching it only if it isn't. Used for the boot country, whose
  // ratings were fetched too early in the page's life to be seen by the
  // service worker on a first-ever visit; on every later visit the entry is
  // already there and this makes no request at all.
  //
  // Resolves true when the feed is cached (or was just fetched successfully) —
  // i.e. when the country's dot may honestly go green. Never throws.
  const ensureRatingsCached = async (code) => {
    const url = RATINGS_URL + '?country=' + code;
    try {
      if ('caches' in window) {
        const hit = await caches.match(new Request(new URL(url, location.origin)));
        if (hit) return true;
      }
    } catch (_e) {
      // Cache Storage unavailable — fall through to the network.
    }
    return fetch(url).then(r => r.ok).catch(() => false);
  };

  // SNOW-524: ``isBootCountry`` marks the one country the boot block has
  // already partly loaded on the critical path (CH). It is NOT a
  // default-country exemption — boot runs this function for CH exactly as for
  // any country the user toggles on, so CH ends up with the same four feeds
  // cached. The flag only avoids redoing work boot already did:
  //
  //   - L4 geometry is already fetched AND installed, so re-fetching it here
  //     would merge CH's polygons into ``geojsonCache`` a second time and
  //     duplicate every feature.
  //   - Season ratings are already fetched into ``SEASON_RATINGS_PROMISE``,
  //     which IS the cache this function otherwise merges into — so there is
  //     nothing to merge and no second fetch to make.
  const ensureCountryLoaded = async (code, { isBootCountry = false, userInitiated = false } = {}) => {
    if (loadedCountries.has(code)) return;
    const upper = code.toUpperCase();
    try {
      const [newRegions, newMajor, newSub] = await Promise.all([
        REGIONS_URL && !isBootCountry ? fetch(REGIONS_URL + '?country=' + code).then(r => {
          if (!r.ok) throw new Error('regions fetch failed');
          return r.json();
        }) : Promise.resolve(null),
        MAJOR_REGIONS_URL ? fetch(MAJOR_REGIONS_URL + '?country=' + code).then(r => {
          if (!r.ok) throw new Error('major fetch failed');
          return r.json();
        }).catch(() => null) : Promise.resolve(null),
        SUB_REGIONS_URL ? fetch(SUB_REGIONS_URL + '?country=' + code).then(r => {
          if (!r.ok) throw new Error('sub fetch failed');
          return r.json();
        }).catch(() => null) : Promise.resolve(null),
      ]);

      // Merge new features into the existing caches and update the sources.
      if (newRegions && newRegions.features && geojsonCache) {
        // Assign numeric ids to new features, continuing from the current max.
        const startId = Object.keys(FEATURE_BY_ID).length;
        newRegions.features.forEach((f, i) => {
          f.id = startId + i;
          const regionID = f.properties.id;
          f.properties.regionID = regionID;
          // SNOW-239: No property-based rating. Choropleth colour is
          // set exclusively via setFeatureState after ratings arrive.
          REGION_LOOKUP[f.id] = f.properties;
          FEATURE_BY_ID[f.id] = f;
          FEATURE_BY_REGION_ID[regionID] = f;
        });
        geojsonCache = {
          ...geojsonCache,
          features: [...geojsonCache.features, ...newRegions.features],
        };
        const regionsSource = map.getSource('regions');
        if (regionsSource) regionsSource.setData(geojsonCache);
        // Notify the search section so it can extend the index with the
        // newly-loaded regions (snowdesk:regions-loaded listener in the
        // search block is idempotent via INDEXED_REGIONS).
        document.dispatchEvent(new CustomEvent('snowdesk:regions-loaded', {
          detail: { regionIDs: newRegions.features.map(f => f.properties.regionID) },
        }));
      }

      // SNOW-493 finding 5: retain L1/L2 results unconditionally, mirroring
      // the merge-always behaviour geojsonCache (L4) uses above. Previously
      // this was gated on ``majorGeojsonCache``/``subGeojsonCache`` already
      // being non-null — which is only true once the user has enabled L1/L2
      // at least once — so fetching a country before that first enable
      // silently discarded its L1/L2 data. loadedCountries then marks the
      // country as loaded, so a later L1/L2 enable never re-fetches it and
      // the foreign hierarchy stays permanently missing. Create the cache
      // here if it doesn't exist yet so the data survives for that later
      // enable to pick up (see the matching merge in ensureOverlayLoaded).
      if (newMajor && newMajor.features) {
        majorGeojsonCache = mergeRegionFeatures(
          majorGeojsonCache,
          { type: 'FeatureCollection', features: [...newMajor.features] },
        );
        const majorSource = map.getSource('major-regions');
        if (majorSource) majorSource.setData(majorGeojsonCache);
      }

      if (newSub && newSub.features) {
        subGeojsonCache = mergeRegionFeatures(
          subGeojsonCache,
          { type: 'FeatureCollection', features: [...newSub.features] },
        );
        const subSource = map.getSource('sub-regions');
        if (subSource) subSource.setData(subGeojsonCache);
      }

      // SNOW-518: the L1/L2/L4 feeds just fetched for this country have now
      // flowed through the SW cache — green their dashboard dots so the layers
      // menu reflects the newly-available offline data. Guarded on the same
      // feature-presence checks as the merges above; markCached no-ops for keys
      // not actually loaded.
      //
      // SNOW-524: these tier marks stay optimistic, but the dashboard's probe
      // is no longer country-agnostic — ``_probeEveryCountry`` now requires the
      // tier to be cached for EVERY enabled country before it paints green. So
      // a mark here can briefly over-claim (this country is cached, another
      // enabled one isn't); the next popover-open ``refresh()`` re-probes and
      // corrects it. The country's own dot is marked at the end of this
      // function, once its ratings feed has been fetched too.
      if (newRegions && newRegions.features) window.pwaLayerSyncStatus?.markCached('l4');
      if (newMajor && newMajor.features) window.pwaLayerSyncStatus?.markCached('l1');
      if (newSub && newSub.features) window.pwaLayerSyncStatus?.markCached('l2');

      loadedCountries.add(code);

      // SNOW-239: Fetch the new country's ratings and merge them into the
      // season cache so scrubber/timelapse frames immediately include it.
      // Also paint the current visible date so new regions colour straight
      // away without waiting for the next scrubber interaction.
      // SNOW-524: did every feed this country's dot depends on actually land?
      // L1/L2 swallow their own errors (``.catch(() => null)``) so a partial
      // load doesn't reject — which means the country dot must not be greened
      // off a successful *call*, only off complete data.
      let ratingsOk = false;
      if (RATINGS_URL && isBootCountry) {
        // Boot already fetched this country's season ratings into
        // SEASON_RATINGS_PROMISE, so there is nothing to fetch or merge — but
        // that fetch can still be missing from Cache Storage: on a first-ever
        // visit it runs before the service worker controls the page, so it is
        // never intercepted and never cached. Top it up only when it really is
        // absent, which costs one request on a first visit and none after.
        ratingsOk = await ensureRatingsCached(code);
      } else if (RATINGS_URL) {
        const countryRatings = await fetch(RATINGS_URL + '?country=' + code)
          .then(r => { if (!r.ok) throw new Error('ratings fetch failed'); return r.json(); })
          .catch(() => null);
        ratingsOk = !!countryRatings;
        if (countryRatings) {
          // Merge into SEASON_RATINGS_PROMISE payload if it has resolved.
          if (SEASON_RATINGS_PROMISE) {
            SEASON_RATINGS_PROMISE.then((cache) => {
              for (const [dateKey, regions] of Object.entries(countryRatings)) {
                if (!cache[dateKey]) cache[dateKey] = {};
                Object.assign(cache[dateKey], regions);
              }
              // SNOW-236: Notify the scrubber that the merged cache now includes
              // this country's ratings so it can re-derive effectiveTodayKey.
              document.dispatchEvent(new CustomEvent('snowdesk:country-ratings-loaded', {
                detail: { code },
              }));
            }).catch(() => {});
          }
          // Paint the currently-displayed date for the new country's regions.
          // We use the display date from countryRatings directly rather than
          // relying on the season cache promise completing first.
          if (MAP) {
            // Determine which date is currently being displayed. The
            // currentDisplayedDate var lives in the inner map.on('load') scope,
            // so we read from the URL as a safe cross-scope fallback.
            const displayDate = readUrlDateParam();
            // SNOW-236: fall back to bootDateKey (season-end clamped) rather than
            // raw today so post-season country toggles paint a populated frame.
            const paintDate = displayDate || bootDateKey;
            const frame = countryRatings[paintDate] || {};
            // Mirror the paintTodayRatings guard: setFeatureState is a no-op
            // if the source has not finished loading. Gate on isSourceLoaded
            // and defer via a one-shot sourcedata listener if not yet ready.
            // SNOW-623: `clearMissing: false` — this frame names only the
            // newly-loaded country's regions, so clearing the ones it
            // omits would wipe every country already on the map.
            const paintNewCountry = () => {
              self.pwaChoroplethCore.paintRatingsFrame(choroplethDeps(), frame, {
                clearMissing: false,
              });
            };
            if (MAP.isSourceLoaded('regions')) {
              paintNewCountry();
            } else {
              const onSourceReady = (e) => {
                if (e.sourceId === 'regions' && MAP.isSourceLoaded('regions')) {
                  MAP.off('sourcedata', onSourceReady);
                  paintNewCountry();
                }
              };
              MAP.on('sourcedata', onSourceReady);
            }
          }
        }
      }
      // SNOW-524: green the country's own dot, but only once all four of its
      // feeds have actually flowed through the SW cache — a skipped feed was
      // already fetched by boot, so it counts. Optimistic on purpose: the SW's
      // ``cache.put`` isn't awaited inside ``_staleWhileRevalidate``, so an
      // immediate re-probe would race the write; the next popover-open
      // ``refresh()`` re-verifies against real cache state and self-corrects.
      const allFeedsLoaded =
        (isBootCountry || !!newRegions) && !!newMajor && !!newSub && ratingsOk;
      if (allFeedsLoaded) {
        window.pwaLayerSyncStatus?.markCached('country.' + code);
      } else {
        // Partial load — let a real probe decide, rather than leaving the row
        // pulsing forever after an optimistic markSyncing.
        window.pwaLayerSyncStatus?.refresh();
      }
    } catch (err) {
      console.warn('[map] Failed to load country', upper, err);
      // SNOW-524: revert the toggle rather than leaving it switched on over an
      // empty map with no feedback — but ONLY for a user-initiated toggle.
      //
      // The boot restore path calls this too, and reverting there PERSISTS
      // ``false`` for a country the user never touched: one offline boot, or
      // any transient fetch failure, would permanently switch Switzerland off.
      // Turn every country off and the map has nothing to draw, so a single
      // failed boot load left the map blank on every subsequent visit. A boot
      // failure means "not cached yet", not "the user doesn't want this
      // country" — leave the preference alone and just re-probe the dots.
      if (!userInitiated) {
        window.pwaLayerSyncStatus?.refresh();
        return;
      }
      countryState[code] = false;
      COUNTRY_STATE[code] = false;
      writeStorage(COUNTRY_STORAGE_KEY(code), 'false');
      const row = document.querySelector(
        `#basemap-menu [data-overlay-key="country.${code}"]`,
      );
      if (row) row.setAttribute('aria-checked', 'false');
      applyCountryFilters();
      // SNOW-524: the country is no longer enabled, so re-probe to re-judge the
      // country-scoped tier dots without it — otherwise they'd stay stuck grey
      // on account of a country that's just been switched back off.
      window.pwaLayerSyncStatus?.refresh();
      revealOfflineToast('map-offline-toast-layer');
    }
  };

  // SNOW-492: reveal the per-overlay "unavailable offline" toast by id —
  // remove `hidden`, add `flex` (per _toast.html's display note; the base
  // class list deliberately omits `flex` so both idioms that toggle it,
  // this one and sw_register.js's, can coexist). overlays.js's shared
  // data-toast-timeout auto-dismiss and "×" handler take it from there.
  // Best-effort: a missing element (template not rendered, e.g. an older
  // cached shell) is a silent no-op rather than a thrown error.
  const revealOfflineToast = (id) => {
    try {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.remove('hidden');
      el.classList.add('flex');
    } catch (_e) {
      // Non-fatal — the toast is a nice-to-have, not load-bearing.
    }
  };

  // SNOW-235: Lazy-load an overlay tier (l1 / l2 / resorts) on first use.
  // Modelled on ensureCountryLoaded — guard flag prevents duplicate fetches,
  // errors degrade silently (no layer install), applyCountryFilters is called
  // after install so freshly-added L1/L2 layers respect the active country
  // filter immediately.
  const overlayLoaded = {
    l1: false, l2: false, l3: false, resorts: false, favourites: false,
    community_reports: false,
  };

  // SNOW-493 P1: the in-flight load promise per key. ``overlayLoaded`` only
  // flips true after the fetch settles, so a rapid on → off → on (the user
  // re-enables a tier before its first request returns) would otherwise
  // pass the guard twice and start a second fetch. For l1/l2 both responses
  // concatenate into majorGeojsonCache/subGeojsonCache, duplicating every
  // country feature — invisible until the next basemap swap reinstalls the
  // source from that cache. Reusing the pending promise collapses the
  // second enable onto the first fetch, so the merge happens exactly once.
  const overlayLoading = {};

  const _loadOverlay = async (key) => {
    if (key === 'l1') {
      if (!MAJOR_REGIONS_URL) return;
      const data = await fetch(MAJOR_REGIONS_URL + '?country=ch')
        .then(r => r.json()).catch(() => null);
      if (!data) {
        revealOfflineToast('map-offline-toast-layer');
        return;
      }
      // SNOW-493 finding 5: merge into any L1 data already retained by
      // ensureCountryLoaded (e.g. a foreign country toggled on before L1's
      // first enable) rather than overwriting it — otherwise this first
      // fetch would wipe out data for a country that's already loaded and
      // will never be re-fetched.
      majorGeojsonCache = mergeRegionFeatures(majorGeojsonCache, data);
      installOverlayLayers(majorGeojsonCache, subGeojsonCache);
    } else if (key === 'l2') {
      if (!SUB_REGIONS_URL) return;
      const data = await fetch(SUB_REGIONS_URL + '?country=ch')
        .then(r => r.json()).catch(() => null);
      if (!data) {
        revealOfflineToast('map-offline-toast-layer');
        return;
      }
      // SNOW-493 finding 5: same merge as L1 above, for L2's retained data.
      subGeojsonCache = mergeRegionFeatures(subGeojsonCache, data);
      installOverlayLayers(majorGeojsonCache, subGeojsonCache);
    } else if (key === 'l3') {
      // Fetch the currently-displayed day's boundary and draw it immediately
      // (no settle delay; that only applies while the scrubber is moving).
      if (!BULLETIN_GROUPINGS_URL) return;
      const dateKey = currentDisplayedDate || bootDateKey;
      const fc = await fetchBulletinGroupingsForDate(dateKey).catch(() => null);
      if (!fc) {
        // Deliberately silent, unlike every other tier here. Those load in
        // response to the user clicking their toggle, so a failure owes them
        // an explanation. This one loads automatically alongside L4 — and its
        // endpoint is network-only (per-date data, excluded from sw.js's
        // STATIC_PATHS), so it fails on every offline boot. Toasting that
        // would fire an "unavailable offline" message at a user who asked for
        // nothing and whose choropleth is working fine.
        return;
      }
      installBulletinGroupingsLayer(fc);
      currentGroupingsFC = fc;
      groupingsDrawn = true;
    } else if (key === 'resorts') {
      if (!RESORTS_GEOJSON_URL) return;
      const data = await fetch(RESORTS_GEOJSON_URL)
        .then(r => r.json()).catch(() => null);
      if (!data) {
        revealOfflineToast('map-offline-toast-layer');
        return;
      }
      resortsGeojsonCache = data;
      installResortsLayer(resortsGeojsonCache);
    } else if (key === 'favourites') {
      // SNOW-414: eligible-gated — anonymous/ineligible visitors never see
      // the toggle, but guard the fetch too in case this is ever reached
      // some other way (e.g. the eager boot-time call below).
      if (!FAVOURITES_ELIGIBLE || !FAVOURITES_URL) return;
      const data = await fetch(FAVOURITES_URL)
        .then(r => r.json()).catch(() => null);
      if (data) {
        // SNOW-492: write-through — favourites never expire, so the
        // cached copy is installed as-is on a later offline read-back.
        window.pwaMapOverlayCache?.putOverlay('favourites', data);
        installFavouritesLayer(data);
      } else {
        const cached = await window.pwaMapOverlayCache?.getOverlay('favourites');
        if (!cached) {
          revealOfflineToast('map-offline-toast-favourites');
          return;
        }
        installFavouritesLayer(cached);
      }
    } else if (key === 'community_reports') {
      // SNOW-419: flag-gated only (no auth eligibility) — guard the fetch
      // in case this is ever reached some other way (e.g. the eager
      // boot-time restore below).
      if (!COMMUNITY_REPORTS_ELIGIBLE || !COMMUNITY_REPORTS_URL) return;
      const data = await fetch(COMMUNITY_REPORTS_URL)
        .then(r => r.json()).catch(() => null);
      if (data) {
        // SNOW-492: write-through, cached before the age-opacity mutation
        // below so the stored copy is the pristine server payload.
        window.pwaMapOverlayCache?.putOverlay('community_reports', data);
        communityReportsGeojsonCache = withCommunityReportsAgeOpacity(data);
        installCommunityReportsLayer(communityReportsGeojsonCache);
      } else {
        const cached = await window.pwaMapOverlayCache?.getOverlay('community_reports');
        // SNOW-493 finding 8: no cached copy at all is genuinely
        // unavailable offline.
        if (!cached) {
          revealOfflineToast('map-offline-toast-community_reports');
          return;
        }
        const fresh = dropExpiredCommunityReports(cached);
        // A cached, valid, but EMPTY FeatureCollection (no reports right
        // now, or every cached report has since aged past the 48h window)
        // is a successful cached response, not a failure — install it as
        // zero markers rather than showing the "unavailable offline"
        // warning. Only a malformed/non-FeatureCollection cached payload
        // (``.features`` isn't an array at all — dropExpiredCommunityReports
        // returns such input unchanged) still counts as unavailable.
        if (!Array.isArray(fresh.features)) {
          revealOfflineToast('map-offline-toast-community_reports');
          return;
        }
        communityReportsGeojsonCache = withCommunityReportsAgeOpacity(fresh);
        installCommunityReportsLayer(communityReportsGeojsonCache);
      }
    }
    overlayLoaded[key] = true;
    // Apply country filters to the freshly-added layers so they
    // respect whichever countries are currently enabled.
    applyCountryFilters();
  };

  // SNOW-235 / SNOW-493 P1: public entry point. Short-circuits if the tier
  // is already loaded, reuses any in-flight load for the same key (see
  // ``overlayLoading`` above), and otherwise starts one — clearing the slot
  // on settle so a genuine later retry (e.g. after an offline failure that
  // left ``overlayLoaded[key]`` false) can attempt the fetch again.
  const ensureOverlayLoaded = (key) => {
    if (overlayLoaded[key]) return Promise.resolve();
    if (overlayLoading[key]) return overlayLoading[key];
    const work = _loadOverlay(key).finally(() => {
      delete overlayLoading[key];
    });
    overlayLoading[key] = work;
    return work;
  };

  // SNOW-235: Layer IDs for the lazily-loaded overlay tiers, restricted
  // to l1 / l2 / resorts. l4 is not lazy — its layers are installed
  // eagerly in installRegionsLayers; the other tiers fetch their
  // GeoJSON on first enable.
  // Mirrors OVERLAY_LAYER_IDS in basemapPickerInit but scoped here so
  // the snowdesk:overlay-load handler below can reach them without
  // crossing IIFE boundaries.
  const OVERLAY_LAYER_IDS_MAIN = {
    l1: ['major-regions-line', 'major-regions-label'],
    l2: ['sub-regions-line', 'sub-regions-label'],
    // SNOW-323: l3 has only a line layer (no label layer — groupings
    // don't carry a user-facing name property).
    l3: ['bulletin-groupings-line'],
    resorts: ['resorts-pin', 'resorts-label'],
    favourites: ['favourites-pin', 'favourites-label'],
    community_reports: [
      'community-reports-clusters',
      'community-reports-cluster-count',
      'community-reports-point',
    ],
  };

  // SNOW-235: Bridge for the basemapPickerInit IIFE — dispatched when
  // the user enables an overlay tier that hasn't been fetched yet.
  // We fetch the GeoJSON, install the layers, then make them visible.
  document.addEventListener('snowdesk:overlay-load', (e) => {
    const { key } = e.detail;
    // Captured before the await: ensureOverlayLoaded short-circuits for an
    // already-loaded key, so this distinguishes a first load (which fetches
    // the current day itself) from a re-enable (which does not, and may be
    // holding a boundary for whatever day was showing when it was hidden).
    const wasLoaded = overlayLoaded[key];
    ensureOverlayLoaded(key).then(() => {
      // SNOW-493 finding 4: the fetch above is async, so the user may have
      // toggled the overlay off again before it settled. Unconditionally
      // setting 'visible' here would revive an overlay the user just
      // disabled. Re-read the persisted state from localStorage — the
      // module's live source of truth for overlay visibility (the
      // picker writes it on every click; ``overlayState`` itself is only
      // re-seeded from it at boot and after a basemap swap, per the
      // SNOW-473 comment above the ``styledata`` handler) — rather than
      // trusting the boot-time ``overlayState`` value.
      // Read the governing key, not necessarily this one: the bulletin
      // boundary (l3) has no persisted state and follows L4 (see
      // OVERLAY_VISIBILITY_GOVERNOR). Every other key governs itself.
      const gov = governorFor(key);
      const stillEnabled = readBoolStorage(OVERLAY_STORAGE_KEY[gov], overlayState[gov]);
      overlayState[gov] = stillEnabled;
      const visibility = stillEnabled ? 'visible' : 'none';
      for (const layerId of OVERLAY_LAYER_IDS_MAIN[key]) {
        if (map.getLayer(layerId)) {
          map.setLayoutProperty(layerId, 'visibility', visibility);
        }
      }
      // The boundary is per-date, and scrubbing while L4 is off deliberately
      // skips refetching it (see the date-changed handler). So a re-enable can
      // reveal a boundary belonging to an earlier day, sitting over a
      // choropleth that has moved on. Refetch for the day now showing.
      if (key === 'l3' && stillEnabled && wasLoaded) {
        scheduleGroupingsForDate(currentDisplayedDate || bootDateKey);
      }
      // SNOW-499: making the favourites overlay (re-)visible means any
      // favourited resort should hide its plain dot again, now the star is
      // back. ensureOverlayLoaded short-circuits for an already-loaded
      // favourites layer, so the recompute inside syncFavouritedResortIds
      // won't have run — reapply the exclusion here.
      if (key === 'favourites') applyResortsFavouritedFilter();
      // SNOW-505: a successful lazy-load means the tier's GeoJSON has now
      // flowed through the SW (STATIC_PATHS → stale-while-revalidate cache)
      // or been written to the overlay IDB store, so it is now available
      // offline. Optimistically flip its sync dot green in real time —
      // tying the toggle action to its offline availability — rather than
      // waiting for the next popover open to re-probe. ``overlayLoaded[key]``
      // is true only after a successful fetch (the offline-toast paths in
      // ``_loadOverlay`` early-return, leaving it false), so this never
      // over-claims on a failed load; markCached itself no-ops for l3
      // (network-only, genuinely never cached) so that dot stays grey.
      if (overlayLoaded[key]) window.pwaLayerSyncStatus?.markCached(key);
    }).catch(() => {});
  });

  // SNOW-518: the layers menu is a live cache-state dashboard. When the user
  // returns to the tab/PWA, re-derive every dot from real cache state so the
  // dashboard reflects feeds warmed (or evicted) while we were backgrounded —
  // something the optimistic markCached path alone can't do (it only greens).
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      window.pwaLayerSyncStatus?.refresh();
      // SNOW-570/SNOW-587: same rationale for the cached-tiles overlay —
      // tiles can be evicted while we're backgrounded, and a square for a
      // tile that is no longer cached is worse than no square.
      window.pwaDownloadedOverlay?.refresh();
    }
  });

  // SNOW-499: the picker toggles an already-installed favourites layer off
  // via a direct setLayoutProperty in its own IIFE (no overlay-load event),
  // so bridge that here: recompute the resort exclusion whenever the
  // favourites overlay visibility changes, so a favourited resort's plain
  // dot reappears the moment its star is hidden.
  document.addEventListener('snowdesk:favourites-visibility-changed', () => {
    applyResortsFavouritedFilter();
  });

  // ==== SNOW-570/SNOW-587: the "Available offline" overlay ====
  //
  // Answers "where is the basemap I already have?" for the whole map at
  // once, where the download roundels only ever answer it for the one
  // region you have selected.
  //
  // PROBED, NEVER STORED — literally true: every tile square is read
  // straight back out of real pinned-cache contents on every refresh, with
  // no stored record involved anywhere in the path. Eviction, a basemap
  // swap and Clear Site Data all change the answer, and all of them show
  // up here for free.
  //
  // ONE cache.keys() PASS PER BUCKET. The roundel probes a single region
  // and can afford cache.match(); this asks about every tile the user has
  // pinned, so it takes one pass over every per-area bucket's URLs
  // (pinnedBasemapCacheURLs, module scope — SNOW-586 gave every downloaded
  // area its own bucket, so "one pass" is now one pass per bucket, unioned)
  // and answers the whole map from that set
  // (pwaBasemapDownloadCore.cachedTilesFromURLs). Never call it per frame —
  // the pinned buckets together hold thousands of entries.
  //
  // PER-BASEMAP, like the roundels: the probe keys off the ACTIVE
  // basemap's tile template, so downloading on Standard and switching to
  // Swisstopo empties the overlay. That is the truth — those tiles are not
  // cached — and it is why this refreshes on snowdesk:basemap-changed.

  // Coalesces overlapping refreshes: several of the signals below can land
  // together (a download settling also refreshes the sync dashboard, which
  // can coincide with a basemap swap), and each one is a cache scan.
  let downloadedRefreshInFlight = null;

  // True while an idle retry is already queued, so repeated unresolved
  // refreshes share one listener rather than stacking.
  let downloadedStyleRetryPending = false;

  /**
   * Re-run the refresh on the next MapLibre idle — i.e. once the style, and
   * so ``activeBasemapTileTemplate``, has settled.
   *
   * @returns {void}
   */
  const _refreshDownloadedWhenStyleSettles = () => {
    if (downloadedStyleRetryPending) return;
    downloadedStyleRetryPending = true;
    map.once('idle', () => {
      downloadedStyleRetryPending = false;
      refreshDownloadedOverlay();
    });
  };

  /**
   * Re-derive which tiles are cached and paint the overlay.
   *
   * A no-op while the overlay is switched off: nothing is on screen to be
   * wrong, and the work is a cache scan. Every path that turns it back on
   * refreshes first, so it can never be revealed holding a stale answer.
   *
   * @returns {Promise<void>}
   */
  const refreshDownloadedOverlay = () => {
    if (!overlayState.downloaded) return Promise.resolve();
    if (downloadedRefreshInFlight) return downloadedRefreshInFlight;
    downloadedRefreshInFlight = (async () => {
      const core = self.pwaBasemapDownloadCore;
      const template = activeBasemapTileTemplate(map);
      // No resolvable template means no question to ask yet — leave
      // whatever is painted alone rather than clearing it on a style that
      // is merely still settling, and come back on the next MapLibre idle.
      // Boot is exactly this case: the regions source is added inside
      // map.on('load'), so the style is still dirty for the whole of the
      // sequence that first installs these layers, and a session that left
      // the overlay switched on would otherwise show nothing until the user
      // touched the basemap. Same "can't tell yet ≠ not cached" distinction
      // the download roundels' own probe makes.
      if (!core || !template) {
        _refreshDownloadedWhenStyleSettles();
        return;
      }
      const cached = await pinnedBasemapCacheURLs();

      // The tiles themselves, read straight back out of the cache's own
      // URLs — no stored record involved, so this cannot drift from what
      // is on disk. Eviction, a basemap swap and Clear Site Data all
      // change the answer, and all of them show up here for free.
      const tileSource = map.getSource('cached-tiles');
      if (tileSource) {
        const tiles = core.cachedTilesFromURLs(template, cached, CACHED_TILES_ZOOM);
        tileSource.setData({
          type: 'FeatureCollection',
          features: tiles.map((tile) => ({
            type: 'Feature',
            properties: {},
            geometry: core.bboxPolygon(core.tileBounds(tile.z, tile.x, tile.y)),
          })),
        });
      }
    })().catch(() => {}).finally(() => {
      downloadedRefreshInFlight = null;
    });
    return downloadedRefreshInFlight;
  };

  // The picker flips the layers' visibility itself (they are not lazy), and
  // tells us here so the answer they reveal is freshly probed rather than
  // whatever the last refresh left behind.
  document.addEventListener('snowdesk:downloaded-overlay-changed', (e) => {
    overlayState.downloaded = !!(e.detail && e.detail.visible);
    refreshDownloadedOverlay();
  });

  // Per-basemap (see the block comment above), and a lazy country load
  // brings regions whose download state has never been probed.
  document.addEventListener('snowdesk:basemap-changed', () => refreshDownloadedOverlay());
  document.addEventListener('snowdesk:regions-loaded', () => refreshDownloadedOverlay());

  // The download controls call this when a run settles, alongside their
  // pwaLayerSyncStatus.refresh() — the tiles it just fetched should appear
  // without the user reopening the menu. Exposed the same way
  // pwaLayerSyncStatus is, because those controls live in sibling IIFEs.
  window.pwaDownloadedOverlay = Object.freeze({ refresh: refreshDownloadedOverlay });

  // Boot: a session that left the overlay switched on has its layers
  // installed visible but nothing probed yet, so derive it once. Returns
  // immediately (and queues an idle retry) while the style is still
  // settling, which at this point it always is.
  refreshDownloadedOverlay();

  // SNOW-172: Bridge for the basemapPickerInit IIFE, which lives in a separate
  // scope and cannot reference countryState / ensureCountryLoaded directly.
  // The picker dispatches this event; we own the state mutation here.
  document.addEventListener('snowdesk:country-toggle', (e) => {
    const { code, next } = e.detail;
    countryState[code] = next;
    // SNOW-236: Mirror the mutation into module-scope COUNTRY_STATE so the
    // scrubber IIFE can read the latest state for effective-last computation.
    COUNTRY_STATE[code] = next;
    writeStorage(COUNTRY_STORAGE_KEY(code), String(next));
    // SNOW-524: show the dashboard populating. Turning a country ON fetches
    // its L1/L2/L4 + ratings, so the country row and all three tier rows go
    // grey-and-pulsing synchronously here, then green one by one from
    // ``ensureCountryLoaded`` as each response lands. This is deliberately
    // marked, not probed — an async ``refresh()`` here would race the fetches
    // it represents (see markSyncing's docstring). Turning a country OFF
    // starts no fetch, so that branch takes the ordinary re-probe, which also
    // re-greens any tier that was only grey on the dropped country's account.
    const sync = window.pwaLayerSyncStatus;
    // Only a toggle-ON that will actually fetch may paint the pending state —
    // otherwise nothing would ever arrive to green the dots and they'd pulse
    // forever. ``loadedCountries.has(code)`` means this country's feeds were
    // fetched earlier this session, so ``ensureCountryLoaded`` returns
    // immediately without calling markCached.
    const willFetch = next && !!map && !loadedCountries.has(code);
    if (willFetch) {
      sync?.markSyncing('country.' + code);
      for (const tier of sync?.COUNTRY_SCOPED_TIER_KEYS || []) sync.markSyncing(tier);
    } else {
      // Nothing in flight — re-probe for the real state. On toggle-OFF this
      // also re-greens any tier that was grey only on the dropped country's
      // account; on a re-enable of an already-loaded country it confirms the
      // feeds are still cached.
      sync?.refresh();
    }
    if (map) {
      if (next) {
        ensureCountryLoaded(code, { userInitiated: true }).then(() => {
          applyCountryFilters();
        }).catch(() => {});
      } else {
        applyCountryFilters();
      }
    }
  });

  // Most recent date the choropleth is showing — seeded from any ``?d=`` on
  // the URL, then kept in sync by every ``snowdesk:date-changed`` event.
  // Hoisted to outer-IIFE scope so the date-changed listener below can be
  // registered synchronously (before the map's 'load' event fires), making
  // it active in environments where the map never loads (e.g. Playwright
  // offline headless tests).
  let currentDisplayedDate = readUrlDateParam();

  // SNOW-318: Forward reference to the refreshPopupForDate function defined
  // inside map.on('load'). Default no-op so the date-changed listener below
  // is always safe to call through before the map finishes loading.
  let _refreshPopupForDate = () => {};

  // SNOW-47: keep currentDisplayedDate in sync as the scrubber commits new
  // dates or timelapse frames advance. Registered at outer-IIFE scope so this
  // listener is active in headless test environments where MapLibre's 'load'
  // event never fires.
  document.addEventListener('snowdesk:date-changed', (e) => {
    currentDisplayedDate = (e.detail && e.detail.date) || null;
  });

  // SNOW-318: Refresh the open popup's colour/label/link when the scrubber
  // commits a new date. Guarded by !IS_PLAYING so the popup isn't updated on
  // every timelapse frame (timelapse closes the popup silently on start, so
  // this branch only fires during manual scrubbing with a popup open).
  // Registered here at outer-IIFE scope so the listener is active before the
  // map's 'load' event; the no-op default above means it's harmless if the
  // map hasn't finished setting up yet.
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (IS_PLAYING) return;
    const dk = (e.detail && e.detail.date) || null;
    if (dk) _refreshPopupForDate(dk);
  });

  // SNOW-323: When the scrubber commits to a date (drag release, each
  // playback frame, keyboard step), blank the boundary and schedule a
  // settle-debounced fetch for that day. Playback fires this per frame, so
  // the debounce collapses a run of frames into a single fetch once motion
  // stops — the boundary is only ever drawn for a day the user rests on.
  // Also skipped while L4 is off: the boundary is hidden then, so refetching
  // it per scrubbed date would be pure network cost for nothing on screen.
  //
  // L4's state is read from localStorage, not from ``overlayState``: the
  // picker writes the key on every click but only a toggle-ON reaches the
  // ``snowdesk:overlay-load`` handler that refreshes ``overlayState``, so
  // after a toggle-OFF the in-memory copy still says true for the rest of
  // the session and this guard never fires. localStorage is the picker's
  // live source of truth — the same read the overlay-load handler makes
  // (``stillEnabled`` above), for the same reason.
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (!overlayLoaded.l3) return;
    if (!readBoolStorage(OVERLAY_STORAGE_KEY.l4, overlayState.l4)) return;
    const dk = (e.detail && e.detail.date) || null;
    if (!dk) return;
    scheduleGroupingsForDate(dk);
  });

  // SNOW-323: While the user is actively dragging the thumb, the scrubber
  // emits a continuous stream of preview dates (no commit). Blank the
  // boundary and cancel any pending fetch so a stale outline never lingers
  // mid-drag; the fetch is (re)scheduled by the date-changed commit on release.
  document.addEventListener('snowdesk:date-preview', () => {
    if (!overlayLoaded.l3) return;
    if (groupingsSettleTimer) {
      clearTimeout(groupingsSettleTimer);
      groupingsSettleTimer = null;
    }
    blankGroupings();
  });

  map.on('load', async () => {
    // SNOW-235: Fetch only the choropleth-critical payloads at boot.
    // L1/L2/resorts overlay fetches have been removed from this
    // Promise.all — they are off by default and loaded lazily on first
    // toggle via ensureOverlayLoaded (see above). This trims ~123 KB
    // uncompressed from the critical path on every default-preference
    // first-load.
    //
    // SNOW-239: today-summaries replaced by a compact ratings fetch
    // (?d=<today>&country=ch, ~2 KB). Choropleth is painted via
    // setFeatureState — no more property-based rating on features.
    // SNOW-236: fetch using bootDateKey (clamped to season end) so the
    // choropleth paints the last populated date when today is post-season.
    const [geojson, todayRatingsPayload, resorts] =
      await Promise.all([
        fetch(REGIONS_URL + '?country=ch').then(r => r.json()),
        RATINGS_URL
          ? fetch(RATINGS_URL + '?d=' + bootDateKey + '&country=ch').then(r => {
              if (!r.ok) throw new Error('ratings fetch failed');
              return r.json();
            }).catch(() => ({}))
          : Promise.resolve({}),
        fetch(RESORTS_URL).then(r => r.json()),
      ]);
    Object.assign(RESORTS_BY_REGION, resorts);
    // todayRatingsPayload shape: { "YYYY-MM-DD": { region_id: rating_int } }
    const todayRatings = todayRatingsPayload[bootDateKey] || {};

    // Assign a numeric id to every feature and build the lookup.
    // MapLibre's feature-state API requires numeric ids; regionID is a string
    // ("CH-4115") so we can't use it directly.
    geojson.features.forEach((f, i) => {
      f.id = i;
      // The API emits the region identifier as properties.id. Normalise to
      // properties.regionID so the rest of the code has a stable name.
      const regionID = f.properties.id;
      f.properties.regionID = regionID;
      // SNOW-239: no property-based rating; colour set via setFeatureState
      // below once the source is installed and the features are loadable.
      REGION_LOOKUP[i] = f.properties;
      FEATURE_BY_ID[i] = f;
      FEATURE_BY_REGION_ID[regionID] = f;
    });

    geojsonCache = geojson;
    // SNOW-478: derive the overlay label font from the just-loaded basemap
    // before installing any overlay symbol layer, so labels resolve against
    // this basemap's glyph server. Must run before the first install below.
    overlayTextFont = deriveOverlayTextFont();
    // SNOW-235: majorGeojsonCache / subGeojsonCache / resortsGeojsonCache
    // remain null until the user first enables that overlay tier; they are
    // populated by ensureOverlayLoaded below.
    installRegionsLayers(geojson);
    // SNOW-235: installOverlayLayers / installResortsLayer are no longer
    // called here; they run inside ensureOverlayLoaded when each tier is
    // first requested. The styledata re-install handler below is
    // already null-safe (passes null caches ⇒ early-returns cleanly).

    // SNOW-172: CH geometry is now loaded; apply the initial filter.
    // SNOW-524: CH is deliberately NOT added to ``loadedCountries`` here any
    // more — that marked it loaded off the critical-path L4 + ratings fetches
    // alone, so its L1/L2 were never fetched and Switzerland could never reach
    // the same cached state as a country the user toggles on. The restore loop
    // below now runs the country load for CH too (skipping the two feeds this
    // block already fetched), and that call is what records it.
    applyCountryFilters();

    // SNOW-239: Paint today's choropleth via setFeatureState.
    // Gate on the 'data' event so setFeatureState calls stick —
    // if ratings resolved before the source finished loading, the calls
    // would silently no-op. The source emits 'data' with isSourceLoaded
    // once all features are available; we register a one-shot listener
    // that fires the paint loop and then removes itself.
    // SNOW-623: `clearMissing: false` — the boot frame covers the
    // initially-loaded country only, and a region with no geometry yet has
    // nothing to paint. Uses the local `map` rather than the module-scope
    // `MAP`, which this boot path runs before.
    const paintTodayRatings = () => {
      self.pwaChoroplethCore.paintRatingsFrame(
        {
          featureById: FEATURE_BY_REGION_ID,
          intToRating: INT_TO_RATING,
          setRating: (featureId, rating) =>
            map.setFeatureState({ source: 'regions', id: featureId }, { rating }),
        },
        todayRatings,
        { clearMissing: false },
      );
    };
    if (map.isSourceLoaded('regions')) {
      paintTodayRatings();
    } else {
      const onSourceData = (e) => {
        if (e.sourceId === 'regions' && map.isSourceLoaded('regions')) {
          map.off('sourcedata', onSourceData);
          paintTodayRatings();
        }
      };
      map.on('sourcedata', onSourceData);
    }

    // Load every enabled country — the ones restored from localStorage AND
    // Switzerland. SNOW-524: CH used to be excluded here, which is why it
    // alone never fetched its L1/L2 and could never reach the cached state
    // every other country reaches. It now takes the same path, minus the two
    // work the boot block above already did (L4 geometry, season ratings).
    // The L1/L2 fetches still run, landing in the offline cache off the
    // critical path so SNOW-235's trimmed first paint is preserved.
    for (const code of COUNTRY_KEYS) {
      if (!countryState[code]) continue;
      ensureCountryLoaded(code, { isBootCountry: code === 'ch' }).catch(() => {});
    }

    // SNOW-235: Restore any overlay tiers the user had enabled in a prior
    // session. These fire after the choropleth installs (not awaited) so
    // they never block first paint. There will be a brief window where the
    // choropleth is visible but the overlay is still fetching — this is
    // intentional and an improvement over the previous blocking behaviour.
    // SNOW-518: a boot restore silently warms the same cache a toggle would,
    // so it must green the dashboard dot the same way the toggle handler
    // does at ``snowdesk:overlay-load`` above — otherwise the dot reads grey
    // until the next popover open even though the feed is already available
    // offline. ``overlayLoaded[key]`` is only true after a successful load
    // (the offline-toast paths in ``_loadOverlay`` early-return leaving it
    // false), so this never over-claims; markCached itself no-ops for keys
    // it doesn't recognise. If the user toggles the same tier on mid-restore,
    // ``ensureOverlayLoaded`` hands both callers the one in-flight promise, so
    // markCached can fire twice for a single load — harmless, as it just
    // repaints the dot to the same "cached" state.
    const restoreOverlay = (key) =>
      ensureOverlayLoaded(key)
        .then(() => {
          if (overlayLoaded[key]) window.pwaLayerSyncStatus?.markCached(key);
        })
        .catch(() => {});

    for (const key of ['l1', 'l2', 'resorts']) {
      if (overlayState[key]) restoreOverlay(key);
    }

    // The bulletin boundary rides along with the micro-region tier rather
    // than having a toggle of its own, so it loads at boot whenever L4 is on
    // (which is the default). Its data is per-date and network-only, so this
    // is a real fetch on every boot, not a cache read — it degrades silently
    // when it fails (see the l3 branch of _loadOverlay).
    if (overlayState.l4) restoreOverlay('l3');

    // SNOW-414: favourites is default-ON (unlike the tiers above), so an
    // eligible user's saved pins load at boot rather than waiting for a
    // toggle. Anonymous/ineligible visitors never reach this branch —
    // ensureOverlayLoaded('favourites') also short-circuits on
    // !FAVOURITES_ELIGIBLE as a second guard.
    if (FAVOURITES_ELIGIBLE && overlayState.favourites) {
      restoreOverlay('favourites');
    }

    // SNOW-419: restore the community-reports overlay if the user had it
    // enabled in a prior session. Off by default (unlike favourites), so
    // this only fires for a returning user who opted in.
    if (COMMUNITY_REPORTS_ELIGIBLE && overlayState.community_reports) {
      restoreOverlay('community_reports');
    }

    // Interaction
    let selectedId = null;

    // SNOW-318: Popup state — decoupled from the selection state. No gesture
    // opens the region popup any more (selecting a region only moves the
    // highlight/ribbon/readout/hash), so this state is dormant unless the
    // popup is opened programmatically; the decoupling below still holds.
    //
    // Closing the popup (via ×/Esc or the timelapse start) does NOT deselect
    // the region. The highlight, pill, and #CH-xxxx hash all persist. Only an
    // empty-canvas tap or a re-tap of the selected region deselects.
    //
    // activePopupRegion tracks { regionID, slug } of the currently-open popup
    // so refreshPopupForDate can build the updated bulletin href without having
    // to look up REGION_LOOKUP again (avoids a subtle bug where selectedId
    // could diverge from the popup's region during rapid region switches).
    let activePopup = null;
    let activePopupRegion = null;  // { regionID, slug } or null
    // Race guard — incremented before every new fetch; stale responses bail
    // out early by comparing their captured seq against the current value.
    let summarySeq = 0;

    // ---- URL fragment state (SNOW-39) ----
    //
    // The currently-selected region is mirrored in ``location.hash`` as
    // ``#CH-xxxx`` so the back button drops the selection (instead of
    // leaving the page) and so a deep link restores it on load.
    //
    // ``popupHistoryOpen`` tracks whether our hash is currently the top
    // history entry — drives push-vs-replace on the next open.
    //
    // ``popupHashWasPushed`` tracks whether the current history entry is
    // one *we* pushed via ``pushState``, as opposed to the entry the user
    // landed on (e.g. arriving at ``/map/#CH-4115`` from the bulletin
    // page). Only pushed entries are safe to dismiss via ``history.back()``
    // — popping a landed-on entry navigates the user off the map and
    // straight back to wherever they came from, which is the trap this
    // flag guards against. When false, ``clearTooltip`` clears the hash
    // via ``replaceState`` instead.
    //
    // ``popstateInProgress`` blocks the recursive ``clearTooltip ->
    // history.back -> popstate -> clearTooltip`` path during back-button
    // dismissal.
    let popupHistoryOpen = false;
    let popupHashWasPushed = false;
    let popstateInProgress = false;

    // Canonical SLF region-ID shape (e.g. "CH-4115", "AT-02-14",
    // "IT-32-BZ-15-02"). Anything else is rejected before it reaches any href
    // to prevent a malformed GeoJSON payload turning into an open-redirect /
    // javascript: URL on the client.
    const REGION_ID_RE = /^[A-Za-z]{2}(-[A-Za-z0-9]+)+$/;

    // Compute the lng/lat bounding box of a GeoJSON Polygon or MultiPolygon.
    // MapLibre's fitBounds takes [[west, south], [east, north]].
    const featureBBox = (feature) => {
      const coords = feature.geometry.type === 'Polygon'
        ? feature.geometry.coordinates
        : feature.geometry.coordinates.flat();  // MultiPolygon → concat rings
      let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
      for (const ring of coords) {
        for (const [lng, lat] of ring) {
          if (lng < w) w = lng;
          if (lng > e) e = lng;
          if (lat < s) s = lat;
          if (lat > n) n = lat;
        }
      }
      return [[w, s], [e, n]];
    };

    // Fit the viewport to a region's bounds. Shared between AUTOZOOM click-fits
    // and the double-click gesture so both use the same padding, maxZoom, and
    // duration — the extra top padding leaves room for the popup body above.
    const zoomToFeatureBounds = (feature) => {
      map.fitBounds(featureBBox(feature), {
        padding: { top: 60, right: 40, bottom: 40, left: 40 },
        maxZoom: 10,
        duration: 400,
      });
    };

    // SNOW-318: Return the lng/lat of the region's north edge mid-point.
    // With anchor:'bottom' the popup tip lands on this point and the body
    // floats above it, keeping the entire polygon visible in the viewport
    // (no need to pan just to see the popup body clear the region's top edge).
    const featureNorthAnchor = (feature) => {
      const [[w], [e, n]] = featureBBox(feature);
      return [(w + e) / 2, n];
    };

    // SNOW-318: Popup-DOM-only teardown. Clears the popup and its region
    // association without touching the selection, highlight, pill, or URL hash.
    // This is the key behavioural change from pre-314: ×/Esc closes the popup
    // but leaves the region highlighted and the hash intact — the user can
    // re-click to reopen.
    //
    // Re-entry guard: null activePopup BEFORE calling p.remove(). MapLibre
    // fires the popup's 'close' event synchronously inside remove(), which
    // would otherwise trigger closePopupOnly() again and run the side-effects
    // twice. Nulling first makes the second entry a harmless early-return.
    //
    // summarySeq++ invalidates any inflight fetch — if openRegionPopup is still
    // awaiting its fetch when the popup is closed, the stale response will bail
    // out early and not re-open the popup.
    const closePopupOnly = () => {
      if (!activePopup) return;
      const p = activePopup;
      activePopup = null;
      activePopupRegion = null;
      summarySeq++;
      p.remove();
    };

    // SNOW-318: Silent dismissal for region-to-region transitions. Removes the
    // current popup WITHOUT bumping summarySeq, so a new fetch already in-flight
    // is not invalidated. The 'close' listener is detached first so closePopupOnly
    // doesn't fire during remove(), which would bump summarySeq and discard the
    // new fetch.
    const dismissActivePopupSilently = () => {
      if (!activePopup) return;
      const p = activePopup;
      activePopup = null;
      activePopupRegion = null;
      p.off('close', closePopupOnly);
      p.remove();
    };

    // SNOW-318: Fetch the server-rendered tooltip HTML for a region and open a
    // MapLibre Popup anchored above the region's north edge. The summarySeq
    // guard discards stale responses when the user taps a different region
    // mid-flight. Returns true on success, false on network error or stale seq.
    //
    // NOTE: currently unreachable. Selecting a region used to open this popup;
    // the trigger was removed because the popup covered the map the user had
    // just tapped and the ribbon + readout already carry the same information.
    // The implementation is kept deliberately (with its refresh/teardown
    // plumbing) pending a decision on a different surface for region detail —
    // don't delete it as dead code without checking that decision first.
    //
    // Design notes:
    //   - anchor:'bottom' + featureNorthAnchor keeps the popup above the polygon.
    //   - closeOnClick:false — the empty-canvas handler routes through
    //     closePopupOnly explicitly; we don't want MapLibre's canvas-click to
    //     deselect (the popup close and the deselect are now independent).
    //   - focusAfterOpen:false — avoids an unwanted focus ring on deep-link
    //     arrival where the popup opens without keyboard activation.
    //   - summarySeq is incremented here (not in dismissActivePopupSilently) so
    //     the new fetch's seq is captured before the old popup is removed.
    const openRegionPopup = async (numericId) => {
      const props = REGION_LOOKUP[numericId];
      if (!props) return false;
      const regionID = props.regionID;
      if (!REGION_ID_RE.test(regionID)) return false;

      dismissActivePopupSilently();

      let url = REGION_SUMMARY_URL_TEMPLATE.replace(
        'XX-0000', encodeURIComponent(regionID),
      );
      if (currentDisplayedDate) url += '?d=' + encodeURIComponent(currentDisplayedDate);

      const seq = ++summarySeq;
      try {
        const resp = await fetch(url, { headers: { Accept: 'application/json' } });
        if (seq !== summarySeq) return false;  // a newer tap won the race
        if (!resp.ok) return false;
        const data = await resp.json();
        if (seq !== summarySeq) return false;

        const feature = FEATURE_BY_ID[numericId];
        if (!feature) return false;

        // Server-trusted HTML: rendered by Django templates with all
        // user-supplied values escaped by autoescape — safe for setHTML.
        const popup = new maplibregl.Popup({
          closeButton: true,
          closeOnClick: false,
          // focusAfterOpen:false — the popup opens in response to pointer / hash
          // navigation, not keyboard activation, so the default focus-ring on the
          // bulletin CTA is just visual noise. The close button is still reachable
          // via Tab for keyboard users.
          focusAfterOpen: false,
          // anchor:'bottom' + featureNorthAnchor: tip points down to the region's
          // north edge, body floats above — keeps the whole polygon visible.
          anchor: 'bottom',
          maxWidth: 'min(320px, calc(100vw - 32px))',
          className: 'region-popup',
        });

        // Set HTML before lngLat so MapLibre can compute correct DOM dimensions
        // when _update runs. Chain order matters: setHTML → setLngLat → addTo.
        popup
          .setHTML(data.html)
          .setLngLat(featureNorthAnchor(feature))
          .addTo(map);

        // Force immediate positioning — MapLibre's _update normally runs on the
        // next rAF tick, which can lag perceptibly on heavy renders. Calling it
        // directly snaps the popup to its anchor on the same frame. _update is a
        // private method (acknowledged trade-off); stable across MapLibre v3/v4.
        if (typeof popup._update === 'function') popup._update();

        // Stamp the rating level on the popup root so map.css drives the border
        // colour via .region-popup[data-level=…].
        const el = popup.getElement();
        if (el) el.setAttribute('data-level', data.level || 'no_rating');

        activePopup = popup;
        activePopupRegion = { regionID, slug: props.slug || '' };

        // Wire the popup's own close event to closePopupOnly so ×/Esc/outside-map
        // close only the popup — not the highlight, pill, or hash.
        popup.on('close', closePopupOnly);
        return true;
      } catch (_err) {
        return false;
      }
    };

    // SNOW-499: a single "detail popup" handle, separate from the region
    // popup — a resort or an *existing* favourite is a point fixed to the
    // map, so its detail overlay is a MapLibre popup anchored to the point
    // (not the docked create/placement sheet, which stays put while the map
    // pans a mobile pin under it). Only one detail popup can be open at a
    // time, and it is tracked independently of activePopup/activePopupRegion
    // (no history/hash involvement, unlike the region popup).
    let activeDetailPopup = null;

    // Remove the open detail popup, if any.
    const closeDetailPopup = () => {
      if (!activeDetailPopup) return;
      const p = activeDetailPopup;
      activeDetailPopup = null;
      p.remove();
    };

    // Anchor a detail popup at ``lngLat`` with server HTML (``content.html``)
    // or a client-built DOM node (``content.node``). Replaces any open detail
    // popup, dismisses the region popup, and closes the favourite create
    // sheet — only one map-detail surface is meaningful at a time.
    const mountDetailPopup = (lngLat, content) => {
      closeDetailPopup();
      dismissActivePopupSilently();
      // Close the favourite create/placement sheet if it is open.
      document.dispatchEvent(new CustomEvent('snowdesk:map-detail-opening'));

      const popup = new maplibregl.Popup({
        closeButton: true,
        closeOnClick: false,
        focusAfterOpen: false,
        anchor: 'bottom',
        maxWidth: 'min(320px, calc(100vw - 32px))',
        className: 'detail-popup',
      });
      if (content.html != null) {
        // Server-trusted HTML: rendered by Django templates with all
        // user-supplied values escaped by autoescape — safe for setHTML.
        popup.setHTML(content.html);
      } else if (content.node) {
        popup.setDOMContent(content.node);
      }
      popup.setLngLat(lngLat).addTo(map);
      if (typeof popup._update === 'function') popup._update();

      activeDetailPopup = popup;
      popup.on('close', () => {
        if (activeDetailPopup === popup) activeDetailPopup = null;
      });
      return popup;
    };

    // SNOW-499: fetch the resort detail body and anchor it in a popup at the
    // tapped resort pin. Mirrors openRegionPopup's fetch/setHTML shape.
    const openResortPopup = async (resortFeature) => {
      const resortId = resortFeature.properties && resortFeature.properties.id;
      if (resortId == null || !RESORT_POPUP_URL_TEMPLATE) return false;

      const url = RESORT_POPUP_URL_TEMPLATE.replace(
        '/resorts/0/popup/', `/resorts/${encodeURIComponent(resortId)}/popup/`,
      );
      try {
        const resp = await fetch(url, { headers: { Accept: 'application/json' } });
        if (!resp.ok) return false;
        const data = await resp.json();
        mountDetailPopup(resortFeature.geometry.coordinates, { html: data.html });
        return true;
      } catch (_err) {
        return false;
      }
    };

    // SNOW-499: favourites.js dispatches this once a resort-popup favourite
    // toggle (star tap), or a favourite rename/delete, has been submitted, so
    // the popup — whose state was captured at open time — closes rather than
    // showing stale content. The next tap on the same pin re-fetches fresh
    // state.
    document.addEventListener('snowdesk:resort-popup-close', closeDetailPopup);
    document.addEventListener('snowdesk:favourite-detail-close', closeDetailPopup);

    // Pin-positioning focus (static/js/map_placement_focus.js) clears every
    // app layer off the basemap while the user places a favourite, an
    // observation, or a resort. The popups are the other things floating over
    // the map, and one anchored to a region that is no longer drawn is a
    // leftover rather than context — dismiss both on entry. Nothing reopens
    // them on exit: by then the user has usually panned the map, so the old
    // anchor no longer points at anything they are looking at. The region
    // *selection* is untouched (its highlight is a layer, so it comes back
    // with the rest), which is why this is closePopupOnly and not clearTooltip.
    document.addEventListener('snowdesk:placement-focus', (e) => {
      if (!(e.detail && e.detail.active)) return;
      closePopupOnly();
      closeDetailPopup();
    });

    // Clear the selection state for the currently-focused region (deselects
    // the map highlight) and keep the URL hash in sync. Called on genuine
    // empty-map-canvas taps and on back-button navigation.
    const clearSelectionDom = () => {
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
        // triggerRepaint ensures the regions-line-selected layer (which reads
        // feature-state via paint line-opacity) redraws immediately.
        map.triggerRepaint();
        selectedId = null;
      }
    };

    // User-facing dismiss path. Clears the selection and keeps the URL hash
    // in sync with the history stack:
    //
    //   - When we pushed the current history entry, pop it via
    //     history.back(). The popstate handler then calls clearSelectionDom.
    //   - When the current entry is the one the user landed on, replace
    //     the hash via replaceState and clear selection directly.
    const clearTooltip = () => {
      if (popstateInProgress) {
        clearSelectionDom();
        return;
      }
      if (popupHashWasPushed) {
        history.back();
        return;
      }
      if (popupHistoryOpen) {
        const cleanUrl = location.pathname + location.search;
        history.replaceState(null, '', cleanUrl);
        popupHistoryOpen = false;
      }
      clearSelectionDom();
    };

    // Push or replace the URL hash to point at ``regionID``. First open
    // of a session pushes a single entry; subsequent region taps replace
    // it so the back stack grows by exactly one no matter how many
    // regions the user sweeps through. ``popupHashWasPushed`` only
    // flips on the pushState branch — replaceState doesn't change the
    // pushed-ness of the underlying entry, so an initial-load hash that
    // gets retargeted via replaceState still isn't safe to ``history.back``.
    const syncUrlForRegion = (regionID) => {
      const hash = '#' + regionID;
      const state = { popup: regionID };
      if (!popupHistoryOpen) {
        history.pushState(state, '', hash);
        popupHistoryOpen = true;
        popupHashWasPushed = true;
      } else {
        history.replaceState(state, '', hash);
      }
    };

    // Deselect the currently-focused region: drop the highlight, clear the URL
    // hash, and tell the ribbon/readout there is no region. Shared by the
    // empty-canvas tap and the re-tap-to-deselect gesture so both produce
    // exactly the same end state.
    //
    // Sequencing matters: closePopupOnly must run before clearTooltip, which
    // resets activePopup/activePopupRegion — the 'close' teardown needs those
    // references live. (No user gesture opens a region popup any more, but the
    // call keeps a programmatically-opened one from being orphaned.)
    const deselectRegion = () => {
      closePopupOnly();
      closeDetailPopup();
      clearTooltip();
      document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
        detail: { region_id: null, region_name: null },
      }));
    };

    // Re-usable selection logic. Both the map click handler and the search
    // dropdown route through this so "make this region the active one" has
    // a single definition.
    //
    // Selecting a region no longer opens the region popup — it only moves the
    // selection (highlight + ribbon + readout + hash). Re-tapping the selected
    // region deselects it, but that toggle lives in the map click handler
    // rather than here: popstate/hashchange/initial-load all call selectFeature
    // with the id that may already be selected, and those must be no-ops.
    //
    // ``urlMode`` controls how the URL hash is reconciled after the popup opens:
    //   'push' (default, user-initiated) — writes the hash via push/replaceState.
    //   'mark' — skips the write because the URL already matches (popstate,
    //   hashchange, initial load) and just records that our hash is the active
    //   history entry.
    const selectFeature = async (
      numericId,
      { urlMode = 'push' } = {},
    ) => {
      // Already the active region — nothing to change. The deselect toggle is
      // the click handler's job (see deselectRegion), so that a repeated
      // popstate/hashchange for the same hash can't drop the selection.
      if (numericId === selectedId) return;

      // Switching to a different region: drop the old highlight first, then
      // silently dismiss any popup left over from another surface.
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
      }
      dismissActivePopupSilently();

      selectedId = numericId;
      map.setFeatureState({ source: 'regions', id: selectedId }, { selected: true });
      // SNOW-174: trigger an immediate repaint so the regions-line-selected
      // layer (paint line-opacity reads feature-state) activates on this frame.
      map.triggerRepaint();

      const props = REGION_LOOKUP[numericId];

      // Keep the URL hash in sync so the selected region stays deep-linkable
      // regardless of whether the popup fetch succeeds.
      if (urlMode === 'push') {
        syncUrlForRegion(props.regionID);
      } else if (urlMode === 'mark') {
        popupHistoryOpen = true;
      }

      // Announce the selection to the season ribbon + readout. Include the
      // display name and slug so the readout can label the region and build its
      // bulletin link without a second lookup.
      document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
        detail: {
          region_id: props.regionID,
          region_name: props.name || props.regionID,
          region_slug: props.slug || '',
          // SNOW-314 prototype: L2 (sub) + L1 (major) names so the season-header
          // readout can build a breadcrumb mirroring the visible map overlays.
          subregion_name: props.subregion_name || '',
          major_name: props.major_name || '',
        },
      }));

      if (AUTOZOOM) {
        const feature = FEATURE_BY_ID[numericId];
        if (feature) zoomToFeatureBounds(feature);
      }
    };

    // SNOW-445: favourite / community-observation / report-cluster markers sit
    // ON TOP of the region choropleth. MapLibre has no stopPropagation between
    // layer-scoped click handlers, so without a carve-out a tap on a marker
    // would BOTH activate the marker AND select the region under it — a jarring
    // double-action. These markers therefore own their tap: a click on a marker
    // glyph activates the marker and never selects the region beneath it.
    //
    // The hotspot is the glyph's own rendered hit-area — exactly the region
    // MapLibre uses to switch the desktop cursor to a pointer (the per-layer
    // mouseenter handlers below). markerUnderPoint() point-tests the same way,
    // so the exclusion zone lines up precisely with the pointer affordance: no
    // padding, and it honours each icon's anchor/offset (e.g. the flag glyph
    // anchored bottom-left) rather than a symmetric box around the cursor.
    //
    // Resort pins are deliberately NOT in this set — a resort pin is a proxy
    // for its parent region, so tapping one is meant to select that region.
    //
    // The layer order below is the priority order: it breaks ties when two
    // marker glyphs overlap under the tap (cluster > favourite > report).
    const MARKER_EXCLUSION_LAYERS = [
      'community-reports-clusters',
      'favourites-pin',
      'community-reports-point',
    ];

    // Return the highest-priority marker whose rendered glyph is under the tap
    // point, or null. Uses an exact-point queryRenderedFeatures — the same
    // hit-test that drives the pointer cursor — so the tappable area matches
    // what the user sees. Filters to layers actually present because these
    // overlays are lazy-installed and queryRenderedFeatures throws on an
    // unknown layer id.
    const markerUnderPoint = (point) => {
      const layers = MARKER_EXCLUSION_LAYERS.filter((id) => map.getLayer(id));
      if (!layers.length) return null;
      let best = null;
      let bestPriority = Infinity;
      for (const f of map.queryRenderedFeatures(point, { layers })) {
        const priority = MARKER_EXCLUSION_LAYERS.indexOf(f.layer.id);
        if (priority < bestPriority) {
          best = f;
          bestPriority = priority;
        }
      }
      return best;
    };

    // SNOW-419: tapping a cluster zooms in just far enough to break it apart
    // (the standard MapLibre clustered-source UX) rather than opening a
    // per-report popup. Guarded on the source existing since the overlay is
    // lazy-installed (see installCommunityReportsLayer / ensureOverlayLoaded).
    //
    // SNOW-445: MapLibre (this bundle is v4+) removed the old
    // getClusterExpansionZoom(clusterId, callback) signature — it now takes
    // just the clusterId and RETURNS A PROMISE. The previous callback form was
    // silently ignored, so the easeTo never fired and cluster taps did nothing.
    const activateCommunityCluster = (feature) => {
      const source = map.getSource('community-reports');
      if (!source) return;
      const clusterId = feature.properties.cluster_id;
      source.getClusterExpansionZoom(clusterId)
        .then((zoom) => {
          map.easeTo({ center: feature.geometry.coordinates, zoom });
        })
        .catch(() => {});
    };

    // SNOW-414 / SNOW-499: tapping an *existing* favourite pin opens its
    // rename/delete detail in a popup anchored to the pin — a favourite is a
    // point fixed to the map, so (like a resort or a region) its detail is a
    // pinned popup, not the docked sheet the mobile create/placement pin
    // uses. favourites.js owns the rename/delete markup + CSRF/URL wiring, so
    // map.js hands it an empty [data-favourite-detail] container to fill (via
    // the same snowdesk:favourite-selected contract), then anchors the filled
    // container in a popup at the favourite's coordinates. If favourites.js
    // isn't loaded (never happens for a rendered favourite pin — the layer is
    // eligibility-gated), the container stays empty and no popup opens.
    const activateFavourite = (feature) => {
      const props = feature.properties;
      const container = document.createElement('div');
      container.setAttribute('data-favourite-detail', '');
      document.dispatchEvent(new CustomEvent('snowdesk:favourite-selected', {
        detail: { uuid: props.uuid, name: props.name, container: container },
      }));
      if (!container.childNodes.length) return;
      mountDetailPopup(feature.geometry.coordinates, { node: container });
    };

    // SNOW-419/SNOW-472: tapping an unclustered community-report pin opens a
    // small popup with the observation type and a relative time — built via
    // DOM methods (not setHTML) since these values, though server-controlled,
    // don't need string-interpolated HTML. No region name: the pin's own
    // position on the map already conveys where the report is, so a place
    // label is redundant (and, since the FK region can be coarser or
    // cross-border than the visible spot, occasionally misleading).
    // Emits a marker-tapped telemetry signal with only the observation
    // type — no location or identity data.
    const activateCommunityReport = (feature) => {
      const props = feature.properties;
      const coordinates = feature.geometry.coordinates.slice();

      window.pwaTelemetry?.emit('map.community_reports.marker_tapped', {
        observation_type: props.type,
      });

      const container = document.createElement('div');
      container.className = 'community-report-popup';

      const typeEl = document.createElement('div');
      typeEl.className = 'community-report-popup__type';
      typeEl.textContent = props.type_label;
      container.appendChild(typeEl);

      // Relative time is computed live from the absolute observed_at against
      // Date.now() (formatRelativeTime), so it stays accurate even when the
      // overlay is served from the offline cache — only the age-fade opacity
      // is baked at fetch time, not this text.
      const relative = formatRelativeTime(props.observed_at);
      if (relative) {
        const metaEl = document.createElement('div');
        metaEl.className = 'community-report-popup__meta';
        metaEl.textContent = relative;
        container.appendChild(metaEl);
      }

      new maplibregl.Popup({
        closeButton: true,
        closeOnClick: true,
        maxWidth: '240px',
        className: 'community-report-popup-wrapper',
        // SNOW-472: pin the popup ABOVE the flag. The flag icon is anchored
        // bottom-left at the coordinate, so it occupies the space up and to
        // the right of the point; a default-anchored popup sits over it.
        // Fixing anchor to 'bottom' and lifting the popup by roughly the
        // flag's rendered height (icon-size 0.6 × 34px ≈ 20px) plus a small
        // gap puts the popup's tip just above the flag, pointing down at it.
        anchor: 'bottom',
        offset: [8, -24],
      })
        .setLngLat(coordinates)
        .setDOMContent(container)
        .addTo(map);
    };

    // Dispatch a marker the exclusion zone claimed to its activation, by layer.
    const activateMarker = (feature) => {
      switch (feature.layer.id) {
        case 'community-reports-clusters':
          activateCommunityCluster(feature);
          break;
        case 'favourites-pin':
          activateFavourite(feature);
          break;
        case 'community-reports-point':
          activateCommunityReport(feature);
          break;
      }
    };

    // Double-click always zooms to the region regardless of AUTOZOOM setting,
    // and prevents the default map double-click zoom so we control the target.
    map.on('dblclick', 'regions-fill', (e) => {
      e.preventDefault();
      if (!e.features.length) return;
      const feature = FEATURE_BY_ID[e.features[0].id];
      if (feature) zoomToFeatureBounds(feature);
    });

    // SNOW-445: single map click dispatcher. MapLibre fires this generic
    // (non-layer-scoped) click after any layer-scoped handlers, and — unlike
    // layer-scoped handlers — it also fires for synthetic MAP.fire('click')
    // calls, so it is the one reliable seam for both real taps and the e2e
    // harness. Consolidating region/resort/marker/empty-area intent here (the
    // per-layer click handlers were removed) means one place decides
    // marker-vs-region, with no cross-handler ordering races.
    //
    // Priority: overlay controls > markers (exclusion zone) > resort pin >
    // region fill > empty-area deselect.
    map.on('click', (e) => {
      // SNOW-314: clicks that originate on an overlaid UI control (the
      // scrubber/timeline, legend, search/basemap cluster, intro card, help
      // coachmark) are not map-area taps — they must never select or deselect
      // a region. Without this guard a scrub-tap would read as an empty-map
      // click and drop the selection.
      const tgt = e.originalEvent && e.originalEvent.target;
      if (
        tgt && tgt.closest &&
        // SNOW-457: #map-help-overlay — the coachmark tooltip's Back/Next/Skip
        // buttons sit over the map, same reasoning as the other overlays.
        tgt.closest('.season-scrubber, #map-utility-cluster, #map-controls-br, #map-legend, #home-intro, #map-help-overlay')
      ) {
        return;
      }

      // SNOW-445: a tap on a marker glyph owns the tap outright — activate it
      // and never fall through to region select/deselect. This is what stops a
      // favourite/observation tap from also selecting (and popping the tooltip
      // of) the region underneath it. Markers stay tappable during timelapse
      // playback, matching the pre-consolidation behaviour.
      const marker = markerUnderPoint(e.point);
      if (marker) {
        activateMarker(marker);
        return;
      }

      // No marker claimed the tap. Resolve region intent from the fill layer
      // and the resort pins. SNOW-235: both are lazy-installed, so filter to
      // present layers — queryRenderedFeatures throws on an unknown layer id.
      const layers = ['regions-fill', 'resorts-pin'].filter((id) => map.getLayer(id));
      const features = layers.length
        ? map.queryRenderedFeatures(e.point, { layers })
        : [];

      if (features.length === 0) {
        // Genuine tap on empty map area (outside any region) — deselects the
        // region (greys the ribbon, drops the readout to date-only, removes the
        // highlight) and closes any anchored resort/favourite popup. Runs even
        // during playback (unchanged from before).
        deselectRegion();
        return;
      }

      // A region or resort was tapped. Timelapse playback suppresses selection
      // changes (mirrors the old regions-fill IS_PLAYING guard).
      if (IS_PLAYING) return;

      // SNOW-499: a resort pin now opens its own minimal popup (name, region,
      // favourite star, bulletin link) instead of proxying to the parent
      // region's selection (former SNOW-78 behaviour) — the popup's "View
      // bulletin" link is the replacement path to the region.
      const resort = features.find((f) => f.layer.id === 'resorts-pin');
      if (resort) {
        openResortPopup(resort);
        return;
      }

      const region = features.find((f) => f.layer.id === 'regions-fill');
      if (region) {
        // Tapping the region that is already selected deselects it — the same
        // end state as an empty-canvas tap, without hunting for a gap in the
        // choropleth to tap.
        if (region.id === selectedId) {
          deselectRegion();
          return;
        }
        // SNOW-499: close any anchored resort/favourite detail popup so it
        // doesn't linger over a region the user has moved on from.
        closeDetailPopup();
        selectFeature(region.id);
      }
    });

    map.on('mouseenter', 'regions-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'regions-fill', () => { map.getCanvas().style.cursor = ''; });

    map.on('mouseenter', 'resorts-pin', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'resorts-pin', () => { map.getCanvas().style.cursor = ''; });

    map.on('mouseenter', 'favourites-pin', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'favourites-pin', () => { map.getCanvas().style.cursor = ''; });

    // SNOW-414: favourites.js dispatches this after a successful
    // create/rename/delete so the map's own pin layer reflects the change
    // without a full page reload. Installs the layer first if this is the
    // very first favourite the user has ever saved (overlayLoaded.favourites
    // is only true once ensureOverlayLoaded('favourites') has run, which
    // requires at least one prior fetch — install here covers the case
    // where an ineligible-at-boot state doesn't apply, since the toggle
    // itself is eligible-gated).
    document.addEventListener('snowdesk:favourites-changed', () => {
      if (!FAVOURITES_ELIGIBLE || !FAVOURITES_URL) return;
      fetch(FAVOURITES_URL).then(r => r.json()).then((fc) => {
        // Authoritative server state replaces the whole collection — this is
        // what drops any optimistic ``pending`` feature once the real pin
        // lands (SNOW-479). Keep the cache in sync so a subsequent optimistic
        // append starts from current truth.
        favouritesGeojsonCache = fc;
        // SNOW-499: recompute before installFavouritesLayer's own call so a
        // resort favourited/unfavourited elsewhere (e.g. the resort popup
        // star) is reflected on the resorts layer as soon as this refetch
        // lands, regardless of which branch below runs.
        syncFavouritedResortIds(fc);
        const source = map.getSource('favourites');
        if (source) {
          source.setData(fc);
        } else {
          installFavouritesLayer(fc);
          overlayLoaded.favourites = true;
        }
      }).catch(() => {});
    });

    // SNOW-479: favourites.js dispatches this the instant an offline (or
    // online) create is enqueued on the mutation queue, so the saved pin is
    // visible immediately without waiting for the queued POST to replay. We
    // append a synthetic ``pending`` feature (no uuid) to the current
    // collection and setData; installing the layer first if this is the user's
    // very first favourite. The pending pin is replaced by the authoritative
    // server pin when the queue drains and re-dispatches
    // snowdesk:favourites-changed above (or dropped there on a permanent
    // failure). Renders at half opacity via the favourites-pin icon-opacity
    // expression.
    document.addEventListener('snowdesk:favourite-pending', (event) => {
      if (!FAVOURITES_ELIGIBLE) return;
      const detail = (event && event.detail) || {};
      const lat = Number(detail.lat);
      const lon = Number(detail.lon);
      if (Number.isNaN(lat) || Number.isNaN(lon)) return;

      const feature = {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lon, lat] },
        properties: { name: detail.name || '', pending: true },
      };
      const base =
        favouritesGeojsonCache && Array.isArray(favouritesGeojsonCache.features)
          ? favouritesGeojsonCache.features
          : [];
      favouritesGeojsonCache = {
        type: 'FeatureCollection',
        features: base.concat([feature]),
      };

      const source = map.getSource('favourites');
      if (source) {
        source.setData(favouritesGeojsonCache);
      } else {
        installFavouritesLayer(favouritesGeojsonCache);
        overlayLoaded.favourites = true;
      }
    });

    // SNOW-445: the community-reports cluster and point taps are now dispatched
    // through the single generic click handler above (activateCommunityCluster /
    // activateCommunityReport), so both markers get the same exclusion-zone
    // treatment as favourites. Only the hover-cursor affordances stay bound per
    // layer here.
    map.on('mouseenter', 'community-reports-clusters', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'community-reports-clusters', () => { map.getCanvas().style.cursor = ''; });

    map.on('mouseenter', 'community-reports-point', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'community-reports-point', () => { map.getCanvas().style.cursor = ''; });

    // ---- History wiring (SNOW-39) ----
    //
    // Resolve the current ``location.hash`` against ``FEATURE_BY_REGION_ID``.
    // Returns the numeric feature id when the hash names a known region,
    // ``null`` when the hash is absent or names a region we don't have.
    // Reuses ``REGION_ID_RE`` so the hash, the GeoJSON-id check, and the
    // CTA href validation all share one definition of "a valid region id".
    const featureIdFromHash = () => {
      const regionID = location.hash.slice(1);
      if (!regionID || !REGION_ID_RE.test(regionID)) return null;
      const feature = FEATURE_BY_REGION_ID[regionID];
      return feature ? feature.id : null;
    };

    // popstate fires on browser back/forward. We do not push during this
    // handler (selectFeature is called with urlMode='mark' so it just
    // records that our hash is the active entry), and ``clearTooltip``
    // takes the ``popstateInProgress`` branch so it doesn't re-pop.
    window.addEventListener('popstate', (event) => {
      popstateInProgress = true;
      try {
        const numericId = featureIdFromHash();
        if (numericId !== null) {
          // Entries we pushed via syncUrlForRegion carry { popup: regionID }
          // in their state; the initial-load entry has null state. Use that
          // to decide whether a later close can safely ``history.back``.
          popupHashWasPushed = !!(event.state && event.state.popup);
          selectFeature(numericId, { urlMode: 'mark' });
        } else {
          popupHistoryOpen = false;
          popupHashWasPushed = false;
          closePopupOnly();
          clearSelectionDom();
        }
      } finally {
        popstateInProgress = false;
      }
    });

    // hashchange fires when the user edits the fragment in the URL bar.
    // (popstate also fires for back/forward — both events fire for that
    // case and the second one is a harmless no-op because selectFeature
    // returns early when numericId === selectedId, and clearSelectionDom
    // is idempotent.)
    window.addEventListener('hashchange', () => {
      const numericId = featureIdFromHash();
      if (numericId !== null) {
        popupHistoryOpen = true;
        // A hashchange adds a real history entry (unlike the initial-load
        // hash, which is part of the entry the user landed on), so a
        // subsequent close can safely pop it.
        popupHashWasPushed = true;
        selectFeature(numericId, { urlMode: 'mark' });
      } else if (location.hash === '' || location.hash === '#') {
        popupHistoryOpen = false;
        popupHashWasPushed = false;
        closePopupOnly();
        clearSelectionDom();
      }
    });

    window.addEventListener('keydown', (e) => {
      // Toggle debug mode; ignore when typing in an input/textarea.
      if (e.key === 'd' && !e.target.matches('input, textarea')) {
        DEBUG = !DEBUG;
      }
    });

    // --- Search ---
    //
    // In-memory autocomplete over region names + resort names. All data
    // is already resident after the initial load, so search is purely
    // local — no server round-trip per keystroke, no indexing cost worth
    // worrying about (a few hundred entries total).

    // SNOW-618: the matching, ordering and entry-shaping live in
    // static/js/search_core.js so they can be unit-tested without a
    // MapLibre instance — same *_core.js split scrubber_core.js and
    // basemap_download_core.js use. What stays here is the index itself
    // and everything that touches the DOM or the map.
    const searchCore = self.pwaSearchCore;

    const SEARCH_INDEX = [];

    // Track which regions are already in the index so indexRegion is safe
    // to call multiple times (e.g. from the snowdesk:regions-loaded listener
    // after a country lazy-loads — the CH entries are already present).
    const INDEXED_REGIONS = new Set();

    const indexRegion = (props) => {
      const regionID = props && props.regionID;
      if (!regionID || INDEXED_REGIONS.has(regionID)) return;
      const entry = searchCore.buildEntry(props, RESORTS_BY_REGION[regionID] || []);
      if (!entry) return;
      INDEXED_REGIONS.add(regionID);
      SEARCH_INDEX.push(entry);
    };

    for (const props of Object.values(REGION_LOOKUP)) {
      indexRegion(props);
    }

    // Extend the index whenever a country's GeoJSON is lazy-loaded.
    document.addEventListener('snowdesk:regions-loaded', (e) => {
      for (const regionID of e.detail.regionIDs) {
        const feature = FEATURE_BY_REGION_ID[regionID];
        if (feature) indexRegion(feature.properties);
      }
    });

    const runSearch = (query) => searchCore.runSearch(SEARCH_INDEX, query);

    const inputEl = document.getElementById('search-input');
    const resultsEl = document.getElementById('search-results');
    const pillEl = document.getElementById('search-pill');
    const toggleEl = document.getElementById('search-toggle');
    let currentResults = [];
    let activeIdx = -1;
    // SNOW-188: The map feature id currently highlighted as a search
    // preview (keyboard/pointer hover over a search result). Carried via
    // its own ``previewing`` feature-state so it stacks independently of
    // the click-driven ``selected`` state and clears cleanly when the
    // dropdown closes — no popup, just the highlight.
    let previewedFeatureId = null;

    const setPreview = (regionID) => {
      const nextId = regionID ? FEATURE_BY_REGION_ID[regionID]?.id : null;
      if (nextId === previewedFeatureId) return;
      if (previewedFeatureId !== null) {
        map.setFeatureState(
          { source: 'regions', id: previewedFeatureId },
          { previewing: false },
        );
      }
      previewedFeatureId = nextId ?? null;
      if (previewedFeatureId !== null) {
        map.setFeatureState(
          { source: 'regions', id: previewedFeatureId },
          { previewing: true },
        );
      }
      // regions-line-selected paints line-opacity from feature-state,
      // which doesn't trigger an automatic redraw — match the
      // selectFeature path and nudge MapLibre into repainting now.
      map.triggerRepaint();
    };

    // Pill expansion — the collapsed default shows only the icon toggle.
    // Tapping it switches the pill into the expanded state, which reveals
    // the input (CSS transition) and moves focus. The pill stays expanded
    // as long as the user is interacting with it; Escape or an outside
    // pointerdown collapses it back (see handlers below).
    const openSearch = () => {
      pillEl.setAttribute('data-state', 'expanded');
      toggleEl.setAttribute('aria-expanded', 'true');
      // Defer focus one frame so the width transition starts before the
      // caret appears — avoids a flash of the input at width 0.
      window.requestAnimationFrame(() => inputEl.focus());
      if (inputEl.value) renderResults(runSearch(inputEl.value));
    };

    const collapseSearch = () => {
      pillEl.setAttribute('data-state', 'collapsed');
      toggleEl.setAttribute('aria-expanded', 'false');
      closeResults();
      inputEl.blur();
    };

    toggleEl.addEventListener('click', (e) => {
      // When already expanded, the toggle is just the leading icon of a
      // live search input — swallow the click so users don't accidentally
      // collapse mid-query. Escape or an outside pointerdown is the
      // deliberate collapse path.
      if (pillEl.getAttribute('data-state') === 'expanded') return;
      e.preventDefault();
      openSearch();
    });

    const closeResults = () => {
      resultsEl.hidden = true;
      inputEl.setAttribute('aria-expanded', 'false');
      inputEl.removeAttribute('aria-activedescendant');
      activeIdx = -1;
      setPreview(null);
    };

    const setActive = (idx) => {
      const items = resultsEl.children;
      if (activeIdx >= 0 && items[activeIdx]) items[activeIdx].classList.remove('active');
      activeIdx = idx;
      if (idx >= 0 && items[idx]) {
        items[idx].classList.add('active');
        inputEl.setAttribute('aria-activedescendant', items[idx].id);
        items[idx].scrollIntoView({ block: 'nearest' });
      } else {
        inputEl.removeAttribute('aria-activedescendant');
      }
      // Mirror the active row on the map as a preview highlight — same
      // visual as a click selection but without opening the popup.
      // When idx === -1 (Enter not pressed yet, ArrowUp past the top),
      // preview the first result so the highlight tracks what Enter
      // would pick.
      const previewItem = currentResults[idx >= 0 ? idx : 0];
      setPreview(previewItem ? previewItem.regionID : null);
    };

    const renderResults = (results) => {
      resultsEl.replaceChildren();
      currentResults = results;
      activeIdx = -1;
      if (results.length === 0) {
        closeResults();
        return;
      }
      results.forEach((r, i) => {
        const li = document.createElement('li');
        li.className = 'search-result';
        li.setAttribute('role', 'option');
        li.id = `search-result-${i}`;

        // Text column (primary/secondary) and a region-ID badge side by side.
        // All rows are regions; the badge carries the EAWS region ID
        // (e.g. CH-4115) so users can see the canonical identifier.
        const text = document.createElement('div');
        text.className = 'search-result-text';
        const primary = document.createElement('div');
        primary.className = 'search-result-primary';
        primary.textContent = r.primary;
        const secondary = document.createElement('div');
        secondary.className = 'search-result-secondary';
        // Secondary line: parent L2 sub-region name (or empty when the
        // fixture has no descriptive L2 — AT/IT). Resorts are searched
        // against but not rendered; see indexRegion for why.
        secondary.textContent = r.subregionName;
        text.append(primary, secondary);

        const badge = document.createElement('span');
        badge.className = 'search-result-badge';
        // SNOW-188: Prefix the region-ID code with the country flag.
        // Country is the ISO-3166 alpha-2 prefix of the EAWS region ID
        // (CH-4115 → ch). Only the four countries with flag symbols in
        // the map.html sprite render a flag; an unknown prefix falls
        // back to the bare ID rather than a broken <use>.
        const country = (r.regionID.split('-')[0] || '').toLowerCase();
        if (['ch', 'at', 'fr', 'it'].includes(country)) {
          const flag = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
          flag.setAttribute('class', `search-result-flag search-result-flag--${country}`);
          flag.setAttribute('aria-hidden', 'true');
          const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
          use.setAttribute('href', `#flag-${country}`);
          flag.append(use);
          badge.append(flag);
        }
        badge.append(document.createTextNode(r.secondary));

        li.append(text, badge);
        // Use pointerdown rather than click so we act before the input's
        // blur handler closes the dropdown. pointerdown covers both mouse
        // and touch — mousedown alone is unreliable on iOS Safari, where
        // the synthesised mousedown after touchend can be skipped.
        li.addEventListener('pointerdown', (e) => {
          e.preventDefault();
          chooseResult(r);
        });
        resultsEl.append(li);
      });
      resultsEl.hidden = false;
      inputEl.setAttribute('aria-expanded', 'true');
      // Preview the top match before any arrow-key press so the highlight
      // tracks what Enter would pick. setActive(-1) keeps activeIdx
      // unanchored (so ArrowDown still lands on row 0) but routes the
      // first result through setPreview.
      setActive(-1);
    };

    const chooseResult = (item) => {
      const feature = FEATURE_BY_REGION_ID[item.regionID];
      if (!feature) return;
      inputEl.value = item.primary;
      collapseSearch();
      // Moves the selection (highlight + ribbon + readout + hash). Picking the
      // region that is already selected is a no-op — the deselect toggle is a
      // map gesture only, so a search pick never clears the selection.
      selectFeature(feature.id);
    };

    inputEl.addEventListener('input', () => {
      renderResults(runSearch(inputEl.value));
    });

    inputEl.addEventListener('focus', () => {
      if (inputEl.value) renderResults(runSearch(inputEl.value));
    });

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') {
        if (!currentResults.length) return;
        e.preventDefault();
        setActive(Math.min(activeIdx + 1, currentResults.length - 1));
      } else if (e.key === 'ArrowUp') {
        if (!currentResults.length) return;
        e.preventDefault();
        // Allow ArrowUp past index 0 back to -1, returning focus to the
        // free-typed state (ARIA APG combobox pattern — keyboard users
        // must not get trapped inside the list).
        setActive(Math.max(activeIdx - 1, -1));
      } else if (e.key === 'Enter') {
        const pick = activeIdx >= 0 ? currentResults[activeIdx] : currentResults[0];
        if (pick) {
          e.preventDefault();
          chooseResult(pick);
        }
      } else if (e.key === 'Escape') {
        if (inputEl.value) {
          inputEl.value = '';
          closeResults();
        } else {
          collapseSearch();
        }
      }
    });

    // Collapse the pill and close the dropdown on outside pointer
    // interaction. Use pointerdown (before focus changes) and ignore
    // clicks inside the pill itself or the results list so li and
    // toggle handlers still fire.
    document.addEventListener('pointerdown', (e) => {
      if (pillEl.contains(e.target)) return;
      if (resultsEl.contains(e.target)) return;
      collapseSearch();
    });

    // ---- Initial-load hash → region focus (SNOW-39) ----
    //
    // If the user landed on ``/map/#CH-xxxx``, open the popup for that
    // region. ``urlMode: 'mark'`` because the URL already matches —
    // selectFeature just needs to record that our hash is the active
    // history entry. Unknown / malformed hashes are silently ignored.
    const initialFeatureId = featureIdFromHash();
    if (initialFeatureId !== null) {
      selectFeature(initialFeatureId, { urlMode: 'mark' });
    }

    // SNOW-58: re-install our source + layers when a new basemap style
    // finishes loading. ``style.load`` only fires reliably for the
    // initial style; ``setStyle()`` doesn't always re-emit it (known
    // quirk in MapLibre 4.x). ``styledata`` is the dependable signal —
    // it fires multiple times during setStyle, so the install function
    // is idempotent (early-returns when the source is already present)
    // and we gate the rest on whether the source needs re-adding.
    //
    // setStyle wipes all sources, layers, and feature-state added on
    // top of the previous style. Layer-bound event handlers (the click
    // / mouseenter / mouseleave wires above) survive because they're
    // bound by layer id — re-adding a layer with the same id revives
    // them. Feature-state does not survive: we restore the selection
    // outline here, and any non-today date paint via the URL-resident
    // ``?d=`` and the shared ratings cache.
    map.on('styledata', () => {
      if (!geojsonCache) return;          // initial load — handled above
      // Gate on the fill LAYER, not just the source: a setStyle that leaves
      // the 'regions' source behind while dropping its layers (see
      // installRegionsLayers) would otherwise satisfy a source-only guard
      // and skip the whole reinstall, stranding the overlay.
      if (map.getSource('regions') && map.getLayer('regions-fill')) return;

      // SNOW-473: overlayState is seeded once at boot and never updated by the
      // picker (which writes localStorage + the live layer only), so re-seeding
      // layer visibility from it after a basemap swap would revert every tier to
      // its boot value. Re-sync from the localStorage shadow (the source of truth
      // the picker keeps current) before any install fn reads overlayState.
      // Mirrors the boot-seed near line 350 — keep the two in sync when adding
      // an overlay key.
      for (const key of ['l1', 'l2', 'resorts', 'community_reports', 'downloaded']) {
        overlayState[key] = readBoolStorage(OVERLAY_STORAGE_KEY[key], false);
      }
      // l4 is re-seeded before any install fn runs because the bulletin
      // boundary's visibility is derived from it, not from a key of its own.
      overlayState.l4 = readBoolStorage(OVERLAY_STORAGE_KEY.l4, true);
      overlayState.favourites = readBoolStorage(OVERLAY_STORAGE_KEY.favourites, true);

      // SNOW-478: the new basemap has its own glyph server and fonts, so
      // re-derive the overlay label font before re-installing any layer.
      overlayTextFont = deriveOverlayTextFont();

      // setStyle wipes every source and layer we added, including the
      // merged multi-country caches. The per-install BASE_LAYER_FILTERS
      // snapshot is also stale (it referenced layers that no longer exist).
      // Re-install with whatever is currently in the caches, then clear
      // loadedCountries (except CH, which is always present in geojsonCache)
      // and re-fetch any country that was active but whose data lived only
      // in the old merged source. Without this, re-toggling a country that
      // was loaded before the basemap switch is a no-op (loadedCountries
      // still has the code), so the data never comes back.
      installRegionsLayers(geojsonCache);
      // SNOW-59: overlays got wiped with the rest of the style. Re-add
      // them and let the install function re-apply the persisted
      // visibility from overlayState.
      installOverlayLayers(majorGeojsonCache, subGeojsonCache);
      // SNOW-78: same story for the resorts pin layer.
      installResortsLayer(resortsGeojsonCache);
      // SNOW-323: Re-install the bulletin-groupings layer if L3 was enabled
      // before the basemap swap. Seed it with the last-drawn FC (no refetch);
      // if it was mid-scrub and blanked, seed empty and let the next settle
      // redraw.
      if (overlayLoaded.l3) {
        installBulletinGroupingsLayer(
          groupingsDrawn ? currentGroupingsFC : EMPTY_FEATURE_COLLECTION,
        );
      }
      // SNOW-419: re-install the community-reports layer if it was enabled
      // before the basemap swap, seeded from the last-fetched cache (no
      // refetch).
      if (overlayLoaded.community_reports) {
        installCommunityReportsLayer(communityReportsGeojsonCache);
      }
      // SNOW-493 finding 2: favourites was never re-installed here, so a
      // basemap swap silently dropped every favourite pin even though
      // favouritesGeojsonCache still held the data (fetched once, never
      // re-requested). Re-install it whenever it was loaded before the
      // swap, mirroring the community-reports reinstall above.
      if (overlayLoaded.favourites) {
        installFavouritesLayer(favouritesGeojsonCache);
      }

      // SNOW-172 / SNOW-493 finding 3: re-apply country filters for the
      // freshly-installed layers. geojsonCache/majorGeojsonCache/
      // subGeojsonCache already hold every merged country's features —
      // installRegionsLayers/installOverlayLayers above installed that
      // complete merged data as-is — so no re-fetch is needed here.
      // Previously this block cleared ``loadedCountries`` down to just
      // ``ch`` and called ``ensureCountryLoaded`` again for every
      // currently-enabled country, which re-fetched and re-merged data the
      // caches already contained, duplicating every foreign region's
      // features on each basemap swap. ``loadedCountries`` itself is
      // untouched by ``setStyle`` (it isn't reset elsewhere), so it still
      // accurately reflects what's already merged into the caches above —
      // nothing further to load.
      applyCountryFilters();

      if (selectedId !== null) {
        map.setFeatureState(
          { source: 'regions', id: selectedId },
          { selected: true },
        );
      }
      const dateKey = readUrlDateParam();
      if (dateKey) {
        getSeasonRatings()
          .then((ratings) => repaintRegionsForDate(dateKey, ratings))
          .catch(() => { /* network fail → leave today's colours */ });
      }

      // A new basemap style has just loaded and its overlays are back.
      // Notify per-basemap consumers (the region-download icon re-probes its
      // done-state against the new basemap's tile template; anything else
      // that cares about "which basemap am I on now") — the reinstall body
      // only runs on a genuine style change, so this fires once per swap.
      document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
    });

    // Recovery path for the micro-region (L4) overlay: if its layers are
    // gone but the cached GeoJSON is still in memory, rebuild them on
    // demand. The picker's L4 toggle dispatches this before making the
    // layers visible, so toggling Micro regions back on ALWAYS restores
    // them — even in the edge case where a style swap dropped the layers
    // and the styledata reinstall didn't re-add them. Mirrors the tail of
    // the styledata reinstall (selection + date repaint) for the regions
    // source specifically.
    document.addEventListener('snowdesk:regions-reinstall', () => {
      if (!geojsonCache || map.getLayer('regions-fill')) return;
      installRegionsLayers(geojsonCache);
      applyCountryFilters();
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: true });
      }
      const dateKey = readUrlDateParam();
      if (dateKey) {
        getSeasonRatings()
          .then((ratings) => repaintRegionsForDate(dateKey, ratings))
          .catch(() => { /* offline → keep today's colours */ });
      }
    });

    // SNOW-318: Refresh the open popup's colour, digit, date label, and bulletin
    // link when the scrubber commits a new date, using only the preloaded season
    // ratings cache — no API fetch.
    //
    // This mirrors updateReadout() in seasonRibbonInit (map.js:~2640) which does
    // the same local lookup for the persistent readout pill.
    //
    // Documented limitation: the local update recolours/relabels the elements the
    // initial server render produced; it does NOT restructure between the rated
    // (danger chip + bulletin link) and no-rating (icon + muted text) layouts
    // when scrubbing across a data gap. That boundary is an edge case for a
    // focused region within its season, and re-clicking the region re-fetches the
    // correct layout. This is the deliberate trade-off for "no API call on date
    // change" — keeping the popup in sync with the pill without a round-trip.
    const refreshPopupForDate = async (dateKey) => {
      if (!activePopup || !activePopupRegion) return;

      // Snapshot the region reference before the async gap so we can detect
      // if a different region took over while we were awaiting the cache.
      const snapRegion = activePopupRegion;

      // Resolve the season ratings cache (already in-flight or cached — no
      // extra network request). If it hasn't settled yet, bail silently; the
      // next scrub after it resolves will update correctly.
      let cache = null;
      try {
        cache = await getSeasonRatings();
      } catch (_err) {
        return;
      }
      if (!activePopup || !activePopupRegion) return;  // popup closed during await

      // Stale-closure guard: if a different region was selected during the
      // await above, activePopupRegion is repointed to the new region while
      // regionID/slug are still bound to the old one. Bail so we don't
      // overwrite the new region's popup with the old region's href.
      if (activePopupRegion !== snapRegion) return;

      const { regionID, slug } = snapRegion;
      const ratingInt = cache && cache[dateKey] ? cache[dateKey][regionID] : undefined;
      const key = (ratingInt != null ? INT_TO_RATING[ratingInt] : null) || 'no_rating';

      const el = activePopup.getElement();
      if (!el) return;

      // Recolour the popup border — CSS targets [data-level] on the root.
      el.setAttribute('data-level', key);

      // Update the danger chip: data-level drives the background colour via
      // .region-popup .danger-tile[data-level=…]; the digit is the integer
      // rating.
      // Documented limitation: the chip digit is recoloured/renumbered from
      // the integer rating only. Any max_subdivision suffix (e.g. "3+") that
      // the server template rendered on first open is not reapplied here —
      // the ratings cache holds integer levels only, not subdivision strings.
      // Re-clicking the region re-fetches the exact server-rendered chip.
      const tile = el.querySelector('.danger-tile');
      if (tile) {
        tile.setAttribute('data-level', key);
        tile.textContent = ratingInt != null ? String(ratingInt) : '';
      }

      // Update the bulletin link text and href. formatDatePopup matches the
      // server render's ``date:"j M Y"`` so the label is unchanged in format
      // when the popup is relabelled in place.
      // SNOW-620: the date is substituted by NAME, not concatenated — a
      // locale is free to put it anywhere in the sentence, or to need
      // different surrounding words on either side of it.
      const link = el.querySelector('.region-tooltip-bulletin-link');
      if (link) {
        link.textContent = self.pwaStrings.interpolate(MAP_STRINGS['bulletin-link'], {
          date: formatDatePopup(dateKey),
        });
        link.href = '/' + regionID.toLowerCase() + '/' + slug + '/' + dateKey + '/';
      }

      // Update the no-bulletin date label (shown when there is no rated bulletin
      // for the date — the rated layout uses .region-tooltip-bulletin-link).
      // The template renders this as a plain <p> with inline text; there is no
      // child .region-tooltip-date element to update, so we set the full string.
      const noBulletin = el.querySelector('.region-tooltip-no-bulletin');
      if (noBulletin) {
        noBulletin.textContent = self.pwaStrings.interpolate(MAP_STRINGS['no-bulletin'], {
          date: formatDatePopup(dateKey),
        });
      }
    };
    // Publish to the outer-IIFE forwarding variable so the date-changed listener
    // registered before map.on('load') can reach it.
    _refreshPopupForDate = refreshPopupForDate;

    // SNOW-318: Timelapse start → close the popup silently. The highlight and
    // pill persist (seasonRibbonInit re-asserts the highlight on every
    // date-changed; the pill is independent of the popup). Closing the popup
    // during playback avoids the popup DOM becoming stale on every frame advance.
    document.addEventListener('snowdesk:timelapse-state', (e) => {
      if (e.detail && e.detail.playing === true) closePopupOnly();
    });

    // Signal to sibling IIFEs (scrubber) that the map style + regions
    // source are ready and setFeatureState calls will now stick. The
    // scrubber awaits this before painting the boot-time ?d= state.
    if (resolveMapReady) resolveMapReady();
  });
})();

// SNOW-47: Season-scrubber wires. Drag the thumb → release commits a
// date. The map repaints region colours for that date and the URL gets
// a ``?d=YYYY-MM-DD`` so the page is linkable. Loading ``/map/?d=…``
// on page boot drops the thumb on that date.
//
// The scrubber owns no data of its own — it consumes the same
// season-ratings payload as the timelapse via getSeasonRatings(), and
// announces date commits via the ``snowdesk:date-changed`` CustomEvent
// so the timelapse IIFE (stop on grab) and the date pill can react
// without seeing each other.
(function seasonScrubberInit() {
  const scrubber = document.getElementById('season-scrubber');
  if (!scrubber) return;
  const track = scrubber.querySelector('.season-scrubber-track');
  const thumb = scrubber.querySelector('.season-scrubber-thumb');
  const todayKey = scrubber.dataset.today;
  const todayPct = parseFloat(scrubber.dataset.todayPct);
  const seasonStartMs = Date.parse(scrubber.dataset.seasonStart);
  const seasonEndMs = Date.parse(scrubber.dataset.seasonEnd);
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  // Convert between a thumb percentage (0..100 along the track) and an
  // ISO date string. Both use the season bounds parsed above and round
  // to the nearest day — the scrubber is intentionally single-day
  // resolution (intraday is a future ticket).
  // SNOW-496: thin delegators — the actual math lives in scrubber_core.js
  // (window.pwaScrubberCore) so it can be unit-tested directly; every
  // closure value below is forwarded unchanged, so behaviour is identical.
  // Inline fallback mirrors the ``self.pwaBasemapCacheCore ||`` idiom in
  // sw.js, so a transient scrubber_core.js load failure can't break the
  // scrubber — the fallback body is byte-identical to the pre-extraction
  // inline code.
  const pctToDateKey = (pct) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.pctToDateKey(pct, seasonStartMs, seasonSpanMs);
    const ms = seasonStartMs + (pct / 100) * seasonSpanMs;
    const day = new Date(ms);
    // Snap to UTC midnight to dodge DST edges, then format.
    const y = day.getUTCFullYear();
    const m = String(day.getUTCMonth() + 1).padStart(2, '0');
    const d = String(day.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  };
  const dateKeyToPct = (dateKey) => {
    if (window.pwaScrubberCore) {
      return window.pwaScrubberCore.dateKeyToPct(dateKey, seasonStartMs, seasonSpanMs, todayPct);
    }
    const ms = Date.parse(dateKey);
    if (Number.isNaN(ms) || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) {
      return todayPct;
    }
    return Math.max(0, Math.min(100, ((ms - seasonStartMs) / seasonSpanMs) * 100));
  };

  // Cache + sorted-keys, populated lazily by the shared promise below.
  // Used both for repaint and for snap-to-data-day on release. Until the
  // fetch resolves, drag still works — it just won't snap to a real day
  // boundary, which is fine; the next release after the fetch resolves
  // will snap.
  let ratingsCache = null;
  let sortedDates = null;

  // SNOW-236: The effective "today" frame — the latest date in the merged
  // ratings cache that contains at least one visible country. Defaults to
  // todayKey (server-rendered) until the season-ratings fetch resolves and
  // proves that a later or earlier date is actually the last populated one.
  // Mutated by the getSeasonRatings callback and by snowdesk:country-ratings-loaded.
  let effectiveTodayKey = todayKey;

  // SNOW-236: Walk sortedDates from the end and return the latest entry
  // whose payload contains at least one region whose country prefix
  // (region_id.split('-')[0].toLowerCase()) matches an active country in
  // COUNTRY_STATE (the module-scope mirror of the main IIFE's countryState).
  // Falls back to the last entry if nothing matches.
  const deriveEffectiveTodayKey = (dates, cache) => {
    if (window.pwaScrubberCore) {
      return window.pwaScrubberCore.deriveEffectiveTodayKey(dates, cache, COUNTRY_STATE, todayKey);
    }
    if (!dates || dates.length === 0) return todayKey;
    // Dedupe unexpected-prefix warnings per call: a full-season payload
    // can contain hundreds of regions and we don't want one stray prefix
    // (e.g. a future Liechtenstein "LI-…") to spam the console once per
    // region per date.
    const warnedPrefixes = new Set();
    for (let i = dates.length - 1; i >= 0; i--) {
      const dateKey = dates[i];
      const frame = cache[dateKey] || {};
      for (const regionID of Object.keys(frame)) {
        const prefix = regionID.split('-')[0].toLowerCase();
        if (COUNTRY_STATE[prefix] === true) return dateKey;
        // Guard: unexpected prefix — don't silently swallow it.
        if (!(prefix in COUNTRY_STATE) && !warnedPrefixes.has(prefix)) {
          warnedPrefixes.add(prefix);
          console.warn('[map] SNOW-236: unexpected region prefix', prefix, 'in ratings payload');
        }
      }
    }
    // No date matched any active country — return the last date as a safe fallback.
    return dates[dates.length - 1];
  };

  // SNOW-234: transition the scrubber out of the loading state once the
  // season-ratings fetch settles. On success, populate the cache and mark
  // the scrubber ready so the transport controls become visible. On failure,
  // keep the loading placeholder visible (now showing an error message) so
  // the user sees feedback rather than a blank pill.
  getSeasonRatings().then((data) => {
    ratingsCache = data;
    sortedDates = Object.keys(data).sort();
    scrubber.dataset.state = 'ready';

    // SNOW-236: Compute the country-aware effective last date and snap
    // the thumb to it if the page has not been loaded with an explicit
    // ?d= param (i.e. the user hasn't deep-linked to a specific date).
    // Always commit silently so the date pill (server-rendered with today)
    // is corrected to the effective date — including the post-season case
    // where newEffective === BOOT_DATE_KEY but the pill still shows today.
    const newEffective = deriveEffectiveTodayKey(sortedDates, ratingsCache);
    effectiveTodayKey = newEffective;
    if (!readUrlDateParam()) {
      // Snap silently — no history entry, just reposition the thumb and repaint.
      Promise.all([MAP_READY_PROMISE]).then(() => {
        commitDate(newEffective, { silent: true });
      });
    }
  }).catch(() => {
    scrubber.dataset.state = 'error';
    const loadingEl = scrubber.querySelector('.season-scrubber-loading');
    if (loadingEl) loadingEl.textContent = MAP_STRINGS['season-unavailable'];
  });

  const snapToNearestDataDay = (dateKey) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.snapToNearestDataDay(dateKey, sortedDates);
    if (!sortedDates || sortedDates.length === 0) return dateKey;
    let best = sortedDates[0];
    let bestDelta = Math.abs(Date.parse(best) - Date.parse(dateKey));
    for (const d of sortedDates) {
      const delta = Math.abs(Date.parse(d) - Date.parse(dateKey));
      if (delta < bestDelta) { best = d; bestDelta = delta; }
    }
    return best;
  };

  // The single commit point. Updates the thumb, repaints regions, syncs
  // the URL, and notifies the rest of the page. ``opts.silent`` skips
  // the URL write — used by the popstate handler so re-applying a
  // browser-back-restored ``?d=`` doesn't re-write history.
  const commitDate = (dateKey, opts = {}) => {
    const isToday = dateKey === todayKey;
    const pct = dateKeyToPct(dateKey);
    thumb.style.left = pct + '%';
    scrubber.setAttribute('aria-valuenow', String(Math.round(pct)));
    if (ratingsCache) repaintRegionsForDate(dateKey, ratingsCache);
    if (!opts.silent) {
      // ``replaceState`` (never push) so a long scrub doesn't bury the
      // back button under dozens of intermediate dates. Today clears the
      // ``?d=`` param entirely, matching the canonical URL. Use the current
      // pathname (not a hardcoded /map/) so scrubbing on the homepage keeps
      // the visitor on ``/`` instead of silently rewriting to ``/map/``.
      const search = isToday ? '' : '?d=' + dateKey;
      history.replaceState(null, '', location.pathname + search + location.hash);
    }
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: dateKey, source: 'scrubber' },
    }));
  };

  // ---- Pointer drag ----
  let dragging = false;
  let pointerId = null;
  let liveDate = null;  // tracked during drag, used by the date-preview event

  // SNOW-614: the date the choropleth is currently painted for. A drag
  // fires pointermove far more often than it crosses into a new day — the
  // thumb moves a pixel at a time across a whole season — and each repaint
  // is a setFeatureState per region. Repainting only on a change turns most
  // frames into a thumb move and an event dispatch.
  let paintedDateKey = null;

  const updateDragVisuals = (clientX) => {
    const rect = track.getBoundingClientRect();
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    thumb.style.left = pct + '%';
    liveDate = pctToDateKey(pct);
    // Dispatch raw liveDate so the date pill follows the thumb exactly.
    document.dispatchEvent(new CustomEvent('snowdesk:date-preview', {
      detail: { date: liveDate, source: 'scrubber' },
    }));
    // Repaint the choropleth live during drag, snapped to the nearest data
    // day so off-data days don't flash everything to no_rating mid-drag.
    if (ratingsCache) {
      const snapped = snapToNearestDataDay(liveDate);
      if (snapped !== paintedDateKey) {
        paintedDateKey = snapped;
        repaintRegionsForDate(snapped, ratingsCache);
      }
    }
  };

  // SNOW-614: pointermove fires faster than the display refreshes, and the
  // only thing a move does is decide what the NEXT frame looks like — so
  // coalescing onto requestAnimationFrame drops the redundant work rather
  // than deferring it. Only the newest position is kept; an older one is
  // already wrong by the time the frame runs.
  let pendingDragX = null;
  let dragFrame = 0;

  const scheduleDragVisuals = (clientX) => {
    pendingDragX = clientX;
    if (dragFrame) return;
    dragFrame = requestAnimationFrame(() => {
      dragFrame = 0;
      if (pendingDragX === null) return;
      const x = pendingDragX;
      pendingDragX = null;
      updateDragVisuals(x);
    });
  };

  track.addEventListener('pointerdown', (e) => {
    dragging = true;
    pointerId = e.pointerId;
    track.classList.add('dragging');
    track.classList.remove('animating');
    // Not coalesced: the press must move the thumb in the same tick it
    // happens, or the control feels unresponsive on the very interaction
    // that starts the drag.
    paintedDateKey = null;
    updateDragVisuals(e.clientX);
    e.preventDefault();
  });

  document.addEventListener('pointermove', (e) => {
    if (!dragging || e.pointerId !== pointerId) return;
    scheduleDragVisuals(e.clientX);
  });

  const release = (e) => {
    if (!dragging || (e && e.pointerId !== pointerId)) return;
    dragging = false;
    pointerId = null;
    // SNOW-614: drop any frame still queued — it holds a position from
    // before the release, and commitDate below is about to paint the
    // authoritative one.
    if (dragFrame) {
      cancelAnimationFrame(dragFrame);
      dragFrame = 0;
    }
    pendingDragX = null;
    paintedDateKey = null;
    track.classList.remove('dragging');
    // SNOW-236: use effectiveTodayKey (country-aware last populated date)
    // as the snap target when the user releases without having dragged.
    const snapped = snapToNearestDataDay(liveDate || effectiveTodayKey);
    commitDate(snapped);
    liveDate = null;
  };
  document.addEventListener('pointerup', release);
  document.addEventListener('pointercancel', release);

  // ---- Boot from URL ----
  // Read ?d= once on init. If parseable and inside the season window,
  // commit it (which positions the thumb + queues the repaint once the
  // ratings cache resolves). Otherwise leave the thumb at today's pct.
  const isInSeason = (dateKey) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.isInSeason(dateKey, seasonStartMs, seasonEndMs);
    const ms = Date.parse(dateKey);
    return Number.isFinite(ms) && ms >= seasonStartMs && ms <= seasonEndMs;
  };
  const bootDate = readUrlDateParam();
  if (bootDate && isInSeason(bootDate)) {
    // Defer until both the map style and the ratings cache are ready —
    // commitDate calls repaintRegionsForDate which needs MAP and the
    // regions source up. The thumb position can be set immediately so
    // the boot UI is correct even before paint.
    thumb.style.left = dateKeyToPct(bootDate) + '%';
    Promise.all([MAP_READY_PROMISE, getSeasonRatings().catch(() => null)]).then(() => {
      commitDate(bootDate, { silent: true });
    });
  }

  // ---- Browser back/forward ----
  window.addEventListener('popstate', () => {
    const d = readUrlDateParam();
    // SNOW-236: fall back to effectiveTodayKey (country-aware last populated
    // date) rather than todayKey so back-nav restores a coloured choropleth
    // when today is past the season end.
    const target = d && isInSeason(d) ? d : effectiveTodayKey;
    commitDate(target, { silent: true });
  });

  // ---- SNOW-236: Re-derive effective today on country ratings load ----
  // ensureCountryLoaded dispatches this event after merging a new country's
  // ratings into the shared cache. Re-run the effective-last computation so
  // the scrubber snaps to the correct date for the newly-active country set.
  document.addEventListener('snowdesk:country-ratings-loaded', () => {
    if (!sortedDates || !ratingsCache) return;
    const newEffective = deriveEffectiveTodayKey(sortedDates, ratingsCache);
    const prevEffective = effectiveTodayKey;
    effectiveTodayKey = newEffective;
    // Only snap if the page is in "today mode" — no explicit ?d= in the URL
    // and the effective date has actually changed.
    if (!readUrlDateParam() && newEffective !== prevEffective) {
      commitDate(newEffective, { silent: true });
    }
  });

})();

// SNOW-38: Collapsible danger-scale legend. State persists in localStorage
// under `snowdesk.map.legend` (namespaced — distinct from the legacy flat
// `offline-map-saved` key, which is intentionally left as-is).
(function legendInit() {
  const root = document.getElementById('map-legend');
  if (!root) return;
  const toggle = document.getElementById('map-legend-toggle');
  const STORAGE_KEY = 'snowdesk.map.legend';

  function applyState(state) {
    const next = state === 'expanded' ? 'expanded' : 'collapsed';
    root.dataset.state = next;
    toggle.setAttribute('aria-expanded', next === 'expanded' ? 'true' : 'false');
  }

  const initial = readStorage(STORAGE_KEY) === 'expanded' ? 'expanded' : 'collapsed';
  applyState(initial);

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const next = root.dataset.state === 'expanded' ? 'collapsed' : 'expanded';
    applyState(next);
    writeStorage(STORAGE_KEY, next);
  });

  // Outside-tap dismiss: any click outside the legend container collapses
  // it. Inside-card clicks bubble harmlessly; the toggle stops propagation
  // above so its own click is not treated as "outside".
  document.addEventListener('click', (e) => {
    if (root.dataset.state !== 'expanded') return;
    if (root.contains(e.target)) return;
    applyState('collapsed');
    writeStorage(STORAGE_KEY, 'collapsed');
  });
})();

// Season timelapse — five transport buttons on the scrubber:
//   |<  skip to season start
//   <   play in reverse from current thumb position (second press = stop)
//   >   play forward from current thumb position (second press = stop)
//   >|  skip to season end
//
// Each frame repaints region colours via feature-state and announces a
// snowdesk:date-changed event so the date readout stays in sync. Pressing
// the opposite play button mid-playback flips direction from the current
// frame without resetting the frame index.
(function timelapseInit() {
  const playButton = document.getElementById('scrubber-play');
  if (!playButton) return;

  // BASE_FRAME_MS gives ~5 fps — consistent in both forward and reverse.
  const BASE_FRAME_MS = 200;

  // Drive the scrubber thumb so playback position is visible.
  const scrubber = document.getElementById('season-scrubber');
  const scrubberThumb = scrubber ? scrubber.querySelector('.season-scrubber-thumb') : null;
  const seasonStartMs = scrubber ? Date.parse(scrubber.dataset.seasonStart) : NaN;
  const seasonEndMs = scrubber ? Date.parse(scrubber.dataset.seasonEnd) : NaN;
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  const reverseButton = document.getElementById('scrubber-reverse');
  const skipStartButton = document.getElementById('scrubber-skip-start');
  const skipEndButton = document.getElementById('scrubber-skip-end');

  // Active playback direction: 1 = forward, -1 = reverse.
  let direction = 1;

  const moveScrubber = (dateKey) => {
    if (!scrubberThumb || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) return;
    const dateMs = Date.parse(dateKey);
    if (Number.isNaN(dateMs)) return;
    const pct = Math.max(0, Math.min(100, ((dateMs - seasonStartMs) / seasonSpanMs) * 100));
    scrubberThumb.style.left = pct + '%';
    // Keep aria-valuenow in lock-step with the visual thumb position so
    // ``currentFrameIdx`` resumes from the right spot when the user stops
    // playback mid-season and then presses play again — without this,
    // the next start() falls back to the last user-committed pct rather
    // than the last frame painted by the timelapse.
    if (scrubber) {
      scrubber.setAttribute('aria-valuenow', String(Math.round(pct)));
    }
  };

  // Determine the frame index to start from so playback begins at the
  // current thumb position rather than always rewinding to frame 0.
  const currentFrameIdx = () => {
    const ariaNow = scrubber ? parseFloat(scrubber.getAttribute('aria-valuenow')) : NaN;
    if (window.pwaScrubberCore) {
      return window.pwaScrubberCore.nearestFrameIndex(sortedDates, ariaNow, seasonStartMs, seasonSpanMs);
    }
    if (!sortedDates || sortedDates.length === 0) return 0;
    if (!Number.isFinite(ariaNow) || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) {
      return 0;
    }
    const targetMs = seasonStartMs + (ariaNow / 100) * seasonSpanMs;
    // Find the nearest sortedDates entry to the current thumb position.
    let best = 0;
    let bestDelta = Math.abs(Date.parse(sortedDates[0]) - targetMs);
    for (let i = 1; i < sortedDates.length; i++) {
      const delta = Math.abs(Date.parse(sortedDates[i]) - targetMs);
      if (delta < bestDelta) { best = i; bestDelta = delta; }
    }
    return best;
  };

  let cache = null;        // {date_iso: {region_id: int}}
  let sortedDates = null;  // ascending list of date keys
  let frameIdx = 0;
  let timer = null;

  const announce = (dateKey) => {
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: dateKey, source: 'timelapse' },
    }));
  };

  const applyFrame = (dateKey) => {
    repaintRegionsForDate(dateKey, cache);
    moveScrubber(dateKey);
    announce(dateKey);
  };

  // Hoisted so start() can re-arm setInterval at a new direction without
  // losing the current frame index.
  const tick = () => {
    if (window.pwaScrubberCore) {
      const result = window.pwaScrubberCore.nextFrame(frameIdx, direction, sortedDates.length);
      frameIdx = result.frameIdx;
      if (result.done) {
        // Boundary reached (forward end or reverse start): last valid frame
        // already painted — stop so the value settles.
        stop();
        return;
      }
      applyFrame(sortedDates[frameIdx]);
      return;
    }
    frameIdx += direction;
    if (direction === 1 && frameIdx >= sortedDates.length) {
      // Forward end: last frame already painted — stop so the value settles.
      stop();
      return;
    }
    if (direction === -1 && frameIdx < 0) {
      // Reverse start: first frame already painted — stop.
      stop();
      return;
    }
    applyFrame(sortedDates[frameIdx]);
  };

  const stop = () => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
    // Reset data-state on both play transport buttons.
    playButton.dataset.state = 'stopped';
    playButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play']);
    if (reverseButton) {
      reverseButton.dataset.state = 'stopped';
      reverseButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play-reverse']);
    }
    // Leave the map painted on the current frame — do not clear
    // feature-state or reset the thumb. The user sees what was playing.
    IS_PLAYING = false;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing: false } }));
  };

  // start(directionArg) — begins playback from the current thumb position.
  // If timer is already running (direction flip mid-playback), re-arms the
  // interval at the new direction without resetting frameIdx so position is
  // preserved.
  const start = async (directionArg) => {
    if (!MAP || !MAP.isStyleLoaded()) return;
    if (cache === null) {
      try {
        cache = await getSeasonRatings();
        sortedDates = Object.keys(cache).sort();
      } catch (_err) {
        return;
      }
    }
    if (sortedDates.length === 0) return;

    direction = directionArg;

    // Only update frameIdx when starting fresh (not a direction flip).
    if (timer === null) {
      frameIdx = currentFrameIdx();
    }

    // Clear any existing timer before arming the new one.
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }

    // Update button states to reflect which transport is now active.
    if (directionArg === 1) {
      playButton.dataset.state = 'playing';
      playButton.setAttribute('aria-label', MAP_STRINGS['timelapse-stop']);
      if (reverseButton) {
        reverseButton.dataset.state = 'stopped';
        reverseButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play-reverse']);
      }
    } else {
      if (reverseButton) {
        reverseButton.dataset.state = 'playing';
        reverseButton.setAttribute('aria-label', MAP_STRINGS['timelapse-stop-reverse']);
      }
      playButton.dataset.state = 'stopped';
      playButton.setAttribute('aria-label', MAP_STRINGS['timelapse-play']);
    }

    IS_PLAYING = true;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing: true } }));
    applyFrame(sortedDates[frameIdx]);
    timer = setInterval(tick, BASE_FRAME_MS);
  };

  // When the scrubber commits a new date, the timelapse must surrender
  // control — both paint via feature-state on the same source, so a
  // running timer would fight a user scrub.
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (timer !== null && (!e.detail || e.detail.source !== 'timelapse')) {
      stop();
    }
  });

  // SNOW-58: a basemap swap wipes the regions source mid-frame — stop
  // so setInterval doesn't paint into a half-loaded style.
  document.addEventListener('snowdesk:basemap-changing', () => {
    if (timer !== null) stop();
  });

  // Play-forward button: running forward → stop; else → start(1).
  // Pressing while reverse is playing flips direction from the current frame.
  playButton.addEventListener('click', () => {
    if (timer !== null && direction === 1) {
      stop();
    } else {
      start(1);
    }
  });

  // Play-reverse button: running reverse → stop; else → start(-1).
  // Pressing while forward is playing flips direction from the current frame.
  if (reverseButton) {
    reverseButton.addEventListener('click', () => {
      if (timer !== null && direction === -1) {
        stop();
      } else {
        start(-1);
      }
    });
  }

  // Skip-to-start / skip-to-end: jump the thumb to the first/last data
   // day. Falls back to ``data-season-start``/``data-season-end`` before
  // the ratings cache resolves. moveScrubber owns the thumb position +
  // aria-valuenow update; the synthetic date-changed event (source:
  // 'scrubber') causes the running timelapse to surrender control via
  // its own listener.
  const commitJump = (target) => {
    if (!target) return;
    if (cache) repaintRegionsForDate(target, cache);
    moveScrubber(target);
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: target, source: 'scrubber' },
    }));
  };

  if (skipStartButton) {
    skipStartButton.addEventListener('click', () => {
      commitJump(sortedDates ? sortedDates[0] : (scrubber ? scrubber.dataset.seasonStart : null));
    });
  }

  if (skipEndButton) {
    skipEndButton.addEventListener('click', () => {
      commitJump(sortedDates ? sortedDates[sortedDates.length - 1] : (scrubber ? scrubber.dataset.seasonEnd : null));
    });
  }
})();

// ``mapDatePillInit`` was removed here. It drove a #map-date-pill element
// that has not existed in _map_embed.html since SNOW-314 moved the scrubbed-
// date readout to .map-date-ribbon in the bottom-left row, so the IIFE
// returned at its first line on every load.

// SNOW-58: basemap layer picker — opens a popover of basemap radio
// buttons and swaps the MapLibre style on selection. Persistence and
// initial aria-checked state are handled by the main IIFE before the
// map is constructed so the popover renders correctly on first paint.
//
// Style swapping itself happens via MAP.setStyle(); the regions source
// + layers are re-installed by a style.load handler inside the main
// IIFE. Active timelapse playback (if any) is stopped first via the
// snowdesk:basemap-changing event so its setInterval doesn't paint
// into a half-loaded style.
(function basemapPickerInit() {
  const pill = document.getElementById('basemap-pill');
  if (!pill) return;
  const toggle = document.getElementById('basemap-toggle');
  const menu = document.getElementById('basemap-menu');
  if (!toggle || !menu) return;
  const items = Array.from(menu.querySelectorAll('.basemap-menu-item'));
  if (items.length === 0) return;

  const STORAGE_KEY = BASEMAP_STORAGE_KEY;

  // SNOW-511: the menu is bottom-anchored (CSS `bottom: -96px`) and grows
  // upward. On a short viewport a tall menu grows past the top of #map,
  // sliding its first rows (the Countries section) up behind the nav and
  // the conditional off-season banner where they can't be reached — the CSS
  // `max-height: calc(100dvh - 96px)` floor reserves nothing for that top
  // chrome. Clamp the height to the room actually available between #map's
  // top edge (a small gap below it) and the menu's fixed bottom baseline so
  // the top rows stay on-screen and the overflowing list scrolls internally.
  // The menu's bottom is pinned by CSS regardless of its height, so reading
  // its baseline before applying the cap is stable. Recomputed on each open
  // and on resize because the banner (conditional) and the top safe-area
  // inset both move #map's top.
  const MENU_TOP_GAP = 8;
  const clampMenuHeight = () => {
    const mapEl = document.getElementById('map');
    if (!mapEl) return;
    const mapTop = mapEl.getBoundingClientRect().top;
    const menuBottom = menu.getBoundingClientRect().bottom;
    const available = Math.max(0, Math.round(menuBottom - mapTop - MENU_TOP_GAP));
    menu.style.maxHeight = `${available}px`;
  };

  const setMenuOpen = (open) => {
    pill.dataset.state = open ? 'expanded' : 'collapsed';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    menu.hidden = !open;
    // SNOW-505: recompute the sync-status dots on every open, so no need
    // to keep them live while closed. SNOW-613: "cheap, client-side
    // probes" was written before the per-area bucket split — a pass is now
    // a dozen Cache Storage reads, several of which walk every pinned
    // bucket, so repeated opens coalesce inside `refresh()` rather than
    // each starting their own pass.
    if (open) window.pwaLayerSyncStatus?.refresh();
    // SNOW-511: size the menu to the visible map area once it's laid out.
    if (open) clampMenuHeight();
  };

  // SNOW-588: let the "Manage downloads" sheet close this menu when it
  // opens over it. The menu's open state is three DOM writes held in this
  // closure; mirroring them in map_downloads_manager.js would be a
  // duplicate free to drift from the real one, so expose the setter
  // instead — the same bridge pattern pwaDownloadedOverlay uses for the
  // download controls in sibling IIFEs.
  window.pwaLayersMenu = Object.freeze({
    close() {
      setMenuOpen(false);
    },
  });

  // SNOW-511: keep the cap correct if the viewport changes while the menu is
  // open (orientation flip, mobile URL-bar show/hide, desktop resize).
  window.addEventListener('resize', () => {
    if (!menu.hidden) clampMenuHeight();
  });

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    setMenuOpen(menu.hidden);
  });

  // Outside-click dismiss. Use click (not pointerdown) so an item
  // selection inside the menu fires before this handler can close.
  document.addEventListener('click', (e) => {
    if (menu.hidden) return;
    if (pill.contains(e.target)) return;
    setMenuOpen(false);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden) {
      setMenuOpen(false);
      toggle.focus();
    }
  });

  // SNOW-59 overlay layer ids, mirrored from the main IIFE. Each tier
  // owns a line layer (the outline, where applicable) and a symbol
  // layer (the zoom-banded label) — toggling the overlay flips both in
  // lockstep so a hidden tier never leaves an orphan label floating
  // with no boundary.
  //
  // The picker mutates layer visibility via setLayoutProperty rather
  // than reaching into the main IIFE's overlayState — the layer state
  // on the map IS the source of truth, and the localStorage key is
  // the persistence shadow.
  const OVERLAY_LAYER_IDS = {
    l1: ['major-regions-line', 'major-regions-label'],
    l2: ['sub-regions-line', 'sub-regions-label'],
    // ``bulletin-groupings-line`` rides in the l4 list rather than owning a
    // row of its own: the boundary has no toggle and is shown whenever the
    // micro-region tier is, so the picker flips it in lockstep with L4's own
    // layers. There is no l3 entry here for the same reason.
    l4: [
      'regions-fill', 'regions-line', 'regions-label', 'bulletin-groupings-line',
    ],
    resorts: ['resorts-pin', 'resorts-label'],
    favourites: ['favourites-pin', 'favourites-label'],
    community_reports: [
      'community-reports-clusters',
      'community-reports-cluster-count',
      'community-reports-point',
    ],
    // SNOW-570/SNOW-587: not lazy — both layers are installed with the
    // regions source itself, so this is a plain visibility flip. The
    // refresh that decides WHICH tiles are drawn is driven from the main
    // IIFE's snowdesk:downloaded-overlay-changed handler.
    downloaded: ['cached-tiles-fill', 'cached-tiles-line'],
  };

  for (const item of items) {
    item.addEventListener('click', (e) => {
      e.stopPropagation();

      // Offline-integrity: a row map_layer_sync_status.js has disabled
      // (offline AND its resource/basemap isn't cached) is inert. Honour
      // aria-disabled here — the source of truth is that module's probe, so
      // there's no state to toggle and no basemap to swap to.
      if (item.getAttribute('aria-disabled') === 'true') return;

      // SNOW-59 / SNOW-172: overlay checkbox — toggle visibility or country filter.
      const overlayKey = item.dataset.overlayKey;
      if (overlayKey) {
        const next = item.getAttribute('aria-checked') !== 'true';
        item.setAttribute('aria-checked', next ? 'true' : 'false');

        // SNOW-314 prototype: notify the season-header readout so its breadcrumb
        // mirrors which region tiers are visible (l1=Major, l2=Minor, l4=Micro).
        if (overlayKey === 'l1' || overlayKey === 'l2' || overlayKey === 'l4') {
          document.dispatchEvent(new CustomEvent('snowdesk:overlays-changed', {
            detail: { key: overlayKey, visible: next },
          }));
        }

        // SNOW-172: handle country.* toggles by delegating to the main IIFE
        // via a CustomEvent. countryState / ensureCountryLoaded / applyCountryFilters
        // are all scoped to the main IIFE and are not accessible here.
        if (overlayKey.startsWith('country.')) {
          const code = overlayKey.slice(8); // 'country.fr' → 'fr'
          document.dispatchEvent(new CustomEvent('snowdesk:country-toggle', {
            detail: { code, next },
          }));
          return;
        }

        // SNOW-414: notify telemetry when the favourites overlay is flipped.
        if (overlayKey === 'favourites') {
          window.pwaTelemetry?.emit('map.favourite.overlay_toggled', { visible: next });
        }
        // SNOW-419: notify telemetry when the community-reports overlay is
        // flipped.
        if (overlayKey === 'community_reports') {
          window.pwaTelemetry?.emit('map.community_reports.overlay_toggled', { visible: next });
        }
        // SNOW-570/SNOW-587: the cached-tiles layers are already installed,
        // so the direct visibility path below handles showing them — but
        // WHICH tiles they draw is a cache probe only the main IIFE can run.
        // Tell it, so the overlay is freshly derived at the moment it is
        // revealed rather than showing whatever the last probe left.
        if (overlayKey === 'downloaded') {
          document.dispatchEvent(new CustomEvent('snowdesk:downloaded-overlay-changed', {
            detail: { visible: next },
          }));
        }

        // Tier overlay — toggle layer visibility.
        writeStorage(OVERLAY_STORAGE_KEY[overlayKey], String(next));
        if (MAP) {
          if (next && (overlayKey === 'l1' || overlayKey === 'l2' || overlayKey === 'resorts' || overlayKey === 'favourites' || overlayKey === 'community_reports')) {
            // SNOW-235: First enable of a lazy overlay tier — delegate to the
            // main IIFE via snowdesk:overlay-load so it can fetch the GeoJSON,
            // install the layers, and then make them visible. The main IIFE
            // listener handles both the fetch and the setLayoutProperty call,
            // so we return here without running the direct visibility loop.
            document.dispatchEvent(new CustomEvent('snowdesk:overlay-load', {
              detail: { key: overlayKey },
            }));
          } else {
            // Toggling off, or toggling a non-lazy tier (l4): use the direct
            // setLayoutProperty path. For the lazy tiers toggling off, the
            // layer may not exist yet (if the user enabled then immediately
            // disabled before the fetch resolved) — getLayer guards cover this.
            //
            // L4 recovery: unlike the lazy tiers, Micro regions has no
            // fetch-and-install path here — if a prior style swap dropped its
            // layers, a plain setLayoutProperty would silently no-op (the
            // reported "toggling micro-regions does nothing" bug). Rebuild
            // from the in-memory cache first (synchronous), then fall through
            // to make the freshly-added layers visible.
            if (next && overlayKey === 'l4' && !MAP.getLayer('regions-fill')) {
              document.dispatchEvent(new CustomEvent('snowdesk:regions-reinstall'));
            }
            // Enabling L4 also brings its companion bulletin boundary back.
            // That layer IS lazy — its per-date data may never have been
            // fetched (first enable) or may belong to a day the user scrubbed
            // past while it was hidden — so it needs the load path, not just
            // the visibility flip below. The main IIFE re-reads L4's key to
            // decide the final visibility, so this is safe if the user
            // toggles off again before the fetch settles.
            if (next && overlayKey === 'l4') {
              document.dispatchEvent(new CustomEvent('snowdesk:overlay-load', {
                detail: { key: 'l3' },
              }));
            }
            for (const layerId of OVERLAY_LAYER_IDS[overlayKey]) {
              if (MAP.getLayer(layerId)) {
                MAP.setLayoutProperty(
                  layerId, 'visibility', next ? 'visible' : 'none',
                );
              }
            }
          }
          // SNOW-499: bridge to the main IIFE so the resort layer's
          // favourited-resort exclusion is recomputed against the new
          // favourites visibility — otherwise a favourited resort stays
          // hidden (no dot, no star) when the overlay is switched off.
          // Dispatched for both directions; the toggle-on lazy path is
          // also covered by the overlay-load handler once its fetch settles.
          if (overlayKey === 'favourites') {
            document.dispatchEvent(
              new CustomEvent('snowdesk:favourites-visibility-changed'),
            );
          }
        }
        return;
      }

      const url = item.dataset.basemapUrl;
      const key = item.dataset.basemapKey;
      if (!url || !key || !MAP) return;
      // No-op if this option is already active — just close the popover.
      if (item.getAttribute('aria-checked') === 'true') {
        setMenuOpen(false);
        return;
      }
      // Notify other consumers (timelapse) to surrender control before
      // we tear down the current style.
      document.dispatchEvent(new CustomEvent('snowdesk:basemap-changing', {
        detail: { key, url },
      }));
      writeStorage(STORAGE_KEY, key);
      // Only update aria-checked on basemap radios — overlay checkboxes
      // are independent and shouldn't be cleared when the basemap swaps,
      // and SNOW-588's "Manage downloads…" action row has no checked state
      // at all. Tested for positively rather than by excluding the overlay
      // rows, so a fourth kind of row added later is left alone by default
      // instead of silently acquiring an aria-checked it should not have.
      for (const other of items) {
        if (!other.dataset.basemapKey) continue;
        other.setAttribute(
          'aria-checked',
          other === item ? 'true' : 'false',
        );
      }
      setMenuOpen(false);
      resolveBasemapStyle(key, url).then((style) => MAP.setStyle(style));
    });
  }
})();

// SNOW-521 final shape: a single "Download basemap" control for the
// focused MICRO region — replaces the viewport-anchored cacheNowInit
// (SNOW-492/493). An earlier iteration of this rework also had
// major/minor crumb icons; they were dropped because their own
// shallower detail floors made the sizes non-monotonic with containment
// (an L1 region could read smaller than an L2 it contains), which read
// as a bug.
//
// The control has moved twice since. SNOW-521 moved it out of the
// #region-readout chip into the bottom-right stack, beside the layers
// pill — the layers menu is the cache-state dashboard and this is the
// other control that writes to that cache. SNOW-522 moved it BACK into
// the #region-readout chip (public/partials/_season_ribbon.html), and
// handed the bottom-right slot to a second, distinct control — a
// user-framed custom-area download (mapCustomDownloadControlInit,
// below) — that this one does not need to know about; this IIFE finds
// #map-download-control by id wherever it currently renders and is
// otherwise unchanged by either move.
//
// Data source — no client-side tile enumeration. Region tile coverage
// is precomputed server-side (regions/services/basemap_tiles.py) and
// already sits on FEATURE_BY_REGION_ID[regionId].properties.download
// once regions.geojson has loaded (the same payload the choropleth
// itself needs), so showing the size is a pure in-memory lookup — no
// extra fetch until the user actually clicks Download.
//
// Show/size — the control is rendered whenever a region is focused
// (snowdesk:region-selected moves it from 'no-region' to an actionable
// state), independent of which overlay tiers (L1/L2) are toggled on.
// Hidden until then via the same CSS sibling rule as its neighbour
// #region-readout-action (#region-readout.has-region ~ …,
// static/css/map.css) — back in the ribbon header a permanently-present
// roundel with nothing focused would be the odd one out next to that
// already-hidden-until-focused neighbour. The 'no-region' state stays in
// the machine below for the rarer case of a focused region with no
// computed download summary (properties.download is null).
//
// State (no-region/idle/busy/done/disabled/offline, data-download-state)
// — idle/done are
// derived from a real pinned-cache probe (every per-area bucket, unioned
// — SNOW-586) every time the icon is (re)shown, never a stored flag (the
// "layers menu is a live cache-state dashboard" invariant — see
// docs/offline-map.md); disabled is the
// server-flagged over_ceiling backstop. That probe needs the active
// basemap's tile template, which isn't resolvable while the style is
// still settling (the boot case) — so a probe that can't tell yet is
// re-run on the next MapLibre 'idle' rather than left reading idle
// (see _retryWhenStyleSettles).
//
// Click (idle only) — fetches the region's full blob (incl. z tile
// ranges) from /api/region-basemap-tiles/, assembles the URL list
// (rangesToTileURLs + same-origin data feeds + active basemap style +
// sprites — mirrors SNOW-492/493's assembly, minus tile enumeration) and
// hands it to the SW's warm-cache handler with `pinned: true`, updating
// the roundel's live fill from onProgress. On completion the icon
// itself carries the outcome (green offline circle on a clean success,
// idle otherwise) — no toast.
(function mapDownloadControlInit() {
  const btn = document.getElementById('map-download-control');
  if (!btn) return;

  const ribbonEl = document.getElementById('season-ribbon');

  // SNOW-570: the regions the user has downloaded, in meta:app under
  // 'basemap.regions' as [{region_id, band, z, savedAt}].
  //
  // SNOW-583 repurposes this record. It used to carry the region's `bbox`
  // (derived from its boundary geometry) so both the "Downloaded areas"
  // overlay and this control's own done-probe could recompute the
  // download's tile set client-side, cheaply, with no per-region fetch.
  // That recomputation assumed a region's download WAS its bbox rectangle
  // — true before this ticket, false after: a region's tiles are now
  // CLIPPED to its real boundary server-side
  // (apps.regions.services.basemap_tiles.build_region_blob), and the clip
  // depends on the boundary polygon in a way `bbox` alone can't
  // reconstruct. So the record now carries the run's actual `z` (the
  // blob's own tile ranges, whichever shape — rectangle or clipped row
  // spans) instead of `bbox`, and `_probeDone` below reads it back
  // directly rather than recomputing anything. SNOW-587 removed the
  // "Downloaded areas" overlay's per-region ring, the other consumer of
  // the old bbox shape — this record's only reader now is this control's
  // own done-probe.
  //
  // This is the record half only — it says the user asked for this region,
  // never that the tiles are still there. The probe still checks real
  // cache contents before claiming `done`, so an evicted download reads
  // `idle` again. That is exactly the split the custom area has always
  // had, where 'basemap.customAreas' records WHERE each frame was and the
  // done state is probed; the region download simply never had the first
  // half.
  const DOWNLOADED_REGIONS_KEY = 'basemap.regions';

  /**
   * Record `regionId` as downloaded, replacing any earlier entry for it.
   *
   * Best-effort throughout: this runs inside a download's finish handler,
   * where a failed IndexedDB write must never surface as an error. The
   * cost of losing it is a missing record, not a wrong one — a fallback
   * fetch in `_probeDone` below still answers correctly, just over the
   * network instead of from IndexedDB.
   *
   * SNOW-632: `bytes` is this run's OWN reported total — from
   * `_warmCache`'s ``warm-cache-done`` reply — recorded outright, never
   * accumulated onto the previous record and never re-measured from the
   * bucket. A same-bbox, same-basemap RETRY re-fetches identical URLs into
   * the same bucket (`cache.put` OVERWRITES each key rather than adding to
   * it, so the bucket doesn't grow), so replacing the record with this
   * run's own figure already lands on the right answer without reading
   * the bucket at all. Re-measuring the bucket after every run (an earlier
   * version of this fix) looked more exact, but a live tile response
   * carries no `Content-Length` under the gzip encoding every browser
   * requests, so it measured 0 in production and silently fell back to
   * this same figure anyway — see
   * docs/decisions/per-area-pinned-basemap-caches.md for the curl
   * evidence.
   *
   * A basemap SWITCH at the same region used to need accumulation,
   * because the bucket is keyed on region id alone and a switch adds
   * genuinely new tiles (different URLs, different origin) to it — but
   * that arithmetic couldn't tell a switch apart from a retry, doubling
   * the recorded total on every repeat. `mapDownloadControlInit`'s
   * `handleClick` now sidesteps the ambiguity instead of resolving it
   * numerically: its `beforeWarm` deletes the bucket outright whenever the
   * `template` this run is about to fetch differs from the one recorded
   * for it, so by the time this function runs the bucket always holds
   * exactly one basemap's tiles and this run's own total is the bucket's
   * whole total.
   *
   * @param {string} regionId
   * @param {string} template The tile URL template this run fetched
   *   (`basemap_download_runner.js`'s `run` resolves it once and hands it
   *   to both `beforeWarm` and `finish`'s `extras`) — stored so a LATER
   *   run can tell whether the bucket still matches the active basemap.
   * @param {Object | null} z The downloaded blob's own tile ranges
   *   (`blob.z` — rectangle or clipped row spans), or null when the run's
   *   blob carried none — in which case nothing is recorded, since an
   *   entry with no `z` could never be verified against the cache.
   * @param {number[]} band The zoom band the run actually fetched.
   * @param {number} bytes This run's own on-disk size, from `_warmCache`'s
   *   ``warm-cache-done`` reply.
   * @returns {Promise<void>}
   */
  async function _recordRegionDownload(regionId, template, z, band, bytes) {
    if (!z || !window.pwaDb) return;
    try {
      const row = await window.pwaDb.get('meta:app', DOWNLOADED_REGIONS_KEY);
      const existing = Array.isArray(row && row.value) ? row.value : [];
      const next = existing.filter((entry) => entry && entry.region_id !== regionId);
      const feature = FEATURE_BY_REGION_ID[regionId];
      const name = (feature && feature.properties && feature.properties.name) || regionId;
      next.push({
        region_id: regionId,
        band: band,
        z: z,
        name: name,
        template: template,
        bytes: Number(bytes) || 0,
        savedAt: new Date().toISOString(),
      });
      await window.pwaDb.put('meta:app', { key: DOWNLOADED_REGIONS_KEY, value: next });
    } catch (err) {
      // Still non-fatal — see the docstring — but no longer silent
      // (SNOW-612). This is the write whose absence leaves a completed
      // download with a pinned bucket and no record, and the bucket then
      // reads as an orphan: the reconciliation above makes that visible
      // and deletable, and this says why it happened.
      console.warn('basemap download record write failed', err);
      window.pwaTelemetry?.emit('map.basemap.record_write_failed', {
        region_id: regionId,
      });
    }
  }

  /**
   * The stored ``basemap.regions`` record for `regionId`, or null.
   *
   * A PRE-SNOW-583 record carries `bbox` and no `z` — the shape this
   * region's own download last wrote before this ticket shipped. Treated
   * as "no record" rather than read literally: its `bbox` is the old
   * bounding-box rectangle, not the clipped tile set the server would
   * compute today, so trusting it would claim tiles the run never
   * fetched. `_probeDone` falls back to a fresh server fetch in that case
   * — the same path a region with no record at all takes.
   *
   * Best-effort: a failed read is "no record", never an error.
   *
   * @param {string} regionId
   * @returns {Promise<{region_id: string, band: number[], z: Object,
   *   template?: string, savedAt: string} | null>} `template` is absent on
   *   a record written before SNOW-632 — callers deciding whether to evict
   *   the region's bucket on a template mismatch treat that absence as
   *   "unknown, so different" (see `handleClick`'s `beforeWarm`).
   */
  async function _storedRegionRecord(regionId) {
    try {
      const row = await window.pwaDb?.get('meta:app', DOWNLOADED_REGIONS_KEY);
      const value = Array.isArray(row && row.value) ? row.value : [];
      const record = value.find((entry) => entry && entry.region_id === regionId);
      return record && record.z ? record : null;
    } catch (_e) {
      return null;
    }
  }

  // Memoised full-blob fetches for `_probeDone`'s fallback path (no local
  // record, or a pre-SNOW-583 one) — keyed by region id, successes and
  // in-flight promises only. A region's blob never changes once computed
  // (region geometry is static reference data), so caching a settled
  // fetch for the rest of the session is exact, not just an optimisation.
  // Failures are deliberately NOT cached: a network hiccup or a
  // still-offline session must not poison every later probe for the
  // region — the next call retries the fetch instead of repeating the
  // same failure from memory.
  const _regionBlobCache = new Map();

  /**
   * The region's full ``basemap_download`` blob, fetched once and
   * memoised for the rest of the session.
   *
   * @param {string} regionId
   * @returns {Promise<Object>} Rejects on a network/HTTP failure — the
   *   caller decides what "can't tell" means for its own state machine.
   */
  function _fetchRegionBlob(regionId) {
    const cached = _regionBlobCache.get(regionId);
    if (cached) return cached;
    // Cache the in-flight promise immediately, so two probes racing the
    // same never-yet-fetched region share one request rather than firing
    // two. The .catch removes it again on failure — so the next call
    // starts a fresh fetch instead of replaying the rejection — while
    // still re-throwing, so THIS call's caller sees the failure.
    const promise = fetch('/api/region-basemap-tiles/?id=' + encodeURIComponent(regionId))
      .then((response) => {
        if (!response.ok) throw new Error(`region-basemap-tiles ${response.status}`);
        return response.json();
      })
      .catch((err) => {
        _regionBlobCache.delete(regionId);
        throw err;
      });
    _regionBlobCache.set(regionId, promise);
    return promise;
  }

  // { regionId, summary } for the currently-focused region, or null
  // when it has no computed data. `summary` is the small {count, mb,
  // over_ceiling, centre_tile} shape carried on regions.geojson's
  // properties.download.
  let regionData = null;

  let currentRegionId = (ribbonEl && ribbonEl.dataset.defaultRegionId) || null;

  /**
   * True when EVERY tile of `data`'s region is present in the pinned
   * cache — "is this region actually available offline?".
   *
   * SNOW-583: a region's tile set is now CLIPPED to its real boundary
   * server-side (`build_region_blob`), which a client only has an
   * approximation of (`FEATURE_BY_REGION_ID`'s geometry, unbuffered) — so
   * this can no longer recompute the tile set from the region's own
   * boundary the way it (and the "Downloaded areas" overlay) briefly did
   * under SNOW-570. It instead checks the ACTUAL tile set: the run's own
   * stored `basemap.regions` record when there is one (works fully
   * offline — no network involved), falling back to a fresh fetch of the
   * region's blob when there is none (a region downloaded in an earlier
   * session before this record shape existed, or one this control has
   * simply never focused before). Either way `blobFullyCached` checks the
   * real blob's tiles against the real cache, never a recomputed
   * approximation of them.
   *
   * This used to be a centre-tile probe: one `cache.match` on the tile at
   * the region's centre, taken as a witness that the region's own
   * download had completed. That reasoning only holds for a region the
   * user downloaded AS A REGION, and the roundel does not get to assume
   * that — it probes whatever region is selected. Both download shapes
   * write to one pinned cache over the same band with the same URL
   * template, so a neighbouring region's download, or a custom area that
   * merely overlapped, caches this region's centre tile without covering
   * it. The roundel then painted `done` for a region holding a handful of
   * tiles, and because `handleClick` only acts on `idle`/`error`, the
   * region could no longer be downloaded at all. Full coverage is the
   * honest question, and — since the tile set now comes from a real blob
   * rather than a recomputed rectangle — it is exact rather than a bbox
   * approximation.
   *
   * Returns `null` for "can't tell yet": the active basemap's tile
   * template isn't resolvable (see `_retryWhenStyleSettles`), or there is
   * no stored record AND the fallback fetch failed (typically: offline,
   * with nothing recorded for this region yet — `renderControl` reads
   * `navigator.onLine` to choose `idle` vs `offline` in that case). Both
   * are deliberately distinct from `false` ("looked, not there").
   *
   * @param {{regionId: string, summary: Object}} data
   * @returns {Promise<boolean | null>}
   */
  async function _probeDone(data) {
    const core = self.pwaBasemapDownloadCore;
    const template = activeBasemapTileTemplate(MAP);
    if (!core || !template) return null;
    // SNOW-586: the cached set is unioned across every per-area pinned
    // bucket, not read from one shared cache. That is the ONLY thing this
    // ticket changes here — SNOW-583's stored-`z` strategy below is kept
    // whole, because it asks about the tile ranges the run actually
    // fetched. Deriving them from the region's bbox instead (as this
    // probe did before SNOW-583 clipped downloads to the region plus a
    // tile of margin) would demand tiles a clipped download never wrote,
    // so every region would read as permanently un-downloaded.
    const cached = await pinnedBasemapCacheURLs();

    const stored = await _storedRegionRecord(data.regionId);
    if (stored) return core.blobFullyCached(template, { z: stored.z }, cached);

    try {
      const blob = await _fetchRegionBlob(data.regionId);
      return core.blobFullyCached(template, blob, cached);
    } catch (_e) {
      return null;
    }
  }

  /**
   * Paint `state` onto the download icon: data-download-state, the busy
   * fill percentage, and an aria-label/title carrying the region's size.
   *
   * @param {string} state - 'no-region' | 'idle' | 'busy' | 'done' |
   *   'error' | 'disabled' | 'offline'.
   * @param {number} mb
   * @param {number} [pct] - Only meaningful for state 'busy'.
   * @returns {void}
   */
  function setState(state, mb, pct) {
    btn.dataset.downloadState = state;
    // Non-runnable states are announced as disabled rather than removed, so
    // the control keeps its place in the stack (see renderControl). 'idle'
    // and (SNOW-568) 'error' are the actionable states — handleClick
    // returns immediately for every other one, including 'busy' (a run is
    // already going) and 'done' (an informational success state), so those
    // are announced as disabled too.
    btn.setAttribute(
      'aria-disabled',
      state === 'idle' || state === 'error' ? 'false' : 'true',
    );
    // Busy progress renders as a bottom-up fill of the roundel (map.css),
    // driven by --download-progress rather than a numeric readout.
    if (state === 'busy') {
      btn.style.setProperty('--download-progress', `${pct || 0}%`);
    } else {
      btn.style.removeProperty('--download-progress');
    }
    const text = {
      // The control is permanently in the bottom-right stack rather than
      // appearing beside the region name, so it can be read with nothing
      // selected — and it no longer sits next to the name that told the user
      // which region it meant. Every label therefore has to say so itself.
      //
      // 'no-region' covers two distinct causes that the old hidden-icon
      // behaviour let us conflate: nothing is focused, or the focused region
      // has no precomputed download summary (properties.download is null —
      // compute_basemap_download hasn't run for it). Now that the control is
      // always on screen a single label would be wrong in one of the two
      // cases, so the copy branches on whether a region is actually focused.
      'no-region': currentRegionId
        ? `Basemap download isn't available for this region`
        : `Select a region to download its basemap`,
      idle: `Download this region's basemap — up to ${mb} MB`,
      busy: `Downloading this region's basemap — ${pct || 0}%`,
      done: `This region's basemap is downloaded — available offline`,
      // SNOW-568: the toast carries the reason; the roundel just has to
      // say the run failed and is retryable.
      error: `This region's basemap download failed — tap to try again`,
      disabled: `This region's basemap is too large to download`,
      // Offline-integrity: no downloading of layers while offline.
      offline: `Basemap download unavailable while offline`,
    }[state];
    btn.setAttribute('aria-label', text);
    btn.title = text;
  }

  // SNOW-611: the shared retry-when-the-style-settles callback — see
  // `makeStyleSettleRetry`. SNOW-522's custom-area control carried a
  // byte-identical copy of this; SNOW-634 deleted its own use of it —
  // that control's "done" no longer depends on the active basemap's tile
  // template at all (see mapCustomDownloadControlInit's `_renderControl`).
  const _retryWhenStyleSettles = makeStyleSettleRetry(() => renderControl());

  /**
   * (Re)probe the control against the current regionData. A stale async
   * resolution (regionData changed, or a run started, while the probe was
   * in flight) is discarded rather than clobbering a newer state.
   *
   * With no region focused this used to set btn.hidden, which made the
   * bottom-right stack grow and shrink under the user as regions were
   * selected and deselected — and, once the control moved into that stack,
   * would have read as the feature disappearing rather than being
   * unavailable. It now paints the inert 'no-region' state instead and the
   * control keeps its slot.
   *
   * @returns {Promise<void>}
   */
  async function _renderControl() {
    const data = regionData;
    if (!data) {
      setState('no-region');
      return;
    }
    if (btn.dataset.downloadState === 'busy') return;
    if (data.summary.over_ceiling) {
      setState('disabled', data.summary.mb);
      return;
    }
    // SNOW-583: `_probeDone` can now be a network round trip (its fallback
    // fetch, when there's no stored record for this region), where it used
    // to be a single synchronous cache read wrapped in one await. Without
    // this, a slow probe would leave the PREVIOUSLY-focused region's
    // `done` painted on screen for the whole of that round trip — this
    // region hasn't been checked yet, so it must not borrow the last
    // region's answer.
    setState(navigator.onLine ? 'idle' : 'offline', data.summary.mb);
    const done = await _probeDone(data);
    if (regionData !== data || btn.dataset.downloadState === 'busy') return;
    // "Can't tell yet" (null): paint the actionable idle state so the icon
    // still carries this region's size, but come back once the style has
    // settled — the region may well already be downloaded.
    if (done === null) {
      setState(navigator.onLine ? 'idle' : 'offline', data.summary.mb);
      _retryWhenStyleSettles();
      return;
    }
    // Offline-integrity: a region already downloaded (done) still reads as
    // the green offline circle; one that isn't can't be fetched now, so it
    // shows the offline-disabled state instead of an actionable idle.
    if (!navigator.onLine && !done) {
      setState('offline', data.summary.mb);
      return;
    }
    setState(done ? 'done' : 'idle', data.summary.mb);
  }

  // SNOW-613: overlapping renders coalesce onto one trailing pass — see
  // `coalesceRenders`. Every trigger below calls this, not `_renderControl`.
  const renderControl = coalesceRenders(_renderControl);

  /**
   * Adopt `regionId` as the focused region: pull its download summary
   * straight off the already-loaded
   * FEATURE_BY_REGION_ID[regionId].properties.download (no fetch) and
   * re-render the icon.
   *
   * @param {string | null} regionId
   * @returns {void}
   */
  function applyRegion(regionId) {
    currentRegionId = regionId;
    const feature = regionId ? FEATURE_BY_REGION_ID[regionId] : null;
    const summary = (feature && feature.properties && feature.properties.download) || null;
    regionData = summary ? { regionId: regionId, summary: summary } : null;
    renderControl();
  }

  /**
   * Run the download for the focused region.
   *
   * SNOW-611: the ordered pre-flight, the eviction and the warm-cache
   * dispatch all live in the shared `runPinnedDownload` — this supplies
   * only what is this control's own: how the roundel paints, where the
   * blob comes from, and what a successful run records.
   *
   * @returns {Promise<void>}
   */
  async function handleClick() {
    const data = regionData;
    // SNOW-568: 'error' is retryable, so it starts a run like 'idle'.
    const state = btn.dataset.downloadState;
    if (!data || (state !== 'idle' && state !== 'error')) return;
    // Offline-integrity: never start a download offline, even if a race left
    // the icon on 'idle' at the moment of the click.
    if (!navigator.onLine) {
      setState('offline', data.summary.mb);
      return;
    }

    const core = self.pwaBasemapDownloadCore;
    // `runPinnedDownload` re-reads the core itself and fails the run
    // properly if it is missing; this only needs it for the area id, so a
    // missing core simply yields none and the runner handles the rest.
    const areaId = core ? core.areaIdForRegion(data.regionId) : '';

    await runPinnedDownload({
      areaId: areaId,
      mb: data.summary.mb,
      // This control's roundel carries the region's size in every state,
      // so the shared runner's (state, pct) pair is widened here.
      paint: (nextState, pct) => setState(nextState, data.summary.mb, pct),
      loadBlob: async () => {
        const response = await fetch(
          '/api/region-basemap-tiles/?id=' + encodeURIComponent(data.regionId),
        );
        if (!response.ok) throw new Error(`region-basemap-tiles ${response.status}`);
        return response.json();
      },
      // SNOW-632: a region's pinned bucket is keyed on the region id ALONE
      // (see `_recordRegionDownload`'s docstring), so downloading the same
      // region under a DIFFERENT basemap would otherwise leave the old
      // basemap's tiles sitting in the bucket alongside the new run's —
      // the bucket's real size stops matching what gets recorded, and
      // `planBasemapDownloadBudget` under-charges the area for it. No
      // confirmation needed: this replaces the user's own prior download
      // of the SAME region, not another area — same reasoning as the
      // custom-area control's own `beforeWarm`, which this mirrors.
      // `template` is what the runner is about to build THIS run's URLs
      // from, so the comparison and the fetch can never disagree about
      // which basemap is active.
      //
      // Accepted trade-off: evicting BEFORE the warm means a run that
      // then fails leaves the user with neither the old download nor a
      // new one. Bucket and record go together, so the state stays
      // consistent (empty bucket, no record, roundel on 'error') rather
      // than stale — and the evicted tiles were the previous basemap's,
      // which the done-probe already ignored under the new one, so there
      // was nothing usable to lose.
      beforeWarm: async (_blob, evictAreaId, template) => {
        const previous = await _storedRegionRecord(data.regionId);
        // No usable record (never downloaded, or a pre-SNOW-632 record
        // with no `template`) is treated the same as a mismatch — the
        // safe direction, since it costs one redundant eviction rather
        // than risking a stale bucket read as bigger than it is.
        if (!previous || previous.template !== template) {
          await evictBasemapAreas([evictAreaId]);
        }
      },
      finish: async (result, blob, { core: runCore, progressFill, template }) => {
        // "done" (the green offline circle) requires at least one success
        // and no failures; a partial, vacuous, or absent result must not
        // claim the region is downloaded.
        //
        // SNOW-568: a run that didn't succeed paints 'error' and raises the
        // shared toast, rather than reverting to 'idle' — which was
        // indistinguishable from never having clicked.
        //
        // SNOW-632: `result.cancelled` is checked here too, even though
        // this control has no Cancel affordance of its own to trigger it
        // — the shared runner's contract now permits a cancelled result
        // from ANY caller (basemap_download_runner.js's `finish`
        // docstring), and a cancelled run's `failed` is always 0, which
        // would otherwise misread as a clean success.
        const ok = !!(result && !result.cancelled && result.ok > 0 && result.failed === 0);
        // SNOW-570: record what was downloaded before anything is painted.
        // SNOW-583: records the blob's own `z` (the clipped tile set the run
        // actually fetched) rather than a bbox — `_probeDone` reads this
        // record back directly, with no recomputation, so it is exactly
        // right for whatever shape the blob was.
        if (ok) {
          await _recordRegionDownload(
            data.regionId,
            template,
            blob.z,
            blob.band || runCore.MICRO_BAND,
            result.bytes,
          );
        }
        // SNOW-569: await the on-map pulse before flipping the roundel — the
        // two are one gesture, the region finishes filling, pulses, and only
        // then does the icon go green. The control stays 'busy' throughout,
        // which also keeps renderControl from repainting underneath the
        // pulse. A failed run clears the fill without pulsing, so the error
        // state and its toast arrive with no delay.
        await progressFill.finish(ok);
        if (ok) {
          setState('done', data.summary.mb);
        } else {
          setState('error', data.summary.mb);
          revealBasemapDownloadError(result ? result.reason : null);
        }
        // SNOW-505: the warm-cache run has just warmed the shell + pinned
        // basemap caches (the SW's warm-cache handler awaits its
        // cache.put calls before replying, so this re-probe races
        // nothing). Re-probe every sync dot against real cache state so
        // the layers popover reflects the newly-warmed feeds/tiles.
        window.pwaLayerSyncStatus?.refresh();
        // SNOW-570/SNOW-587: and the cached-tiles overlay, so tiles that
        // just finished downloading appear immediately rather than at the
        // next basemap swap or reload.
        window.pwaDownloadedOverlay?.refresh();
      },
    });
  }

  btn.addEventListener('click', () => handleClick());

  document.addEventListener('snowdesk:region-selected', (e) => {
    applyRegion((e.detail && e.detail.region_id) || null);
  });

  // Per-basemap download state: the "done" probe (_probeDone) keys off the
  // ACTIVE basemap's tile template, so switching basemap changes whether
  // this region reads as downloaded. Re-render on every basemap swap (the
  // main IIFE fires this once the new style's overlays are back) so the icon
  // flips done↔idle to match the basemap you're now on — e.g. download on
  // Standard, switch to Swisstopo, and the icon reverts to "download".
  document.addEventListener('snowdesk:basemap-changed', () => renderControl());

  // Offline-integrity: re-render on every connectivity transition so the
  // icon greys out (offline, not yet downloaded) or becomes actionable
  // again (back online) without needing the region re-selected.
  document.addEventListener('snowdesk:connectivity-changed', () => renderControl());

  // Pick up the homepage's server-rendered default focus once its
  // geojson feature (and download data) has loaded. The initial CH
  // load populates FEATURE_BY_REGION_ID directly in the main IIFE's
  // map.on('load') handler WITHOUT dispatching snowdesk:regions-loaded
  // (that event is SNOW-172-lazy-country-load-only) — MAP_READY_PROMISE
  // resolves right after that handler's install step, by which point
  // FEATURE_BY_REGION_ID is populated, so it's the reliable signal for
  // the initial default-region case. snowdesk:regions-loaded is also
  // listened for as a safety net (a deep-linked/default region in a
  // country loaded lazily later), mirroring seasonRibbonInit's own
  // setHighlight listener for that event.
  MAP_READY_PROMISE.then(() => {
    if (currentRegionId) applyRegion(currentRegionId);
  });
  document.addEventListener('snowdesk:regions-loaded', () => {
    if (currentRegionId) applyRegion(currentRegionId);
  });

  if (currentRegionId) applyRegion(currentRegionId);
})();

// SNOW-522: "Download a custom area" — takes the bottom-right utility-stack
// slot the per-region control (above) vacated when it moved back into the
// ribbon header. Unlike that control there is no fixed region to size
// ahead of time: clicking the roundel opens a framing overlay
// (#map-frame-overlay, _map_embed.html) — a Google-Maps-style dim mask
// with a centred frame. The user pans/zooms the map underneath it; the
// live "up to N MB" readout is recomputed once per animation frame for as
// long as the SELECTION is changing, entirely client-side, via
// pwaBasemapDownloadCore.buildBlob (static/js/basemap_download_core.js —
// see its header for why this tile math is a deliberate re-port of the
// server-side module, not drift).
//
// Frame geometry — the frame's four corners (read from its own
// getBoundingClientRect(), never a hardcoded size: the dimensions live in
// CSS) are unprojected ALL FOUR, not just two opposite ones, and the bbox
// is the min/max over the lot: MapLibre supports rotation, so a rotated
// view makes the frame a non-axis-aligned quad on the map, and two
// corners alone would under-cover it. _screenBoxForBBox does the same in
// reverse, for the same reason.
//
// Two regimes, and knowing which one you are in explains everything about
// how the frame behaves (SNOW-567 — see _updateSelection):
//
//   Under the ceiling, the frame is a viewport-anchored reticle. It fills
//   its gutter-inset area, and the selection is whatever ground happens to
//   be beneath it — so both pan and zoom change the selection.
//
//   Once the ceiling caps the area, the selection LOCKS to the ground and
//   the frame becomes a projection of it. Zooming then recomputes nothing
//   at all: the same bbox covers the same tiles, so the frame just tracks
//   its own terrain (and scales with the map, as any map feature does)
//   while the estimate holds still. Panning is what re-aims a locked
//   selection, moving it back under the frame at the same size.
//
// Persistence — SNOW-635: any number of custom areas can exist at once.
// A confirmed download is appended to IndexedDB's meta:app store under
// 'basemap.customAreas' (an ARRAY — see `_appendCustomArea`/
// `_readCustomAreas`, and their docstrings for the lazy migration from the
// old single-row 'basemap.customArea'), each entry {id, ordinal, name?,
// bbox, band, centre_tile, template, bytes, savedAt}. `id` is minted fresh
// per download (`generateCustomAreaId`) rather than the single fixed
// `CUSTOM_AREA_ID` every earlier version shared — that shared id is why a
// second download used to silently replace the first. `name` is written
// ONLY by a rename (map_downloads_manager.js's Rename control); an
// unrenamed area's default label ("Custom area N") is derived from
// `ordinal` on every READ instead — `basemapDownloadedAreas()` fills it
// into the in-memory result, never onto this stored record — which is
// what keeps it translatable instead of frozen in whatever language was
// active at download time.
//
// The roundel's "done" state (SNOW-634) does not probe any one area's own
// bbox against the pinned cache's WHOLE tile set — that was `_probeDone`,
// deleted before this ticket. Clicking the roundel opens the downloads
// sheet (public/partials/_map_downloads_sheet.html), which lists EVERY
// downloaded area, so "done" means "the device holds at least one
// downloaded area" (basemapDownloadedAreas(), filtered to non-orphaned —
// see _renderControl below), region or custom, of however many there are.
//
// openFraming no longer re-centres the map on a saved area on open — with
// several custom areas possibly on disk, picking one to jump to would be
// arbitrary, so framing always starts from wherever the map currently
// sits. The "Download a custom area" trigger lives in the sheet
// ([data-downloads-add], map_downloads_manager.js), not the roundel's own
// click.
//
// SNOW-586 gave a confirmed run TWO distinct evictions it might trigger:
// (1) replacing the single saved area's own bucket outright when the frame
// moved or the basemap changed, and (2) making room under the standing
// byte budget by evicting OTHER areas entirely (`planBasemapDownloadBudget`
// / `planEviction`, always confirmed first via `confirmBasemapEviction`).
// SNOW-635 removes (1): a fresh id never collides with an existing bucket,
// so a confirmed run has nothing of the user's own left to replace —
// moving the frame or switching basemap before re-confirming now downloads
// a SECOND, independent area rather than replacing the first. Only (2)
// remains, exactly as mapDownloadControlInit's own region `beforeWarm`
// already works for a genuine same-region re-download.
//
// Offline-integrity: SNOW-634 relaxed the first half of this — the
// roundel now opens the downloads sheet unconditionally, which is exactly
// where storage pressure is felt, so browsing/deleting what is already
// downloaded needs no connection. Confirming a NEW download is still
// refused offline, both by the sheet's own add-trigger (a toast, not the
// overlay — map_downloads_manager.js) and by this overlay's own Download
// button once framing is open, mirroring mapDownloadControlInit's offline
// handling there.
//
// SNOW-632: a confirmed run now OWNS the overlay for its duration rather
// than handing straight back to the map. The CTA readout shows live
// progress by BOTH measures — "42% · 6.1 MB", tile count and actual
// on-disk bytes — Download disables, and dragPan plus every zoom handler
// freeze (extending _anchorZoomOnTheFrame/_releaseZoomAnchor's own
// anchor/release pair — see _lockMapForRun/_unlockMapAfterRun) so neither
// gesture can shift the selection out from under tiles already being
// fetched. Cancel stays live throughout — it is the one thing NOT
// disabled — and, mid-run, actually stops the download
// (window.pwaWarmCacheCancel(), posted from the overlay:dismissed
// listener below) rather than merely hiding a run that carries on
// unseen. A run that settles `cancelled` (basemap_download_runner.js's
// `finish` docstring) is neither success nor failure: it records nothing,
// so the roundel — re-derived from storage once `paintRun` (SNOW-634's
// rename of `setState`) calls `renderControl()` on settling — reads idle,
// never done. A SUCCESSFUL run no longer closes the overlay either — see
// paintRun's 'done' branch — it repaints the CTA in
// place ("23.4 MB downloaded", Download hidden, Cancel relabelled Close)
// and leaves closing it to the user, on the same dismiss idiom Cancel
// always used. The top instruction bar becomes a standing-budget banner
// while framing is open — "39 MB / 500 MB downloaded" across every
// pinned area, not just this one (_renderBudgetBanner) — read from
// IndexedDB once per framing session and once more when a run settles,
// never per progress tick.
(function mapCustomDownloadControlInit() {
  const btn = document.getElementById('map-custom-download-control');
  const overlayEl = document.getElementById('map-frame-overlay');
  const frameAreaEl = document.getElementById('map-frame-area');
  const frameRectEl = document.getElementById('map-frame-rect');
  const readoutEl = document.getElementById('map-frame-readout');
  const confirmBtn = document.getElementById('map-frame-confirm');
  // SNOW-632: Cancel doubles as Close once a run has completed (see
  // paintRun's 'done' branch) — its own click still goes through the
  // shared data-action="dismiss" idiom, so no new listener is needed, but
  // its label has to be readable/restorable, hence the lookup and the
  // captured default below. The instruction bar becomes the standing
  // download-budget banner while framing is open (see
  // _renderBudgetBanner) — optional, since a missing element degrades to
  // "no banner", never a hard failure.
  const cancelBtn = document.getElementById('map-frame-cancel');
  const instructionEl = document.getElementById('map-frame-instruction');
  if (!btn || !overlayEl || !frameAreaEl || !frameRectEl || !readoutEl || !confirmBtn || !cancelBtn) {
    return;
  }

  // SNOW-632: Cancel's own translated label ({% trans "Cancel" %} in the
  // template), captured once so it can be restored after a run relabels
  // the button to "Close" — see paintRun's 'done' branch and openFraming's
  // reset.
  const CANCEL_LABEL_DEFAULT = cancelBtn.textContent;

  // SNOW-635: the id `handleConfirm` mints for the run currently in
  // flight, or null between runs — see `_refreshBudgetBanner`'s own
  // comment for why this replaces a `CUSTOM_AREA_ID` lookup there.
  let currentRunAreaId = null;

  // SNOW-634: is-a-run-in-flight, replacing the six `btn.dataset.
  // downloadState === 'busy'` reads this file used to have. The roundel's
  // own `data-download-state` is now only ever 'idle'/'done' — derived
  // from storage by `_renderControl`, never told what to paint by a run —
  // so it stopped being a channel a run in flight could use at all.
  // Mirrored onto `#map-frame-overlay` as `data-run-state` by `paintRun`,
  // which IS visible for the run's whole life (`.map-framing` hides
  // `#map-controls-br`, the roundel's own container, for as long as the
  // overlay is open — see this IIFE's own header comment) and is what an
  // e2e test now reads to synchronise on a run in progress.
  let runState = 'idle';

  // The live bbox/blob for whatever the frame currently covers, while the
  // overlay is open — null when it is closed.
  let pendingBbox = null;
  let pendingBlob = null;

  // The 'move' and 'resize' listeners registered while framing, so they can
  // be removed on close. Null when not framing.
  let moveHandler = null;
  let resizeHandler = null;

  // SNOW-632: the standing download-budget banner's cached inputs — the
  // total bytes already recorded across every pinned area, and the
  // effective budget, both in bytes. Read once per framing session
  // (openFraming) and once more when a run settles (finish), never per
  // progress tick — see _refreshBudgetBanner's own comment for why.
  let bannerBaselineBytes = 0;
  let bannerBudgetBytes = 0;
  // SNOW-632 review finding: true once the pair above has been read from
  // IndexedDB at least once. openFraming fires _refreshBudgetBanner()
  // without awaiting it, so a run confirmed before that read resolves
  // would otherwise paint against the zero bannerBudgetBytes has not yet
  // been given a real value — "X MB / 0 MB downloaded". _renderBudgetBanner
  // checks this and holds the pre-existing instruction text instead.
  let bannerBudgetKnown = false;

  // This area's OWN share of `bannerBaselineBytes`. A run replaces that
  // share rather than adding to it (finish records the run's own bytes —
  // see docs/decisions/per-area-pinned-basemap-caches.md), so the live
  // banner has to take it back out or it counts the area twice for the
  // duration: baseline 62.6 MB + 18.4 MB landed read as 80.9 MB used
  // when the true figure was 18.4 MB, snapping back only once `finish`
  // re-read the records. Mirrors what `planEviction` already does for
  // budget planning, where the incoming area is excluded from the
  // standing total for exactly the same reason.
  let bannerOwnAreaBytes = 0;

  /**
   * `bytes` as a display string, via basemap_manage_core.js's
   * `formatMegabytes` — nearest-MB rounding, since this is always an
   * ACTUAL size already on disk, never buildBlob's round-UP estimate (see
   * that module's header for why the two must not be conflated).
   * Defensive: `pwaBasemapManageCore` is loaded on the map page (SNOW-570
   * put it there for the layers-menu sync dashboard), but map.js already
   * treats it as optional elsewhere (see basemapDownloadedAreas) — follow
   * suit rather than assume.
   *
   * @param {number} bytes
   * @returns {string}
   */
  function _formatBytes(bytes) {
    const manage = self.pwaBasemapManageCore;
    return manage && typeof manage.formatMegabytes === 'function'
      ? manage.formatMegabytes(bytes)
      : '0 MB';
  }

  /**
   * Drive a run: the map lock/unlock edges, the CTA's live "42% · 6.1 MB"
   * readout, and the done/error CTA repaint. Renamed from `setState`
   * (SNOW-634) — that function used to ALSO paint the roundel
   * (data-download-state, the busy fill percentage, an aria-label/title
   * drawn from a five-entry state map), but that channel was never
   * visible: `.map-framing` hides `#map-controls-br` — the roundel's own
   * container — for the overlay's entire open life (static/css/map.css),
   * and since SNOW-632 a successful run leaves the overlay open, so
   * 'busy'/'error' were painted onto an element nobody could see. The
   * roundel's own state now comes from `_renderControl`, re-derived from
   * storage rather than told what to paint — see its own docstring below.
   *
   * Two things still only change on the busy transition EDGES — never on
   * a call that merely repeats the current state:
   *
   *   Entering busy: locks the map underneath the overlay
   *   (_lockMapForRun) and starts the CTA's live "42% · 6.1 MB" readout.
   *
   *   Leaving busy: unlocks the map (_unlockMapAfterRun), asks the
   *   roundel to re-derive itself from storage now the run has settled
   *   (`renderControl()`), and paints the CTA's outcome — "23.4 MB
   *   downloaded" with Download hidden and Cancel relabelled Close for a
   *   success, or a restored "Up to N MB" readout (via _updateReadout)
   *   for anything else.
   *
   * The edge check matters: this function used to ALSO be how the
   * background probe repainted 'done'/'idle'/'offline' whenever the
   * roundel was re-evaluated (boot, a basemap switch, a connectivity
   * flip) — which can happen while the overlay is open for an unrelated
   * reason (the user reopened an already-'done' saved area). Gating the
   * CTA-specific work on `wasBusy` — true only for the single call where
   * a REAL run just ended — keeps that background path from ever
   * clobbering the CTA underneath it; the same gate still matters now
   * that the background probe is `_renderControl` calling `renderControl()`
   * rather than this function directly.
   *
   * @param {string} state - 'idle' | 'busy' | 'done' | 'error' | 'offline'.
   * @param {number} [pct] - Only meaningful for state 'busy'.
   * @param {number} [bytes] - SNOW-632: for 'busy', the run's on-disk
   *   bytes so far; for 'done', its final total. Unused otherwise.
   * @returns {void}
   */
  function paintRun(state, pct, bytes) {
    const wasBusy = runState === 'busy';
    runState = state;
    // SNOW-634: the run's own observable, now that the roundel is not —
    // visible for the run's whole life (unlike #map-controls-br), which
    // is what an e2e test (or anything else needing to observe a run in
    // flight) reads instead of the roundel.
    overlayEl.dataset.runState = state;

    // SNOW-632: lock/unlock exactly on the busy edges — every progress
    // tick repaints 'busy' but must not re-lock an already-locked map.
    if (state === 'busy' && !wasBusy) {
      _lockMapForRun();
    } else if (state !== 'busy' && wasBusy) {
      _unlockMapAfterRun();
      // SNOW-634: the run has settled — ask the roundel to re-derive its
      // own state from real storage rather than being told what to paint.
      renderControl();
    }

    // The CTA sheet's own readout/button state. See the docstring above
    // for why this is gated on the busy edges rather than on `state`
    // alone.
    if (state === 'busy') {
      if (!overlayEl.hasAttribute('hidden')) {
        confirmBtn.disabled = true;
        const busyText = self.pwaStrings.interpolate(MAP_STRINGS['frame-readout-busy'], {
          pct: `${pct || 0}%`,
          mb: _formatBytes(bytes),
        });
        if (readoutEl.textContent !== busyText) readoutEl.textContent = busyText;
        // The banner swaps this area's recorded share for the run's own
        // live bytes, against the baseline cached at openFraming — no
        // further IndexedDB read. Coerced so the opening paint('busy', 0)
        // (no bytes yet) still counts as "a run is under way, nothing
        // landed" rather than falling back to the un-excluded baseline.
        _renderBudgetBanner(Number(bytes) || 0);
      }
    } else if (wasBusy && !overlayEl.hasAttribute('hidden')) {
      if (state === 'done') {
        // Hidden via an inline style, not the `hidden` attribute:
        // .map-frame-btn sets `display: inline-flex` at the same
        // specificity as the UA's `[hidden]` rule, and — unlike
        // #map-frame-overlay, which has its own `[hidden]` override in
        // map.css — nothing here would win that tie.
        confirmBtn.style.display = 'none';
        cancelBtn.textContent = MAP_STRINGS['action-close'];
        readoutEl.classList.remove('map-frame-readout--over-ceiling');
        readoutEl.textContent = self.pwaStrings.interpolate(MAP_STRINGS['frame-readout-done'], {
          mb: _formatBytes(bytes),
        });
      } else {
        // Error, or (defensively) a cancelled run settling while the
        // overlay is somehow still open — in practice a cancel has
        // always torn framing down (and hidden the overlay) before this
        // runs. pendingBbox/pendingBlob are untouched by an error (SNOW-
        // 568 keeps the frame up for a retry).
        //
        // A ground-locked selection (the common case away from the
        // default zoomed-out view — see _updateSelection's two-regimes
        // comment) hasn't moved during a run that locked pan/zoom, so
        // _updateSelection's own "bbox unchanged, nothing to repaint"
        // optimisation (SNOW-567) would otherwise return null here and
        // leave the busy state's disabled Download/percentage text
        // sitting on screen forever. Clearing lockedBbox first forces a
        // genuine repaint of the SAME bbox — the same trick openFraming
        // itself uses before its own first paint, for the same reason.
        lockedBbox = null;
        _updateReadout();
      }
    }
  }

  /**
   * (Re)probe the roundel against real storage.
   *
   * SNOW-634: this used to probe THIS area's own saved bbox against the
   * pinned cache's actual tile contents (`_probeDone`, now deleted) —
   * "done" meant "the custom area is fully cached". The roundel now opens
   * the downloads sheet rather than framing directly, and that sheet
   * covers every downloaded area, not just this one, so "done" now means
   * "the device holds at least one downloaded area" — a user with five
   * downloaded regions and no custom area used to read `idle`, which was
   * false; there IS something to manage offline. `basemapDownloadedAreas()`
   * is the same reader `map_downloads_manager.js` lists from, so the
   * roundel and the sheet can never disagree about what "done" means. An
   * orphaned bucket (SNOW-612 — a failed part-download with no completed
   * record) is excluded: it is not "you have this area offline", it is
   * leftover quota waiting to be reclaimed from the sheet.
   *
   * Neither connectivity nor the active basemap affect this any more —
   * there is no tile-template dependency left to re-probe on
   * `snowdesk:basemap-changed`, and nothing here to grey out offline (the
   * sheet lists and deletes offline, which is exactly when storage
   * pressure is felt) — so this control no longer listens for either.
   *
   * @returns {Promise<void>}
   */
  async function _renderControl() {
    if (runState === 'busy') return;
    const areas = await basemapDownloadedAreas();
    const done = areas.some((area) => !area.orphaned);
    btn.dataset.downloadState = done ? 'done' : 'idle';
    const text = done
      ? MAP_STRINGS['custom-control-done']
      : MAP_STRINGS['custom-control-idle'];
    btn.setAttribute('aria-label', text);
    btn.title = text;
  }

  // SNOW-613: overlapping renders coalesce onto one trailing pass — see
  // `coalesceRenders`. Every trigger below calls this, not `_renderControl`.
  const renderControl = coalesceRenders(_renderControl);

  /**
   * Pixel padding (top/right/bottom/left) that fits MAP.fitBounds() to
   * the frame rect's current on-screen position, rather than the whole
   * map viewport — so re-opening at a saved area puts that area under
   * the frame, not just somewhere on screen.
   *
   * @returns {{top: number, right: number, bottom: number, left: number} | null}
   */
  function _framePadding() {
    if (!MAP || typeof MAP.getContainer !== 'function') return null;
    const container = MAP.getContainer();
    if (!container) return null;
    const mapRect = container.getBoundingClientRect();
    const frameRect = frameRectEl.getBoundingClientRect();
    if (!mapRect.width || !mapRect.height) return null;
    return {
      top: Math.max(0, frameRect.top - mapRect.top),
      right: Math.max(0, mapRect.right - frameRect.right),
      bottom: Math.max(0, mapRect.bottom - frameRect.bottom),
      left: Math.max(0, frameRect.left - mapRect.left),
    };
  }

  /**
   * The bbox a `width`x`height` box centred on (cx, cy) would cover.
   *
   * Coordinates are MAP-CONTAINER-relative, the space MapLibre's own
   * project/unproject work in — never viewport-relative. Mixing the two
   * is a live bug source: anything that shifts the map within the page
   * (a scroll, furniture appearing) moves a viewport coordinate without
   * moving a container one, and a cached value in the wrong space then
   * offsets the frame by the difference.
   *
   * Pure projection maths — reads no DOM and writes none, so the caller
   * can evaluate several candidate boxes without a single reflow.
   *
   * @param {number} cx - Centre x, in map-container pixels.
   * @param {number} cy - Centre y, in map-container pixels.
   * @param {number} width - Box width in pixels.
   * @param {number} height - Box height in pixels.
   * @returns {[number, number, number, number] | null} [west, south, east,
   *   north] in degrees, or null before the map is ready.
   */
  function _bboxForBox(cx, cy, width, height) {
    if (!MAP || typeof MAP.unproject !== 'function') return null;
    const halfW = width / 2;
    const halfH = height / 2;
    const corners = [
      [cx - halfW, cy - halfH],
      [cx + halfW, cy - halfH],
      [cx + halfW, cy + halfH],
      [cx - halfW, cy + halfH],
    ];
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    for (const [vx, vy] of corners) {
      const point = MAP.unproject([vx, vy]);
      if (point.lng < west) west = point.lng;
      if (point.lng > east) east = point.lng;
      if (point.lat < south) south = point.lat;
      if (point.lat > north) north = point.lat;
    }
    return [west, south, east, north];
  }

  /**
   * The frame's full-size box — the centre of .map-frame-area and its
   * content box, i.e. the gutter-inset rectangle the CSS allows. Read from
   * the area rather than the frame itself so the frame's own (possibly
   * capped) size never feeds back into the next measurement.
   *
   * @returns {{cx: number, cy: number, width: number, height: number} | null}
   */
  function _naturalFrameBox() {
    if (!frameAreaEl) return null;
    // Cached for the framing session. This is the only DOM *read* on the
    // per-frame path, and it forces a synchronous layout — which is what
    // pushed the geometry write onto a later animation frame, leaving the
    // frame trailing the canvas it is supposed to be glued to. The gutter
    // box only changes when the viewport does, so it is measured on open
    // and on resize (see _invalidateNaturalFrameBox) and read from here
    // otherwise.
    if (naturalBoxCache) return naturalBoxCache;
    if (!MAP || typeof MAP.getContainer !== 'function') return null;
    const container = MAP.getContainer();
    if (!container) return null;
    // Both rects are viewport-relative; the difference converts the gutter
    // box into the map-container space every other function here works in.
    // Cached in THAT space deliberately — a viewport-space cache silently
    // desyncs the moment anything shifts the map within the page.
    const mapRect = container.getBoundingClientRect();
    const rect = frameAreaEl.getBoundingClientRect();
    const style = getComputedStyle(frameAreaEl);
    const left = rect.left + parseFloat(style.paddingLeft || '0') - mapRect.left;
    const right = rect.right - parseFloat(style.paddingRight || '0') - mapRect.left;
    const top = rect.top + parseFloat(style.paddingTop || '0') - mapRect.top;
    const bottom = rect.bottom - parseFloat(style.paddingBottom || '0') - mapRect.top;
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    if (!width || !height) return null;
    naturalBoxCache = { cx: (left + right) / 2, cy: (top + bottom) / 2, width, height };
    return naturalBoxCache;
  }

  // The memoised _naturalFrameBox measurement, or null when it needs
  // re-taking.
  let naturalBoxCache = null;

  // Watches .map-frame-area for any size change and drops the cache.
  //
  // A ResizeObserver rather than a list of known causes: the area is a
  // flex child sized by what is left over after the instruction bar and
  // the CTA sheet, so it moves whenever THEY reflow — and the readout
  // inside the sheet changes text as the user pans, which is enough to
  // change the sheet's height and shift the area's centre. That produced a
  // frame offset vertically by ~18px against an otherwise perfectly
  // tracked selection. Enumerating the causes is how that bug happens
  // again; observing the element is not.
  let frameAreaObserver = null;

  /**
   * Drop the cached gutter box so the next read re-measures it.
   *
   * @returns {void}
   */
  function _invalidateNaturalFrameBox() {
    naturalBoxCache = null;
  }

  // Never shrink the frame below this fraction of its natural size. A
  // frame smaller than this is not aimable, and reaching it means the
  // ceiling cannot be met at this zoom at all — the over_ceiling backstop
  // (a red readout and a disabled Download) then still applies, exactly as
  // it did before the frame could shrink.
  const MIN_FRAME_SCALE = 0.08;

  // SNOW-567: while the ceiling caps the area, the selection holds a fixed
  // ground SIZE — {lon, merc}, a longitude span and a Web Mercator y span —
  // and is re-centred on whatever the frame is over. Null when the natural
  // frame fits under the ceiling and no cap is needed.
  //
  // Holding the size rather than the whole box is what makes the frame
  // behave like a layer on the map. A viewport-anchored frame holding a
  // fixed maximum ground area has a screen size proportional to 2**zoom
  // (the same reason a scale bar changes length), so it MUST resize as you
  // zoom; deriving it from a fixed footprint instead makes the frame a
  // projection of a fixed piece of ground, so it tracks the terrain and
  // needs no recomputation at all while zooming — an unchanged box covers
  // an unchanged set of tiles, so even the MB readout holds still.
  let lockedSize = null;

  // The last box that produced a readout, so an unchanged one can skip the
  // tile math. Purely a memo of lockedSize projected onto the current
  // frame centre — never the source of truth for the selection's size.
  let lockedBbox = null;

  /**
   * The on-screen box (viewport pixels) that `bbox` currently projects to.
   *
   * All FOUR corners are projected and the axis-aligned bounds taken over
   * the lot, mirroring _bboxForBox's own handling of the reverse
   * direction: MapLibre supports rotation, so a rotated view turns a
   * lat/lon rectangle into a non-axis-aligned quad on screen.
   *
   * @param {[number, number, number, number]} bbox
   * @returns {{cx: number, cy: number, width: number, height: number} | null}
   */
  function _screenBoxForBBox(bbox) {
    if (!MAP || typeof MAP.project !== 'function') return null;
    const [west, south, east, north] = bbox;
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (const corner of [[west, north], [east, north], [east, south], [west, south]]) {
      const point = MAP.project(corner);
      left = Math.min(left, point.x);
      right = Math.max(right, point.x);
      top = Math.min(top, point.y);
      bottom = Math.max(bottom, point.y);
    }
    return {
      cx: (left + right) / 2,
      cy: (top + bottom) / 2,
      width: right - left,
      height: bottom - top,
    };
  }

  /**
   * Draw the frame at `screenBox`, expressed relative to `natural` (the
   * gutter-inset box the stylesheet centres it in).
   *
   * The offset is a transform rather than a position change so the element
   * stays flex-centred in .map-frame-area and needs no layout to move —
   * which matters because the frame carries the dim mask as a 9999px
   * box-shadow spread, and moving it by layout would repaint that mask.
   * Writes are skipped when nothing changed.
   *
   * @param {{cx: number, cy: number, width: number, height: number}} screenBox
   * @param {{cx: number, cy: number, width: number, height: number}} natural
   * @returns {void}
   */
  function _placeFrame(screenBox, natural) {
    // Sub-pixel, deliberately. The canvas underneath moves at sub-pixel
    // precision, so rounding the frame to whole pixels makes it snap
    // against a smoothly-moving map — a stutter of up to a pixel per frame
    // that reads as judder however well the geometry itself tracks.
    const width = `${screenBox.width}px`;
    const height = `${screenBox.height}px`;
    const dx = screenBox.cx - natural.cx;
    const dy = screenBox.cy - natural.cy;
    const transform = dx === 0 && dy === 0 ? '' : `translate3d(${dx}px, ${dy}px, 0)`;
    if (frameRectEl.style.width !== width) frameRectEl.style.width = width;
    if (frameRectEl.style.height !== height) frameRectEl.style.height = height;
    if (frameRectEl.style.transform !== transform) frameRectEl.style.transform = transform;
  }

  /**
   * Hand the frame's geometry back to the stylesheet — it fills its area
   * again, and a viewport resize keeps working.
   *
   * @returns {void}
   */
  function _releaseFrame() {
    frameRectEl.style.removeProperty('width');
    frameRectEl.style.removeProperty('height');
    frameRectEl.style.removeProperty('transform');
  }

  /**
   * `bbox`'s size as a ground footprint that is independent of zoom: a
   * longitude span, and a span in Web Mercator y (NOT degrees of latitude,
   * which are not linear in the projection).
   *
   * @param {[number, number, number, number]} bbox
   * @returns {{lon: number, merc: number}}
   */
  function _groundSizeOf(bbox) {
    const [west, south, east, north] = bbox;
    return {
      lon: east - west,
      merc:
        maplibregl.MercatorCoordinate.fromLngLat({ lng: west, lat: south }).y -
        maplibregl.MercatorCoordinate.fromLngLat({ lng: west, lat: north }).y,
    };
  }

  /**
   * The bbox of ground size `size` centred on whatever the frame is over
   * right now.
   *
   * Holding the SIZE and re-deriving the centre each time — rather than
   * translating the previous bbox — is what keeps this stable: there is no
   * project/unproject round trip whose rounding could accumulate over the
   * hundreds of updates a long gesture produces.
   *
   * @param {{lon: number, merc: number}} size
   * @param {{cx: number, cy: number}} frameCentre - Map-container pixels.
   * @returns {[number, number, number, number] | null}
   */
  function _bboxOfSizeUnderFrame(size, frameCentre) {
    if (!MAP || typeof MAP.unproject !== 'function') return null;
    const centre = MAP.unproject([frameCentre.cx, frameCentre.cy]);
    const centreMerc = maplibregl.MercatorCoordinate.fromLngLat(centre);
    const halfMerc = size.merc / 2;
    // Clamp to the projection's valid range: a box hanging off the top or
    // bottom of the Mercator world has no latitude to convert back to.
    const northY = Math.max(0, centreMerc.y - halfMerc);
    const southY = Math.min(1, centreMerc.y + halfMerc);
    const north = new maplibregl.MercatorCoordinate(centreMerc.x, northY, 0).toLngLat().lat;
    const south = new maplibregl.MercatorCoordinate(centreMerc.x, southY, 0).toLngLat().lat;
    return [centre.lng - size.lon / 2, south, centre.lng + size.lon / 2, north];
  }

  /**
   * Bring the frame and the selection up to date with the map, and return
   * the selection — or null when it is unchanged and there is nothing for
   * the caller to repaint.
   *
   * Two cases:
   *
   * - **Under the ceiling.** No cap is needed. The frame fills its area
   *   under stylesheet control, and the selection is whatever ground it
   *   covers.
   * - **Capped.** The selection holds a fixed ground SIZE — the largest
   *   that fits the ceiling — centred on whatever the frame is over. The
   *   frame is then drawn where that box projects to.
   *
   * There is deliberately no pan-versus-zoom branch. Framing re-anchors
   * zoom to the frame's own centre (_anchorZoomOnTheFrame), so a zoom
   * cannot change the ground under the frame: re-deriving the selection
   * from that centre every update is a no-op through a whole zoom
   * gesture — same box, same tiles, an estimate that does not so much as
   * flicker — and moves it only when a pan (or the map clamping against
   * maxBounds) genuinely puts different ground under the frame. Trying to
   * tell the two gestures apart instead is what previously left the
   * selection yanked back mid-zoom, and no float comparison of zoom and
   * centre survived contact with a real inertial gesture.
   *
   * The cap threshold comes from pwaBasemapDownloadCore.budgetScaleForBBox
   * in one step — see that function for why a search against buildBlob's
   * own (floored, hence step-shaped) tile count made the frame shimmer
   * while panning (SNOW-566). Locking and releasing share that one
   * threshold, and at it the locked box and the natural frame coincide, so
   * crossing it is continuous rather than a jump.
   *
   * @returns {{bbox: [number, number, number, number], blob: Object} | null}
   *   The selection, or null when it is unchanged (or the map is not ready)
   *   and there is nothing for the caller to repaint.
   */
  function _updateSelection() {
    const core = self.pwaBasemapDownloadCore;
    const natural = _naturalFrameBox();
    if (!core || !natural) return null;

    const naturalBbox = _bboxForBox(natural.cx, natural.cy, natural.width, natural.height);
    if (!naturalBbox) return null;
    const [minZ, maxZ] = core.MICRO_BAND;
    const scale = core.budgetScaleForBBox(naturalBbox, minZ, maxZ);

    if (scale >= 1) {
      lockedSize = null;
      lockedBbox = null;
      _releaseFrame();
      return { bbox: naturalBbox, blob: core.buildBlob(naturalBbox, minZ, maxZ) };
    }

    if (lockedSize === null) {
      // Engaging the cap: adopt the largest ground footprint that fits.
      const capped = _bboxForBox(
        natural.cx,
        natural.cy,
        natural.width * Math.max(MIN_FRAME_SCALE, scale),
        natural.height * Math.max(MIN_FRAME_SCALE, scale),
      );
      if (!capped) return null;
      lockedSize = _groundSizeOf(capped);
    }

    const bbox = _bboxOfSizeUnderFrame(lockedSize, natural);
    if (!bbox) return null;
    const screenBox = _screenBoxForBBox(bbox);
    if (screenBox) _placeFrame(screenBox, natural);
    // Unchanged through a zoom, which is the common case — say so, and the
    // caller skips the tile math and leaves the readout alone.
    if (_bboxesEqual(bbox, lockedBbox)) return null;
    lockedBbox = bbox;
    return { bbox, blob: core.buildBlob(bbox, minZ, maxZ) };
  }

  /**
   * Recompute pendingBbox/pendingBlob from the frame's current on-screen
   * position and paint the readout. Cheap, local arithmetic
   * (pwaBasemapDownloadCore.buildBlob) — no network round-trip on any
   * frame of a pan or zoom, which is the whole point of computing this
   * client-side.
   *
   * @returns {void}
   */
  function _updateReadout() {
    const core = self.pwaBasemapDownloadCore;
    // Placing the frame and measuring it are the same step: the update
    // above already produced the box it settled on, so reuse its result
    // rather than re-measuring the DOM it just wrote to (which would read
    // back the pre-layout size on the same frame). A null means the
    // selection is unchanged — a zoom against a ground-locked area — and
    // there is nothing here to repaint.
    const fitted = core ? _updateSelection() : null;
    if (!core || !fitted) return;
    pendingBbox = fitted.bbox;
    pendingBlob = fitted.blob;
    const overCeiling = pendingBlob.over_ceiling;
    const text = overCeiling
      ? self.pwaStrings.interpolate(MAP_STRINGS['frame-over-ceiling'], {
          mb: core.DOWNLOAD_CEILING_MB,
        })
      : self.pwaStrings.interpolate(MAP_STRINGS['frame-up-to'], { mb: pendingBlob.mb });
    // Same no-op guard as the frame's own size write above: the estimate
    // holds still across most 'move's, and rewriting identical text still
    // costs a layout of the CTA bar.
    if (readoutEl.textContent !== text) readoutEl.textContent = text;
    readoutEl.classList.toggle('map-frame-readout--over-ceiling', overCeiling);
    // Offline-integrity: never let the CTA's own Download button start a
    // run while offline, even if the roundel that opened framing read
    // idle at the time (a connectivity change mid-session).
    confirmBtn.disabled = overCeiling || !navigator.onLine;
  }

  /**
   * Convert a bbox [west, south, east, north] to MapLibre's fitBounds
   * shape [[west, south], [east, north]].
   *
   * @param {[number, number, number, number]} bbox
   * @returns {[[number, number], [number, number]]}
   */
  function _boundsFromBBox(bbox) {
    const [west, south, east, north] = bbox;
    return [[west, south], [east, north]];
  }

  /**
   * Make every zoom gesture pivot on the frame instead of the pointer, for
   * as long as framing is open.
   *
   * MapLibre anchors a wheel or pinch zoom at the cursor: the ground under
   * the pointer stays put and everything else moves around it. With a
   * ground-locked selection that is visibly wrong — zoom out with the
   * pointer off to one side and the frame, still correctly glued to its
   * terrain, sails off towards that corner, then has to travel back when
   * you zoom in again.
   *
   * Two steps, because "the frame" is not "the map centre": the frame area
   * is the space left between the instruction bar and the CTA sheet, so
   * its centre sits above the viewport's.
   *
   *   1. ``setPadding`` tells the map that its centre is the frame's
   *      centre. Everything downstream — ``getCenter``, ``fitBounds``, and
   *      the centre-anchored zoom below — then works to the frame.
   *   2. The zoom handlers are re-enabled with ``around: 'center'``.
   *      ``scrollZoom.enable`` no-ops when the handler is already enabled
   *      (which it is, by default), hence the disable first — without it
   *      the option is accepted and silently ignored.
   *
   * The pay-off is more than cosmetic: a centre-anchored zoom leaves
   * ``getCenter()`` untouched, so "the centre moved" now means a pan and
   * nothing else, and the locked bbox stays centred under the frame with
   * no offset to apply.
   *
   * @returns {void}
   */
  function _anchorZoomOnTheFrame() {
    const natural = _naturalFrameBox();
    const container = MAP.getContainer();
    if (natural && container) {
      MAP.setPadding({
        top: Math.max(0, natural.cy - natural.height / 2),
        bottom: Math.max(0, container.clientHeight - (natural.cy + natural.height / 2)),
        left: Math.max(0, natural.cx - natural.width / 2),
        right: Math.max(0, container.clientWidth - (natural.cx + natural.width / 2)),
      });
    }
    MAP.scrollZoom.disable();
    MAP.scrollZoom.enable({ around: 'center' });
    MAP.touchZoomRotate.enable({ around: 'center' });
    // Double-click zoom has no centre-anchored mode, and it pivots on the
    // click point — the same defect by another route.
    MAP.doubleClickZoom.disable();
  }

  /**
   * Undo _anchorZoomOnTheFrame: hand the map back its own centre and its
   * default, pointer-anchored zoom handlers.
   *
   * @returns {void}
   */
  function _releaseZoomAnchor() {
    if (!MAP) return;
    MAP.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
    MAP.scrollZoom.disable();
    MAP.scrollZoom.enable();
    MAP.touchZoomRotate.enable();
    MAP.doubleClickZoom.enable();
    // SNOW-632: also the safety net for _lockMapForRun's dragPan.disable()
    // — a Cancel click mid-run tears framing (and this) down synchronously,
    // well before the run's own settle reaches _unlockMapAfterRun, so
    // without this dragPan would stay frozen for however long the
    // cancellation round trip takes. Idempotent the rest of the time:
    // dragPan is never disabled outside a run, so this is a harmless no-op
    // on every other call site.
    MAP.dragPan.enable();
  }

  /**
   * Freeze every map gesture — pan and all three zoom handlers — for the
   * duration of a confirmed run (SNOW-632, requirement 3). Framing itself
   * leaves dragPan enabled (panning is how the user re-aims a ground-
   * locked selection — see _updateSelection's header comment); this is
   * stricter and applies only while a download is actually in flight,
   * because a run has already committed to a specific tile set and must
   * not have the ground shift under it mid-fetch.
   *
   * Called from paintRun on the busy transition edge, never directly —
   * see that function's docstring.
   *
   * @returns {void}
   */
  function _lockMapForRun() {
    if (!MAP) return;
    MAP.dragPan.disable();
    MAP.scrollZoom.disable();
    MAP.touchZoomRotate.disable();
    MAP.doubleClickZoom.disable();
  }

  /**
   * Undo _lockMapForRun once a run settles. Re-anchors to the frame
   * (_anchorZoomOnTheFrame) only if framing is STILL open — a run that
   * settles after the user has already cancelled finds the overlay
   * hidden and _teardownFraming/_releaseZoomAnchor already run, and
   * re-anchoring here would resurrect the padding and handlers that
   * teardown just cleared. dragPan is re-enabled unconditionally either
   * way — _releaseZoomAnchor also does this defensively (see its own
   * comment), but the settle path is this function's own responsibility
   * whenever framing is still up.
   *
   * @returns {void}
   */
  function _unlockMapAfterRun() {
    if (!MAP) return;
    MAP.dragPan.enable();
    if (!overlayEl.hasAttribute('hidden')) {
      _anchorZoomOnTheFrame();
    }
  }

  /**
   * Render #map-frame-instruction as the standing download total against
   * the budget — "39 MB / 500 MB downloaded" — from the cached
   * `bannerBaselineBytes`/`bannerBudgetBytes`, optionally layering a live
   * run's own progress on top.
   *
   * @param {number} [liveBytes] Bytes landed by a run in progress. The
   *   area's own previously-recorded share is swapped out for this figure
   *   rather than added to it — see `bannerOwnAreaBytes`. Omitted outside
   *   a run, when the cached baseline is already the whole truth.
   * @returns {void}
   */
  function _renderBudgetBanner(liveBytes) {
    // SNOW-632 review finding: bannerBudgetBytes starts at 0, and
    // openFraming's _refreshBudgetBanner() read is unawaited — painting
    // before it resolves would show a false "X MB / 0 MB" denominator.
    // Leave whatever instruction text is already there until the real
    // budget is known.
    if (!instructionEl || !bannerBudgetKnown) return;
    // `undefined` (no run) leaves the baseline untouched; a run swaps this
    // area's recorded share for what it has landed so far. Floored at 0
    // because the two figures come from different reads and a stale
    // baseline must never render a negative total.
    const usedBytes =
      liveBytes === undefined
        ? bannerBaselineBytes
        : Math.max(0, bannerBaselineBytes - bannerOwnAreaBytes) + (Number(liveBytes) || 0);
    instructionEl.textContent = self.pwaStrings.interpolate(
      MAP_STRINGS['frame-budget-banner'],
      { used: _formatBytes(usedBytes), budget: _formatBytes(bannerBudgetBytes) },
    );
  }

  /**
   * (Re)read the standing download total and budget from IndexedDB, cache
   * them, and repaint the banner.
   *
   * Called exactly twice a run — once from openFraming, once more from
   * `finish` once the run has settled — never from the progress-tick path
   * (paintRun/_renderBudgetBanner), which repaints the SAME cached numbers
   * plus the run's own live bytes. Each read here is two `meta:app` round
   * trips (basemapDownloadedAreas covers `basemap.regions` AND
   * `basemap.customAreas`) plus a third for the budget row; a live run
   * repaints its percentage roughly once per tile, so doing this on every
   * tick would be dozens of IndexedDB reads a second for no visible gain
   * — the banner already tracks the run via `liveBytes`.
   *
   * Best-effort and async: never blocks the overlay's own appearance on
   * IndexedDB, and a failed read just leaves the previous figures in
   * place.
   *
   * @returns {Promise<void>}
   */
  async function _refreshBudgetBanner() {
    if (!instructionEl) return;
    try {
      const [areas, budgetBytes] = await Promise.all([
        basemapDownloadedAreas(),
        basemapDownloadBudgetBytes(),
      ]);
      bannerBaselineBytes = areas.reduce((sum, area) => sum + (Number(area.bytes) || 0), 0);
      // Read alongside the total, from the SAME snapshot, so the two can
      // never disagree about what this area currently contributes.
      //
      // SNOW-635: keyed off THIS RUN's own generated id
      // (`currentRunAreaId`), not the legacy `CUSTOM_AREA_ID` — every
      // confirm downloads a NEW area now, so a run's id never has an
      // existing record to exclude until it has actually recorded one.
      // Called from openFraming, before any run this session has minted an
      // id, `currentRunAreaId` is null and this always misses — resolving
      // to 0 bytes, correctly: a session that has confirmed nothing yet
      // owns no bytes to exclude from the baseline.
      const ownArea = currentRunAreaId
        ? areas.find((area) => area.id === currentRunAreaId)
        : null;
      bannerOwnAreaBytes = ownArea ? Number(ownArea.bytes) || 0 : 0;
      bannerBudgetBytes = budgetBytes;
      bannerBudgetKnown = true;
    } catch (_e) {
      // Best-effort — the banner keeps showing its previous figures (or,
      // if this is the first read and it fails, the pre-existing
      // instruction text — bannerBudgetKnown stays false).
    }
    _renderBudgetBanner();
  }

  /**
   * Open the framing overlay: reveal it, and start tracking the live
   * readout on every 'move'.
   *
   * SNOW-635: no longer moves the map to a saved area first — with any
   * number of custom areas possibly on disk, picking one to jump to would
   * be arbitrary. Framing always starts from wherever the map currently
   * sits, exactly like the very first time this control is ever opened.
   *
   * @returns {void}
   */
  function openFraming() {
    if (!MAP) return;
    overlayEl.removeAttribute('hidden');
    // SNOW-632: undo whatever the PREVIOUS session's completed run left on
    // the CTA bar (paintRun's 'done' branch hides Download and relabels
    // Cancel to Close) — without this a Close button, and no Download at
    // all, would leak into a framing session that has not downloaded
    // anything yet. _updateReadout below repaints the readout text and
    // confirmBtn's disabled state from the CURRENT selection, so nothing
    // further is needed for those.
    confirmBtn.style.removeProperty('display');
    cancelBtn.textContent = CANCEL_LABEL_DEFAULT;
    // SNOW-635: a fresh session has downloaded nothing (yet) of its own —
    // see _refreshBudgetBanner's own comment for why this is what makes
    // its "own area" lookup correctly resolve to 0 bytes below.
    currentRunAreaId = null;
    // The standing download-budget banner — read once here (never per
    // progress tick; see _refreshBudgetBanner's own comment) and rendered
    // as soon as the numbers arrive, without making the overlay's own
    // appearance wait on IndexedDB.
    _refreshBudgetBanner();
    // Strip the map furniture that has nothing to do with the area being
    // framed — the region readout names a region the download does not
    // follow, and the date ribbon/scrubber describe a day. Both would
    // otherwise sit lit inside the cutout and read as part of the
    // selection. Driven by a body class rather than per-element hidden
    // attributes so each owner module keeps sole control of its own
    // visibility state (see .map-framing in static/css/map.css).
    document.body.classList.add('map-framing');
    // Drop any cap left on the frame by the previous open, so the frame
    // measures the natural gutter rather than a shrunken frame from a zoom
    // level the map is no longer at. _updateReadout re-applies the cap for
    // the view we actually land on. The ground lock goes with it: a fresh
    // open aims from wherever the map is now, never from the area the last
    // one happened to leave locked.
    lockedSize = null;
    lockedBbox = null;
    _invalidateNaturalFrameBox();
    _releaseFrame();
    _anchorZoomOnTheFrame();
    _updateReadout();
    // Synchronously, on every 'move' — NOT deferred to the next animation
    // frame. MapLibre fires 'move' from inside its own render loop, so a
    // style write made here is composited with the very frame that moved
    // the canvas; scheduling it instead put the frame one frame behind the
    // map, which measured up to 12px of positional lag and 15px of size lag
    // mid-gesture and read as judder (SNOW-567). Settled state was correct
    // throughout, which is why only a test that samples DURING the
    // animation catches it.
    //
    // Deferring was originally there to avoid re-measuring the DOM on every
    // event; that cost is gone now the gutter box is cached, leaving this
    // path a handful of projections and a style write with no layout read
    // at all.
    moveHandler = () => {
      // SNOW-632, requirement 2: the map is locked during a run (see
      // _lockMapForRun) so this should never fire from a user gesture
      // while busy — but guard it anyway rather than trust that no
      // programmatic 'move' (e.g. _unlockMapAfterRun's own re-anchor) can
      // land here first and re-enable Download out from under a run still
      // in flight.
      if (runState === 'busy') return;
      _updateReadout();
    };
    MAP.on('move', moveHandler);
    if (typeof ResizeObserver === 'function') {
      frameAreaObserver = new ResizeObserver(() => {
        _invalidateNaturalFrameBox();
        // Dropping the cache is not enough: the frame is only ever placed
        // in response to a map 'move', so after a reflow with no movement
        // it would keep the offset it was given against the OLD gutter box
        // until the user next touched the map. Re-place it here — geometry
        // only, deliberately not the estimate, so this cannot feed back
        // into the readout that changed the sheet's height in the first
        // place.
        if (!moveHandler || !lockedBbox) return;
        const natural = _naturalFrameBox();
        const screenBox = natural ? _screenBoxForBBox(lockedBbox) : null;
        if (natural && screenBox) _placeFrame(screenBox, natural);
      });
      frameAreaObserver.observe(frameAreaEl);
    } else {
      // No ResizeObserver: fall back to the map's own resize event, which
      // covers the viewport case but not a sheet reflow.
      resizeHandler = () => _invalidateNaturalFrameBox();
      MAP.on('resize', resizeHandler);
    }
  }

  /**
   * Stop tracking the frame (removes the 'move' listener) and clear the
   * pending bbox/blob. Does NOT hide the overlay itself — that is the
   * Cancel/Close button's own job, via overlays.js's shared dismiss
   * handler, which has already hidden the overlay before dispatching
   * overlay:dismissed by the time this runs (SNOW-632: since a SUCCESSFUL
   * run no longer closes the overlay itself — see paintRun's 'done'
   * branch — dismissal is now the ONLY path that ever calls this).
   *
   * @returns {void}
   */
  function _teardownFraming() {
    // Restores the furniture openFraming stripped.
    document.body.classList.remove('map-framing');
    if (moveHandler && MAP) {
      MAP.off('move', moveHandler);
    }
    if (resizeHandler && MAP) {
      MAP.off('resize', resizeHandler);
    }
    if (frameAreaObserver) {
      frameAreaObserver.disconnect();
      frameAreaObserver = null;
    }
    moveHandler = null;
    resizeHandler = null;
    _releaseZoomAnchor();
    _invalidateNaturalFrameBox();
    lockedSize = null;
    lockedBbox = null;
    pendingBbox = null;
    pendingBlob = null;
  }

  /**
   * Whether two bboxes describe the same area, to a tolerance tight
   * enough to absorb floating-point noise from repeated
   * unproject()/fitBounds() round trips but loose enough that "the user
   * didn't touch the map" always reads as unchanged.
   *
   * @param {number[] | null | undefined} a
   * @param {number[] | null | undefined} b
   * @returns {boolean}
   */
  function _bboxesEqual(a, b) {
    if (!a || !b) return false;
    for (let i = 0; i < 4; i++) {
      if (Math.abs(a[i] - b[i]) > 1e-6) return false;
    }
    return true;
  }

  /**
   * Run the confirmed download: assemble the URL list and hand it to the
   * SW's warm-cache handler — mirrors mapDownloadControlInit's
   * handleClick, sharing its assembleBasemapDownloadFeedURLs helper.
   *
   * SNOW-635: every confirm mints a FRESH area id (`generateCustomAreaId`)
   * and downloads it as a new, independent area — there is no longer a
   * single saved area a moved frame or a changed basemap could replace, so
   * this no longer runs a `beforeWarm` eviction of its own before warming;
   * only the runner's own budget-driven eviction (of OTHER areas,
   * confirmed first) can still remove anything.
   *
   * SNOW-632: a run's outcome no longer decides whether the overlay
   * closes — only the user's own Cancel/Close click does that (see
   * paintRun's 'done' branch and the overlay:dismissed listener). A
   * SUCCESSFUL run instead repaints the CTA in place: the readout becomes
   * "23.4 MB downloaded", Download hides, and Cancel relabels to Close. A
   * CANCELLED run (the user dismissed while this was in flight) is
   * neither success nor failure — see the `cancelled` branch below.
   *
   * @returns {Promise<void>}
   */
  async function handleConfirm() {
    if (!pendingBbox || !pendingBlob || pendingBlob.over_ceiling) return;
    // SNOW-568: the overlay now stays open across a failed run (so the
    // framed area survives for a retry), and it was always open during a
    // running one — so the Download button, unlike the roundel, needs its
    // own re-entrancy guard.
    if (runState === 'busy') return;
    // Offline-integrity: never start a download offline, even if a race
    // left the button enabled at the moment of the click.
    if (!navigator.onLine) {
      paintRun('offline');
      return;
    }
    const blob = pendingBlob;
    const bbox = pendingBbox;

    const core = self.pwaBasemapDownloadCore;
    // SNOW-635: a fresh id per run — never CUSTOM_AREA_ID — is what lets
    // more than one custom area exist at once; see this function's own
    // docstring. `runPinnedDownload` re-reads the core itself and fails
    // the run properly if it is missing; this only needs it for the area
    // id, so a missing core simply yields none and the runner handles the
    // rest.
    const areaId = core ? core.generateCustomAreaId() : '';
    currentRunAreaId = areaId;

    await runPinnedDownload({
      areaId: areaId,
      mb: blob.mb,
      // This control's roundel carries no size, so the shared runner's
      // (state, pct, bytes) triple is passed straight through — SNOW-632:
      // `bytes` is what drives the CTA's live "42% · 6.1 MB" readout.
      paint: (nextState, pct, bytes) => paintRun(nextState, pct, bytes),
      loadBlob: () => blob,
      finish: async (result, runBlob, { core, progressFill, template }) => {
        // SNOW-632: a cancelled run is neither success nor failure — the
        // user asked it to stop, not for it to fail — so this is checked
        // BEFORE `ok`. A cancelled run always has `failed === 0` (nothing
        // the cancel skipped was ever attempted), which would otherwise
        // read as a clean success; see basemap_download_runner.js's
        // `finish` docstring for why the check has to come first.
        const cancelled = !!(result && result.cancelled);
        // "done" requires at least one success and no failures; a partial,
        // vacuous, or absent result must not claim the area is downloaded.
        //
        // SNOW-568: a run that didn't succeed now says so. It used to fall
        // back to 'idle' silently, indistinguishable from never having
        // clicked Download.
        const ok = !cancelled && !!(result && result.ok > 0 && result.failed === 0);
        if (cancelled) {
          // Overlay teardown already ran synchronously when the user
          // dismissed (see the overlay:dismissed listener below) — this
          // just settles the roundel. Partial tiles may have landed
          // before the worker honoured the cancel, so this can never
          // claim 'done': the probe checks the WHOLE saved area's tile
          // set, and painting done here would claim more than is true.
          await progressFill.finish(false);
          paintRun(navigator.onLine ? 'idle' : 'offline');
        } else if (ok) {
          // SNOW-632: `bytes` is this run's OWN reported total, recorded
          // outright — never accumulated onto anything, and never
          // re-measured from the bucket (a live tile response carries no
          // `Content-Length` under gzip, so a bucket measurement reads ~0
          // in production; see the decision doc for the curl evidence).
          // SNOW-635: this is always a brand-new bucket under a fresh id
          // (no prior run ever shared it), so the run's own total is
          // trivially the bucket's whole total — there is no prior-basemap
          // or prior-bbox leftover to have cleared first, unlike the
          // single shared area this replaces.
          const area = {
            id: areaId,
            ordinal: await _nextCustomAreaOrdinal(),
            bbox: bbox,
            band: runBlob.band,
            centre_tile: runBlob.centre_tile,
            template: template,
            bytes: Number(result.bytes) || 0,
            savedAt: new Date().toISOString(),
          };
          await _appendCustomArea(area);
          // SNOW-569's pulse still plays on success; SNOW-632 removed the
          // overlay-close that used to precede it (SNOW-568 already left
          // it open on failure, and requirement 5 now does the same for a
          // success — see paintRun's 'done' branch for what replaces it).
          await progressFill.finish(true);
          paintRun('done', undefined, area.bytes);
        } else {
          await progressFill.finish(false);
          paintRun('error');
          // A null result means there was no active worker at all — nothing
          // ran and nothing was cached, which is still a failed download
          // from the user's point of view, just one with no reason to
          // report beyond the generic line.
          revealBasemapDownloadError(result ? result.reason : null);
        }
        // SNOW-505/522: the warm-cache run has just warmed the shell +
        // pinned basemap caches; re-probe every sync dot against real
        // cache state, mirroring mapDownloadControlInit's own post-run
        // refresh — the layers menu is a live cache-state dashboard. A
        // cancelled run needs this exactly as much as a completed one:
        // partial tiles may have landed before the worker honoured the
        // cancel, and stale dots would misreport them.
        window.pwaLayerSyncStatus?.refresh();
        // SNOW-570/SNOW-587: and the cached-tiles overlay, so tiles that
        // just finished downloading appear immediately rather than at the
        // next basemap swap or reload.
        window.pwaDownloadedOverlay?.refresh();
        // SNOW-632: the standing total on disk has changed (a success
        // adds this run's bytes; a cancel or a partial failure may have
        // landed some tiles too), so the banner's cached baseline is
        // stale — re-read it once, rather than trying to derive the new
        // figure from what `finish` already knows.
        _refreshBudgetBanner();
      },
    });
  }

  // SNOW-634: the roundel now opens the downloads sheet unconditionally —
  // no busy guard (framing hides #map-controls-br, the roundel's own
  // container, for as long as it is open, so this can never be clicked
  // mid-run anyway) and no offline guard (the sheet lists and deletes
  // offline, which is exactly when storage pressure is felt). Confirming
  // a NEW download is still gated on connectivity, both by the sheet's own
  // add-trigger and by this overlay's own Download button.
  btn.addEventListener('click', () => {
    window.pwaDownloadsManager?.open();
  });

  confirmBtn.addEventListener('click', () => handleConfirm());

  // Cancel/Close both go through overlays.js's shared [data-action="dismiss"]
  // handler (it already hid the overlay and dispatched this event by the
  // time this listener runs) — teardown-only here, matching this IIFE's
  // header comment.
  document.addEventListener('overlay:dismissed', (e) => {
    if (e.detail && e.detail.overlay === overlayEl) {
      // SNOW-632: capture BEFORE teardown — a busy run is this control's
      // own sign that it is in flight, and teardown below does not touch
      // it (only paintRun, from the run's own eventual `finish`, does).
      const wasBusy = runState === 'busy';
      _teardownFraming();
      // SNOW-568: cancelling framing abandons the attempt the toast was
      // reporting on. Leaving it up would also strand it at the offset
      // that cleared the CTA sheet which has just gone away.
      clearBasemapDownloadError();
      // SNOW-632: a Cancel click while a run is in flight asks the worker
      // to stop dispatching further URLs. A safe no-op otherwise — this
      // is the SAME dismiss idiom an idle Cancel (nothing running) or a
      // post-completion Close (already settled) also go through, and
      // `pwaWarmCacheCancel` is itself a no-op with no run in flight — but
      // the check avoids posting to a service worker that isn't waiting
      // for anything.
      if (wasBusy) {
        window.pwaWarmCacheCancel?.();
      }
    }
  });

  // SNOW-634: unlike mapDownloadControlInit's own copy of this listener,
  // this control no longer listens for 'snowdesk:basemap-changed' — the
  // roundel's "done" no longer depends on the active basemap's tile
  // template (there is no tile-cache probe left to re-run; see
  // _renderControl's own docstring).
  //
  // Offline-integrity: re-validate the open CTA's Download button on every
  // connectivity transition — SNOW-632: a run in flight owns the CTA (see
  // paintRun), so this is skipped while busy, exactly like the 'move'
  // handler above. The roundel itself no longer has an offline state to
  // re-render here either (see _renderControl).
  document.addEventListener('snowdesk:connectivity-changed', () => {
    if (pendingBbox && runState !== 'busy') _updateReadout();
  });

  // Boot: probe the roundel against real storage. SNOW-634: unlike the old
  // tile-cache probe, `basemapDownloadedAreas()` needs neither MAP nor the
  // active basemap's tile template, so this doesn't wait on
  // MAP_READY_PROMISE — that used to be a SEPARATE trigger for a second,
  // map-dependent probe. SNOW-635: no longer preceded by loading a saved
  // area either — see openFraming's own docstring for why there is none
  // left to load.
  renderControl();

  window.pwaCustomAreaDownload = Object.freeze({
    // SNOW-634: the sheet's add-trigger reaches framing through this —
    // same frozen-global idiom as pwaBasemapDownloads/pwaLayersMenu/
    // pwaDownloadedOverlay.
    openFraming: openFraming,
    // So a delete in the sheet settles the roundel without waiting for
    // the next basemap switch or connectivity flip — neither of which
    // affect it any more.
    refresh: renderControl,
  });
})();

// SNOW-65: auto-zoom toggle — now a menuitemcheckbox inside the layers
// menu rather than a standalone icon button.
(function autozoomToggleInit() {
  const btn = document.getElementById('autozoom-toggle');
  if (!btn) return;

  const STORAGE_KEY = AUTOZOOM_STORAGE_KEY;

  const sync = () => {
    btn.setAttribute('aria-checked', AUTOZOOM ? 'true' : 'false');
  };

  sync(); // Reflect the value already set by the main IIFE from localStorage.

  btn.addEventListener('click', () => {
    AUTOZOOM = !AUTOZOOM;
    writeStorage(STORAGE_KEY, String(AUTOZOOM));
    sync();
  });
})();

// SNOW-328: Locate-me roundel — wires #locate-toggle to a MapLibre
// GeolocateControl so clicking the pill one-shots a pan/zoom to the
// user's current position. The control's own DOM button is hidden via
// CSS (.maplibregl-ctrl-geolocate { display: none }) so MapLibre still
// manages the internal state machine and the blue accuracy circle, while
// our pill in the utility cluster is the only visible affordance.
//
// Permission-denied and position-unavailable errors are swallowed
// silently — the map simply stays where it was, which is the least
// surprising behaviour when the user declines or the device has no fix.
(function geolocateInit() {
  const btn = document.getElementById('locate-toggle');
  if (!btn || !MAP) return;

  const control = new maplibregl.GeolocateControl({
    trackUserLocation: false,
    showAccuracyCircle: true,
    // maximumAge lets control.trigger() reuse the fix from the bounds pre-check
    // below (taken moments earlier) instead of acquiring a second one.
    positionOptions: { enableHighAccuracy: true, maximumAge: 60000 },
  });

  // SNOW-324/486: the off-map notification (#offmap-banner) renders through
  // the shared floating-banner primitive (includes/_overlay_banner.html) and
  // is a [data-overlay], so overlays.js owns the "×" hide via the class idiom.
  // It floats (fixed) on every viewport, so revealing it no longer affects the
  // map's layout — no resize needed. map.js owns only the reveal and the 7s
  // auto-dismiss timer.
  const offMapBanner = document.getElementById('offmap-banner');
  let offMapTimer = null;

  function hideOffMapBanner() {
    if (!offMapBanner) return;
    clearTimeout(offMapTimer);
    offMapBanner.classList.add('hidden');
  }

  function showOffMapBanner() {
    if (!offMapBanner) return;
    clearTimeout(offMapTimer);
    offMapBanner.classList.remove('hidden');
    offMapTimer = setTimeout(hideOffMapBanner, 7000);
  }

  // If the user dismisses via the "×" (handled by overlays.js) before the
  // timeout fires, cancel the pending auto-dismiss so it can't fight a later
  // re-reveal.
  document.addEventListener('overlay:dismissed', (event) => {
    if (event.detail && event.detail.overlay === offMapBanner) {
      clearTimeout(offMapTimer);
    }
  });

  // Register the control at top-right so MapLibre adds its (hidden)
  // button and stands up the internal state machine. The position
  // marker and accuracy circle are added to the map canvas once a fix
  // is obtained, not at addControl time.
  MAP.addControl(control, 'top-right');

  btn.addEventListener('click', () => {
    if (!navigator.geolocation) return;
    // Check the fix against the map's bounds BEFORE handing off to
    // control.trigger(): MapLibre's GeolocateControl throws ("Unexpected
    // watchState undefined") on an out-of-maxBounds point with
    // trackUserLocation:false, and there's nowhere to fly to anyway. When the
    // user is off the map we say so; when they're on it we trigger the control
    // (which reuses this fix via maximumAge) for the fly + marker + circle.
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const bounds = MAP.getMaxBounds();
        const onMap =
          !bounds ||
          bounds.contains([position.coords.longitude, position.coords.latitude]);
        if (onMap) {
          control.trigger();
        } else {
          showOffMapBanner();
        }
      },
      () => {
        // Denial / position-unavailable — stay silent (map doesn't move),
        // matching the original locate-pill behaviour.
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  });

  // Swallow the control's own geolocation errors silently — by the time we
  // call control.trigger() the fix is known-good and in bounds, so this only
  // covers a mid-flight failure; the map simply doesn't move.
  control.on('error', () => {
    // Intentionally empty — no user-facing noise on a failed locate.
  });

  // SNOW-324: answer the field-report flow's location requests. report.js
  // dispatches ``snowdesk:locate-request`` and consumes the result on
  // ``snowdesk:geolocate`` / ``snowdesk:geolocate-error`` — so map.js remains
  // the single owner of geolocation and report.js never calls the API itself.
  //
  // This deliberately does NOT go through ``control.trigger()``: the report
  // only needs coordinates, not a camera move, and MapLibre's GeolocateControl
  // throws ("Unexpected watchState undefined") when a fix lands outside the
  // map's maxBounds with trackUserLocation:false — i.e. for every location
  // outside the Alps box (a tester in the UK, say). A plain getCurrentPosition
  // is robust wherever the user is; out-of-region points then resolve to a
  // null region server-side (the "we couldn't match your location" path).
  document.addEventListener('snowdesk:locate-request', () => {
    if (!navigator.geolocation) {
      document.dispatchEvent(
        new CustomEvent('snowdesk:geolocate-error', { detail: { code: 2 } })
      );
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        document.dispatchEvent(
          new CustomEvent('snowdesk:geolocate', {
            detail: {
              lat: position.coords.latitude,
              lon: position.coords.longitude,
              accuracy: position.coords.accuracy,
            },
          })
        );
      },
      (err) => {
        document.dispatchEvent(
          new CustomEvent('snowdesk:geolocate-error', {
            detail: { code: err && err.code },
          })
        );
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  });
})();

// SNOW-314: Season ribbon — the season scrubber's track doubles as the danger
// ribbon. When a region is the focus, one decorative coloured cell per season
// day is painted into the track (behind the thumb); a persistent readout names
// that region and shows its danger for the scrubbed day. Both the track fill
// and the readout are driven from the in-memory season-ratings cache (same
// payload as the scrubber / timelapse — no extra fetch), so they update live
// during manual scrub AND playback, decoupling region identity from the
// ephemeral map popup. The homepage pre-selects CH-4115; /map/ starts grey
// (no focus) until the user taps a region.
(function seasonRibbonInit() {
  // #season-ribbon is retained as the data carrier (season bounds + default
  // region) and the caption header; the visible cells live in the scrubber
  // track's .scrubber-ribbon fill, the label in #region-readout.
  const ribbonEl = document.getElementById('season-ribbon');
  if (!ribbonEl) return;

  const fill = document.querySelector('.season-scrubber .scrubber-ribbon');
  const trackEl = document.querySelector(
    '.season-scrubber .season-scrubber-track'
  );
  if (!fill || !trackEl) return;

  const readoutEl = document.getElementById('region-readout');
  // The scrubbed date now lives in its own ribbon beside the scrubber
  // (bottom-left), not in the top region chip — see updateReadout below.
  const dateRibbonEl = document.getElementById('map-date-ribbon');
  const readoutSwatch =
    readoutEl && readoutEl.querySelector('.region-readout-swatch');
  const readoutCrumbs =
    readoutEl && readoutEl.querySelector('.region-readout-crumbs');
  const readoutLeaf =
    readoutEl && readoutEl.querySelector('.region-readout-leaf');
  // The "view bulletin" action is now a separate roundel (sibling of the
  // readout pill), not the pill itself; it carries the bulletin href.
  const readoutAction = document.getElementById('region-readout-action');

  // Convert the int rating from the ratings cache to a key string.
  // SNOW-496: thin delegator — see scrubber_core.js's module header.
  const intToKey = (n) => {
    if (window.pwaScrubberCore) return window.pwaScrubberCore.intToKey(n, INT_TO_RATING);
    if (n == null || n < 0 || n >= INT_TO_RATING.length) return 'no_rating';
    return INT_TO_RATING[n];
  };

  // Focus state. Empty region => no focus (grey track, hidden readout, no
  // map highlight).
  let cache = null;
  let regionId = ribbonEl.dataset.defaultRegionId || null;
  let regionName = ribbonEl.dataset.defaultRegionName || null;
  let regionSlug = ribbonEl.dataset.defaultRegionSlug || null;
  let dateKey = ribbonEl.dataset.defaultDate || null;
  // SNOW-314 prototype: L2 (sub) + L1 (major) names for the breadcrumb. Seeded
  // from data attributes for the pre-selected region, then overwritten on every
  // region-selected event (which carries the full hierarchy).
  let regionSubName = ribbonEl.dataset.defaultSubregionName || '';
  let regionMajorName = ribbonEl.dataset.defaultMajorName || '';
  // Which region tiers are visible on the map (l1=Major, l2=Minor); the chip
  // breadcrumb mirrors these. Seeded from the persisted overlay state, updated
  // on snowdesk:overlays-changed. The leaf is the region name; it remains in
  // the breadcrumb regardless of the L4 map-layer visibility because it is a
  // text readout, not the polygon layer.
  const overlayVisible = {
    l1: readBoolStorage('snowdesk.map.overlay.l1', false),
    l2: readBoolStorage('snowdesk.map.overlay.l2', false),
  };

  // Paint one decorative cell per CALENDAR DAY across [seasonStart, seasonEnd]
  // for the focused region into the scrubber track. One cell per day (not per
  // data-date) is essential: the scrubber thumb maps date→position linearly
  // over the same range, so a cell-per-day grid lines up with the thumb. A
  // cell-per-data-date grid would compress gap days and drift every cell off
  // its true date — making the colour under the thumb mismatch the map. Gap
  // days (and any days past the last data point) render as no_rating slivers.
  // Cells are pointer-events:none — the track beneath keeps its drag/click; the
  // thumb marks the active day. With no focus or no region data → grey rail.
  const DAY_MS = 86400000;
  const paintTrack = () => {
    if (!cache || !regionId) {
      fill.replaceChildren();
      trackEl.removeAttribute('data-ribbon');
      return;
    }
    const startMs = Date.parse(ribbonEl.dataset.seasonStart);
    const endMs = Date.parse(ribbonEl.dataset.seasonEnd);
    if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs < startMs) {
      fill.replaceChildren();
      trackEl.removeAttribute('data-ribbon');
      return;
    }
    // UTC-midnight stepping (seasonStart parses as UTC midnight); UTC has no
    // DST, so adding exact days and slicing toISOString gives correct ISO keys.
    let regionHasData = false;
    const cells = [];
    for (let t = startMs; t <= endMs; t += DAY_MS) {
      const d = new Date(t).toISOString().slice(0, 10);
      const ratingInt = cache[d] ? cache[d][regionId] : null;
      if (ratingInt != null) regionHasData = true;
      const cell = document.createElement('span');
      cell.className = 'scrubber-ribbon-cell ribbon-cell--' + intToKey(ratingInt);
      // Month-boundary hairline: mark the first-of-month cells (skip the very
      // first cell so there's no leading mark). A pure CSS overlay — see
      // .scrubber-ribbon-cell--month — so it adds no width and the cells stay
      // aligned with the scrubber thumb.
      if (d.slice(8, 10) === '01' && t !== startMs) {
        cell.classList.add('scrubber-ribbon-cell--month');
      }
      cells.push(cell);
    }
    if (!regionHasData) {
      fill.replaceChildren();
      trackEl.removeAttribute('data-ribbon');
      return;
    }
    fill.replaceChildren(...cells);
    trackEl.setAttribute('data-ribbon', 'on');
  };

  // Persist the focused region's border highlight on the map, independent of
  // the popup (which is destroyed during playback). Driven from the
  // module-scope MAP + FEATURE_BY_REGION_ID. Re-asserted on every date change
  // so playback's popup-clear can't strip the selection outline.
  let highlightedFeatureId = null;
  const setHighlight = () => {
    if (!MAP) return;
    const feature = regionId ? FEATURE_BY_REGION_ID[regionId] : null;
    const fid = feature ? feature.id : null;
    if (highlightedFeatureId != null && highlightedFeatureId !== fid) {
      MAP.setFeatureState(
        { source: 'regions', id: highlightedFeatureId }, { selected: false },
      );
    }
    if (fid != null) {
      MAP.setFeatureState({ source: 'regions', id: fid }, { selected: true });
    }
    highlightedFeatureId = fid;
    if (MAP.triggerRepaint) MAP.triggerRepaint();
  };

  // Update the two split readouts. The bottom #map-date-ribbon always shows
  // the scrubbed date (the timeline's own readout, region or no region); the
  // top #region-readout chip names the focused region and shows its danger
  // swatch, and is hidden until a region is focused. Pure in-memory lookup
  // (no fetch), so it is safe to call on every scrub/preview/playback frame.
  const updateReadout = () => {
    // Bottom date ribbon — day-first, title-case date ("18 May 2026") matching
    // the popup card; deliberately not the uppercase scrubber format.
    if (dateRibbonEl) {
      dateRibbonEl.hidden = !dateKey;
      if (dateKey) dateRibbonEl.textContent = formatDatePopup(dateKey);
    }
    if (!readoutEl) return;
    const hasRegion = !!(dateKey && regionId && regionName);
    readoutEl.hidden = !hasRegion;
    readoutEl.classList.toggle('has-region', hasRegion);
    if (hasRegion) {
      // Breadcrumb: Major (L1) › Minor (L2) › Micro (L4, the leaf), including
      // only the tiers currently visible on the map. The leaf is always shown.
      if (readoutLeaf) readoutLeaf.textContent = regionName;
      if (readoutCrumbs) {
        const crumbs = [];
        if (overlayVisible.l1 && regionMajorName) crumbs.push(regionMajorName);
        if (overlayVisible.l2 && regionSubName) crumbs.push(regionSubName);
        readoutCrumbs.textContent = crumbs.length ? crumbs.join(' › ') + ' › ' : '';
      }
      const ratingInt = cache && cache[dateKey] ? cache[dateKey][regionId] : null;
      const key = intToKey(ratingInt);
      // The swatch is its own element (a colour block that divides date from
      // name), not a pseudo-element on the date.
      if (readoutSwatch) {
        readoutSwatch.style.background =
          key === 'no_rating' ? 'transparent' : 'var(--color-eaws-' + key.replace(/_/g, '-') + ')';
      }
      // Point the action roundel at the bulletin: /<region_id>/<slug>/<date>/.
      // Region id is lowercased to match the canonical URL form. The roundel is
      // shown via CSS (#region-readout.has-region ~ .region-readout-action).
      if (readoutAction) {
        if (regionSlug) {
          readoutAction.setAttribute(
            'href',
            '/' + regionId.toLowerCase() + '/' + regionSlug + '/' + dateKey + '/',
          );
        } else {
          readoutAction.removeAttribute('href');
        }
      }
    } else {
      // Clear the leaf/crumbs text (not the wrapper's textContent, which would
      // detach the cached child spans).
      if (readoutLeaf) readoutLeaf.textContent = '';
      if (readoutCrumbs) readoutCrumbs.textContent = '';
      if (readoutSwatch) readoutSwatch.style.background = 'transparent';
      if (readoutAction) readoutAction.removeAttribute('href');
    }
  };

  const refresh = () => {
    paintTrack();
    updateReadout();
    setHighlight();
  };

  // Tap a region → it becomes the focus.
  document.addEventListener('snowdesk:region-selected', (e) => {
    regionId = (e.detail && e.detail.region_id) || null;
    regionName = (e.detail && e.detail.region_name) || null;
    regionSlug = (e.detail && e.detail.region_slug) || null;
    regionSubName = (e.detail && e.detail.subregion_name) || '';
    regionMajorName = (e.detail && e.detail.major_name) || '';
    refresh();
  });

  // SNOW-314 prototype: a Major/Minor overlay toggle changes which breadcrumb
  // tiers are shown. Re-render the readout in place (the region is unchanged).
  document.addEventListener('snowdesk:overlays-changed', (e) => {
    const key = e.detail && e.detail.key;
    if (key === 'l1' || key === 'l2') {
      overlayVisible[key] = !!(e.detail && e.detail.visible);
      updateReadout();
    }
  });

  // Scrubber commit / live drag preview → update the readout's date, and
  // re-assert the highlight (playback destroys the popup and its outline).
  document.addEventListener('snowdesk:date-changed', (e) => {
    dateKey = (e.detail && e.detail.date) || dateKey;
    updateReadout();
    setHighlight();
  });
  document.addEventListener('snowdesk:date-preview', (e) => {
    dateKey = (e.detail && e.detail.date) || dateKey;
    updateReadout();
  });

  // Apply the initial highlight once the region features are loaded (the
  // default homepage focus, or a deep-linked region).
  document.addEventListener('snowdesk:regions-loaded', setHighlight);

  // Paint the default focus (homepage) once the ratings cache resolves.
  getSeasonRatings().then((c) => {
    cache = c || null;
    refresh();
  });
})();
