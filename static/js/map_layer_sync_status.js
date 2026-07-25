/*
 * static/js/map_layer_sync_status.js — Sync-status dots for the map's
 * layers popover (SNOW-505).
 *
 * The layers popover (#basemap-menu in _map_embed.html) lists everything
 * the PWA can cache for offline use — the region overlays (L1/L2/L3/L4),
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
 * GATE interaction. A cacheable resource that isn't cached, and l3 (never
 * cacheable), can't be loaded offline, so its row gets the red
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
 *   l1, l2, l4, resorts  — same-origin GeoJSON feeds cached by sw.js's
 *                          STATIC_PATHS shell cache. Probed via the
 *                          GLOBAL ``caches.match()`` (searches every
 *                          cache, so nothing here hardcodes the
 *                          versioned CACHE_VERSION shell-cache name) with
 *                          ``ignoreSearch: true`` (the app appends
 *                          ``?country=ch`` to these URLs).
 *   l3                   — NOT in STATIC_PATHS (network-only per sw.js's
 *                          classification) — can never be cached for
 *                          offline use, so it resolves to the distinct
 *                          "unavailable" state (a hollow, border-only dot,
 *                          visually separate from the grey "not cached yet"
 *                          fill), without a wasted probe.
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
 * ``cache.put`` didn't land. ``markCached`` no-ops for ``l3`` (network-only
 * in sw.js — genuinely never cached, so its hollow "unavailable" dot stays
 * put even after a successful load) and for any key absent from
 * ``OVERLAY_RESOURCES``.
 */

(function () {
  'use strict';

  const IDB_STORE = 'data:map_overlays';

  const CACHED_LABEL = 'Available offline';
  const UNCACHED_LABEL = 'Not cached — view online first';
  // l3 is a distinct third state (a hollow dot): not "not cached yet" but
  // "can never be cached", so a different message from UNCACHED_LABEL.
  const UNAVAILABLE_LABEL = 'Not available offline';
  // Offline + uncached: genuinely unavailable *right now* (red dot, disabled
  // row) — distinct from the grey advisory "view online first" (which only
  // applies while online, when viewing online is actually an option).
  const OFFLINE_BLOCKED_LABEL = 'Unavailable offline — not cached';
  const BASEMAP_CACHED_LABEL = 'Available offline';
  const BASEMAP_UNCACHED_LABEL = 'Not cached — view online first';
  const BASEMAP_OFFLINE_BLOCKED_LABEL = 'Unavailable offline — switch back online to load';

  // Marker set on any menu row this module disabled for the offline+uncached
  // case, so the reverse transition only re-enables what it disabled (never
  // a row disabled for another reason). Mirrors pwa_offline.js's
  // ``data-was-disabled-offline`` idiom, namespaced to this module.
  const DISABLED_MARKER = 'data-sync-disabled-offline';

  // The core row→resource constant. Keys are the overlay rows'
  // ``data-overlay-key`` values; the basemap indicator is handled
  // separately (single dot, no key). Countries and the Options rows
  // (autozoom, cache-now) are deliberately absent — this module keys
  // strictly off this map, so keyless/unlisted rows never get a dot.
  const OVERLAY_RESOURCES = Object.freeze({
    l1: Object.freeze({ kind: 'geojson', path: '/api/major-regions.geojson' }),
    l2: Object.freeze({ kind: 'geojson', path: '/api/sub-regions.geojson' }),
    l4: Object.freeze({ kind: 'geojson', path: '/api/regions.geojson' }),
    resorts: Object.freeze({ kind: 'geojson', path: '/api/resorts.geojson' }),
    l3: Object.freeze({ kind: 'uncacheable' }),
    favourites: Object.freeze({ kind: 'idb', key: 'favourites' }),
    community_reports: Object.freeze({ kind: 'idb', key: 'community_reports' }),
  });

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
   * Paint l3's distinct state and gate its row. l3 (bulletin groupings) is
   * network-only in sw.js — it can never be cached:
   *
   *   - online  → hollow "never cacheable" dot, row enabled (it still
   *               fetches live).
   *   - offline → red "unavailable offline", row DISABLED (genuinely
   *               un-loadable now).
   *
   * @param {Element | null} dot
   * @returns {void}
   */
  function _applyUnavailable(dot) {
    if (_offline()) {
      _paintDot(dot, 'unavailable-offline', OFFLINE_BLOCKED_LABEL);
      _setRowDisabled(_rowOf(dot), true);
    } else {
      _paintDot(dot, 'unavailable', UNAVAILABLE_LABEL);
      _setRowDisabled(_rowOf(dot), false);
    }
  }

  /**
   * Low-level dot painter shared by ``_applyState`` / ``_applyUnavailable``:
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
   * ANY Cache Storage cache — the per-basemap "available offline" proxy.
   * The style JSON is cached both by passive browsing (sw.js's
   * ``_basemapStaleWhileRevalidate`` writes ``BASEMAP_CACHE``) and by a
   * deliberate "Download basemap" run (which pins the active basemap's
   * style into ``BASEMAP_PINNED_CACHE``), so a globally-searched
   * ``caches.match`` covers both partitions without hardcoding either
   * versioned name.
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

  /**
   * Re-probe every resource in ``OVERLAY_RESOURCES`` plus the basemap
   * indicator, and paint the resolved state onto each row's dot.
   * Feature-detects Cache Storage support: if ``'caches' in window`` is
   * false, every dot is left at its server-rendered ``unknown`` state
   * (hidden) and this resolves immediately. Otherwise every probe runs
   * concurrently and is individually guarded, so one throwing probe can't
   * stop the others from resolving — this promise itself NEVER rejects.
   *
   * @returns {Promise<void>}
   */
  async function refresh() {
    if (!('caches' in window)) return;

    const tasks = [];

    for (const [key, resource] of Object.entries(OVERLAY_RESOURCES)) {
      const dot = _overlayDot(key);
      if (!dot) continue;

      if (resource.kind === 'uncacheable') {
        // l3 is network-only in sw.js — it can never be cached for offline
        // use, a distinct state from "not cached yet": a hollow (border-
        // only) dot rather than a grey fill. Never probed.
        _applyUnavailable(dot);
        continue;
      }

      const probe =
        resource.kind === 'geojson'
          ? _probeGeoJson(resource.path)
          : _probeIdbRow(resource.key);
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
   * Optimistically flip a single overlay row's dot to "cached" without a
   * probe — the real-time counterpart to ``refresh()``. Called by
   * static/js/map.js when a lazy tier's toggle-on load has just succeeded
   * (``overlayLoaded[key]`` true), so the resource has now flowed through
   * the SW cache / overlay IDB store and is available offline. No-ops for
   * ``l3`` (``kind: 'uncacheable'`` — network-only, never cached, so its
   * hollow "unavailable" dot stays put) and for any key not in
   * ``OVERLAY_RESOURCES``. The next ``refresh()`` (popover re-open)
   * re-verifies against real cache state.
   *
   * @param {string} key - an ``OVERLAY_RESOURCES`` key.
   * @returns {void}
   */
  function markCached(key) {
    const resource = OVERLAY_RESOURCES[key];
    // Only genuinely-cacheable rows flip green. l3 ('uncacheable') is
    // network-only in sw.js — a successful load doesn't make it available
    // offline — so its hollow dot stays put. Unknown keys are ignored.
    if (!resource || (resource.kind !== 'geojson' && resource.kind !== 'idb')) return;
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
    value: Object.freeze({ refresh, markCached }),
    writable: false,
    configurable: false,
  });
})();
