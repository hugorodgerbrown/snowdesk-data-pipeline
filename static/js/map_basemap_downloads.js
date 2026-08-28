/*
 * static/js/map_basemap_downloads.js — offline basemap download machinery.
 *
 * SNOW-610: extracted verbatim from map.js, which had grown to 9,192 lines.
 * This is a move, not a redesign — every function below is unchanged from
 * the block that used to sit between the basemap-style helpers and the
 * shared-state channel.
 *
 * What lives here: everything that answers "what is on this device, how
 * big is it, and what has to go to make room" — the pinned Cache Storage
 * bucket helpers, the `basemap.regions` / `basemap.customAreas` records,
 * the byte-budget planner and its eviction path, the failure toasts, the
 * on-map progress grid, and the thin delegator to
 * `basemap_download_runner.js`.
 *
 * LOAD ORDER MATTERS. This is a classic script, so its top-level `let` /
 * `const` land in the global lexical scope — readable from map.js as bare
 * identifiers, but NOT as `window.X` (the asymmetry behind SNOW-610's M1).
 * It must load BEFORE map.js, whose main IIFE runs at parse time. Nothing
 * here reads another file's binding at load time: `window.pwaBasemapDownloads`
 * and `PINNED_DOWNLOAD_DEPS` are both objects of arrow values, so every
 * cross-file read (`MAP`, `COUNTRY_STATE`, `RATINGS_URL`) happens when the
 * user triggers a download, long after every file has run.
 */

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

// SNOW-645: the settings.BASEMAP_STYLES key of the basemap currently
// selected in the picker, read off the checked radio row that map.js:150-159
// sets on boot and map_basemap_picker.js:285-291 maintains on every change.
// Display-only — unlike activeBasemapTileTemplate above, which reads the
// *rendered* style and is what beforeWarm uses to decide eviction, this
// reads the *picker DOM*, which map_basemap_picker.js updates SYNCHRONOUSLY
// on click, before MapLibre's asynchronous setStyle() has actually loaded
// the new style. So for the moment between those two, this LEADS the
// render rather than lagging it: it already reports the newly-picked key
// while activeBasemapTileTemplate still resolves the outgoing style's
// template. A download triggered in that narrow window would therefore
// record the new key against tiles that were actually fetched from the
// OLD basemap — display-only is what keeps that mismatch harmless: nothing
// here feeds the eviction decision, which stays template-only. Returns
// null with no menu, or no checked row (nothing has resolved yet).
function activeBasemapKey() {
  const basemapMenu = document.getElementById('basemap-menu');
  if (!basemapMenu) return null;
  const checked = basemapMenu.querySelector(
    '.basemap-menu-item[data-basemap-key][aria-checked="true"]',
  );
  return (checked && checked.dataset.basemapKey) || null;
}

// SNOW-645: lazily built {key: label} map, read off the basemap picker's
// own rendered buttons rather than duplicating apps/public/views.py's
// _BASEMAP_LABELS in JS — that keeps every caller showing the SAME
// server-translated string the popover itself shows, with no new JS
// literal for tox -e i18n-lint to flag. Built once and cached: the
// picker's markup is static for the life of the page, it never re-renders.
// Shared module scope rather than a per-caller copy — the Manage downloads
// sheet (map_downloads_manager.js) and the region roundel's "downloaded
// under another basemap" state (map_region_download.js) both need it, and
// a second implementation would be the same lookup written twice.
let _basemapLabelsByKey = null;

/**
 * The picker's basemap label for `key`, or '' if the picker has no
 * matching row (an unrecognised key, or a picker-invisible one like
 * `swisstopo_light` — see `_BASEMAP_LABELS`'s own docstring).
 *
 * @param {string} key
 * @returns {string}
 */
function basemapLabel(key) {
  if (!_basemapLabelsByKey) {
    _basemapLabelsByKey = {};
    const menu = document.getElementById('basemap-menu');
    if (menu) {
      // Iterates every [data-basemap-key] row and compares dataset.basemapKey
      // — never interpolates a stored key into a CSS selector, which would
      // be an injection risk if a key ever contained selector syntax.
      menu.querySelectorAll('[data-basemap-key]').forEach((btn) => {
        const btnKey = btn.dataset.basemapKey;
        if (btnKey) _basemapLabelsByKey[btnKey] = self.pwaStrings.collapse(btn.textContent);
      });
    }
  }
  return _basemapLabelsByKey[key] || '';
}

