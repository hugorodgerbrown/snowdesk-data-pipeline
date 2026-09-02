/*
 * static/js/calendar_core.js — Pure, context-free month-grid maths for the
 * scrubber's date picker (SNOW-792).
 *
 * The scrubber is a ~300px track spanning a seven-month season, so a pixel
 * is about a day and landing on a chosen date is a fiddle. The calendar
 * popup is the precise route to the same commit point; this module is the
 * half of it that has no DOM in it, so the grid maths can be unit-tested
 * directly (tests/js/test_calendar_core.js) rather than through a browser.
 *
 * Attached to ``window`` (not ``self``) — every consumer is a page script,
 * as with scrubber_core.js; there is no service-worker context to share
 * with.
 *
 * Deliberately dependency-free: every export is a pure function of its
 * arguments. Season bounds, the ratings cache and the selected date are all
 * passed in explicitly rather than read from module-scope globals.
 *
 * Dates are handled as ``YYYY-MM-DD`` strings and UTC-midnight timestamps
 * throughout, matching scrubber_core.js. Constructing local-time Dates from
 * a date-only string is what makes a picker show the wrong day either side
 * of a DST boundary, and the whole season window straddles one.
 *
 * Public API — attached to ``window.pwaCalendarCore``:
 *
 *   monthKeyOf(dateKey)
 *     ``'2026-02-16'`` → ``'2026-02'``. Empty string for anything
 *     unparseable, so a caller can test it without a second guard.
 *   shiftMonth(monthKey, delta)
 *     ``('2026-02', -1)`` → ``'2026-01'``. Wraps the year in both
 *     directions.
 *   clampMonthKey(monthKey, seasonStartKey, seasonEndKey)
 *     The month, pulled inside the season window if it falls outside it.
 *     This is what stops prev/next walking off into an empty August.
 *   buildMonthGrid(monthKey, opts)
 *     The cells for one month, Monday-first, as a flat array of 7×N
 *     entries. See the function docstring for the cell shape.
 *   selectableDates(ratingsCache, seasonStartKey, seasonEndKey)
 *     The set of date keys the calendar will let you pick: every key in
 *     the cache that falls inside the season window.
 */

