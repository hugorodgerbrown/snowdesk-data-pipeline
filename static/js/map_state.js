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

// SNOW-660: ``BOOT_DATE_KEY`` (SNOW-236's clamped min(today, seasonEnd))
// used to live here so the scrubber could snap to it. Nothing computes or
// reads it any more: an empty querystring means no day has been asked for,
// and the map paints nothing rather than a date it chose itself. The one
// answer to "which day" is map.js's ``currentDisplayedDate``, seeded from
// ``?d=``; there is deliberately no shared fallback for a chosen day to be
// silently substituted from.

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
  // DATE-BOUND. They were mutually exclusive with the downloads panel's
  // "Display on the map" — both paint the same polygons — until SNOW-663
  // made the download squares a hatch the danger colour reads through; the
  // two overlays are independent now, and nothing suppresses this one but
  // staff resort-edit mode. This key stores the user's PREFERENCE only; a
  // suppression sits on top of it, unpersisted — see
  // static/js/layer_visibility_core.js.
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
  // SNOW-761: the map's Weather overlay row. Ungated like community reports
  // — the feed is public, filtered server-side by Location.objects.public()
  // — so this key is read and written for every visitor. Defaults OFF: a
  // condition symbol at every station is a second layer of information over
  // the danger ratings someone opened the map to read.
  weather: 'snowdesk.map.overlay.weather',
  // The downloads panel's "Display on the map" switch. PERSISTED, like the
  // three switches beside it — this reverses SNOW-645's "session-scoped
  // inspection mode", which is what Hugo reported as a bug: four identical
  // switches on four identical panels, three of which survive a reload and
  // one of which silently forgets. A view setting the user set deliberately
  // is a preference, and the panel it lives in gives no hint that this one
  // is different.
  //
  // A NEW key name, not SNOW-570's 'snowdesk.map.overlay.downloaded'. That
  // one is still on disk on any device that used the layers-menu row this
  // control replaced, holding a value written for a control that no longer
  // exists — reading it back now would switch the overlay on at boot for a
  // user whose last actual instruction was given to something else. The
  // dead key is left unread and unwritten; nothing clears it, since
  // localStorage has no cost to leaving a stale key alone.
  downloads: 'snowdesk.map.overlay.downloads',
  // SNOW-687: eligible-only, like favourites — the switch lives in the
  // routes panel and this key is only ever read/written when
  // data-routes-eligible="true" (an authenticated user; SNOW-724 retired
  // the ``routes`` flag that used to AND with it). Defaults OFF, unlike
  // favourites: a GPX track is
  // visually far heavier than a pin, so the overlay is opt-in the way
  // community_reports is.
  routes: 'snowdesk.map.overlay.routes',
  // SNOW-691: gated on settings.SLOPE_TILE_URL (SNOW-724 moved the gate off
  // a waffle flag and onto the setting) — the row exists in the DOM, and
  // this key is only ever read/written, when
  // data-slope-layer-eligible="true".
  // Defaults OFF: the raster covers the whole viewport wherever it has data,
  // and a visitor who opened the map to read danger ratings did not ask for a
  // second full-screen colour scheme under them.
  slope: 'snowdesk.map.overlay.slope',
};

// No ``l3`` entry above: the bulletin-boundary layer has no toggle and no
// persisted state of its own — see OVERLAY_VISIBILITY_GOVERNOR, which since
// SNOW-656 points it at ``bulletins`` rather than ``l4``.

// SNOW-904: whether one collapsible section of the layers menu is open, one
// key per section slug (``places`` / ``conditions`` / ``boundaries`` /
// ``basemap``). A factory rather than four literals for the same reason
// ``COUNTRY_STORAGE_KEY`` is one: the slug is already in the DOM, on each
// heading's ``data-section-toggle``, so restating it here four times would
// be a fifth and sixth copy of the same name to keep in step.
//
// Every section defaults CLOSED except ``conditions``, read with
// ``readBoolStorage`` — a plain two-state preference, unlike the downloads
// overlay's tri-state next to it, because "never touched" and "closed" want
// exactly the same behaviour here.
const LAYERS_SECTION_STORAGE_KEY = (slug) => `snowdesk.map.layers.section.${slug}`;

// The one section that opens EXPANDED on a first visit. Conditions is what
// the map is for: the danger ratings and what the mountain is doing today.
const LAYERS_SECTION_DEFAULT_OPEN = 'conditions';