// Hex floor for basemapIdentityColour below — the SAME green
// --color-sync-ok resolves to in light mode (src/css/main.css @theme).
// MapLibre paint values can't reference a CSS custom property at all, so
// every consumer of an identity colour (the download progress grid, the
// downloaded-areas overlay) has to read the live value off the document
// instead — this is the floor for the pathological case where even THAT
// comes back empty (no stylesheet loaded at all).
const DOWNLOAD_PROGRESS_COLOUR_FALLBACK = '#16a34a';

// SNOW-645: resolve `key` (a settings.BASEMAP_STYLES key, or null/unknown)
// to its identity colour, in the same three tiers as the CSS
// var(--color-basemap-…, var(--color-sync-ok)) fallback the swatch and
// roundel rules use (src/css/main.css, static/css/map.css) — kept in step
// with that fallback deliberately, since the failure mode (a stale
// output.css build with the token undefined) is identical here:
//   1. --color-basemap-<key, underscores to dashes> off the document root
//      — the exact token those CSS rules read, so every surface (roundel,
//      sheet swatch, progress grid, downloaded-areas overlay) agrees.
//   2. --color-sync-ok — no key (unresolved picker), an unrecognised key,
//      or a stale build where tier 1's token isn't defined.
//   3. DOWNLOAD_PROGRESS_COLOUR_FALLBACK — tier 2 itself came back empty.
//
// @param {string | null} key
// @returns {string} A CSS colour value, never empty.
function basemapIdentityColour(key) {
  const root = getComputedStyle(document.documentElement);
  if (key) {
    const value = root.getPropertyValue(`--color-basemap-${key.replace(/_/g, '-')}`).trim();
    if (value) return value;
  }
  return root.getPropertyValue('--color-sync-ok').trim() || DOWNLOAD_PROGRESS_COLOUR_FALLBACK;
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
// own glyph-range logic for marginal benefit.
//
// SNOW-742: that reasoning still holds — this function still does not
// enumerate glyph ranges, and `activeBasemapGlyphPrefix` below does not
// either. But the conclusion it drew, that ordinary browsing therefore
// leaves the area covered, was wrong. Browsing caches those ranges into
// BASEMAP_CACHE, which is FIFO-trimmed to 600 entries, while pinned
// download buckets are never trimmed — so within a couple of sessions the
// glyphs get evicted and the area decays into geometry with no labels,
// its tiles still perfectly intact. The fix is not to enumerate but to
// PROMOTE: sw.js's `_promoteGlyphs` copies the already-cached entries into
// the pinned bucket at the end of a download, using the prefix below.
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

/**
 * SNOW-742: the URL prefix every glyph request for `map`'s current style
 * shares — its `glyphs` template truncated at the first placeholder.
 *
 * A style's `glyphs` looks like
 * `https://tiles.example/fonts/{fontstack}/{range}.pbf`, so everything before
 * `{fontstack}` is a prefix that matches every glyph URL for that style and
 * nothing else. That is all `_promoteGlyphs` (sw.js) needs: it is selecting
 * already-cached entries out of BASEMAP_CACHE, not constructing URLs, so it
 * never has to know which fontstacks or ranges exist.
 *
 * Returns '' when the style has no `glyphs` (the offline fallback style), or
 * when the template has no placeholder at all — a prefix of the whole string
 * would match only an exact URL, and a prefix of '' would match EVERY entry
 * in the passive cache and promote the lot. Both cases mean "promote
 * nothing", which is what the empty string tells the worker.
 *
 * @param {object|null} map
 * @returns {string}
 */
function activeBasemapGlyphPrefix(map) {
  if (!map) return '';
  const style = map.getStyle && map.getStyle();
  const glyphs = style && style.glyphs;
  if (typeof glyphs !== 'string') return '';
  const brace = glyphs.indexOf('{');
  if (brace <= 0) return '';
  return glyphs.slice(0, brace);
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
 *   band: number[], centre_tile: Object, template: string,
 *   basemapKey?: string|null, bytes: number, savedAt: string}} area
 * @returns {Promise<void>}
 */
