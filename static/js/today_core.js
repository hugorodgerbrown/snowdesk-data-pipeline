// @ts-check
/*
 * static/js/today_core.js — can the map go back to today? (SNOW-1165)
 *
 * The map can show any day of the season, and most people, most of the time,
 * are on today. Its date control carries a "Today" half that is always shown
 * and enabled only when there is somewhere to go back from; this file holds
 * that one decision, so it is tested without a DOM:
 *
 *   - ``canGoToToday`` — true when the page knows today and the map is on a
 *     different day, or on no day at all. With no today to go to (no
 *     ``data-today``) there is nothing to press for.
 *
 * "Today" is the page's ``#season-scrubber[data-today]``, never the browser
 * clock (SNOW-793). A cached page rendered yesterday therefore calls
 * yesterday today; that case is the stale-day notice's (SNOW-920), not this.
 */
(function () {
  'use strict';

  /**
   * Whether "Today" has anywhere to take the map.
   *
   * @param {string | null | undefined} date The day the map is showing.
   * @param {string | null | undefined} today The page's today.
   * @returns {boolean}
   */
  function canGoToToday(date, today) {
    if (!today) return false;
    return date !== today;
  }

  window.pwaTodayCore = Object.freeze({
    canGoToToday: canGoToToday,
  });
})();