// SNOW-897: overlay key -> the MapLibre layer ids that overlay controls.
//
// ONE table. There were two, with near-identical names and identical shape:
// ``OVERLAY_LAYER_IDS_MAIN`` in map.js and ``OVERLAY_LAYER_IDS`` in
// map_basemap_picker.js. Five entries (l1, l2, resorts, slope, weather) were
// duplicated verbatim between them, four existed only in map.js and one only
// in the picker — so the pair was neither a clean partition nor a clean copy,
// and establishing which it was meant reading roughly sixty lines of comment
// across two files. Every reader had to redo that, and the answer was not
// visible from either site. Nothing would have noticed the duplicated five
// drifting apart.
//
// Both consumers only ever LOOK KEYS UP; neither enumerates the table. So one
// table serves both, and each simply resolves the keys it drives: map.js the
// panel-driven overlays plus the tiers, the picker the menu rows. A key
// visible to a consumer that never asks for it costs nothing.
//
// ``regions-fill`` is deliberately in NO entry, and adding it would be a bug
// rather than a tidy-up. It is the only overlay layer driven by OPACITY
// rather than visibility, because it is the map's hit-test target and
// ``queryRenderedFeatures`` returns nothing from a layer at
// ``visibility: none``. It goes through ``applyBulletinsVisibility`` in
// map.js, which is its single writer.
// ``tests/js/test_overlay_layer_registry.js`` asserts that.
//
// Order within a group is load-bearing in one place: map.js's
// ``panelOverlayPainted`` answers for a whole group from element [0], so the
// first id must be the layer the user actually sees.
const OVERLAY_LAYERS = Object.freeze({
  l1: ['major-regions-line', 'major-regions-label'],
  l2: ['sub-regions-line', 'sub-regions-label'],
  // SNOW-323: a line layer only — groupings carry no user-facing name to
  // label. No storage key either (see the note above this table).
  l3: ['bulletin-groupings-line'],
  // The micro-region GEOGRAPHY: the boundary and its label, and NOT the
  // choropleth painted onto it (SNOW-656). Driven by the picker's row.
  l4: ['regions-line', 'regions-label'],
  resorts: ['resorts-pin', 'resorts-label'],
  favourites: ['favourites-pin', 'favourites-label'],
  community_reports: [
    'community-reports-clusters',
    'community-reports-cluster-count',
    'community-reports-point',
  ],
  // SNOW-761: one symbol layer, not a pin+label pair — the condition glyph
  // is an inline `image` section inside `text-field`.
  weather: ['weather-point'],
  // SNOW-687: the coloured line FIRST and the casing second — deliberately
  // the inverse of the order installRoutesLayer adds them in, where the
  // casing has to be added first to paint underneath. SNOW-764's
  // 'routes-line-pending' is NOT first either: a visitor holding only a
  // pending share is the exception, not the case the roundel ring is
  // painted from.
  routes: [
    'routes-line', 'routes-line-casing', 'routes-line-pending', 'routes-endpoints',
  ],
  // SNOW-691: the raster alone. The coverage outline that rode alongside it
  // was removed; see slope_overlay_core.js's header.
  slope: ['slope-raster'],
});

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

// SNOW-872: the map's OPENING VIEW — what a device with no stored
// preference gets. Server-configured (settings.MAP_DEFAULT_PROVIDERS /
// _BOUNDARY / _OPACITY_STEP), rendered onto `#map` by _map_embed.html, and
// read back here.
//
// One owner, for the reason SNOW-615 gave OVERLAY_STORAGE_KEY one: the same
// three literals used to be written out four times — map.js's boot IIFE, the
// re-seed in its `styledata` handler after a basemap swap,
// map_season_ribbon.js, and the template's aria-checked attributes — and a
// copy missed is not a crash, it is an opening view that quietly reverts on
// the next basemap swap.
//
// EVERY ATTRIBUTE IS OPTIONAL, and absence falls back to the literal that
// shipped before this. That is a requirement rather than politeness: around
// forty fixtures across tests/js hand-write a `#map` root and will never
// carry these, and trip_map.js drives two map roots of its own (#trip-map,
// #trip-meeting-picker) that know nothing about layers.
//
// Read fresh on each call rather than memoised. It is called about three
// times at boot, so there is nothing to save, and a cached value would go
// stale across the bundle re-boots tests/js performs between suites.
/**
 * The server-configured defaults for a first visit.
 *
 * @returns {{overlays: Set<string>, boundary: string, opacityStep: (number|undefined)}}
 *   `overlays` holds layers-menu overlay keys (`country.ch`, …) — the
 *   overlay-key → country-code routing stays in `COUNTRY_GROUPS` above and
 *   is resolved through `countryCodesFor` / `overlayKeyForCountry`, never
 *   restated. `boundary` is an EAWS tier key, or `''` for no boundary.
 *   `opacityStep` is `undefined` when unset, which is what
 *   `pwaLayerVisibilityCore.seedFromLegacy` reads as "use your own default".
 */
