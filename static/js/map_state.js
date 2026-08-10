/*
 * static/js/map_state.js — the map's shared state, and the one named
 * channel to it.
 *
 * SNOW-610, step 4. This is the file every other map module depends on,
 * so it loads FIRST of them all.
 *
 * `window.snowdeskMapState` (SNOW-610 step 0, PR #589) is the reason the
 * split was possible at all. Every declaration below is a top-level
 * `let`/`const` in a CLASSIC script: that puts it in the global LEXICAL
 * scope — readable from any later classic script as a bare identifier —
 * but NOT on `window`. The distinction is invisible until you rely on the
 * wrong half of it, which is precisely how `map_layer_sync_status.js` read
 * `window.MAP` for its entire life and always got `undefined`.
 *
 * So there are two routes to this state, and which one to use is not a
 * matter of taste:
 *
 *   - Modules INSIDE the split (map.js and its map_*.js siblings) read the
 *     bare identifiers. They are all classic scripts loaded after this one,
 *     so the bindings are simply in scope.
 *   - Modules OUTSIDE it use `window.snowdeskMapState`, which is greppable
 *     and cannot silently yield `undefined`.
 *
 * FROZEN SURFACE, MUTABLE VALUES. `Object.freeze` stops anything replacing
 * or adding an accessor; the accessors themselves read and write the live
 * bindings. A frozen plain-data object would have published the boot-time
 * nulls forever — `map` is null until the style loads.
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
  // SNOW-656: 'l4' NARROWED. It used to mean "the micro-region boundary AND
  // the bulletin data painted onto it" — one key driving regions-fill,
  // regions-line, regions-label and bulletin-groupings-line together. It now
  // means the boundary and its label ALONE (the "Micro regions" row); the
  // choropleth and the dissolved bulletin boundary moved to 'bulletins'
  // below. The key name is unchanged because the DOM contract
  // (data-overlay-key="l4"), the sync-status resource and the country-scoped
  // tier list all key off it and none of them changed meaning.
  l4: 'snowdesk.map.overlay.l4',
  // SNOW-656: the "Bulletins" row — regions-fill (the danger choropleth) and
  // bulletin-groupings-line. Unlike the micro-region geography these are
  // DATE-BOUND, and they are mutually exclusive with the downloads sheet's
  // the downloads panel's "Display on the map" (both paint the same
  // polygons). This key stores
  // the user's PREFERENCE only; the exclusivity is a session-scoped
  // suppression on top of it — see static/js/layer_visibility_core.js.
  //
  // A device that has never seen this key seeds it from 'l4' exactly once
  // (seedFromLegacy), so an existing 'l4=false' comes back with both rows
  // off rather than silently acquiring a choropleth the user switched off.
  bulletins: 'snowdesk.map.overlay.bulletins',
  resorts: 'snowdesk.map.overlay.resorts',
  // SNOW-414: eligible-only — the toggle only exists in the DOM (and this
  // key is only ever read/written) when data-favourites-eligible="true".
  favourites: 'snowdesk.map.overlay.favourites',
  // SNOW-419: flag-gated only — the toggle exists in the DOM (and this key
  // is only ever read/written) when data-community-reports-eligible="true".
  community_reports: 'snowdesk.map.overlay.community_reports',
  // SNOW-645 review, twice over: 'downloaded' was here (SNOW-570) — the
  // layers-menu toggle it persisted is gone. It is now an "Available
  // offline" toggle INSIDE the "Manage downloads" sheet instead
  // (map_downloads_manager.js, driving window.pwaDownloadedOverlay in
  // map.js directly) — deliberately SESSION-scoped, not persisted here or
  // anywhere else: closing the sheet leaves it as the user set it, but a
  // fresh page load always starts it off. This localStorage key is now
  // write-once dead: an existing device may still carry a stored
  // 'true'/'false' from before this change, but nothing reads it any
  // more, and nothing should start reading it again — reviving it would
  // silently turn the overlay on for a returning user who never asked to
  // see it this session.
  // SNOW-573: flag-gated only — the toggle exists in the DOM (and this key
  // is only ever read/written) when data-weather-layer-eligible="true".
  weather: 'snowdesk.map.overlay.weather',
};

// No ``l3`` entry above: the bulletin-boundary layer has no toggle and no
// persisted state of its own — see OVERLAY_VISIBILITY_GOVERNOR, which since
// SNOW-656 points it at ``bulletins`` rather than ``l4``.

// SNOW-658: the layers menu lists BULLETIN PROVIDERS, not countries — SLF
// (CH), MétéoFrance (FR), ALBINA (AT, IT) — because that is what a row
// actually switches on: one provider's bulletins. ALBINA publishes for both
// Austria and Italy, so its single row drives TWO country codes.
//
// Nothing below the menu changed shape for that. ``countryState``, the
// per-code localStorage keys and ``applyCountryFilters``'s country filter are
// all still per-code, and one row now simply writes two of them. Which is why
// this mapping is a ROUTING table, declared once here and read by every
// consumer, rather than a new "albina" pseudo-country threaded through the
// filter code.
//
// A key absent from this table maps to the single code in its own suffix, so
// ``country.ch`` needs no entry.
const COUNTRY_GROUPS = {
  'country.albina': ['at', 'it'],
};

/**
 * The country codes a layers-menu overlay key switches.
 *
 * @param {string} overlayKey - e.g. ``'country.albina'`` or ``'country.ch'``.
 * @returns {string[]} One or more lowercase country codes.
 */
