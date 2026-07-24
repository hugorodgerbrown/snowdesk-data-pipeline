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
 * basemapPickerInit calls ``refresh()`` from its ``setMenuOpen``), so the
 * dots need not be live while the menu is closed.
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
 *                          classification) — always resolves to
 *                          "uncached", without a wasted probe.
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
 */

(function () {
  'use strict';

  const IDB_STORE = 'data:map_overlays';
  const BASEMAP_CACHE_PREFIX = 'snowdesk-basemap-';

  const CACHED_LABEL = 'Available offline';
  const UNCACHED_LABEL = 'Not cached — view online first';
  const BASEMAP_CACHED_LABEL = 'Cached tiles available offline (browsed areas only)';
  const BASEMAP_UNCACHED_LABEL = 'No offline map tiles yet';

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
    l3: Object.freeze({ kind: 'uncached' }),
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
   * The basemap section's single ``.sync-dot``, or ``null`` if the
   * caption row isn't rendered.
   *
   * @returns {Element | null}
   */
  function _basemapDot() {
    return document.querySelector('#basemap-sync-status .sync-dot');
  }

  /**
   * Paint a resolved cached/uncached state onto a dot: ``data-sync-state``
   * plus an accessible name (``role="img"`` + ``aria-label``) and a
   * ``title`` tooltip. Dots start ``aria-hidden="true"`` (per the
   * server-rendered ``unknown`` state); resolving to a real state makes
   * the dot's accessible name available too.
   *
   * @param {Element | null} dot
   * @param {boolean} cached
   * @param {string} cachedLabel
   * @param {string} uncachedLabel
   * @returns {void}
   */
  function _applyState(dot, cached, cachedLabel, uncachedLabel) {
    if (!dot) return;
    const label = cached ? cachedLabel : uncachedLabel;
    dot.dataset.syncState = cached ? 'cached' : 'uncached';
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
   * True when a ``snowdesk-basemap-*`` cache exists and holds at least
   * one entry. The cache is discovered by prefix (not a hardcoded
   * version) so a BASEMAP_CACHE version bump in sw.js never breaks this
   * probe. Never throws.
   *
   * @returns {Promise<boolean>}
   */
  async function _probeBasemap() {
    try {
      const names = await caches.keys();
      const basemapCacheName = names.find((name) => name.startsWith(BASEMAP_CACHE_PREFIX));
      if (!basemapCacheName) return false;
      const cache = await caches.open(basemapCacheName);
      const keys = await cache.keys();
      return keys.length > 0;
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

      if (resource.kind === 'uncached') {
        // l3 is network-only in sw.js — never cached, never probed.
        _applyState(dot, false, CACHED_LABEL, UNCACHED_LABEL);
        continue;
      }

      const probe =
        resource.kind === 'geojson'
          ? _probeGeoJson(resource.path)
          : _probeIdbRow(resource.key);
      tasks.push(
        probe
          .then((cached) => _applyState(dot, cached, CACHED_LABEL, UNCACHED_LABEL))
          .catch(() => _applyState(dot, false, CACHED_LABEL, UNCACHED_LABEL)),
      );
    }

    const basemapDot = _basemapDot();
    if (basemapDot) {
      tasks.push(
        _probeBasemap()
          .then((cached) =>
            _applyState(basemapDot, cached, BASEMAP_CACHED_LABEL, BASEMAP_UNCACHED_LABEL),
          )
          .catch(() =>
            _applyState(basemapDot, false, BASEMAP_CACHED_LABEL, BASEMAP_UNCACHED_LABEL),
          ),
      );
    }

    await Promise.all(tasks);
  }

  Object.defineProperty(window, 'pwaLayerSyncStatus', {
    value: Object.freeze({ refresh }),
    writable: false,
    configurable: false,
  });
})();