function mapDefaults() {
  const el = document.getElementById('map');
  const ds = (el && el.dataset) || {};
  // `undefined` rather than falsy throughout: an EMPTY attribute is a
  // configuration — no provider on, no boundary drawn — and `||` would read
  // an operator's deliberate blank as "not set" and put SLF back.
  const overlays = ds.defaultOverlays === undefined ? 'country.ch' : ds.defaultOverlays;
  return {
    overlays: new Set(overlays.split(/\s+/).filter(Boolean)),
    boundary: ds.defaultBoundary === undefined ? 'l4' : ds.defaultBoundary,
    opacityStep: ds.defaultOpacityStep === undefined ? undefined : Number(ds.defaultOpacityStep),
  };
}

const BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
const AUTOZOOM_STORAGE_KEY = 'snowdesk.map.autozoom';
// SNOW-737: where the visitor last left the camera. ONE key holding a JSON
// blob of all five numbers, deliberately unlike the boolean-per-key shape
// above: a centre, a zoom, a bearing and a pitch are only meaningful
// together, and five independent keys make a half-written camera — a
// longitude from this session beside a zoom from the last — representable.
// Read and validated through `window.pwaViewportCore` (map_viewport_core.js),
// which rejects a camera the current build's limits no longer allow rather
// than letting MapLibre clamp it silently.
const VIEWPORT_STORAGE_KEY = 'snowdesk.map.viewport';

