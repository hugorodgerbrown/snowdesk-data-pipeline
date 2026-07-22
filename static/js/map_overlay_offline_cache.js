/*
 * static/js/map_overlay_offline_cache.js — Offline read cache for the map's
 * favourites / community-reports overlays (SNOW-492).
 *
 * Companion to favourites_offline.js (SNOW-418), same shape: a write-through
 * cache keyed into IndexedDB (``data:map_overlays``, ``db.js`` schema v4) on
 * every successful overlay fetch, and a read-back on a failed one so the map
 * can still install the overlay while offline. Unlike favourites_offline.js
 * this file does not hook DOM/HTMX events itself — ``map.js``'s
 * ``ensureOverlayLoaded`` calls ``putOverlay`` / ``getOverlay`` directly
 * around its own fetch, because the map's overlay toggles are plain fetch()
 * calls, not HTMX swaps.
 *
 * Storage shape
 * -------------
 * One row per resource: ``{ key, geojson, cached_at }``, keyed by
 * ``resource`` (``'favourites'`` or ``'community_reports'``). Expiry is a
 * caller concern, not enforced here — favourites never expire; community
 * reports apply the existing 48h age-opacity horizon at read-back time (see
 * ``withCommunityReportsAgeOpacity`` in map.js), so ``cached_at`` is recorded
 * only for observability, not as a store-level cutoff.
 *
 * No exports beyond ``window.pwaMapOverlayCache`` — every call site in
 * map.js reaches this via that global.
 */

(function () {
  'use strict';

  const STORE = 'data:map_overlays';

  /**
   * True when ``window.pwaDb`` is present and the app is not in the
   * terminal Reset Required state. Every DB access in this file is guarded
   * by this check first, mirroring favourites_offline.js.
   *
   * @returns {boolean}
   */
  function dbReady() {
    return (
      typeof window.pwaDb === 'object' && !window.pwaDb.isResetRequired()
    );
  }

  /**
   * Write-through: cache the given GeoJSON payload for ``resource``. Best
   * effort — never throws, so a broken cache write can't break the overlay
   * fetch it rides on.
   *
   * @param {string} resource - ``'favourites'`` or ``'community_reports'``
   * @param {object} geojson - the fetched FeatureCollection
   * @returns {Promise<void>}
   */
  async function putOverlay(resource, geojson) {
    if (!dbReady() || !resource || !geojson) return;
    try {
      await window.pwaDb.put(STORE, {
        key: resource,
        geojson,
        cached_at: new Date().toISOString(),
      });
    } catch (_e) {
      // Non-fatal — the cache is best-effort.
    }
  }

  /**
   * Read back the cached GeoJSON payload for ``resource``, or ``null`` if
   * nothing is cached (or the DB isn't ready).
   *
   * @param {string} resource - ``'favourites'`` or ``'community_reports'``
   * @returns {Promise<object | null>}
   */
  async function getOverlay(resource) {
    if (!dbReady() || !resource) return null;
    try {
      const record = await window.pwaDb.get(STORE, resource);
      return (record && record.geojson) || null;
    } catch (_e) {
      return null;
    }
  }

  Object.defineProperty(window, 'pwaMapOverlayCache', {
    value: Object.freeze({ putOverlay, getOverlay }),
    writable: false,
    configurable: false,
  });
})();
