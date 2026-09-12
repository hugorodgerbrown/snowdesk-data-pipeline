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
 * SNOW-860 moved the READER out — `basemapDownloadedAreas`,
 * `pinnedBucketAreaIds` and the two `meta:app` record reads behind them
 * are `static/js/basemap_downloaded_areas.js` now, and the functions of
 * those names left here delegate to it. Everything that WRITES a record,
 * plans an eviction, runs a download or paints on the map stayed. The
 * reason is that /account/settings/ has to state what "Reset local data"
 * is about to delete, and it must read the same list the Manage downloads
 * sheet does — which it cannot do from this file, whose bare `MAP` /
 * `COUNTRY_STATE` / `RATINGS_URL` reads only resolve on the map page.
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

// SNOW-521: resolve the active basemap's vector-tile URLs — the same
// lookup `computeBasemapTileURLs` used to do before per-region download
// replaced viewport tile enumeration. Reads the *resolved* tile URLs off
// each vector source's runtime instance (`map.getSource(id).tiles`) rather
// than the static style JSON — a TileJSON-backed source only populates
// `tiles` once its tilejson fetch resolves. Returns null for a style with
// no vector sources (the offline fallback style, SNOW-483) or before the
// style has finished loading. `mapDownloadControlInit` substitutes these
// into a region's stored tile-index ranges
// (`pwaBasemapDownloadCore.rangesToTileURLs`) rather than enumerating
// anything itself.
//
// SNOW-843: EVERY vector source, and every URL of each — not the first
// source's first URL, which is what this returned for its whole life. Both
// halves of that were wrong against a real multi-source style, and the
// swisstopo winter style is one:
//
//   - it declares TWO vector sources (`ch.swisstopo.relief.vt` and
//     `ch.swisstopo.base.vt`), and stopping at the first meant a download
//     pinned the relief and never fetched a single base tile — an offline
//     map of hillshade with no roads, labels or features;
//   - each source lists FIVE hosts (`vectortiles0-4.geo.admin.ch`) that
//     MapLibre round-robins between per tile, so four in five of the tiles
//     that WERE pinned sat under a URL the map would never ask for.
//
// The shape is `pwaBasemapDownloadCore.tileSources`' — one array of URL
// templates per source, in style order — and every consumer of it goes
// through that module rather than indexing in here.
function activeBasemapTileSources(map) {
  if (!map || !map.isStyleLoaded()) return null;
  const style = map.getStyle();
  if (!style || !style.sources) return null;
  const sources = [];
  for (const sourceId of Object.keys(style.sources)) {
    if (style.sources[sourceId].type !== 'vector') continue;
    const runtime = map.getSource(sourceId);
    if (!runtime || !Array.isArray(runtime.tiles)) continue;
    const urls = runtime.tiles.filter((url) => typeof url === 'string' && url);
    if (urls.length) sources.push(urls);
  }
  return sources.length ? sources : null;
}

// SNOW-645: the settings.BASEMAP_STYLES key of the basemap currently
// selected in the picker, read off the checked radio row that map.js:150-159
// sets on boot and map_basemap_picker.js:285-291 maintains on every change.
// Display-only — unlike activeBasemapTileSources above, which reads the
// *rendered* style and is what beforeWarm uses to decide eviction, this
// reads the *picker DOM*, which map_basemap_picker.js updates SYNCHRONOUSLY
// on click, before MapLibre's asynchronous setStyle() has actually loaded
// the new style. So for the moment between those two, this LEADS the
// render rather than lagging it: it already reports the newly-picked key
// while activeBasemapTileSources still resolves the outgoing style's
// template. A download triggered in that narrow window would therefore
// record the new key against tiles that were actually fetched from the
// OLD basemap — display-only is what keeps that mismatch harmless: nothing
// here feeds the eviction decision, which stays template-only. SNOW-868's
// per-basemap zoom band is template-only for the same reason (see
// `resolveBaseLayerPlan`'s `bandKey`); anything else that comes to depend
// on which basemap is on screen has to make that choice consciously.
// Returns null with no menu, or no checked row (nothing has resolved yet).
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

/**
 * Every basemap key the picker offers, in the order it offers them
 * (SNOW-832).
 *
 * The same source as `basemapLabel` above, and for the same reason:
 * `apps/public/views.py`'s `_BASEMAP_LABELS` is the single definition of
 * both the label AND the order (see `basemap_options`' own docstring —
 * the picker's display order is curated there, not in
 * `settings.BASEMAP_STYLES`), and duplicating the order into JS would be
 * a second list to keep in step with no test able to notice it drifting.
 *
 * The Manage downloads sheet is the caller: it groups its rows by basemap
 * (`basemap_manage_core.js`'s `groupRowsByBasemap`) and lists the groups
 * in the order the picker lists the basemaps, so the panel and the picker
 * read the same way round.
 *
 * NOT cached, unlike `basemapLabel`'s lookup: this is one DOM walk per
 * sheet open rather than one per row, and the cache is what would have to
 * be invalidated if the picker ever became dynamic.
 *
 * @returns {string[]} Empty with no picker in the document — the caller
 *   then falls back to first-appearance order, which is an order, just not
 *   the curated one.
 */
