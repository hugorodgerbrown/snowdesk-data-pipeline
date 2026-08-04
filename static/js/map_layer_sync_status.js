/*
 * static/js/map_layer_sync_status.js — Sync-status dots for the map's
 * layers popover (SNOW-505).
 *
 * The layers popover (#basemap-menu in _map_embed.html) lists everything
 * the PWA can cache for offline use — the region overlays (L1/L2/L4),
 * resorts, favourites, community reports, and the active basemap — but
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
 * The dots don't just advise — while ``navigator.onLine === false`` they
 * GATE interaction. A resource that isn't cached can't be loaded offline,
 * so its row gets the red
 * ``unavailable-offline`` dot AND is disabled (``aria-disabled``, honoured
 * by the picker's click handler and dimmed by map.css). Basemaps are gated
 * too: each basemap row carries its own dot, and a basemap whose style
 * isn't cached is non-selectable offline — except the active basemap, which
 * is always available (you're already on it). Disabling a row never hides a
 * layer already on the map; it only locks the menu control ("keep shown,
 * lock the toggle"). Online, an uncached row stays the grey advisory
 * "view online first" and fully interactive.
 *
 * Row → resource map (the single source of truth driving every probe):
 *
 *   country.<code>       — (SNOW-524) the four ``?country=<code>``-scoped
 *                          feeds a country load fetches: L1, L2, L4 and
 *                          ratings. Green only when ALL four are cached for
 *                          that code, Switzerland included — the first page
 *                          load is just "toggle CH on from blank", so there
 *                          is no default-country special case. Basemap tiles
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
 *   favourites,
 *   community_reports    — IndexedDB ``data:map_overlays`` rows written
 *                          by map_overlay_offline_cache.js. A truthy row
 *                          carrying ``.geojson`` counts as cached.
 *   basemap (one dot)    — the active/any basemap's tile cache,
 *                          discovered by the ``snowdesk-basemap-``
 *                          prefix (SNOW-484's BASEMAP_CACHE) rather than
 *                          a hardcoded version, so a cache-version bump
 *                          never breaks the probe. Non-empty → cached.
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
  const COUNTRY_CACHED_LABEL = STRINGS['country-cached'];
  const COUNTRY_UNCACHED_LABEL = STRINGS['country-uncached'];
  const COUNTRY_OFFLINE_BLOCKED_LABEL = STRINGS['country-offline-blocked'];
  const SYNCING_LABEL = STRINGS.syncing;

  // Marker set on any menu row this module disabled for the offline+uncached
  // case, so the reverse transition only re-enables what it disabled (never
  // a row disabled for another reason). Mirrors pwa_offline.js's
  // ``data-was-disabled-offline`` idiom, namespaced to this module.
  const DISABLED_MARKER = 'data-sync-disabled-offline';

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
    // Not country-scoped: /api/resorts.geojson takes no ``?country=`` param,
    // it's one payload for every country.
    resorts: Object.freeze({ kind: 'geojson', path: '/api/resorts.geojson' }),
    favourites: Object.freeze({ kind: 'idb', key: 'favourites' }),
    community_reports: Object.freeze({ kind: 'idb', key: 'community_reports' }),
    // The "Available offline" row. Unlike every other entry this has no
    // feed of its own to probe — its question is "are any basemap tiles
    // pinned at all", i.e. is there an offline map to speak of.
    downloaded: Object.freeze({ kind: 'pinned-tiles' }),
  });

  // The cache deliberate downloads write to. NOT the passive
  // ``snowdesk-basemap-`` cache that ordinary panning and zooming fills:
  // that one is a browsing side effect, capped and LRU-trimmed, and can
  // vanish under the user at any moment. Reporting it as "available
  // offline" would be a promise this app cannot keep.
  const PINNED_BASEMAP_CACHE_PREFIX = 'snowdesk-basemap-pinned-';

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
   * absent — favourites/community_reports rows are conditionally
   * rendered (flag/eligibility gated), so a missing dot is expected and
   * simply skipped rather than treated as an error.
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
   * The country code carried by a country row, e.g. ``'fr'``.
   *
   * @param {HTMLElement} item
   * @returns {string}
   */
  function _countryCodeOf(item) {
    return (item.dataset.overlayKey || '').slice(COUNTRY_KEY_PREFIX.length);
  }

  /**
   * The codes of the countries currently switched ON. Read from the rows'
   * ``aria-checked`` rather than from map.js's ``countryState`` — that lives
   * in another IIFE, and the DOM is already the picker's source of truth.
   *
   * @returns {string[]}
   */
  function _enabledCountryCodes() {
    return _countryItems()
      .filter((item) => item.getAttribute('aria-checked') === 'true')
      .map(_countryCodeOf)
      .filter(Boolean);
  }

  /**
   * True when the app is offline. Read live from ``navigator.onLine`` on
   * every ``refresh()`` (and every ``snowdesk:connectivity-changed``), so
   * the gating reflects the connection state at paint time.
   *
   * @returns {boolean}
   */
  function _offline() {
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
      row.removeAttribute('aria-disabled');
      row.removeAttribute(DISABLED_MARKER);
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
   * @param {Element | null} dot
   * @param {boolean} cached
   * @param {string} cachedLabel
   * @param {string} uncachedLabel
   * @param {string} blockedLabel
   * @returns {void}
   */
  function _applyState(dot, cached, cachedLabel, uncachedLabel, blockedLabel) {
    const offline = _offline();
    if (cached) {
      _paintDot(dot, 'cached', cachedLabel);
      _setRowDisabled(_rowOf(dot), false);
    } else if (offline) {
      _paintDot(dot, 'unavailable-offline', blockedLabel);
      _setRowDisabled(_rowOf(dot), true);
    } else {
      _paintDot(dot, 'uncached', uncachedLabel);
      _setRowDisabled(_rowOf(dot), false);
    }
  }

  /**
   * Low-level dot painter behind ``_applyState``:
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
   * True when every feed in ``COUNTRY_FEED_PATHS`` is cached for ``code`` —
   * the country row's own availability signal.
   *
   * @param {string} code - a country code, e.g. ``'fr'``.
   * @returns {Promise<boolean>}
   */
  async function _probeCountry(code) {
    const results = await Promise.all(
      COUNTRY_FEED_PATHS.map((path) => _probeExact(`${path}?country=${code}`)),
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
   * Whether the pinned basemap cache holds at least one TILE.
   *
   * Tiles specifically, not merely "at least one entry": a download also
   * pins the style JSON and its sprites, so an entry count would go green
   * off a run that cached the furniture and none of the map. Tiles are
   * identified by running the active basemap's URL template backwards
   * (``cachedTilesFromURLs``), which also makes the dot per-basemap —
   * consistent with the overlay it sits beside, and honest, since tiles
   * pinned for one basemap genuinely are not available on another.
   *
   * SNOW-586: every per-area pinned bucket is UNIONED (accumulated into
   * one URL list), not just the first ``caches.keys()`` happens to
   * return. Each deliberate download now writes into its OWN bucket
   * (``PINNED_BASEMAP_CACHE_PREFIX + areaId``), so taking only the first
   * match would answer "does the first bucket, by Cache Storage insertion
   * order, hold a tile for the active basemap?" rather than "is ANY area
   * available offline?" — concretely, an area downloaded under a
   * DIFFERENT basemap than the one now active can sort first and hold
   * none of the active template's tiles, which would read the whole dot
   * as uncached even though a complete area sits in a later bucket.
   *
   * This re-implements the same union `static/js/map.js`'s
   * ``pinnedBasemapCacheURLs()`` performs, as a local loop rather than a
   * shared call: this module is deliberately self-contained (its own
   * ``PINNED_BASEMAP_CACHE_PREFIX``, its own never-throws contract), and
   * reaching across into another script's helper would make that
   * contract depend on script load order instead.
   *
   * Falls back to "any entry at all" when the template is unresolvable
   * (the style still settling) rather than reporting a bare no.
   *
   * SNOW-610: the map handle comes from ``window.snowdeskMapState``, the
   * one named channel to map.js's shared state. It used to be read as the
   * bare identifier ``MAP`` — which works, because a top-level ``let`` in
   * a classic script lands in the global declarative environment — but
   * that is invisible to a reader and impossible to grep for, and reading
   * the same handle as ``window.MAP`` (which is NOT a window property) is
   * what left this probe stuck on the "any entry at all" fallback the
   * paragraph above warns against for its whole life (M1,
   * docs/code-reviews/2026-08-03-js-review.md).
   *
   * @returns {Promise<boolean>}
   */
  async function _probeAnyPinnedTile() {
    try {
      const names = await caches.keys();
      const pinnedNames = names.filter((n) => n.startsWith(PINNED_BASEMAP_CACHE_PREFIX));
      if (!pinnedNames.length) return false;
      const urls = [];
      for (const name of pinnedNames) {
        try {
          const cache = await caches.open(name);
          const requests = await cache.keys();
          for (const request of requests) urls.push(request.url);
        } catch (_e) {
          // One bucket failing to enumerate must not lose the others.
        }
      }
      if (!urls.length) return false;
      const core = self.pwaBasemapDownloadCore;
      const map = window.snowdeskMapState ? window.snowdeskMapState.map : null;
      const template =
        typeof window.activeBasemapTileTemplate === 'function' && map
          ? window.activeBasemapTileTemplate(map)
          : null;
      if (!core || !template) return true;
      return core.cachedTilesFromURLs(template, urls).length > 0;
    } catch (_e) {
      return false;
    }
  }

  /**
   * True when ``url`` (a basemap's cross-origin style URL) is present in
   * ANY Cache Storage cache — the per-basemap "available offline" proxy.
   * The style JSON is cached both by passive browsing (sw.js's
   * ``_basemapStaleWhileRevalidate`` writes ``BASEMAP_CACHE``) and by a
   * deliberate "Download basemap" run (which pins the active basemap's
   * style into that download's OWN per-area bucket — SNOW-586, one of
   * potentially several ``snowdesk-basemap-pinned-*`` caches now), so a
   * globally-searched ``caches.match`` covers every partition without
   * hardcoding any of their names.
   *
   * Limitation: a cached style proves the basemap has been loaded/downloaded
   * before, not that every tile for the current viewport is present. The
   * residual tile gap is handled downstream — sw.js serves cached tiles and
   * the map's fallback style (SNOW-483) + overlay re-install cover a
   * mid-pan miss — so style-presence is the honest, cheap availability
   * signal for the menu. Never throws.
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
      if (resource.kind === 'pinned-tiles') {
        probe = _probeAnyPinnedTile();
      } else if (resource.kind === 'idb') {
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
      const code = _countryCodeOf(item);
      if (!code) continue;
      tasks.push(
        _probeCountry(code)
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

    // Per-basemap availability. A basemap is available offline if its style
    // is cached (browsed/downloaded before) OR it is the active basemap
    // (already loaded this session — never disabled, or the user would be
    // stranded on a map they can't leave). Offline + unavailable ⟹ red dot
    // and a disabled row, so switching to a basemap that can't load offline
    // (the trigger for the micro-region overlay loss) is simply not offered.
    for (const item of _basemapItems()) {
      const dot = item.querySelector('.sync-dot');
      if (!dot) continue;
      const isActive = item.getAttribute('aria-checked') === 'true';
      tasks.push(
        _probeUrlCached(item.dataset.basemapUrl)
          .then((cached) =>
            _applyState(
              dot,
              cached || isActive,
              BASEMAP_CACHED_LABEL,
              BASEMAP_UNCACHED_LABEL,
              BASEMAP_OFFLINE_BLOCKED_LABEL,
            ),
          )
          .catch(() =>
            _applyState(
              dot,
              isActive,
              BASEMAP_CACHED_LABEL,
              BASEMAP_UNCACHED_LABEL,
              BASEMAP_OFFLINE_BLOCKED_LABEL,
            ),
          ),
      );
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
