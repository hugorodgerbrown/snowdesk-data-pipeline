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
 *
 * SNOW-610: this file used to be all of the above PLUS eleven other
 * surfaces — 9,192 lines. It is now the boot IIFE alone, and the surfaces
 * are the files below. The split followed seams the file already had, so
 * every one of them is a verbatim move.
 *
 * Load order is a real constraint, not a convention. These are classic
 * scripts sharing one global lexical scope: a top-level `let`/`const` is
 * readable from a later script as a bare identifier, but is in the
 * temporal dead zone until its own script has run. Every IIFE here runs at
 * parse time. So declarations load before this file, and surfaces after —
 * exactly the order they had when they were one file. `home.html` carries
 * the tags in this order:
 *
 *   map_state.js              MAP, FEATURE_BY_*, COUNTRY_STATE, AUTOZOOM,
 *                             the storage keys, MAP_STRINGS, and
 *                             window.snowdeskMapState — the channel modules
 *                             OUTSIDE this set must use (see its header).
 *   map_basemap_downloads.js  pinned-cache probes, the downloaded-area
 *                             records, the byte budget and eviction,
 *                             failure toasts, the progress grid,
 *                             runPinnedDownload.
 *   map_shared.js             INT_TO_RATING, the date helpers, the
 *                             localStorage trio, getSeasonRatings,
 *                             fetchBulletinGroupingsForDate,
 *                             repaintRegionsForDate.
 *   map.js                    <- you are here: boot, style/overlay install,
 *                             region select, popups, markers, search.
 *   map_scrubber.js           season scrubber and ?d= sync.
 *   map_legend.js             collapsible danger-scale legend.
 *   map_timelapse.js          playback transport.
 *   map_basemap_picker.js     basemap popover and setStyle.
 *   map_region_download.js    per-region download roundel.
 *   map_custom_download.js    custom-area framing overlay.
 *   map_autozoom.js           auto-zoom checkbox.
 *   map_geolocate.js          locate-me roundel.
 *   map_season_ribbon.js      danger ribbon and region readout.
 */


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

  // SNOW-645 (Hugo's explicit call, overruling the plan's own non-goal):
  // the overlay is already computed against the ACTIVE basemap's template
  // (it only ever shows tiles cached for the basemap showing now — see the
  // "PER-BASEMAP" note on refreshDownloadedOverlay below), so the active
  // basemap's identity colour is the honest colour for it — a plain green
  // here while the roundel and progress grid turn (say) blue would read as
  // a colour seam the instant the grid fades out and the overlay paints.
  // basemapIdentityColour (static/js/map_basemap_downloads.js) falls back
  // to --color-sync-ok itself for an unresolved/unknown key, so this is
  // still "green matching the sync dots" for the common case.
  //
  // A FUNCTION, not the const it used to be: the identity colour has to
  // track basemap changes, not freeze at whatever was active on first
  // paint. installRegionsLayers calls this fresh every time it (re)installs
  // the two layers below — which setStyle forces on every basemap switch —
  // and refreshDownloadedOverlay ADDITIONALLY re-applies it with
  // setPaintProperty on every refresh (including snowdesk:basemap-changed),
  // so an already-installed pair of layers updates too, not only ones
  // about to be freshly (re)added.
  const downloadedOutlineColour = () => basemapIdentityColour(activeBasemapKey());

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
        'fill-color': downloadedOutlineColour(),
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
        'line-color': downloadedOutlineColour(),
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
  // SNOW-543: pin size is the resort's curated ``tier``. Every tier is
  // drawn at every zoom — the review's framing was to suppress Minor
  // below a zoom threshold, but the small resorts are frequently the ones
  // with the most interesting terrain for this product, so hiding them is
  // backwards. Size does the ranking; nothing disappears.
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
        // SNOW-543: radius carries the curated tier, so Zermatt and a
        // one-lift village hill stop reading as equally important. Tier is
        // matched inside each zoom stop rather than multiplying a base
        // radius, because MapLibre interpolates between the stop *values* —
        // a per-tier stop set keeps the ramp linear for every tier.
        //
        // Colour deliberately stays constant: it is reserved for the
        // orthogonal "kind of resort" axis (touring / piste / freeride) and
        // spending both channels on tier would be redundant. The map's
        // colour budget is already committed to the danger scale, which is
        // the one colour language users are meant to read precisely.
        'circle-radius': [
          'interpolate', ['linear'], ['zoom'],
          5, ['match', ['get', 'tier'], 'CORE', 4.5, 'MINOR', 2, 3],
          9, ['match', ['get', 'tier'], 'CORE', 7, 'MINOR', 3.5, 5],
          12, ['match', ['get', 'tier'], 'CORE', 9.5, 'MINOR', 5, 7],
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

      // SNOW-645: re-apply the identity colour on EVERY refresh, not only
      // when installRegionsLayers happens to have (re)created the layers
      // this call. A basemap switch normally does force a full re-add
      // (setStyle wipes every custom layer), but this call is also the
      // one the "downloaded" overlay toggle itself triggers — with no
      // basemap change and so no re-add — and relying on re-add alone
      // would leave two ALREADY-INSTALLED layers holding whatever colour
      // they were first painted with, drifting the moment the user
      // switches basemap without ever toggling the overlay off and on.
      const colour = downloadedOutlineColour();
      if (map.getLayer('cached-tiles-fill')) {
        map.setPaintProperty('cached-tiles-fill', 'fill-color', colour);
      }
      if (map.getLayer('cached-tiles-line')) {
        map.setPaintProperty('cached-tiles-line', 'line-color', colour);
      }

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
    //
    // SNOW-638: these three fetches used to share this one Promise.all with
    // no guards on the regions/resorts legs, so a single always-rejecting
    // sibling took the whole boot handler down with it. /api/resorts.json
    // is deliberately NOT in sw.js's STATIC_PATHS (only the .geojson
    // sibling is) — offline, that fetch rejects on every single load,
    // which meant installRegionsLayers/applyCountryFilters/the choropleth
    // paint below never ran even though the regions feed itself was fine
    // and the sync dot (which only probes regions.geojson) showed green.
    // The three legs still run in parallel in ONE Promise.all — SNOW-235
    // trimmed this exact critical path, and serialising the regions fetch
    // ahead of ratings/resorts would silently reintroduce that cost. Each
    // leg now carries its own .catch() so a failing leg degrades on its
    // own instead of rejecting its siblings — the danger was never the
    // shared Promise.all, it was the unguarded legs; don't re-collapse
    // them into a single failure domain by dropping a .catch(), and don't
    // pull any leg out of this Promise.all to "fix" this again.
    const [geojson, todayRatingsPayload, resorts] =
      await Promise.all([
        fetch(REGIONS_URL + '?country=ch').then(r => {
          if (!r.ok) throw new Error('regions fetch failed');
          return r.json();
        }).catch((err) => {
          console.warn('[map] regions fetch failed', err);
          return null;
        }),
        RATINGS_URL
          ? fetch(RATINGS_URL + '?d=' + bootDateKey + '&country=ch').then(r => {
              if (!r.ok) throw new Error('ratings fetch failed');
              return r.json();
            }).catch(() => ({}))
          : Promise.resolve({}),
        // SNOW-638: guarded like the ratings leg above — RESORTS_BY_REGION
        // only feeds searchCore.buildEntry, which already falls back to []
        // for a region with no resorts data, so degrading here just means
        // search entries list no resorts while offline.
        fetch(RESORTS_URL).then(r => {
          if (!r.ok) throw new Error('resorts fetch failed');
          return r.json();
        }).catch(() => ({})),
      ]);
    if (!geojson) {
      // No geometry to install — fail safe instead of leaving an uncaught
      // rejection. The sync dot probes this same URL, so a failure here
      // means it is not showing green either: the two agree, which is the
      // whole point of the ticket.
      return;
    }
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