(function () {
  'use strict';

  var MS_PER_DAY = 86400000;
  var DATE_KEY_RE = /^\d{4}-\d{2}-\d{2}$/;
  var MONTH_KEY_RE = /^\d{4}-\d{2}$/;

  /**
   * Format a UTC timestamp as a ``YYYY-MM-DD`` date key.
   *
   * @param {number} ms Epoch milliseconds.
   * @returns {string}
   */
  function keyFromMs(ms) {
    var d = new Date(ms);
    var y = d.getUTCFullYear();
    var m = String(d.getUTCMonth() + 1).padStart(2, '0');
    var day = String(d.getUTCDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  /**
   * The month a date key belongs to.
   *
   * Works on the string rather than parsing to a Date: the input is
   * already the canonical form, and a substring cannot drift a day the way
   * a timezone-sensitive parse can.
   *
   * @param {string} dateKey A ``YYYY-MM-DD`` date key.
   * @returns {string} A ``YYYY-MM`` month key, or ``''`` if unparseable.
   */
  function monthKeyOf(dateKey) {
    if (typeof dateKey !== 'string' || !DATE_KEY_RE.test(dateKey)) return '';
    return dateKey.slice(0, 7);
  }

  /**
   * Step a month key forwards or backwards, wrapping the year.
   *
   * @param {string} monthKey A ``YYYY-MM`` month key.
   * @param {number} delta Months to move; negative goes back.
   * @returns {string} The shifted month key, or ``''`` if unparseable.
   */
  function shiftMonth(monthKey, delta) {
    if (typeof monthKey !== 'string' || !MONTH_KEY_RE.test(monthKey)) return '';
    var year = parseInt(monthKey.slice(0, 4), 10);
    var month = parseInt(monthKey.slice(5, 7), 10) - 1 + (delta || 0);
    // Floor-divide so a negative month index carries into the year
    // correctly — ``-1`` is December of the previous year, not month -1.
    year += Math.floor(month / 12);
    month = ((month % 12) + 12) % 12;
    return year + '-' + String(month + 1).padStart(2, '0');
  }

  /**
   * Pull a month key inside the season window.
   *
   * The season is a date range, so its first and last months are partial;
   * clamping is by month, and ``buildMonthGrid`` is what disables the days
   * either side of the exact bounds.
   *
   * @param {string} monthKey A ``YYYY-MM`` month key.
   * @param {string} seasonStartKey Season start as a ``YYYY-MM-DD`` key.
   * @param {string} seasonEndKey Season end as a ``YYYY-MM-DD`` key.
   * @returns {string} The clamped month key, or ``''`` if unparseable.
   */
  function clampMonthKey(monthKey, seasonStartKey, seasonEndKey) {
    if (typeof monthKey !== 'string' || !MONTH_KEY_RE.test(monthKey)) return '';
    var first = monthKeyOf(seasonStartKey);
    var last = monthKeyOf(seasonEndKey);
    if (first && monthKey < first) return first;
    if (last && monthKey > last) return last;
    return monthKey;
  }

  /**
   * The set of dates the calendar will let a visitor pick.
   *
   * The ratings cache is the same payload the scrubber snaps to, so the
   * calendar offers exactly the days the map can actually paint. Keys
   * outside the season window are dropped rather than trusted: the cache
   * is a merged, multi-country object and nothing guarantees every key in
   * it sits inside the window this scrubber was rendered for.
   *
   * @param {Object|null} ratingsCache Date key → per-region ratings frame.
   * @param {string} seasonStartKey Season start as a ``YYYY-MM-DD`` key.
   * @param {string} seasonEndKey Season end as a ``YYYY-MM-DD`` key.
   * @returns {Set<string>} The selectable date keys.
   */
  function selectableDates(ratingsCache, seasonStartKey, seasonEndKey) {
    var out = new Set();
    if (!ratingsCache || typeof ratingsCache !== 'object') return out;
    for (var key of Object.keys(ratingsCache)) {
      if (!DATE_KEY_RE.test(key)) continue;
      if (seasonStartKey && key < seasonStartKey) continue;
      if (seasonEndKey && key > seasonEndKey) continue;
      out.add(key);
    }
    return out;
  }

  /**
   * Build one month's grid of day cells, Monday-first.
   *
   * The array is always a whole number of weeks: leading and trailing
   * padding cells (``{blank: true}``) fill the first and last rows, so a
   * caller can lay it out as a seven-column grid without doing any offset
   * arithmetic of its own.
   *
   * A day cell is::
   *
   *     {
   *       blank: false,
   *       dateKey: '2026-02-16',
   *       day: 16,               // for the visible label
   *       selectable: true,      // in season AND present in the data set
   *       selected: false,       // the date the map is currently showing
   *       isToday: false,
   *     }
   *
   * ``selectable`` folds together the two reasons a day cannot be picked —
   * outside the season window, or inside it with no bulletin data — because
   * the control treats them the same way. They are not the same fact, but
   * making the caller re-derive which one applies would put the season
   * bounds in two places.
   *
   * @param {string} monthKey A ``YYYY-MM`` month key.
   * @param {Object} opts Grid inputs.
   * @param {Set<string>} [opts.available] Date keys carrying data.
   * @param {string} [opts.seasonStart] Season start ``YYYY-MM-DD``.
   * @param {string} [opts.seasonEnd] Season end ``YYYY-MM-DD``.
   * @param {string} [opts.selected] The currently-showing date, or ''.
   * @param {string} [opts.today] Today's date key, for the today marker.
   * @returns {Array<Object>} Cells, length a multiple of 7.
   */
  function buildMonthGrid(monthKey, opts) {
    var options = opts || {};
    if (typeof monthKey !== 'string' || !MONTH_KEY_RE.test(monthKey)) return [];

    var available = options.available || null;
    var seasonStart = options.seasonStart || '';
    var seasonEnd = options.seasonEnd || '';
    var selected = options.selected || '';
    var today = options.today || '';

    var firstMs = Date.parse(monthKey + '-01T00:00:00Z');
    if (!Number.isFinite(firstMs)) return [];
    var nextMonthMs = Date.parse(shiftMonth(monthKey, 1) + '-01T00:00:00Z');
    var dayCount = Math.round((nextMonthMs - firstMs) / MS_PER_DAY);

    // getUTCDay() is Sunday-first (0..6); the grid is Monday-first, so
    // Sunday becomes 6 and every other day shifts down one.
    var leading = (new Date(firstMs).getUTCDay() + 6) % 7;

    var cells = [];
    var i;
    for (i = 0; i < leading; i++) cells.push({ blank: true });

    for (i = 0; i < dayCount; i++) {
      var dateKey = keyFromMs(firstMs + i * MS_PER_DAY);
      var inSeason =
        (!seasonStart || dateKey >= seasonStart) && (!seasonEnd || dateKey <= seasonEnd);
      cells.push({
        blank: false,
        dateKey: dateKey,
        day: i + 1,
        selectable: inSeason && (available === null || available.has(dateKey)),
        selected: dateKey === selected,
        isToday: dateKey === today,
      });
    }

    // Pad the final row out to a whole week.
    while (cells.length % 7 !== 0) cells.push({ blank: true });

    return cells;
  }

  window.pwaCalendarCore = Object.freeze({
    monthKeyOf: monthKeyOf,
    shiftMonth: shiftMonth,
    clampMonthKey: clampMonthKey,
    selectableDates: selectableDates,
    buildMonthGrid: buildMonthGrid,
  });
})();