function basemapOrder() {
  const menu = document.getElementById('basemap-menu');
  if (!menu) return [];
  const keys = [];
  // Same iterate-and-compare shape as `basemapLabel`: never interpolates a
  // stored key into a selector.
  menu.querySelectorAll('[data-basemap-key]').forEach((btn) => {
    const key = btn.dataset.basemapKey;
    if (key && keys.indexOf(key) === -1) keys.push(key);
  });
  return keys;
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

/**
 * SNOW-843: every TileJSON document the live style's vector sources are
 * declared by — `style.sources[id].url`, absolute.
 *
 * A vector source can name its tiles in two ways: inline (`tiles: [...]`)
 * or by pointing at a TileJSON document (`url: "…/tiles.json"`) that
 * carries the array. swisstopo uses the second form for both its sources,
 * and that document is a HARD dependency of rendering: with it uncached,
 * MapLibre offline cannot learn a single tile URL, so the pinned tiles are
 * unreachable and the basemap is blank however complete the download was.
 *
 * Browsing does cache it — it is served from the style's own (allowlisted)
 * origin, so `_basemapStaleWhileRevalidate` writes it into `BASEMAP_CACHE`
 * — but that cache is FIFO-trimmed to `BASEMAP_CACHE_MAX_ENTRIES` while
 * pinned buckets never are. This is the glyph decay SNOW-742 fixed, one
 * document further up: a couple of browsing sessions evict it and the
 * downloaded area quietly stops rendering. So a download pins it outright
 * rather than relying on the passive copy surviving.
 *
 * @param {object|null} map
 * @returns {string[]} Empty for a style still settling, or one whose
 *   sources all declare their tiles inline (nothing to fetch).
 */
function activeBasemapSourceDocumentURLs(map) {
  if (!map || !map.isStyleLoaded()) return [];
  const style = map.getStyle && map.getStyle();
  if (!style || !style.sources) return [];
  const urls = [];
  for (const sourceId of Object.keys(style.sources)) {
    const source = style.sources[sourceId];
    if (!source || source.type !== 'vector') continue;
    if (typeof source.url !== 'string' || !source.url) continue;
    try {
      urls.push(new URL(source.url, window.location.href).toString());
    } catch (_err) {
      // A mapbox:// or otherwise unresolvable reference is not something
      // this can fetch; MapLibre resolving it is a case this project has
      // never had, and inventing a URL for it would pin a 404.
    }
  }
  return urls;
}

/**
 * SNOW-844: every RENDER dependency of the live style — the documents a
 * pinned area needs in its bucket to draw at all, beyond its tiles.
 *
 * Three things, and they are the three a download already fetches: the
 * active basemap's style document, its sprite JSON+PNG at 1x and 2x
 * (`computeBasemapSpriteURLs`), and the TileJSON each vector source is
 * declared by (`activeBasemapSourceDocumentURLs` — SNOW-843).
 *
 * It exists because those three pushes used to live inside
 * `assembleBasemapDownloadFeedURLs` alone, where only the DOWNLOAD could
 * see them. SNOW-844's probe has to check the same list, and a second copy
 * of it is precisely the drift SNOW-843 was: what the download fetched and
 * what the done-probe checked disagreed, and every surface reported the
 * download's answer. So this is the one definition, and
 * `assembleBasemapDownloadFeedURLs` below now calls it rather than
 * repeating it.
 *
 * SNOW-847: glyph ranges ARE now part of this list, reversing SNOW-844's
 * exclusion. That exclusion was correct for as long as glyphs arrived by
 * PROMOTION out of the passive cache (`sw.js`'s `_promoteGlyphs`): a
 * promoted set is whatever the user's browsing happened to have cached, so
 * checking it reported a failure no repair could ever clear. Now the
 * download FETCHES a fixed set (`glyphURLs`), the same list is checkable by
 * value on both sides, and a missing range is a real, repairable gap —
 * which is the condition SNOW-844's own exclusion note named as what would
 * have to change first.
 *
 * @param {object|null} map The live MapLibre map. Reading it is exactly
 *   why this composer stays here rather than moving into
 *   `basemap_download_core.js`, which is a dependency-free pure IIFE by
 *   contract (its own header).
 * @returns {string[]} Possibly empty — a style still settling yields no
 *   source documents, and a basemap picker that has not resolved yields no
 *   style URL. An empty list is the UNKNOWN case, never "nothing is
 *   missing"; every caller reads it that way.
 */
function activeBasemapRenderDependencyURLs(map) {
  const urls = [];
  const activeBasemap = document.querySelector(
    '#basemap-menu .basemap-menu-item[data-basemap-url][aria-checked="true"]',
  );
  if (activeBasemap && activeBasemap.dataset.basemapUrl) {
    urls.push(activeBasemap.dataset.basemapUrl);
  }
  urls.push(...computeBasemapSpriteURLs(map));
  urls.push(...activeBasemapSourceDocumentURLs(map));
  urls.push(...activeBasemapGlyphURLs(map));
  return urls;
}

/**
 * SNOW-847: every glyph URL the active style's labels can need, from the
 * fixed range set `basemap_download_core.js` documents.
 *
 * Here rather than in the core module for the same reason
 * `activeBasemapRenderDependencyURLs` is: it reads the live map. The
 * enumeration itself is pure and lives in the core, where
 * `tests/js/test_basemap_download_core.js` can reach it.
 *
 * @param {object|null} map The live MapLibre map.
 * @returns {string[]} Empty for a map whose style has not settled, or a
 *   style declaring no `glyphs` template — both read as UNKNOWN by every
 *   caller, never as "no glyphs needed".
 */
function activeBasemapGlyphURLs(map) {
  const core = self.pwaBasemapDownloadCore;
  if (!core || !map || typeof map.getStyle !== 'function') return [];
  return core.glyphURLs(map.getStyle());
}

/**
 * SNOW-692: the slope-angle raster URLs covering one download blob.
 *
 * The template comes from `#map`'s `data-slope-tile-url`, which the view
 * renders only while `settings.SLOPE_TILE_URL` is configured — so an
 * environment with the overlay switched off pins nothing here and the rest
 * of the download is unaffected. The rectangle and the zoom ceiling come
 * from `slope_overlay_core.js`, the one definition the live overlay's own
 * source is built from, so the download cannot request ground or zooms the
 * map itself would refuse to ask for.
 *
 * @param {Object|null} blob The download blob, for its `z` row spans.
 * @returns {string[]} Empty when the overlay is not configured, its core
 *   module has not loaded, or the blob carries no ranges.
 */
function activeSlopeTileURLs(blob) {
  const core = self.pwaBasemapDownloadCore;
  const slope = self.pwaSlopeOverlayCore;
  const mapEl = document.getElementById('map');
  const template = mapEl ? mapEl.dataset.slopeTileUrl : '';
  if (!core || !slope || !template) return [];
  return core.slopeTileURLs(template, blob, slope.COVERAGE_BOUNDS, slope.MAX_ZOOM);
}

/**
 * SNOW-692: the slope tiles ONE recorded area should hold, derived from
 * its record rather than read out of it.
 *
 * The slope set is a pure function of ground the record already describes
 * plus three page-level constants (the template, the raster's rectangle,
 * its zoom ceiling), so recording ~273 URLs per area would store nothing
 * the probe cannot recompute — 27.4 KB per area, measured on a
 * CH-4115-shaped region. Deriving keeps the record the size it was.
 *
 * The two area kinds describe their ground differently, which is the only
 * reason this needs to know which it is holding:
 *
 *   - a REGION record carries the run's own `z` row spans, the same shape
 *     `blobFullyCached` is handed at the tile-probe call sites;
 *   - a CUSTOM area carries `bbox` + `band` and no `z` at all, because its
 *     tile set was never server-computed — `buildBlob` is the client-side
 *     twin that produced it in the first place, so it reproduces it here.
 *
 * The consequence of deriving rather than recording, stated because it is
 * a real behavioural difference: the check asks what TODAY'S code would
 * fetch, not what that run did. If the raster's rectangle or its template
 * ever changes, every existing area re-reads against the new set at once.
 * For a constant quoted from the service's own capabilities document that
 * is the wanted behaviour — the areas really would be missing tiles — but
 * it is not the same promise `deps` makes.
 *
 * @param {Object|null} record A `basemap.regions` or `basemap.customAreas`
 *   entry.
 * @returns {string[]} Empty when the overlay is unconfigured, or the
 *   record describes no ground this can rebuild — never a partial list.
 */
function areaSlopeTileUrls(record) {
  const core = self.pwaBasemapDownloadCore;
  if (!core || !record) return [];
  if (record.z) return activeSlopeTileURLs({ z: record.z });
  const band = Array.isArray(record.band) ? record.band : null;
  if (!record.bbox || !band) return [];
  return activeSlopeTileURLs(core.buildBlob(record.bbox, band[0], band[1]));
}

/**
 * SNOW-844: which render-dependency list to check ONE recorded area
 * against — the three-row resolution rule, in one place because three
 * surfaces apply it (both download controls and the Manage downloads
 * sheet) and a fourth would otherwise invent a fourth answer.
 *
 *   record's `deps` present   → the record's own list, whatever basemap
 *                               the row belongs to. It names what that
 *                               run actually fetched.
 *   absent, basemap IS active → derive it live from the loaded style.
 *   absent, basemap NOT active → NONE. Skip the check.
 *
 * The third row is the one that matters. A record written before this
 * ticket names no dependencies, and a style that is not loaded cannot be
 * asked what its sprite is — so for such an area we genuinely cannot
 * answer, and reporting `incomplete` would be the same class of lie as
 * today's false `done`, just pointing the other way. It resolves itself
 * the moment the user switches to that basemap (the roundel then probes
 * live and heals the record) or repairs.
 *
 * @param {string[] | null | undefined} recordedDeps The area's stored
 *   `deps`, as written by its own download run.
 * @param {boolean} basemapIsActive Whether the area's basemap is the one
 *   currently on screen — the only condition under which the live style
 *   can stand in for a record that names nothing.
 * @returns {string[]} Possibly empty, and an empty list means UNKNOWN:
 *   `pwaBasemapDownloadCore.missingRenderDependencies` answers `[]` for
 *   it, which every caller reads as "no claim", never as "complete".
 */
function areaRenderDependencyURLs(recordedDeps, basemapIsActive) {
  if (Array.isArray(recordedDeps) && recordedDeps.length > 0) return recordedDeps;
  if (basemapIsActive) return activeBasemapRenderDependencyURLs(MAP);
  return [];
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
  // SNOW-844: the style document, the sprite and each vector source's
  // TileJSON, from the ONE definition the probe also reads — see
  // `activeBasemapRenderDependencyURLs` for why a second copy of these
  // three pushes here is the exact drift this ticket removes.
  urls.push(...activeBasemapRenderDependencyURLs(MAP));
  return urls;
}

/* -------------------------------------------------------------------- *
 * SNOW-924: the content half of a download — what sits inside the area.
 * -------------------------------------------------------------------- */

/**
 * The four overlay feeds, fetched whole and written to the offline cache.
 *
 * WHOLE and unfiltered, deliberately. Each is a single small request that
 * already covers everything, so narrowing one to the area would save
 * almost no bytes and would cost a per-area storage model that overlapping
 * areas make ambiguous. The boundary decides what must VERIFY present, not
 * what gets stored.
 *
 * Not routed through `map.js`'s `ensureOverlayLoaded`, which is the other
 * writer of these same rows, and the difference is the point: that
 * function's job is to INSTALL MapLibre layers, with caching as a
 * write-through side effect. Here the bytes are the whole purpose and the
 * layers are not wanted — a download must not turn overlays on. Same
 * store, same principal stamping, different reason to be writing.
 *
 * The eligibility gates are `map.js`'s, re-read rather than shared,
 * because they are page state (`#map`'s dataset) rather than module state.
 *
 * @returns {Promise<Object|null>} The weather GeoJSON, which the caller
 *   needs in order to resolve the area's detail sheets, or ``null`` when
 *   it could not be fetched. Every other feed's outcome is deliberately
 *   invisible: a failed favourites fetch must not fail a download.
 */
async function cacheOverlayFeedsForDownload() {
  const mapEl = document.getElementById('map');
  const cache = window.pwaMapOverlayCache;
  if (!mapEl || !cache) return null;

  const feeds = [
    ['weather', mapEl.dataset.weatherUrl],
    [
      'community_reports',
      mapEl.dataset.communityReportsEligible === 'true'
        ? mapEl.dataset.communityReportsUrl
        : null,
    ],
    [
      'favourites',
      mapEl.dataset.favouritesEligible === 'true' ? mapEl.dataset.favouritesUrl : null,
    ],
    ['routes', mapEl.dataset.routesEligible === 'true' ? mapEl.dataset.routesUrl : null],
  ];

  let weather = null;
  await Promise.all(
    feeds.map(async ([resource, url]) => {
      if (!url) return;
      try {
        const data = await fetch(url).then((r) => (r.ok ? r.json() : null));
        if (!data) return;
        await cache.putOverlay(resource, data);
        if (resource === 'weather') weather = data;
      } catch (_e) {
        // Best-effort, one feed at a time. The tiles are what the user
        // asked for and they are still worth having.
      }
    }),
  );
  return weather;
}

/**
 * The days an area's bulletins are taken for.
 *
 * Reads the same forward bound the scrubber and calendar answer to
 * (`pwaCalendarCore.latestKnownDate`, SNOW-927) rather than hardcoding
 * today, so the evening issue — the bulletin someone packing at 5pm
 * actually needs offline — is picked up the moment it is published,
 * without this function knowing that is what happened.
 *
 * The day on screen is included when it is in the past, because a visitor
 * who scrubbed back and then downloaded meant that day.
 *
 * @returns {Promise<string[]>} Date keys, oldest first. Empty only when
 *   the page carries no readable `data-today`.
 */
async function downloadContentDays() {
  const today = readTodayDateParam();
  if (!today) return [];

  let ceiling = today;
  try {
    const core = window.pwaCalendarCore;
    if (core && typeof core.latestKnownDate === 'function') {
      ceiling = core.latestKnownDate(await getSeasonRatings(), today) || today;
    }
  } catch (_e) {
    // No payload, no ceiling — today alone, which is what this did before
    // SNOW-927 existed.
  }

  const days = [];
  const startMs = Date.parse(today);
  const endMs = Date.parse(ceiling);
  if (!Number.isFinite(startMs)) return [];
  for (let ms = startMs; Number.isFinite(endMs) && ms <= endMs; ms += 86400000) {
    days.push(new Date(ms).toISOString().slice(0, 10));
  }
  if (days.length === 0) days.push(today);

  const onScreen = readUrlDateParam();
  if (onScreen && !days.includes(onScreen)) days.unshift(onScreen);
  return days;
}

/**
 * Every content URL an area's boundary implies.
 *
 * The `contentUrls` dep, and the second half of a download run. Caches the
 * feeds first because the weather sheets are derived from the feed it
 * fetches; then asks `areaContentPlan` which regions and which locations
 * the area's rectangle contains.
 *
 * `featureByRegionId` is the map's own loaded `regions.geojson`, so this
 * costs no request — and an empty one (a country not yet loaded) simply
 * yields no bulletins for that country rather than a wrong answer.
 *
 * @param {Object} blob The run's download blob, for its tile ranges.
 * @returns {Promise<string[]>} Possibly empty, which every caller reads as
 *   "nothing to add" rather than as a failure.
 */
async function assembleAreaContentURLs(blob) {
  const core = self.pwaBasemapDownloadCore;
  const mapEl = document.getElementById('map');
  if (!core || !core.areaContentPlan || !mapEl) return [];

  const weather = await cacheOverlayFeedsForDownload();

  const bbox = core.areaBBox(blob);
  if (!bbox) return [];

  const state = window.snowdeskMapState;
  const byRegion = (state && state.featureByRegionId) || {};
  const plan = core.areaContentPlan({
    bbox,
    regionFeatures: Object.keys(byRegion).map((key) => byRegion[key]),
    weatherFeatures: (weather && weather.features) || [],
    days: await downloadContentDays(),
    weatherDetailTemplate: mapEl.dataset.weatherDetailUrl || '',
  });
  return [...plan.bulletinUrls, ...plan.weatherDetailUrls];
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

// SNOW-860: `pinnedBucketAreaIds` moved to
// `static/js/basemap_downloaded_areas.js` with the reader that was its only
// caller. Reach it as `window.pwaBasemapAreas.pinnedBucketAreaIds()`.

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

// SNOW-860: the `meta:app` keys for the custom-area and base-layer
// records, and the reads over them, moved to
// `static/js/basemap_downloaded_areas.js` — the page-agnostic reader both
// the map page and /account/settings/ now share. The WRITERS stayed here,
// because they belong to the download runs this file drives; they name
// their rows through `window.pwaBasemapAreas.CUSTOM_AREAS_KEY` /
// `.BASE_LAYERS_KEY` so there is still exactly one definition of each key.

/**
 * SNOW-856: the `basemap.baseLayers` record, via the extracted reader
 * (SNOW-860). Kept as a local name because the base-layer writers below
 * read-modify-write through it.
 *
 * Optional-chained rather than assumed: this file's own contract is that
 * a failed read is "no base layer recorded", so a page that somehow
 * loaded it without the reader degrades the same way a thrown IndexedDB
 * error does rather than taking the download path down.
 *
 * @returns {Promise<Array<Object>>}
 */
async function _readBaseLayers() {
  return (await window.pwaBasemapAreas?.readBaseLayers()) || [];
}

/**
 * The bbox every vector source in the active style declares coverage for
 * (SNOW-856) — the UNION of their TileJSON `bounds`.
 *
 * Union rather than intersection, and rather than per-source boxes: the
 * base layer is enumerated once for all sources (one blob, one call to
 * `rangesToTileURLs`), so the choice is which way to err. A union asks a
 * narrower source for a few tiles outside its coverage, which answers 404
 * and which `warmOne` already classifies as `'other'` and carries on past.
 * An intersection would instead leave a HOLE in the wider source, which
 * nothing detects and which shows up as a blank patch offline. Wasting a
 * handful of requests beats silently not downloading ground.
 *
 * A source that declares no `bounds` is claiming all of them, so it
 * collapses the union to null and leaves the camera as the only bound —
 * which is exactly right for the global default basemap.
 *
 * @param {Object} map The live MapLibre instance.
 * @returns {number[]|null} `[minLon, minLat, maxLon, maxLat]`, or null for
 *   "unbounded, or nothing to read yet".
 */
function activeBasemapSourceBounds(map) {
  if (!map || !map.isStyleLoaded()) return null;
  const style = map.getStyle();
  if (!style || !style.sources) return null;
  let union = null;
  for (const sourceId of Object.keys(style.sources)) {
    if (style.sources[sourceId].type !== 'vector') continue;
    const runtime = map.getSource(sourceId);
    const bounds = runtime && runtime.bounds;
    // One unbounded source makes the whole union unbounded.
    if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isFinite)) {
      return null;
    }
    union = union
      ? [
          Math.min(union[0], bounds[0]),
          Math.min(union[1], bounds[1]),
          Math.max(union[2], bounds[2]),
          Math.max(union[3], bounds[3]),
        ]
      : bounds.slice();
  }
  return union;
}

