/*
 * static/js/basemap_area_removal.js — remove downloaded areas from this
 * device: the one implementation (SNOW-1308).
 *
 * Until SNOW-1308 this was `evictBasemapAreas` inside
 * `map_basemap_downloads.js`, which only the map loads. The `/offline/`
 * panels needed the same Remove — for a recorded area and for an orphaned
 * bucket no record accounts for (SNOW-1138) — and a second copy of the
 * removal on that page would be two answers to "what does Remove delete"
 * that could drift. So it moved here, and both surfaces load this file:
 *
 *   - the map's Manage downloads sheet and its budget eviction, through
 *     `map_basemap_downloads.js`'s `evictBasemapAreas`, which keeps the two
 *     map-only follow-ups (the downloaded-areas overlay and the
 *     `snowdesk:downloads-changed` event) and delegates the rest here;
 *   - the `/offline/` panels (`offline_journey.js`), which re-read storage
 *     through the audit afterwards rather than trusting this to have
 *     worked.
 *
 * What one removal does, best-effort per area so one failure never aborts
 * the rest:
 *
 *   1. deletes each area's pinned Cache Storage bucket;
 *   2. removes its record from `basemap.customAreas` or `basemap.regions`
 *      where one exists — an orphaned bucket has none, and is simply
 *      deleted;
 *   3. tells the service worker its memoised pinned-bucket list is stale
 *      (SNOW-613), or the worker would recreate the deleted bucket empty.
 *
 * The record writes are one read and one write per record TYPE, never one
 * per id: both rows are whole arrays, and a per-id read-filter-write run in
 * parallel is a lost update the moment two ids of one type go in one call
 * (SNOW-635 review).
 *
 * Needs `pwaBasemapDownloadCore` (the bucket-id family) and `pwaDb`;
 * `pwaBasemapAreas` for the custom-area row, whose reader owns the legacy
 * migration. `available()` says whether they are all here, so a host that
 * lacks them (static/offline.html loads none) offers no Remove rather than
 * a control bound to nothing.
 */
(function () {
  'use strict';

  /**
   * Whether this page has what a removal needs.
   *
   * @returns {boolean}
   */
  function available() {
    var core = self.pwaBasemapDownloadCore;
    return !!(
      core &&
      typeof core.pinnedCacheName === 'function' &&
      self.pwaDb &&
      self.pwaBasemapAreas &&
      self.caches
    );
  }

  /**
   * Delete whole areas — each one's pinned bucket AND its record.
   *
   * @param {string[]} areaIds Bucket ids, as minted (`region-CH-4115`,
   *   `custom-…`).
   * @param {{onBucketDeleted?: function(string): void}} [options]
   *   `onBucketDeleted` runs once per id after its bucket delete was
   *   attempted — the map forgets its byte measurement of the bucket there.
   * @returns {Promise<boolean>} False when there was nothing to do or no
   *   core to do it with; true once every step has been attempted.
   */
  async function evict(areaIds, options) {
    var core = self.pwaBasemapDownloadCore;
    var ids = Array.isArray(areaIds) ? areaIds : [];
    var hooks = options || {};
    if (!core || !ids.length) return false;

    await Promise.all(
      ids.map(async function (areaId) {
        try {
          await self.caches.delete(core.pinnedCacheName(areaId));
        } catch (_e) {
          // Best-effort.
        }
        if (typeof hooks.onBucketDeleted === 'function') hooks.onBucketDeleted(areaId);
      }),
    );

    // SNOW-635: `core.isCustomAreaId` — a custom area's own bucket-id
    // FAMILY, not the single legacy `CUSTOM_AREA_ID`.
    var customIds = new Set(
      ids.filter(function (id) {
        return core.isCustomAreaId(id);
      }),
    );
    var regionIds = new Set(
      ids.filter(function (id) {
        return !core.isCustomAreaId(id);
      }),
    );
    var db = self.pwaDb;

    try {
      if (customIds.size && db && self.pwaBasemapAreas) {
        var existing = (await self.pwaBasemapAreas.readCustomAreas()) || [];
        var next = existing.filter(function (entry) {
          return !entry || !customIds.has(entry.id);
        });
        if (next.length !== existing.length) {
          // Always a `put`, even for `[]` — an absent key is what the
          // custom-area reader treats as "try the legacy migration".
          await db.put('meta:app', {
            key: self.pwaBasemapAreas.CUSTOM_AREAS_KEY,
            value: next,
          });
        }
      }
    } catch (_e) {
      // Best-effort — a stale record with no bucket behind it is
      // evictable-first the next time budget planning runs.
    }

    try {
      if (regionIds.size && db) {
        var row = await db.get('meta:app', 'basemap.regions');
        var regions = Array.isArray(row && row.value) ? row.value : [];
        var kept = regions.filter(function (entry) {
          return !(entry && regionIds.has(core.areaIdForRegion(entry.region_id)));
        });
        if (kept.length !== regions.length) {
          await db.put('meta:app', { key: 'basemap.regions', value: kept });
        }
      }
    } catch (_e) {
      // Best-effort — see above.
    }

    // SNOW-613: the worker has no other way to learn about a page-side
    // deletion, and a stale name there would be handed to `caches.open`,
    // recreating the bucket just deleted as an empty one.
    try {
      var controller = self.navigator && self.navigator.serviceWorker
        ? self.navigator.serviceWorker.controller
        : null;
      if (controller) controller.postMessage({ type: 'pinned-buckets-changed' });
    } catch (_e) {
      // Best-effort.
    }
    return true;
  }

  self.pwaBasemapAreaRemoval = Object.freeze({
    available: available,
    evict: evict,
  });
})();
