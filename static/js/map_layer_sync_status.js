/*
 * static/js/map_layer_sync_status.js — Sync-status dots for the map's
 * layers popover (SNOW-505).
 *
 * The layers popover (#basemap-menu in _map_embed.html) lists everything
 * the PWA can cache for offline use — the bulletin providers, the boundary
 * tiers (L1/L2/L4), resorts, weather and the active basemap — but
 * previously gave no signal of which of those are actually available
 * offline. This module is the read side of the existing "Cache this area
 * for offline" control (SNOW-492): a small two-state dot beside each
 * overlay row (cached = green / not cached = red), plus one honest
 * "partial" indicator for the basemap.
 *
 * Deliberately client-side-only — no new service-worker plumbing. The
 * probes below inspect Cache Storage and IndexedDB directly and are
 * re-run each time the popover opens (static/js/map.js's
 * basemapPickerInit calls ``refresh()`` from its ``setMenuOpen``), and on
 * every ``snowdesk:connectivity-changed`` (broadcast by pwa_offline.js), so
 * the menu reacts the instant the device goes offline/online rather than
 * only on the next popover open.
 *
 * Offline gating (offline-integrity)
 * ----------------------------------
 * The dots don't just advise — while the app is not using the network they
 * GATE interaction. A resource that isn't cached can't be loaded offline,
 * so its row gets the red
 * ``unavailable-offline`` dot AND is disabled (``aria-disabled``, honoured
 * by the picker's click handler and dimmed by map.css). Basemaps are gated
 * too — each basemap row carries its own dot — with one exception: the
 * ACTIVE basemap's row is never disabled, because you can't be stranded on
 * a map you can't leave. SNOW-722: that exception applies to the ROW ONLY.
 * The active basemap's dot reports its real cache state like every other;
 * it used to be forced green, which is how the layers menu could promise
 * "available offline" for a basemap with nothing stored. Disabling a row
 * never hides a layer already on the map; it only locks the menu control
 * ("keep shown, lock the toggle"). Online, an uncached row stays the grey
 * advisory "view online first" and fully interactive.
 *
 * What a basemap dot MEANS is a separate question from the gating, and
 * SNOW-722 gives it THREE answers rather than two (``_applyBasemapState``,
 * which is why basemap rows don't go through ``_applyState``):
 *
 *   - downloaded area coverage for that basemap → green "available
 *     offline";
 *   - style cached but nothing downloaded → grey "partly cached — may not
 *     load everywhere". Such a basemap genuinely draws wherever tiles were
 *     picked up online, so the row stays SELECTABLE offline: an advisory,
 *     not a gate. Refusing it would take away a map that works;
 *   - neither → the existing grey "view online first" online, red
 *     ``unavailable-offline`` (and disabled) offline.
 *
 * Those answers do NOT depend on connectivity — only the red state and the
 * disabling do. The middle state is what this module used to get wrong,
 * and getting it wrong ONLINE is what mattered: a style JSON is cached by
 * merely glancing at a basemap in the picker (sw.js's
 * ``_basemapStaleWhileRevalidate``), so style-presence alone painted a
 * green "available offline" dot for a basemap that offline resolves to a
 * style with essentially no tiles — a blank map. The user who checks the
 * menu at home before a flight is precisely the one who could still fix it
 * by downloading the area, and precisely the one the green dot lied to.
 *
 * Row → resource map (the single source of truth driving every probe):
 *
 *   country.<key>        — (SNOW-524) the four ``?country=<code>``-scoped
 *                          feeds a country load fetches: L1, L2, L4 and
 *                          ratings. Green only when ALL four are cached for
 *                          EVERY code the row switches (SNOW-658: a row is a
 *                          bulletin provider now, and ALBINA covers AT and IT
 *                          — its ``data-country-codes`` carries both),
 *                          Switzerland included — the first page load is just
 *                          "toggle CH on from blank", so there is no
 *                          default-country special case. Basemap tiles
 *                          are NOT part of this signal — they have their own
 *                          rows and their own per-micro-region download flow
 *                          (SNOW-521) — so the label says "region data
 *                          available offline", not a bare "available
 *                          offline".
 *   l1, l2, l4, resorts  — same-origin GeoJSON feeds cached by sw.js's
 *                          STATIC_PATHS shell cache. Probed via the
 *                          GLOBAL ``caches.match()`` (searches every
 *                          cache, so nothing here hardcodes the
 *                          versioned CACHE_VERSION shell-cache name).
 *                          l1/l2/l4 are ``?country=``-scoped, and the SW
 *                          caches per full URL, so they are probed
 *                          EXACTLY, once per enabled country, and go green
 *                          only when cached for every country switched on
 *                          — otherwise a tier dot could sit green above a
 *                          red country row. resorts takes no country param
 *                          and keeps the single ``ignoreSearch`` probe.
 *   weather              — an IndexedDB ``data:map_overlays`` row written by
 *                          map_overlay_offline_cache.js. A truthy row
 *                          carrying ``.geojson`` counts as cached. (SNOW-658:
 *                          ``favourites`` and ``community_reports`` were
 *                          probed the same way until their rows left this
 *                          menu for their own panels — see OVERLAY_RESOURCES
 *                          below.)
 *   basemap (one per row)— TWO signals, resolved together (SNOW-722).
 *                          Real downloaded area coverage for that basemap
 *                          — ``window.pwaBasemapDownloads.areas()``, read
 *                          ONCE per pass and matched on each area's
 *                          ``basemapKey`` — is the green claim. Whether
 *                          the row's own style URL is in any cache
 *                          (``_probeUrlCached``) is the weaker second
 *                          signal, and on its own means only "partly
 *                          cached". With the downloads module absent
 *                          there is no coverage to read, so the style
 *                          signal stands alone exactly as it used to.
 *
 * Every probe is wrapped so a throw resolves to "uncached" —
 * ``refresh()`` must never reject, since it runs from a UI event handler
 * with no caller-side error handling.
 *
 * Live update (SNOW-505 iteration): ``refresh()`` re-probes real cache
 * state and runs on every popover open. ``markCached(key)`` is the
 * optimistic real-time counterpart — static/js/map.js calls it the moment
 * a lazy overlay tier's toggle-on load succeeds, flipping that row's dot
 * green immediately so the user sees the toggle action populate the offline
 * cache, rather than only discovering it on the next popover open. It is
 * deliberately optimistic: a successful online load routes the GeoJSON
 * through the SW's STATIC_PATHS stale-while-revalidate (or writes the
 * favourites/community_reports IDB overlay row), so the resource is — to
 * all practical intents — now cached; the next ``refresh()`` re-verifies
 * against real cache state and self-corrects in the rare case a background
 * ``cache.put`` didn't land. ``markCached`` no-ops for any key absent from
 * ``OVERLAY_RESOURCES``.
 *
 * Not listed: the bulletin-boundary layer (internal key ``l3``). It has no
 * layers-menu row — SNOW-521 removed it, and since PR #506 the boundary
 * simply follows L4's visibility rather than carrying a toggle of its own —
 * so there is nothing here to paint. Its responses are still cached for
 * settled dates by sw.js (SNOW-526); that state is deliberately not
 * surfaced in the menu (SNOW-532), because the boundary is a companion
 * outline with no control and no user action attached to its cache state.
 */