// SNOW-620: the strings this file writes into the DOM itself, server-
// translated into the template _map_embed.html renders and read back here.
// makemessages does not scan JavaScript, so a literal written below would
// ship as English to every locale. The literals here are the English
// fallback — see static/js/i18n_strings.js.
//
// Module scope rather than per-IIFE: several separate IIFEs read these.
//
// SNOW-895: 'bulletin-link' and 'no-bulletin' were here for map.js's region
// popup, which relabelled its own bulletin link as the scrubber moved. That
// popup is gone, and so are they. The same two sentences are still rendered
// SERVER-side by public/_region_tooltip.html, which is what the region panel
// shows — the client-side copies had no reader left.
const MAP_STRINGS = self.pwaStrings.read('map-strings-template', {
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
  // SNOW-697: the search dropdown's row-type words. Visually hidden — a
  // sighted user reads the type off the pin glyph and the secondary line,
  // so these exist for screen readers, which have neither. Needed the
  // moment resorts became rows again: two rows that both say "Verbier"
  // and both name a region are indistinguishable read aloud.
  'search-type-region': 'Region',
  'search-type-resort': 'Resort',
  // SNOW-658 removed 'custom-control-idle'/'custom-control-done' — the
  // custom-area roundel's two labels for a state it no longer has (see
  // map_custom_download.js's header). Its label is server-rendered now,
  // and the one thing that varies is composed onto it by
  // map_roundel_overlay_state.js from its own strings template.
  // SNOW-635: an unrenamed custom area's default display name, filled in
  // by basemapDownloadedAreas() itself — see that function's own comment.
  'default-custom-name': 'Custom area %(n)s',
  // SNOW-856: the shared z0-9 base layer's display name in the Manage
  // downloads sheet — the zoomed-out map every area under a basemap
  // reads once the camera passes a download's z10 floor.
  'base-layer-name': 'Overview map',
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
  // SNOW-844: the tiles are here and the documents needed to draw them are
  // not. The label says what the tap does, because the state is unfamiliar
  // and the remedy is four small documents rather than a re-download.
  'download-incomplete':
    "This region's basemap is missing part of itself — tap to finish it",
  'download-disabled': "This region's basemap is too large to download",
  'download-offline': 'Basemap download unavailable while offline',
  'download-other-basemap':
    "This region's basemap is downloaded for %(basemap)s — tap to download it for this basemap too",
  'download-other-basemap-unnamed':
    "This region's basemap is downloaded for another basemap — tap to download it for this basemap too",
  // SNOW-871: the body of the "this replaces the copy you already have"
  // confirm (`confirmBasemapReplace`, map_basemap_downloads.js). The title
  // asks the question and is server-rendered in the banner itself; this
  // line is the SPECIFICS — which basemap's copy, of what, and how big —
  // so the user is answering about something identifiable rather than
  // about "an earlier download". Two variants, for the same reason
  // 'download-other-basemap' has two: a pre-SNOW-645 record can carry no
  // basemap key that anything on the device can name, and an empty name
  // must never be interpolated into the named string.
  'download-replace-body': 'Your %(basemap)s copy of %(region)s (%(size)s)',
  'download-replace-body-unnamed': 'Your earlier copy of %(region)s (%(size)s)',
  // SNOW-749: the roundel's ninth state. Downloading is gated on an
  // account, and the control stays
  // VISIBLE and tappable while the visitor is signed out — a hidden
  // control reads as a missing feature. Tapping it goes to sign-in, so
  // the label has to promise that and nothing more.
  'download-signin': "Sign in to download this region's basemap",
  // SNOW-660: #map-date-ribbon's empty state. A cold boot no longer paints
  // a day nobody asked for, so the ribbon says which day is showing —
  // including when the honest answer is "none yet".
  'map-date-none': 'No date selected',
  // SNOW-687: the figures in the route detail popup (map.js's
  // activateRoute). Separate strings rather than one templated line,
  // because each is OMITTED ENTIRELY when its Route field is null — a GPX
  // with no <ele> means "unknown", not "flat", and rendering the second
  // for the first is a safety-relevant lie (see the model's own
  // docstring). One combined string would have no way to leave part of
  // itself out.
  'route-distance': '%(km)skm',
  'route-ascent': '%(m)sm ↑',
  // Ascent's opposite number, and shown beside it rather than instead of
  // it: an out-and-back and a one-way traverse can carry the same length
  // and the same climb, and only the descent separates them. A positive
  // magnitude, as stored — Route.descent_m is never signed.
  'route-descent': '%(m)sm ↓',
  // The elevation profile's accessible name and the range caption under
  // it. The caption is not decoration: the chart's y-axis is scaled to
  // the track's OWN min-to-max (see elevation_profile_core.js's
  // buildPaths), so without these two numbers the curve's height has no
  // stated meaning. Both are omitted along with the chart when the GPX
  // carried no elevation.
  'route-profile-label': 'Elevation profile of this route',
  'route-elevation-range': '%(low)s–%(high)s m',
  // SNOW-750: how long the recording ran, shown on the caption line beside
  // the range. ELAPSED, not moving time — the span between the file's first
  // and last <time>, stops included. Two forms rather than one padded
  // string: a 41-minute tour reading "0h41m" states an hours figure it does
  // not have, and an hour count is not a leading zero on a minute count. A
  // multi-day track keeps counting in hours ("31h05m"); days would need a
  // third form for a case a ski tour does not have.
  'route-duration-hours': '%(hours)sh%(minutes)sm',
  'route-duration-minutes': '%(minutes)sm',
  // The same last-resort label routes/partials/_route.html falls
  // back to, so one route reads identically in the panel and in the popup.
  // The popup's payload carries no ``source_filename`` (the row's middle
  // fallback), so a nameless route lands straight here.
  'route-untitled': 'Untitled route',
  // SNOW-764: the shared-route popup's Save control — its label and its
  // three outcomes. No 'route-shared-with-you' qualifier: both labels
  // below already say the route is somebody else's being offered, so the
  // line only repeated them. The pending PANEL ROW keeps its own prefix,
  // because a row sits in a list beside owned ones and a popup does not.
  //
  // The at-cap line names the remedy, because the remedy is the only thing
  // that distinguishes it from the generic failure — a user at the cap who
  // is only told "that couldn't be saved" will retry the identical action.
  'route-save': 'Save route',
  'route-save-signin': 'Sign in to save this route',
  'route-save-failed': "That couldn't be saved. Try again.",
  'route-save-limit': "You've reached your saved-route limit. Delete one to save this.",
  // SNOW-691: the Terrain row's disable reason — the viewport centre has
  // left the slope raster's declared coverage rectangle, so there is
  // nothing for the layer to draw here. One string, not two: the row is
  // only in the DOM when the layer is available at all, so WHERE the
  // visitor is looking is the only thing that can make it unusable.
  'slope-out-of-coverage': 'No slope data for this area',
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
// block — but that block is where `MAP`, `FEATURE_BY_ID`, `COUNTRY_STATE`
// and `AUTOZOOM` are declared, so extracting it first would
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

  /** @returns {Object} SNOW-897: overlay key → its MapLibre layer ids. */
  get overlayLayers() {
    return OVERLAY_LAYERS;
  },

  /**
   * SNOW-897: overlay key → the localStorage key persisting its switch.
   *
   * Published alongside ``overlayLayers`` so the pairing between the two is
   * checkable: a key with a switch but no layers is a control wired to
   * nothing, which is exactly the class of bug one merged table makes
   * findable and two separate ones hid.
   *
   * @returns {Object}
   */
  get overlayStorageKey() {
    return OVERLAY_STORAGE_KEY;
  },
});