/**
 * The map's own camera extent as a bbox (SNOW-856).
 *
 * Read off the LIVE map rather than from `map.js`'s `MAX_BOUNDS`
 * constant, which is scoped inside that file's IIFE and is in any case
 * loaded AFTER this module. Reading it back through `getMaxBounds()` means
 * there is exactly one definition of where this map can go, and the base
 * layer cannot drift out of agreement with it.
 *
 * @param {Object} map The live MapLibre instance.
 * @returns {number[]|null} `[minLon, minLat, maxLon, maxLat]`, or null on
 *   a map with no max bounds set.
 */
function mapCameraBBox(map) {
  try {
    const bounds = map && typeof map.getMaxBounds === 'function' ? map.getMaxBounds() : null;
    if (!bounds) return null;
    const [[west, south], [east, north]] = bounds.toArray();
    const bbox = [west, south, east, north];
    return bbox.every(Number.isFinite) ? bbox : null;
  } catch (_e) {
    return null;
  }
}

/**
 * The active basemap's base-layer top-up plan (SNOW-856).
 *
 * Resolves the bucket to write into and — the important half — only the
 * urls NOT already cached. That is what makes the base layer a one-off
 * cost rather than a tax on every download: the second area downloaded
 * under a basemap finds an empty list here and the runner returns
 * immediately.
 *
 * Missing-only is also the repair path. A run interrupted halfway leaves a
 * partial bucket, and the next download completes it with no separate
 * machinery, no `incomplete` state and nothing for the user to do.
 *
 * The cached set is the union across EVERY pinned bucket, not just the
 * base layer's own. A z10 tile inside a downloaded region is genuinely
 * available offline whichever bucket holds it, and re-fetching it into a
 * second bucket would spend bytes to store a duplicate.
 *
 * SNOW-929: `urls` is the missing TILES plus the missing DOCUMENTS that
 * draw them, and `deps` states the document list whole so `recordBaseLayer`
 * can store what this bucket is owed rather than what one top-up happened
 * to fetch.
 *
 * @returns {Promise<{areaId: string, basemapKey: string|null,
 *   bandKey: string, bbox: number[], urls: string[], deps: string[]}
 *   |null>} `null` when the style has not settled, when there is no
 *   basemap key to file it under, or when the style's coverage does not
 *   meet the map's own extent. `basemapKey` is the picker's (bucket
 *   identity); `bandKey` is the rendered style's (which band was fetched)
 *   — see the inline comment for why those are two values and not one.
 */