async function _appendCustomArea(area) {
  const existing = await _readCustomAreas();
  const next = existing.filter((entry) => !entry || entry.id !== area.id);
  next.push(area);
  await _writeCustomAreas(next);
  // SNOW-749: and record the DEFINITION against the account, from the same
  // moment the device record is written so the two cannot disagree about
  // what was downloaded. Enqueued through the mutation queue, so an area
  // framed and downloaded with no signal is recorded when the device
  // surfaces; a no-op for a signed-out visitor. Never awaited for its outcome and never able to fail the
  // download: the tiles are what the user asked for, and they are already
  // on disk by the time this runs.
  //
  // The name is the STORED one only. An unrenamed area's "Custom area N"
  // default is filled in at read time from `ordinal` (see
  // `basemapDownloadedAreas`), in the language active then — persisting it
  // to the account would freeze one device's language into a row every
  // other device reads.
  await window.pwaDownloadsSync?.push({
    areaId: area.id,
    bbox: area.bbox,
    basemapKey: area.basemapKey,
    name: area.name || '',
  });
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
  const ok = await _writeCustomAreas(next);
  // SNOW-749: carry the new name to the account row too, so the area reads
  // the same on every device. Only after the local write landed — the
  // sheet renders from the local record, so a name that did not stick here
  // must not be claimed anywhere else. A no-op with the flag off or the
  // visitor signed out.
  if (ok) await window.pwaDownloadsSync?.rename(areaId, name);
  return ok;
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
// @returns {Promise<Array<{id: string, name?: string, bytes: number,
//   savedAt: string, basemapKey: string|null}>>} `basemapKey` (SNOW-645)
//   is the basemap the area was fetched under, null for a record written
//   before that ticket or for a reconciled orphan — "downloaded, basemap
//   unknown". SNOW-722: named here because it is load-bearing outside the
//   eviction path now (map_layer_sync_status.js decides each basemap
//   row's dot on it), and the abbreviated shape above read as though it
//   were dropped.
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
        // SNOW-645: absent on a record written before this ticket shipped
        // — reads as "downloaded, basemap unknown" rather than a wrong one.
        basemapKey: entry.basemapKey || null,
        // SNOW-749: the region id, carried so `reconcileAreas` can compare
        // and `downloads_sync.js` can describe this area to the account
        // without re-parsing it back out of the bucket id — that format
        // belongs to `areaIdForRegion` and is deliberately never
        // reverse-engineered elsewhere.
        regionId: entry.region_id,
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
        // SNOW-645: see the region branch above for the "unknown" fallback.
        basemapKey: entry.basemapKey || null,
        // SNOW-749: a custom area IS its box — it is the only thing that
        // lets another device (or this one after an eviction) fetch the
        // same ground again, so it travels with the area.
        bbox: entry.bbox,
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
  // SNOW-749: and union in the areas on the ACCOUNT, in this same one
  // normalising layer — exactly where SNOW-612's orphans already join,
  // and for the same reason: a second reader somewhere else would be free
  // to disagree with this one about what exists.
  //
  // `accountAreas()` resolves `[]` for an anonymous visitor, a flag-off
  // page, an offline device and any failure alike, so this never waits on
  // a network it cannot reach and never turns a read of local storage
  // into a rejection. With `[]` the reconciliation output is what it was
  // before this ticket, which is the path every existing caller takes.
  const accountAreas = window.pwaDownloadsSync
    ? await window.pwaDownloadsSync.accountAreas()
    : [];
  return manage.reconcileAreas(areas, storedIds, bytesById, accountAreas);
}

/**
 * SNOW-645: every DISTINCT tile template currently downloaded, paired with
 * the basemap it was fetched under — the input `refreshDownloadedOverlay`
 * (static/js/map.js) needs to paint every basemap's downloads at once,
 * each in its own identity colour, rather than only the active basemap's.
 *
 * Deliberately NOT `basemapDownloadedAreas()` widened to carry `template` —
 * that reader is the canonical, lossy-by-design normaliser eviction
 * planning and the Manage downloads sheet share (see its own docstring),
 * and neither of those callers has any use for a tile template. This reads
 * `basemap.regions` and `basemap.customAreas` directly instead, which is
 * the same pair of records `basemapDownloadedAreas()` reads — this
 * function is a sibling of it, not a wrapper around it.
 *
 * A template can appear on more than one recorded area (several regions,
 * or a region and a custom area, downloaded under the same basemap) — this
 * dedupes by template, since `cachedTilesFromURLs` is run once per
 * template regardless of how many areas share it. If two records
 * disagree about the template's `basemapKey` (only possible with a
 * pre-SNOW-645 keyless record alongside a keyed one for the same
 * template), the non-empty key wins — an unresolved basemap should never
 * shadow a known one.
 *
 * A record with NO `template` (written before SNOW-632) falls back to the
 * ACTIVE basemap's template rather than being skipped. Skipping it was the
 * bug behind "the roundel says Downloaded but the map draws no squares":
 * `_probeDone` (map_region_download.js) reads a missing template as "the
 * active basemap's", so the roundel resolves `done` off real cached tiles,
 * while this function — the overlay's only source of templates — dropped
 * the record entirely and the overlay had nothing to scan for. The two
 * surfaces disagreed about a download that was genuinely on disk.
 *
 * The fallback cannot invent coverage: the caller still runs
 * `cachedTilesFromURLs` against real Cache Storage contents, so a record
 * whose tiles were fetched under some OTHER basemap simply matches nothing
 * and contributes no squares — the same empty answer as before, just
 * reached by looking rather than by skipping.
 *
 * @returns {Promise<Array<{template: string, basemapKey: string}>>}
 *   `basemapKey` is always a string, `''` for unknown — never `null` —
 *   so a caller can use it as a MapLibre `match` arm directly. Empty when
 *   nothing is recorded, or the reads fail — best-effort, matching
 *   `basemapDownloadedAreas()`'s own degrade-to-nothing behaviour.
 */
