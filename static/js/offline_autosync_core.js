// @ts-check
/*
 * static/js/offline_autosync_core.js — what a sync fetches, and which
 * downloaded areas are due a background sync (SNOW-1416).
 *
 * Two pure decisions, shared by every sync on the map page:
 *
 *   - `contentDelta` — which of an area's content urls to fetch. A sync
 *     never re-downloads what the device holds: a url missing from the
 *     area's bucket is fetched; a held PERISHABLE url (bulletins, sheets,
 *     ratings, weather — every one dated by day) is refetched only when
 *     the area is stale; a held PERMANENT url (a trip's page and route) is
 *     never refetched. Because the perishable urls carry their day, a new
 *     day's bulletins are new urls and are always fetched.
 *   - `dueAreas` — which areas a background sync should refresh now: the
 *     stale ones whose last attempt is older than the same interval, so a
 *     failing area is not retried on every trigger.
 *
 * "Stale" is one rule for both: no `contentAt`, a `contentAt` older than
 * `CONTENT_FRESH_MS`, or `contentIncomplete` set. One hour, Hugo's call
 * (4 Nov 2026), and one constant to tune.
 *
 * No DOM, no storage, no clock of its own: `now` is passed in.
 * docs/decisions/sync-fetches-only-what-changed.md has the why.
 */

(function () {
  'use strict';

  const CONTENT_FRESH_MS = 60 * 60 * 1000;

  /**
   * Milliseconds since an ISO instant, or Infinity when it cannot be read.
   *
   * @param {unknown} value
   * @param {Date} now
   * @returns {number}
   */
  function ageMs(value, now) {
    if (typeof value !== 'string' || !value) return Infinity;
    const at = new Date(value).getTime();
    if (Number.isNaN(at)) return Infinity;
    return now.getTime() - at;
  }

  /**
   * Whether an area's content needs refetching.
   *
   * @param {{contentAt?: string, contentIncomplete?: boolean}|null|undefined} record
   * @param {Date} now
   * @param {number} [freshMs]
   * @returns {boolean}
   */
  function isStale(record, now, freshMs) {
    if (!record) return true;
    if (record.contentIncomplete) return true;
    const window_ = Number.isFinite(freshMs) ? Number(freshMs) : CONTENT_FRESH_MS;
    return !(ageMs(record.contentAt, now) < window_);
  }

  /**
   * The urls a sync should fetch for one area.
   *
   * @param {string[]} urls The whole content plan, in its stage order.
   * @param {Set<string>} held The urls already in the area's bucket.
   * @param {Set<string>} permanent The urls that never change once held.
   * @param {{contentAt?: string, contentIncomplete?: boolean}|null|undefined} record
   * @param {Date} now
   * @param {number} [freshMs]
   * @returns {string[]} A subset of `urls`, in the same order.
   */
  function contentDelta(urls, held, permanent, record, now, freshMs) {
    const stale = isStale(record, now, freshMs);
    return (urls || []).filter((url) => {
      if (!held.has(url)) return true;
      if (permanent.has(url)) return false;
      return stale;
    });
  }

  /**
   * The areas a background sync should refresh now, in the given order.
   *
   * @param {Array<{id: string, record: Object|null|undefined}>} areas
   * @param {Object<string, string>|null|undefined} attempts Area id → the
   *   ISO instant of its last attempt, success or failure.
   * @param {Date} now
   * @param {number} [freshMs]
   * @returns {string[]}
   */
  function dueAreas(areas, attempts, now, freshMs) {
    const window_ = Number.isFinite(freshMs) ? Number(freshMs) : CONTENT_FRESH_MS;
    const tried = attempts && typeof attempts === 'object' ? attempts : {};
    return (areas || [])
      .filter((area) => area && area.id)
      .filter((area) => isStale(/** @type {any} */ (area.record), now, window_))
      .filter((area) => !(ageMs(tried[area.id], now) < window_))
      .map((area) => area.id);
  }

  self.pwaOfflineAutosyncCore = Object.freeze({
    CONTENT_FRESH_MS: CONTENT_FRESH_MS,
    isStale: isStale,
    contentDelta: contentDelta,
    dueAreas: dueAreas,
  });
})();