async function resolveBaseLayerPlan() {
  const core = self.pwaBasemapDownloadCore;
  if (!core || !core.baseLayerTileURLs) return null;
  const tileSources = activeBasemapTileSources(MAP);
  const basemapKey = activeBasemapKey();
  // Keyless would mean a bucket nothing can later match to a basemap, so
  // it could never be evicted with the areas that share it.
  if (!tileSources || !basemapKey) return null;
  const cameraBBox = mapCameraBBox(MAP);
  if (!cameraBBox) return null;
  // Read ONCE and reused for both calls below: they must agree, and a
  // second read is a second chance for the style to have changed between
  // the extent this plan reports and the urls it hands over.
  const sourceBounds = activeBasemapSourceBounds(MAP);
  const bbox = core.baseLayerBBox(cameraBBox, sourceBounds);
  if (!bbox) return null;
  // SNOW-868: the band is the BASEMAP's, not one global constant — a
  // national style closes the seam to z9 for 4.4 MB, which the global
  // default cannot do for 121. Threading a key here is also what makes
  // the migration below per-basemap, with no second lookup to keep in
  // step.
  //
  // TWO keys, deliberately, and they must not be tidied into one:
  //
  //   - `bandKey` — which BAND to fetch — is derived from the tile
  //     TEMPLATES of the style that is actually rendered.
  //     `activeBasemapKey()` cannot be used for it: that reads the picker
  //     DOM, which `map_basemap_picker.js` updates synchronously on
  //     click, while `activeBasemapTileSources` above still returns the
  //     OUTGOING style's templates until MapLibre's asynchronous
  //     `setStyle()` has landed. `activeBasemapKey`'s own comment calls
  //     that mismatch harmless precisely because it is display-only and
  //     feeds no decision — selecting the band with it would end that. In
  //     the race window an OpenFreeMap style would be asked for a
  //     national z0-9 band, which is the 121 MB this ticket exists to
  //     avoid, or a national style asked for z0-7.
  //   - `basemapKey` — the BUCKET IDENTITY — stays the picker's. It names
  //     the areaId, the `basemap.baseLayers` entry filed under it, and
  //     the eviction that reads both. `basemapKeyForTileSources` matches
  //     on the template's HOST, and `swisstopo_winter` and
  //     `swisstopo_light` share hosts, so deriving identity from it would
  //     collapse two buckets into one — a behaviour change nobody asked
  //     for.
  //
  // The band is safe on the ambiguous key for exactly the reason identity
  // is not: those two swisstopo styles share a band ([0, 9]) AND a
  // bytes-per-tile figure (96 KB), so the host's inability to tell them
  // apart cannot change the answer. Give them different bands and this
  // stops being true.
  const bandKey = core.basemapKeyForTileSources(tileSources);
  const all = core.baseLayerTileURLs(tileSources, cameraBBox, sourceBounds, bandKey);
  const areaId = core.areaIdForBaseLayer(basemapKey);
  // SNOW-863: a bucket holding anything the CURRENT band does not ask for
  // is from an older one, and is dropped whole before planning.
  //
  // Needed because SNOW-856 shipped z0-9 and the default band is z0-7.
  // Without it, every device that ever ran the old band keeps its z8 and
  // z9 tiles for good: they are a superset, so the missing-url plan below
  // is empty, nothing ever re-warms, and 121 MB sits there on the default
  // basemap with no path out short of a full reset.
  //
  // Detected from the bucket's own contents rather than the record's
  // stored `band`, deliberately — the record can be absent (see
  // `basemapDownloadedAreas`' note on why), and a migration that only
  // fires for devices with an intact record would miss exactly the ones
  // in the worst state. A superset is also the only shape this can be in:
  // the url set is a pure function of band, camera and style.
  //
  // SNOW-868 made the band per basemap and needed nothing added here.
  // Two things follow, and both are the reason:
  //
  //   - `all` is already this basemap's own url set, because `bandKey` is
  //     threaded into `baseLayerTileURLs` above. The comparison is
  //     therefore per-basemap by construction, not by a second lookup
  //     that could drift out of step with the first.
  //   - The national bands WIDEN (z0-7 -> z0-9), so a national bucket
  //     filled under the old band is a strict SUBSET of what the new one
  //     asks for. Subsets are not stale: nothing is evicted, nothing
  //     already held is re-fetched, and the ordinary missing-url plan
  //     below tops the bucket up with the two new levels. Only
  //     OpenFreeMap's old z0-9 buckets are supersets, and dropping those
  //     is exactly what this path already existed to do.
  //
  // SNOW-929 lifted the bucket read out of the old `_baseLayerBucketIsStale`
  // predicate and into this one call, because the plan needs the bucket's
  // OWN contents twice over — once to judge staleness, and once to decide
  // which documents are missing from it (see below). Two reads of the same
  // bucket is two chances for them to disagree.
  const bucketEntries = await _baseLayerBucketURLs(areaId);
  const stale = core.baseLayerStaleEntries(bucketEntries, all).length > 0;
  if (stale) {
    await evictBasemapAreas([areaId]);
    await _forgetBaseLayerRecord(basemapKey);
  }
  // Emptied when the bucket was just dropped: nothing in it is on disk
  // any more, so every document below has to be planned again.
  const bucket = stale ? new Set() : new Set(bucketEntries);
  // SNOW-929: the documents that DRAW the band, planned beside it — the
  // same list, from the same function, that an area download has pinned
  // since SNOW-843/847 (style JSON, each vector source's TileJSON, the
  // sprite JSON+PNG, the glyph ranges).
  //
  // Without them the device holds a band it cannot render. Measured on a
  // cold origin: every base-layer bucket held `.pbf` entries and nothing
  // else, while the four documents sat in the unpinned
  // `snowdesk-basemap-v1` passive cache, which is FIFO-trimmed and
  // evictable — and on a FIRST visit the style and sprite were not cached
  // at all, because MapLibre requests them before the worker is in
  // control. The base layer was the one download path still asking the
  // tiles-only question, and it is the one download nobody chooses: every
  // user gets it just by being shown a basemap.
  //
  // Read once, like `sourceBounds` above, and for the same reason.
  const deps = activeBasemapRenderDependencyURLs(MAP);
  const cached = stale ? new Set() : await pinnedBasemapCacheURLs();
  // The two halves are deduped against DIFFERENT sets, and that asymmetry
  // is the point rather than an oversight (reported on #902 by review,
  // where the first cut of this used the union for both).
  //
  //   - TILES against the union of every pinned bucket. A tile is
  //     genuinely available offline whichever bucket holds it — `sw.js`'s
  //     `_searchPinnedBuckets` walks them all — so re-fetching one to
  //     store a second copy spends megabytes to store a duplicate. That
  //     trade is the one this function has always made; it is unchanged.
  //   - DOCUMENTS against this bucket's OWN contents. The same reasoning
  //     inverts, because the documents are the same URLs for every area
  //     sharing the basemap: a device holding one region download already
  //     has them in THAT bucket, so a union check reads them as cached
  //     and copies nothing here — and deleting that region then takes the
  //     base layer's only render dependencies with it. That is exactly
  //     the blank-map defect this ticket exists to fix, reached by
  //     another route, and it would be invisible: `recordBaseLayer`
  //     declares the full list, so `areaState` would read the bucket as
  //     `incomplete` while a base row carries no Repair control and is
  //     filtered out of the manage panel. At 0.7–1.5 MB the second copy
  //     is worth having; the whole promise of this bucket is that it
  //     renders on its own and outlives any one area.
  const urls = all
    .filter((url) => !cached.has(url))
    .concat(deps.filter((url) => !bucket.has(url)));
  window.pwaDebugLog?.record('cache', 'baselayer.plan', {
    basemapKey: basemapKey,
    // Logged beside it because the two disagreeing is the race above, and
    // a trace that showed only one of them could not tell you it happened.
    bandKey: bandKey,
    bbox: bbox,
    total: all.length,
    // SNOW-929: how many documents the plan carries, so a trace can tell
    // "the band was already complete" from "the band was complete and its
    // style was missing" — which is the state this ticket found devices in.
    deps: deps.length,
    missing: urls.length,
    rebanded: stale,
  });
  return { areaId: areaId, basemapKey, bandKey, bbox, urls, deps };
}

/**
 * Everything in `areaId`'s OWN pinned bucket (SNOW-929).
 *
 * This was `_baseLayerBucketIsStale(areaId, expected)` until SNOW-929 —
 * the same Cache Storage read with the staleness verdict folded into it.
 * `resolveBaseLayerPlan`, its only caller, now needs the entries
 * themselves rather than a boolean: it judges staleness through
 * `core.baseLayerStaleEntries` (pure and truth-tabled — SNOW-863's
 * re-banding migration, narrowed to tile entries) AND asks which of the
 * band's DOCUMENTS this bucket is missing. A second read for the second
 * question would be a second chance for the two to disagree, so the read
 * moved out here and the judgements moved up there.
 *
 * Deliberately this bucket alone, NOT `pinnedBasemapCacheURLs`' union
 * across every pinned bucket. The two are the right answer to two
 * different questions and `resolveBaseLayerPlan` uses both — see its own
 * comment for why a document has to be in THIS bucket while a tile may
 * be in any.
 *
 * Best-effort: a bucket that cannot be read reads as EMPTY. Every caller
 * has to be safe on that, and both are — an empty set is "not stale", so
 * a transient Cache Storage failure can never destroy a good download,
 * and it is "plan every document", which at worst re-fetches ~1 MB the
 * bucket already had.
 *
 * @param {string} areaId
 * @returns {Promise<string[]>} The bucket's entry urls, in Cache
 *   Storage's own order. `[]` for an absent, empty or unreadable bucket.
 */
async function _baseLayerBucketURLs(areaId) {
  if (!('caches' in window)) return [];
  try {
    const core = self.pwaBasemapDownloadCore;
    const cache = await caches.open(core.pinnedCacheName(areaId));
    const requests = await cache.keys();
    return requests.map((request) => request.url);
  } catch (_e) {
    return [];
  }
}

/**
 * Drop one basemap's `basemap.baseLayers` entry (SNOW-863).
 *
 * Paired with the eviction above so a re-banded bucket does not leave its
 * old byte total behind on the budget. Best-effort — a stale record with
 * no bucket produces no row (see `basemapDownloadedAreas`), so failing
 * here costs nothing the reader sees.
 *
 * @param {string} basemapKey
 * @returns {Promise<void>}
 */
async function _forgetBaseLayerRecord(basemapKey) {
  if (!window.pwaDb) return;
  try {
    const existing = await _readBaseLayers();
    await window.pwaDb.put('meta:app', {
      key: window.pwaBasemapAreas.BASE_LAYERS_KEY,
      value: existing.filter((entry) => entry && entry.basemapKey !== basemapKey),
    });
  } catch (_e) {
    // Best-effort — see docstring.
  }
}