function countryCodesFor(overlayKey) {
  return COUNTRY_GROUPS[overlayKey] || [overlayKey.slice('country.'.length)];
}

/**
 * The layers-menu overlay key that owns a country code — the inverse of
 * ``countryCodesFor``. Used to find the row to paint (or revert) for a code
 * the map itself is working with.
 *
 * @param {string} code - a lowercase country code, e.g. ``'it'``.
 * @returns {string} The overlay key, e.g. ``'country.albina'``.
 */
function overlayKeyForCountry(code) {
  for (const [key, codes] of Object.entries(COUNTRY_GROUPS)) {
    if (codes.includes(code)) return key;
  }
  return `country.${code}`;
}

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
  // that used to be assembled in JS — at the time, bin/i18n-lint could
  // not see a literal assigned to a variable before being rendered, so
  // these were moved here by hand rather than because the check demanded
  // it. SNOW-645 closed that gap (the check now follows one hop of
  // indirection), so the same class of string is caught automatically
  // now. 'frame-readout-busy' deliberately has no literal '%' in the
  // msgid — the caller appends it to the interpolated `pct` value itself,
  // so there is nothing here for a translation to get wrong.
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
  // SNOW-642: #region-readout's empty state — see updateReadout below.
  'no-region': 'No region selected',
  // SNOW-645: the per-region download roundel's own labels
  // (map_region_download.js's `setState`) — see that file's own comment
  // and _map_embed.html's map-strings-template for why these moved here.
  'download-no-region-unavailable': "Basemap download isn't available for this region",
  'download-no-region-select': 'Select a region to download its basemap',
  'download-idle': "Download this region's basemap — up to %(mb)s MB",
  'download-busy': "Downloading this region's basemap — %(pct)s",
  'download-done': "This region's basemap is downloaded — available offline",
  'download-error': "This region's basemap download failed — tap to try again",
  'download-disabled': "This region's basemap is too large to download",
  'download-offline': 'Basemap download unavailable while offline',
  'download-other-basemap':
    "This region's basemap is downloaded for %(basemap)s — tap to download it for this basemap too",
  'download-other-basemap-unnamed':
    "This region's basemap is downloaded for another basemap — tap to download it for this basemap too",
  // SNOW-573: the Weather overlay's layers-menu row disable reason —
  // point weather is forecast-only and often runs short (ICON-CH2
  // commonly returns fewer days than requested), so a scrubbed date
  // outside the stored window disables the row with this as its title.
  'weather-out-of-window': 'No forecast for this date',
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

// SNOW-610: the offline basemap-download block that used to sit here —
// pinned-cache helpers, the downloaded-area records, the byte budget and
// its eviction path, the failure toasts, the on-map progress grid and the
// `runPinnedDownload` delegator — now lives in
// static/js/map_basemap_downloads.js, which loads immediately before this
// file. Its declarations are still reachable here as bare identifiers:
// classic scripts share one global lexical scope.

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
