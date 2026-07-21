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
 *   4. Wire up click and region-popup interactions.
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

// "2026-04-25" → "APR 25 2026". Locale-friendly, unambiguous (avoids the
// 04/05 day-vs-month confusion of all-numeric formats). Uppercase to
// match the season-bookend labels and the server-rendered date pill.
const SCRUBBER_MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                         'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const formatDateLong = (dateKey) => {
  const [y, m, d] = dateKey.split('-');
  return `${SCRUBBER_MONTHS[parseInt(m, 10) - 1]} ${parseInt(d, 10)} ${y}`;
};

// SNOW-318: "2026-04-08" → "8 Apr 2026" — day-first, title-case 3-letter month.
// Deliberately distinct from formatDateLong (uppercase, month-first, for the
// readout pill). This mirrors the popup card's server render, where
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

// Repaint every known region's choropleth fill via MapLibre feature-state
// for the supplied date. Missing regions in the frame fall back to
// no_rating so colours from a previous frame don't leak through.
const repaintRegionsForDate = (dateKey, cache) => {
  if (!MAP) return;
  const frame = (cache && cache[dateKey]) || {};
  for (const [regionID, feature] of Object.entries(FEATURE_BY_REGION_ID)) {
    const ratingInt = frame[regionID];
    const rating = ratingInt == null ? 'no_rating' : INT_TO_RATING[ratingInt];
    MAP.setFeatureState({ source: 'regions', id: feature.id }, { rating });
  }
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
  const BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
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
  // The selector deliberately excludes the SNOW-59 overlay checkboxes —
  // they own their own aria-checked state, applied below from
  // ``overlayState``.
  if (basemapMenu) {
    for (const btn of basemapMenu.querySelectorAll(
      '.basemap-menu-item:not(.basemap-menu-item--overlay)',
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
  const OVERLAY_STORAGE_KEY = {
    l1: 'snowdesk.map.overlay.l1',
    l2: 'snowdesk.map.overlay.l2',
    l3: 'snowdesk.map.overlay.l3',
    l4: 'snowdesk.map.overlay.l4',
    resorts: 'snowdesk.map.overlay.resorts',
    // SNOW-414: eligible-only — the toggle only exists in the DOM (and this
    // key is only ever read/written) when data-favourites-eligible="true".
    favourites: 'snowdesk.map.overlay.favourites',
    // SNOW-419: flag-gated only — the toggle exists in the DOM (and this key
    // is only ever read/written) when data-community-reports-eligible="true".
    community_reports: 'snowdesk.map.overlay.community_reports',
  };
  // L4 defaults to visible: hiding it leaves only the basemap and any
  // active overlay tiers, which is intended. SNOW-78 resorts default off
  // so the map opens uncluttered.
  // SNOW-323: l3 (bulletin groupings) defaults off so the map opens uncluttered.
  // SNOW-414: favourites defaults ON — a user's own saved pins should be
  // visible without an extra toggle-hunt, unlike resorts (a public dataset).
  // SNOW-419: community_reports defaults OFF — a shared layer of other
  // people's reports is an opt-in, unlike a user's own favourites.
  const overlayState = {
    l1: false, l2: false, l3: false, l4: true, resorts: false,
    favourites: true, community_reports: false,
  };
  // SNOW-473: this seed is re-run inside the ``styledata`` handler after a
  // basemap swap (search "SNOW-473") — keep the two blocks in sync when adding
  // an overlay key.
  for (const key of ['l1', 'l2', 'l3', 'resorts', 'community_reports']) {
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
  AUTOZOOM = readBoolStorage('snowdesk.map.autozoom', false);
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
  map.on('error', () => {
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
  const updateMapAttribution = () => {
    if (!attributionTarget) return;
    const style = map.getStyle && map.getStyle();
    if (!style || !style.sources) return;
    const seen = new Set();
    // ``getStyle().sources`` returns the static style config, which does
    // not include the attribution string for tilejson-backed sources —
    // that arrives on the runtime ``Source`` instance after the tilejson
    // resolves. ``map.getSource(id)`` is the public path to that
    // instance, mirroring what MapLibre's own AttributionControl uses.
    for (const id of Object.keys(style.sources)) {
      const src = map.getSource(id);
      if (src && src.attribution) seen.add(src.attribution);
    }
    // Source attribution strings carry trusted HTML (provider links) — we
    // assign innerHTML rather than textContent so the same anchors that
    // MapLibre's stock AttributionControl renders stay clickable. The
    // basemap URLs are server-controlled, so the trust boundary matches.
    attributionTarget.innerHTML = Array.from(seen).join(' &middot; ');
  };
  map.on('sourcedata', updateMapAttribution);
  map.on('style.load', updateMapAttribution);

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
    if (map.getSource('regions')) return;
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
    raiseMarkerLayers();
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
  // The layer uses a dashed line so it reads visually distinct from the
  // L2 (sub-regions, solid thin blue) and L1 (major, solid heavier red)
  // outlines.  Colour uses the same neutral near-black as the selection ring
  // but at lower opacity and with a dash pattern so it reads as an informational
  // overlay rather than a selection indicator.  Inserted above
  // 'regions-line-selected' so it sits between the choropleth and the
  // selection ring in the layer stack.
  //
  // Visibility is seeded from overlayState.l3 so a page-reload with l3
  // persisted to localStorage makes the layer appear immediately.
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
          visibility: overlayState.l3 ? 'visible' : 'none',
          'line-join': 'round',
          'line-cap': 'round',
        },
        paint: {
          'line-color': '#1a6b3c',
          'line-width': 2.0,
          'line-dasharray': [4, 3],
          'line-opacity': 0.85,
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
  const applyCountryFilters = () => {
    const enabled = COUNTRY_KEYS
      .filter(code => countryState[code])
      .map(code => code.toUpperCase());
    // ['match', input, [values...], true, false] evaluates to true when the
    // feature's country property is in the enabled list, false otherwise.
    // When no countries are enabled use an always-false expression so every
    // layer empties cleanly rather than showing stale data.
    const countryFilter = enabled.length > 0
      ? ['match', ['get', 'country'], enabled, true, false]
      : ['==', false, true];
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
        : ['==', false, true];
      const base = BASE_LAYER_FILTERS['bulletin-groupings-line'];
      const composed = base ? ['all', base, arrayFilter] : arrayFilter;
      map.setFilter('bulletin-groupings-line', composed);
    }
  };

  // SNOW-172: Lazy-fetch a country's L1 + L2 + L4 GeoJSON and merge it
  // into the existing MapLibre sources. loadedCountries prevents re-fetching.
  const ensureCountryLoaded = async (code) => {
    if (loadedCountries.has(code)) return;
    const upper = code.toUpperCase();
    try {
      const [newRegions, newMajor, newSub] = await Promise.all([
        REGIONS_URL ? fetch(REGIONS_URL + '?country=' + code).then(r => {
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

      if (newMajor && newMajor.features && majorGeojsonCache) {
        majorGeojsonCache = {
          ...majorGeojsonCache,
          features: [...majorGeojsonCache.features, ...newMajor.features],
        };
        const majorSource = map.getSource('major-regions');
        if (majorSource) majorSource.setData(majorGeojsonCache);
      }

      if (newSub && newSub.features && subGeojsonCache) {
        subGeojsonCache = {
          ...subGeojsonCache,
          features: [...subGeojsonCache.features, ...newSub.features],
        };
        const subSource = map.getSource('sub-regions');
        if (subSource) subSource.setData(subGeojsonCache);
      }

      loadedCountries.add(code);

      // SNOW-239: Fetch the new country's ratings and merge them into the
      // season cache so scrubber/timelapse frames immediately include it.
      // Also paint the current visible date so new regions colour straight
      // away without waiting for the next scrubber interaction.
      if (RATINGS_URL) {
        const countryRatings = await fetch(RATINGS_URL + '?country=' + code)
          .then(r => { if (!r.ok) throw new Error('ratings fetch failed'); return r.json(); })
          .catch(() => null);
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
            const paintNewCountry = () => {
              for (const [regionID, ratingInt] of Object.entries(frame)) {
                const feature = FEATURE_BY_REGION_ID[regionID];
                if (feature) {
                  const rating = INT_TO_RATING[ratingInt] || 'no_rating';
                  MAP.setFeatureState({ source: 'regions', id: feature.id }, { rating });
                }
              }
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
    } catch (err) {
      console.warn('[map] Failed to load country', upper, err);
      // Leave toggle visually on so the user can retry — don't reset countryState.
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

  const ensureOverlayLoaded = async (key) => {
    if (overlayLoaded[key]) return;
    if (key === 'l1') {
      if (!MAJOR_REGIONS_URL) return;
      const data = await fetch(MAJOR_REGIONS_URL + '?country=ch')
        .then(r => r.json()).catch(() => null);
      if (!data) return;
      majorGeojsonCache = data;
      installOverlayLayers(majorGeojsonCache, subGeojsonCache);
    } else if (key === 'l2') {
      if (!SUB_REGIONS_URL) return;
      const data = await fetch(SUB_REGIONS_URL + '?country=ch')
        .then(r => r.json()).catch(() => null);
      if (!data) return;
      subGeojsonCache = data;
      installOverlayLayers(majorGeojsonCache, subGeojsonCache);
    } else if (key === 'l3') {
      // SNOW-323: enabling L3 is a deliberate, settled action — fetch the
      // currently-displayed day's boundary and draw it immediately (no
      // settle delay; that only applies while the scrubber is moving).
      if (!BULLETIN_GROUPINGS_URL) return;
      const dateKey = currentDisplayedDate || bootDateKey;
      const fc = await fetchBulletinGroupingsForDate(dateKey).catch(() => null);
      if (!fc) return;
      installBulletinGroupingsLayer(fc);
      currentGroupingsFC = fc;
      groupingsDrawn = true;
    } else if (key === 'resorts') {
      if (!RESORTS_GEOJSON_URL) return;
      const data = await fetch(RESORTS_GEOJSON_URL)
        .then(r => r.json()).catch(() => null);
      if (!data) return;
      resortsGeojsonCache = data;
      installResortsLayer(resortsGeojsonCache);
    } else if (key === 'favourites') {
      // SNOW-414: eligible-gated — anonymous/ineligible visitors never see
      // the toggle, but guard the fetch too in case this is ever reached
      // some other way (e.g. the eager boot-time call below).
      if (!FAVOURITES_ELIGIBLE || !FAVOURITES_URL) return;
      const data = await fetch(FAVOURITES_URL)
        .then(r => r.json()).catch(() => null);
      if (!data) return;
      installFavouritesLayer(data);
    } else if (key === 'community_reports') {
      // SNOW-419: flag-gated only (no auth eligibility) — guard the fetch
      // in case this is ever reached some other way (e.g. the eager
      // boot-time restore below).
      if (!COMMUNITY_REPORTS_ELIGIBLE || !COMMUNITY_REPORTS_URL) return;
      const data = await fetch(COMMUNITY_REPORTS_URL)
        .then(r => r.json()).catch(() => null);
      if (!data) return;
      communityReportsGeojsonCache = withCommunityReportsAgeOpacity(data);
      installCommunityReportsLayer(communityReportsGeojsonCache);
    }
    overlayLoaded[key] = true;
    // Apply country filters to the freshly-added layers so they
    // respect whichever countries are currently enabled.
    applyCountryFilters();
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
    ensureOverlayLoaded(key).then(() => {
      for (const layerId of OVERLAY_LAYER_IDS_MAIN[key]) {
        if (map.getLayer(layerId)) {
          map.setLayoutProperty(layerId, 'visibility', 'visible');
        }
      }
    }).catch(() => {});
  });

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
    if (map) {
      if (next) {
        ensureCountryLoaded(code).then(() => {
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
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (!overlayLoaded.l3) return;
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

    // SNOW-172: CH geometry is now loaded; record it and apply initial filter.
    loadedCountries.add('ch');
    applyCountryFilters();

    // SNOW-239: Paint today's choropleth via setFeatureState.
    // Gate on the 'data' event so setFeatureState calls stick —
    // if ratings resolved before the source finished loading, the calls
    // would silently no-op. The source emits 'data' with isSourceLoaded
    // once all features are available; we register a one-shot listener
    // that fires the paint loop and then removes itself.
    const paintTodayRatings = () => {
      for (const [regionID, ratingInt] of Object.entries(todayRatings)) {
        const feature = FEATURE_BY_REGION_ID[regionID];
        if (feature) {
          const rating = INT_TO_RATING[ratingInt] || 'no_rating';
          map.setFeatureState({ source: 'regions', id: feature.id }, { rating });
        }
      }
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

    // Restore any countries that were previously enabled in localStorage.
    for (const code of COUNTRY_KEYS) {
      if (code !== 'ch' && countryState[code]) {
        ensureCountryLoaded(code).catch(() => {});
      }
    }

    // SNOW-235: Restore any overlay tiers the user had enabled in a prior
    // session. These fire after the choropleth installs (not awaited) so
    // they never block first paint. There will be a brief window where the
    // choropleth is visible but the overlay is still fetching — this is
    // intentional and an improvement over the previous blocking behaviour.
    for (const key of ['l1', 'l2', 'resorts']) {
      if (overlayState[key]) ensureOverlayLoaded(key).catch(() => {});
    }

    // SNOW-414: favourites is default-ON (unlike the tiers above), so an
    // eligible user's saved pins load at boot rather than waiting for a
    // toggle. Anonymous/ineligible visitors never reach this branch —
    // ensureOverlayLoaded('favourites') also short-circuits on
    // !FAVOURITES_ELIGIBLE as a second guard.
    if (FAVOURITES_ELIGIBLE && overlayState.favourites) {
      ensureOverlayLoaded('favourites').catch(() => {});
    }

    // SNOW-419: restore the community-reports overlay if the user had it
    // enabled in a prior session. Off by default (unlike favourites), so
    // this only fires for a returning user who opted in.
    if (COMMUNITY_REPORTS_ELIGIBLE && overlayState.community_reports) {
      ensureOverlayLoaded('community_reports').catch(() => {});
    }

    // Interaction
    let selectedId = null;

    // SNOW-318: Popup state — decoupled from the selection state.
    //
    // The key design change from pre-SNOW-314: closing the popup (via ×/Esc or
    // the timelapse start) does NOT deselect the region. The highlight, pill,
    // and #CH-xxxx hash all persist. Only an empty-canvas tap truly deselects.
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
    // ``#CH-xxxx`` so the back button dismisses the popup (instead of
    // leaving the page) and so a deep link reopens the popup on load.
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

    // Re-usable selection logic. Both the map click handler and the search
    // dropdown route through this so "make this region the active one" has
    // a single definition.
    //
    // SNOW-318 decoupling: popup open/closed state is now separate from the
    // selection (highlight + pill + hash). A re-tap of the already-selected
    // region no longer toggles the selection off — instead it reopens a closed
    // popup. The toggle-closed path was removed entirely; empty-canvas tap is
    // the only deselect gesture.
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
      // SNOW-318: Re-tapping the already-highlighted region reopens a closed
      // popup without changing any selection state. Re-tapping while the popup
      // is already open is a no-op (the user sees it, nothing to do).
      if (numericId === selectedId) {
        if (!activePopup) openRegionPopup(numericId);
        return;
      }

      // Switching to a different region: drop the old highlight first, then
      // silently dismiss the old popup (summarySeq is NOT bumped here —
      // openRegionPopup bumps it at the start of its own fetch).
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

      // Open the popup above the region's north edge. Fire-and-forget — a fetch
      // failure leaves the selection (highlight/pill/hash) intact; the user can
      // re-tap to retry.
      openRegionPopup(numericId);

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

    // SNOW-414: tapping a favourite pin opens the rename/delete detail sheet
    // (favourites.js listens for this event) rather than focusing a region,
    // since a favourite isn't necessarily inside a known EAWS region.
    const activateFavourite = (feature) => {
      const props = feature.properties;
      document.dispatchEvent(new CustomEvent('snowdesk:favourite-selected', {
        detail: { uuid: props.uuid, name: props.name },
      }));
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
        // Genuine tap on empty map area (outside any region) — the only gesture
        // that both closes the popup AND deselects the region (greys the ribbon,
        // drops the readout to date-only, removes the highlight). Runs even
        // during playback (unchanged from before).
        // SNOW-318: close the popup before clearTooltip deselects the region.
        // Sequencing matters: clearTooltip resets activePopup/activePopupRegion,
        // so closePopupOnly must run first to fire the 'close' teardown while
        // those references are still live.
        closePopupOnly();
        clearTooltip();
        document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
          detail: { region_id: null, region_name: null },
        }));
        return;
      }

      // A region or resort was tapped. Timelapse playback suppresses selection
      // changes (mirrors the old regions-fill IS_PLAYING guard).
      if (IS_PLAYING) return;

      // SNOW-78: a resort pin is a proxy for its parent region — prefer its
      // region_id so a resort near a border selects the region it belongs to,
      // not merely the polygon rendered under the tap. Falls through to the
      // fill feature when no resort pin is under the point.
      const resort = features.find((f) => f.layer.id === 'resorts-pin');
      if (resort) {
        const regionID = resort.properties.region_id;
        const feature = regionID ? FEATURE_BY_REGION_ID[regionID] : null;
        if (feature) selectFeature(feature.id);
        return;
      }

      const region = features.find((f) => f.layer.id === 'regions-fill');
      if (region) selectFeature(region.id);
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

    const MAX_RESULTS = 8;

    // NFD-decompose and strip combining marks so "Évolène" matches "evolene",
    // "Graubünden" matches "graubunden", etc.
    const normalise = (s) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');

    const SEARCH_INDEX = [];

    // Track which regions are already in the index so indexRegion is safe
    // to call multiple times (e.g. from the snowdesk:regions-loaded listener
    // after a country lazy-loads — the CH entries are already present).
    const INDEXED_REGIONS = new Set();

    // Build one search entry per region. Resort names are folded into
    // the searchable string so a query for "Verbier" still returns the
    // parent region row, but they are deliberately not surfaced in the
    // rendered row — the resort list is either empty (AT/IT/FR) or
    // long enough that truncating it adds noise, so the secondary line
    // shows just the parent L2 sub-region name.
    const indexRegion = (props) => {
      const regionID = props.regionID;
      if (!regionID || INDEXED_REGIONS.has(regionID)) return;
      INDEXED_REGIONS.add(regionID);
      const name = props.name || regionID;
      const resorts = RESORTS_BY_REGION[regionID] || [];
      // subregion_name is the L2 parent's English name (e.g. "Lower
      // Valais" for CH-4115); blank for AT/IT where the fixtures store
      // the prefix as a placeholder, suppressed at the API boundary.
      const subregionName = props.subregion_name || '';
      SEARCH_INDEX.push({
        primary: name,
        secondary: regionID,
        subregionName,
        regionID,
        // Match against the region name, EAWS ID, parent L2 name, and
        // every resort name attached to the region.
        searchable: normalise([name, regionID, subregionName, ...resorts].join(' ')),
      });
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

    // Ordering: sort by EAWS region ID so results group by country
    // (AT-… before CH-… before FR-… before IT-…) and run in numeric
    // order within each country. Cap at MAX_RESULTS so the dropdown
    // stays usable on narrow viewports.
    const runSearch = (query) => {
      const q = normalise(query).trim();
      if (!q) return [];
      const hits = SEARCH_INDEX.filter(item => item.searchable.includes(q));
      hits.sort((a, b) => a.regionID.localeCompare(b.regionID));
      return hits.slice(0, MAX_RESULTS);
    };

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
      // SNOW-318: selectFeature now opens the popup automatically. If the
      // region is already selected, openRegionPopup is called directly via the
      // re-tap branch inside selectFeature — the user gets a fresh popup open.
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
      if (map.getSource('regions')) return;  // still installed on this style

      // SNOW-473: overlayState is seeded once at boot and never updated by the
      // picker (which writes localStorage + the live layer only), so re-seeding
      // layer visibility from it after a basemap swap would revert every tier to
      // its boot value. Re-sync from the localStorage shadow (the source of truth
      // the picker keeps current) before any install fn reads overlayState.
      // Mirrors the boot-seed near line 350 — keep the two in sync when adding
      // an overlay key.
      for (const key of ['l1', 'l2', 'l3', 'resorts', 'community_reports']) {
        overlayState[key] = readBoolStorage(OVERLAY_STORAGE_KEY[key], false);
      }
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
      // refetch). Unlike favourites, this overlay IS re-installed here —
      // omitting it would leave the pins vanished after a basemap swap.
      if (overlayLoaded.community_reports) {
        installCommunityReportsLayer(communityReportsGeojsonCache);
      }

      // SNOW-172: Re-apply country filters for the freshly-installed layers.
      // The caches (geojsonCache, majorGeojsonCache, subGeojsonCache) still
      // hold the merged multi-country data from before the basemap switch,
      // so we only need to re-set the filters — no re-fetch required.
      // Reset loadedCountries to just CH so ensureCountryLoaded will
      // re-merge any previously-loaded country back into the reinstalled
      // sources.
      loadedCountries.clear();
      loadedCountries.add('ch');
      // Re-merge data for any country that is currently enabled and was
      // previously loaded. geojsonCache already has the merged features but
      // the fresh source only has CH (from the reinstalled cache).  Re-fetch
      // so the source gets the full merged set again.
      const countriesToReload = COUNTRY_KEYS.filter(
        code => code !== 'ch' && countryState[code],
      );
      if (countriesToReload.length > 0) {
        Promise.all(countriesToReload.map(code => ensureCountryLoaded(code)))
          .then(() => applyCountryFilters())
          .catch(() => applyCountryFilters());
      } else {
        applyCountryFilters();
      }

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
      // Note: this string is built in JS (not from a Django template tag) so
      // the project is English-only pre-launch. When i18n is added, this
      // JS-built string will need the same treatment as the #region-readout
      // strings in seasonRibbonInit. See docs/i18n.md.
      const link = el.querySelector('.region-tooltip-bulletin-link');
      if (link) {
        link.textContent = 'Open bulletin for ' + formatDatePopup(dateKey) + ' →';
        link.href = '/' + regionID.toLowerCase() + '/' + slug + '/' + dateKey + '/';
      }

      // Update the no-bulletin date label (shown when there is no rated bulletin
      // for the date — the rated layout uses .region-tooltip-bulletin-link).
      // The template renders this as a plain <p> with inline text; there is no
      // child .region-tooltip-date element to update, so we set the full string.
      // Note: same i18n caveat as the bulletin link above.
      const noBulletin = el.querySelector('.region-tooltip-no-bulletin');
      if (noBulletin) {
        noBulletin.textContent = 'No bulletin available for ' + formatDatePopup(dateKey);
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
  const pctToDateKey = (pct) => {
    const ms = seasonStartMs + (pct / 100) * seasonSpanMs;
    const day = new Date(ms);
    // Snap to UTC midnight to dodge DST edges, then format.
    const y = day.getUTCFullYear();
    const m = String(day.getUTCMonth() + 1).padStart(2, '0');
    const d = String(day.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  };
  const dateKeyToPct = (dateKey) => {
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
    if (loadingEl) loadingEl.textContent = 'Season data unavailable';
  });

  const snapToNearestDataDay = (dateKey) => {
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
      repaintRegionsForDate(snapped, ratingsCache);
    }
  };

  track.addEventListener('pointerdown', (e) => {
    dragging = true;
    pointerId = e.pointerId;
    track.classList.add('dragging');
    track.classList.remove('animating');
    updateDragVisuals(e.clientX);
    e.preventDefault();
  });

  document.addEventListener('pointermove', (e) => {
    if (!dragging || e.pointerId !== pointerId) return;
    updateDragVisuals(e.clientX);
  });

  const release = (e) => {
    if (!dragging || (e && e.pointerId !== pointerId)) return;
    dragging = false;
    pointerId = null;
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

  // SNOW-314: ribbon click-to-scrub — the season ribbon dispatches this
  // event when a day cell is clicked; commitDate drives the scrubber thumb
  // and repaints the choropleth just as a drag-release would.
  document.addEventListener('snowdesk:scrub-to', (e) => {
    commitDate(e.detail.date);
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
    if (!sortedDates || sortedDates.length === 0) return 0;
    const ariaNow = scrubber ? parseFloat(scrubber.getAttribute('aria-valuenow')) : NaN;
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
    playButton.setAttribute('aria-label', 'Play season timelapse');
    if (reverseButton) {
      reverseButton.dataset.state = 'stopped';
      reverseButton.setAttribute('aria-label', 'Play season timelapse in reverse');
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
      playButton.setAttribute('aria-label', 'Stop season timelapse');
      if (reverseButton) {
        reverseButton.dataset.state = 'stopped';
        reverseButton.setAttribute('aria-label', 'Play season timelapse in reverse');
      }
    } else {
      if (reverseButton) {
        reverseButton.dataset.state = 'playing';
        reverseButton.setAttribute('aria-label', 'Stop reverse timelapse');
      }
      playButton.dataset.state = 'stopped';
      playButton.setAttribute('aria-label', 'Play season timelapse');
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

// Date pill — floats above the scrubber thumb inside .season-scrubber-track.
// Server-rendered for first-paint correctness; this IIFE keeps both the
// horizontal position (via --thumb-pct) and the text content in sync as
// the user scrubs or the timelapse advances.
(function mapDatePillInit() {
  const pill = document.getElementById('map-date-pill');
  if (!pill) return;

  // Read season bounds once — the same constants used by seasonScrubberInit
  // and timelapseInit. The pill uses them to compute the thumb percentage
  // for any incoming date key without needing a reference to the thumb DOM.
  const scrubber = document.getElementById('season-scrubber');
  const seasonStartMs = scrubber ? Date.parse(scrubber.dataset.seasonStart) : NaN;
  const seasonEndMs = scrubber ? Date.parse(scrubber.dataset.seasonEnd) : NaN;
  const seasonSpanMs = seasonEndMs - seasonStartMs;
  const todayPct = scrubber ? parseFloat(scrubber.dataset.todayPct) : 50;

  const dateKeyToPct = (dateKey) => {
    const ms = Date.parse(dateKey);
    if (Number.isNaN(ms) || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) {
      return todayPct;
    }
    return Math.max(0, Math.min(100, ((ms - seasonStartMs) / seasonSpanMs) * 100));
  };

  const setFrom = (e) => {
    const dk = e.detail && e.detail.date;
    if (!dk) return;
    // Update text content so the pill always shows the correct date.
    pill.textContent = formatDateLong(dk);
    // Slide the pill horizontally to track the thumb.
    const pct = dateKeyToPct(dk);
    pill.style.setProperty('--thumb-pct', pct + '%');
  };

  // Both events carry the same shape; date-changed fires on commit
  // (scrubber release, timelapse frame, popstate), date-preview fires
  // continuously during a drag so the pill follows the thumb live.
  document.addEventListener('snowdesk:date-changed', setFrom);
  document.addEventListener('snowdesk:date-preview', setFrom);
})();

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

  const STORAGE_KEY = 'snowdesk.map.basemap';

  const setMenuOpen = (open) => {
    pill.dataset.state = open ? 'expanded' : 'collapsed';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    menu.hidden = !open;
  };

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
    // SNOW-323: l3 — bulletin groupings, lazily loaded on first toggle.
    l3: ['bulletin-groupings-line'],
    l4: ['regions-fill', 'regions-line', 'regions-label'],
    resorts: ['resorts-pin', 'resorts-label'],
    favourites: ['favourites-pin', 'favourites-label'],
    community_reports: [
      'community-reports-clusters',
      'community-reports-cluster-count',
      'community-reports-point',
    ],
  };
  const OVERLAY_STORAGE_KEY = {
    l1: 'snowdesk.map.overlay.l1',
    l2: 'snowdesk.map.overlay.l2',
    l3: 'snowdesk.map.overlay.l3',
    l4: 'snowdesk.map.overlay.l4',
    resorts: 'snowdesk.map.overlay.resorts',
    favourites: 'snowdesk.map.overlay.favourites',
    community_reports: 'snowdesk.map.overlay.community_reports',
  };

  for (const item of items) {
    item.addEventListener('click', (e) => {
      e.stopPropagation();

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

        // Tier overlay — toggle layer visibility.
        writeStorage(OVERLAY_STORAGE_KEY[overlayKey], String(next));
        if (MAP) {
          if (next && (overlayKey === 'l1' || overlayKey === 'l2' || overlayKey === 'l3' || overlayKey === 'resorts' || overlayKey === 'favourites' || overlayKey === 'community_reports')) {
            // SNOW-235: First enable of a lazy overlay tier — delegate to the
            // main IIFE via snowdesk:overlay-load so it can fetch the GeoJSON,
            // install the layers, and then make them visible. The main IIFE
            // listener handles both the fetch and the setLayoutProperty call,
            // so we return here without running the direct visibility loop.
            // SNOW-323: l3 (bulletin groupings) is also lazy — same pattern.
            document.dispatchEvent(new CustomEvent('snowdesk:overlay-load', {
              detail: { key: overlayKey },
            }));
          } else {
            // Toggling off, or toggling a non-lazy tier (l4): use the direct
            // setLayoutProperty path. For the lazy tiers toggling off, the
            // layer may not exist yet (if the user enabled then immediately
            // disabled before the fetch resolved) — getLayer guards cover this.
            for (const layerId of OVERLAY_LAYER_IDS[overlayKey]) {
              if (MAP.getLayer(layerId)) {
                MAP.setLayoutProperty(
                  layerId, 'visibility', next ? 'visible' : 'none',
                );
              }
            }
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
      // are independent and shouldn't be cleared when the basemap swaps.
      for (const other of items) {
        if (other.dataset.overlayKey) continue;
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

// SNOW-65: auto-zoom toggle — now a menuitemcheckbox inside the layers
// menu rather than a standalone icon button.
(function autozoomToggleInit() {
  const btn = document.getElementById('autozoom-toggle');
  if (!btn) return;

  const STORAGE_KEY = 'snowdesk.map.autozoom';

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
  const readoutDate =
    readoutEl && readoutEl.querySelector('.region-readout-date');
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
  const intToKey = (n) => {
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

  // Update the persistent readout. Always shows the scrubbed date (a minimal
  // date display even with no region focused); when a region IS focused it also
  // shows the region name and a danger-coloured swatch. Pure in-memory lookup
  // (no fetch), so it is safe to call on every scrub/preview/playback frame.
  const updateReadout = () => {
    if (!readoutEl) return;
    readoutEl.hidden = !dateKey;
    if (!dateKey) return;
    // SNOW-314 prototype: day-first, title-case date ("18 May 2026") matching
    // the popup card; deliberately not the uppercase scrubber format.
    if (readoutDate) readoutDate.textContent = formatDatePopup(dateKey);
    const hasRegion = !!(regionId && regionName);
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