(function () {
  'use strict';

  const IDB_STORE = 'data:map_overlays';

  // SNOW-620: every label below is server-translated into the strings
  // template _map_embed.html renders, and read back here. The literals are
  // the English fallback for when that partial is absent — see
  // static/js/i18n_strings.js.
  //
  // The three groups (layer / basemap / country) keep separate keys even
  // where their English coincides. gettext dedupes identical msgids, so the
  // repetition costs nothing in the catalogue, and it leaves each row free
  // to diverge — in a locale or in a later copy change — without having to
  // be untangled first.
  const STRINGS = self.pwaStrings.read('map-sync-status-strings-template', {
    cached: 'Available offline',
    uncached: 'Not cached — view online first',
    // Offline + uncached: genuinely unavailable *right now* (red dot,
    // disabled row) — distinct from the grey advisory "view online first"
    // (which only applies while online, when viewing online is an option).
    'offline-blocked': 'Unavailable offline — not cached',
    'basemap-cached': 'Available offline',
    'basemap-uncached': 'Not cached — view online first',
    'basemap-offline-blocked': 'Unavailable offline — switch back online to load',
    // SNOW-722: offline, the honest middle state — the style is cached from
    // browsing, so this basemap draws where its tiles happen to be cached
    // and blank where they aren't. Neither "available offline" nor
    // "unavailable".
    'basemap-partial': 'Partly cached — may not load everywhere',
    // SNOW-524: country rows claim only that the country's REGION DATA is
    // cached — basemap tiles are a separate row with a separate download
    // flow (SNOW-521), so the label deliberately doesn't say "available
    // offline" unqualified.
    'country-cached': 'Region data available offline',
    'country-uncached': 'Not cached — view online first',
    'country-offline-blocked': 'Unavailable offline — not cached',
    // SNOW-524: mid-fetch — the data is on its way into the offline cache.
    syncing: 'Caching for offline use…',
  });

  const CACHED_LABEL = STRINGS.cached;
  const UNCACHED_LABEL = STRINGS.uncached;
  const OFFLINE_BLOCKED_LABEL = STRINGS['offline-blocked'];
  const BASEMAP_CACHED_LABEL = STRINGS['basemap-cached'];
  const BASEMAP_UNCACHED_LABEL = STRINGS['basemap-uncached'];
  const BASEMAP_OFFLINE_BLOCKED_LABEL = STRINGS['basemap-offline-blocked'];
  const BASEMAP_PARTIAL_LABEL = STRINGS['basemap-partial'];
  const COUNTRY_CACHED_LABEL = STRINGS['country-cached'];
  const COUNTRY_UNCACHED_LABEL = STRINGS['country-uncached'];
  const COUNTRY_OFFLINE_BLOCKED_LABEL = STRINGS['country-offline-blocked'];
  const SYNCING_LABEL = STRINGS.syncing;

  // Marker set on any menu row this module disabled for the offline+uncached
  // case, so the reverse transition only re-enables what it disabled (never
  // a row disabled for another reason). Mirrors pwa_offline.js's
  // ``data-was-disabled-offline`` idiom, namespaced to this module.
  const DISABLED_MARKER = 'data-sync-disabled-offline';

  // SNOW-573: the one OTHER module that disables a row in this menu —
  // map.js, while the scrubbed date sits outside the weather overlay's
  // stored forecast window. Read (never written) here, so ``_setRowDisabled``
  // can honour a disable it does not own. Kept in sync with
  // ``WEATHER_ROW_DISABLED_MARKER`` in map.js by name only; a mismatch fails
  // the round-trip test in tests/js/test_map_layer_sync_status.js.
  const WEATHER_DISABLED_MARKER = 'data-weather-disabled-out-of-window';

  // The core row→resource constant. Keys are the overlay rows'
  // ``data-overlay-key`` values; the basemap indicator and (SNOW-524) the
  // country rows are handled separately — the country rows' resource set is
  // per-code, so it lives in COUNTRY_FEED_PATHS below rather than here. The
  // Options rows (autozoom, cache-now) are deliberately absent from both, so
  // they never get a dot.
  const OVERLAY_RESOURCES = Object.freeze({
    l1: Object.freeze({
      kind: 'geojson',
      path: '/api/major-regions.geojson',
      countryScoped: true,
    }),
    l2: Object.freeze({
      kind: 'geojson',
      path: '/api/sub-regions.geojson',
      countryScoped: true,
    }),
    l4: Object.freeze({
      kind: 'geojson',
      path: '/api/regions.geojson',
      countryScoped: true,
    }),
    // SNOW-656: a ``bulletins`` entry lived here while the bulletin-fill
    // control was a row in this menu. The control moved to the map canvas
    // (``.map-fill-steps``, beside the scrubbed date), so there is no row to
    // hang a dot on and no row for the offline gate to disable.
    //
    // Nothing is lost from the dashboard: the feed it reported
    // (``/api/ratings/``) is one of the four COUNTRY_FEED_PATHS below, so a
    // country whose ratings are missing already shows red on its own row —
    // and the geometry the choropleth paints onto is the ``l4`` row's answer,
    // unchanged. This menu's invariant is about the rows it lists; Bulletins
    // is no longer one of them.
    // Not country-scoped: /api/resorts.geojson takes no ``?country=`` param,
    // it's one payload for every country.
    resorts: Object.freeze({ kind: 'geojson', path: '/api/resorts.geojson' }),
    // SNOW-658: the ``favourites`` and ``community_reports`` entries lived
    // here while those overlays were rows in this menu. Both moved into the
    // panel their own roundel opens (favourites.js / report.js), so there is
    // no row to hang a dot on — the same call SNOW-645 made for the
    // downloaded-areas row: drop the dot rather than relocate it. This menu's
    // invariant is about the rows it lists, and neither is one of them.
    //
    // ``markCached('favourites')`` is still called by map.js's lazy-load path.
    // It no-ops now — the membership check in ``_markCachedNow`` is the
    // allowlist, and ``_overlayDot`` returns null for a row that isn't there.
    // SNOW-573: the map weather layer's forecast payload. `idb`, not
    // `geojson` — its endpoint is flag-gated and public, but the payload
    // is a mutable forecast, not static reference data suited to sw.js's
    // STATIC_PATHS shell cache (which never expires). The write-through
    // IndexedDB row (window.pwaMapOverlayCache, same posture as
    // community_reports) is self-correcting on read-back: a stale cached
    // payload simply stops drawing anything as scrubbed dates roll past
    // its forecast window, rather than needing an explicit staleness check.
    weather: Object.freeze({ kind: 'idb', key: 'weather' }),
    // SNOW-645: the "Available offline" row that lived here (``downloaded:
    // {kind: 'pinned-tiles'}``, probed by the now-deleted
    // ``_probeAnyPinnedTile``) is gone — its dot went permanently grey and
    // unclickable on a basemap switch, because the probe was inherently
    // per-ACTIVE-template with no way to say so from the row. The overlay
    // it reported on moved into the "Manage downloads" sheet, bound to the
    // sheet being open rather than a togglable layer — see
    // map_downloads_manager.js and window.pwaDownloadedOverlay in map.js.
  });

  // SNOW-524: the four feeds a country load fetches (``ensureCountryLoaded`` in
  // static/js/map.js). All four are ``?country=<code>``-scoped and all four are
  // in sw.js's STATIC_PATHS, so all four are cached per country — a country is
  // offline-ready only when every one of them is present.
  //
  // This holds for EVERY country including Switzerland: there is no
  // default-country special case. The first page load is just "toggle CH on
  // from blank", so boot runs the same country load and ends in the same
  // four-feeds-cached state as any country the user switches on later.
  const COUNTRY_FEED_PATHS = Object.freeze([
    '/api/major-regions.geojson',
    '/api/sub-regions.geojson',
    '/api/regions.geojson',
    '/api/ratings/',
  ]);

  const COUNTRY_KEY_PREFIX = 'country.';

  // SNOW-524: the country-scoped tiers a country toggle also populates —
  // ``ensureCountryLoaded`` fetches all three regardless of which are switched
  // on, so all three go pending on a country click.
  const COUNTRY_SCOPED_TIER_KEYS = Object.freeze(['l1', 'l2', 'l4']);

  // Minimum time a row stays in the pulsing "syncing" state before it may go
  // green, so the transition is perceptible even when the fetch resolves in
  // milliseconds (a warm cache, or a local dev server).
  const MIN_SYNCING_MS = 450;

  // key → timestamp the row entered "syncing", for the MIN_SYNCING_MS dwell.
  // The only mutable state in this module; entries are deleted as they're
  // consumed, and a key that never reaches markCached (a failed load) is
  // cleared by the next refresh() instead.
  const _syncingSince = new Map();

  /**
   * Monotonic-ish clock for the dwell calculation.
   *
   * @returns {number} milliseconds.
   */
  function _now() {
    return typeof performance === 'object' && performance ? performance.now() : Date.now();
  }

  /**
   * The ``.sync-dot`` element for a given overlay row, or ``null`` when
   * absent — the weather row is conditionally rendered (flag-gated), and
   * SNOW-658 removed the favourites/community_reports rows outright while
   * map.js still calls ``markCached`` for both, so a missing dot is expected
   * and simply skipped rather than treated as an error.
   *
   * @param {string} key - an ``OVERLAY_RESOURCES`` key.
   * @returns {Element | null}
   */
  function _overlayDot(key) {
    return document.querySelector(`#basemap-menu [data-overlay-key="${key}"] .sync-dot`);
  }

  /**
   * The menu-row ``button`` that owns ``dot`` (the ``.basemap-menu-item``),
   * so a resolved state can also gate the row's interactivity. Null when
   * the dot has no such ancestor.
   *
   * @param {Element | null} dot
   * @returns {HTMLElement | null}
   */
  function _rowOf(dot) {
    return dot ? dot.closest('.basemap-menu-item') : null;
  }

  /**
   * Every basemap radio row (one per configured basemap) — the
   * ``menuitemradio`` buttons carrying ``data-basemap-url``. Excludes the
   * overlay checkboxes, which carry ``data-overlay-key`` and no basemap URL.
   *
   * @returns {HTMLElement[]}
   */
  function _basemapItems() {
    return Array.from(
      document.querySelectorAll('#basemap-menu .basemap-menu-item[data-basemap-url]'),
    );
  }

  /**
   * Every country toggle row (``data-overlay-key="country.<code>"``) in the
   * layers menu.
   *
   * @returns {HTMLElement[]}
   */
  function _countryItems() {
    return Array.from(
      document.querySelectorAll(`#basemap-menu [data-overlay-key^="${COUNTRY_KEY_PREFIX}"]`),
    );
  }

  /**
   * The country codes a row switches, e.g. ``['fr']`` — or ``['at', 'it']``
   * for the ALBINA row, which is one bulletin provider covering two countries
   * (SNOW-658).
   *
   * Read from the row's own ``data-country-codes`` attribute rather than from
   * ``countryCodesFor`` in static/js/map_state.js, which is where the grouping
   * is DECLARED: this module loads before the map bundle (see home.html) and
   * is unit-tested on its own, so a bare cross-script identifier would be in
   * its temporal dead zone at parse time and undefined in isolation. The
   * template renders the attribute from that same declaration, and
   * ``tests/public/test_map_country_groups.py`` fails if the two disagree.
   *
   * Falls back to the key's own suffix, so a row without the attribute
   * behaves exactly as it did before this ticket.
   *
   * @param {HTMLElement} item
   * @returns {string[]}
   */
  function _countryCodesOf(item) {
    const declared = (item.dataset.countryCodes || '').trim();
    if (declared) return declared.split(/\s+/);
    const suffix = (item.dataset.overlayKey || '').slice(COUNTRY_KEY_PREFIX.length);
    return suffix ? [suffix] : [];
  }

  /**
   * The codes of the countries currently switched ON. Read from the rows'
   * ``aria-checked`` rather than from map.js's ``countryState`` — that lives
   * in another IIFE, and the DOM is already the picker's source of truth.
   *
   * Flat-mapped over each row's codes: one checked ALBINA row means both AT
   * and IT are on, and the country-scoped tier probes below must judge
   * against both.
   *
   * @returns {string[]}
   */
  function _enabledCountryCodes() {
    return _countryItems()
      .filter((item) => item.getAttribute('aria-checked') === 'true')
      .flatMap(_countryCodesOf)
      .filter(Boolean);
  }

  /**
   * True when the app is not using the network. Read live on every
   * ``refresh()`` (and every ``snowdesk:connectivity-changed``), so the gating
   * reflects the state at paint time.
   *
   * SNOW-748: read through ``window.pwaConnectivity``, not from
   * ``navigator.onLine`` directly. The interface being up is only half the
   * question — under an offline mode (the worker's latch, or the one the user
   * forced from the header toggle) no request leaves the app at all, and a
   * green dot beside an uncached layer would promise data that cannot arrive.
   * ``navigator.onLine`` stays the fallback for a page where
   * ``pwa_offline.js`` has not run.
   *
   * @returns {boolean}
   */
  function _offline() {
    const connectivity = window.pwaConnectivity;
    if (connectivity) return !connectivity.isOnline();
    return typeof navigator !== 'undefined' && navigator.onLine === false;
  }

  /**
   * Enable / disable a menu row for the offline+uncached case. Uses
   * ``aria-disabled`` (the picker's click handler in map.js honours it, and
   * map.css already dims + not-allowed-cursors ``[aria-disabled="true"]``
   * rows) rather than the native ``disabled`` property, so a screen reader
   * still announces the row and its red dot. Only rows this module disabled
   * (tagged with ``DISABLED_MARKER``) are re-enabled, so a row disabled for
   * another reason is never clobbered.
   *
   * @param {HTMLElement | null} row
   * @param {boolean} disabled
   * @returns {void}
   */
  function _setRowDisabled(row, disabled) {
    if (!row) return;
    if (disabled) {
      row.setAttribute('aria-disabled', 'true');
      row.setAttribute(DISABLED_MARKER, '1');
    } else if (row.getAttribute(DISABLED_MARKER) === '1') {
      row.removeAttribute(DISABLED_MARKER);
      // SNOW-573: the weather row carries a SECOND, independent disable —
      // map.js disables it while the scrubbed date sits outside the stored
      // forecast window (WEATHER_ROW_DISABLED_MARKER there). Dropping our own
      // marker must not clear ``aria-disabled`` while that one is still in
      // force, or coming back online would re-enable a row whose date still
      // has nothing to draw. map.js guards the mirror case the same way, so
      // whichever reason clears second is the one that re-enables the row.
      if (row.getAttribute(WEATHER_DISABLED_MARKER) !== '1') {
        row.removeAttribute('aria-disabled');
      }
    }
  }

  /**
   * Resolve and paint a cacheable resource's dot AND gate its row, given
   * real cache state and the live connection state:
   *
   *   - cached           → green, row enabled (available offline right now).
   *   - uncached, online → grey "view online first", row enabled.
   *   - uncached, offline→ red "unavailable offline", row DISABLED.
   *
   * The offline+uncached row is disabled so the user can't toggle a layer
   * whose data can't be fetched — but a row already switched ON keeps its
   * on-map layer (this only locks the menu control; it never hides a
   * visible layer), matching the "keep shown, lock the toggle" rule.
   *
   * Overlay and country rows only. Basemap rows have a third state this
   * two-way split can't express — see ``_applyBasemapState``, which owns
   * the never-disable-the-active-basemap rule too.
   *
   * @param {Element | null} dot
   * @param {boolean} cached - is the resource actually in a cache?
   * @param {string} cachedLabel
   * @param {string} uncachedLabel
   * @param {string} blockedLabel
   * @returns {void}
   */
  function _applyState(dot, cached, cachedLabel, uncachedLabel, blockedLabel) {
    const offline = _offline();
    if (cached) {
      _paintDot(dot, 'cached', cachedLabel);
    } else if (offline) {
      _paintDot(dot, 'unavailable-offline', blockedLabel);
    } else {
      _paintDot(dot, 'uncached', uncachedLabel);
    }
    // Only an offline miss locks a row: online, anything uncached is one
    // fetch away, so the control stays live whatever the dot says.
    _setRowDisabled(_rowOf(dot), offline && !cached);
  }

  /**
   * SNOW-722: the basemap rows' own applier — three states, not two.
   *
   * Deliberately NOT ``_applyState`` widened again. That function serves
   * eight overlay/country call sites whose resource is a single cacheable
   * thing: present or absent, and absent-while-offline means the row can't
   * work. A basemap has a middle state those rows have no equivalent of —
   * partly usable — so folding it in would mean a third boolean every
   * other caller has to pass and ignore. Both share the ``_paintDot`` /
   * ``_setRowDisabled`` primitives, including the ``DISABLED_MARKER``
   * discipline that keeps the reverse transition to rows this module
   * disabled.
   *
   *   downloaded        → green ``cached``, "Available offline".
   *   style cached only → grey ``uncached``, "Partly cached — may not
   *                       load everywhere".
   *   neither           → grey ``uncached`` "view online first" ONLINE;
   *                       red ``unavailable-offline`` offline.
   *
   * The availability answer is the same in both connectivity states, on
   * purpose. The journey this ticket exists for is the pre-flight check —
   * at home on wifi, open the layers menu, then get on a plane — so a dot
   * that only turns honest once the user is offline lies at the one moment
   * they could still act on it by downloading the area. Only the RED state
   * and the GATING are offline-only: online every basemap is one fetch
   * away, so nothing is ever disabled and nothing is ever red.
   *
   * Offline, a row is disabled unless it has coverage, a cached style, or
   * is the active basemap — which is never disabled, because you can't be
   * stranded on a map you can't leave. The middle state stays selectable
   * deliberately: such a basemap draws wherever tiles were picked up
   * online, so it is an advisory, not a gate.
   *
   * Note the ``cached`` / ``uncached`` / ``unavailable-offline``
   * vocabulary is the EXISTING one; the middle state reuses ``uncached``
   * (grey, advisory, interactive) and differs from it only in the label.
   * No new ``data-sync-state`` value, so map.css needs no new rule.
   *
   * @param {Element | null} dot
   * @param {boolean} downloaded - real downloaded area coverage for this
   *   basemap (``_hasBasemapCoverage``).
   * @param {boolean} styleCached - is its style URL in any cache?
   * @param {boolean} isActive - is this the basemap currently displayed?
   * @returns {void}
   */
  function _applyBasemapState(dot, downloaded, styleCached, isActive) {
    const offline = _offline();
    if (downloaded) {
      _paintDot(dot, 'cached', BASEMAP_CACHED_LABEL);
    } else if (styleCached) {
      _paintDot(dot, 'uncached', BASEMAP_PARTIAL_LABEL);
    } else if (offline) {
      _paintDot(dot, 'unavailable-offline', BASEMAP_OFFLINE_BLOCKED_LABEL);
    } else {
      _paintDot(dot, 'uncached', BASEMAP_UNCACHED_LABEL);
    }
    _setRowDisabled(_rowOf(dot), offline && !(downloaded || styleCached || isActive));
  }

  /**
   * Low-level dot painter behind ``_applyState`` and
   * ``_applyBasemapState``:
   * set ``data-sync-state`` plus an accessible name (``role="img"`` +
   * ``aria-label``) and a ``title`` tooltip, and reveal the dot (dots start
   * ``aria-hidden="true"`` in the server-rendered ``unknown`` state).
   *
   * @param {Element | null} dot
   * @param {string} state - a resolved ``data-sync-state`` value.
   * @param {string} label
   * @returns {void}
   */
  function _paintDot(dot, state, label) {
    if (!dot) return;
    dot.dataset.syncState = state;
    dot.setAttribute('role', 'img');
    dot.setAttribute('aria-label', label);
    dot.setAttribute('aria-hidden', 'false');
    dot.title = label;
  }

  /**
   * True when ``path`` (relative to ``location.origin``) is present in
   * ANY Cache Storage cache, ignoring its query string. Never throws —
   * any failure (including ``caches`` being unavailable, though callers
   * already feature-detect that) resolves to ``false``.
   *
   * @param {string} path - a same-origin path, e.g. ``/api/regions.geojson``.
   * @returns {Promise<boolean>}
   */
  async function _probeGeoJson(path) {
    try {
      const request = new Request(new URL(path, location.origin));
      const response = await caches.match(request, { ignoreSearch: true });
      return !!response;
    } catch (_e) {
      return false;
    }
  }

  /**
   * SNOW-524: True when ``pathAndQuery`` is present in ANY Cache Storage
   * cache, matching the query string EXACTLY (no ``ignoreSearch``).
   *
   * The SW's ``_staleWhileRevalidate`` caches per full URL — ``cache.put(request,
   * …)`` in static/js/sw.js — so ``?country=ch`` and ``?country=at`` are
   * separate entries. An ``ignoreSearch`` probe erases that distinction and
   * reports a country cached because a *different* country was cached, which
   * is what let an offline Austria toggle fire three failing fetches behind
   * three green dots. Never throws.
   *
   * @param {string} pathAndQuery - e.g. ``/api/regions.geojson?country=at``.
   * @returns {Promise<boolean>}
   */
  async function _probeExact(pathAndQuery) {
    try {
      const request = new Request(new URL(pathAndQuery, location.origin));
      const response = await caches.match(request);
      return !!response;
    } catch (_e) {
      return false;
    }
  }

  /**
   * True when ``path`` is cached for EVERY code in ``codes`` — the
   * country-aware replacement for ``_probeGeoJson`` on the country-scoped
   * tiers (l1/l2/l4). A tier is only honestly "available offline" if it's
   * available for every country the user has switched on; otherwise the tier
   * dot would sit green above a red country row.
   *
   * @param {string} path - a country-scoped feed path.
   * @param {string[]} codes - enabled country codes.
   * @returns {Promise<boolean>}
   */
  async function _probeEveryCountry(path, codes) {
    const results = await Promise.all(
      codes.map((code) => _probeExact(`${path}?country=${code}`)),
    );
    return results.every(Boolean);
  }

  /**
   * True when every feed in ``COUNTRY_FEED_PATHS`` is cached for EVERY code
   * the row switches — the provider row's own availability signal.
   *
   * SNOW-658: the ALBINA row covers two countries, and this dot must not go
   * green on half of them. Tapping that row switches both on, so a green dot
   * with only Austria cached would promise an offline map that comes up
   * missing Italy — the exact class of lie SNOW-524 built these dots to stop.
   *
   * @param {string[]} codes - the row's country codes, e.g. ``['at', 'it']``.
   * @returns {Promise<boolean>}
   */
  async function _probeCountry(codes) {
    const results = await Promise.all(
      codes.flatMap((code) =>
        COUNTRY_FEED_PATHS.map((path) => _probeExact(`${path}?country=${code}`)),
      ),
    );
    return results.every(Boolean);
  }

  /**
   * True when ``window.pwaDb``'s ``data:map_overlays`` store holds a row
   * for ``key`` carrying a truthy ``.geojson`` payload. Never throws — a
   * missing/broken ``window.pwaDb`` resolves to ``false``, matching the
   * "DB probe path unavailable" case in the module docstring.
   *
   * @param {string} key - ``'favourites'`` or ``'community_reports'``.
   * @returns {Promise<boolean>}
   */
  async function _probeIdbRow(key) {
    try {
      if (typeof window.pwaDb !== 'object' || window.pwaDb === null) return false;
      const record = await window.pwaDb.get(IDB_STORE, key);
      return !!(record && record.geojson);
    } catch (_e) {
      return false;
    }
  }

  /**
   * True when ``url`` (a basemap's cross-origin style URL) is present in
   * ANY Cache Storage cache. The style JSON is cached both by passive
   * browsing (sw.js's ``_basemapStaleWhileRevalidate`` writes
   * ``BASEMAP_CACHE``) and by a deliberate "Download basemap" run (which
   * pins the active basemap's style into that download's OWN per-area
   * bucket — SNOW-586, one of potentially several
   * ``snowdesk-basemap-pinned-*`` caches now), so a globally-searched
   * ``caches.match`` covers every partition without hardcoding any of
   * their names.
   *
   * SNOW-722: this used to BE the per-basemap "available offline" signal,
   * on the reasoning that a cached style is a cheap proxy for a basemap
   * the user has actually loaded. It isn't. Passive browsing caches the
   * style of any basemap the user so much as glanced at, and selecting
   * that basemap offline yields a style with essentially no tiles — a
   * blank map behind a green dot. So it is now the WEAKER of two signals:
   * downloaded area coverage (``_downloadedBasemapKeys``) is what earns
   * green, and a bare cached style earns only the grey "partly cached"
   * advisory — true, because sw.js does serve whatever tiles are cached
   * and the map's fallback style (SNOW-483) + overlay re-install cover a
   * mid-pan miss, so such a basemap draws in some places and not others.
   *
   * Never throws.
   *
   * @param {string} url - a basemap style URL (``data-basemap-url``).
   * @returns {Promise<boolean>}
   */
  async function _probeUrlCached(url) {
    try {
      if (!url) return false;
      const response = await caches.match(new Request(url), { ignoreSearch: true });
      return !!response;
    } catch (_e) {
      return false;
    }
  }

  /**
   * SNOW-722: the set of basemap keys with real downloaded area coverage —
   * the green claim behind each basemap row's dot.
   *
   * Read from ``window.pwaBasemapDownloads.areas()``
   * (static/js/map_basemap_downloads.js), which returns every recorded
   * area — regions, custom areas and reconciled orphans alike — each
   * carrying the ``basemapKey`` it was fetched under. An area counts as
   * coverage for exactly that basemap.
   *
   * ``basemapKey`` is null on a record written before SNOW-645 (and on an
   * orphaned bucket, which has no record at all), meaning "downloaded,
   * basemap unknown". Such an area is attributed to the ACTIVE basemap
   * only, mirroring the ``basemapKey || activeKey`` convention in
   * ``basemapDownloadedTemplates`` — greening every basemap off one
   * keyless record would restore the very over-claim this exists to stop,
   * and greening none of them would regress the basemap the user is
   * actually on.
   *
   * Called ONCE per ``refresh()`` pass, not once per row: it is an
   * IndexedDB read plus (for orphans) a Cache Storage walk, and every row
   * wants the same answer.
   *
   * @param {string} activeKey - the active row's ``data-basemap-key``, or
   *   ``''`` when no row is checked (then a keyless area counts for
   *   nothing).
   * @returns {Promise<Set<string>|null>} ``null`` — distinct from an empty
   *   set — when the downloads module is unavailable or its read failed,
   *   i.e. "coverage is unknowable here". Callers fall back to the
   *   style-only signal, which is what this module did before SNOW-722.
   *   Never throws.
   */
  async function _downloadedBasemapKeys(activeKey) {
    try {
      const downloads = window.pwaBasemapDownloads;
      if (!downloads || typeof downloads.areas !== 'function') return null;
      const areas = await downloads.areas();
      if (!Array.isArray(areas)) return null;
      const keys = new Set();
      for (const area of areas) {
        if (!area) continue;
        const key = area.basemapKey || activeKey;
        if (key) keys.add(key);
      }
      return keys;
    } catch (_e) {
      return null;
    }
  }

  /**
   * Does ``key`` have downloaded coverage, given the resolved key set?
   *
   * With coverage unknowable (``keys === null`` — no downloads module, see
   * ``_downloadedBasemapKeys``) this falls back to ``styleCached``, the
   * pre-SNOW-722 signal, so a page without the map bundle behaves exactly
   * as it always did rather than reporting every basemap unavailable.
   *
   * @param {Set<string>|null} keys
   * @param {string} key - the row's ``data-basemap-key``.
   * @param {boolean} styleCached
   * @returns {boolean}
   */
  function _hasBasemapCoverage(keys, key, styleCached) {
    if (keys === null) return styleCached;
    return !!key && keys.has(key);
  }

  // SNOW-613: the pass currently running, and the single trailing pass
  // queued behind it. See ``refresh``.
  /** @type {Promise<void>|null} */
  let _refreshInFlight = null;
  /** @type {Promise<void>|null} */
  let _refreshQueued = null;

  /**
   * Re-probe every resource in ``OVERLAY_RESOURCES`` plus the basemap
   * indicator, and paint the resolved state onto each row's dot.
   * Feature-detects Cache Storage support: if ``'caches' in window`` is
   * false, every dot is left at its server-rendered ``unknown`` state
   * (hidden) and this resolves immediately. Otherwise every probe runs
   * concurrently and is individually guarded, so one throwing probe can't
   * stop the others from resolving — this promise itself NEVER rejects.
   *
   * SNOW-613: concurrent callers coalesce onto at most one QUEUED pass
   * rather than sharing the one already running. The triggers are all
   * interactive and all bunched — a layers-menu open, a region tap, a
   * connectivity flip, the end of a download run — and each pass is a
   * dozen Cache Storage probes, so a burst used to issue that work several
   * times over for one answer.
   *
   * Trailing rather than leading, because this menu is a live cache-state
   * dashboard: a caller arriving mid-pass is usually reporting a change
   * the running pass has already probed past — a download that has just
   * written its tiles, say — so handing back the in-flight promise would
   * settle its dots against state from before the thing it is reacting to.
   * At most one extra pass is ever queued, however many callers arrive.
   *
   * @returns {Promise<void>}
   */
  function refresh() {
    if (!_refreshInFlight) {
      _refreshInFlight = _refresh().finally(() => {
        _refreshInFlight = null;
      });
      return _refreshInFlight;
    }
    if (!_refreshQueued) {
      _refreshQueued = _refreshInFlight.then(() => {
        // Cleared before recursing: by now the `finally` above has already
        // dropped `_refreshInFlight`, so this call starts the fresh pass
        // rather than queueing behind itself.
        _refreshQueued = null;
        return refresh();
      });
    }
    return _refreshQueued;
  }

  /**
   * The pass itself — see ``refresh`` above, which is what callers use.
   *
   * @returns {Promise<void>}
   */
  async function _refresh() {
    if (!('caches' in window)) return;

    // SNOW-524: a full re-probe supersedes any pending state — including a
    // load that failed and so never reached markCached. Dropping the
    // timestamps here stops a stale entry from imposing a dwell on some
    // unrelated markCached call much later.
    _syncingSince.clear();

    const tasks = [];

    // SNOW-524: the country-scoped tiers are judged against the countries the
    // user actually has switched on. With none enabled there is no country to
    // judge against, so fall back to the country-blind probe rather than
    // reporting "not cached" for a tier that has nothing to cache.
    const enabledCountries = _enabledCountryCodes();

    for (const [key, resource] of Object.entries(OVERLAY_RESOURCES)) {
      const dot = _overlayDot(key);
      if (!dot) continue;

      let probe;
      if (resource.kind === 'idb') {
        probe = _probeIdbRow(resource.key);
      } else if (resource.countryScoped && enabledCountries.length > 0) {
        probe = _probeEveryCountry(resource.path, enabledCountries);
      } else {
        probe = _probeGeoJson(resource.path);
      }
      tasks.push(
        probe
          .then((cached) =>
            _applyState(dot, cached, CACHED_LABEL, UNCACHED_LABEL, OFFLINE_BLOCKED_LABEL),
          )
          .catch(() =>
            _applyState(dot, false, CACHED_LABEL, UNCACHED_LABEL, OFFLINE_BLOCKED_LABEL),
          ),
      );
    }

    // SNOW-524: per-country availability. A country is offline-ready when all
    // four of its feeds are cached; offline + uncached disables the row, so
    // toggling a country whose data can't be fetched is simply not offered
    // (previously it turned on, fired four failing requests, and did nothing).
    for (const item of _countryItems()) {
      const dot = item.querySelector('.sync-dot');
      if (!dot) continue;
      const codes = _countryCodesOf(item);
      if (codes.length === 0) continue;
      tasks.push(
        _probeCountry(codes)
          .then((cached) =>
            _applyState(
              dot,
              cached,
              COUNTRY_CACHED_LABEL,
              COUNTRY_UNCACHED_LABEL,
              COUNTRY_OFFLINE_BLOCKED_LABEL,
            ),
          )
          .catch(() =>
            _applyState(
              dot,
              false,
              COUNTRY_CACHED_LABEL,
              COUNTRY_UNCACHED_LABEL,
              COUNTRY_OFFLINE_BLOCKED_LABEL,
            ),
          ),
      );
    }

    // Per-basemap availability, on the two signals SNOW-722 splits it into
    // — downloaded area coverage (green) and a cached style (grey "partly
    // cached") — so switching to a basemap that would come up blank
    // offline is not offered, while one that draws in some places still
    // is. See ``_applyBasemapState``.
    //
    // The active basemap is the one exception, and only to the ROW. It is
    // already loaded this session, so it must stay selectable or the user
    // is stranded on a map they can't leave — but that says nothing about
    // what is stored, so it never colours the dot.
    const basemapItems = _basemapItems();
    if (basemapItems.length > 0) {
      const activeItem = basemapItems.find((item) => item.getAttribute('aria-checked') === 'true');
      const activeKey = (activeItem && activeItem.dataset.basemapKey) || '';
      // Started here, awaited per row: one read for the whole pass (it is
      // an IndexedDB read plus a possible Cache Storage walk), and started
      // alongside the style probes rather than before them, so the pass
      // stays as concurrent as it was. Never rejects — see
      // ``_downloadedBasemapKeys`` — so it needs no rejection handler of
      // its own beyond each row's belt-and-braces ``.catch``.
      const downloadedKeys = _downloadedBasemapKeys(activeKey);
      for (const item of basemapItems) {
        const dot = item.querySelector('.sync-dot');
        if (!dot) continue;
        const isActive = item.getAttribute('aria-checked') === 'true';
        const key = item.dataset.basemapKey || '';
        tasks.push(
          Promise.all([_probeUrlCached(item.dataset.basemapUrl), downloadedKeys])
            .then(([styleCached, keys]) =>
              _applyBasemapState(
                dot,
                _hasBasemapCoverage(keys, key, styleCached),
                styleCached,
                isActive,
              ),
            )
            // A throw tells us nothing was found either way, so the row
            // resolves as the empty case: red offline, grey online, and
            // still selectable when it is the active basemap.
            .catch(() => _applyBasemapState(dot, false, false, isActive)),
        );
      }
    }

    await Promise.all(tasks);
  }

  /**
   * SNOW-524: paint a row as mid-fetch — grey and pulsing — synchronously,
   * with no probe. Called by map.js the instant a country toggle turns on,
   * for the country row and every country-scoped tier, so the user watches
   * their click populate the offline cache; ``markCached`` then greens each
   * row as its data lands.
   *
   * Deliberately probe-free. An async ``refresh()`` here would race the very
   * fetches it's meant to represent: on a fast connection the load wins and
   * the pending state never paints, and on a slow one the probe can resolve
   * AFTER ``markCached`` and repaint a freshly-green row grey. Driving the
   * state off the load lifecycle instead makes the sequence deterministic.
   *
   * Never disables the row (unlike the offline+uncached state) — a fetch in
   * flight isn't a reason to lock the control.
   *
   * @param {string} key - an ``OVERLAY_RESOURCES`` key or ``country.<code>``.
   * @returns {void}
   */
  function markSyncing(key) {
    const dot = _overlayDot(key);
    if (!dot) return;
    _paintDot(dot, 'syncing', SYNCING_LABEL);
    _setRowDisabled(_rowOf(dot), false);
    _syncingSince.set(key, _now());
  }

  /**
   * Optimistically flip a single overlay row's dot to "cached" without a
   * probe — the real-time counterpart to ``refresh()``. Called by
   * static/js/map.js when a lazy tier's toggle-on load has just succeeded
   * (``overlayLoaded[key]`` true), so the resource has now flowed through
   * the SW cache / overlay IDB store and is available offline. No-ops for
   * any key not in ``OVERLAY_RESOURCES``. The next ``refresh()`` (popover
   * re-open) re-verifies against real cache state.
   *
   * @param {string} key - an ``OVERLAY_RESOURCES`` key or ``country.<code>``.
   * @returns {void}
   */
  function markCached(key) {
    // SNOW-524: hold a pulsing row at "syncing" for a minimum dwell before
    // greening it. Without this a warm/local fetch resolves in tens of
    // milliseconds and the transition the pending state exists to show is
    // imperceptible — the dot appears to flip straight to green. Only applies
    // to rows actually mid-sync; every other markCached call paints
    // immediately, so this never delays an ordinary update.
    const since = _syncingSince.get(key);
    if (since !== undefined) {
      const elapsed = _now() - since;
      _syncingSince.delete(key);
      if (elapsed < MIN_SYNCING_MS) {
        setTimeout(() => markCached(key), MIN_SYNCING_MS - elapsed);
        return;
      }
    }
    _markCachedNow(key);
  }

  /**
   * The immediate half of ``markCached`` — paints green with no dwell check.
   *
   * @param {string} key
   * @returns {void}
   */
  function _markCachedNow(key) {
    // SNOW-524: country rows are keyed ``country.<code>`` and aren't in
    // OVERLAY_RESOURCES (their resource set is COUNTRY_FEED_PATHS, resolved
    // per code at probe time). map.js calls this the moment a country's four
    // feeds have loaded, which only happens online — so cached=true.
    if (key.startsWith(COUNTRY_KEY_PREFIX)) {
      _applyState(
        _overlayDot(key),
        true,
        COUNTRY_CACHED_LABEL,
        COUNTRY_UNCACHED_LABEL,
        COUNTRY_OFFLINE_BLOCKED_LABEL,
      );
      return;
    }
    // Unknown keys are ignored — every remaining OVERLAY_RESOURCES entry is
    // a 'geojson' or 'idb' resource whose successful load does put it in the
    // offline cache, so membership alone is the allowlist.
    if (!OVERLAY_RESOURCES[key]) return;
    // A successful load only happens online; ``_applyState`` with cached=true
    // paints green and clears any offline-disabled marker on the row.
    _applyState(_overlayDot(key), true, CACHED_LABEL, UNCACHED_LABEL, OFFLINE_BLOCKED_LABEL);
  }

  // Re-run the full probe on every connectivity transition (broadcast by
  // pwa_offline.js), so going offline immediately reds-out + disables the
  // uncached rows/basemaps and coming back online re-enables them — the menu
  // reflects reality without waiting for the next popover open. refresh()
  // never rejects, so the bare .catch is belt-and-braces.
  document.addEventListener('snowdesk:connectivity-changed', () => {
    try {
      refresh();
    } catch (_e) {
      // refresh() is internally guarded; ignore any synchronous throw.
    }
  });

  Object.defineProperty(window, 'pwaLayerSyncStatus', {
    value: Object.freeze({ refresh, markCached, markSyncing, COUNTRY_SCOPED_TIER_KEYS }),
    writable: false,
    configurable: false,
  });
})();
