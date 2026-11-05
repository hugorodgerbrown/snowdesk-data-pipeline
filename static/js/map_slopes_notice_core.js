/*
 * static/js/map_slopes_notice_core.js — when the map says the bulletin's
 * slopes have taken over (SNOW-1378).
 *
 * Since SNOW-1377 the problem-terrain layer ("Sync with map terrain") is on
 * by default: from z12, in Switzerland, it paints the slopes the day's
 * bulletin describes, and each painted region's fill drops out. The map's
 * surface changes by itself as a reader zooms past z12, so the first time
 * a device's map settles there, a card says what has changed and names the
 * marks (Hugo, 2 Nov 2026). This module decides when; the DOM half,
 * static/js/map_slopes_notice.js, reads the map and shows the card.
 *
 * The card is shown once per device. It is marked seen at the moment it is
 * shown, not when it is answered, so a reload never brings it back. Where
 * storage cannot be read or written it is never shown: a device that
 * cannot remember the card would be shown it on every visit.
 *
 * Exports (frozen `self.pwaSlopesNoticeCore`):
 *
 *   MIN_ZOOM / TERRAIN_COUNTRIES / SEEN_KEY
 *   eligible(input)        whether the map's state calls for the card
 *   hasBeenSeen(storage)   whether this device has been shown it
 *   claim(input, storage)  eligible, unseen, and now marked seen
 */

// @ts-check

(function () {
  'use strict';

  /** Where the layer starts to paint (bulletin_terrain_core.js, map.js). */
  const MIN_ZOOM = 12;
  /** Countries the terrain-class tiles cover (apps/map/problem_terrain.py). */
  const TERRAIN_COUNTRIES = Object.freeze(['CH']);
  /** The device's record that the card has been shown. */
  const SEEN_KEY = 'snowdesk.map.slopes_notice_seen';
  const SEEN = 'seen';

  /**
   * @typedef {Object} NoticeInput
   * @property {number} zoom The camera's zoom once the move has ended.
   * @property {boolean} slopesOn Whether the bulletin's slopes are drawn
   *   (`window.pwaBulletinTerrain.isEnabled()`).
   * @property {boolean} filterOn Whether Steep terrain is on; it takes
   *   precedence, so the bulletin's slopes are not drawn.
   * @property {?string} [country] ISO-2 country of the region at the centre.
   * @property {boolean} sheetOpen Whether an object sheet is open.
   * @property {boolean} [overlayOpen] Whether another map overlay is open —
   *   showing the card would close it under the reader.
   * @property {boolean} tourRunning Whether a tour or walkthrough is running.
   */

  /**
   * @typedef {Object} NoticeStorage
   * @property {function(string): ?string} getItem
   * @property {function(string, string): void} setItem
   * @property {function(string): void} [removeItem]
   */

  /**
   * Whether the map's state calls for the card: close enough for the
   * slopes to be painted, the slopes drawn and not replaced by Steep
   * terrain, over a country the tiles cover, and nothing else holding the
   * screen.
   *
   * @param {NoticeInput} input
   * @returns {boolean}
   */
  function eligible(input) {
    if (!(input.zoom >= MIN_ZOOM)) return false;
    if (!input.slopesOn || input.filterOn) return false;
    if (!TERRAIN_COUNTRIES.includes(input.country || '')) return false;
    return !input.sheetOpen && !input.overlayOpen && !input.tourRunning;
  }

  /**
   * Whether this device has been shown the card. Storage that throws, or
   * none at all, counts as seen: it could not record the showing either.
   *
   * @param {?NoticeStorage} storage
   * @returns {boolean}
   */
  function hasBeenSeen(storage) {
    if (!storage) return true;
    try {
      return storage.getItem(SEEN_KEY) !== null;
    } catch (_) {
      return true;
    }
  }

  /**
   * Record that the card has been shown.
   *
   * @param {?NoticeStorage} storage
   * @returns {boolean} Whether the record was written.
   */
  function markSeen(storage) {
    if (!storage) return false;
    try {
      storage.setItem(SEEN_KEY, SEEN);
      return true;
    } catch (_) {
      return false;
    }
  }

  /**
   * Take back a record whose showing never reached the screen: the reader
   * closed it or tapped the map while its key was still loading, so the
   * device has not in fact been shown the card.
   *
   * @param {?NoticeStorage} storage
   * @returns {void}
   */
  function unmarkSeen(storage) {
    if (!storage || !storage.removeItem) return;
    try {
      storage.removeItem(SEEN_KEY);
    } catch (_) {
      // Nothing to take back: the next claim reads the record as before.
    }
  }

  /**
   * Whether to show the card now. True only when the map calls for it,
   * the device has not been shown it, and the showing has been recorded —
   * so a true answer has already marked it seen.
   *
   * @param {NoticeInput} input
   * @param {?NoticeStorage} storage
   * @returns {boolean}
   */
  function claim(input, storage) {
    if (!eligible(input)) return false;
    if (hasBeenSeen(storage)) return false;
    return markSeen(storage);
  }

  self.pwaSlopesNoticeCore = Object.freeze({
    MIN_ZOOM,
    TERRAIN_COUNTRIES,
    SEEN_KEY,
    eligible,
    hasBeenSeen,
    claim,
    unmarkSeen,
  });
})();