/**
 * Record what a base-layer top-up fetched (SNOW-856).
 *
 * `bytes` ACCUMULATES across top-ups rather than replacing, because a
 * top-up only ever fetches what was missing: a run that completes a
 * half-warmed layer reports only its own half, and overwriting would halve
 * the recorded size of a bucket that just got bigger. The same reasoning
 * `planEviction` applies to a re-downloaded area does not hold here — that
 * is a replacement, this is an addition.
 *
 * Best-effort: a base layer whose record fails to write still SERVES (the
 * bucket is on disk and `_searchPinnedBuckets` finds it). All that is lost
 * is its line in the budget, and the next top-up writes one.
 *
 * @param {Object|null} result The warm run's report.
 * @param {{areaId: string, basemapKey: string|null, bbox: number[],
 *   deps?: string[]}} plan
 * @returns {Promise<void>}
 */
/**
 * Fetch the shown basemap's z0-7 overview, if this device has not got it
 * (SNOW-867).
 *
 * The wide half is the app's own map data, and it is small — measured
 * across the four basemaps on 2026-09-07: OpenFreeMap 12.6 MB (56 tiles,
 * world bounds so nothing clips it), IGN 4.0 MB, basemap.at 3.2 MB,
 * Swisstopo 2.7 MB (25 tiles, two sources each). All four on one device is
 * about 22 MB.
 *
 * At that size it should not wait for a download. It used to arrive as a
 * top-up after the first area was downloaded, which meant a user who
 * simply switched basemap and went offline had no zoomed-out map at all,
 * and a user who did download one paid for their area and the overview in
 * the same wait. Fetching it when the basemap is first SHOWN separates the
 * two: the map you are looking at is complete on its own, and a download
 * is only ever the area you asked for plus its own deep zooms.
 *
 * Online only, and best-effort throughout: it is not the user's request,
 * so it must never surface an error, block anything, or spend a byte on a
 * connection they have told the app not to use. `resolveBaseLayerPlan`
 * hands back only the urls not already cached, so the common case — every
 * later switch back to a basemap — resolves an empty list and fetches
 * nothing.
 *
 * SNOW-929: what it fetches is the band AND the documents that draw it —
 * see `resolveBaseLayerPlan`. That adds roughly 0.7–1.5 MB to a band of
 * 2.7–12.6 MB, and it is the difference between holding a map and holding
 * tiles nothing can read.
 *
 * @returns {Promise<void>} Always resolves.
 */
// How long `warmBaseLayerWideBand` waits for the style before giving up
// on this attempt. Bounded because a style that never settles must not
// leave a promise standing for the life of the page; the next basemap
// change, or the next app open, tries again.
const WIDE_BAND_STYLE_SETTLE_MS = 10000;

// Basemap keys whose wide half this session has already warmed. The plan
// itself is the real check — it resolves only the urls NOT already cached
// — but resolving one walks EVERY pinned bucket's key list, several
// thousand entries on a device holding a few areas, and a user flipping
// through the picker triggers a walk per switch. Session-scoped rather
// than persisted: a reload should re-check the disk, not trust a memory
// of it.
const WARMED_WIDE_BANDS = new Set();

async function warmBaseLayerWideBand() {
  try {
    const connectivity = window.pwaConnectivity;
    const online = connectivity ? connectivity.isOnline() : navigator.onLine !== false;
    if (!online) return;
    if (typeof window.pwaWarmCache !== 'function') return;
    const activeKey = activeBasemapKey();
    if (activeKey && WARMED_WIDE_BANDS.has(activeKey)) return;
    let plan = await resolveBaseLayerPlan();
    if (!plan) {
      // No plan means the style has not settled: `activeBasemapTileSources`
      // is gated on `isStyleLoaded()`, which is false for the whole of the
      // boot sequence that resolves MAP_READY_PROMISE. Waiting one `idle`
      // is the difference between warming at boot and not warming until
      // the user next changes basemap — which, for someone who never
      // changes it, is never.
      await new Promise((resolve) => {
        if (!MAP || typeof MAP.once !== 'function') {
          resolve();
          return;
        }
        MAP.once('idle', resolve);
        setTimeout(resolve, WIDE_BAND_STYLE_SETTLE_MS);
      });
      plan = await resolveBaseLayerPlan();
    }
    if (!plan || !Array.isArray(plan.urls) || plan.urls.length === 0) {
      // Nothing missing — which is the answer the session cache above
      // exists to remember.
      if (plan && plan.basemapKey) WARMED_WIDE_BANDS.add(plan.basemapKey);
      return;
    }
    // SNOW-929: `glyphPrefix` as well, matching what an area download
    // passes (`basemap_download_runner.js`). The plan enumerates the fixed
    // `GLYPH_RANGES`, which is not every range a style's labels can reach;
    // the prefix additionally lets the worker PROMOTE ranges already in
    // the passive cache into this bucket (`sw.js`'s `_promoteGlyphs`), so
    // a band gets both the ranges we can name and the ones browsing found.
    const warming = window.pwaWarmCache(plan.urls, {
      pinned: true,
      areaId: plan.areaId,
      glyphPrefix: activeBasemapGlyphPrefix(MAP),
    });
    if (!warming) return;
    const result = await warming;
    await recordBaseLayer(result, plan);
    // Only a run that lost nothing is remembered: a partial warm has to be
    // retried on the next switch, which is how an interrupted first visit
    // completes itself.
    if (plan.basemapKey && result && !Number(result.failed)) {
      WARMED_WIDE_BANDS.add(plan.basemapKey);
    }
    // The layers menu claims a basemap is available offline; one more
    // filled bucket can change that answer.
    window.pwaLayerSyncStatus?.refresh();
  } catch (_err) {
    // Best-effort by design — the next switch to this basemap retries
    // whatever this attempt left missing.
  }
}

async function recordBaseLayer(result, plan) {
  const core = self.pwaBasemapDownloadCore;
  if (!core || !window.pwaDb || !plan || !plan.basemapKey) return;
  // A cancelled or wholly-failed top-up has nothing to record. A PARTIAL
  // one does: those bytes are on disk and the budget has to know.
  if (!result || !(Number(result.ok) > 0)) return;
  try {
    const existing = await _readBaseLayers();
    const previous = existing.find((entry) => entry && entry.basemapKey === plan.basemapKey);
    const next = existing.filter((entry) => entry && entry.basemapKey !== plan.basemapKey);
    next.push({
      basemapKey: plan.basemapKey,
      // SNOW-868: the band this basemap actually asked for, not the
      // default one. Writing the constant here recorded z0-7 against a
      // bucket holding z0-9, which is the read the re-banding check in
      // `resolveBaseLayerPlan` deliberately does not trust (it goes to
      // the bucket's own contents via `_baseLayerBucketURLs` instead) —
      // but a wrong number in a stored
      // record is a trap for the next reader either way.
      //
      // `plan.bandKey`, NOT `plan.basemapKey`: the record has to state the
      // band that was FETCHED, and the fetch was planned off the rendered
      // style's templates. Those two keys differ inside the basemap-switch
      // race `resolveBaseLayerPlan` documents, and recording the picker's
      // band there would write the number the run did not use.
      band: core.baseLayerBand(plan.bandKey),
      bbox: plan.bbox,
      // SNOW-929: the documents this bucket needs to RENDER the band —
      // recorded on the same terms as a region's or a custom area's, so
      // the offline report and the downloaded-areas reader can verify a
      // base layer by what it draws rather than asserting it draws with
      // nothing (which is what both of them used to say).
      //
      // The WHOLE list, not the subset this top-up fetched: the question
      // a reader asks later is "what does this bucket need?", and a
      // top-up that found the style already cached would otherwise record
      // a bucket that needs no style.
      deps: Array.isArray(plan.deps) ? plan.deps : [],
      bytes: (Number(previous && previous.bytes) || 0) + (Number(result.bytes) || 0),
      savedAt: new Date().toISOString(),
    });
    await window.pwaDb.put('meta:app', {
      key: window.pwaBasemapAreas.BASE_LAYERS_KEY,
      value: next,
    });
  } catch (err) {
    console.warn('base layer record write failed', err);
  }
  forgetPinnedBucketMeasurement(plan.areaId);
}

/**
 * SNOW-635: the `basemap.customAreas` record, via the extracted reader
 * (SNOW-860 — `static/js/basemap_downloaded_areas.js`), which also owns
 * the lazy migration from the legacy single-row `basemap.customArea`.
 * Kept as a local name because every custom-area writer below
 * read-modify-writes through it.
 *
 * Optional-chained for the same reason `_readBaseLayers` above is: a
 * missing reader reads as "nothing recorded", never as a throw on the
 * boot path.
 *
 * @returns {Promise<Array<Object>>}
 */
