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
 * One row per resource: ``{ key, geojson, cached_at, principal }``, keyed by
 * ``resource`` (``'favourites'`` or ``'community_reports'``). Expiry is a
 * caller concern, not enforced here — favourites never expire; community
 * reports apply the existing 48h age-opacity horizon at read-back time (see
 * ``withCommunityReportsAgeOpacity`` in map.js), so ``cached_at`` is recorded
 * only for observability, not as a store-level cutoff.
 *
 * ``principal`` (SNOW-493) partitions every row by the signed-in account —
 * ``window.pwaDb.context().user_id``, normalised to ``null`` for anonymous
 * sessions — so a cached favourites row from one account can never be read
 * back after a different account signs in on the same browser. Every row is
 * stamped uniformly (including ``community_reports``, which has no account
 * binding of its own) so both resources share one code path: anonymous /
 * community rows carry ``principal: null`` on both write and read, and so
 * still match each other.
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
   * The current principal — the ``<meta name="pwa-user-id">`` value read
   * via ``window.pwaDb.context().user_id``, normalised so an empty string /
   * ``undefined`` reads as ``null`` (anonymous). Mirrors
   * ``mutation_queue.js``'s ``_currentPrincipal()``. Guarded end to end:
   * returns ``null`` on any throw, or when ``window.pwaDb`` isn't available.
   *
   * @returns {string|null}
   */
  function _currentPrincipal() {
    try {
      if (typeof window.pwaDb !== 'object') return null;
      const userId = window.pwaDb.context().user_id;
      return userId === undefined || userId === '' ? null : userId;
    } catch (_e) {
      return null;
    }
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
        principal: _currentPrincipal(),
      });
    } catch (_e) {
      // Non-fatal — the cache is best-effort.
    }
  }

  /**
   * Read back the cached GeoJSON payload for ``resource``, or ``null`` if
   * nothing is cached, the DB isn't ready, or the cached row belongs to a
   * different principal than the one currently signed in (SNOW-493) — a
   * row with no ``principal`` at all (written before this fix) never
   * matches, so it can't leak across an account switch either.
   *
   * @param {string} resource - ``'favourites'`` or ``'community_reports'``
   * @returns {Promise<object | null>}
   */
  async function getOverlay(resource) {
    if (!dbReady() || !resource) return null;
    try {
      const record = await window.pwaDb.get(STORE, resource);
      if (!record || !record.geojson) return null;
      if (record.principal !== _currentPrincipal()) return null;
      return record.geojson;
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
