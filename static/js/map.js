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
 *   2. Fetch regions GeoJSON, today's summaries, and resorts in parallel.
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

// Whether a single click on a region auto-pans/zooms to fit it into view.
// Off by default; persisted in localStorage under
// 'snowdesk.map.autozoom'. The autozoomToggleInit IIFE at the bottom of
// this file owns the button wiring; selectFeature reads this flag.
let AUTOZOOM = false;

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

// Lazily-fetched, cached payload from /api/season-ratings/. Shape:
// { date_iso: { region_id: rating_int } }. Both timelapse (SNOW-46) and
// the scrubber (SNOW-47) consume the same dataset; sharing one fetch
// keeps the payload off the wire twice.
let SEASON_RATINGS_URL = null;
let SEASON_RATINGS_PROMISE = null;

const getSeasonRatings = () => {
  if (SEASON_RATINGS_PROMISE !== null) return SEASON_RATINGS_PROMISE;
  if (!SEASON_RATINGS_URL) {
    return Promise.reject(new Error('season-ratings URL not set'));
  }
  SEASON_RATINGS_PROMISE = fetch(SEASON_RATINGS_URL).then((resp) => {
    if (!resp.ok) throw new Error('season-ratings fetch failed');
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

// Clear per-feature rating state, reverting the choropleth to the
// property-based ``rating`` written at page load (today's bulletins).
const clearRegionRepaint = () => {
  if (!MAP) return;
  for (const feature of Object.values(FEATURE_BY_REGION_ID)) {
    MAP.removeFeatureState({ source: 'regions', id: feature.id }, 'rating');
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
  const SUMMARIES_URL     = mapEl.dataset.summariesUrl;
  const RESORTS_URL       = mapEl.dataset.resortsUrl;
  // The summary URL carries the literal placeholder __REGION__ which is
  // substituted with the tapped region's region_id at fetch time. Server
  // renders this via {% url 'api:region_summary' '__REGION__' %} so the
  // route name stays the single source of truth.
  const REGION_SUMMARY_URL_TEMPLATE = mapEl.dataset.regionSummaryUrl;
  // Hand the season-ratings URL to module scope so the timelapse and
  // scrubber IIFEs (defined further down in this file) can share one
  // fetch via getSeasonRatings().
  SEASON_RATINGS_URL = mapEl.dataset.seasonRatingsUrl;

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

  const BULLETIN_SUMMARIES = {};
  const RESORTS_BY_REGION  = {};
  const RATINGS            = {};

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
    // South bound 41.0: French alpine regions reach to ~41.7°N; giving a
    // 1° visual buffer below the southernmost feature keeps the map from
    // clipping at the edge.  Italian regions (min ~44.1°N) and CH are
    // comfortably within this bound.
    maxBounds: [[-2.0, 41.0], [17.0, 50.5]],
    attributionControl: { compact: true },
  });
  // Expose for sibling IIFEs (timelapse, season scrubber). FEATURE_BY_ID
  // and FEATURE_BY_REGION_ID are at module scope and get populated below.
  MAP = map;

  // SNOW-68: log zoom level on each zoom gesture when debug mode is active.
  map.on('zoomend', () => {
    if (DEBUG) console.log('[map] zoom:', map.getZoom().toFixed(2));
  });

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
    // Colour resolution prefers a feature-state ``rating`` if one is set
    // (used by the SNOW-46 timelapse to recolour regions per frame
    // without re-uploading the source) and falls back to the
    // property-based ``rating`` written at load time. Removing the
    // feature-state on stop reverts to the property colour, i.e. today's
    // bulletins.
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      paint: {
        'fill-color': [
          'match',
          ['coalesce', ['feature-state', 'rating'], ['get', 'rating']],
          'low',          RATING_COLOURS.low,
          'moderate',     RATING_COLOURS.moderate,
          'considerable', RATING_COLOURS.considerable,
          'high',         RATING_COLOURS.high,
          'very_high',    RATING_COLOURS.very_high,
          RATING_COLOURS.no_rating,
        ],
        'fill-opacity': [
          'case',
          ['boolean', ['feature-state', 'selected'], false], 0.85,
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
    map.addLayer({
      id: 'regions-line-selected',
      type: 'line',
      source: 'regions',
      filter: ['boolean', ['feature-state', 'selected'], false],
      layout: {
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': '#1a1a1a',
        'line-width': 4,
        // Soft halo so the outline reads against any choropleth fill colour.
        'line-blur': 0.5,
      },
    });
    BASE_LAYER_FILTERS['regions-line-selected'] = map.getFilter('regions-line-selected') ?? null;

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
  // with — rather than overwrites — any pre-existing layer filter (e.g.
  // the feature-state selection filter on regions-line-selected).
  const BASE_LAYER_FILTERS = {};

  // SNOW-172: Compute the MapLibre filter expression that shows only
  // enabled countries on all region layers.  Any layer that was given a
  // filter at install time has its base filter preserved by composing
  // ['all', baseFilter, countryFilter]; layers with no base filter
  // receive the country filter alone.
  //
  // Note: 'regions-line-selected' is intentionally excluded from this list.
  // Its filter uses a feature-state expression (['boolean',
  // ['feature-state', 'selected'], false]) which MapLibre does not support
  // when nested inside an ['all', ...] compound filter.  The selection ring
  // only activates on features the user has actually clicked (which must
  // already be visible through regions-fill), so skipping the country
  // constraint here is safe — a user cannot click a hidden fill feature.
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
    // regions-line-selected: restore its original feature-state filter without
    // country composition (see note above).
    if (map.getLayer('regions-line-selected')) {
      const base = BASE_LAYER_FILTERS['regions-line-selected'];
      map.setFilter('regions-line-selected', base ?? ['boolean', ['feature-state', 'selected'], false]);
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
          f.properties.rating = RATINGS[regionID] || 'no_rating';
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
    } catch (err) {
      console.warn('[map] Failed to load country', upper, err);
      // Leave toggle visually on so the user can retry — don't reset countryState.
    }
  };

  // SNOW-172: Bridge for the basemapPickerInit IIFE, which lives in a separate
  // scope and cannot reference countryState / ensureCountryLoaded directly.
  // The picker dispatches this event; we own the state mutation here.
  document.addEventListener('snowdesk:country-toggle', (e) => {
    const { code, next } = e.detail;
    countryState[code] = next;
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

  map.on('load', async () => {
    // Fetch everything in parallel. All requests are independent —
    // geometry, bulletin summaries, resort lists, and the L1/L2 overlay
    // geometry (SNOW-59) — so they can all fly at once. The overlay
    // fetches degrade gracefully on failure: a missing payload just
    // skips that layer install.
    const [geojson, summaries, resorts, majorGeojson, subGeojson, resortsGeojson] =
      await Promise.all([
        fetch(REGIONS_URL + '?country=ch').then(r => r.json()),
        fetch(SUMMARIES_URL).then(r => r.json()),
        fetch(RESORTS_URL).then(r => r.json()),
        MAJOR_REGIONS_URL
          ? fetch(MAJOR_REGIONS_URL + '?country=ch').then(r => r.json()).catch(() => null)
          : Promise.resolve(null),
        SUB_REGIONS_URL
          ? fetch(SUB_REGIONS_URL + '?country=ch').then(r => r.json()).catch(() => null)
          : Promise.resolve(null),
        RESORTS_GEOJSON_URL
          ? fetch(RESORTS_GEOJSON_URL).then(r => r.json()).catch(() => null)
          : Promise.resolve(null),
      ]);
    Object.assign(BULLETIN_SUMMARIES, summaries);
    Object.assign(RESORTS_BY_REGION, resorts);
    // Derive RATINGS from summaries — single source of truth for the choropleth.
    for (const [id, s] of Object.entries(summaries)) RATINGS[id] = s.rating;

    // Assign a numeric id to every feature and build the lookup.
    // MapLibre's feature-state API requires numeric ids; regionID is a string
    // ("CH-4115") so we can't use it directly.
    geojson.features.forEach((f, i) => {
      f.id = i;
      // The API emits the region identifier as properties.id. Normalise to
      // properties.regionID so the rest of the code has a stable name.
      const regionID = f.properties.id;
      f.properties.regionID = regionID;
      f.properties.rating = RATINGS[regionID] || 'no_rating';
      REGION_LOOKUP[i] = f.properties;
      FEATURE_BY_ID[i] = f;
      FEATURE_BY_REGION_ID[regionID] = f;
    });

    geojsonCache = geojson;
    majorGeojsonCache = majorGeojson;
    subGeojsonCache = subGeojson;
    resortsGeojsonCache = resortsGeojson;
    installRegionsLayers(geojson);
    installOverlayLayers(majorGeojson, subGeojson);
    installResortsLayer(resortsGeojson);

    // SNOW-172: CH geometry is now loaded; record it and apply initial filter.
    loadedCountries.add('ch');
    applyCountryFilters();

    // Restore any countries that were previously enabled in localStorage.
    for (const code of COUNTRY_KEYS) {
      if (code !== 'ch' && countryState[code]) {
        ensureCountryLoaded(code).catch(() => {});
      }
    }

    // Interaction
    let selectedId = null;
    // Tracks the most recent inflight summary fetch so a slow tap-A
    // followed by a fast tap-B never lets A's response overwrite B's.
    let summarySeq = 0;
    // Most recent date the choropleth is showing — seeded from any
    // ``?d=`` on the URL, then kept in sync by every
    // ``snowdesk:date-changed`` event (scrubber commit, timelapse frame).
    // ``currentDisplayedDate`` is used only by the choropleth/scrubber
    // path; the region tooltip is date-independent.
    let currentDisplayedDate = (() => {
      const d = new URL(location.href).searchParams.get('d');
      return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
    })();

    // The currently-open MapLibre Popup, or null when none is open.
    let activePopup = null;

    // ---- URL fragment state (SNOW-39) ----
    //
    // The currently-selected region is mirrored in ``location.hash`` as
    // ``#CH-xxxx`` so the back button dismisses the popup (instead of
    // leaving the page) and so a deep link reopens the popup on load.
    //
    // ``popupHistoryOpen`` tracks whether our hash is currently the top
    // history entry — drives push-vs-replace on the next open and tells
    // ``clearTooltip`` whether to ``history.back()`` or just remove the
    // popup directly. ``popstateInProgress`` blocks the recursive
    // ``clearTooltip -> history.back -> popstate -> clearTooltip`` path
    // during back-button dismissal.
    let popupHistoryOpen = false;
    let popstateInProgress = false;

    // Canonical SLF region-ID shape (e.g. "CH-4115"). Anything else is
    // rejected before it reaches any href to prevent a malformed GeoJSON
    // payload turning into an open-redirect / javascript: URL on the client.
    const REGION_ID_RE = /^[A-Za-z]{2}-[A-Za-z0-9]+$/;

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
        // SNOW-174: the filter on regions-line-selected reads feature-state;
        // triggerRepaint ensures the dedicated selection layer redraws
        // immediately rather than waiting for the next idle frame.
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

    // User-facing dismiss path. If our hash is the active history entry
    // and we're not already inside a popstate, pop it so the URL and
    // popup state stay in lockstep. The popstate handler calls
    // clearPopupDom directly.
    const clearTooltip = () => {
      if (popupHistoryOpen && !popstateInProgress) {
        history.back();
        return;
      }
      clearPopupDom();
    };

    // Push or replace the URL hash to point at ``regionID``. First open
    // of a session pushes a single entry; subsequent region taps replace
    // it so the back stack grows by exactly one no matter how many
    // regions the user sweeps through.
    const syncUrlForRegion = (regionID) => {
      const hash = '#' + regionID;
      const state = { popup: regionID };
      if (!popupHistoryOpen) {
        history.pushState(state, '', hash);
        popupHistoryOpen = true;
      } else {
        history.replaceState(state, '', hash);
      }
    };

    // Fetch the server-rendered tooltip HTML for a region, open a
    // MapLibre Popup anchored to the click point (when supplied) or the
    // region's bbox centre (deep-link / resort-pin path), and wire its
    // 'close' event back to clearTooltip so Esc / × / outside-map clicks
    // all reset the URL hash. The summarySeq guard discards stale
    // responses when the user taps a different region mid-flight.
    // Returns true on success, false on 404 / network error.
    //
    // closeOnClick: true makes MapLibre auto-dismiss the popup on any
    // map canvas click, so region-to-region transitions don't stack
    // and tapping empty map area auto-dismisses without a separate handler.
    const loadRegionSummary = async (regionID, { dateKey, clickPoint } = {}) => {
      if (!REGION_ID_RE.test(regionID)) return false;
      let url = REGION_SUMMARY_URL_TEMPLATE.replace(
        '__REGION__', encodeURIComponent(regionID),
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
          closeOnClick: true,
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
        '__REGION__', encodeURIComponent(regionID),
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
      // filter (which reads feature-state) activates on this frame.
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
      // Pass the click's lngLat so the popup opens over the tapped point,
      // not the region bbox centre. closeOnClick: true on the Popup means
      // MapLibre auto-dismisses any previous popup before this handler
      // fires, so no manual swap is needed.
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

    // NOTE: the previous "dismiss popup on map tap outside a region" handler
    // (map.on('click', e => ...)) is intentionally removed. closeOnClick: true
    // on the MapLibre Popup handles both taps on empty map area and
    // region-to-region transitions without a separate handler.

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
    window.addEventListener('popstate', () => {
      popstateInProgress = true;
      try {
        const numericId = featureIdFromHash();
        if (numericId !== null) {
          selectFeature(numericId, { toggle: false, urlMode: 'mark' });
        } else {
          popupHistoryOpen = false;
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
        selectFeature(numericId, { toggle: false, urlMode: 'mark' });
      } else if (location.hash === '' || location.hash === '#') {
        popupHistoryOpen = false;
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

    // One entry per region (matchable by display name or SLF region_id).
    for (const props of Object.values(REGION_LOOKUP)) {
      const regionID = props.regionID;
      const name = props.name || regionID;
      SEARCH_INDEX.push({
        type: 'region',
        primary: name,
        secondary: regionID,
        regionID,
        searchable: normalise(`${name} ${regionID}`),
      });
    }
    // One entry per resort, pointing back to its parent region. The
    // secondary label carries the region name so users see context when
    // several resorts share a first word.
    for (const [regionID, resorts] of Object.entries(RESORTS_BY_REGION)) {
      const feature = FEATURE_BY_REGION_ID[regionID];
      if (!feature) continue;
      const regionName = feature.properties.name || regionID;
      for (const resort of resorts) {
        SEARCH_INDEX.push({
          type: 'resort',
          primary: resort,
          secondary: regionName,
          regionID,
          searchable: normalise(`${resort} ${regionName}`),
        });
      }
    }

    // Ranking: prefix matches on the primary label sort above substring
    // matches; ties break alphabetically. Cap at MAX_RESULTS so the
    // dropdown stays usable on narrow viewports.
    const runSearch = (query) => {
      const q = normalise(query).trim();
      if (!q) return [];
      const hits = [];
      for (const item of SEARCH_INDEX) {
        const idx = item.searchable.indexOf(q);
        if (idx === -1) continue;
        const primaryIdx = normalise(item.primary).indexOf(q);
        const score = primaryIdx === 0 ? 0 : primaryIdx > 0 ? 1 : 2;
        hits.push({ item, score, pos: idx });
      }
      hits.sort((a, b) =>
        a.score - b.score ||
        a.pos - b.pos ||
        a.item.primary.localeCompare(b.item.primary),
      );
      return hits.slice(0, MAX_RESULTS).map(h => h.item);
    };

    const inputEl = document.getElementById('search-input');
    const resultsEl = document.getElementById('search-results');
    const pillEl = document.getElementById('search-pill');
    const toggleEl = document.getElementById('search-toggle');
    let currentResults = [];
    let activeIdx = -1;

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

        // Text column (primary/secondary) and a type badge side by side.
        // The badge disambiguates region hits from resort hits, which
        // otherwise render identically when a resort shares its name
        // with its region (e.g. "Davos" in the Davos region).
        const text = document.createElement('div');
        text.className = 'search-result-text';
        const primary = document.createElement('div');
        primary.className = 'search-result-primary';
        primary.textContent = r.primary;
        const secondary = document.createElement('div');
        secondary.className = 'search-result-secondary';
        secondary.textContent = r.secondary;
        text.append(primary, secondary);

        const badge = document.createElement('span');
        badge.className = `search-result-badge search-result-badge--${r.type}`;
        // i18n: translatable — search result type badges
        badge.textContent = r.type === 'region' ? 'Region' : 'Resort';

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

    // SNOW-47 / SNOW-174: keep currentDisplayedDate in sync and refresh
    // the open popup when the scrubber commits a new date. If a popup is
    // open, swap its HTML to reflect the new day's danger rating without
    // closing and re-opening it.
    document.addEventListener('snowdesk:date-changed', (e) => {
      currentDisplayedDate = (e.detail && e.detail.date) || null;
      refreshActivePopupForDate(currentDisplayedDate);
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
  getSeasonRatings().then((data) => {
    ratingsCache = data;
    sortedDates = Object.keys(data).sort();
  }).catch(() => { /* network fail → leave snap disabled, drag still works */ });

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
    const snapped = snapToNearestDataDay(liveDate || todayKey);
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
    const target = d && /^\d{4}-\d{2}-\d{2}$/.test(d) && isInSeason(d) ? d : todayKey;
    commitDate(target, { silent: true });
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

// Season timelapse — the play button on the scrubber cycles through
// every dated frame in the season at ~10 fps. Each frame repaints
// region colours via feature-state and announces a snowdesk:date-changed
// event so the date pill (and any open popup) stays in sync. A second
// click — or any user scrub — stops playback and reverts to today.
(function timelapseInit() {
  const button = document.getElementById('scrubber-play');
  if (!button) return;

  // 1× = 10 fps. The speed-button cycles through SPEED_PRESETS; the
  // active multiplier divides BASE_FRAME_MS to derive the setInterval
  // delay. The 10ms floor in frameMs() guards against a future >10×
  // preset starving the main thread.
  const BASE_FRAME_MS = 200;
  const SPEED_PRESETS = [1, 2, 4, 0.5];
  const SPEED_STORAGE_KEY = 'snowdesk.map.timelapse-speed';

  // Drive the existing scrubber thumb so the playback position is
  // visible on the same control the user can drag.
  const scrubber = document.getElementById('season-scrubber');
  const scrubberThumb = scrubber ? scrubber.querySelector('.season-scrubber-thumb') : null;
  const seasonStartMs = scrubber ? Date.parse(scrubber.dataset.seasonStart) : NaN;
  const seasonEndMs = scrubber ? Date.parse(scrubber.dataset.seasonEnd) : NaN;
  const todayPct = scrubber ? parseFloat(scrubber.dataset.todayPct) : NaN;
  const todayKey = scrubber ? scrubber.dataset.today : null;
  const seasonSpanMs = seasonEndMs - seasonStartMs;

  const speedButton = document.getElementById('scrubber-speed');
  let speed = 1;
  try {
    const stored = parseFloat(localStorage.getItem(SPEED_STORAGE_KEY));
    if (SPEED_PRESETS.includes(stored)) speed = stored;
  } catch (_) {}

  const frameMs = () => Math.max(10, Math.round(BASE_FRAME_MS / speed));

  const formatSpeedLabel = (s) => (s === 0.5 ? '½×' : s + '×');

  const renderSpeedButton = () => {
    if (!speedButton) return;
    speedButton.textContent = formatSpeedLabel(speed);
    speedButton.dataset.speed = String(speed);
  };

  const moveScrubber = (dateKey) => {
    if (!scrubberThumb || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) return;
    const dateMs = Date.parse(dateKey);
    if (Number.isNaN(dateMs)) return;
    const pct = Math.max(0, Math.min(100, ((dateMs - seasonStartMs) / seasonSpanMs) * 100));
    scrubberThumb.style.left = pct + '%';
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

  // Hoisted so the speed-button handler can re-arm setInterval with the
  // same callback when the user changes speed mid-playback.
  const tick = () => {
    frameIdx += 1;
    if (frameIdx >= sortedDates.length) {
      // Last frame already painted on the previous tick — stop here so
      // the final value sits long enough to register before regions
      // snap back to today.
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
    button.dataset.state = 'stopped';
    button.setAttribute('aria-label', 'Play season timelapse');
    // Leave the map painted on the current frame — do not clear feature-state,
    // reset the thumb, or announce today. The user sees exactly what the
    // timelapse was showing when they pressed stop.
  };

  const start = async () => {
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
    frameIdx = 0;
    button.dataset.state = 'playing';
    button.setAttribute('aria-label', 'Stop season timelapse');
    applyFrame(sortedDates[frameIdx]);
    timer = setInterval(tick, frameMs());
  };

  // When the scrubber commits a new date, the timelapse must surrender
  // control — both consumers paint via feature-state on the same source,
  // so a running timer would fight any user scrub.
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (timer !== null && (!e.detail || e.detail.source !== 'timelapse')) {
      stop();
    }
  });

  // SNOW-58: a basemap swap wipes the regions source mid-frame — the
  // setInterval would keep firing repaintRegionsForDate() against a
  // source that doesn't exist yet during the style.load gap. Stop here
  // and let the user re-press play after the new basemap settles.
  document.addEventListener('snowdesk:basemap-changing', () => {
    if (timer !== null) stop();
  });

  button.addEventListener('click', () => {
    if (timer !== null) stop();
    else start();
  });

  if (speedButton) {
    renderSpeedButton();
    speedButton.addEventListener('click', () => {
      const idx = SPEED_PRESETS.indexOf(speed);
      speed = SPEED_PRESETS[(idx + 1) % SPEED_PRESETS.length];
      try { localStorage.setItem(SPEED_STORAGE_KEY, String(speed)); } catch (_) {}
      renderSpeedButton();
      // Re-arm the running loop at the new rate without losing position
      // so the user sees the speed change take effect immediately.
      if (timer !== null) {
        clearInterval(timer);
        timer = setInterval(tick, frameMs());
      }
    });
  }
})();

// Always-visible date pill anchored next to the (i) legend toggle.
// Server-rendered with today's date for first-paint correctness; this
// IIFE keeps it in sync as the user scrubs or watches the timelapse.
(function mapDatePillInit() {
  const pill = document.getElementById('map-date-pill');
  if (!pill) return;
  const setFrom = (e) => {
    const dk = e.detail && e.detail.date;
    if (dk) pill.textContent = formatDateLong(dk);
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

        // Tier overlay — toggle layer visibility (existing SNOW-59 logic).
        try { localStorage.setItem(OVERLAY_STORAGE_KEY[overlayKey], String(next)); }
        catch (_) { /* private mode — choice still applies for this session */ }
        if (MAP) {
          for (const layerId of OVERLAY_LAYER_IDS[overlayKey]) {
            if (MAP.getLayer(layerId)) {
              MAP.setLayoutProperty(
                layerId, 'visibility', next ? 'visible' : 'none',
              );
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