async function _readCustomAreas() {
  return (await window.pwaBasemapAreas?.readCustomAreas()) || [];
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
    await window.pwaDb.put('meta:app', {
      key: window.pwaBasemapAreas.CUSTOM_AREAS_KEY,
      value: areas,
    });
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

// SNOW-860: the reader itself now lives in
// `static/js/basemap_downloaded_areas.js`, page-agnostic, so
// /account/settings/'s "what will Reset local data delete" breakdown reads
// the SAME list the Manage downloads sheet does — the two surfaces cannot
// disagree about what is on this device, which is the whole reason the
// extraction happened rather than a second reader being written. This is
// the map page's binding for it, and it stays a bare identifier because
// half a dozen call sites in this file and in `map.js` read it as one.
//
// The two things this file still supplies:
//
//   - `MAP_STRINGS` (map_state.js), so a custom area's numbered default
//     name and the shared overview map's name arrive in the language the
//     map page rendered. The settings page passes its own panel's copies.
//   - `measurePinnedBucketBytes`, for an ORPHANED bucket with no record to
//     read a size off. It walks every entry of the bucket, and that is
//     work the map page absorbs and an account page should not do on load
//     — so it is injected here rather than moved.
//
// @returns {Promise<Array<{id: string, name?: string, bytes: number,
//   savedAt: string, basemapKey: string|null}>>}
async function basemapDownloadedAreas() {
  return window.pwaBasemapAreas.downloadedAreas({
    strings: MAP_STRINGS,
    measureBytes: measurePinnedBucketBytes,
  });
}

/**
 * SNOW-645: every DISTINCT set of tile sources currently downloaded, paired
 * with the basemap it was fetched under — the input `refreshDownloadedOverlay`
 * (static/js/map.js) needs to paint every basemap's downloads at once,
 * each in its own identity colour, rather than only the active basemap's.
 *
 * SNOW-843: a record's stored `template` field is no longer a template
 * string — it is the whole tile-source spec
 * (`pwaBasemapDownloadCore.tileSources`), because a basemap is one or more
 * vector sources each served from one or more hosts. The FIELD keeps its
 * name, since it is persisted in `meta:app` and a record written before
 * this ticket still holds a bare string there (which `tileSources`
 * normalises). What this function RETURNS is named for what it is.
 *
 * Deliberately NOT `basemapDownloadedAreas()` widened to carry `template` —
 * that reader is the canonical, lossy-by-design normaliser eviction
 * planning and the Manage downloads sheet share (see its own docstring),
 * and neither of those callers has any use for a tile template. This reads
 * `basemap.regions` and `basemap.customAreas` directly instead, which is
 * the same pair of records `basemapDownloadedAreas()` reads — this
 * function is a sibling of it, not a wrapper around it.
 *
 * One source spec can appear on more than one recorded area (several
 * regions, or a region and a custom area, downloaded under the same
 * basemap) — this dedupes on `tileSourcesKey`, since `cachedTilesFromURLs`
 * is run once per spec regardless of how many areas share it. If two
 * records disagree about that spec's `basemapKey` (only possible with a
 * pre-SNOW-645 keyless record alongside a keyed one for the same spec), the
 * non-empty key wins — an unresolved basemap should never shadow a known
 * one.
 *
 * A record with NO `template` (written before SNOW-632) falls back to the
 * ACTIVE basemap's sources rather than being skipped. Skipping it was the
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
 * @returns {Promise<Array<{tileSources: string[][], basemapKey: string}>>}
 *   `tileSources` is the normalised (SNOW-843) source spec, ready to hand
 *   straight to `cachedTilesFromURLs`. `basemapKey` is always a string,
 *   `''` for unknown — never `null` — so a caller can use it as a MapLibre
 *   `match` arm directly. Empty when nothing is recorded, or the reads fail
 *   — best-effort, matching `basemapDownloadedAreas()`'s own
 *   degrade-to-nothing behaviour.
 */
async function basemapDownloadedTemplates() {
  const core = self.pwaBasemapDownloadCore;
  if (!core) return [];
  // key -> {tileSources, basemapKey}
  const bySources = new Map();
  // Resolved once, not per record: it reads the live style, and every
  // templateless record falls back to the same answer. Null (no style
  // settled yet) leaves those records skipped exactly as before.
  const activeSources = activeBasemapTileSources(MAP);
  const activeKey = activeBasemapKey();
  const record = (stored, basemapKey) => {
    const resolved = core.tileSources(stored || activeSources);
    if (!resolved.length) return;
    // A record with no sources of its own borrows the active basemap's KEY
    // too — its own is equally absent, and the pair has to stay consistent
    // or the overlay would colour the active basemap's tiles as "unknown"
    // green.
    const key = (stored ? basemapKey : basemapKey || activeKey) || '';
    const mapKey = core.tileSourcesKey(resolved);
    const existing = bySources.get(mapKey);
    if (existing === undefined || (!existing.basemapKey && key)) {
      bySources.set(mapKey, { tileSources: resolved, basemapKey: key });
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

  return Array.from(bySources.values());
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

  // SNOW-856: a base layer outlives the area that fetched it, but not the
  // LAST one. Cascaded here rather than exposed as its own control,
  // because "delete the overview map" is not a thing the user should have
  // to think about — it appears when their first download does and leaves
  // with their last.
  // SNOW-867 removed an `evictOrphanedBaseLayers()` call here. The base
  // layer used to be deleted once no downloaded area was left to need it,
  // which followed from its being a cost the user's downloads incurred.
  // It is the app's own map data now — fetched when a basemap is SHOWN,
  // and the reason opening the app offline gets you a map at all — so
  // deleting the last area is no longer a reason to take it. It goes with
  // a full reset from account settings, which is where the app's own
  // storage is stated and cleared.
  //
  // What this does keep on disk is the near half (z8-9) of areas since
  // deleted: a few MB per part of the map, in the same bucket, with
  // nothing to prune it individually. Named here rather than left for a
  // reader to find.

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

/**
 * SNOW-871: delete NAMED urls — and, optionally, one PREFIX's worth of
 * unnameable ones — from one area's pinned bucket, leaving the bucket (and
 * every other entry in it) alone.
 *
 * `evictBasemapAreas` above is the whole-area instrument: bucket deleted,
 * record deleted, ring gone. This is the surgical one, and it exists for
 * exactly one caller — the region control replacing its own earlier copy
 * of an area under a different basemap. That bucket is keyed on the region
 * id alone, so both basemaps' tiles share it, and the copy being replaced
 * has to be taken out from UNDER the copy that just landed. `caches.delete`
 * of the whole bucket would take both.
 *
 * The area's record is deliberately untouched: the run that called this
 * has already rewritten it (`_recordRegionDownload`), and this only
 * removes the entries that record no longer describes.
 *
 * Sequential rather than `Promise.all` over the list: a replaced download
 * is hundreds to thousands of urls, and this runs AFTER the roundel has
 * settled green, so there is nothing to be gained by asking the browser
 * for a thousand concurrent Cache Storage transactions on a device that
 * has just finished a large download.
 *
 * Best-effort throughout — a bucket that cannot be opened, or one entry
 * that will not delete, must never surface as an error inside a completed
 * download's `finish`. The cost of a failure here is disk, not
 * correctness: the record already names the new basemap, so nothing reads
 * a leftover entry as available.
 *
 * GLYPHS are why the second argument is not the whole story. A download
 * does not fetch glyph PBFs — sw.js's `_promoteGlyphs` copies whatever the
 * passive cache already held under the style's glyph prefix into the
 * bucket (SNOW-742) — so the set that landed is partial, unpredictable and
 * named by nothing the device stores. `missingRenderDependencies` excludes
 * them for exactly that reason, which means the replaced record's `deps`
 * can never contain one and a url-list prune structurally cannot reach
 * them. Until SNOW-871 they went with the whole-bucket delete; a
 * PREFIX sweep is what replaces that, and it is the only place in this
 * function that reads the bucket rather than being told what to remove.
 *
 * @param {string} areaId The bucket, in `pwaBasemapDownloadCore`'s own
 *   `areaIdForRegion` form — never assembled by hand.
 * @param {string[]} urls Exactly the entries to remove.
 * @param {{prefix: string, spare?: string} | null} glyphs SNOW-871: also
 *   remove every entry starting with `prefix` — the replaced style's glyph
 *   prefix, as `activeBasemapGlyphPrefix` derives it and the record stores
 *   it. `spare` is the prefix the REPLACEMENT promotes under, and nothing
 *   beginning with it is ever deleted: two styles can legitimately be
 *   served from one glyph host, and the incoming copy's labels must
 *   survive the outgoing copy's prune. An empty or absent `prefix` sweeps
 *   nothing — a prefix of `''` matches every entry in the bucket, which is
 *   the whole-bucket delete this function exists to avoid.
 * @returns {Promise<number>} How many entries were actually deleted —
 *   returned for the debug trace and the tests, not for control flow.
 */
async function prunePinnedBasemapURLs(areaId, urls, glyphs) {
  const core = self.pwaBasemapDownloadCore;
  const list = Array.isArray(urls) ? urls : [];
  const prefix = (glyphs && glyphs.prefix) || '';
  const spare = (glyphs && glyphs.spare) || '';
  if (!core || !areaId || !('caches' in window)) return 0;
  if (!list.length && !prefix) return 0;
  let deleted = 0;
  let swept = 0;
  try {
    const cache = await caches.open(core.pinnedCacheName(areaId));
    for (const url of list) {
      try {
        if (await cache.delete(url)) deleted += 1;
      } catch (_e) {
        // One entry refusing to go must not strand the rest.
      }
    }
    if (prefix) {
      // Enumerated AFTER the named deletions, so the sweep never
      // reconsiders an entry that has already gone. `cache.keys()` answers
      // with a snapshot array, so deleting while walking it is safe.
      for (const request of await cache.keys()) {
        const url = request.url;
        if (!url.startsWith(prefix)) continue;
        // The replacement's own glyphs, on a shared host — and the reason
        // this is a `startsWith` rather than an inequality: one prefix can
        // legitimately nest inside the other (`…/fonts/` and
        // `…/fonts/noto/`), and the incoming one wins either way.
        if (spare && url.startsWith(spare)) continue;
        try {
          if (await cache.delete(url)) swept += 1;
        } catch (_e) {
          // As above — best-effort, per entry.
        }
      }
    }
  } catch (_e) {
    // Cache Storage unavailable, or the bucket gone out from under this —
    // both leave the new download intact, which is what matters.
  }
  // SNOW-612: the bucket just changed size, so any measurement of it is
  // stale — same reason an eviction forgets it.
  forgetPinnedBucketMeasurement(areaId);
  window.pwaDebugLog?.record('cache', 'basemap.prune', {
    areaId: areaId,
    asked: list.length,
    deleted: deleted,
    // Separate counts: the named half is checkable against what was asked
    // for, the glyph half is only ever discovered by the sweep itself.
    glyphPrefix: prefix,
    glyphsSwept: swept,
  });
  return deleted + swept;
}

/**
 * SNOW-871: whether ONE area's pinned bucket holds any entry beginning
 * with `prefix`.
 *
 * Area-scoped on purpose, unlike `pinnedBasemapCacheURLs` above, which
 * unions every bucket: the one caller (`_probeDone`'s glyph-prefix heal,
 * map_region_download.js) is establishing a fact about THIS area's own
 * record, and a hit in a sibling area's bucket would prove nothing about
 * it.
 *
 * An empty `prefix` answers `false` rather than "everything matches" — a
 * style with no `glyphs` yields `''` from `activeBasemapGlyphPrefix`, and
 * treating that as a match would heal a record with a prefix that sweeps
 * the whole bucket.
 *
 * @param {string} areaId
 * @param {string} prefix
 * @returns {Promise<boolean>} `false` for an unreadable or absent bucket —
 *   the caller heals nothing on a `false`, which is the safe direction.
 */
async function pinnedAreaCacheHasPrefix(areaId, prefix) {
  const core = self.pwaBasemapDownloadCore;
  if (!core || !areaId || !prefix || !('caches' in window)) return false;
  try {
    const cache = await caches.open(core.pinnedCacheName(areaId));
    const requests = await cache.keys();
    return requests.some((request) => request.url.startsWith(prefix));
  } catch (_e) {
    return false;
  }
}

// SNOW-588: `basemapDownloadedAreas` and `evictBasemapAreas` above, for
// modules OUTSIDE this file — the "Manage downloads" sheet
// (static/js/map_downloads_manager.js), which lists every downloaded area
// and deletes the ones the user picks. (SNOW-871 put two more functions
// between them and this comment, which used to say "the two functions
// above"; `prunePinnedBasemapURLs` and `pinnedAreaCacheHasPrefix` are both
// reached from this file's own lexical scope by the region control, and
// neither is exposed here.)
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
// SNOW-867: the shown basemap's z0-7 overview is fetched when it is first
// shown, not when something is first downloaded — see
// `warmBaseLayerWideBand` for the sizes that make that affordable. Two
// triggers, because there are two ways a basemap comes to be on screen:
// the app opening on the one the user left it on, and the picker changing
// it. Both are no-ops once that basemap's wide half is on disk.
//
// The boot trigger is wrapped because this module is also imported on its
// own by the JS unit tests, where `map_state.js` — and so
// `MAP_READY_PROMISE` — does not exist. A bare identifier throws a
// ReferenceError, which is catchable; the listener below needs no such
// guard, since a page with no map dispatches no basemap change.
try {
  MAP_READY_PROMISE.then(() => warmBaseLayerWideBand());
} catch (_e) {
  // Loaded outside the map page. Nothing to warm.
}
document.addEventListener('snowdesk:basemap-changed', () => warmBaseLayerWideBand());

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
   * The shared base layer's top-up plan for the ACTIVE basemap
   * (SNOW-856), including SNOW-863's re-banding of a bucket left by an
   * older band. SNOW-868: which band that is depends on the basemap —
   * see `baseLayerBand`.
   *
   * The download runner reaches this through its own deps bundle
   * (`PINNED_DOWNLOAD_DEPS.baseLayer`) rather than here — this is the
   * same function, published so the behaviour can be driven directly in
   * tests. Both go through one implementation, so a test cannot pass
   * against a plan the runner would never get.
   *
   * @returns {Promise<Object|null>} See `resolveBaseLayerPlan`.
   */
  baseLayerPlan: () => resolveBaseLayerPlan(),

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
   * SNOW-832: every picker basemap key in the picker's own order — see
   * `basemapOrder`'s own docstring. Exposed for the same load-order
   * reason `basemapLabel` is: the Manage downloads sheet is outside the
   * map bundle's parse-time contract.
   *
   * @returns {string[]}
   */
  basemapOrder: () => basemapOrder(),

  /**
   * SNOW-844: the picker's currently-checked basemap key, or null — see
   * `activeBasemapKey`. The Manage downloads sheet needs it to apply the
   * three-row resolution rule below to a row: only a row on the ACTIVE
   * basemap may have its dependency list derived from the live style.
   *
   * @returns {string | null}
   */
  activeBasemapKey: () => activeBasemapKey(),

  /**
   * SNOW-844: the live style's render dependencies — see
   * `activeBasemapRenderDependencyURLs`. Exposed for the same load-order
   * reason `basemapLabel` and `basemapOrder` are: the Manage downloads
   * sheet is outside the map bundle's parse-time contract, and it needs
   * this list to resolve a row whose record predates the field.
   *
   * @returns {string[]}
   */
  renderDependencyUrls: () => activeBasemapRenderDependencyURLs(MAP),

  /**
   * SNOW-844: the three-row resolution rule — see
   * `areaRenderDependencyURLs`. The sheet applies it per row, so it reads
   * it from here rather than restating it.
   *
   * @param {string[] | null | undefined} recordedDeps
   * @param {boolean} basemapIsActive
   * @returns {string[]}
   */
  areaRenderDependencyUrls: (recordedDeps, basemapIsActive) =>
    areaRenderDependencyURLs(recordedDeps, basemapIsActive),

  /**
   * SNOW-692: the slope-angle tiles one recorded area should hold, derived
   * from its record — see `areaSlopeTileUrls`.
   *
   * Unlike `areaRenderDependencyUrls` this takes the WHOLE record, because
   * a region and a custom area describe their ground with different fields
   * and the derivation has to read whichever is present. It is also not
   * subject to the three-row rule: the slope raster is one layer on one
   * host for every basemap, so a row whose basemap is not on screen can
   * still be judged.
   *
   * @param {Object|null} record
   * @returns {string[]}
   */
  areaSlopeTileUrls: (record) => areaSlopeTileUrls(record),

  /**
   * SNOW-844: refetch `urls` into `areaId`'s pinned bucket — the Manage
   * downloads sheet's Repair control. See `basemap_download_runner.js`'s
   * `repair` for why this is NOT the download path (no eviction, no budget
   * plan): a repair is a handful of documents, and running it through the
   * eviction confirm could destroy another area to make room for a sprite.
   *
   * The sheet paints nothing while it runs — it re-renders on the result,
   * and a repair is four small documents rather than a several-minute
   * download — so the runner's `paint` is a no-op here.
   *
   * @param {string} areaId
   * @param {string[]} urls The MISSING documents only.
   * @returns {Promise<boolean>} Whether every one of them landed.
   */
  repair: (areaId, urls) =>
    new Promise((resolve) => {
      repairPinnedDownload({
        areaId: areaId,
        urls: urls,
        paint: () => {},
        finish: async (result, extras) => {
          const runCore = extras && extras.core;
          resolve(!!(runCore && runCore.downloadSucceeded(result)));
        },
      });
    }),

  /**
   * SNOW-844: every URL held across every pinned bucket — see
   * `pinnedBasemapCacheURLs`. The sheet asks the same question the
   * roundels do ("is this area's whole render set on disk?"), and the
   * service worker answers a basemap request from ANY pinned bucket
   * (sw.js's `_pinnedBasemapMatch`), so the union is the honest set to
   * check against rather than one area's own bucket.
   *
   * @returns {Promise<Set<string>>}
   */
  pinnedCacheUrls: () => pinnedBasemapCacheURLs(),

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
 * Reveal one `_overlay_banner.html` confirm carrying `bodyText`, and
 * resolve once the user answers it (SNOW-871).
 *
 * The listener/cleanup body below was `confirmBasemapEviction`'s alone
 * until this ticket gave the download surface a SECOND question to ask
 * ("this replaces the copy you already have"). Both are the same
 * interaction against the same primitive — reveal, wait, resolve `true` on
 * the CTA and `false` on the overlay's own dismiss — and the only things
 * that differ are which three elements to drive and what to write into the
 * body. Copying it would have been the third place in this file where an
 * `overlay:dismissed` listener has to remember to remove itself.
 *
 * Degrades to `false` (treated as "cancelled") when the banner markup
 * isn't present — an older cached shell mid-rollout, say — because
 * silently proceeding without ever having asked is exactly the silence
 * both callers exist to remove; refusing the run is the safe direction.
 *
 * @param {{banner: string, body: string, cta: string}} ids The element ids
 *   of the include's wrapper, its `body_id` paragraph and its `cta_id`
 *   button. Passed rather than derived from the wrapper id: the partial
 *   takes all three independently, so deriving them here would encode a
 *   naming convention the template does not actually enforce.
 * @param {string} bodyText The specifics, as DATA — a region's own name, a
 *   basemap's translated picker label, a formatted size. Written with
 *   `textContent`, never assembled as HTML.
 * @returns {Promise<boolean>} `true` = proceed (every caller still has to
 *   do the destructive thing itself — this only asks), `false` = cancel.
 */
function confirmViaOverlayBanner(ids, bodyText) {
  return new Promise((resolve) => {
    const banner = document.getElementById(ids.banner);
    const body = document.getElementById(ids.body);
    const cta = document.getElementById(ids.cta);
    if (!banner || !cta) {
      resolve(false);
      return;
    }
    if (body) body.textContent = bodyText;
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

/**
 * SNOW-586: reveal the whole-area-eviction confirm banner naming
 * `evictAreas` and resolve once the user answers.
 *
 * @param {Array<{id: string, name?: string}>} evictAreas
 * @returns {Promise<boolean>} `true` = proceed (the caller still has to
 *   call `evictBasemapAreas` itself — this only asks), `false` = cancel.
 */
function confirmBasemapEviction(evictAreas) {
  // SNOW-635 review: `name` is populated for every non-orphaned area —
  // stored for a region, stored-or-defaulted-from-ordinal for a custom
  // area (see `basemapDownloadedAreas`'s own comment) — so this banner
  // never has to know how to build a default itself. The `|| a.id`
  // fallback exists only for the one case that still has no name at
  // all: an orphaned bucket (SNOW-612) with no record behind it, which
  // `planEviction` can legitimately pick (its missing `savedAt` sorts
  // it as the oldest thing on disk).
  const names = (evictAreas || []).map((a) => a.name || a.id).join(', ');
  return confirmViaOverlayBanner(
    {
      banner: 'map-download-evict-confirm',
      body: 'map-download-evict-confirm-body',
      cta: 'map-download-evict-confirm-cta',
    },
    names,
  );
}

/**
 * SNOW-871: ask before REPLACING the copy of an area this device already
 * holds under a different basemap.
 *
 * A region's pinned bucket is keyed on the region id alone, so downloading
 * the same region under a second basemap replaces the first — the tiles
 * the user already paid for go, and until this ticket they went silently,
 * before the new run had fetched anything. Every other destructive control
 * on this surface confirms first (the budget eviction above, the Manage
 * downloads sheet's Delete); this one did not, which is half of what
 * SNOW-871 exists to fix. The other half is the ORDER — see the region
 * control's `finish`, which now prunes the old copy only once the new one
 * has landed.
 *
 * Only ever raised when there is genuinely something to lose: the caller
 * establishes that the record names a different basemap AND that its tiles
 * are still on disk (`map_region_download.js`'s `beforeWarm`). A stale
 * record whose bucket has already been evicted replaces nothing, and must
 * not be dressed up as a loss.
 *
 * @param {{basemapKey: string, name: string, bytes: number}} previous What
 *   the replacement costs, from the existing record: the basemap it was
 *   downloaded under (a picker key, `''` when nothing on record names it),
 *   the area's own name, and its recorded size.
 * @returns {Promise<boolean>} `true` = go ahead and download, `false` =
 *   leave the existing copy alone and start nothing.
 */
function confirmBasemapReplace(previous) {
  // The picker's own server-translated label, so the banner names the
  // basemap exactly as the popover the user chose it from does. `''` for a
  // record whose basemap cannot be named (a pre-SNOW-645 record nothing
  // else on the device shares a template with) — which takes the unnamed
  // string rather than interpolating an empty name into the named one, the
  // same pairing the roundel's own label uses.
  const label = basemapLabel(previous.basemapKey || '');
  // Defensive, like map_custom_download.js's `_formatBytes`:
  // `pwaBasemapManageCore` is loaded on the map page, and treated as
  // optional here anyway.
  const manage = self.pwaBasemapManageCore;
  const size =
    manage && typeof manage.formatMegabytes === 'function'
      ? manage.formatMegabytes(previous.bytes || 0)
      : '0 MB';
  const body = label
    ? self.pwaStrings.interpolate(MAP_STRINGS['download-replace-body'], {
        basemap: label,
        region: previous.name,
        size: size,
      })
    : self.pwaStrings.interpolate(MAP_STRINGS['download-replace-body-unnamed'], {
        region: previous.name,
        size: size,
      });
  return confirmViaOverlayBanner(
    {
      banner: 'map-download-replace-confirm',
      body: 'map-download-replace-confirm-body',
      cta: 'map-download-replace-confirm-cta',
    },
    body,
  );
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
// SNOW-867: the device's own download ceiling, resolved once and reused.
//
// `navigator.storage.estimate()` is async and the callers are not: both
// framing surfaces recompute their selection once per animation frame, and
// awaiting a storage estimate inside that loop would be a promise per frame
// for a number that changes on the timescale of downloads, not frames. So
// it is resolved on the edges that can actually change it — a surface
// opening, a run settling, an area being deleted — and read synchronously
// in between.
//
// `null` until the first resolve, which the getter reports as the core's
// fallback constant rather than as "unlimited".
let _basemapDeviceCeilingMb = null;

/**
 * The last resolved device ceiling, in megabytes.
 *
 * @returns {number} The device's own ceiling, or
 *   `DOWNLOAD_CEILING_MB` before the first resolve (and on a device that
 *   will not report its quota).
 */
function basemapDeviceCeilingMb() {
  const core = self.pwaBasemapDownloadCore;
  if (_basemapDeviceCeilingMb !== null) return _basemapDeviceCeilingMb;
  return core ? core.DOWNLOAD_CEILING_MB : 200;
}

/**
 * Re-read the device's storage estimate and cache the ceiling it implies.
 *
 * Best-effort: a browser with no Storage API, or one that refuses to
 * answer, leaves the cached value alone and the getter keeps reporting the
 * fallback constant.
 *
 * @returns {Promise<number>} The resolved ceiling, for a caller that wants
 *   to act on it immediately rather than read it back.
 */
async function refreshBasemapDeviceCeiling() {
  const core = self.pwaBasemapDownloadCore;
  if (!core || typeof core.deviceCeilingMb !== 'function') return basemapDeviceCeilingMb();
  if (!('storage' in navigator) || typeof navigator.storage.estimate !== 'function') {
    return basemapDeviceCeilingMb();
  }
  try {
    _basemapDeviceCeilingMb = core.deviceCeilingMb(await navigator.storage.estimate());
  } catch (_e) {
    // Leave the previous answer standing — a failed estimate is not
    // evidence that the device shrank.
  }
  return basemapDeviceCeilingMb();
}

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
  tileSources: () => activeBasemapTileSources(MAP),
  basemapKey: () => activeBasemapKey(),
  planBudget: (areaId, mb) => planBasemapDownloadBudget(areaId, mb),
  confirmEviction: (areas) => confirmBasemapEviction(areas),
  evict: (areaIds) => evictBasemapAreas(areaIds),
  feedUrls: () => assembleBasemapDownloadFeedURLs(),
  // SNOW-692: takes the blob, because the slope raster covers the same
  // ground and band as the area's own tiles — see `slopeTileURLs`.
  slopeUrls: (blob) => activeSlopeTileURLs(blob),
  // SNOW-924: the bulletins and weather inside the area's boundary, and
  // the four overlay feeds cached whole on the way past. Async, alone
  // among these — see `assembleAreaContentURLs`.
  contentUrls: (blob) => assembleAreaContentURLs(blob),
  // SNOW-844: the subset of `feedUrls` that is a RENDER dependency of the
  // active style, captured at run start alongside `tileSources` so the
  // record stores the list this run actually fetched rather than whatever
  // the picker happens to say by the time `finish` runs.
  renderDeps: () => activeBasemapRenderDependencyURLs(MAP),
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
  // SNOW-856: the shared base layer, topped up AFTER the area this
  // run was actually for — see `topUpBaseLayer` in the runner for why
  // after, and why its failures never reach `finish`.
  baseLayer: () => resolveBaseLayerPlan(),
  finishBaseLayer: (result, plan) => recordBaseLayer(result, plan),
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
 * SNOW-844: refetch one area's MISSING render dependencies — see
 * `basemap_download_runner.js`'s `repair` for why this is a separate,
 * much shorter path than `runPinnedDownload` above (no quota pre-flight,
 * no budget plan, and above all no eviction confirm).
 *
 * The same thin-delegator shape, and the same treatment of a missing
 * runner module: fail the repair rather than silently do nothing.
 *
 * @param {Object} options `areaId`, `urls`, `paint`, `finish` — the
 *   runner's own argument contract.
 * @returns {Promise<void>}
 */
async function repairPinnedDownload(options) {
  // The bucket's size changes, so a cached orphan measurement of it is
  // stale from here on — same reason `runPinnedDownload` forgets it.
  forgetPinnedBucketMeasurement(options.areaId);
  const runner = self.pwaBasemapDownloadRunner;
  if (!runner || typeof runner.repair !== 'function') {
    options.paint('error');
    revealBasemapDownloadError(null);
    // Settled even here, so a caller awaiting the outcome (the Manage
    // downloads sheet's Repair control) is never left hanging on a
    // promise an older cached shell can never resolve.
    await options.finish(null, { core: self.pwaBasemapDownloadCore });
    return;
  }
  return runner.repair(PINNED_DOWNLOAD_DEPS, options);
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
 * Needed because `activeBasemapTileSources` is gated on
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
