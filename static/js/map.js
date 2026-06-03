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

// "2026-04-25" → "APR 25 2026". Locale-friendly, unambiguous (avoids the
// 04/05 day-vs-month confusion of all-numeric formats). Uppercase to
// match the season-bookend labels and the server-rendered date pill.
const SCRUBBER_MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                         'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const formatDateLong = (dateKey) => {
  const [y, m, d] = dateKey.split('-');
  return `${SCRUBBER_MONTHS[parseInt(m, 10) - 1]} ${parseInt(d, 10)} ${y}`;
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
  // The summary URL carries the literal placeholder XX-0000 which is
  // substituted with the tapped region's region_id at fetch time. Server
  // renders this via {% url 'api:region_summary' 'XX-0000' %} so the
  // route name stays the single source of truth. XX-0000 passes the
  // RegionIdConverter regex while being an obviously non-existent ID.
  const REGION_SUMMARY_URL_TEMPLATE = mapEl.dataset.regionSummaryUrl;
  // SNOW-239: Hand the ratings URL to module scope so the timelapse and
  // scrubber IIFEs (defined further down in this file) can share one
  // full-season fetch via getSeasonRatings().
  RATINGS_URL = mapEl.dataset.ratingsUrl;

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
  let storedBasemapKey = null;
  try { storedBasemapKey = localStorage.getItem(BASEMAP_STORAGE_KEY); }
  catch (_) { /* private mode / disabled storage — fall through */ }
  const initialBasemapKey = (storedBasemapKey && BASEMAP_OPTIONS[storedBasemapKey])
    ? storedBasemapKey
    : DEFAULT_BASEMAP_KEY;
  const initialBasemapUrl = BASEMAP_OPTIONS[initialBasemapKey];
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

  // SNOW-59: EAWS region overlay layers — three tiers stacked above
  // the basemap. L1 (Major) and L2 (Sub) are outline-only line layers;
  // L4 (Micro) is the data-bearing choropleth and stays on permanently
  // (the user-facing checkbox is rendered checked-and-disabled).
  // Visibility is user-driven via the basemap picker popover and
  // persisted in localStorage; the ``style.load`` handler re-applies
  // it after a basemap swap.
  const OVERLAY_STORAGE_KEY = {
    l1: 'snowdesk.map.overlay.l1',
    l2: 'snowdesk.map.overlay.l2',
    l4: 'snowdesk.map.overlay.l4',
    resorts: 'snowdesk.map.overlay.resorts',
  };
  // L4 defaults to true and is force-locked below — the choropleth is
  // the entire point of the page, so toggling it off would leave the
  // map empty. SNOW-78 resorts default off so the map opens uncluttered.
  const overlayState = { l1: false, l2: false, l4: true, resorts: false };
  for (const key of ['l1', 'l2', 'resorts']) {
    try {
      overlayState[key] =
        localStorage.getItem(OVERLAY_STORAGE_KEY[key]) === 'true';
    } catch (_) { /* private mode — default off */ }
  }
  // Persist the L4 default once so localStorage shows a complete
  // picture of the popover's state to anyone debugging.
  try { localStorage.setItem(OVERLAY_STORAGE_KEY.l4, 'true'); }
  catch (_) { /* private mode — fall through */ }

  // SNOW-172: Country toggle state — which country's geometry is shown.
  // Default: CH on, others off. Each key maps to a boolean (visible/hidden).
  // Persisted in localStorage under snowdesk.map.overlay.country.<code>.
  const COUNTRY_KEYS = ['ch', 'fr', 'at', 'it'];
  const COUNTRY_STORAGE_KEY = (code) => `snowdesk.map.overlay.country.${code}`;
  const countryState = { ch: true, fr: false, at: false, it: false };
  for (const code of COUNTRY_KEYS) {
    try {
      const stored = localStorage.getItem(COUNTRY_STORAGE_KEY(code));
      if (stored !== null) countryState[code] = stored === 'true';
    } catch (_) { /* private mode — use defaults */ }
  }
  // SNOW-236: Mirror the initial state into the module-scope COUNTRY_STATE
  // so the scrubber IIFE can read it for country-aware effective-last computation.
  Object.assign(COUNTRY_STATE, countryState);
  // loadedCountries tracks which countries' GeoJSON has been fetched already
  // so we don't re-fetch on each toggle-on.
  const loadedCountries = new Set();

  // SNOW-63: restore auto-zoom preference from localStorage.
  try { AUTOZOOM = localStorage.getItem('snowdesk.map.autozoom') === 'true'; }
  catch (_) { /* private mode — default off */ }
  // Reflect the persisted overlay state on first paint so the popover
  // matches reality before the click handler at the bottom of the file
  // takes over. The L4 button is disabled in markup, so we just
  // confirm aria-checked="true" without making it clickable.
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
    style: initialBasemapUrl,
    bounds: [[5.9, 45.8], [10.5, 47.9]],
    fitBoundsOptions: { padding: 20 },
    minZoom: 4,
    maxZoom: 12,
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

  // SNOW-58: source + layer install, factored out so it can be re-applied
  // after MAP.setStyle() wipes the style. Idempotent — refuses to re-add
  // if the source is still around (defensive, MapLibre normally drops
  // sources during setStyle but this lets a future ``diff`` setStyle
  // strategy land without breaking us).
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
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      paint: {
        'fill-color': [
          'match',
          ['feature-state', 'rating'],
          'low',          RATING_COLOURS.low,
          'moderate',     RATING_COLOURS.moderate,
          'considerable', RATING_COLOURS.considerable,
          'high',         RATING_COLOURS.high,
          'very_high',    RATING_COLOURS.very_high,
          RATING_COLOURS.no_rating,
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
        'text-field': ['get', 'name'],
        'text-font': ['Noto Sans Regular'],
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
          'text-font': ['Noto Sans Bold'],
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
          'text-font': ['Noto Sans Bold'],
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
        'text-font': ['Noto Sans Regular'],
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
  };

  // Cached at IIFE scope so the style.load handler (registered inside
  // map.on('load') below) can re-install layers without a refetch when
  // the user picks a new basemap.
  let geojsonCache = null;
  let majorGeojsonCache = null;
  let subGeojsonCache = null;
  let resortsGeojsonCache = null;

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
            const displayDate = (() => {
              const d = new URL(location.href).searchParams.get('d');
              return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
            })();
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
  const overlayLoaded = { l1: false, l2: false, resorts: false };

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
    } else if (key === 'resorts') {
      if (!RESORTS_GEOJSON_URL) return;
      const data = await fetch(RESORTS_GEOJSON_URL)
        .then(r => r.json()).catch(() => null);
      if (!data) return;
      resortsGeojsonCache = data;
      installResortsLayer(resortsGeojsonCache);
    }
    overlayLoaded[key] = true;
    // Apply country filters to the freshly-added L1/L2 layers so they
    // respect whichever countries are currently enabled.
    applyCountryFilters();
  };

  // SNOW-235: Layer IDs for the lazily-loaded overlay tiers, restricted
  // to l1 / l2 / resorts (l4 / Micro regions is always-on and not lazy).
  // Mirrors OVERLAY_LAYER_IDS in basemapPickerInit but scoped here so
  // the snowdesk:overlay-load handler below can reach them without
  // crossing IIFE boundaries.
  const OVERLAY_LAYER_IDS_MAIN = {
    l1: ['major-regions-line', 'major-regions-label'],
    l2: ['sub-regions-line', 'sub-regions-label'],
    resorts: ['resorts-pin', 'resorts-label'],
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
    try {
      localStorage.setItem(COUNTRY_STORAGE_KEY(code), String(next));
    } catch (_) { /* private mode */ }
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
  let currentDisplayedDate = (() => {
    const d = new URL(location.href).searchParams.get('d');
    return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
  })();

  // Forward references to map.on('load')-scoped functions.  Populated by the
  // map-load callback after the full popup machinery is defined.  The defaults
  // below are intentional no-ops so the listeners registered before map.on('load')
  // can call through without a null-guard — in environments where the map never
  // loads (e.g. Playwright offline headless), no popup is ever open so both
  // functions return immediately, which is the correct observable behaviour.
  let _clearTooltip = () => {};
  let _refreshActivePopupForDate = async (_dateKey) => {};

  // SNOW-240: Test hook — active only when window.__SNOWDESK_TEST_MODE__ is
  // set before the page JS runs (via Playwright's page.add_init_script).
  // Exposes setRefreshFn() and setIsPlaying() so tests can inject a spy into
  // the forwarding variable and control the IS_PLAYING flag directly, making
  // the IS_PLAYING guard observable without needing the basemap to load or
  // the timelapse start()/stop() functions to execute.
  // In production (no test-mode flag) this entire block is dead and adds no
  // closures, no globals, and no measurable overhead.
  if (window.__SNOWDESK_TEST_MODE__) {
    window.__snowdesk_test = {
      // Replace _refreshActivePopupForDate with a caller-supplied function.
      // The date-changed listener calls through this variable; injecting a
      // spy here lets tests observe whether the IS_PLAYING guard allows or
      // blocks the call.
      setRefreshFn: (fn) => { _refreshActivePopupForDate = fn; },
      // Read IS_PLAYING so tests can assert state-machine transitions.
      getIsPlaying: () => IS_PLAYING,
      // Set IS_PLAYING directly so tests can control the guard without
      // invoking the timelapse start()/stop() functions (which require a
      // loaded map and populated sortedDates).
      setIsPlaying: (val) => { IS_PLAYING = val; },
    };
  }

  // SNOW-47 / SNOW-174: keep currentDisplayedDate in sync and refresh the
  // open popup when the scrubber commits a new date. If a popup is open,
  // swap its HTML to reflect the new day's danger rating without closing and
  // re-opening it. During timelapse playback the popup is suppressed, so skip
  // the API call — just track the date.
  //
  // Registered at outer-IIFE scope (not inside map.on('load')) so this
  // listener is active in offline headless test environments where MapLibre's
  // 'load' event never fires.
  document.addEventListener('snowdesk:date-changed', (e) => {
    currentDisplayedDate = (e.detail && e.detail.date) || null;
    if (!IS_PLAYING) _refreshActivePopupForDate(currentDisplayedDate);
  });

  // Dismiss the open popup at the very start of timelapse playback so
  // per-frame /api/region/<id>/summary/ requests are not fired while the
  // choropleth animates through the season.
  //
  // Registered at outer-IIFE scope (not inside map.on('load')) so this
  // listener is active in offline headless test environments where MapLibre's
  // 'load' event never fires.
  document.addEventListener('snowdesk:timelapse-state', (e) => {
    if (e.detail && e.detail.playing) _clearTooltip();
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

    // Interaction
    let selectedId = null;
    // Tracks the most recent inflight summary fetch so a slow tap-A
    // followed by a fast tap-B never lets A's response overwrite B's.
    let summarySeq = 0;
    // The currently-open MapLibre Popup, or null when none is open.
    let activePopup = null;

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

    // Return the lng/lat centre of a feature's bbox — the anchor for the
    // MapLibre Popup so it floats over the region area.
    const featureCentre = (feature) => {
      const [[w, s], [e, n]] = featureBBox(feature);
      return [(w + e) / 2, (s + n) / 2];
    };

    // DOM-only popup teardown. Called by clearTooltip (user-facing dismiss)
    // and by the popup's own 'close' event (Esc, ×-button, outside-click
    // on the map). Does not push history — callers that need URL sync call
    // clearTooltip instead.
    //
    // IMPORTANT: null activePopup *before* calling p.remove(). MapLibre fires
    // the popup's 'close' event synchronously inside remove(), which triggers
    // clearTooltip() → clearPopupDom() again. Nulling first makes the guard
    // on the second entry a no-op, so side-effects only run once.
    const clearPopupDom = () => {
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
        // SNOW-174: triggerRepaint ensures the regions-line-selected layer
        // (which reads feature-state via its paint line-opacity expression)
        // redraws immediately rather than waiting for the next idle frame.
        map.triggerRepaint();
        selectedId = null;
      }
      summarySeq++;  // invalidate any inflight fetch so it can't reopen the popup
      if (activePopup) {
        const p = activePopup;
        activePopup = null;
        p.remove();
      }
    };

    // User-facing dismiss path. Keep the URL hash and popup state in
    // lockstep:
    //
    //   - When we pushed the current history entry, pop it via
    //     ``history.back()``. The popstate handler then dispatches into
    //     ``clearPopupDom`` (re-entry guarded by ``popstateInProgress``).
    //   - When the current entry is the one the user landed on
    //     (``/map/#CH-xxxx`` from the bulletin page, or a hash typed in
    //     the URL bar before the listener attached), popping would
    //     navigate them off the page. Clear the hash via
    //     ``replaceState`` and tear the popup down directly.
    const clearTooltip = () => {
      if (popstateInProgress) {
        clearPopupDom();
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
      clearPopupDom();
    };
    // Publish clearTooltip to the outer-IIFE forwarding variable so the
    // listeners registered before map.on('load') can reach it.
    _clearTooltip = clearTooltip;

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

    // Silent dismissal helper — removes the current popup without bumping
    // summarySeq. Used for region-to-region transitions where the caller
    // immediately starts a new fetch (summarySeq++ happens there). The
    // 'close' listener is detached first so clearTooltip is not triggered,
    // which would bump summarySeq a second time and invalidate the new fetch.
    const dismissActivePopupSilently = () => {
      if (!activePopup) return;
      const p = activePopup;
      activePopup = null;
      p.off('close', clearTooltip);
      p.remove();
    };

    // Fetch the server-rendered tooltip HTML for a region, open a
    // MapLibre Popup anchored to the click point (when supplied) or the
    // region's bbox centre (deep-link / resort-pin path), and wire its
    // 'close' event back to clearTooltip so Esc / × / outside-map clicks
    // all reset the URL hash. The summarySeq guard discards stale
    // responses when the user taps a different region mid-flight.
    // Returns true on success, false on 404 / network error.
    //
    // closeOnClick: false — we manage popup lifetime explicitly.
    // dismissActivePopupSilently() removes any open popup before the fetch
    // starts; this avoids the race where closeOnClick's 'close' event would
    // bump summarySeq after the new fetch's seq is already captured.
    const loadRegionSummary = async (regionID, { dateKey, clickPoint } = {}) => {
      if (!REGION_ID_RE.test(regionID)) return false;
      dismissActivePopupSilently();
      let url = REGION_SUMMARY_URL_TEMPLATE.replace(
        'XX-0000', encodeURIComponent(regionID),
      );
      if (dateKey) url += '?d=' + encodeURIComponent(dateKey);
      const seq = ++summarySeq;
      try {
        const resp = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (seq !== summarySeq) return false;  // a newer tap won the race
        if (!resp.ok) return false;
        const data = await resp.json();
        if (seq !== summarySeq) return false;
        const feature = FEATURE_BY_REGION_ID[regionID];
        const anchor = clickPoint || (feature ? featureCentre(feature) : null);
        if (!anchor) return false;
        // Server-trusted HTML: rendered by Django templates with all
        // user-supplied values escaped by autoescape — safe for setHTML.
        const popup = new maplibregl.Popup({
          closeButton: true,
          closeOnClick: false,
          // MapLibre defaults to focusing the popup's first focusable
          // element on open, which on a deep-link arrival
          // (``/map/#CH-xxxx`` from a bulletin page) yanks focus onto
          // the bulletin CTA inside the popup and renders an obvious
          // focus ring around it. The popup is opened in response to
          // pointer / hash navigation, not keyboard activation, so the
          // ring is just visual noise — the close button is still
          // Tab-reachable for keyboard users.
          focusAfterOpen: false,
          // SNOW-174: use 'bottom' so the popup tip always points down to
          // the tap point and the body floats above it. 'auto' can flip the
          // popup to an unexpected side when near the viewport edge, and it
          // was the root cause of the popup landing at (0, 0) under
          // synthetic click events (the edge-flip path sets no position).
          anchor: 'bottom',
          maxWidth: 'min(320px, calc(100vw - 32px))',
          className: 'region-popup',
        });
        // SNOW-174: set HTML before lngLat so MapLibre can compute correct
        // DOM dimensions when _update runs. Chain order matters: setHTML →
        // setLngLat → addTo.
        popup.setHTML(data.html).setLngLat(anchor).addTo(map);
        // Force immediate positioning — MapLibre's _update normally runs on
        // the next rAF tick, but that can lag perceptibly on heavy renders.
        // Calling it directly snaps the popup to its anchor on the same
        // frame. _update is a private method (acknowledged trade-off); it
        // has been stable across MapLibre v3/v4 and is the standard escape
        // hatch for this timing issue.
        if (typeof popup._update === 'function') popup._update();
        // Stamp the rating level on the popup root so map.css can drive
        // the border colour via the EAWS token matching data-level.
        const el = popup.getElement();
        if (el) el.setAttribute('data-level', data.level || 'no_rating');
        activePopup = popup;
        // Wire MapLibre's own close event (Esc, ×-button, canvas click)
        // back to our dismiss path so the URL hash is always cleared on
        // close, regardless of which gesture the user used.
        popup.on('close', clearTooltip);
        return true;
      } catch (_err) {
        return false;
      }
    };

    // Fetch fresh tooltip HTML for the currently-open popup without
    // re-creating it — swap only the inner HTML. Used by the
    // snowdesk:date-changed listener when the scrubber commits a new
    // date while a popup is already open. Early-returns when no popup
    // is open or no region is selected.
    const refreshActivePopupForDate = async (dateKey) => {
      if (!activePopup || selectedId === null) return;
      const props = REGION_LOOKUP[selectedId];
      if (!props) return;
      const regionID = props.regionID;
      if (!REGION_ID_RE.test(regionID)) return;
      let url = REGION_SUMMARY_URL_TEMPLATE.replace(
        'XX-0000', encodeURIComponent(regionID),
      );
      if (dateKey) url += '?d=' + encodeURIComponent(dateKey);
      try {
        const resp = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!resp.ok) return;
        const data = await resp.json();
        // Guard: popup may have been closed while the fetch was in flight.
        if (activePopup) activePopup.setHTML(data.html);
      } catch (_err) { /* silently ignore refresh errors */ }
    };
    // Publish to the outer-IIFE forwarding variable so the date-changed
    // listener registered before map.on('load') can reach it.
    _refreshActivePopupForDate = refreshActivePopupForDate;

    // Re-usable selection logic. Both the map click handler and the search
    // dropdown route through this so "make this region the active one" has
    // a single definition. ``toggle`` mirrors the map-click UX where a
    // second click on the already-selected region dismisses the popup;
    // search callers pass ``toggle: false`` so selecting a result always
    // opens it, never toggles it off. ``urlMode`` controls how the URL
    // hash is reconciled after the popup opens: ``'push'`` (default,
    // user-initiated) writes the hash via push/replaceState; ``'mark'``
    // skips the write because the URL already matches (popstate,
    // hashchange, initial load) and just records that our hash is now
    // the active history entry. ``clickPoint`` is the lngLat of the
    // click event, used as the popup anchor; absent for deep-link and
    // search paths (falls back to region bbox centre).
    const selectFeature = async (
      numericId,
      { toggle = true, urlMode = 'push', clickPoint } = {},
    ) => {
      if (numericId === selectedId) {
        if (toggle) clearTooltip();
        return;
      }
      if (selectedId !== null) {
        map.setFeatureState({ source: 'regions', id: selectedId }, { selected: false });
      }
      selectedId = numericId;
      map.setFeatureState({ source: 'regions', id: selectedId }, { selected: true });
      // SNOW-174: trigger an immediate repaint so the regions-line-selected
      // layer (paint line-opacity reads feature-state) activates on this frame.
      map.triggerRepaint();

      const props = REGION_LOOKUP[numericId];
      const ok = await loadRegionSummary(props.regionID, {
        dateKey: currentDisplayedDate,
        clickPoint,
      });
      // If the user dismissed (or selected a different region) while the
      // fetch was in flight, selectedId may no longer match. Bail out
      // without updating the URL.
      if (selectedId !== numericId) return;
      if (!ok) {
        // 404 or network failure — clear the outline and leave popup closed.
        clearTooltip();
        return;
      }

      if (urlMode === 'push') {
        syncUrlForRegion(props.regionID);
      } else if (urlMode === 'mark') {
        popupHistoryOpen = true;
      }

      if (AUTOZOOM) {
        const feature = FEATURE_BY_ID[numericId];
        if (feature) {
          map.fitBounds(featureBBox(feature), {
            padding: { top: 60, right: 40, bottom: 40, left: 40 },
            maxZoom: 10,
            duration: 400,
          });
        }
      }
    };

    map.on('click', 'regions-fill', (e) => {
      if (!e.features.length) return;
      if (IS_PLAYING) return;
      // Pass the click's lngLat so the popup opens over the tapped point,
      // not the region bbox centre. dismissActivePopupSilently() at the top
      // of loadRegionSummary handles swapping away any existing popup without
      // bumping summarySeq, so region-to-region transitions open in one tap.
      selectFeature(e.features[0].id, { clickPoint: e.lngLat });
    });

    // Double-click always zooms to the region regardless of AUTOZOOM setting,
    // and prevents the default map double-click zoom so we control the target.
    map.on('dblclick', 'regions-fill', (e) => {
      e.preventDefault();
      if (!e.features.length) return;
      const feature = FEATURE_BY_ID[e.features[0].id];
      if (feature) {
        map.fitBounds(featureBBox(feature), {
          padding: { top: 60, right: 40, bottom: 40, left: 40 },
          maxZoom: 10,
          duration: 400,
        });
      }
    });

    // Dismiss the popup when the user taps empty map area (no region or resort
    // feature under the cursor). This is the generic map click — MapLibre fires
    // it AFTER layer-scoped clicks, so by the time it runs the layer handler
    // has already called loadRegionSummary → dismissActivePopupSilently and
    // started a new fetch. queryRenderedFeatures returns non-empty for those
    // clicks, so this handler only fires for true empty-area taps.
    //
    // SNOW-235: the resorts-pin layer is now lazy-installed, so it may not
    // exist at query time. queryRenderedFeatures throws on any unknown layer
    // id, so filter the list to layers currently present on the map.
    map.on('click', (e) => {
      const layers = ['regions-fill', 'resorts-pin'].filter(
        (id) => map.getLayer(id),
      );
      if (!layers.length) return;
      const features = map.queryRenderedFeatures(e.point, { layers });
      if (features.length === 0) clearTooltip();
    });

    map.on('mouseenter', 'regions-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'regions-fill', () => { map.getCanvas().style.cursor = ''; });

    // SNOW-78: tapping a resort pin opens the region tooltip for the
    // resort's parent region. Pass the pin's lngLat as clickPoint so
    // the popup anchors over the pin rather than the region centre.
    map.on('click', 'resorts-pin', (e) => {
      if (!e.features.length) return;
      const regionID = e.features[0].properties.region_id;
      if (!regionID) return;
      const feature = FEATURE_BY_REGION_ID[regionID];
      if (feature) selectFeature(feature.id, { toggle: false, clickPoint: e.lngLat });
    });
    map.on('mouseenter', 'resorts-pin', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'resorts-pin', () => { map.getCanvas().style.cursor = ''; });

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
          selectFeature(numericId, { toggle: false, urlMode: 'mark' });
        } else {
          popupHistoryOpen = false;
          popupHashWasPushed = false;
          clearPopupDom();
        }
      } finally {
        popstateInProgress = false;
      }
    });

    // hashchange fires when the user edits the fragment in the URL bar.
    // (popstate also fires for back/forward — both events fire for that
    // case and the second one is a harmless no-op because selectFeature
    // returns early when numericId === selectedId, and clearPopupDom is
    // idempotent.)
    window.addEventListener('hashchange', () => {
      const numericId = featureIdFromHash();
      if (numericId !== null) {
        popupHistoryOpen = true;
        // A hashchange adds a real history entry (unlike the initial-load
        // hash, which is part of the entry the user landed on), so a
        // subsequent close can safely pop it.
        popupHashWasPushed = true;
        selectFeature(numericId, { toggle: false, urlMode: 'mark' });
      } else if (location.hash === '' || location.hash === '#') {
        popupHistoryOpen = false;
        popupHashWasPushed = false;
        clearPopupDom();
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
      // Force a fresh open even if the region is already the selected one —
      // the user clearly wants to see it, not toggle it off.
      selectFeature(feature.id, { toggle: false });
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

    // SNOW-174: dismiss the popup on clicks that land outside both the
    // popup element and the map canvas — covers header, scrubber, and
    // page attribution which the MapLibre closeOnClick: true does not
    // catch (closeOnClick only fires for canvas clicks).
    document.addEventListener('pointerdown', (e) => {
      if (!activePopup) return;
      const popupEl = activePopup.getElement ? activePopup.getElement() : null;
      if (popupEl && popupEl.contains(e.target)) return;
      if (e.target.closest('.maplibregl-canvas-container')) return;
      clearTooltip();
    });

    // ---- Initial-load hash → popup (SNOW-39) ----
    //
    // If the user landed on ``/map/#CH-xxxx``, open the popup for that
    // region. ``urlMode: 'mark'`` because the URL already matches —
    // selectFeature just needs to record that our hash is the active
    // history entry. Unknown / malformed hashes are silently ignored.
    const initialFeatureId = featureIdFromHash();
    if (initialFeatureId !== null) {
      selectFeature(initialFeatureId, { toggle: false, urlMode: 'mark' });
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
      const dateKey = new URL(location.href).searchParams.get('d');
      if (dateKey && /^\d{4}-\d{2}-\d{2}$/.test(dateKey)) {
        getSeasonRatings()
          .then((ratings) => repaintRegionsForDate(dateKey, ratings))
          .catch(() => { /* network fail → leave today's colours */ });
      }
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
    const bootParam = new URL(location.href).searchParams.get('d');
    if (!bootParam) {
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
      // ``?d=`` param entirely, matching the canonical /map/ URL.
      const search = isToday ? '' : '?d=' + dateKey;
      history.replaceState(null, '', '/map/' + search + location.hash);
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
  const bootDate = new URL(location.href).searchParams.get('d');
  if (bootDate && /^\d{4}-\d{2}-\d{2}$/.test(bootDate) && isInSeason(bootDate)) {
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
    const d = new URL(location.href).searchParams.get('d');
    // SNOW-236: fall back to effectiveTodayKey (country-aware last populated
    // date) rather than todayKey so back-nav restores a coloured choropleth
    // when today is past the season end.
    const target = d && /^\d{4}-\d{2}-\d{2}$/.test(d) && isInSeason(d) ? d : effectiveTodayKey;
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
    const bootParam = new URL(location.href).searchParams.get('d');
    if (!bootParam && newEffective !== prevEffective) {
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

  let initial = 'collapsed';
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'expanded') initial = 'expanded';
  } catch (_) { /* private mode / disabled storage — fall through */ }
  applyState(initial);

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const next = root.dataset.state === 'expanded' ? 'collapsed' : 'expanded';
    applyState(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch (_) {}
  });

  // Outside-tap dismiss: any click outside the legend container collapses
  // it. Inside-card clicks bubble harmlessly; the toggle stops propagation
  // above so its own click is not treated as "outside".
  document.addEventListener('click', (e) => {
    if (root.dataset.state !== 'expanded') return;
    if (root.contains(e.target)) return;
    applyState('collapsed');
    try { localStorage.setItem(STORAGE_KEY, 'collapsed'); } catch (_) {}
  });
})();

// Season timelapse — four transport buttons on the scrubber:
//   |<  skip to season start
//   >   play at 1× from current thumb position (second press = stop)
//   >>  play at 2× from current thumb position (second press = stop)
//   >|  skip to season end
//
// Each frame repaints region colours via feature-state and announces a
// snowdesk:date-changed event so the date pill stays in sync. Pressing
// the other speed button mid-playback switches speed without losing
// the current frame index.
(function timelapseInit() {
  const playButton = document.getElementById('scrubber-play');
  if (!playButton) return;

  // BASE_FRAME_MS gives ~10 fps at 1×; 2× halves the interval.
  // The 10 ms floor prevents a future multiplier from starving the thread.
  const BASE_FRAME_MS = 200;

  // Drive the scrubber thumb so playback position is visible.
  const scrubber = document.getElementById('season-scrubber');
  const scrubberThumb = scrubber ? scrubber.querySelector('.season-scrubber-thumb') : null;
  const seasonStartMs = scrubber ? Date.parse(scrubber.dataset.seasonStart) : NaN;
  const seasonEndMs = scrubber ? Date.parse(scrubber.dataset.seasonEnd) : NaN;
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  const fastButton = document.getElementById('scrubber-fast');
  const skipStartButton = document.getElementById('scrubber-skip-start');
  const skipEndButton = document.getElementById('scrubber-skip-end');

  // Active playback speed — set per button click (1 for play, 2 for fast).
  let speed = 1;

  const frameMs = () => Math.max(10, Math.round(BASE_FRAME_MS / speed));

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

  // Hoisted so start() can re-arm setInterval at a new speed without
  // losing the current frame index.
  const tick = () => {
    frameIdx += 1;
    if (frameIdx >= sortedDates.length) {
      // Last frame already painted — stop so the final value settles.
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
    // Reset data-state on both transport buttons.
    playButton.dataset.state = 'stopped';
    playButton.setAttribute('aria-label', 'Play season timelapse');
    if (fastButton) {
      fastButton.dataset.state = 'stopped';
      fastButton.setAttribute('aria-label', 'Fast-forward season timelapse');
    }
    // Leave the map painted on the current frame — do not clear
    // feature-state or reset the thumb. The user sees what was playing.
    IS_PLAYING = false;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing: false } }));
  };

  // start(speedArg) — begins playback from the current thumb position.
  // If timer is already running (speed switch mid-playback), re-arms the
  // interval at the new rate without resetting frameIdx so position is
  // preserved.
  const start = async (speedArg) => {
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

    speed = speedArg;

    // Only update frameIdx when starting fresh (not a speed switch).
    if (timer === null) {
      frameIdx = currentFrameIdx();
    }

    // Clear any existing timer before arming the new one.
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }

    // Update button states to reflect which transport is now active.
    if (speedArg === 1) {
      playButton.dataset.state = 'playing';
      playButton.setAttribute('aria-label', 'Stop season timelapse');
      if (fastButton) {
        fastButton.dataset.state = 'stopped';
        fastButton.setAttribute('aria-label', 'Fast-forward season timelapse');
      }
    } else {
      if (fastButton) {
        fastButton.dataset.state = 'playing';
        fastButton.setAttribute('aria-label', 'Stop fast-forward timelapse');
      }
      playButton.dataset.state = 'stopped';
      playButton.setAttribute('aria-label', 'Play season timelapse');
    }

    IS_PLAYING = true;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing: true } }));
    applyFrame(sortedDates[frameIdx]);
    timer = setInterval(tick, frameMs());
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

  // Play button: same speed → stop; other speed active → switch to 1×;
  // stopped → start at 1×.
  playButton.addEventListener('click', () => {
    if (timer !== null && speed === 1) {
      stop();
    } else {
      start(1);
    }
  });

  // Fast-forward button: same speed → stop; other speed active → switch
  // to 2×; stopped → start at 2×.
  if (fastButton) {
    fastButton.addEventListener('click', () => {
      if (timer !== null && speed === 2) {
        stop();
      } else {
        start(2);
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
  // with no boundary. L4 is included for completeness even though its
  // checkbox is disabled — flipping it would also be a no-op against
  // the disabled-button guard below.
  //
  // The picker mutates layer visibility via setLayoutProperty rather
  // than reaching into the main IIFE's overlayState — the layer state
  // on the map IS the source of truth, and the localStorage key is
  // the persistence shadow.
  const OVERLAY_LAYER_IDS = {
    l1: ['major-regions-line', 'major-regions-label'],
    l2: ['sub-regions-line', 'sub-regions-label'],
    l4: ['regions-fill', 'regions-line', 'regions-label'],
    resorts: ['resorts-pin', 'resorts-label'],
  };
  const OVERLAY_STORAGE_KEY = {
    l1: 'snowdesk.map.overlay.l1',
    l2: 'snowdesk.map.overlay.l2',
    l4: 'snowdesk.map.overlay.l4',
    resorts: 'snowdesk.map.overlay.resorts',
  };

  for (const item of items) {
    // Disabled menu items (currently just the L4 / Micro regions
    // checkbox) shouldn't dispatch a click in modern browsers, but
    // skip the wiring entirely as a belt-and-braces guard. Without
    // this, a future caller calling .click() programmatically could
    // sneak past the browser's disabled gate.
    if (item.disabled) continue;

    item.addEventListener('click', (e) => {
      e.stopPropagation();

      // SNOW-59 / SNOW-172: overlay checkbox — toggle visibility or country filter.
      const overlayKey = item.dataset.overlayKey;
      if (overlayKey) {
        const next = item.getAttribute('aria-checked') !== 'true';
        item.setAttribute('aria-checked', next ? 'true' : 'false');

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

        // Tier overlay — toggle layer visibility.
        try { localStorage.setItem(OVERLAY_STORAGE_KEY[overlayKey], String(next)); }
        catch (_) { /* private mode — choice still applies for this session */ }
        if (MAP) {
          if (next && (overlayKey === 'l1' || overlayKey === 'l2' || overlayKey === 'resorts')) {
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
      try { localStorage.setItem(STORAGE_KEY, key); }
      catch (_) { /* private mode — choice still applies for this session */ }
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
      MAP.setStyle(url);
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
    try { localStorage.setItem(STORAGE_KEY, String(AUTOZOOM)); }
    catch (_) { /* private mode — apply for session only */ }
    sync();
  });
})();