async function basemapDownloadedTemplates() {
  const byTemplate = new Map();
  // Resolved once, not per record: it reads the live style, and every
  // templateless record falls back to the same answer. Null (no style
  // settled yet) leaves those records skipped exactly as before.
  const activeTemplate = activeBasemapTileTemplate(MAP);
  const activeKey = activeBasemapKey();
  const record = (template, basemapKey) => {
    const resolved = template || activeTemplate;
    if (!resolved) return;
    // A templateless record borrows the active basemap's KEY too — its own
    // is equally absent, and the pair has to stay consistent or the
    // overlay would colour the active basemap's tiles as "unknown" green.
    const key = (template ? basemapKey : basemapKey || activeKey) || '';
    const existing = byTemplate.get(resolved);
    if (existing === undefined || (!existing && key)) {
      byTemplate.set(resolved, key);
    }
  };

  if (window.pwaDb) {
    try {
      const row = await window.pwaDb.get('meta:app', 'basemap.regions');
      const regions = Array.isArray(row && row.value) ? row.value : [];
      for (const entry of regions) {
        if (entry) record(entry.template, entry.basemapKey);
      }
    } catch (_e) {
      // Best-effort — see docstring.
    }
  }

  try {
    const customAreas = await _readCustomAreas();
    for (const entry of customAreas) {
      if (entry) record(entry.template, entry.basemapKey);
    }
  } catch (_e) {
    // Best-effort — see docstring.
  }

  return Array.from(byTemplate, ([template, basemapKey]) => ({ template, basemapKey }));
}

/**
 * SNOW-645 review: infer which basemap an ORPHANED bucket (SNOW-612 — a
 * pinned bucket with no record, so `reconcileAreas` gives it
 * `basemapKey: null`) most likely belongs to, for the "Manage downloads"
 * row's pale left-edge rule — DECORATION ONLY. This is inference from
 * what is actually on disk, never a record: nothing writes it back
 * anywhere, and no other reader may ever treat the result as anything
 * more than a colour hint for a row whose only action is Remove.
 *
 * Matches the orphan's own cached tile URLs against every DISTINCT
 * template `basemapDownloadedTemplates()` already knows about — the SAME
 * per-template regex match `cachedTilesFromURLs` performs for the
 * downloaded-tiles overlay, just against one bucket's URLs instead of the
 * union of all of them. No new URL-parsing or origin-sniffing: a bucket
 * whose tiles match a template on record was fetched from that
 * template's basemap.
 *
 * @param {string} areaId The orphan's own area id (its bucket name is
 *   `BASEMAP_PINNED_CACHE_PREFIX + areaId`).
 * @returns {Promise<string|null>} The first matching basemapKey, or
 *   `null` when the bucket's tiles match no template currently on record
 *   (a basemap since retired from the picker, an unreadable bucket, or
 *   Cache Storage itself unavailable) — the caller's cue to fall back to
 *   a pale NEUTRAL rule rather than guessing a basemap.
 */
