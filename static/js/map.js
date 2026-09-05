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
  // SNOW-761: the Weather overlay's data endpoint. ONE feed, anchored on
  // Location, filtered server-side by Location.objects.public() — public
  // data like community reports, so there is no eligibility attribute to
  // pair with it.
  const WEATHER_URL = mapEl.dataset.weatherUrl || null;
  // SNOW-687: per-user saved-routes GeoJSON — eligibility was an
  // authenticated user (the endpoint 403s for anyone else, and there is
  // nothing to draw), so this is gated the same way as favourites rather
  // than as the two public layers above.
  //
  // SNOW-764 widened it: a visitor holding a followed share link is
  // eligible too, and the payload they get is the shared route alone. That
  // is what makes the ?route_share deep link work signed out — the layer
  // has to be fetchable before there is anything to fly to.
  const ROUTES_URL = mapEl.dataset.routesUrl || null;
  const ROUTES_ELIGIBLE = mapEl.dataset.routesEligible === 'true';
  // SNOW-764: the shared-route popup's Save. Three values, because the
  // control has three states — post here, or send them to sign in first.
  // Templated on __TOKEN__ rather than a uuid: a pending feature carries
  // the share token and deliberately no uuid (apps/routes/views.py).
  const ROUTE_CLAIM_URL_TEMPLATE = mapEl.dataset.routeClaimUrlTemplate || null;
  const ROUTES_UPLOAD_ELIGIBLE = mapEl.dataset.routesUploadEligible === 'true';
  // SNOW-828: where a ``?trip=`` / ``?trip_share=`` arrival fetches its
  // geometry from. Keyed by the identifier space each parameter carries, so
  // ``pwaTripDeepLinkCore.endpointFor`` can pick one without knowing which
  // parameter it read. Unconditional — both endpoints scope themselves, and
  // the map has no trips overlay whose eligibility these would gate.
  const TRIP_ROUTE_URL_TEMPLATES = Object.freeze({
    uuid: mapEl.dataset.tripRouteUrlTemplate || null,
    token: mapEl.dataset.tripShareRouteUrlTemplate || null,
  });
  const ROUTES_SIGNIN_URL = mapEl.dataset.routesSigninUrl || null;
  // SNOW-691: the slope-angle raster's tile template and its gate. Public
  // third-party tiles, so "eligible" is settings.SLOPE_TILE_URL being
  // configured (SNOW-724 moved the gate off a waffle flag and onto the
  // setting, which is the operator kill switch) rather than the
  // gate-AND-authentication shape routes and favourites use. The template
  // is a swisstopo WMTS XYZ URL rather than a Snowdesk endpoint, which is
  // why it is rendered as a whole URL instead of being reversed here.
  const SLOPE_TILE_URL = mapEl.dataset.slopeTileUrl || null;
  const SLOPE_ELIGIBLE = mapEl.dataset.slopeLayerEligible === 'true';

  // SNOW-672: marker tints, in one place. MapLibre paint properties cannot
  // reference a CSS custom property, so these two literals are the JS side
  // of --color-marker-favourite / --color-marker-observation in
  // src/css/main.css. Change one, change the other — the map legend in
  // _map_embed.html reads the tokens, so a drift shows up as a legend that
  // disagrees with the map.
  const MARKER_FAVOURITE_COLOUR = '#1a73e8';
  const MARKER_OBSERVATION_COLOUR = '#e8711a';
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
  // SNOW-499: The resort-pin popup URL template — the literal '__SLUG__'
  // is string-replaced with the tapped resort's slug before each fetch.
  // Rendered via {% url 'api:resort_popup' slug='__SLUG__' %}; public
  // endpoint, always present regardless of favourites eligibility. The
  // slug is the feed's ``id`` (SNOW-796) — no integer key anywhere here.
  const RESORT_POPUP_URL_TEMPLATE = mapEl.dataset.resortPopupUrl || '';

  // SNOW-660: ``bootDateKey`` — min(today, seasonEnd), SNOW-236's cold-open
  // date — used to live here, and every date-consuming path fell back to it
  // when nothing had been chosen. That fallback is what made a cold boot
  // paint a day nobody asked for: the map opened coloured for a date it had
  // picked itself, with nothing on screen naming it. Off season, where that
  // date can carry a rating for a single region, the result was one coloured
  // polygon and no explanation.
  //
  // The one answer to "which day" is now ``currentDisplayedDate`` (declared
  // further down, seeded via ``readDisplayDate()`` and kept current by
  // ``snowdesk:date-changed``), and ``null`` means no day is known at all.
  // Nothing reconstructs a substitute — a path with no date paints nothing
  // rather than guessing.
  //
  // SNOW-793 gave that seed a default again, and the distinction from the
  // removed ``bootDateKey`` is the whole point: the default is TODAY, read
  // from the server-rendered ``data-today``, never the last day carrying a
  // rating. Today is nameable and #map-date-ribbon names it; the date this
  // block is about was neither. ``null`` is now only reachable where the
  // scrubber is absent or its ``data-today`` is malformed, and every path
  // below still treats it as "paint nothing".

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
    // SNOW-691: the slope raster's origin joins the list. It is not a
    // basemap — it never reaches BASEMAP_OPTIONS and has no picker row — but
    // it is the same KIND of thing to the service worker: cross-origin tiles
    // whose opportunistic caching is what lets previously-browsed terrain
    // still paint offline. Without it every slope tile is network-only, and
    // the layer goes blank the moment the signal does, which is precisely
    // the situation it is most wanted in. (Pinning slope tiles as part of a
    // deliberate area download is SNOW-692; this is the passive half.)
    const basemapOrigins = [
      ...new Set(
        Object.values(BASEMAP_OPTIONS)
          .filter((url) => typeof url === 'string' && url)
          .map((url) => new URL(url).origin)
          .concat(SLOPE_TILE_URL ? [new URL(SLOPE_TILE_URL).origin] : []),
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
  // SNOW-687: routes defaults OFF too, and deliberately NOT like
  // favourites even though both are the signed-in user's own data: a GPX
  // track is visually far heavier than a pin, so it is opt-in.
  //
  // SNOW-645 review: 'downloaded' used to be a key here, a togglable
  // layers-menu row like every other overlay — persisted, seeded on boot
  // and after every basemap swap same as the rest. It is gone from this
  // object entirely now: the downloaded-tiles overlay is no longer that
  // kind of overlay at all, it is governed by downloadedOverlayVisible
  // below instead — Hugo's report was that switching basemap while it was
  // on left its layers-menu dot permanently grey and unclickable, because
  // "any tile pinned under the ACTIVE template" is inherently per-basemap
  // and the row gave no way to say that. A second report, once the first
  // fix bound visibility to the "Manage downloads" sheet being open, was
  // that a bottom-docked full-width mobile sheet then covered the very
  // squares it drew — see downloadedOverlayVisible's own comment for the
  // shape this settled on instead.
  //
  // SNOW-656: 'l4' means the micro-region BOUNDARY and its label alone now —
  // the choropleth it used to carry with them lives behind 'bulletins', a
  // separate row. Both default on, so the map opens exactly as it did.
  // 'bulletins' is the user's PREFERENCE; what is actually painted is that
  // AND-ed with any active suppression, held in bulletinsVisibility below.
  // SNOW-691: slope defaults OFF. It is the only overlay that paints the
  // WHOLE viewport rather than discrete features, so opening the map with it
  // on would put a second full-screen colour scheme under the danger ratings
  // for a visitor who came to read the ratings.
  const overlayState = {
    l1: false, l2: false, l4: true, bulletins: true, resorts: false,
    favourites: true, community_reports: false,
    // SNOW-761: weather defaults OFF, like community_reports — a layer of
    // condition symbols over every station is an opt-in, not the state the
    // map should open in for someone who came to read danger ratings.
    weather: false,
    routes: false, slope: false,
  };

  // SNOW-656: the Bulletins row's live state — the persisted preference plus
  // whatever is currently suppressing it (resort-edit mode; the downloads
  // overlay no longer does, see showDownloadedOverlay). Reassigned wholesale
  // by the transitions in static/js/layer_visibility_core.js, which are pure;
  // every write is followed by applyBulletinsVisibility() so the map and both
  // toggles cannot drift from it. Seeded below, once overlayState has been
  // read out of localStorage.
  const BULLETINS_CORE = self.pwaLayerVisibilityCore;
  let bulletinsVisibility = BULLETINS_CORE.create(overlayState.bulletins);

  // The downloaded-areas overlay's own state. Read by installRegionsLayers
  // (initial layout.visibility), refreshDownloadedOverlay, and
  // window.pwaDownloadedOverlay.isEnabled() (the "Display on the map"
  // switch INSIDE the "Manage downloads" panel reads this, not a flag of
  // its own, so the two can never drift); written only by show()/hide() on
  // window.pwaDownloadedOverlay, which persist it alongside.
  //
  // PERSISTED, reversing SNOW-645's session-scoped inspection mode — see
  // OVERLAY_STORAGE_KEY.downloads in map_state.js for why, and for why the
  // key is a new name rather than SNOW-570's. It is still NOT a key of
  // overlayState: that object's boot loop and its basemap-swap re-seed
  // drive layer visibility through the install functions, and this overlay's
  // two layers are installed from this variable directly. Keeping it out of
  // overlayState is also what keeps a basemap swap from reaching in and
  // closing the overlay out from under an open panel — the swap re-seeds
  // overlayState wholesale, and this must survive it untouched.
  let downloadedOverlayVisible = readBoolStorage(OVERLAY_STORAGE_KEY.downloads, false);

  // The bulletin-boundary layer (internal key ``l3``) is not an overlay the
  // user toggles — it is a companion to the choropleth, drawn whenever the
  // choropleth is. It keeps its own key for the lazy-load machinery (its data
  // is per-date and fetched separately from the region geometry), but its
  // visibility is governed by another key's state rather than its own. This
  // maps an overlay key to the key that governs it; a key absent here governs
  // itself.
  //
  // Rationale: the boundary answers "which of these regions share one
  // bulletin?", which is only a meaningful question while that day's bulletin
  // data is on screen. Shown alone it is a set of outlines around nothing;
  // hidden while the choropleth is on, the fill implies each micro-region was
  // judged independently when most were not.
  //
  // SNOW-656: the governor moved from ``l4`` to ``bulletins``. The boundary is
  // a BULLETIN concept — the dissolved outer boundary of every micro-region
  // sharing one bulletin — and, like the choropleth and unlike the
  // micro-region geography, it is date-bound. It rode on ``l4``'s key only
  // because that key used to mean both things.
  const OVERLAY_VISIBILITY_GOVERNOR = { l3: 'bulletins' };
  const governorFor = (key) => OVERLAY_VISIBILITY_GOVERNOR[key] || key;

  // SNOW-473: this seed is re-run inside the ``styledata`` handler after a
  // basemap swap (search "SNOW-473") — keep the two blocks in sync when adding
  // an overlay key.
  for (const key of ['l1', 'l2', 'resorts', 'community_reports', 'weather', 'routes', 'slope']) {
    overlayState[key] = readBoolStorage(OVERLAY_STORAGE_KEY[key], false);
  }
  overlayState.l4 = readBoolStorage(OVERLAY_STORAGE_KEY.l4, true);
  // SNOW-656: seeded from the legacy ``l4`` key until ``bulletins`` has been
  // written once, so a device carrying ``l4=false`` comes back with BOTH rows
  // off. Raw reads, not readBoolStorage, because "absent" and "explicitly
  // false" have to stay distinguishable for that hand-over to work.
  overlayState.bulletins = BULLETINS_CORE.seedFromLegacy(
    readStorage(OVERLAY_STORAGE_KEY.bulletins),
    readStorage(OVERLAY_STORAGE_KEY.l4),
    BULLETINS_CORE.DEFAULT_STEP,
  );
  bulletinsVisibility = BULLETINS_CORE.create(overlayState.bulletins);
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
        // SNOW-658: a provider row can own more than one country (ALBINA:
        // AT + IT), so it is checked only when EVERY code it switches is on.
        // Checked-when-any would claim coverage the map is not drawing.
        checked = countryCodesFor(key).every((code) => countryState[code]);
      } else {
        checked = overlayState[key];
      }
      btn.setAttribute('aria-checked', checked ? 'true' : 'false');
      // SNOW-656: the Bulletins control is five radio segments carrying a
      // numeric step, not a checkbox — it has no data-overlay-key and so
      // never reaches this loop. applyBulletinsVisibility seeds it instead,
      // from the same overlayState.bulletins this reads for everything else.
    }
  }

  const RESORTS_BY_REGION  = {};

  // The EAWS danger-scale palette, which the standard mandates and this
  // project does not adjust. Literal values because MapLibre paint
  // properties cannot read a CSS variable — the same five colours live in
  // src/css/main.css @theme for every other surface. Keep the two in step.
  //
  // very_high is #820100, from SLF's interpretation guide: EAWS gives level
  // 5 no colour of its own, SLF darkens it (Hugo, SNOW-739). It was
  // #a500a5 — a magenta belonging to no scale — from this layer's first
  // commit, so the map alone painted a level-5 region a colour the legend,
  // the tiles and the calendar never used.
  const RATING_COLOURS = {
    low:          '#ccff66',
    moderate:     '#ffff00',
    considerable: '#ff9900',
    high:         '#ff0000',
    very_high:    '#820100',
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
  //
  // SNOW-737: the frame and the three camera limits are named here rather
  // than written inline in the constructor, because the stored-viewport
  // validation below needs the same numbers. Two copies of `maxZoom` would
  // be a restore that accepts a camera the map then clamps — the silent
  // failure map_viewport_core.js exists to avoid.
  const DEFAULT_BOUNDS = [[5.9, 45.8], [10.5, 47.9]];
  const MIN_ZOOM = 4;
  // SNOW-442: raised from 12. The swisstopo base vector source
  // (ch.swisstopo.base.vt) publishes a TileJSON `maxzoom` of 14 and its
  // style authors layers up to zoom 20; MapLibre overzooms vector tiles
  // cleanly above 14, so 18 was chosen to allow close-in reading without
  // exposing genuinely blank overzoomed tiles at the extreme end.
  const MAX_ZOOM = 18;
  // (Bounds taken from console.log when ?debug=true)
  // West / south / north match the original Western-European frame
  // (Atlantic buffer / French Alps min lat / Stuttgart-ish top). East
  // extended from 17° to 23° to cover the full Austrian / Slovenian /
  // northern-Balkan arc visible in the avalanche-region polygons.
  const MAX_BOUNDS = [[0.9482, 41.9952], [19.6674, 49.9983]];

  // Canonical SLF region-ID shape (e.g. "CH-4115", "AT-02-14",
  // "IT-32-BZ-15-02"). Anything else is rejected before it reaches any href
  // to prevent a malformed GeoJSON payload turning into an open-redirect /
  // javascript: URL on the client.
  //
  // SNOW-737 hoisted this out of the ``load`` handler to here: the deep-link
  // check below runs before the map is constructed and needs the same
  // definition of "a valid region id" the hash resolution, the GeoJSON-id
  // check and the CTA href validation already share. A fourth call site with
  // its own looser idea of the shape is exactly what that sharing exists to
  // prevent.
  const REGION_ID_RE = /^[A-Za-z]{2}(-[A-Za-z0-9]+)+$/;

  // SNOW-737: restore the camera the visitor last left, unless the URL
  // already names somewhere specific.
  //
  // Both deep links are excluded, for DIFFERENT reasons. `?favourite=`
  // flies to its pin inside the `load` handler (see openFavouriteDeepLink),
  // so a restore would only make the visitor watch a pointless jump from
  // their old viewport to the target. A `#REGION-ID` hash does NOT move the
  // camera at all — the initial-load handler calls selectFeature, which
  // frames the region only when AUTOZOOM is on, and that defaults to off —
  // so restoring would open a popup for a region nowhere near the stored
  // view. Falling back to the default frame leaves a shared link behaving
  // exactly as it did before this change.
  //
  // Matched against REGION_ID_RE rather than tested for a non-empty hash: a
  // stray '#' left in the URL by some other surface is not a deep link, and
  // treating it as one would silently cost the visitor their restore.
  const hasDeepLink = (
    REGION_ID_RE.test(location.hash.slice(1))
    || new URLSearchParams(location.search).has('favourite')
  );
  const storedViewport = hasDeepLink ? null : self.pwaViewportCore.restore(
    readStorage(VIEWPORT_STORAGE_KEY),
    { minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM, maxBounds: MAX_BOUNDS },
  );
  // `bounds` and `center`/`zoom` are alternatives, not a pair, so exactly one
  // of the two shapes is spread into the constructor below.
  const initialCamera = storedViewport || {
    bounds: DEFAULT_BOUNDS,
    fitBoundsOptions: { padding: 20 },
  };

  const map = new maplibregl.Map({
    container: 'map',
    ...initialCamera,
    // ESRI basemaps (see resolveBasemapStyle) can't be handed to the
    // constructor synchronously — boot them with an empty style and swap
    // the fetched+rewritten style in once it resolves (below). Native
    // basemaps load directly from their URL.
    style: ESRI_BASEMAP_KEYS.has(initialBasemapKey)
      ? { version: 8, sources: {}, layers: [] }
      : initialBasemapUrl,
    minZoom: MIN_ZOOM,
    maxZoom: MAX_ZOOM,
    maxBounds: MAX_BOUNDS,
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

  // ---- Remember where the visitor left the camera (SNOW-737) ----
  //
  // Bound HERE, immediately after the constructor, rather than inside the
  // ``load`` handler. ``load`` does not fire when the basemap style fails to
  // load, which is a real state this map reaches — it is the whole reason
  // SNOW-483's inline fallback style exists — and a viewport that quietly
  // stops being remembered whenever the visitor is offline is worse than one
  // that is never remembered at all, because it looks like it works.
  //
  // Binding this early is safe: the constructor's ``bounds`` framing sets the
  // camera WITHOUT emitting ``moveend`` (verified against MapLibre 4.7.1 —
  // zero events, synchronously or on any later tick). So the default frame is
  // never mistaken for a move, and a deep-link boot, where the restore is
  // deliberately skipped, cannot overwrite the stored viewport with
  // Switzerland.
  //
  // Every real ``moveend`` is persisted, including the ones the custom-area
  // framing overlay and the place picker drive. Those are places the visitor
  // deliberately navigated to, and a suppression list keyed on transient UI
  // modes would be an abstraction with one caller.
  map.on('moveend', () => {
    const centre = map.getCenter();
    writeStorage(VIEWPORT_STORAGE_KEY, self.pwaViewportCore.serialise({
      lng: centre.lng,
      lat: centre.lat,
      zoom: map.getZoom(),
      bearing: map.getBearing(),
      pitch: map.getPitch(),
    }));
  });

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
    // SNOW-761: weather symbols sit above region fills and boundaries but
    // below the more personal pin layers, so a favourite star or a
    // community-report flag at the same point is never hidden behind a
    // weather icon.
    'weather-point',
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

  // The choropleth's two blend weights (0.55 resting, 0.85 emphasised) lived
  // here. They were `fill-opacity` values until #625 made them weights
  // against a fixed backdrop; SNOW-656 has taken the fill translucent again,
  // so they are opacity values once more — see REGION_FILL_OPACITY and
  // EMPHASIS_RATIO below, which preserve their ratio.

  // The whole choropleth is TRANSLUCENT — every region, rated or not, painted
  // at half strength so the terrain, roads and place names read through it.
  //
  // This REVERSES docs/decisions/choropleth-blended-not-translucent.md (#625,
  // merged 2026-08-09), which made the fill opaque and baked the translucency
  // into the colours instead. That decision was taken to stop one rating
  // rendering as a different colour on each of the five basemaps: a
  // translucent fill is composited by MapLibre against whatever the basemap
  // draws underneath, and the five draw very different things. Going
  // translucent again brings that drift back, and the ADR needs updating or
  // reversing to match — see the note in this ticket.
  //
  // The colours must be RAW here, not the backdrop-composited ones. The
  // composite already pulls each colour ~45% toward the backdrop; painting
  // that at 0.5 would blend twice and wash every rating out to a pastel.
  // Compositing and translucency are two ways of doing the same job and only
  // one can be in force.
  // The resting opacity is the user's chosen step, held in bulletinsVisibility
  // and AND-ed with any suppression — there is no constant to read. The
  // Bulletins control in the layers menu writes it; `applyBulletinsVisibility`
  // repaints from it.

  // Selection/preview emphasis, which the two blend weights used to carry.
  // With the fill translucent, opacity is the lever instead — the ratio
  // mirrors the old 0.55/0.85 weights so a selected region still reads
  // stronger than its neighbours at every step.
  const EMPHASIS_RATIO = 0.85 / 0.55;
  const emphasisOpacity = (resting) => Math.min(1, +(resting * EMPHASIS_RATIO).toFixed(3));

  // "This region is selected or being previewed from the sheet." Module scope
  // because both the install path and applyBulletinsVisibility build the
  // opacity expression, and a second copy would be free to drift.
  const REGION_EMPHASISED = [
    'any',
    ['boolean', ['feature-state', 'selected'], false],
    ['boolean', ['feature-state', 'previewing'], false],
  ];

  /**
   * ``fill-opacity`` for the choropleth at a given resting opacity: that
   * value, stepped up for a selected or previewed region.
   *
   * A plain number when the resting value is 0 — at the off step there is
   * nothing to emphasise, and a selected region must not be the one thing
   * still painted after the user switched the layer off.
   *
   * @param {number} resting The effective resting opacity, 0–1.
   * @returns {Array|number} A MapLibre ``case`` expression, or 0.
   */
  const regionFillOpacity = (resting) => (
    resting > 0
      ? ['case', REGION_EMPHASISED, emphasisOpacity(resting), resting]
      : 0
  );

  // ``compositeOverBackdrop`` was destructured here for the choropleth's
  // fill colours. The fill is translucent again (see the Bulletins step
  // control), so the colours are raw and nothing in this file composites any
  // more. The helper itself stays in choropleth_core.js — the legend and any
  // other surface wanting "the same colour as the map" still needs it, and
  // the ADR it belongs to has to be settled before it goes.
  //
  // A DEBUG-only ``window.pwaChoroplethOpacity`` slider lived here while the
  // treatment was being chosen. It is gone: the five-step control in the
  // layers menu is the same lever, shipped, so keeping a second one would be
  // two controls writing one value.

  // SNOW-570/SNOW-587: the cached-tiles overlay — one square per tile
  // actually in the pinned cache. Fainter than a download's live grid —
  // this is ambient state the user can leave switched on, not transient
  // feedback demanding attention. Drawn at the band's detail floor, the
  // same zoom the download grid uses, so the two describe the same
  // squares.
  const CACHED_TILES_OPACITY = 0.55;
  const CACHED_TILES_LINE_OPACITY = 0.4;
  const CACHED_TILES_ZOOM = 14;

  // The squares are a diagonal HATCH rather than a flat tint, and that is
  // what lets them share the map with the choropleth: a hatch makes no
  // colour claim, so the danger colour reads through it. The pixels — and
  // the seam invariant that makes them tile — live in
  // static/js/hatch_core.js; see its header. Screen-space at a fixed period
  // (fill-pattern does not scale with zoom), so contiguous downloaded
  // country reads as texture at z6 and as stripes across a single square at
  // z14 — the same mark at both ends.
  const HATCH_CORE = self.pwaHatchCore;

  // The colour of the squares is the identity colour of the basemap they
  // belong to — and, since the overlay shows the ACTIVE basemap's downloads
  // and only those, that is one flat colour resolved per refresh rather
  // than a per-feature expression.
  //
  // It was a MapLibre `match` on each tile's own `basemapKey` for the life
  // of SNOW-645, when the overlay painted every basemap's downloads at
  // once. Hugo's call: two basemaps' squares over the same ground is a
  // picture of nothing anyone asked for — "it should filter to the current
  // basemap, so it never overlays downloads. If you are on Swisstopo and
  // toggle on the downloads it shows Swisstopo downloads. If you then
  // switch maps it honours the toggle and shows the new map downloads."
  // With the tile set filtered in refreshDownloadedOverlay, every feature
  // in it belongs to the active basemap by construction, so the expression
  // had exactly one arm and the `basemapKey` property nothing left to
  // discriminate. Both are gone.
  //
  // basemapIdentityColour(null) — --color-sync-ok — remains the answer for
  // an unresolved basemap: the picker DOM not yet readable, or a template
  // recorded before SNOW-645 wrote keys. That is the same "green matching
  // the sync dots" default the layer is created with, before any tile has
  // been probed at all.

  /**
   * The id of the hatch image for one basemap key. ``null``/unknown gets the
   * fallback image, painted in the same colour ``basemapIdentityColour``
   * falls back to, so the fill and the outline always agree.
   *
   * @param {?string} key A settings.BASEMAP_STYLES key, or null.
   * @returns {string} A map image id.
   */
  const hatchImageId = (key) => `cached-tiles-hatch-${key || 'default'}`;

  /**
   * Resolve any CSS colour string to its `[r, g, b]` channels.
   *
   * A 1×1 canvas fill, because nothing else parses every CSS colour
   * syntax: a `@theme` token in `src/css/main.css` is hex today, but
   * `oklch()` is one Tailwind upgrade away and no amount of
   * string-slicing turns that into three channels.
   *
   * Shared by the downloaded-areas hatch and the route end markers —
   * extracted at the second caller rather than copied a second time, per
   * the design-system rule against a third inline copy.
   *
   * @param {string} colour Any CSS colour value.
   * @returns {Array<number>} `[r, g, b]`, each 0-255.
   */
  const cssColourChannels = (colour) => {
    const probe = document.createElement('canvas');
    probe.width = 1;
    probe.height = 1;
    const ctx = probe.getContext('2d');
    ctx.fillStyle = colour;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b];
  };

  /**
   * Build the hatch image for one identity colour, as raw RGBA.
   *
   * @param {string} colour Any CSS colour value.
   * @returns {{width: number, height: number, data: Uint8ClampedArray}}
   *   A MapLibre StyleImage.
   */
  const buildHatchImage = (colour) => {
    const [r, g, b] = cssColourChannels(colour);
    return HATCH_CORE.hatchPixels(r, g, b);
  };

  /**
   * Register the hatch image for one key if the style is not already holding
   * it. ``setStyle`` drops every image along with every layer, so this is
   * called again from both re-install paths rather than once at boot;
   * ``hasImage`` is what makes the repeat calls free.
   *
   * @param {?string} key A settings.BASEMAP_STYLES key, or null.
   * @returns {void}
   */
  const ensureHatchImage = (key) => {
    const id = hatchImageId(key);
    if (map.hasImage(id)) return;
    // pixelRatio 2: the period above is in device pixels, so this is a 6px
    // hatch on screen — fine enough to read as texture, coarse enough that
    // the choropleth colour is visible between the strokes.
    map.addImage(id, buildHatchImage(basemapIdentityColour(key)), { pixelRatio: 2 });
  };

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
    //
    // The fill is TRANSLUCENT — see REGION_FILL_OPACITY for what that
    // reverses and what it costs.
    //
    // RAW colours throughout — the backdrop composite is gone. It and the
    // translucency below are two ways of doing the same job (softening the
    // fill), and applying both blends every rating twice: at the old resting
    // weight ``#e0e0e0`` already composited to ``#e8e7e5``, four points off
    // the ``#f2f0ec`` backdrop, and halving that left nothing visible at all.
    const fillColours = [
      'case',
      ['==', ['get', 'covered'], false],
      UNCOVERED_FILL_COLOUR,
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
    ];
    // SNOW-656: the fill answers to the "Bulletins" row, not to "Micro
    // regions" — and it is hidden by OPACITY, not by visibility. This layer
    // is the map's hit-test target (``queryRenderedFeatures`` on click, the
    // hover cursor's mouseenter/mouseleave), and a layer at
    // ``visibility: none`` returns nothing from that query, so switching
    // Bulletins off that way would leave visible borders that cannot be
    // tapped. ``regionsFillLayout`` derives both values from the two rows —
    // see its own docstring for the four-quadrant table, including why the
    // layer IS dropped when neither row is on.
    const fillLayout = BULLETINS_CORE.regionsFillLayout(
      overlayState.l4, bulletinsVisibility,
    );
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      layout: {
        visibility: fillLayout.visibility,
      },
      paint: {
        'fill-color': fillColours,
        'fill-opacity': regionFillOpacity(fillLayout.opacity),
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

    // SNOW-570/SNOW-587: the downloaded-tiles overlay — one square per tile
    // actually in the pinned cache, at the band's detail floor. Derived from
    // the cache ALONE — no stored record involved — so it cannot drift from
    // what is on disk: eviction, a basemap swap and Clear Site Data all
    // change the answer, and all of them show up here for free.
    //
    // SNOW-645 review: no longer a layers-menu row — its visibility is now
    // an "Available offline" toggle INSIDE the "Manage downloads" sheet
    // (downloadedOverlayVisible, written only by window.pwaDownloadedOverlay's
    // show()/hide()). The layer is still installed whether or not that is
    // switched on right now — a style swap mid-session reinstalls it with
    // everything else rather than leaving show()/hide() pointing at a layer
    // that isn't there.
    if (!map.getSource('cached-tiles')) {
      map.addSource('cached-tiles', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
    }
    // The pattern below names this image, and a `fill-pattern` pointing at an
    // image the style does not hold paints nothing — so it is registered
    // before the layer that uses it, on every re-install.
    ensureHatchImage(null);
    map.addLayer({
      id: 'cached-tiles-fill',
      type: 'fill',
      source: 'cached-tiles',
      layout: { visibility: downloadedOverlayVisible ? 'visible' : 'none' },
      paint: {
        // No tiles painted yet at (re)install time, and the active basemap's
        // template is not necessarily resolvable this early — the fallback
        // hatch. refreshDownloadedOverlay repaints this in the active
        // basemap's own identity colour as soon as it can read one.
        'fill-pattern': hatchImageId(null),
        'fill-opacity': CACHED_TILES_OPACITY,
      },
    });
    map.addLayer({
      id: 'cached-tiles-line',
      type: 'line',
      source: 'cached-tiles',
      layout: {
        visibility: downloadedOverlayVisible ? 'visible' : 'none',
        'line-join': 'round',
        'line-cap': 'round',
      },
      paint: {
        'line-color': basemapIdentityColour(null),
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
    // SNOW-656: mirror the Bulletins step onto the menu control. The layers
    // above were just built from ``bulletinsVisibility``, but the control is
    // server-rendered with the default step pre-checked — so without this a
    // device with a stored step paints at that step while the menu still
    // shows 50%, and the two only agree once the user touches something.
    // Cheap and idempotent, so it runs on every (re)install rather than only
    // the first.
    applyBulletinsVisibility();
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

  /**
   * Broadcast that one of the three roundel-owned overlays has changed
   * whether it is drawn on the map.
   *
   * SNOW-658: the downloads, favourites and field-observation roundels each
   * carry a visible "my overlay is on the map" state, and one signal drives
   * all three (static/js/map_roundel_overlay_state.js) rather than three
   * hand-rolled ones — three separate signals is how the roundels diverged
   * in the first place. No detail: the listener re-reads all three bridges'
   * ``isVisible()``, so this only has to say "something moved", and a
   * fourth overlay needs no new event.
   *
   * Fired from every writer of any of the three, and — since SNOW-658's
   * review made ``isVisible()`` answer from paint rather than from the
   * preference — from everything that changes what is DRAWN without the
   * preference moving at all: ``showDownloadedOverlay`` /
   * ``hideDownloadedOverlay`` (via ``announceDownloadedOverlay``),
   * ``showPanelOverlay`` /
   * ``hidePanelOverlay``, the settle of a lazy ``snowdesk:overlay-load``
   * (success and failure alike), ``installFavouritesLayer`` /
   * ``installCommunityReportsLayer``, once at boot when the bridges are
   * published, and again after the ``styledata`` handler a basemap swap runs
   * has re-installed the layers. static/js/map_placement_focus.js dispatches
   * the same event directly — it hides every app layer without going through
   * any bridge, so it is a writer of all three at once.
   *
   * Declared up here beside ``layerPainted`` rather than beside the bridges
   * at the foot of this IIFE: the install functions below call it, and a
   * ``const`` referenced before its own line has run is a temporal-dead-zone
   * throw waiting for the first caller that runs at parse time.
   *
   * @returns {void}
   */
  const announceOverlayVisibility = () => {
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
  };

  /**
   * Whether one layer is currently painted on the map.
   *
   * SNOW-658: the one place this question is answered, because it is asked
   * from two directions now — the resort exclusion just below, and the three
   * overlay bridges' ``isVisible()`` at the foot of this IIFE. A layer that
   * was never installed (a lazy overlay whose fetch has not landed, or
   * failed) is not painted, which is why the ``getLayer`` guard is part of
   * the answer rather than a defensive wrapper around it.
   *
   * @param {string} layerId - A MapLibre layer id.
   * @returns {boolean} True when the layer exists and is not hidden.
   */
  const layerPainted = (layerId) =>
    !!map.getLayer(layerId) &&
    map.getLayoutProperty(layerId, 'visibility') !== 'none';

  // SNOW-499: whether the favourites overlay is currently drawn. The resort
  // exclusion below is only justified while the favourite star is actually
  // visible to stand in for the hidden resort dot — with the favourites
  // overlay toggled off there is no star, so a favourited resort must fall
  // back to its plain resort dot rather than vanishing from the map
  // entirely. Reads the live layer state, so it is correct however the
  // caller reached here (boot, toggle on, toggle off).
  const favouritesLayerVisible = () => layerPainted('favourites-pin');

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
  // favouritesGeojsonCache is set to a new authoritative payload. The key
  // is the resort SLUG (SNOW-796): favourites.geojson carries it as
  // ``resort_slug`` and resorts.geojson as ``id``, so the exclusion filter
  // compares like with like.
  const syncFavouritedResortIds = (geojson) => {
    favouritedResortIds = [];
    if (geojson && Array.isArray(geojson.features)) {
      for (const feature of geojson.features) {
        const resortSlug = feature.properties && feature.properties.resort_slug;
        if (resortSlug != null) favouritedResortIds.push(resortSlug);
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
        'icon-color': MARKER_FAVOURITE_COLOUR,
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
        'text-color': MARKER_FAVOURITE_COLOUR,
        'text-halo-color': 'rgba(255,255,255,0.95)',
        'text-halo-width': 1.4,
      },
    });
    raiseMarkerLayers();
    // SNOW-658 review: the pins are on the map (or explicitly hidden) only
    // as of this line, and every path that gets them there ends here — the
    // boot restore, the lazy load, the user's very first saved favourite,
    // and the re-install after a basemap swap. Announcing from the one
    // install function covers all four; announcing from each caller is how
    // one of them would eventually be forgotten and leave a stale ring.
    announceOverlayVisibility();
  };

  // The route line's two colours, named once. MapLibre paint properties
  // cannot reference a CSS `@theme` token at all, so these mirror
  // --color-route-line and --color-route-line-casing in src/css/main.css and
  // are kept in step with them by hand — the idiom installFavouritesLayer
  // uses for its star colour. They are constants rather than four literals
  // because the end markers are painted from the same two values: a start
  // dot in a different fuchsia from its own line would be a bug nobody
  // would think to look for.
  const ROUTE_LINE_COLOUR = '#c026d3';
  const ROUTE_CASING_COLOUR = '#1a1916';
  // SNOW-764: the pending (shared-with-you, not yet saved) line's own
  // colour, mirroring --color-route-line-pending in src/css/main.css. A
  // different HUE rather than a lighter fuchsia, because the distinction is
  // categorical — "this one is not yours yet" — and a tint of the owned
  // colour reads as the same thing seen through haze. Teal is far enough
  // round the wheel to be unmistakable beside the fuchsia and is not in the
  // EAWS danger palette, so a pending route can never be mistaken for a
  // rating.
  const ROUTE_PENDING_COLOUR = '#0d9488';

  /** Map image ids for the two route end markers. */
  const ROUTE_START_ICON = 'route-start-dot';
  const ROUTE_END_ICON = 'route-finish-flag';

  /**
   * Register the start dot and finish flag, unless the style already holds
   * them.
   *
   * `setStyle` drops every registered image along with every layer, so this
   * is called from installRoutesLayer rather than once at boot — the
   * basemap-swap re-install goes through there too, and `hasImage` is what
   * makes the repeat call free. Same shape as ensureHatchImage above.
   *
   * `addImage` must be given the core's own PIXEL_RATIO: the icon is sized
   * in device pixels, and registering a 40px image at ratio 1 would draw a
   * marker twice the intended size on every screen.
   *
   * @returns {void}
   */
  const ensureRouteMarkerImages = () => {
    const core = self.pwaRouteMarkersCore;
    if (!core) return;
    const ratio = { pixelRatio: core.PIXEL_RATIO };
    if (!map.hasImage(ROUTE_START_ICON)) {
      map.addImage(
        ROUTE_START_ICON,
        core.startDotPixels(...cssColourChannels(ROUTE_LINE_COLOUR)),
        ratio,
      );
    }
    if (!map.hasImage(ROUTE_END_ICON)) {
      map.addImage(
        ROUTE_END_ICON,
        core.finishFlagPixels(...cssColourChannels(ROUTE_CASING_COLOUR)),
        ratio,
      );
    }
  };

  /**
   * The start/finish point features for a routes payload.
   *
   * A guarded wrapper over the core: `route_markers_core.js` is a separate
   * classic script, and every other core on this map is read defensively
   * for the same reason — a load failure should cost the markers, not the
   * whole routes overlay.
   *
   * The endpoints layer is deliberately NOT in MARKER_EXCLUSION_LAYERS. A
   * tap on a marker should open the route it belongs to, and it already
   * does: the marker sits on the line's own endpoint, well inside the 8px
   * tolerance markerUnderPoint gives `routes-line`. Adding it to the
   * exclusion set would mean maintaining a second path to the same popup.
   *
   * @param {?object} geojson The routes FeatureCollection.
   * @returns {{type: string, features: Array<object>}} A point
   *   FeatureCollection, empty when the core is unavailable.
   */
  const routeEndpointsFor = (geojson) => {
    const core = self.pwaRouteMarkersCore;
    if (!core) return { type: 'FeatureCollection', features: [] };
    return core.endpointsGeojson(geojson);
  };

  // SNOW-764: which features each of the three route line layers draws.
  //
  // ``pending`` is present and true only on a share this session has
  // followed and not yet claimed (apps/routes/views.py's routes_geojson).
  // ``['!=', ['get', 'pending'], true]`` rather than ``['==', …, false]``
  // because an OWNED feature omits the property entirely — ``get`` answers
  // null for it, which is not equal to false either.
  const OWNED_ROUTE_FILTER = ['!=', ['get', 'pending'], true];
  const PENDING_ROUTE_FILTER = ['==', ['get', 'pending'], true];

  // SNOW-687: install the saved-routes layer — one GeoJSON source of
  // LineStrings (routes:geojson), drawn as TWO ``line`` layers (three
  // since SNOW-764 — see the pending one below).
  //
  // The casing is added FIRST so MapLibre paints it underneath: a wider,
  // translucent dark under-stroke, with the route colour over it. One
  // stroke on its own is unreadable somewhere — a mid-saturation line
  // vanishes into the choropleth's orange band, and a pale one into a
  // white satellite basemap — and the casing is what lets a single colour
  // work over both. Both widths are zoom-interpolated so the line stays a
  // hairline at country scale and a followable track at valley scale.
  //
  // Idempotent, like installFavouritesLayer: early-returns on an existing
  // source, so the lazy load, the basemap-swap re-install and any later
  // refresh can all call it without duplicating layers.
  const installRoutesLayer = (geojson) => {
    if (!geojson || map.getSource('routes')) return;
    routesGeojsonCache = geojson;
    map.addSource('routes', { type: 'geojson', data: geojson });
    map.addLayer({
      id: 'routes-line-casing',
      type: 'line',
      source: 'routes',
      layout: {
        visibility: overlayState.routes ? 'visible' : 'none',
        'line-cap': 'round',
        'line-join': 'round',
      },
      paint: {
        // See ROUTE_CASING_COLOUR above for why the value is named here
        // rather than referenced from the stylesheet.
        'line-color': ROUTE_CASING_COLOUR,
        'line-opacity': 0.55,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 3, 12, 7, 16, 11],
      },
    });
    map.addLayer({
      id: 'routes-line',
      type: 'line',
      source: 'routes',
      // SNOW-764: owned routes only. The pending ones get their own layer
      // below so they can be painted differently; without the filter they
      // would be drawn twice, once in each colour, with whichever layer
      // sits on top winning.
      filter: OWNED_ROUTE_FILTER,
      layout: {
        visibility: overlayState.routes ? 'visible' : 'none',
        'line-cap': 'round',
        'line-join': 'round',
      },
      paint: {
        'line-color': ROUTE_LINE_COLOUR,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
      },
    });
    // SNOW-764: the shared-with-you line. A THIRD layer rather than a
    // data-driven `line-color` on the one above, because the difference is
    // not only colour: this line is DASHED, and `line-dasharray` is not a
    // data-driven property in MapLibre — it cannot vary per feature, so a
    // dashed subset needs a layer of its own.
    //
    // Dashed AND a different hue, deliberately both. The dash carries the
    // meaning on its own for a colour-blind reader; the hue carries it at a
    // zoom where a 1.5px dashed line and a 1.5px solid one are hard to tell
    // apart. Neither is decoration: a route somebody sent you and a route
    // you own support different actions, and the map has to say which is
    // which before it is tapped.
    //
    // It is added AFTER the owned line, so a pending route drawn over one
    // of your own is the one on top — which is right, because it is the one
    // with an action attached.
    map.addLayer({
      id: 'routes-line-pending',
      type: 'line',
      source: 'routes',
      filter: PENDING_ROUTE_FILTER,
      layout: {
        visibility: overlayState.routes ? 'visible' : 'none',
        'line-cap': 'butt',
        'line-join': 'round',
      },
      paint: {
        'line-color': ROUTE_PENDING_COLOUR,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
        // In line-widths, so the dash keeps its proportions as the line
        // thickens with zoom. 'line-cap' is butt rather than round above
        // for the same reason: round caps on a short dash close the gaps.
        'line-dasharray': [2, 1.5],
      },
    });
    // SNOW-687 follow-up: the start dot and finish flag. Their own point
    // source, derived from the same payload — MapLibre cannot symbolise
    // "the ends of a LineString" (`symbol-placement: 'line'` repeats a
    // symbol ALONG one, which is a different thing), so the endpoints are
    // computed once here and kept beside the lines.
    //
    // minzoom 10 matches the resort labels, and for their reason: it keeps
    // the markers off-screen until the map is genuinely zoomed in, rather
    // than littering a country-scale view with flags on tracks a few
    // pixels long. It is also the zoom at which a route's two ends are far
    // enough apart to read as two markers.
    ensureRouteMarkerImages();
    map.addSource('route-endpoints', {
      type: 'geojson',
      data: routeEndpointsFor(geojson),
    });
    map.addLayer({
      id: 'routes-endpoints',
      type: 'symbol',
      source: 'route-endpoints',
      minzoom: 10,
      layout: {
        visibility: overlayState.routes ? 'visible' : 'none',
        'icon-image': [
          'case', ['==', ['get', 'role'], 'start'], ROUTE_START_ICON, ROUTE_END_ICON,
        ],
        // Both ends of a short track can sit close together, and the whole
        // point of these markers is that BOTH are visible — MapLibre's
        // default collision would silently drop one.
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
        // The dot is centred on its point; the flag hangs off a pole whose
        // foot is the point, so it is anchored bottom-left.
        'icon-anchor': [
          'case', ['==', ['get', 'role'], 'start'], 'center', 'bottom-left',
        ],
      },
    });
    // The lines were just added on top of everything, so lift the pin
    // layers back over them — a favourite star or a report flag sitting on
    // a route must stay visible and stay tappable (MARKER_EXCLUSION_LAYERS
    // gives them the tap, and burying them would make that invisible).
    raiseMarkerLayers();
    // Same one-install-function-announces rule installFavouritesLayer
    // carries: every path that gets these lines onto the map (or explicitly
    // hides them) ends here, so the roundel ring is settled from one place.
    announceOverlayVisibility();
  };

  // ==== SNOW-828: the trip a ?trip= / ?trip_share= arrival is drawing ====
  //
  // A trip's route is NOT part of the routes overlay. It has no switch in
  // the layers menu, no row in the routes panel and no entry in
  // ``overlayState``, because it is not a thing the visitor keeps — it is
  // one route, drawn because they arrived on a link that named it, and gone
  // on the next navigation. Folding it into the routes source would have
  // reused the line, the popup and the fit for free, and it is the option
  // this ticket rejected: it would put trips inside the routes app's feed
  // and make an owner-scoped endpoint conditionally not (see
  // apps/trips/views.py's own note beside the two endpoints).
  //
  // So: its own source, its own layers, its own cache for the styledata
  // re-install. The line is painted in the SAME colours as an owned route,
  // deliberately — it is a route, and inventing a third route colour would
  // make the map's palette say something it does not mean. What identifies
  // it is the arrival banner, which names the trip and links back to it.
  const TRIP_ROUTE_SOURCE = 'trip-route';
  const TRIP_MEETING_ICON = 'trip-meeting-point';

  // The last-fetched trip collection, for the basemap-swap re-install. Null
  // for every visit that did not arrive on a trip link, which is almost all
  // of them — and the guard the styledata handler reads.
  let tripRouteGeojsonCache = null;

  /**
   * Register the meeting-point icon, once.
   *
   * The same recoloured start dot the trip page uses
   * (`static/js/trip_map.js`), from the same core and at the same size, so
   * the marker a reader saw on the trip page is the marker they find on the
   * map. Registered under an id of its own rather than reusing
   * ROUTE_START_ICON: that one is the START of a track, and a meeting point
   * is not — they coincide often and mean different things.
   */
  const ensureTripMeetingImage = () => {
    const core = self.pwaRouteMarkersCore;
    if (!core || map.hasImage(TRIP_MEETING_ICON)) return;
    map.addImage(
      TRIP_MEETING_ICON,
      core.startDotPixels(...cssColourChannels(ROUTE_LINE_COLOUR)),
      { pixelRatio: core.PIXEL_RATIO },
    );
  };

  /**
   * Install the trip's line, its casing and its meeting marker.
   *
   * Idempotent on the source, like every other install function here, so
   * the fetch path and the basemap-swap re-install can both call it.
   *
   * Always visible: there is no switch that could have turned it off, and
   * an arrival that drew a hidden line would be a link to nothing.
   *
   * @param {?Object} geojson The FeatureCollection from the trips endpoint.
   */
  const installTripRouteLayers = (geojson) => {
    if (!geojson || map.getSource(TRIP_ROUTE_SOURCE)) return;
    tripRouteGeojsonCache = geojson;
    ensureTripMeetingImage();
    map.addSource(TRIP_ROUTE_SOURCE, { type: 'geojson', data: geojson });
    map.addLayer({
      id: 'trip-route-line-casing',
      type: 'line',
      source: TRIP_ROUTE_SOURCE,
      filter: ['==', ['get', 'kind'], 'route'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ROUTE_CASING_COLOUR,
        'line-opacity': 0.55,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 3, 12, 7, 16, 11],
      },
    });
    map.addLayer({
      id: 'trip-route-line',
      type: 'line',
      source: TRIP_ROUTE_SOURCE,
      filter: ['==', ['get', 'kind'], 'route'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ROUTE_LINE_COLOUR,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
      },
    });
    map.addLayer({
      id: 'trip-route-meeting',
      type: 'symbol',
      source: TRIP_ROUTE_SOURCE,
      filter: ['==', ['get', 'kind'], 'meeting'],
      layout: {
        'icon-image': TRIP_MEETING_ICON,
        // No minzoom, unlike the route endpoints: there is exactly one of
        // these on the map and it is the thing the reader most needs to
        // find, so it stays visible at every zoom the arrival can land on.
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
    });
    // The lines went on top of everything — lift the pins back over them,
    // for the reason installRoutesLayer states.
    raiseMarkerLayers();
  };

  /**
   * Honour a ``/?trip=<uuid>`` or ``/?trip_share=<token>`` arrival
   * (SNOW-828): fetch the trip's route, draw it, frame it, and say which
   * trip it is.
   *
   * The way OUT of a trip page's 320px canvas. For a recipient that
   * canvas is the ONLY place the route is ever rendered, so every
   * limitation of it — no layers, no danger overlay, no scrubber — is the
   * whole experience of looking at the terrain rather than a summary of
   * it. This is what makes it a summary.
   *
   * TRANSIENT, and that is the design rather than a shortcut. The
   * parameter is stripped before the fetch even resolves, nothing is
   * written to the session, and a reload of the bare ``/`` shows no trip.
   * A route share persists because it is an offer awaiting a decision
   * with nowhere else to live; a trip has a durable page of its own, and
   * the banner this reveals is a link straight back to it.
   *
   * Silent in every case it cannot satisfy, exactly as the favourite and
   * route-share deep links are. A revoked or expired token answers 404
   * and a trip the viewer is not on answers 403 or 404 — none of which
   * this can explain better than the trip page itself can, and all of
   * which would be a puzzle rather than an explanation if announced over
   * a map the visitor may have meant to open anyway.
   *
   * Declared here beside ``installTripRouteLayers`` rather than up with
   * the other deep links, because unlike them it reaches no popup
   * machinery — but INVOKED from the same place they are, inside
   * ``map.on('load')``, where the style is ready to take the source it
   * adds.
   *
   * @returns {Promise<void>}
   */
  const openTripDeepLink = async () => {
    const core = self.pwaTripDeepLinkCore;
    if (!core) return;
    const link = core.read(location.search);
    if (!link) return;

    // Stripped FIRST, before anything can fail: a parameter left in the
    // address bar is a standing instruction, and a fetch that 404s must
    // not leave one behind to be re-honoured on the next refetch.
    const query = core.strip(location.search);
    history.replaceState(null, '', location.pathname + query + location.hash);

    const url = core.endpointFor(link, TRIP_ROUTE_URL_TEMPLATES);
    if (!url) return;

    let collection = null;
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) return;
      collection = await response.json();
    } catch (err) {
      return;
    }

    const features = (collection && collection.features) || [];
    const route = features.find(
      (f) => f && f.properties && f.properties.kind === 'route',
    );
    if (!route) return;

    installTripRouteLayers(collection);

    // Frame it. The snapshot's own bbox, flat as it is stored and nested
    // as fitBounds wants it — the same reshape trip_map.js makes.
    const bounds = route.properties.bounds;
    if (Array.isArray(bounds) && bounds.length === 4) {
      map.fitBounds(
        [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        { padding: 40 },
      );
    }

    // Say which trip this is. Without this the arrival is an unexplained
    // line: the trip is in no panel and has no popup, by choice.
    const banner = document.getElementById('trip-arrival-banner');
    const title = document.getElementById('trip-arrival-banner-title');
    const back = document.getElementById('trip-arrival-back');
    if (!banner || !title) return;
    // textContent, never innerHTML: the name is organiser-authored.
    title.textContent = route.properties.name || '';
    const pageUrl = route.properties.page_url;
    if (back && pageUrl) {
      back.addEventListener('click', () => {
        location.href = pageUrl;
      });
    } else if (back) {
      back.hidden = true;
    }
    banner.classList.remove('hidden');
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
        'circle-color': MARKER_OBSERVATION_COLOUR,
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
        'icon-color': MARKER_OBSERVATION_COLOUR,
        // SNOW-419: age fade — baked into each feature by
        // withCommunityReportsAgeOpacity before install/setData.
        'icon-opacity': ['get', '_ageOpacity'],
      },
    });
    raiseMarkerLayers();
    // SNOW-658 review: as for the favourites install above — the flags are
    // on the map only as of this line, and the roundel's ring reads paint.
    announceOverlayVisibility();
  };

  // ---------------------------------------------------------------------
  // SNOW-761: Weather overlay — one condition symbol per public Location,
  // captioned with the day's max temperature.
  // Everything pure about it — the code -> icon mapping, the label, the
  // date projection and the cluster-to-lowest collapse — lives in
  // map_weather_core.js (window.pwaWeatherCore) so it is Vitest-covered;
  // everything here needs a real MapLibre `map` and is glue only.
  // ---------------------------------------------------------------------

  // The raw, unprojected multi-day payload from WEATHER_URL (or its
  // offline-cached copy). Retained so a date change, a zoom change or a
  // basemap swap can re-derive what is drawn with no refetch — the payload
  // carries the whole forecast window, so re-projection is a pure in-memory
  // transform.
  let weatherGeojsonCache = null;

  // The weather icons are multi-path, image-shaded SVGs. Unlike the favourite
  // star and the community-report flag (single-colour Path2D fills,
  // registered `sdf: true`), SDF discards colour and keeps only the alpha
  // mask, which cannot carry that — so these are rasterised full-colour via
  // an <img> decode. Decoded once per filename into this module-level
  // cache; every later map.addImage (including the re-register after a
  // basemap setStyle wipes registered images) is then synchronous from the
  // cache, preserving the "image id exists before addLayer references it"
  // invariant every other icon on this map relies on. Image ids ARE the
  // filenames, so the layer's `['get', 'icon']` reads the projected
  // property directly with no id-mapping table.
  // SNOW-791: the active icon set's directory, resolved server-side and
  // handed over on the map element, so the map and the server-rendered
  // weather partials can never disagree about which set is being shown.
  // Falls back to the built-in path if the attribute is absent.
  const WEATHER_ICON_BASE_URL =
    mapEl.dataset.weatherIconDir || '/static/icons/weather/snowdesk/';
  // Whether this set needs the canvas silhouette edge painted for it.
  const WEATHER_ICON_HALO = mapEl.dataset.weatherIconHalo !== 'false';
  // Logical (CSS-pixel) footprint of the decoded icon — matches the
  // forecast panel's day-strip icon (`w-8 h-8`).
  const WEATHER_ICON_RASTER_SIZE = 27;
  // The icon viewBox, and the box its ink actually occupies inside it.
  // Measured off the rendered alpha channel, not read off the markup.
  //
  // The Yr set fills its box: the union alpha bbox across all thirteen
  // source drawings is x[0, 93] y[2, 100] in a 100-unit viewBox, so the
  // ink box IS the viewBox and the crop below is a no-op (SNOW-791). It is
  // kept rather than deleted because the numbers are a property of whichever
  // set is vendored, not of this code — the Meteocons set it was written for
  // left a uniform 16-unit transparent border on every side, and a future
  // set may too.
  const WEATHER_ICON_VIEWBOX = 100;
  const WEATHER_ICON_INK_ORIGIN = 0;
  const WEATHER_ICON_INK_BOX = 100;
  const weatherIconImageDataCache = new Map();

  // The weather label's type scale, in one place. The elevation mark is
  // drawn to match the caption, so a change to either of these that the
  // mark did not see would put them back out of step.
  const WEATHER_TEXT_SIZE = 13;
  const WEATHER_TEMP_SCALE = 1.2;
  const WEATHER_ELEV_SCALE = 0.95;

  // The elevation mark — a mountain silhouette drawn before the metre
  // value on the label's second line.
  //
  // It exists because "1300 m" beside a weather symbol on a map reads as a
  // DISTANCE. Nothing else in the label disambiguates it, and the one
  // surface where the number is unlabelled is the one where the wrong
  // reading is most plausible.
  //
  // Canvas rather than an SVG asset: it is two triangles at roughly 9px,
  // it must exist before the layer's `format` references it (a missing
  // image drops the whole symbol, not just that section), and a canvas
  // draw is synchronous where an SVG decode is not.
  //
  // Not SDF, so it cannot take `icon-color` — the fill below is the same
  // literal the layer's `text-color` uses, and the two have to be changed
  // together.
  // Sized off the caption it sits beside rather than by eye, so the two
  // cannot drift apart when either is retuned. `WEATHER_ELEV_SCALE` gives
  // the caption's font size; ~0.72 of a font size is where a digit's cap
  // sits, and the mark should stand exactly that tall — shorter and it
  // reads as a bullet, taller and it reads as a second glyph.
  const DIGIT_CAP_RATIO = 0.72;
  const ELEVATION_MARK_SIZE = Math.round(
    WEATHER_TEXT_SIZE * WEATHER_ELEV_SCALE * DIGIT_CAP_RATIO,
  );

  const buildElevationMark = () => {
    const ratio = window.devicePixelRatio || 1;
    const size = ELEVATION_MARK_SIZE * ratio;
    // ONE try/catch around the whole draw, not a check on the context.
    // `getContext('2d')` does not fail in one predictable way: it returns
    // null in a browser that refuses one, a partial stub under jsdom (an
    // object with no `beginPath`, so a truthiness guard passes and the
    // first call still throws), and a working context whose `getImageData`
    // throws once the canvas is tainted. Catching the operation covers all
    // three; testing the object covers whichever one was in mind.
    //
    // It matters because this runs inside installWeatherLayer between
    // addSource and addLayer: an escaping throw leaves an orphaned source
    // and no weather layer at all.
    try {
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext('2d');
      // A peak and a lower shoulder — one triangle reads as a "play"
      // arrow at this size, two read as terrain.
      //
      // FLUSH TO THE BOTTOM OF THE BOX. MapLibre pins an inline image's
      // bottom edge to the text baseline, so any gap left under the ink
      // becomes the mark floating above the digits beside it. `base` is
      // 1px short of the edge only so the fill's antialiasing is not
      // clipped.
      const base = size - ratio;
      ctx.fillStyle = '#1a1916';
      ctx.beginPath();
      ctx.moveTo(size * 0.02, base);
      ctx.lineTo(size * 0.40, 0);
      ctx.lineTo(size * 0.68, base);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(size * 0.52, base);
      ctx.lineTo(size * 0.76, size * 0.38);
      ctx.lineTo(size * 0.98, base);
      ctx.closePath();
      ctx.fill();
      return { data: ctx.getImageData(0, 0, size, size), pixelRatio: ratio };
    } catch (_err) {
      return null;
    }
  };

  const ensureElevationMarkRegistered = () => {
    const id = window.pwaWeatherCore.ELEVATION_MARK_ID;
    if (map.hasImage(id)) return true;
    const mark = buildElevationMark();
    if (mark) {
      map.addImage(id, mark.data, { pixelRatio: mark.pixelRatio });
      return true;
    }
    // The mark could not be drawn. Register a 1x1 transparent image under
    // the same id anyway, because the label's `format` names it and
    // MapLibre drops the WHOLE symbol when a section's image is missing —
    // so the alternative to an invisible mark is no weather on the map at
    // all. The plain {width, height, data} form is deliberate: it is the
    // one addImage accepts without a canvas, which is exactly what is
    // unavailable when this branch is reached.
    map.addImage(id, { width: 1, height: 1, data: new Uint8Array(4) });
    return false;
  };
  // Decode one weather icon SVG into RGBA ImageData, memoised by filename.
  // Async by necessity (an Image decode has no synchronous equivalent) —
  // callers register the result with map.addImage themselves once it
  // resolves; see ensureWeatherIconsRegistered.
  const decodeWeatherIcon = (filename) => new Promise((resolve, reject) => {
    if (weatherIconImageDataCache.has(filename)) {
      resolve(weatherIconImageDataCache.get(filename));
      return;
    }
    const img = new Image();
    img.onload = () => {
      const pixelRatio = window.devicePixelRatio || 1;
      const canvas = document.createElement('canvas');
      canvas.width = WEATHER_ICON_RASTER_SIZE * pixelRatio;
      canvas.height = WEATHER_ICON_RASTER_SIZE * pixelRatio;
      const ctx = canvas.getContext('2d');
      // Crop the set's uniform transparent border before rasterising.
      //
      // A set that pads every drawing with the same border spends that
      // padding on nothing at symbol size — pushing the glyph away from the
      // temperature beside it, inflating the collision box it reserves
      // against its neighbours, and leaving the symbol small for the space
      // it took. The constants above say how much to take back, and the
      // Yr set makes them a no-op: its ink fills the viewBox, `inset` is
      // 0, and this draws the full box.
      //
      // The crop is the SAME for every icon on purpose. Cropping each one
      // to its own ink would scale `cloudy` up to fill the box a full sun
      // occupies, and the set encodes meaning in relative size — an
      // overcast day is not as loud as a clear one.
      const scale = canvas.width / WEATHER_ICON_INK_BOX;
      const inset = WEATHER_ICON_INK_ORIGIN * scale;
      const drawSymbol = () => {
        ctx.drawImage(
          img,
          -inset,
          -inset,
          WEATHER_ICON_VIEWBOX * scale,
          WEATHER_ICON_VIEWBOX * scale,
        );
      };
      // Outline the symbol before drawing it.
      //
      // The set draws cloud bodies in a pale grey with white highlights
      // (Yr's is `#dddddd`, 1.28:1 on white). That is legible on a dark
      // plate and invisible here:
      // the plate under it is --color-card at 92%, and the winter basemap
      // under THAT is near-white too, so a cloud's edge had nothing to
      // meet and the glyph dissolved into its own background.
      //
      // A zero-offset drop-shadow is a halo around the alpha channel, so
      // repeating it dilates a dark edge that follows the symbol's real
      // silhouette rather than boxing it. The unfiltered pass on top then
      // restores the interior, leaving the artist's colours untouched and
      // only the perimeter darkened.
      //
      // `ctx.filter` is unsupported in a few engines, where assignment is
      // a silent no-op — the icon then renders exactly as it did before
      // this block, which is the correct degradation.
      let imageData;
      // The try covers the DRAWING as well as the read-back. A 2D context
      // fails in more than one way — null in a browser that refuses one, a
      // partial stub with no `beginPath` under jsdom, a working context
      // whose `getImageData` throws on a tainted canvas — and the caller
      // handles a rejected decode by leaving that icon unregistered, which
      // is the right outcome for all of them.
      try {
        // SNOW-791: the edge is a blur, so a set that paints its own
        // silhouette is only softened by it — at this size that turns a
        // six-armed flake into a disc. Painted only for the sets that need
        // it, which the server names on the map element.
        if (WEATHER_ICON_HALO) {
          ctx.filter =
            'drop-shadow(0 0 ' + pixelRatio + 'px rgba(41, 45, 54, 0.85))';
          drawSymbol();
          drawSymbol();
          ctx.filter = 'none';
        }
        drawSymbol();
        imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      } catch (err) {
        reject(err);
        return;
      }
      const entry = { data: imageData, pixelRatio };
      weatherIconImageDataCache.set(filename, entry);
      resolve(entry);
    };
    img.onerror = () => reject(new Error('weather icon decode failed: ' + filename));
    img.src = WEATHER_ICON_BASE_URL + filename;
  });

  // Decode + register every filename not already a registered MapLibre
  // image. Never rejects — one icon's decode failure (a malformed SVG, a
  // network hiccup) is logged and skipped rather than blocking every other
  // icon or the caller's `.then()`.
  const ensureWeatherIconsRegistered = (filenames) => Promise.all(
    filenames
      .filter((filename) => filename && !map.hasImage(filename))
      .map((filename) => decodeWeatherIcon(filename)
        .then(({ data, pixelRatio }) => {
          if (!map.hasImage(filename)) map.addImage(filename, data, { pixelRatio });
        })
        .catch((err) => {
          console.warn('[map] SNOW-761: weather icon decode failed', filename, err);
        })),
  );

  // Below this zoom the symbols are not drawn at all: a condition icon per
  // station across a whole country is a texture, not information.
  const WEATHER_MIN_ZOOM = 7;

  // Cluster half-width in degrees at WEATHER_MIN_ZOOM, halved for each zoom
  // level above it — the same doubling the viewport itself does, so the
  // radius is a roughly constant number of SCREEN pixels at every zoom.
  // Above WEATHER_DECLUTTER_MAX_ZOOM the stations no longer collide, so
  // collapsing is switched off entirely (a zero radius) and every station
  // draws.
  const WEATHER_CLUSTER_BASE_DEGREES = 0.55;
  const WEATHER_DECLUTTER_MAX_ZOOM = 11;

  // The cluster radius for the current camera. Read at projection time
  // rather than stored, so it can never disagree with the zoom on screen.
  const weatherClusterRadius = () => {
    const zoom = map.getZoom();
    if (zoom >= WEATHER_DECLUTTER_MAX_ZOOM) return 0;
    return WEATHER_CLUSTER_BASE_DEGREES / Math.pow(2, zoom - WEATHER_MIN_ZOOM);
  };

  // Collapse to the lowest station of each cluster, then project the day's
  // entry onto flat icon/label properties. Collapse FIRST: projecting every
  // station and discarding most of the work would be the same picture at
  // more cost, and — more importantly — the collapse must be decided on
  // elevation, which the projection does not carry.
  //
  // A null `currentDisplayedDate` — no day asked for — projects every
  // feature to `icon: ''`, which the layer's own filter drops, so the map
  // carries no weather symbols for a day the visitor never chose.
  const weatherDataForCurrentView = () => {
    const collapsed = window.pwaWeatherCore.collapseToLowest(
      weatherGeojsonCache, weatherClusterRadius(),
    );
    return window.pwaWeatherCore.projectFeatureCollectionForDate(
      collapsed, currentDisplayedDate,
    );
  };

  // Which call to ``refreshWeatherSourceData`` is the current one. The
  // recompute is async — it waits on an icon decode before it writes — so
  // two calls started by a rapid pan can settle in either order, and the
  // slower FIRST one would then paint the previous viewport's collapse over
  // the newer one. Every call takes the next number and only writes if it
  // is still holding it.
  let weatherRefreshToken = 0;

  // Recompute what is drawn from whatever is cached — no fetch.
  //
  // Three reasons to do nothing, all of them cheap and all of them checked
  // before the collapse, which walks every feature in the payload:
  //
  //   - the layer has not been installed yet;
  //   - the overlay is switched off, so both its layers are
  //     ``visibility: none`` and the result would be projected for nobody.
  //     A date changed or a pan made while it is off is picked up by the
  //     re-enable (see the ``snowdesk:overlay-load`` handler), which is the
  //     same stale-day fix the bulletin boundary takes there. Read from
  //     STORAGE rather than ``overlayState``, for the reason that handler
  //     gives: the picker writes the key on every click, while
  //     ``overlayState`` is only re-seeded from it at boot and after a
  //     basemap swap — and a toggle-OFF takes the picker's direct
  //     visibility path, which never reaches this module's copy at all;
  //   - the camera is below ``WEATHER_MIN_ZOOM``, which both layers carry as
  //     their own ``minzoom``, so nothing is drawn at this zoom either way.
  //     ``moveend`` fires on the way back up, which is what repaints it.
  const refreshWeatherSourceData = () => {
    if (!map.getSource('weather')) return;
    if (!readBoolStorage(OVERLAY_STORAGE_KEY.weather, overlayState.weather)) return;
    if (map.getZoom() < WEATHER_MIN_ZOOM) return;
    const token = ++weatherRefreshToken;
    const projected = weatherDataForCurrentView();
    ensureWeatherIconsRegistered(
      window.pwaWeatherCore.iconFilenamesForPayload(weatherGeojsonCache),
    ).then(() => {
      // A newer call has started since; its projection is the one that
      // matches what is on screen, so this one's is discarded.
      if (token !== weatherRefreshToken) return;
      const src = map.getSource('weather');
      // The style may have swapped mid-decode, taking the source with it.
      if (src) src.setData(projected);
    });
  };

  // Install the Weather overlay. Idempotent, like installFavouritesLayer /
  // installCommunityReportsLayer — early-returns if the source already
  // exists, so the styledata re-install handler can call it safely on every
  // basemap swap.
  const installWeatherLayer = (geojson) => {
    if (!geojson || map.getSource('weather')) return;
    weatherGeojsonCache = geojson;
    const projected = weatherDataForCurrentView();
    map.addSource('weather', { type: 'geojson', data: projected });
    // Before the layer: an unregistered image in a `format` expression
    // drops the entire symbol, not just its own section. This one is a
    // canvas draw with no decode, so unlike the condition glyphs there is
    // no window in which it is absent.
    ensureElevationMarkRegistered();

    map.addLayer({
      id: 'weather-point',
      type: 'symbol',
      source: 'weather',
      minzoom: WEATHER_MIN_ZOOM,
      // A feature with no entry for the current date projects to `icon: ''`
      // — filtered out entirely rather than drawing an empty symbol, so it
      // also never reserves a collision box.
      filter: ['!=', ['get', 'icon'], ''],
      layout: {
        visibility: overlayState.weather ? 'visible' : 'none',
        // TWO ROWS, and every part of both is inside `text-field`:
        //
        //     [WMO]  19°
        //     [mark] 1300 m
        //
        // The condition symbol was briefly an `icon-image` anchored beside
        // the label, to centre it on the temperature. That is not a row —
        // `icon-anchor` centres the symbol on the whole TEXT BLOCK, so
        // with a second line it lands between the two and the three parts
        // read as a triangle. An icon outside the text cannot sit on a
        // text row; only an inline `image` section can.
        //
        // The cost is that MapLibre shapes an inline image at
        // `ONE_EM - height * scale`, which pins its BOTTOM to the row's
        // baseline rather than centring it on the text. The symbol
        // therefore rides a little high against the digits, and that is
        // not tunable: padding under it in the raster only lifts it
        // further, padding above moves nothing, and this build has no
        // per-section `vertical-align` (the `verticalAlign` inside
        // maplibre-gl.min.js belongs to `text-anchor`/`icon-anchor`, not
        // to `format`). Sharing a baseline is what "same row" can mean
        // here.
        //
        // The station's ground elevation is `Location.elevation_m` — where
        // the reading is taken — NOT the freezing level, which is
        // `Weather.freezing_level_height` and has never been on the map.
        // Its mark is there because "1300 m" beside a weather symbol on a
        // map otherwise reads as a distance.
        //
        // `label_break` and `elev_mark` are separate feature properties
        // rather than literals because a `format` is fixed at style time:
        // a station with no resolved elevation contributes three empty
        // strings, not three omitted sections. Put the break inside the
        // value instead and that station renders a blank second row,
        // which shifts the whole label off the point it is labelling.
        // See formatElevationBreak in map_weather_core.js.
        'text-field': [
          'format',
          ['image', ['get', 'icon']], {},
          // The gap. Its own section because the raster's transparent
          // margin is cropped on purpose (see decodeWeatherIcon) and
          // re-adding some of it would undo that crop on one side only.
          ' ', {'font-scale': WEATHER_TEMP_SCALE},
          ['get', 'label_temp'], {'font-scale': WEATHER_TEMP_SCALE},
          ['get', 'label_break'], {},
          ['image', ['get', 'elev_mark']], {},
          // The mark's own gap. An inline image's advance is exactly its
          // width, so without this the digits start against its edge.
          // Full size rather than the caption's, because the space is
          // separating a glyph from a number and wants to read at least
          // as wide as the one on row 1.
          ' ', {},
          ['get', 'label_elev'], {'font-scale': WEATHER_ELEV_SCALE},
        ],
        'text-font': overlayTextFont,
        'text-size': WEATHER_TEXT_SIZE,
        'text-anchor': 'center',
        'text-allow-overlap': false,
        'text-padding': 4,
        // Ems of text-size. It looks tight for two rows because it is
        // not the whole story: MapLibre
        // advances a row by `lineHeight * maxScale + imageOverhang`, where
        // the overhang is how far a tall inline image sticks out of its
        // line box (`r[1] * scale - ONE_EM * s` in the shaper). The 27px
        // symbol contributes 21 em-units of that on its own, so the rows
        // are already held apart whatever this says — an earlier 1.66,
        // picked on the belief that this property was what kept the
        // symbol off row 2, was paying for the same clearance twice and
        // left a visible hole under the temperature.
        'text-line-height': 1.15,
      },
      paint: {
        // --color-text-1. The caption is the overlay's own content rather
        // than an annotation beside a pin, so it does not take
        // resorts-label's muted #5a5a5a: that is the right treatment for a
        // name sitting next to a solid pin and the wrong one for the only
        // figure the layer carries. On the pale winter basemap the muted
        // grey was close to unreadable.
        'text-color': '#1a1916',
        'text-halo-color': 'rgba(255,255,255,0.95)',
        'text-halo-width': 2.2,
      },
    });
    raiseMarkerLayers();
    // The icons referenced by `projected` are almost certainly not
    // registered yet on a first install. Until this resolves the layer
    // exists with no icons registered; MapLibre logs a warning and omits
    // the icon rather than throwing, so it is a brief "no icons yet" flash
    // rather than a broken layer.
    ensureWeatherIconsRegistered(
      window.pwaWeatherCore.iconFilenamesForPayload(weatherGeojsonCache),
    ).then(() => {
      const src = map.getSource('weather');
      if (src) src.setData(projected);
    });
    announceOverlayVisibility();
  };

  // ==== SNOW-691: the slope-angle overlay ====
  //
  // A third-party raster (swisstopo's ch.swisstopo.hangneigung-ueber_30),
  // banded to the SLF classification. Everything pure about it — the
  // coverage rectangle, the zoom limits, the opacity constant, the class
  // table — lives in static/js/slope_overlay_core.js so it can be tested;
  // this is the MapLibre half.
  //
  // Installed EAGERLY and hidden, unlike every other opt-in overlay, which
  // lazy-loads on first toggle-on. There is nothing to lazy-load: MapLibre
  // requests no tiles for a source whose layers are all `visibility: none`,
  // so a hidden raster costs one source entry and no network at all. That
  // is also why `slope` is absent from the picker's lazy-load branch and
  // from ensureOverlayLoaded — the generic setLayoutProperty path is the
  // whole toggle.
  const SLOPE_CORE = self.pwaSlopeOverlayCore;

  // The Terrain row's own namespaced disabled marker — mirroring
  // pwa_offline.js's `data-was-disabled-offline` /
  // map_layer_sync_status.js's `data-sync-disabled-offline` idiom, so this
  // module only ever re-enables a row IT disabled.
  //
  // Nothing else disables this one today: the slope
  // tiles are a third-party raster with no entry in
  // map_layer_sync_status.js's OVERLAY_RESOURCES, so that module's
  // offline gate never reaches this row and its marker is the only one in
  // play. The `data-sync-disabled-offline` check below is therefore
  // defensive rather than load-bearing — kept because it costs one
  // comparison and is exactly what would have to be here the day a probe
  // for the slope tile cache is added (SNOW-692's territory).
  const SLOPE_ROW_DISABLED_MARKER = 'data-slope-disabled-out-of-coverage';

  /**
   * Apply or clear the Terrain row's coverage disable.
   *
   * @param {boolean} disabled Whether the row should be inert.
   * @param {string} reason The title text explaining why.
   */
  const _setSlopeRowDisabled = (disabled, reason) => {
    const row = document.querySelector('#basemap-menu [data-overlay-key="slope"]');
    if (!row) return;
    if (disabled) {
      row.setAttribute('aria-disabled', 'true');
      row.setAttribute(SLOPE_ROW_DISABLED_MARKER, '1');
      row.title = reason;
    } else if (row.getAttribute(SLOPE_ROW_DISABLED_MARKER) === '1') {
      row.removeAttribute(SLOPE_ROW_DISABLED_MARKER);
      row.removeAttribute('title');
      // Never re-enable a row the offline gate is still holding down —
      // whichever reason clears second is the one that re-enables it.
      if (row.getAttribute('data-sync-disabled-offline') !== '1') {
        row.removeAttribute('aria-disabled');
      }
    }
  };

  /**
   * Re-derive the Terrain row's disabled state from where the map is
   * currently looking.
   *
   * The viewport CENTRE, not its bounds: a partial overlap would otherwise
   * leave the row enabled while most of the screen has no data, and the
   * centre is the thing a visitor is actually looking at. Panning back
   * inside re-enables it.
   *
   * The row is only in the DOM for an eligible request, so there is exactly
   * one reason it can be unusable and one string to say it.
   */
  const updateSlopeRowAvailability = () => {
    if (!SLOPE_ELIGIBLE || !SLOPE_CORE) return;
    const centre = map.getCenter();
    const covered = SLOPE_CORE.coversPoint(centre.lng, centre.lat);
    _setSlopeRowDisabled(!covered, MAP_STRINGS['slope-out-of-coverage']);
  };

  /**
   * Install the slope raster and its coverage outline.
   *
   * Idempotent — early-returns when the source is already present, so the
   * styledata re-install handler can call it on every basemap swap.
   */
  const installSlopeLayer = () => {
    if (!SLOPE_ELIGIBLE || !SLOPE_TILE_URL || !SLOPE_CORE) return;
    if (map.getSource('slope')) return;

    const visibility = overlayState.slope ? 'visible' : 'none';

    map.addSource('slope', {
      type: 'raster',
      tiles: [SLOPE_TILE_URL],
      tileSize: 256,
      // Both declared explicitly, and maxzoom is 16 rather than the
      // service's advertised 17 — see MAX_ZOOM in slope_overlay_core.js for
      // the measurement behind that. Declaring it at all is SNOW-604's
      // lesson: a raster source's maxzoom defaults to 22, and this origin
      // answers HTTP 400 past z17.
      minzoom: SLOPE_CORE.MIN_ZOOM,
      maxzoom: SLOPE_CORE.MAX_ZOOM,
      // The declared coverage rectangle. This is what keeps MapLibre from
      // ever asking for a tile outside it — the service answers those with
      // HTTP 400 and a JSON body rather than an empty tile, so an unbounded
      // source would spend real requests on errors along every edge.
      bounds: SLOPE_CORE.COVERAGE_BOUNDS,
      // swisstopo's terms make the free geoservices usable commercially and
      // oblige us to name the source. `updateMapAttribution` unions this
      // into the legend's "Map data" section; /colophon/ carries the longer
      // form. Trusted, server-controlled HTML, like every other source's.
      //
      // It names the DATASET, not just the publisher, and that is
      // load-bearing rather than pedantic: two of the basemaps are also
      // swisstopo and contribute a bare "© swisstopo", so a matching string
      // here rendered as "© swisstopo · swisstopo" — a duplicate that
      // credits nothing. The union is by exact string, so the way to say
      // "this is a second, different swisstopo dataset" is to say which.
      attribution:
        '<a href="https://www.swisstopo.admin.ch/" target="_blank" rel="noopener">'
        + 'swisstopo — slope classes</a>',
    });

    // Under the choropleth, so the danger ratings stay readable on top of
    // it and the SNOW-656 fill-strength control does the "am I reading
    // danger or terrain" trade-off it was built for — no new exclusivity
    // rule, and no second opacity control. `beforeId` rather than relying
    // on insertion order because the two install paths run in opposite
    // orders: at boot this runs before installRegionsLayers, and on a
    // basemap swap the styledata handler reinstalls the regions first.
    const beforeId = map.getLayer('regions-fill') ? 'regions-fill' : undefined;
    map.addLayer(
      {
        id: 'slope-raster',
        type: 'raster',
        source: 'slope',
        layout: { visibility: visibility },
        paint: {
          'raster-opacity': SLOPE_CORE.RASTER_OPACITY,
          // NEAREST, not MapLibre's default linear. This raster is
          // CATEGORICAL — five discrete class colours — so interpolating
          // between neighbouring pixels invents colours that correspond to
          // no slope class at all, softening every class boundary into a
          // gradient the data does not contain. On a layer a reader might
          // use to judge whether a slope is under or over 40°, a made-up
          // colour between two classes is worse than a visible pixel edge.
          //
          // The blockiness that leaves at high zoom is the source's own 10 m
          // grid, not a rendering artefact: measured against the served
          // tiles, the smallest distinguishable feature is ~10 m at every
          // zoom (a 2px run at z14, 3px at z15, 6px at z16 — each doubling
          // with the zoom rather than resolving further).
          'raster-resampling': 'nearest',
        },
      },
      beforeId,
    );

    raiseMarkerLayers();

    // `updateMapAttribution` caches the style's source ids the first time it
    // runs (at style.load) and only clears that cache on the next style
    // load. This source is added AFTER that, so without dropping the cache
    // its attribution is never read and the swisstopo credit silently never
    // reaches the legend — a licence obligation lost to a memoisation.
    attributionSourceIds = null;
    updateMapAttribution();

    updateSlopeRowAvailability();
  };

  // Panning changes whether the map is looking at ground the raster covers,
  // so the row's reason is re-derived on every settled move. `moveend`
  // rather than `move`: this only touches the menu, which nobody can read
  // mid-gesture, and one DOM write per gesture is the right budget.
  map.on('moveend', updateSlopeRowAvailability);

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
  // Visibility is seeded from the Bulletins row's EFFECTIVE state — the
  // boundary is a companion to the choropleth and has no state of its own
  // (see OVERLAY_VISIBILITY_GOVERNOR, which SNOW-656 moved from ``l4`` to
  // ``bulletins``). Unlike ``regions-fill`` this one really does use
  // ``visibility``: it is a line layer, hit-tests nothing, and so has no
  // reason to stay installed-but-invisible.
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
          visibility: BULLETINS_CORE.isEffective(bulletinsVisibility) ? 'visible' : 'none',
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
          // on: only draw if this is still the displayed date. SNOW-660: with
          // no day chosen `currentDisplayedDate` is null, which matches no
          // `dateKey` a fetch was ever scheduled for — the boundary stays
          // blank, which is the same answer the choropleth gives.
          if (currentDisplayedDate === dateKey) drawGroupings(fc);
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
  // SNOW-687: same job for the saved-routes lines — the basemap-swap
  // re-install reads this rather than re-fetching an endpoint whose answer
  // has not changed (and which is unreachable offline).
  let routesGeojsonCache = null;

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
  // (which must already be present in regions-fill), so skipping the country
  // filter here is safe — a user cannot click a fill feature the country
  // FILTER has excluded, which is what matters here. SNOW-656 made the fill
  // hideable by opacity rather than visibility, and a transparent feature IS
  // still clickable — deliberately, so borders stay tappable with the colour
  // off — but that is orthogonal: filtering removes the feature outright.
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

      // SNOW-812: which of the three per-country feeds actually answered.
      // Each of the two optional ones swallows its own failure with
      // `.catch(() => null)`, so a partially-loaded country renders as a
      // map with missing boundaries and no error anywhere.
      window.pwaDebugLog?.record('net', 'country.load', {
        country: code,
        regions: newRegions ? (newRegions.features || []).length : null,
        major: newMajor ? (newMajor.features || []).length : null,
        sub: newSub ? (newSub.features || []).length : null,
      });

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
          // Which date is currently being displayed — the committed date
          // first, ``?d=`` behind it, exactly as a basemap swap resolves it.
          //
          // SNOW-660: this used to read ``readUrlDateParam()`` alone, on the
          // reasoning that ``commitDate`` now writes ``?d=`` for every
          // chosen day. It does — but it is not the only thing that commits
          // one. The timelapse paints a frame per tick and only syncs the
          // URL where playback settles, so a country toggled on mid-playback
          // would find a bare URL, skip its paint, and leave the new
          // country's regions grey beside correctly-graded neighbours. The
          // precedence helper is the codebase's existing answer to exactly
          // this question, and using it keeps the two callers from drifting.
          //
          // ``currentDisplayedDate`` is declared at this IIFE's top level,
          // below this function but above every call site (a country toggle
          // click and the map's 'load' handler), so it is initialised by the
          // time this runs.
          //
          // Null still means nothing has been chosen: the paint is skipped
          // rather than invented — and only the paint, so the sync-dot
          // bookkeeping below still runs.
          const paintDate = self.pwaChoroplethCore.repaintDateForStyleSwap(
            currentDisplayedDate, readDisplayDate(),
          );
          if (MAP && paintDate) {
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
        // SNOW-658: the ROW's key, not the code's — AT and IT share the ALBINA
        // row, whose dot may only green once both have landed. markCached is
        // optimistic for a single-country row and would be a lie for this one,
        // so a grouped row hands off to a real probe instead.
        const rowKey = overlayKeyForCountry(code);
        if (countryCodesFor(rowKey).length === 1) {
          window.pwaLayerSyncStatus?.markCached(rowKey);
        } else {
          window.pwaLayerSyncStatus?.refresh();
        }
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
      // SNOW-658: the revert is GROUP-ATOMIC — every code the failing code's
      // row switches, not just the one that threw.
      //
      // A row is one bulletin provider, and ALBINA's covers two countries, so
      // one tap fires two of these loads. Reverting only the failing code left
      // the row unchecked while the country that HAD loaded stayed on the map,
      // and persisted, so it survived a reload: boot's ``every()`` seed
      // computes the row as off while still seeding countryState.at = true and
      // drawing Austria. The user could not clear it either — re-toggling
      // short-circuits on ``loadedCountries.has('at')``, so the drift only
      // resolved if a later retry of ``it`` happened to succeed. Reverting the
      // whole group means the row reads off and shows nothing, which is the
      // only pair of states a reader can act on.
      //
      // ``loadedCountries`` is deliberately untouched: the country that loaded
      // really is cached, and only the ENABLED state is being reverted.
      const rowKey = overlayKeyForCountry(code);
      const groupCodes = countryCodesFor(rowKey);
      for (const member of groupCodes) {
        countryState[member] = false;
        COUNTRY_STATE[member] = false;
        writeStorage(COUNTRY_STORAGE_KEY(member), 'false');
      }
      const row = document.querySelector(
        `#basemap-menu [data-overlay-key="${rowKey}"]`,
      );
      // Derived, not hardcoded to 'false': the row is checked only when every
      // code it switches is on, which is the same rule the boot seed applies.
      // Identical to 'false' today — the loop above just cleared them all —
      // but it cannot drift if the group grows a third member or the revert
      // semantics change.
      if (row) {
        row.setAttribute(
          'aria-checked',
          groupCodes.every((member) => countryState[member]) ? 'true' : 'false',
        );
      }
      // Now removes every member of the group from the map, including one that
      // loaded successfully moments ago.
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

  // The ``?route_share=`` deep link's one-shot ``sourcedata`` listener, held
  // out here so the failure branch of the routes load below can reach it.
  //
  // It is bound inside ``openRouteShareDeepLink`` and unbinds itself on the
  // first routes load it sees — but a routes load that FAILS never installs
  // the layer, so no ``sourcedata`` ever fires and the listener stays bound
  // for the rest of the session, running on every subsequent source event
  // the map makes. Null whenever nothing is waiting.
  let routeShareSourceDataListener = null;

  /**
   * Unbind the route-share deep link's pending ``sourcedata`` listener.
   *
   * Idempotent, and safe to call when nothing is waiting — which is the
   * common case, since a deep link is a small minority of arrivals.
   *
   * @returns {void}
   */
  const dropRouteShareSourceDataListener = () => {
    if (!routeShareSourceDataListener) return;
    map.off('sourcedata', routeShareSourceDataListener);
    routeShareSourceDataListener = null;
  };

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
      // SNOW-660: the boundary is per-day, so with no day asked for there is
      // nothing to fetch. Install the source EMPTY rather than not at all,
      // and fall THROUGH to the `overlayLoaded[key] = true` below rather
      // than returning: `drawGroupings` silently no-ops without a source,
      // and the date-changed handler that would fill it in is gated on the
      // tier being loaded — so either shortcut would leave the boundary
      // blank for the rest of the session, including after a day is chosen.
      const dateKey = currentDisplayedDate;
      if (!dateKey) {
        installBulletinGroupingsLayer(null);
      } else {
        const fc = await fetchBulletinGroupingsForDate(dateKey).catch(() => null);
        if (!fc) {
          // Deliberately silent, unlike every other tier here. Those load in
          // response to the user clicking their toggle, so a failure owes
          // them an explanation. This one loads automatically alongside L4 —
          // and its endpoint is network-only (per-date data, excluded from
          // sw.js's STATIC_PATHS), so it fails on every offline boot.
          // Toasting that would fire an "unavailable offline" message at a
          // user who asked for nothing and whose choropleth is working fine.
          // Returns (unlike the no-date case above) so the tier stays
          // unloaded and a later enable can retry the fetch.
          return;
        }
        installBulletinGroupingsLayer(fc);
        currentGroupingsFC = fc;
        groupingsDrawn = true;
      }
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
    } else if (key === 'weather') {
      // SNOW-761: no eligibility gate at all, like community_reports — the
      // feed is public. Guard on the URL alone in case this is ever reached
      // some other way (e.g. the boot-time restore below).
      if (!WEATHER_URL) return;
      const data = await fetch(WEATHER_URL).then(r => r.json()).catch(() => null);
      if (data) {
        // SNOW-492: write-through. The payload carries the whole forecast
        // window, so a cached copy stays useful for every date in it rather
        // than for the day it was fetched on.
        window.pwaMapOverlayCache?.putOverlay('weather', data);
        installWeatherLayer(data);
      } else {
        const cached = await window.pwaMapOverlayCache?.getOverlay('weather');
        if (!cached) {
          revealOfflineToast('map-offline-toast-weather');
          return;
        }
        installWeatherLayer(cached);
      }
    } else if (key === 'routes') {
      // SNOW-687: eligible-gated like favourites (flag + authenticated) —
      // the switch only exists in the DOM for an eligible user, but guard
      // the fetch too in case this is ever reached some other way.
      if (!ROUTES_ELIGIBLE || !ROUTES_URL) return;
      const data = await fetch(ROUTES_URL).then(r => r.json()).catch(() => null);
      if (data) {
        // Write-through, like favourites: a route never expires (it is the
        // user's own stored geometry), so the cached copy is installed
        // as-is on a later offline read-back.
        window.pwaMapOverlayCache?.putOverlay('routes', data);
        installRoutesLayer(data);
      } else {
        const cached = await window.pwaMapOverlayCache?.getOverlay('routes');
        if (!cached) {
          revealOfflineToast('map-offline-toast-routes');
          // Nothing will be installed, so the deep link's one-shot listener
          // is waiting for an event that can no longer happen. Release it
          // here rather than leaving it to run on every source event for the
          // rest of the session.
          dropRouteShareSourceDataListener();
          return;
        }
        installRoutesLayer(cached);
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
  //
  // SNOW-656: there is no ``bulletins`` entry here and there is no
  // ``regions-fill`` entry anywhere in this map. The Bulletins row's two
  // layers are driven differently from every other overlay — the fill by
  // opacity (it must stay hit-testable), the groupings boundary through the
  // ``l3`` entry below via OVERLAY_VISIBILITY_GOVERNOR — so both go through
  // applyBulletinsVisibility rather than the generic visibility loop this
  // table feeds.
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
    // SNOW-761: one symbol layer, not a pin+label pair — the condition
    // glyph is an inline `image` section inside `text-field`, so there is
    // a single layer to toggle rather than the pin+label pairs favourites
    // and resorts use.
    weather: ['weather-point'],
    // SNOW-687: the coloured line FIRST and the casing second — deliberately
    // the inverse of the order installRoutesLayer adds them in, where the
    // casing has to be added first to paint underneath. This list's order is
    // read by panelOverlayPainted below, which answers for the whole group
    // from element [0], and that has to be the layer the user actually sees.
    // SNOW-764 adds 'routes-line-pending'. It is NOT first: panelOverlayPainted
    // answers for the whole group from element [0], and that has to be the
    // layer every routes user has — a visitor with only a pending share is
    // the exception, not the case the roundel ring is painted from.
    routes: [
      'routes-line', 'routes-line-casing', 'routes-line-pending', 'routes-endpoints',
    ],
    // SNOW-691: one layer — the raster. The coverage outline that rode
    // alongside it was removed; see slope_overlay_core.js's header.
    slope: ['slope-raster'],
  };

  /**
   * Whether a panel-driven overlay is actually drawn on the map right now.
   *
   * SNOW-658: the FIRST id in each group above is that overlay's principal
   * layer — the one carrying its markers ('favourites-pin',
   * 'community-reports-clusters'). Every id in a group is installed by one
   * function and flipped by one loop, so the principal layer answers for the
   * group; reading it off the table rather than naming a second literal
   * keeps the group definition the only place layer ids are written down.
   *
   * @param {string} key - ``'favourites'`` or ``'community_reports'``.
   * @returns {boolean} True when that overlay's layers are on the map.
   */
  const panelOverlayPainted = (key) => layerPainted(OVERLAY_LAYER_IDS_MAIN[key][0]);

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
      // SNOW-656: ``bulletins`` stores a STEP (0 / 0.25 / … / 1), not a
      // boolean, so it cannot go through readBoolStorage — that returns
      // ``v === 'true'``, which is false for every step including "1", and
      // the re-seed below would then zero the very preference that triggered
      // this load. (Choosing a step dispatches the l3 lazy-load, so the bug
      // fired on every step the user picked, a tick later, with nothing on
      // screen to explain it.) Its own read is in seedFromLegacy's shape.
      let stillEnabled;
      if (gov === 'bulletins') {
        const step = BULLETINS_CORE.seedFromLegacy(
          readStorage(OVERLAY_STORAGE_KEY.bulletins),
          readStorage(OVERLAY_STORAGE_KEY.l4),
          overlayState.bulletins,
        );
        overlayState.bulletins = step;
        // A re-read of persisted state, not a click, so it takes
        // ``setPreference`` (mechanical) rather than ``choose`` — which would
        // clear the downloads suppression on the user's behalf and revive the
        // choropleth under the download squares.
        bulletinsVisibility = BULLETINS_CORE.setPreference(bulletinsVisibility, step);
        // What gets painted is the step AND-ed with any active suppression: a
        // boundary made visible here while the downloads overlay is on would
        // be exactly the fight this ticket removed.
        stillEnabled = BULLETINS_CORE.isEffective(bulletinsVisibility);
      } else {
        stillEnabled = readBoolStorage(OVERLAY_STORAGE_KEY[gov], overlayState[gov]);
        overlayState[gov] = stillEnabled;
      }
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
      // SNOW-660: with no day asked for this passes null, and
      // scheduleGroupingsForDate's own falsy guard blanks the boundary and
      // schedules nothing — a re-enable reveals no day's outline until one
      // is chosen.
      if (key === 'l3' && stillEnabled && wasLoaded) {
        scheduleGroupingsForDate(currentDisplayedDate);
      }
      // SNOW-761: the weather symbols have exactly the l3 problem above, for
      // exactly the l3 reason. ``refreshWeatherSourceData`` does nothing
      // while the overlay is off, so a day scrubbed to — or a pan made —
      // while it was hidden never reached the source, and a re-enable would
      // reveal an earlier day's icons over the current choropleth. Only for
      // a re-enable: a FIRST load installs the layer with the current day
      // already projected, and the guard inside would refuse this call
      // anyway until the fetch had installed the source.
      if (key === 'weather' && stillEnabled && wasLoaded) {
        refreshWeatherSourceData();
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
      // SNOW-658 review: this is where a lazy overlay's paint actually
      // changes — the caller that dispatched this event announced a tick
      // ago, before the fetch above had installed anything, so without this
      // the roundel ring would stay off for an overlay now on the map (or,
      // on a re-enable of an already-loaded layer, off for a layer this
      // handler's own loop has just made visible). A settled fetch that
      // installed nothing announces too: it says "still not drawn", which
      // is the state this whole distinction exists to keep honest.
      announceOverlayVisibility();
    }).catch(() => {
      announceOverlayVisibility();
    });
  });

  // ==== SNOW-656: the Bulletins row ====
  //
  // Everything that can change what the choropleth and the bulletin boundary
  // are doing funnels through ``applyBulletinsVisibility``: the step control,
  // resort-edit mode, and every re-install after a basemap swap. Three
  // independent writers of one layer's visibility is how this drifts —
  // before SNOW-656 there were already two (the picker's toggle loop and
  // map_edit_resorts.js's direct hide).

  /**
   * Paint the two Bulletins layers from ``bulletinsVisibility``, and mirror
   * the result onto the layers-menu row.
   *
   * ``regions-fill`` is hidden by OPACITY rather than visibility — it is the
   * map's hit-test target, and a layer at ``visibility: none`` returns
   * nothing from ``queryRenderedFeatures``, so hiding it that way would
   * leave visible borders that cannot be tapped. See
   * ``regionsFillLayout``'s own docstring for the four-quadrant table.
   *
   * The row shows the EFFECTIVE value, not the stored preference, so a user
   * whose choropleth has been taken off the map by a mode they are in
   * (resort-edit) can see why rather than finding it mysteriously missing.
   *
   * @returns {void}
   */
  const applyBulletinsVisibility = () => {
    const effective = BULLETINS_CORE.isEffective(bulletinsVisibility);
    const fill = BULLETINS_CORE.regionsFillLayout(overlayState.l4, bulletinsVisibility);
    if (map.getLayer('regions-fill')) {
      map.setLayoutProperty('regions-fill', 'visibility', fill.visibility);
      map.setPaintProperty('regions-fill', 'fill-opacity', regionFillOpacity(fill.opacity));
    }
    if (map.getLayer('bulletin-groupings-line')) {
      map.setLayoutProperty(
        'bulletin-groupings-line', 'visibility', effective ? 'visible' : 'none',
      );
    }
    // Mirror the EFFECTIVE step onto the control's five segments by direct
    // DOM write — the control
    // is wired in a sibling IIFE and IS its own state. Effective, not
    // preferred: while something is suppressing the choropleth the control
    // must read 0, so the user can see why the colour went.
    //
    // Document-wide, not scoped to the layers menu — SNOW-656 moved the
    // control onto the canvas beside the scrubbed date.
    const shown = fill.opacity;
    for (const seg of document.querySelectorAll('[data-bulletins-step]')) {
      const step = Number(seg.dataset.bulletinsStep);
      seg.setAttribute('aria-checked', step === shown ? 'true' : 'false');
    }
  };

  /**
   * Add or remove a suppression on the Bulletins layers and repaint.
   *
   * Exposed to the sibling IIFEs that need it — ``map_edit_resorts.js``
   * suppresses for the length of resort-edit mode — so they never reach for
   * ``setLayoutProperty('regions-fill', …)`` themselves. The downloads
   * overlay used to be the other caller; it no longer suppresses anything.
   *
   * @param {string} reason A ``pwaLayerVisibilityCore.SUPPRESSION`` value.
   * @param {boolean} active Whether the reason is now in force.
   * @returns {void}
   */
  const setBulletinsSuppressed = (reason, active) => {
    bulletinsVisibility = active
      ? BULLETINS_CORE.suppress(bulletinsVisibility, reason)
      : BULLETINS_CORE.unsuppress(bulletinsVisibility, reason);
    applyBulletinsVisibility();
  };

  // The named channel for modules outside this IIFE — currently
  // map_edit_resorts.js, which suppresses for the length of resort-edit mode.
  // A window bridge rather than a bare identifier for the reason map_state.js
  // spells out: a top-level ``const`` in a classic script is NOT a window
  // property, so a consumer reaching for ``window.setBulletinsSuppressed``
  // would silently get ``undefined`` (finding M1). Frozen, and a function
  // rather than a property, because the state changes after this is built.
  window.pwaBulletinsLayer = Object.freeze({
    setSuppressed: setBulletinsSuppressed,
    isVisible: () => BULLETINS_CORE.isEffective(bulletinsVisibility),
  });

  // The Micro regions row owns ``regions-line``/``regions-label`` — the
  // picker flips those itself — but ``regions-fill``'s VISIBILITY is derived
  // from both rows (it is dropped only when neither is on), so this IIFE has
  // to hear about an L4 flip too. ``overlayState.l4`` is otherwise re-seeded
  // only at boot and after a basemap swap, which would leave the derivation
  // reading a stale value for the rest of the session.
  document.addEventListener('snowdesk:overlays-changed', (e) => {
    if (!e.detail || e.detail.key !== 'l4') return;
    overlayState.l4 = !!e.detail.visible;
    applyBulletinsVisibility();
  });

  // Bridge for basemapPickerInit, mirroring the snowdesk:country-toggle one
  // below: the picker owns the click, this IIFE owns the state.
  //
  // The downloads half of the exclusivity is gone (SNOW-663) — raising the
  // step no longer switches the squares off, because the hatch and the infill
  // are legible together. This used to call ``choose``, the transition that
  // cleared the DOWNLOADS suppression on the user's behalf; with nothing
  // setting that reason it did nothing ``setPreference`` does not, and it has
  // been removed rather than left as a second name for one operation.
  // Suppressions are deliberately untouched here: resort-edit mode is a mode
  // the user is still in, and picking a step says what to show when it ends.
  document.addEventListener('snowdesk:bulletins-step', (e) => {
    const next = BULLETINS_CORE.nearestStep(e.detail && e.detail.step);
    bulletinsVisibility = BULLETINS_CORE.setPreference(bulletinsVisibility, next);
    overlayState.bulletins = bulletinsVisibility.preference;
    writeStorage(OVERLAY_STORAGE_KEY.bulletins, String(overlayState.bulletins));
    applyBulletinsVisibility();
    // The boundary is per-date and lazily fetched: a first enable has never
    // loaded it, and a re-enable may be holding a day the user scrubbed past
    // while it was hidden. The overlay-load handler re-reads the state
    // before making anything visible, so this is safe if the user steps back
    // to 0 before the fetch settles.
    if (next > 0) {
      document.dispatchEvent(new CustomEvent('snowdesk:overlay-load', {
        detail: { key: 'l3' },
      }));
    }
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

  // ==== SNOW-570/SNOW-587: the downloaded-tiles overlay ====
  // (SNOW-645 review: no longer a togglable layers-menu row — see
  // downloadedOverlayVisible's own declaration above for why. SNOW-645
  // second review: it is now a "Display on the map" switch INSIDE the
  // "Manage downloads" panel, not the sheet's own open/closed state — a
  // sheet that is bottom-docked and full-width on mobile would otherwise
  // cover the very squares it draws, making the overlay unreachable on
  // the platform that needs offline maps most. Neither opening nor closing
  // the sheet touches the overlay now — SNOW-656 also stopped open() calling
  // show() — and the switch's setting is persisted across reloads like the
  // other three panels'.)
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
  // THE ACTIVE BASEMAP ONLY — the roundels' own scope, and Hugo's call
  // after living with the alternative. SNOW-645 review widened this to
  // paint EVERY downloaded basemap at once, each in its own identity
  // colour, because switching from OpenFreeMap to Swisstopo emptied the
  // overlay outright and that read as data loss. The cure was worse: two
  // basemaps' squares stack over the same ground, so what the map showed
  // was neither basemap's coverage. "It should filter to the current
  // basemap, so it never overlays downloads. If you are on Swisstopo and
  // toggle on the downloads it shows Swisstopo downloads. If you then
  // switch maps it honours the toggle and shows the new map downloads."
  //
  // The emptying-out that started all this is no longer a silent one: the
  // switch is persisted and stays on across the swap, the overlay repaints
  // from the snowdesk:basemap-changed listener below, and the "Manage
  // downloads" panel lists every area under every basemap, each row naming
  // the basemap its record was written under — so an area that is not drawn
  // right now is still one tap from being accounted for.
  //
  // So this reads basemapDownloadedTemplates()
  // (static/js/map_basemap_downloads.js) for the DISTINCT templates
  // actually recorded, keeps the one matching the live style's own tile
  // template, and runs cachedTilesFromURLs against that alone (still a real
  // Cache Storage read per tile — "probed, never stored" above still holds,
  // this is not a switch to painting stored record geometry). Every square
  // it paints therefore belongs to the basemap under it, which is what lets
  // the colour be one flat value per refresh rather than a per-feature
  // expression.

  // Coalesces overlapping refreshes: several of the signals below can land
  // together (a download settling also refreshes the sync dashboard, which
  // can coincide with a basemap swap), and each one is a cache scan.
  let downloadedRefreshInFlight = null;

  // True while an idle retry is already queued, so repeated unresolved
  // refreshes share one listener rather than stacking.
  let downloadedStyleRetryPending = false;

  /**
   * Re-run the refresh on the next MapLibre idle — i.e. once the style has
   * settled.
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
   * Re-derive which of the ACTIVE basemap's tiles are cached, and paint the
   * overlay. Areas downloaded under any other basemap are not drawn — see
   * the block comment above.
   *
   * A no-op while the overlay is switched off: nothing is on screen to be
   * wrong, and the work is a cache scan per template. Every path that turns
   * it back on (window.pwaDownloadedOverlay.show()) refreshes first, so it
   * can never be revealed holding a stale answer.
   *
   * @returns {Promise<void>}
   */
  const refreshDownloadedOverlay = () => {
    if (!downloadedOverlayVisible) return Promise.resolve();
    if (downloadedRefreshInFlight) return downloadedRefreshInFlight;
    downloadedRefreshInFlight = (async () => {
      const core = self.pwaBasemapDownloadCore;
      if (!core || !map.isStyleLoaded() || !map.getSource('cached-tiles')) {
        // No settled style, or installRegionsLayers hasn't reached this
        // pair yet, means no reliable read yet — leave whatever is painted
        // alone rather than clearing it, and come back on the next
        // MapLibre idle. Same "can't tell yet ≠ not cached" distinction the
        // download roundels' own probe makes.
        _refreshDownloadedWhenStyleSettles();
        return;
      }
      const activeTemplate = activeBasemapTileTemplate(map);
      if (!activeTemplate) {
        // The style is settled (guarded above) but declares no vector tile
        // source — SNOW-483's inline fallback style, swapped in when the
        // basemap's own style document can't be fetched. There is no basemap
        // on screen, so there is no "current basemap" to filter to, and
        // squares drawn here would be attributed to a basemap that is not
        // being rendered.
        //
        // Reachable while downloads exist: every download pins the style
        // document of the basemap it ran under, so a user offline on a
        // basemap they HAVE downloaded still gets a real style and real
        // squares. It is the other case — offline, having switched to a
        // basemap they never downloaded under — that lands here, and under
        // this overlay's rule the answer for that basemap is "nothing"
        // either way. The "Manage downloads" panel still accounts for every
        // area, on every basemap, offline included.
        const emptySource = map.getSource('cached-tiles');
        if (emptySource) emptySource.setData({ type: 'FeatureCollection', features: [] });
        return;
      }
      const [cached, templates] = await Promise.all([
        pinnedBasemapCacheURLs(),
        basemapDownloadedTemplates(),
      ]);

      // ONE cachedTilesFromURLs pass, against the ACTIVE basemap's template
      // alone. Still a real Cache Storage read per tile, never a read of
      // stored record geometry (see the block comment above).
      //
      // `basemapDownloadedTemplates()` returns every downloaded basemap's
      // template; the ones that are not on screen are dropped here rather
      // than in that reader, which several other callers share.
      const activeKey = activeBasemapKey();
      const features = [];
      for (const { template } of templates) {
        if (template !== activeTemplate) continue;
        for (const tile of core.cachedTilesFromURLs(template, cached, CACHED_TILES_ZOOM)) {
          features.push({
            type: 'Feature',
            properties: {},
            geometry: core.bboxPolygon(core.tileBounds(tile.z, tile.x, tile.y)),
          });
        }
      }

      // Re-applied on EVERY refresh, not only when installRegionsLayers
      // happens to have (re)created the layers this call: a basemap switch
      // normally does force a full re-add (setStyle wipes every custom
      // layer), but this call is also the one show()/hide() and a settling
      // download themselves trigger — with no re-add — and relying on
      // re-add alone would leave an already-installed pair of layers
      // painted in the colour of the basemap that was active at the LAST
      // refresh.
      if (map.getLayer('cached-tiles-fill')) {
        // A `fill-pattern` naming an image the style is not holding paints
        // nothing at all, so the image comes first.
        ensureHatchImage(activeKey);
        map.setPaintProperty('cached-tiles-fill', 'fill-pattern', hatchImageId(activeKey));
      }
      if (map.getLayer('cached-tiles-line')) {
        map.setPaintProperty('cached-tiles-line', 'line-color', basemapIdentityColour(activeKey));
      }

      const tileSource = map.getSource('cached-tiles');
      if (tileSource) {
        tileSource.setData({ type: 'FeatureCollection', features: features });
      }
    })().catch(() => {}).finally(() => {
      downloadedRefreshInFlight = null;
    });
    return downloadedRefreshInFlight;
  };

  /**
   * Broadcast a change in the downloaded-areas overlay's visibility.
   *
   * The "Display on the map" switch inside the downloads sheet is not the
   * only thing that can change this — placement focus clears every app layer
   * off the map — and the switch has to move rather than sit there claiming
   * a state the map does not have. The sheet owns its own DOM, so it listens
   * for this instead of this IIFE reaching into it.
   *
   * @returns {void}
   */
  const announceDownloadedOverlay = () => {
    document.dispatchEvent(new CustomEvent('snowdesk:downloaded-overlay-changed', {
      detail: { visible: downloadedOverlayVisible },
    }));
    announceOverlayVisibility();
  };

  /**
   * Switch the overlay on and (re)probe it. Called only from
   * window.pwaDownloadedOverlay.show() — see that export for callers.
   *
   * SNOW-656 made this the point where the choropleth yielded: two
   * translucent fills over the same polygons were unreadable together, so
   * showing the squares suppressed the infill. That exclusivity is GONE —
   * the squares are a diagonal hatch now (see ``buildHatchImage``), an
   * annotation the danger colour reads through rather than a second tint
   * competing with it, so the two coexist and neither toggle touches the
   * other's state. Both remain independently switchable, which is what the
   * user asked for: "which days are dangerous" and "which areas do I have
   * offline" are different questions and can be on screen at once.
   *
   * Visibility is bound to the overlay itself and NOT to the sheet's
   * open/closed lifecycle — closing the sheet calls nothing, so the squares
   * survive a dismissal (the sheet covers them on mobile). SNOW-656 also
   * stopped open() calling show(); that stays, since opening a sheet should
   * not repaint the map on its own.
   *
   * The choice is persisted here and restored at boot, the same as the other
   * three panel switches — see downloadedOverlayVisible's own declaration.
   *
   * @returns {Promise<void>}
   */
  const showDownloadedOverlay = () => {
    downloadedOverlayVisible = true;
    writeStorage(OVERLAY_STORAGE_KEY.downloads, 'true');
    for (const id of ['cached-tiles-fill', 'cached-tiles-line']) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'visible');
    }
    announceDownloadedOverlay();
    return refreshDownloadedOverlay();
  };

  /**
   * Switch the overlay off. No re-probe needed — hidden means nothing on
   * screen to be wrong, same as the toggle-off branch this replaces.
   *
   * Nothing to unsuppress any more: the Bulletins layers were never touched
   * on the way in, so the choropleth is already at whatever step the user
   * chose.
   *
   * @returns {void}
   */
  const hideDownloadedOverlay = () => {
    downloadedOverlayVisible = false;
    writeStorage(OVERLAY_STORAGE_KEY.downloads, 'false');
    for (const id of ['cached-tiles-fill', 'cached-tiles-line']) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
    }
    announceDownloadedOverlay();
  };

  // The tile set can change under a switched-on overlay — a lazy country
  // load brings regions whose download state has never been probed, and a
  // basemap swap changes which template is the active one, which is now
  // exactly what decides WHICH downloads are drawn. The swap listener is
  // therefore load-bearing rather than housekeeping: it is what makes the
  // switch survive a basemap change and come back showing the new basemap's
  // areas. (setStyle also re-adds the source geometry empty, so a refresh
  // would be needed to repopulate it either way.)
  document.addEventListener('snowdesk:basemap-changed', () => refreshDownloadedOverlay());
  document.addEventListener('snowdesk:regions-loaded', () => refreshDownloadedOverlay());

  // The download controls call this when a run settles, alongside their
  // pwaLayerSyncStatus.refresh() — the tiles it just fetched should appear
  // without the sheet being reopened. Exposed the same way pwaLayerSyncStatus
  // is, because those controls live in sibling IIFEs.
  //
  // SNOW-645 review (Hugo's second call — the first version of this bridge
  // bound visibility to the sheet being OPEN, full stop; that made the
  // overlay unreachable on mobile, where the sheet is bottom-docked and
  // full-width, covering the very squares it would have drawn): neither
  // opening nor closing the sheet touches the overlay any more. The panel's
  // "Display on the map" switch is the only caller of show()/hide(), and
  // what it sets is a PERSISTED preference like the other three panels' —
  // close the sheet with it on, look at the map, come back tomorrow and it
  // is still on. Both reads below are functions, not plain frozen
  // properties, since what they answer changes after this object is built.
  //
  // SNOW-658 review: ``isVisible()`` reads the SQUARES, not the flag that
  // asked for them — see the isVisible/isEnabled note beside the two
  // panel-driven bridges below for why the pair exists. The flag and the
  // paint agree everywhere show()/hide() are the only writers, which was the
  // whole of this overlay's life until placement focus (which clears every
  // app layer off the map without touching any bridge). They also disagree,
  // legitimately, while the overlay is on and the active basemap has no
  // downloads: the switch reads ON (the preference took) over an empty
  // source. ``isEnabled()`` publishes the flag itself, which is what the
  // in-sheet switch reads on every open.
  window.pwaDownloadedOverlay = Object.freeze({
    refresh: refreshDownloadedOverlay,
    show: showDownloadedOverlay,
    hide: hideDownloadedOverlay,
    isVisible: () => layerPainted('cached-tiles-fill'),
    isEnabled: () => downloadedOverlayVisible,
  });

  // ==== SNOW-658: the two user-data overlays, driven from their own panels ====
  //
  // Favourites and Community reports lost their layers-menu rows this ticket.
  // The switch that drives each now lives in the panel its own roundel opens —
  // the pattern SNOW-634 set for downloads — so the callers are favourites.js
  // and report.js, separate IIFEs that reach this one through a frozen bridge
  // of the same shape ``pwaDownloadedOverlay`` already publishes.
  //
  // Being INSIDE this IIFE, these do directly what map_basemap_picker.js had
  // to ask for across the boundary: write the persisted preference, dispatch
  // the lazy load, and — for favourites — recompute the favourited-resort
  // exclusion. (``snowdesk:favourites-visibility-changed`` stays: other
  // callers still fire it, and its listener is the same one-line call.)
  //
  // Unlike the downloads overlay these ARE persisted, exactly as the rows
  // were: same localStorage key, same default, so a device carrying a
  // preference from before this ticket keeps it.

  /**
   * Show a panel-driven overlay: persist the preference, then hand off to the
   * lazy-load path, which fetches the GeoJSON (first enable only), installs
   * the layers and makes them visible.
   *
   * @param {string} key - ``'favourites'`` or ``'community_reports'``.
   * @returns {void}
   */
  const showPanelOverlay = (key) => {
    // First: the overlay-load handler re-reads this persisted value before it
    // paints (SNOW-493), so a write after the dispatch would be read too late.
    writeStorage(OVERLAY_STORAGE_KEY[key], 'true');
    overlayState[key] = true;
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-load', {
      detail: { key },
    }));
    // SNOW-499: that handler calls this too, but only once its fetch settles —
    // and ``ensureOverlayLoaded`` short-circuits for an already-loaded layer,
    // so on a re-enable nothing would call it at all.
    if (key === 'favourites') applyResortsFavouritedFilter();
    // SNOW-658 review: nothing is painted yet — the handler above sets
    // visibility a microtask later at the earliest — so this announcement
    // says "asked for, not drawn", and the one at the end of that handler
    // says "drawn" (or, on a failed fetch, "still not drawn"). Both are
    // wanted: the pair is what makes an overlay that never arrives
    // distinguishable from one that has.
    announceOverlayVisibility();
  };

  /**
   * Hide a panel-driven overlay: persist the preference and drop every layer
   * it owns. No fetch and no re-probe — hidden means there is nothing on
   * screen to be wrong, the same reasoning ``hideDownloadedOverlay`` carries.
   *
   * @param {string} key - ``'favourites'`` or ``'community_reports'``.
   * @returns {void}
   */
  const hidePanelOverlay = (key) => {
    writeStorage(OVERLAY_STORAGE_KEY[key], 'false');
    overlayState[key] = false;
    for (const layerId of OVERLAY_LAYER_IDS_MAIN[key]) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, 'visibility', 'none');
      }
    }
    // SNOW-499: a favourited resort must get its plain dot back the moment its
    // star is hidden, or it disappears from the map entirely — the side effect
    // the picker's own off-path used to trigger by CustomEvent.
    if (key === 'favourites') applyResortsFavouritedFilter();
    announceOverlayVisibility();
  };

  // ==== A panel wrote to the server, so the layer behind it is stale ====
  //
  // The three UGC overlays are the user's own data drawn twice — once as
  // rows in a panel, once as features on the map — and only ONE of the two
  // was kept current after a write. Delete a route or a field observation
  // from its panel and the row went, while the line or the flag stayed on
  // the map until the page was reloaded; upload a route and the panel
  // listed it while the map did not draw it.
  //
  // ``snowdesk:favourites-changed`` already existed for the third
  // (SNOW-414), so the fix is that shape twice over rather than a new
  // mechanism: each panel announces that ITS data moved, and this file
  // decides what that costs the map. The panels stay ignorant of sources,
  // caches and layer ids, which is the same division ``pwaRowFocus`` and
  // the overlay bridges already draw.
  //
  // Downloads is deliberately absent: its areas are device state rather
  // than server state, and ``window.pwaDownloadedOverlay.refresh()`` — a
  // Cache Storage re-probe, not a fetch — is already called on every path
  // that adds or evicts one.

  /**
   * Re-read one panel overlay's payload from the server and repaint it.
   *
   * Gated on ``overlayLoaded[key]``, which is the difference between a
   * refresh and a load: an overlay the user has never enabled has no
   * source to setData and must not be installed here, because installing
   * is what ``ensureOverlayLoaded`` does when they ask for it — and it
   * will fetch this same URL then. The gate is NOT ``overlayState[key]``:
   * a hidden-but-loaded overlay keeps its layers and its cache, and
   * skipping it would leave the stale copy to be revealed by the next
   * show() (which short-circuits on ``overlayLoaded``).
   *
   * Write-through to the offline overlay cache mirrors ``_loadOverlay``'s,
   * and for the same reason: a deletion the user made online must not come
   * back the next time the map reads that key offline.
   *
   * Failure is silent. The write it follows has already been reported by
   * the panel that made it, and a refetch that cannot land leaves the map
   * showing what it showed a moment ago — stale, but not wrong about
   * anything it claims to know.
   *
   * @param {string} key - ``'routes'`` or ``'community_reports'``.
   * @returns {Promise<void>}
   */
  const refreshPanelOverlay = (key) => {
    if (!overlayLoaded[key]) return Promise.resolve();
    const url = key === 'routes' ? ROUTES_URL : COMMUNITY_REPORTS_URL;
    if (!url) return Promise.resolve();
    return fetch(url)
      .then(r => r.json())
      .then((data) => {
        if (!data) return;
        window.pwaMapOverlayCache?.putOverlay(key, data);
        if (key === 'routes') {
          routesGeojsonCache = data;
          // TWO sources, not one: the lines and the derived start/finish
          // points (see installRoutesLayer). Refreshing only the first
          // would leave a deleted route's flag standing on the map.
          map.getSource('routes')?.setData(data);
          map.getSource('route-endpoints')?.setData(routeEndpointsFor(data));
        } else {
          // Cached pristine above, mutated here — the same order
          // ``_loadOverlay`` uses, so the stored copy is the server's
          // payload and the drawn copy carries the age fade.
          communityReportsGeojsonCache = withCommunityReportsAgeOpacity(data);
          map.getSource('community-reports')?.setData(communityReportsGeojsonCache);
        }
      })
      .catch(() => {});
  };

  // SNOW-752: these two bound inside ``map.on('load')`` for their whole life,
  // which is a bind point that does not always come — ``load`` waits on the
  // first complete render, so a basemap style that fails to load (offline
  // with nothing cached, or an unreachable tile origin) means it never fires
  // and every listener inside it is silently never registered. The favourites
  // LAYER does not depend on that handler — ``snowdesk:overlay-load`` installs
  // it from IIFE level — so the map would draw pins it then had no way to
  // update. Moved here beside the two below so all three write-listeners bind
  // the same way, and none of them depends on a render.
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

  // Bound at IIFE level rather than inside ``map.on('load')`` — a style that
  // never loads must not cost the map its ability to notice a write, and
  // every identifier these two touch is declared above.
  document.addEventListener('snowdesk:routes-changed', () => {
    refreshPanelOverlay('routes');
  });
  document.addEventListener('snowdesk:reports-changed', () => {
    refreshPanelOverlay('community_reports');
  });

  // ==== SNOW-658 review: isVisible() means PAINT, isEnabled() means INTENT ====
  //
  // All four bridges (SNOW-687 added routes) answer two different questions,
  // and conflating them was the defect this pair of methods removes. ``isVisible()`` is "these layers
  // are drawn on the map right now" — read off MapLibre, never off a flag.
  // ``isEnabled()`` is "the user asked for this overlay" — the persisted
  // preference, which is what decides what to restore at boot and what the
  // panel switch shows as its own checked state on open. All four are
  // persisted now; downloads was the session-scoped exception until its
  // switch was found to forget across a reload.
  //
  // The two can legitimately disagree, and the case they disagree in is the
  // one that matters: enable favourites offline with nothing cached and the
  // switch reads ON (the preference took) while the roundel's ring stays off
  // (nothing was drawn). That is not a glitch to paper over — it is the only
  // way the user can see that their request has not reached the map. A ring
  // claiming "shown" over a blank map would be the same defect class as a
  // sync dot claiming a layer is cached when it isn't.
  //
  // So: status indicators (the roundel ring) read isVisible(); controls that
  // state what the user asked for (the panel switches) read isEnabled().

  window.pwaFavouritesOverlay = Object.freeze({
    show() {
      showPanelOverlay('favourites');
      window.pwaTelemetry?.emit('map.favourite.overlay_toggled', { visible: true });
    },
    hide() {
      hidePanelOverlay('favourites');
      window.pwaTelemetry?.emit('map.favourite.overlay_toggled', { visible: false });
    },
    isVisible: () => panelOverlayPainted('favourites'),
    isEnabled: () => !!overlayState.favourites,
  });

  window.pwaCommunityReportsOverlay = Object.freeze({
    show() {
      showPanelOverlay('community_reports');
      window.pwaTelemetry?.emit('map.community_reports.overlay_toggled', {
        visible: true,
      });
    },
    hide() {
      hidePanelOverlay('community_reports');
      window.pwaTelemetry?.emit('map.community_reports.overlay_toggled', {
        visible: false,
      });
    },
    isVisible: () => panelOverlayPainted('community_reports'),
    isEnabled: () => !!overlayState.community_reports,
  });

  // SNOW-687: the fourth bridge, and the same shape as the three above —
  // ``showPanelOverlay``/``hidePanelOverlay`` needed no edit to take a new
  // key, since their only key-specific branch is the favourites
  // resort-exclusion recompute. Read by static/js/routes.js (the panel
  // switch, via isEnabled) and by static/js/map_roundel_overlay_state.js
  // (the roundel ring, via isVisible) — see the block above for why those
  // two are different questions.
  window.pwaRoutesOverlay = Object.freeze({
    show() {
      showPanelOverlay('routes');
      window.pwaTelemetry?.emit('map.route.overlay_toggled', { visible: true });
    },
    hide() {
      hidePanelOverlay('routes');
      window.pwaTelemetry?.emit('map.route.overlay_toggled', { visible: false });
    },
    isVisible: () => panelOverlayPainted('routes'),
    isEnabled: () => !!overlayState.routes,
  });

  // ==== SNOW-803: ``?panel=<name>`` opens a sheet with nothing selected ====
  //
  // The three account list pages became permanent redirects to the map
  // with the matching sheet open (docs/decisions/two-documents-and-a-map.md).
  // Only item-specific deep links existed before — ``?favourite=<uuid>``
  // and ``?route_share=<token>``; this is the sheet-level one. Consumed
  // the way ``consumeFavouriteDeepLink`` is, read once and stripped from
  // the address bar, so a refetch or a shared URL does not reopen a sheet
  // nobody asked for.
  //
  // Each sheet module exposes ``open()`` on a frozen ``window.pwa*Sheet``
  // bridge — its roundel's own open path, which goes through
  // MapSheet.attach's registration with window.pwaMapOverlays. So an open
  // from here closes whatever else is up, exactly as a tap would; a path
  // that skipped the registry would be two overlays open at once.
  //
  // The bridges are assigned by other deferred scripts. In home.html the
  // surfaces' tags precede map.js, so they exist by now; a page that
  // orders them the other way is covered by the DOMContentLoaded fallback,
  // by which point every deferred script has run.
  const PANEL_SHEET_BRIDGES = {
    favourites: () => window.pwaFavouritesSheet,
    routes: () => window.pwaRoutesSheet,
    reports: () => window.pwaReportSheet,
  };

  /**
   * Read ``?panel=`` and strip it from the address bar in one step.
   *
   * @returns {?string} The requested sheet name, or null when there is none.
   */
  const consumePanelDeepLink = () => {
    const params = new URLSearchParams(location.search);
    const name = params.get('panel');
    if (!name) return null;
    params.delete('panel');
    const query = params.toString();
    history.replaceState(
      null,
      '',
      location.pathname + (query ? `?${query}` : '') + location.hash,
    );
    return name;
  };

  /**
   * Honour a ``/?panel=favourites|routes|reports`` arrival.
   *
   * Silent for a name no sheet answers to: the parameter is consumed
   * either way, and the map opens as it otherwise would.
   *
   * @returns {void}
   */
  const openPanelDeepLink = () => {
    const name = consumePanelDeepLink();
    // Own-property check, not a bare index: the name is whatever the URL
    // says, and ``PANEL_SHEET_BRIDGES['constructor']`` is a function too.
    if (!name || !Object.hasOwn(PANEL_SHEET_BRIDGES, name)) return;
    const bridge = PANEL_SHEET_BRIDGES[name]();
    if (!bridge) return;
    bridge.open();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', openPanelDeepLink, { once: true });
  } else {
    openPanelDeepLink();
  }

  // ==== The camera, for a panel row that names a place ====
  //
  // Hugo: "For routes, resorts, and observations, clicking on the name of an
  // item should zoom in to it." Each of those three panels lives in its own
  // IIFE with no access to ``map`` — ``map.js`` declares it with ``const``
  // inside this one, so it is not a window property and a panel could not
  // reach it even by name (the class of miss ``tox -e js-globals-lint``
  // exists to catch). This is the bridge they call instead, and it is
  // deliberately the SMALLEST one that answers the question: move the
  // camera to a coordinate. It knows nothing about routes, favourites or
  // observations, so a fifth surface with a place in it needs no edit here.
  //
  // WHY IT TAKES A COORDINATE AND NOT A UUID. The obvious alternative is
  // ``focus(uuid)`` per overlay, looking the feature up in the collection
  // the layer was installed from, the way ``openFavouriteDeepLink`` does.
  // Two things rule it out. The community-reports feed carries no uuid at
  // all — it is anonymised server-side (apps/public/api.py), and adding an
  // identifier to a public payload to serve the owner's own panel is the
  // wrong trade. And a uuid lookup can only answer once the overlay's
  // source has loaded, so every caller would need the deep link's
  // wait-for-``sourcedata`` dance for a camera move that does not depend on
  // it. The rows are server-rendered from the owner's own data and already
  // know where their item is; passing that through is both simpler and the
  // only thing that works for all three.
  //
  // The overlay is a separate concern and stays with the caller: each panel
  // already holds its own ``pwa*Overlay`` bridge and turns it on before
  // calling here, so nothing about which layers are drawn leaks into a
  // camera API.

  // Close enough to read one pin against its surroundings. Past the
  // favourite-label minzoom (8) so a pin arrives named, past the L4
  // boundary minzoom (8.5) so it sits visibly inside its danger-rated
  // region, and past the resort-label minzoom (10) so the places around it
  // name themselves too. Tighter than this fills the viewport with terrain
  // and drops every one of those cues. Shared with the
  // ``/map/?favourite=<uuid>`` deep link, which framed a pin first and set
  // the number.
  const POINT_FOCUS_ZOOM = 11;

  // Compute the lng/lat bounding box of a GeoJSON Polygon or MultiPolygon.
  // MapLibre's fitBounds takes [[west, south], [east, north]].
  //
  // Module scope since SNOW-811: the region popup's own fits used it from a
  // nested block, and `pwaMapFocus.region()` below needs the same answer for
  // a downloads row naming a region. One definition, two scopes — a second
  // copy is how the popup's framing and the panel's would come to disagree
  // about where a region is.
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

  // The fit behind both `bounds` and `region` below. A module-scope
  // function rather than one bridge method calling another through `this`:
  // a caller that destructures the frozen bridge (`const { region } =
  // window.pwaMapFocus`) would lose `this` and take the method with it.
  const focusBounds = (bbox) => {
    if (!map || !Array.isArray(bbox) || bbox.length !== 4) return;
    if (!bbox.every((n) => Number.isFinite(n))) return;
    map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], {
      padding: { top: 60, right: 40, bottom: 40, left: 40 },
      maxZoom: 10,
      duration: 400,
    });
  };

  window.pwaMapFocus = Object.freeze({
    /**
     * Centre the map on one point.
     *
     * Zooms IN only: a viewer already closer than ``POINT_FOCUS_ZOOM`` has
     * chosen that scale, and pulling them back out to frame a pin they
     * asked to be taken to would undo it. From further out — the usual
     * case, since the panels are opened over a country-level map — this
     * lands at the readable floor above.
     *
     * @param {number} lon Longitude in WGS-84 degrees.
     * @param {number} lat Latitude in WGS-84 degrees.
     * @returns {void}
     */
    point(lon, lat) {
      if (!map || !Number.isFinite(lon) || !Number.isFinite(lat)) return;
      map.flyTo({
        center: [lon, lat],
        zoom: Math.max(map.getZoom(), POINT_FOCUS_ZOOM),
      });
    },

    /**
     * Frame a bounding box.
     *
     * No zoom floor here, and no "in only" rule: a bbox names an extent
     * rather than a place, and a long route can only be seen whole by
     * zooming out. Padding, ``maxZoom`` and duration match
     * ``activateRoute``'s own fit, so a route framed from its panel row and
     * the same route framed by tapping its line come to rest identically.
     *
     * @param {number[]} bbox GeoJSON bbox — [west, south, east, north].
     * @returns {void}
     */
    bounds(bbox) {
      focusBounds(bbox);
    },

    /**
     * Frame one EAWS micro-region by its id (SNOW-811).
     *
     * The downloads panel's region rows are the caller. A region download
     * is recorded against a ``region_id`` and carries no geometry of its
     * own — ``reconcileAreas`` leaves its ``bbox`` null — because the
     * polygon is already on the map, in ``FEATURE_BY_REGION_ID``. So the
     * row names the region and this resolves it, rather than the record
     * growing a copy of a boundary the map is drawing anyway.
     *
     * Runs the same ``focusBounds`` fit as ``bounds`` above, so a region
     * framed from a downloads row comes to rest exactly where a custom
     * area's bbox would — and reads the same ``featureBBox`` the region
     * popup's own fits use, so the two cannot disagree about where a
     * region is.
     *
     * Returns silently for an id the map has no feature for: regions load
     * per country as the viewport moves, so a row can name a region this
     * session has not fetched. Nothing to say about it — see
     * static/js/row_focus.js on why a press that cannot be served moves
     * nothing rather than explaining itself.
     *
     * @param {string} regionId An EAWS micro-region id ("CH-4242").
     * @returns {void}
     */
    region(regionId) {
      if (!map || !regionId) return;
      const feature = FEATURE_BY_REGION_ID[regionId];
      if (!feature || !feature.geometry) return;
      const [[w, s], [e, n]] = featureBBox(feature);
      focusBounds([w, s, e, n]);
    },
  });

  // SNOW-658: all four bridges now exist, so a listener can safely be told
  // to read them. Announced HERE rather than beside the seed loop near the
  // top of this IIFE, because a listener that hears it will immediately call
  // ``isVisible()`` on all four, and three of them do not exist until the
  // lines above have run.
  //
  // SNOW-658 review: this announcement now says "nothing is drawn yet", and
  // that is the truth at parse time — no overlay layer has been installed,
  // whatever ``overlayState.favourites`` (default ON) says the user wants.
  // The rings light as each install lands, from the install functions
  // themselves. Still worth making: it settles the roundels against a live
  // read on a page where the map never finishes booting.
  announceOverlayVisibility();

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
      // SNOW-658: the ROW's key — a grouped provider row (ALBINA) receives one
      // of these per code, and painting the same row "syncing" twice is
      // harmless and correct: it is waiting on both.
      sync?.markSyncing(overlayKeyForCountry(code));
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

  // Most recent date the choropleth is showing — seeded from the ``?d=`` on
  // the URL, falling back to today (SNOW-793), then kept in sync by every
  // ``snowdesk:date-changed`` event.
  // Hoisted to outer-IIFE scope so the date-changed listener below can be
  // registered synchronously (before the map's 'load' event fires), making
  // it active in environments where the map never loads (e.g. Playwright
  // offline headless tests).
  let currentDisplayedDate = readDisplayDate();

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
  // Also skipped while the boundary is hidden — refetching it per scrubbed
  // date would be pure network cost for nothing on screen.
  //
  // SNOW-656: that used to read L4's localStorage key directly, on the
  // reasoning that the picker writes it on every click while ``overlayState``
  // is only refreshed on a toggle-ON. The Bulletins row has no such gap:
  // every transition goes through ``bulletinsVisibility``, which is the live
  // state (and the only thing that knows about suppression — a boundary
  // hidden because the downloads overlay is on must not be refetched either).
  //
  // SNOW-660: a commit can now carry ``date: null`` — the scrubber's popstate
  // handler dispatches one when a back step lands on a URL with no ``?d=``,
  // i.e. back to "no day asked for". That has to BLANK the boundary rather
  // than return early and leave the previous day's outline sitting over a
  // choropleth that has just been cleared.
  document.addEventListener('snowdesk:date-changed', (e) => {
    if (!overlayLoaded.l3) return;
    if (!BULLETINS_CORE.isEffective(bulletinsVisibility)) return;
    const dk = (e.detail && e.detail.date) || null;
    if (!dk) {
      blankGroupings();
      return;
    }
    scheduleGroupingsForDate(dk);
  });

  // SNOW-761: re-project the weather overlay for the newly committed date.
  // No fetch — the payload already holds the whole forecast window, so a
  // date change is an in-memory transform (map_weather_core.js) and a
  // setData call. Registered unconditionally: refreshWeatherSourceData
  // no-ops until the layer exists.
  //
  // ``currentDisplayedDate`` is updated by the listener registered above,
  // and listeners fire in registration order, so it is already the new date
  // by the time this one runs.
  document.addEventListener('snowdesk:date-changed', () => {
    refreshWeatherSourceData();
  });

  // SNOW-761: the cluster-to-lowest collapse is zoom-dependent, so zooming
  // has to re-derive which station stands for each cluster. `moveend`
  // rather than `zoom`: the collapse walks every feature, and running it on
  // each frame of a pinch would do that work sixty times a second for a
  // picture nobody sees until the gesture ends.
  map.on('moveend', () => {
    refreshWeatherSourceData();
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
    // (?d=<date>&country=ch, ~2 KB). Choropleth is painted via
    // setFeatureState — no more property-based rating on features.
    // SNOW-660: that date is the one the visitor ASKED for (``?d=``), and
    // nothing else. With an empty querystring the leg is skipped entirely
    // rather than fetched for a day the map picked itself: a request whose
    // only possible use is painting a day nobody chose is a request not
    // worth making, and skipping it also removes the late-landing frame that
    // SNOW-656 had to guard against.
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
    // SNOW-660: the day the URL asked for — or, since SNOW-793, today when
    // it asked for none. Read once here so the fetch and the frame it is
    // unpacked from cannot disagree.
    //
    // This is the cold-load cost of the default: one single-date ratings
    // leg (~2 KB), already in this Promise.all and already parallel with
    // the regions fetch. The full-season payload stays lazy — the scrubber
    // does NOT reach for it to apply the default (see map_scrubber.js).
    const requestedDate = readDisplayDate();
    const [geojson, bootRatingsPayload, resorts] =
      await Promise.all([
        fetch(REGIONS_URL + '?country=ch').then(r => {
          if (!r.ok) throw new Error('regions fetch failed');
          return r.json();
        }).catch((err) => {
          console.warn('[map] regions fetch failed', err);
          return null;
        }),
        RATINGS_URL && requestedDate
          ? fetch(RATINGS_URL + '?d=' + requestedDate + '&country=ch').then(r => {
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
    // bootRatingsPayload shape: { "YYYY-MM-DD": { region_id: rating_int } }.
    // SNOW-660: keyed by the REQUESTED day — with none asked for the payload
    // is the empty object the skipped leg resolved to, and this is empty too.
    const bootRatings = requestedDate ? (bootRatingsPayload[requestedDate] || {}) : {};

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
    // SNOW-691: before the regions, so the raster lands under the
    // choropleth. installSlopeLayer also passes `beforeId: 'regions-fill'`
    // when that layer already exists, so the ordering survives the
    // styledata path running these two the other way round.
    installSlopeLayer();
    installRegionsLayers(geojson);
    // The downloaded-areas overlay is a persisted preference now, so a
    // reload with it switched on has to arrive with the squares ON the map.
    // installRegionsLayers has just created the two layers VISIBLE (it reads
    // downloadedOverlayVisible for their layout), but visible layers over an
    // empty source paint nothing — only a refresh fills it. The other paths
    // that fill it are all reactions to something happening later (a basemap
    // swap, a lazy country load, a settling download, the switch itself), and
    // a boot where none of those fires is exactly the case being fixed. The
    // call is free when the overlay is off — refreshDownloadedOverlay returns
    // immediately — and self-retries on MapLibre idle if the style is not
    // settled enough to resolve a tile template yet.
    refreshDownloadedOverlay();
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
    // SNOW-660: renamed from ``paintTodayRatings``. It never painted "today"
    // as such, and now it paints only the day the URL asked for — so the old
    // name described the one behaviour this ticket removed.
    const paintBootRatings = () => {
      // No day is known at all — no ``?d=`` and no readable ``data-today``
      // — so there is nothing to paint and the choropleth stays uncoloured.
      // #map-date-ribbon says so on screen (map_season_ribbon.js), so the
      // blank map is a stated state rather than a page that looks broken.
      // Since SNOW-793 the ordinary cold boot does not reach this branch:
      // it has today.
      if (!requestedDate) return;
      // SNOW-656: do not paint this frame over a LATER commit. It lands
      // asynchronously — gated on the source's 'data' event — and the
      // scrubber may already have repainted for a day the visitor scrubbed
      // to. Because this paint runs with ``clearMissing: false``, it does
      // not wipe the map; it silently overwrites exactly those regions the
      // boot frame has a rating for, leaving them showing the wrong day's
      // colour beside correct neighbours, with nothing on screen to explain
      // the discrepancy.
      if (currentDisplayedDate && currentDisplayedDate !== requestedDate) return;
      self.pwaChoroplethCore.paintRatingsFrame(
        {
          featureById: FEATURE_BY_REGION_ID,
          intToRating: INT_TO_RATING,
          setRating: (featureId, rating) =>
            map.setFeatureState({ source: 'regions', id: featureId }, { rating }),
        },
        bootRatings,
        { clearMissing: false },
      );
    };
    if (map.isSourceLoaded('regions')) {
      paintBootRatings();
    } else {
      const onSourceData = (e) => {
        if (e.sourceId === 'regions' && map.isSourceLoaded('regions')) {
          map.off('sourcedata', onSourceData);
          paintBootRatings();
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

    // The bulletin boundary rides along with the Bulletins row rather than
    // having a toggle of its own (SNOW-656 moved it off L4 — see
    // OVERLAY_VISIBILITY_GOVERNOR), so it loads at boot whenever that row is
    // on, which is the default. Its data is per-date and network-only, so
    // this is a real fetch on every boot, not a cache read — it degrades
    // silently when it fails (see the l3 branch of _loadOverlay). No
    // suppression can be active this early: the downloads overlay starts off
    // and edit mode suppresses from a MAP_READY_PROMISE callback.
    if (overlayState.bulletins) restoreOverlay('l3');

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

    // SNOW-761: restore the weather overlay if the user had it enabled in a
    // prior session. Off by default, so this only fires for a returning
    // user who opted in.
    if (overlayState.weather) {
      restoreOverlay('weather');
    }

    // SNOW-687: restore the routes overlay if the user had it enabled in a
    // prior session. Off by default (like community reports, unlike
    // favourites), so this only fires for a returning user who opted in.
    if (ROUTES_ELIGIBLE && overlayState.routes) {
      restoreOverlay('routes');
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

    // `featureBBox` was declared here until SNOW-811, which needed the same
    // computation from `window.pwaMapFocus.region()` — outside this block.
    // It moved to module scope rather than being copied; the name resolves
    // to the one definition and every call site below is unchanged.

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

    // SNOW-658: the anchored detail popup is a map overlay like the layers
    // menu and the three UGC panels, so it takes part in the same
    // "only one at a time" rule through window.pwaMapOverlays
    // (static/js/map_overlay_exclusivity.js) rather than through the pair of
    // custom events it and favourites.js used to swap.
    window.pwaMapOverlays?.register('map-detail-popup', {
      isOpen: () => !!activeDetailPopup,
      close: closeDetailPopup,
    });

    // Anchor a detail popup at ``lngLat`` with server HTML (``content.html``)
    // or a client-built DOM node (``content.node``). Replaces any open detail
    // popup, dismisses the region popup, and closes every other map overlay —
    // only one map-detail surface is meaningful at a time.
    const mountDetailPopup = (lngLat, content) => {
      closeDetailPopup();
      dismissActivePopupSilently();
      // SNOW-658: replaces the snowdesk:map-detail-opening dispatch, which
      // only favourites.js listened for — so this popup opened over the
      // report sheet, the downloads sheet and the layers menu alike.
      window.pwaMapOverlays?.opening('map-detail-popup');

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
        '__SLUG__', encodeURIComponent(String(resortId)),
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
    // toggle (star tap) has been submitted, so the popup — whose state was
    // captured at open time — closes rather than showing stale content. The
    // next tap on the same pin re-fetches fresh state.
    //
    // SNOW-658: its sibling `snowdesk:favourite-detail-close` went with the
    // rename/delete forms the favourite popup used to host. It existed to
    // close a popup whose row had just deleted itself; the popup now shows
    // only a name and a saved time, so nothing dispatched it any more.
    document.addEventListener('snowdesk:resort-popup-close', closeDetailPopup);

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
    //
    // SNOW-687: 'routes-line' joins the set LAST, and last means lowest
    // priority. A route is a long thin thing that runs UNDER other markers
    // for most of its length, so a favourite star or a report flag sitting
    // on top of one still wins the tap; the route only claims taps nothing
    // else wanted. It is still in the set rather than out of it, because a
    // tap on a track has to open that track rather than select the region
    // the track happens to cross. The casing is deliberately absent: the
    // line's tap tolerance is set explicitly by ROUTE_TAP_SLOP_PX below,
    // and querying a second, wider layer as well would make that tolerance
    // whatever the casing's width happened to be.
    //
    // SNOW-764: 'routes-line-pending' joins on the same terms and directly
    // above the owned line, so a pending route drawn over one of your own
    // takes the tap — it is the one carrying an action (Save) the user has
    // not taken yet.
    const MARKER_EXCLUSION_LAYERS = [
      'community-reports-clusters',
      'favourites-pin',
      'community-reports-point',
      'routes-line-pending',
      'routes-line',
    ];

    // The route line's tap tolerance, in pixels either side of the touch.
    //
    // A pin is a glyph roughly 18px across, so an exact-point hit test lands
    // on it and "the tappable area matches what the user sees" is a true
    // description. A route line is 1.5px wide at z6 and 4px at z12 (see
    // installRoutesLayer's line-width interpolation), and an exact-point
    // test against a 1.5px line is a target no finger can hit — so the tap
    // fell through to regions-fill and SELECTED THE REGION instead, which
    // is what the route tap did in practice for its whole life.
    //
    // 8px is about a third of a fingertip and well inside the gap between
    // two markers, so it makes the line reachable without letting it
    // reach across to steal taps that were aimed at something else.
    const ROUTE_TAP_SLOP_PX = 8;

    /** The exclusion layers that are lines rather than points (SNOW-764). */
    const ROUTE_LINE_LAYERS = ['routes-line', 'routes-line-pending'];

    // Return the highest-priority marker whose rendered glyph is under the tap
    // point, or null. Filters to layers actually present because these
    // overlays are lazy-installed and queryRenderedFeatures throws on an
    // unknown layer id.
    //
    // TWO HIT TESTS, not one, because the layers are two different shapes.
    // The point layers keep the exact-point query — the same hit-test that
    // drives the pointer cursor, so a pin's tappable area is exactly its
    // glyph. The route line gets a slop box, for the reason on
    // ROUTE_TAP_SLOP_PX above.
    //
    // The pins are queried FIRST and win outright, which preserves the
    // priority order the array encodes: a favourite star or a report flag
    // sitting on a route still takes the tap, and the wider box can never
    // let the line outrank one. A route only claims taps nothing else
    // wanted — it is just now able to claim them at all.
    const markerUnderPoint = (point) => {
      const layers = MARKER_EXCLUSION_LAYERS.filter((id) => map.getLayer(id));
      if (!layers.length) return null;

      const pointLayers = layers.filter((id) => !ROUTE_LINE_LAYERS.includes(id));
      if (pointLayers.length) {
        let best = null;
        let bestPriority = Infinity;
        for (const f of map.queryRenderedFeatures(point, { layers: pointLayers })) {
          const priority = MARKER_EXCLUSION_LAYERS.indexOf(f.layer.id);
          if (priority < bestPriority) {
            best = f;
            bestPriority = priority;
          }
        }
        if (best) return best;
      }

      const lineLayers = layers.filter((id) => ROUTE_LINE_LAYERS.includes(id));
      if (!lineLayers.length) return null;
      const slop = ROUTE_TAP_SLOP_PX;
      const box = [
        [point.x - slop, point.y - slop],
        [point.x + slop, point.y + slop],
      ];
      // Topmost first, which for overlapping routes is the one drawn last —
      // the same one the user sees on top at the point they touched. Both
      // line layers are queried in one call (SNOW-764) rather than in the
      // exclusion array's priority order, precisely so that "drawn last"
      // decides: the pending layer is added above the owned one, so a
      // pending route over one of your own wins without a second rule
      // saying so.
      return map.queryRenderedFeatures(box, { layers: lineLayers })[0] || null;
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
    // detail in a popup anchored to the pin — a favourite is a point fixed to
    // the map, so (like a resort or a region) its detail is a pinned popup,
    // not the docked sheet the mobile create/placement pin uses.
    // favourites.js owns the popup's markup, so map.js hands it an empty
    // [data-favourite-detail] container to fill (via the snowdesk:
    // favourite-selected contract), then anchors the filled container in a
    // popup at the favourite's coordinates. If favourites.js isn't loaded
    // (never happens for a rendered favourite pin — the layer is
    // eligibility-gated), the container stays empty and no popup opens.
    //
    // SNOW-658: the detail carries created_at — the raw ISO timestamp off
    // the feature, formatted into the popup's relative subheader by
    // favourites.js exactly as the observation popup below formats
    // observed_at. Reading it off the feature the map already holds is what
    // lets the popup open offline; renaming and removing moved to the
    // favourites panel, which is why nothing else rides this event now.
    const activateFavourite = (feature) => {
      const props = feature.properties;
      const container = document.createElement('div');
      container.setAttribute('data-favourite-detail', '');
      document.dispatchEvent(new CustomEvent('snowdesk:favourite-selected', {
        detail: {
          uuid: props.uuid,
          name: props.name,
          created_at: props.created_at,
          container: container,
        },
      }));
      if (!container.childNodes.length) return;
      mountDetailPopup(feature.geometry.coordinates, { node: container });
    };

    // ==== SNOW-711: the /map/?favourite=<uuid> deep link ====
    //
    // A favourite's detail card carries a crosshair link to this URL, so
    // "show me where this is" is an ordinary navigation rather than a
    // gesture only available to someone already looking at the map. What
    // the visitor lands on has to be indistinguishable from having tapped
    // the pin themselves, which is why this calls ``activateFavourite``
    // above rather than building a popup of its own: two popup paths for
    // one pin drift apart, and the one nobody taps drifts first.
    //
    // The feature is looked up in ``favouritesGeojsonCache``, not through
    // ``queryRenderedFeatures``. The cache is the collection the layer was
    // installed from — the network fetch and the ``pwaMapOverlayCache``
    // offline read-back alike — so the lookup answers identically offline,
    // and it answers for a pin outside the current viewport, which for a
    // pin the visitor is asking to be flown to is the usual case.

    // The reasoning for the number, and the number itself, moved out to
    // ``POINT_FOCUS_ZOOM`` beside ``window.pwaMapFocus`` when the panel
    // rows started framing a pin too. Two constants at 11 would have been
    // one edit away from disagreeing, and there is no reason a favourite
    // reached by link should sit at a different scale from the same
    // favourite reached from its row.
    const FAVOURITE_DEEP_LINK_ZOOM = POINT_FOCUS_ZOOM;

    /**
     * The cached favourite feature carrying ``uuid``, if the collection
     * holds one.
     *
     * @param {string} uuid - The favourite's UUID, as read from the URL.
     * @returns {?object} The GeoJSON feature, or null — the favourite was
     *   deleted, or belongs to someone else and was never in this
     *   collection. Both are ordinary, and neither is worth saying.
     */
    const favouriteFeatureByUuid = (uuid) => {
      const features = favouritesGeojsonCache && favouritesGeojsonCache.features;
      if (!Array.isArray(features)) return null;
      return features.find(
        (feature) => feature && feature.properties && feature.properties.uuid === uuid,
      ) || null;
    };

    /**
     * Read ``?favourite=`` and strip it from the address bar in one step.
     *
     * Consumed rather than merely read. The collection is refetched on
     * every ``snowdesk:favourites-changed``, so a parameter still sitting
     * in the URL would be a standing instruction — renaming a pin an hour
     * later would fly the map back to it. Stripping also means a reload or
     * a shared address bar carries the map the visitor is looking at, not
     * the link they arrived on.
     *
     * @returns {?string} The requested UUID, or null when there is none.
     */
    const consumeFavouriteDeepLink = () => {
      const params = new URLSearchParams(location.search);
      const uuid = params.get('favourite');
      if (!uuid) return null;
      params.delete('favourite');
      const query = params.toString();
      history.replaceState(
        null,
        '',
        location.pathname + (query ? `?${query}` : '') + location.hash,
      );
      return uuid;
    };

    /**
     * Honour a ``/map/?favourite=<uuid>`` arrival: fly to that favourite's
     * pin and open the popup a tap on it would have opened.
     *
     * Silent in every case it cannot satisfy. An ineligible visitor reaches
     * this URL only by guessing it (the link is rendered for the owner
     * alone), and a UUID matching nothing is a favourite that has been
     * deleted — telling either of them anything is either a leak or a
     * puzzle, so the map just opens as it otherwise would.
     *
     * @returns {void}
     */
    const openFavouriteDeepLink = () => {
      const uuid = consumeFavouriteDeepLink();
      if (!uuid || !FAVOURITES_ELIGIBLE) return;

      // Someone who followed a link to one specific pin means to see that
      // pin, so an overlay left switched off is switched back on rather
      // than flown to invisibly. This goes through ``showPanelOverlay`` —
      // the same call the panel's own switch makes — because that persists
      // the preference and drives the lazy load. Flipping ``visibility`` by
      // hand would paint layers whose stored preference still said "off",
      // and the next thing to re-read that preference (SNOW-493's
      // overlay-load path, a basemap swap) would switch them off again
      // under a visitor who never touched the switch.
      if (!overlayState.favourites) showPanelOverlay('favourites');

      const flyToFavourite = () => {
        const feature = favouriteFeatureByUuid(uuid);
        if (!feature) return false;
        // Fly first, open second: the popup anchors to the coordinate and
        // rides the camera in, so the pin is never briefly popup-less.
        map.flyTo({
          center: feature.geometry.coordinates,
          zoom: FAVOURITE_DEEP_LINK_ZOOM,
        });
        activateFavourite(feature);
        return true;
      };

      // The layer may already be installed by the time this runs — the boot
      // restore is async and this sits late in the ``load`` handler, so
      // both orders happen.
      if (flyToFavourite()) return;

      // Otherwise wait for the install, exactly once. There is no
      // "favourites installed" event, and ``sourcedata`` is what the source
      // itself emits whichever branch of the boot load installed it — the
      // network fetch or the offline cache read-back — which is what makes
      // the deep link work on an offline boot. Following the boot ratings
      // paint above, the loaded check reads ``map.isSourceLoaded`` rather
      // than the event's own flag.
      //
      // It unbinds on the first favourites load it sees, found or not: a
      // UUID absent from that collection will not appear in a later one,
      // and a listener left bound would re-fire on every subsequent
      // ``snowdesk:favourites-changed`` setData.
      const onFavouritesSourceData = (e) => {
        if (e.sourceId !== 'favourites' || !map.isSourceLoaded('favourites')) return;
        map.off('sourcedata', onFavouritesSourceData);
        flyToFavourite();
      };
      map.on('sourcedata', onFavouritesSourceData);
    };

    openFavouriteDeepLink();

    /**
     * Read ``?resort=`` and strip it from the address bar in one step.
     *
     * SNOW-807: the resort page's "reports near here" link is
     * ``/?panel=reports&resort=<slug>`` — the sheet-level ``?panel=`` opens
     * the reports sheet (SNOW-803), and this flies the camera to the resort.
     * Consumed for the same reasons ``consumeFavouriteDeepLink`` gives.
     *
     * @returns {?string} The requested resort slug, or null when none.
     */
    const consumeResortDeepLink = () => {
      const params = new URLSearchParams(location.search);
      const slug = params.get('resort');
      if (!slug) return null;
      params.delete('resort');
      const query = params.toString();
      history.replaceState(
        null,
        '',
        location.pathname + (query ? `?${query}` : '') + location.hash,
      );
      return slug;
    };

    /**
     * Honour a ``/?resort=<slug>`` arrival: fly to that resort's pin.
     *
     * The resort is resolved by identity in the already-loaded ``resorts``
     * source — its feature ``id`` IS the slug since SNOW-796 — the same
     * shape as the favourite deep link above, so no new camera mechanism
     * and no raw lat/lon in the URL. A slug matching nothing (a renamed
     * or deleted resort) is silent: the map opens as it otherwise would.
     *
     * @returns {void}
     */
    const openResortDeepLink = () => {
      const slug = consumeResortDeepLink();
      if (!slug) return;

      // Someone who followed a link to one resort means to see its pin, so
      // an overlay left switched off is switched back on through the
      // panel's own path — the same reasoning as the favourite deep link.
      if (!overlayState.resorts) showPanelOverlay('resorts');

      const flyToResort = () => {
        const features = resortsGeojsonCache && resortsGeojsonCache.features;
        if (!Array.isArray(features)) return false;
        const feature = features.find(
          (f) => f && f.properties && f.properties.id === slug,
        );
        if (!feature) return false;
        map.flyTo({
          center: feature.geometry.coordinates,
          zoom: FAVOURITE_DEEP_LINK_ZOOM,
        });
        return true;
      };

      if (flyToResort()) return;

      // Otherwise wait for the resorts source to install, exactly once —
      // ``sourcedata`` is what the source emits whichever path loaded it.
      const onResortsSourceData = (e) => {
        if (e.sourceId !== 'resorts' || !map.isSourceLoaded('resorts')) return;
        map.off('sourcedata', onResortsSourceData);
        flyToResort();
      };
      map.on('sourcedata', onResortsSourceData);
    };

    openResortDeepLink();

    /**
     * Read ``?route_share=`` and strip it from the address bar in one step.
     *
     * SNOW-764. Consumed rather than merely read, for the same reasons
     * ``consumeFavouriteDeepLink`` above gives: the routes collection is
     * refetched on every ``snowdesk:routes-changed``, so a parameter left
     * in the URL would be a standing instruction that flew the map back to
     * the shared track every time the user saved or deleted anything. And
     * once the route is claimed, an address bar still naming the share
     * would be a link to a row the user now owns.
     *
     * The TOKEN leaves the URL; it does not leave the session. The
     * redirect that brought the visitor here already recorded it
     * (apps.routes.views.route_share_redirect), which is what keeps the
     * Save control alive across a sign-in round trip after this strip.
     *
     * @returns {?string} The share token, or null when there is none.
     */
    const consumeRouteShareDeepLink = () => {
      const params = new URLSearchParams(location.search);
      const token = params.get('route_share');
      if (!token) return null;
      params.delete('route_share');
      const query = params.toString();
      history.replaceState(
        null,
        '',
        location.pathname + (query ? `?${query}` : '') + location.hash,
      );
      return token;
    };

    /**
     * Honour a ``/?route_share=<token>`` arrival: frame the shared route
     * and open the popup a tap on its line would have opened.
     *
     * The twin of ``openFavouriteDeepLink``, and deliberately the same
     * shape — including the wait-for-``sourcedata`` dance, which is what
     * makes it work whichever branch of the boot load installs the layer
     * (the network fetch or the offline cache read-back).
     *
     * It calls ``activateRoute`` directly rather than synthesising a tap.
     * That function takes the popup's anchor as an argument because a LINE
     * has no single natural anchor and the tap point is the honest one —
     * but a deep-link arrival HAS no tap, so the bounds centre is used
     * instead. The objection recorded on that function is specifically
     * about overriding a real touch point with a computed one, which does
     * not arise when there was no touch.
     *
     * Silent in every case it cannot satisfy, exactly as the favourite
     * deep link is: an expired share never reaches here (the redirect
     * answers 410 before the map loads), and a token that resolves to no
     * feature means the layer answered without it. Saying anything about
     * either would be a puzzle rather than an explanation.
     *
     * @returns {void}
     */
    const openRouteShareDeepLink = () => {
      const token = consumeRouteShareDeepLink();
      if (!token || !ROUTES_ELIGIBLE) return;

      // Somebody who followed a link to one specific route means to see
      // it, so an overlay left switched off is switched back on rather
      // than flown to invisibly. Through ``showPanelOverlay`` — the call
      // the panel's own switch makes — because that persists the
      // preference and drives the lazy load; flipping ``visibility`` by
      // hand would paint layers whose stored preference still said "off".
      if (!overlayState.routes) showPanelOverlay('routes');

      const flyToShare = () => {
        const features = (routesGeojsonCache && routesGeojsonCache.features) || [];
        const feature = features.find(
          (f) => f && f.properties && f.properties.token === token,
        );
        if (!feature) return false;
        const bounds = readFeatureJson(feature.properties.bounds);
        if (!Array.isArray(bounds) || bounds.length !== 4) return false;
        // activateRoute fits the camera to these same bounds; the anchor
        // is their centre because there is no tap point to honour.
        activateRoute(feature, [
          (bounds[0] + bounds[2]) / 2,
          (bounds[1] + bounds[3]) / 2,
        ]);
        return true;
      };

      // The layer may already be installed by the time this runs — the
      // boot restore is async and this sits late in the ``load`` handler,
      // so both orders happen.
      if (flyToShare()) return;

      // Otherwise wait for the install, exactly once. It unbinds on the
      // first routes load it sees, found or not: a token absent from that
      // collection will not appear in a later one, and a listener left
      // bound would re-fire on every subsequent ``snowdesk:routes-changed``
      // setData — flying the map back to a route the user has since
      // claimed.
      //
      // It is ALSO unbound by the routes load's own failure branch (see
      // ``dropRouteShareSourceDataListener``): an offline load with nothing
      // cached installs no layer, so the event this waits for never arrives
      // and nothing else would ever release it.
      const onRoutesSourceData = (e) => {
        if (e.sourceId !== 'routes' || !map.isSourceLoaded('routes')) return;
        map.off('sourcedata', onRoutesSourceData);
        routeShareSourceDataListener = null;
        flyToShare();
      };
      routeShareSourceDataListener = onRoutesSourceData;
      map.on('sourcedata', onRoutesSourceData);
    };

    // NOT called here, unlike openFavouriteDeepLink() directly above.
    // ``activateRoute`` and ``readFeatureJson`` are ``const`` arrow
    // functions declared further down this same block, so calling from
    // here would hit their temporal dead zone and throw
    // ("Cannot access 'activateRoute' before initialization") — taking the
    // whole load handler with it. The invocation sits immediately after
    // ``activateMarker``, which is the first point at which everything it
    // reaches exists. Nothing about the timing changes: it is the same
    // synchronous run of the same handler.

    // SNOW-419/SNOW-472: tapping an unclustered community-report pin opens a
    // small popup with the observation type and a relative time — built via
    // DOM methods (not setHTML) since these values, though server-controlled,
    // don't need string-interpolated HTML.
    //
    // The popup is the field-observation panel's row, in a popup: a bold
    // label over "<region> · <age>", the same two lines in the same order.
    // The region used to be left out here on the grounds that the pin's own
    // position says where the report is — true, but it made the two surfaces
    // read differently for one report, and the panel row names the region
    // anyway. One format, whichever way the user reaches the report.
    //
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
      // is baked at fetch time, not this text. observed_at is the instant as
      // recorded, so this reads identically to the same report's row in the
      // field-observation panel.
      //
      // The age goes in a `<time data-relative-time>`, which is
      // static/js/relative_time.js's hook: a popup left open while the user
      // reads the map keeps its age current instead of freezing at the
      // moment it was tapped. Same element, same module, as the panel row.
      const relative = formatRelativeTime(props.observed_at);
      if (relative || props.region_name) {
        const metaEl = document.createElement('div');
        metaEl.className = 'community-report-popup__meta';
        if (props.region_name) {
          metaEl.append(props.region_name);
          if (relative) metaEl.append(' · ');
        }
        if (relative) {
          const timeEl = document.createElement('time');
          timeEl.setAttribute('datetime', props.observed_at);
          timeEl.setAttribute('data-relative-time', '');
          timeEl.textContent = relative;
          metaEl.appendChild(timeEl);
        }
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

    /**
     * Read a feature property that may have arrived as JSON text.
     *
     * MapLibre serialises every non-scalar feature property when it hands a
     * feature back from ``queryRenderedFeatures``, so ``properties.bounds``
     * is the array the server sent on some paths and its JSON text on
     * others. Both are accepted; anything unparseable comes back null so
     * the caller can skip that step rather than throw and take the whole
     * tap down with it.
     *
     * @param {*} value - A raw feature property value.
     * @returns {*} The parsed value, or null if it could not be read.
     */
    const readFeatureJson = (value) => {
      if (typeof value !== 'string') return value == null ? null : value;
      try {
        return JSON.parse(value);
      } catch (_err) {
        return null;
      }
    };

    /**
     * Draw the route's elevation profile into its popup, if it has one.
     *
     * The chart is a picture of the SAME data the figures above it state:
     * every route coordinate carries its elevation as a third ordinate
     * (RFC 7946 allows it, MapLibre ignores it), straight from the GPX's
     * own `<ele>`. Nothing is fetched, so this works offline exactly as
     * the rest of the popup does.
     *
     * THE GEOMETRY COMES FROM THE CACHE, NOT FROM THE TAPPED FEATURE, and
     * that is the whole reason this takes a uuid rather than the feature
     * it is called beside. A click feature is whatever
     * `queryRenderedFeatures` returned, which is the TILE's copy of the
     * line: clipped at tile boundaries — a long route comes back as just
     * the piece the tap landed in — and simplified for the current zoom.
     * Drawing from it would give a profile of part of the route, or of a
     * coarser one, varying with where the user tapped and how far out
     * they were zoomed. `routesGeojsonCache` holds the whole line as the
     * server sent it, already in memory, and never varies.
     *
     * Silently draws nothing when the track has no elevation at all — a
     * GPX with no `<ele>` means "unknown", and an empty flat line at zero
     * would be the same lie the omitted ascent figure exists to avoid.
     * Returns the profile either way, so appendRouteCaption can caption a
     * chart that exists without having to re-read the geometry.
     *
     * SNOW-764: the key may be a uuid OR a share token. A pending route
     * carries no uuid at all — a non-owner must not be handed the
     * identifier the owner-scoped endpoints are addressed by — so the
     * cache lookup matches on either. It has to be the cache for a pending
     * route too, and for the same reason as an owned one: the tapped
     * feature is the tile's clipped, zoom-simplified copy, and a profile
     * drawn from it would be a profile of part of the track.
     *
     * @param {HTMLElement} container The popup body being built.
     * @param {string} key The route's uuid, or a pending share's token,
     *   from the feature properties.
     * @returns {object|null} The profile drawn, or null if none was.
     */
    const appendElevationProfile = (container, key) => {
      const core = self.pwaElevationProfileCore;
      if (!core || !key || !routesGeojsonCache) return null;

      const features = routesGeojsonCache.features || [];
      const cached = features.find(
        (f) =>
          f && f.properties && (f.properties.uuid === key || f.properties.token === key),
      );
      const coordinates = cached && cached.geometry && cached.geometry.coordinates;
      if (!Array.isArray(coordinates)) return null;

      const profile = core.readProfile(coordinates);
      const svg = core.createProfileSvg(profile, {
        label: MAP_STRINGS['route-profile-label'],
      });
      if (!svg) return null;
      container.appendChild(svg);
      return profile;
    };

    /**
     * Format a duration in seconds as hours and minutes, or null.
     *
     * Whole minutes: a tour is not read to the second, and rounding rather
     * than truncating keeps 59.6 minutes from reading as 59. The hours form
     * pads the minutes so "4h05m" cannot be misread as "4h5m"; the
     * minutes-only form does not, since there is nothing to align it to.
     *
     * @param {number} seconds Elapsed seconds, from the feature's duration_s.
     * @returns {string|null} The formatted span, or null if not a duration.
     */
    const formatDuration = (seconds) => {
      if (typeof seconds !== 'number' || !isFinite(seconds) || seconds <= 0) {
        return null;
      }
      const totalMinutes = Math.round(seconds / 60);
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes % 60;
      if (hours === 0) {
        return self.pwaStrings.interpolate(
          MAP_STRINGS['route-duration-minutes'],
          { minutes: String(minutes) },
        );
      }
      return self.pwaStrings.interpolate(MAP_STRINGS['route-duration-hours'], {
        hours: String(hours),
        minutes: String(minutes).padStart(2, '0'),
      });
    };

    /**
     * Append the caption line under the profile: elevation range, duration.
     *
     * SEPARATE FROM THE CHART ON PURPOSE (SNOW-750). The range caption used
     * to live inside appendElevationProfile, which tied it to a chart being
     * drawn — so a GPX carrying timing but no <ele> drew nothing and lost
     * its duration with it. The two facts are independent: either, both or
     * neither may be known, and the line renders whatever is.
     *
     * The range half is not decoration. The chart's y-axis is scaled to
     * this track's own highest and lowest point, so the curve's height
     * means nothing without the pair that bounds it.
     *
     * @param {HTMLElement} container The popup body being built.
     * @param {object|null} profile The drawn profile, or null if none was.
     * @param {number} durationSeconds The feature's duration_s.
     */
    const appendRouteCaption = (container, profile, durationSeconds) => {
      const parts = [];
      if (profile) {
        parts.push(
          self.pwaStrings.interpolate(MAP_STRINGS['route-elevation-range'], {
            low: String(Math.round(profile.minEle)),
            high: String(Math.round(profile.maxEle)),
          }),
        );
      }
      const duration = formatDuration(durationSeconds);
      if (duration) parts.push(duration);
      if (!parts.length) return;

      const caption = document.createElement('div');
      caption.className = 'text-xs text-text-3';
      caption.textContent = parts.join(' · ');
      container.appendChild(caption);
    };

    // The shared-route popup's Save control, in both its states. Design
    // tokens only — `bg-status-info-*` is the informational status pair
    // from @theme, the same one static/js/signin_cta.js uses for the four
    // panels' sign-in prompt, so the two read as one treatment. It is not
    // signin_cta.js itself because that builds a prompt-plus-link PAIR and
    // this is one control that is sometimes a button.
    const ROUTE_CLAIM_CTA_CLASS =
      'mt-2 block w-full cursor-pointer rounded-pill bg-status-info-bg ' +
      'px-4 py-2 text-center text-sm font-medium text-status-info-text ' +
      'disabled:cursor-not-allowed disabled:opacity-60';

    /**
     * The CSRF token for the claim POST.
     *
     * Read from the DOM at call time rather than captured: the token input
     * lives in the routes surface's own hidden upload form, which is
     * rendered on the page but is not this IIFE's. Django issues one token
     * per session and it is valid across every form on the page, so any of
     * them answers.
     *
     * @returns {string}
     */
    const routeCsrfToken = () => {
      const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
      return input ? input.value : '';
    };

    /**
     * Append the Save control to a pending (unclaimed) route's popup.
     *
     * SNOW-764. The popup is where a deep-link recipient arrives — the
     * link lands on the map with the camera already on the track — so the
     * action has to be here and not only in the routes panel, which they
     * would have to know to open.
     *
     * TWO STATES, AND NEITHER IS "NOTHING". A signed-in viewer gets a
     * button that posts the claim; a signed-out one gets a link to sign
     * in. The control is never hidden: a hidden control reads as a bug,
     * and the whole reason they are looking at this popup is that somebody
     * sent them the route.
     *
     * The claim itself is window.pwaShare's, so this and the panel's own
     * HTMX form reach the same endpoint the same way. On success it
     * announces `snowdesk:routes-changed` and closes the popup — the
     * claimed route is an owned route now, and the pending line it was
     * drawn as has to be replaced by the owned one, which the refetch that
     * event triggers does.
     *
     * @param {HTMLElement} container The popup body being built.
     * @param {string} token The share token, from the feature properties.
     * @returns {void}
     */
    const appendRouteClaimCta = (container, token) => {
      // NO "Shared with you" line above the control. It shipped with one
      // and it said nothing the control below it does not already say —
      // "Sign in to save this route" and "Save route" both state that this
      // is somebody else's route being offered. The row in the panel keeps
      // its own prefix, because a row sits in a list beside owned ones and
      // has only its actions to tell them apart; a popup has no such
      // neighbour and needs no such label.

      // Signed out: the way in, not a dead button. Same treatment the four
      // UGC panels give an ineligible visitor.
      if (!ROUTES_UPLOAD_ELIGIBLE) {
        if (!ROUTES_SIGNIN_URL) return;
        const link = document.createElement('a');
        link.href = ROUTES_SIGNIN_URL;
        link.className = ROUTE_CLAIM_CTA_CLASS;
        link.textContent = MAP_STRINGS['route-save-signin'];
        container.appendChild(link);
        return;
      }

      if (!ROUTE_CLAIM_URL_TEMPLATE || !token || !window.pwaShare) return;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = ROUTE_CLAIM_CTA_CLASS;
      button.textContent = MAP_STRINGS['route-save'];
      button.addEventListener('click', function () {
        // Disabled for the duration: the claim writes a row, and a second
        // tap while the first is in flight would write a second copy.
        button.disabled = true;
        window.pwaShare
          .claim(ROUTE_CLAIM_URL_TEMPLATE.replace('__TOKEN__', token), routeCsrfToken())
          .then(function () {
            window.pwaTelemetry?.emit('map.route.claimed', {});
            document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));
            closeDetailPopup();
          })
          .catch(function (resp) {
            button.disabled = false;
            const status = resp && resp.status;
            button.textContent =
              status === 409
                ? MAP_STRINGS['route-save-limit']
                : MAP_STRINGS['route-save-failed'];
          });
      });
      container.appendChild(button);
    };

    // SNOW-687: tapping a saved route frames the whole track and opens its
    // detail. Two halves, and the order matters: the fit runs first and the
    // popup anchors to the tap point, which MapLibre keeps pinned to its
    // lng/lat for the duration of the ease — so the popup travels with the
    // line rather than being left behind at a screen position.
    //
    // The anchor is passed in rather than derived. Every other member of
    // MARKER_EXCLUSION_LAYERS is a point with one natural anchor; a line
    // has none, and anchoring at (say) its midpoint would open the popup
    // somewhere the user did not touch, possibly off screen.
    //
    // The body is the panel row's own two lines, in the panel's own order
    // and format ("12.4 km · 850 m asc · 1100 m desc"), for the
    // reason activateCommunityReport gives above: one route should read the
    // same whichever surface it is reached from. Built with createElement,
    // never innerHTML — the name is user-supplied.
    //
    // The popup then adds what the panel row cannot: the elevation profile
    // itself. The row is includes/_ugc_panel_row.html, whose five-slot
    // anatomy is shared with favourites, observations and downloads — a
    // chart inside it would be a shape only one of the four panels has. The
    // popup is already this route's detail surface, so the picture goes
    // where the tap goes. See appendElevationProfile just above.
    const activateRoute = (feature, lngLat) => {
      const props = feature.properties || {};

      const bounds = readFeatureJson(props.bounds);
      if (Array.isArray(bounds) && bounds.length === 4) {
        // GeoJSON bbox [min_lon, min_lat, max_lon, max_lat] → MapLibre's
        // [[west, south], [east, north]]. Same padding/maxZoom/duration as
        // zoomToFeatureBounds, so a route and a region frame alike.
        map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], {
          padding: { top: 60, right: 40, bottom: 40, left: 40 },
          maxZoom: 10,
          duration: 400,
        });
      }

      const container = document.createElement('div');
      container.setAttribute('data-route-detail', '');

      const title = document.createElement('div');
      title.className = 'text-sm font-semibold text-text-1';
      title.textContent = props.name || MAP_STRINGS['route-untitled'];
      container.appendChild(title);

      const parts = [];
      if (typeof props.distance_m === 'number') {
        parts.push(self.pwaStrings.interpolate(MAP_STRINGS['route-distance'], {
          km: (props.distance_m / 1000).toFixed(1),
        }));
      }
      // ``ascent_m`` is null when the GPX carried no <ele> at all, and that
      // null is MEANINGFUL: Route's own docstring says "we don't know" and
      // "flat" are different facts, and rendering the second for the first
      // is a safety-relevant lie about a route somebody may be planning to
      // ski. So the segment is omitted entirely rather than shown as 0 m —
      // note the explicit null test, since 0 is a legitimate ascent.
      if (props.ascent_m != null) {
        parts.push(self.pwaStrings.interpolate(MAP_STRINGS['route-ascent'], {
          m: String(Math.round(props.ascent_m)),
        }));
      }
      // Descent gets the same null test and for the same reason. It sits
      // beside the ascent rather than replacing it: the two are not each
      // other's mirror (an out-and-back climbs and drops the same height,
      // a traverse does not), and they are never netted — see
      // Route.descent_m.
      if (props.descent_m != null) {
        parts.push(self.pwaStrings.interpolate(MAP_STRINGS['route-descent'], {
          m: String(Math.round(props.descent_m)),
        }));
      }
      if (parts.length) {
        const meta = document.createElement('div');
        meta.className = 'mt-0.5 text-xs text-text-2';
        meta.textContent = parts.join(' · ');
        container.appendChild(meta);
      }

      // uuid for an owned route, token for a pending one — see
      // appendElevationProfile. The two can never collide: a pending
      // feature carries no uuid and an owned one carries no token.
      const profile = appendElevationProfile(container, props.uuid || props.token);
      appendRouteCaption(container, profile, props.duration_s);

      if (props.pending) appendRouteClaimCta(container, props.token);

      // The already-registered 'map-detail-popup' exclusivity member, so a
      // route tap closes every other map overlay and needs no registration
      // of its own.
      mountDetailPopup(lngLat, { node: container });
    };

    // Dispatch a marker the exclusion zone claimed to its activation, by
    // layer. ``lngLat`` is the tap's own coordinate — only the route needs
    // it (a line has no single natural anchor), but it is passed
    // unconditionally rather than as a special case at the call site.
    const activateMarker = (feature, lngLat) => {
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
        case 'routes-line':
        case 'routes-line-pending':
          activateRoute(feature, lngLat);
          break;
      }
    };

    // SNOW-764: honour a ``/?route_share=<token>`` arrival. Declared far
    // above beside the favourite deep link it mirrors; invoked HERE because
    // everything it reaches — activateRoute, readFeatureJson — is declared
    // between the two points. See its own note for why.
    openRouteShareDeepLink();

    // SNOW-828: and a ``/?trip=`` / ``/?trip_share=`` one. Declared at IIFE
    // level beside installTripRouteLayers rather than up here, because
    // unlike its route-share sibling it reaches no popup machinery — but
    // invoked from the same place and for the same reason: inside ``load``
    // the style is ready to take the source it adds. NOT awaited, since it
    // fetches and nothing below may wait on a network round trip.
    openTripDeepLink();

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
        // SNOW-687: the tap's own lng/lat goes through too — the route
        // popup anchors there, having no single natural anchor of its own.
        activateMarker(marker, e.lngLat);
        return;
      }

      // SNOW-761: a weather symbol owns its tap the same way a marker does,
      // and is checked in the same place — before the region fill, so a
      // tap on a symbol does not also select the region under it. The
      // symbol IS the tap target: `icon-text-fit` is gone, so the hit box
      // is the label's own extent, which is what the user aimed at.
      //
      // `collapseToLowest` means the feature under the cursor may stand for
      // several stations. Its `short_id` is the one that survived the
      // collapse — the lowest — which is the reading the symbol is drawing,
      // so the sheet answers for the same place the map showed. The feed
      // carries the opaque short id, never the pk (SNOW-797).
      if (map.getLayer('weather-point')) {
        const weatherHit = map.queryRenderedFeatures(e.point, {
          layers: ['weather-point'],
        })[0];
        if (weatherHit) {
          window.pwaWeatherDetail?.open(
            weatherHit.properties.short_id, currentDisplayedDate,
          );
          return;
        }
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

    // SNOW-761: the weather symbol is tappable, so say so. Bound
    // unconditionally like the rest — MapLibre tolerates a handler on a
    // layer that does not exist yet and starts delivering when it does,
    // which is what the lazily-installed weather layer needs.
    map.on('mouseenter', 'weather-point', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'weather-point', () => { map.getCanvas().style.cursor = ''; });

    map.on('mouseenter', 'favourites-pin', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'favourites-pin', () => { map.getCanvas().style.cursor = ''; });

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
      // SNOW-812: a miss here means the region is not in the GeoJSON this
      // page loaded — usually because its country's feeds never arrived
      // (see the `country.load` line), which offline is entirely silent.
      // The map simply does not move, and there is nothing else on the
      // page that says why.
      window.pwaDebugLog?.record('map', 'hash.resolve', {
        regionID,
        found: !!feature,
        known: Object.keys(FEATURE_BY_REGION_ID).length,
      });
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

    // SNOW-697: a region contributes its own row plus one per resort, so
    // this pushes a list rather than a single entry. RESORTS_BY_REGION is
    // already populated by the time this first runs (the boot fetch above
    // awaits it) and is not country-scoped, so a country that lazy-loads
    // later finds its resorts waiting. When that fetch failed the map
    // degrades to region-only rows — see its .catch().
    const indexRegion = (props) => {
      const regionID = props && props.regionID;
      if (!regionID || INDEXED_REGIONS.has(regionID)) return;
      const entries = searchCore.buildEntries(props, RESORTS_BY_REGION[regionID] || []);
      if (!entries.length) return;
      INDEXED_REGIONS.add(regionID);
      SEARCH_INDEX.push(...entries);
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
        // SNOW-697: rows are regions OR resorts. The badge carries the EAWS
        // region ID either way — for a resort that is the region it sits
        // in, which is both what the row selects and the bulletin the user
        // is on their way to, so it is context rather than clutter.
        li.dataset.resultType = r.type;

        const text = document.createElement('div');
        text.className = 'search-result-text';
        const primary = document.createElement('div');
        primary.className = 'search-result-primary';
        // A pin glyph marks the resort rows. Sighted users get the
        // region/resort distinction from this plus the secondary line;
        // the visually-hidden word below is what carries it to a screen
        // reader, which cannot see either.
        if (r.type === 'resort') {
          // Stroked in currentColor rather than filled with a punched-out
          // centre: a filled pin needs a literal colour for the hole, and
          // the row behind it is --color-card, which is not the same colour
          // in both themes. Stroke-only inherits and is right in either.
          // Same idiom as the search toggle's own icon in _map_embed.html.
          const pin = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
          pin.setAttribute('class', 'search-result-pin');
          pin.setAttribute('viewBox', '0 0 24 24');
          pin.setAttribute('fill', 'none');
          pin.setAttribute('stroke', 'currentColor');
          pin.setAttribute('stroke-width', '2');
          pin.setAttribute('aria-hidden', 'true');
          const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          path.setAttribute('d', 'M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z');
          const hole = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          hole.setAttribute('cx', '12');
          hole.setAttribute('cy', '10');
          hole.setAttribute('r', '2.5');
          pin.append(path, hole);
          primary.append(pin);
        }
        const typeLabel = document.createElement('span');
        typeLabel.className = 'search-result-type';
        // Trailing space inside the hidden span, not a text node between
        // the two: the span is clipped to a 1px box, so a separator
        // outside it can collapse and leave a screen reader reading
        // "ResortVerbier" as one word. Invisible either way.
        typeLabel.textContent = (r.type === 'resort'
          ? MAP_STRINGS['search-type-resort']
          : MAP_STRINGS['search-type-region']) + ' ';
        primary.append(typeLabel, document.createTextNode(r.primary));

        const secondary = document.createElement('div');
        secondary.className = 'search-result-secondary';
        // Region row: the parent L2 sub-region name, blank where the
        // fixture has no descriptive L2 (AT/IT). Resort row: the name of
        // the region it belongs to, so resorts sharing a first word stay
        // told apart.
        secondary.textContent = r.secondary;
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
        badge.append(document.createTextNode(r.regionID));

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
    // outline here, and the whole choropleth via repaintAfterStyleSwap.

    /**
     * Repaint every region's rating after a style swap dropped the source.
     *
     * Reinstalling the 'regions' source wipes feature-state, and rating IS
     * feature-state — so without this the entire choropleth falls through
     * the paint expression's `match` to `no_rating` grey and the map goes
     * blank the moment the user picks a different basemap.
     *
     * The date this reads used to be `readUrlDateParam()` alone, which was
     * null whenever the map was showing a date the scrubber had committed
     * silently — the ordinary way to arrive on the page back then — so the
     * bug this fixes was not an edge case, it was every visitor who had not
     * deep-linked to a date and then changed basemap.
     *
     * The precedence between the committed date and `?d=` lives in
     * `choropleth_core.js` so it can be unit-tested; this function is the
     * MapLibre wiring around it.
     *
     * SNOW-660: null now means what it says — no day has been asked for —
     * and the answer is to paint NOTHING. The swap has already wiped
     * feature-state, so returning here leaves a genuinely uncoloured map,
     * which is the same map the visitor was looking at before they changed
     * basemap. The boot frame survives only as the offline fallback below,
     * where it is the requested day's frame and so cannot invent a day.
     */
    const repaintAfterStyleSwap = () => {
      const dateKey = self.pwaChoroplethCore.repaintDateForStyleSwap(
        currentDisplayedDate, readDisplayDate(),
      );
      if (!dateKey) return;
      getSeasonRatings()
        .then((ratings) => repaintRegionsForDate(dateKey, ratings))
        .catch(() => {
          // Offline or a failed feed — the boot frame is the best colours
          // available for the day that WAS asked for, and is strictly better
          // than leaving the map grey.
          paintBootRatings();
        });
    };

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
      // an overlay key. 'downloaded' is deliberately absent (SNOW-645
      // review — see overlayState's own declaration): it is not a key of
      // overlayState any more, and installRegionsLayers reads
      // downloadedOverlayVisible instead, which this handler must not touch
      // — a basemap swap must not silently close the downloads overlay out
      // from under an open "Manage downloads" sheet.
      for (const key of ['l1', 'l2', 'resorts', 'community_reports', 'weather', 'routes', 'slope']) {
        overlayState[key] = readBoolStorage(OVERLAY_STORAGE_KEY[key], false);
      }
      // l4 and bulletins are re-seeded before any install fn runs: the fill's
      // layout is derived from both, and the bulletin boundary's visibility
      // from bulletins alone rather than a key of its own.
      overlayState.l4 = readBoolStorage(OVERLAY_STORAGE_KEY.l4, true);
      // SNOW-656: ``setPreference``, not ``choose`` — a basemap swap is not a
      // click, and must leave the downloads suppression (and the downloads
      // overlay itself, per the note above) exactly as it found them.
      overlayState.bulletins = BULLETINS_CORE.seedFromLegacy(
        readStorage(OVERLAY_STORAGE_KEY.bulletins),
        readStorage(OVERLAY_STORAGE_KEY.l4),
        BULLETINS_CORE.DEFAULT_STEP,
      );
      bulletinsVisibility = BULLETINS_CORE.setPreference(
        bulletinsVisibility, overlayState.bulletins,
      );
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
      // SNOW-691: the raster went with the style too. Re-added after the
      // regions here rather than before them — installSlopeLayer resolves
      // its own `beforeId` against 'regions-fill', so it slots underneath
      // either way and neither call site has to know the other's order.
      installSlopeLayer();
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
      // SNOW-761: the weather symbols went with the style too, along with
      // every registered icon image. installWeatherLayer re-registers them
      // from the module-level decode cache, so this costs no network.
      if (overlayLoaded.weather) {
        installWeatherLayer(weatherGeojsonCache);
      }
      // SNOW-687: same story for the route lines — re-install from the
      // last-fetched cache rather than re-requesting an endpoint whose
      // answer has not changed (and which a basemap swap made offline
      // cannot reach at all).
      if (overlayLoaded.routes) {
        installRoutesLayer(routesGeojsonCache);
      }
      // SNOW-828: the trip's line went with the style too. Gated on the
      // cache alone and not on an ``overlayLoaded`` key, because a trip is
      // not an overlay — the cache is null for every visit that did not
      // arrive on a trip link, which is what makes this a no-op for almost
      // everyone. Re-installed from the cache rather than re-fetched: the
      // parameter naming the trip was stripped on arrival, so there is no
      // longer anything in the URL to fetch it from.
      if (tripRouteGeojsonCache) {
        installTripRouteLayers(tripRouteGeojsonCache);
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

      // SNOW-658: the four roundels' "my overlay is on the map" state has to
      // be repainted here or a basemap swap leaves it stating the pre-swap
      // answer for the rest of the session — the path a boot-only
      // implementation passes without.
      //
      // SNOW-658 review: announced AFTER the re-installs above, not beside
      // the overlayState re-seed at the top of this handler. setStyle has
      // torn every app layer off the map by then, so a ring painted at that
      // point would read the empty style and report "not shown" for an
      // overlay that is about to be re-installed a few lines later — now
      // that the bridges answer from paint rather than from the preference
      // this handler had just re-read.
      announceOverlayVisibility();

      if (selectedId !== null) {
        map.setFeatureState(
          { source: 'regions', id: selectedId },
          { selected: true },
        );
      }
      repaintAfterStyleSwap();

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
      repaintAfterStyleSwap();
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