async function orphanBasemapKey(areaId) {
  const core = self.pwaBasemapDownloadCore;
  if (!core || !('caches' in window)) return null;

  let urls;
  try {
    const cache = await caches.open(BASEMAP_PINNED_CACHE_PREFIX + areaId);
    const requests = await cache.keys();
    urls = requests.map((request) => request.url);
  } catch (_e) {
    return null;
  }
  if (!urls.length) return null;

  const templates = await basemapDownloadedTemplates();
  for (const { template, basemapKey } of templates) {
    if (!basemapKey) continue;
    if (core.cachedTilesFromURLs(template, urls).length > 0) return basemapKey;
  }
  return null;
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
  // SNOW-749: the budget is what THIS DEVICE is holding, so an area that
  // exists only on the account is not a candidate for eviction — it costs
  // nothing here, and "evicting" it would name an area the user cannot
  // see in a confirm banner and free no bytes at all.
  const onDevice = areas.filter((area) => area.onDevice !== false);
  const plan = core.planEviction(
    onDevice,
    { id: areaId, bytes: incomingBytes },
    budgetBytes,
  );
  const areasById = new Map(onDevice.map((a) => [a.id, a]));
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
   * Every recorded area, normalised to
   * `{id, name, bytes, savedAt, basemapKey}` and keyed by the id that also
   * names its pinned Cache Storage bucket.
   *
   * SNOW-722: `basemapKey` was omitted from that shape above while the
   * only consumers were the eviction planner and the Manage downloads
   * sheet, and it read as though the key were normalised away — it never
   * was. It is now what map_layer_sync_status.js matches each basemap
   * row's `data-basemap-key` against to decide whether that basemap has
   * real downloaded coverage, so the omission is no longer harmless.
   * Null means "downloaded, basemap unknown" (a pre-SNOW-645 record, or a
   * reconciled orphan) — never a wrong basemap.
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

  /**
   * SNOW-645: the picker's translated label for a basemap key — see
   * `basemapLabel`'s own docstring above. Exposed here (rather than left
   * as a bare identifier) because this bridge is specifically for modules
   * OUTSIDE the map bundle's load-order contract, which map_downloads_manager.js
   * is: unlike map_region_download.js (inside the bundle, so it calls
   * `basemapLabel` bare), it cannot assume this script has already run.
   *
   * @param {string} key
   * @returns {string}
   */
  basemapLabel: (key) => basemapLabel(key),

  /**
   * SNOW-645 review: infer an orphaned bucket's basemap from its own
   * cached tiles — see `orphanBasemapKey`'s own docstring for the
   * matching and the "decoration only" caveat. Exposed here for the same
   * load-order reason `basemapLabel` is.
   *
   * @param {string} areaId
   * @returns {Promise<string|null>}
   */
  orphanBasemapKey: (areaId) => orphanBasemapKey(areaId),

  // SNOW-649: the two render-scheduling primitives below are exposed for
  // ONE reason — they were untestable. Both are pure higher-order
  // functions with no DOM or MapLibre dependency of their own, yet the
  // only coverage they had was a Playwright test watching a roundel
  // settle, because a module-scope `function` inside the map bundle
  // cannot be reached from tests/js. Neither is called from another
  // module; if that changes, the caller belongs in this file instead.
  //
  // Function declarations hoist, so referencing them from this block —
  // which evaluates well before their definitions further down — is safe.

  /**
   * Wrap an async, idempotent render so overlapping calls coalesce.
   *
   * @param {function(): Promise<void>} render
   * @returns {function(): Promise<void>}
   */
  coalesceRenders: (render) => coalesceRenders(render),

  /**
   * Build a "re-run `render` once MapLibre next goes idle" callback.
   *
   * @param {function(): void} render
   * @returns {function(): void}
   */
  makeStyleSettleRetry: (render) => makeStyleSettleRetry(render),
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
  // SNOW-645 (Hugo's explicit ask, overriding the plan's own non-goal): the
  // grid now fills in the ACTIVE basemap's identity colour rather than the
  // generic green, so it speaks the same visual language as the roundel it
  // completes into rather than seaming into a different colour the instant
  // the pulse fades and the roundel takes over.
  const colour = basemapIdentityColour(activeBasemapKey());
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
  basemapKey: () => activeBasemapKey(),
  planBudget: (areaId, mb) => planBasemapDownloadBudget(areaId, mb),
  confirmEviction: (areas) => confirmBasemapEviction(areas),
  evict: (areaIds) => evictBasemapAreas(areaIds),
  feedUrls: () => assembleBasemapDownloadFeedURLs(),
  glyphPrefix: () => activeBasemapGlyphPrefix(MAP),
  progressGrid: (plan, offset) => createDownloadProgressGrid(plan, offset),
  warmCache: (urls, opts) =>
    typeof window.pwaWarmCache === 'function' ? window.pwaWarmCache(urls, opts) : null,
  // SNOW-748: the effective state, not the interface's. The runner uses this
  // to repaint the roundel after a declined eviction, and painting 'idle'
  // there would offer a retry the worker would refuse under a user-forced
  // offline mode. ``window.pwaConnectivity`` is pwa_offline.js's read of the
  // same value its ``snowdesk:connectivity-changed`` broadcast carries;
  // ``navigator.onLine`` is the fallback for a page where that module has not
  // run.
  isOnline: () =>
    window.pwaConnectivity ? window.pwaConnectivity.isOnline() : navigator.onLine !== false,
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
