/*
 * static/js/calendar_core.js — Pure, context-free month-grid maths for the
 * map's date picker (SNOW-792).
 *
 * The season scrubber is a ~300px track spanning a seven-month season, so a
 * pixel is about a day and landing on a chosen date is a fiddle. The
 * calendar popup is the precise route to the same commit point; this module
 * is the half of it that has no DOM in it, so the grid maths can be
 * unit-tested directly (tests/js/test_calendar_core.js) rather than through
 * a browser.
 *
 * Attached to ``window`` (not ``self``) — every consumer is a page script,
 * as with scrubber_core.js; there is no service-worker context to share
 * with.
 *
 * Deliberately dependency-free: every export is a pure function of its
 * arguments. The range bounds, the season bounds, the ratings cache and the
 * selected date are all passed in explicitly rather than read from
 * module-scope globals.
 *
 * Dates are handled as ``YYYY-MM-DD`` strings and UTC-midnight timestamps
 * throughout, matching scrubber_core.js. Constructing local-time Dates from
 * a date-only string is what makes a picker show the wrong day either side
 * of a DST boundary, and the whole season window straddles one.
 *
 * ## The reachable range is not the season
 *
 * The picker's bounds and the avalanche season are two different things,
 * and conflating them was this module's first mistake. The map carries
 * weather as well as bulletins, and weather has an answer for a day in
 * September; a picker that stopped at the end of May could not reach it.
 *
 * So the reachable range runs from the earliest day the site holds
 * anything for up to TODAY — the future is the one hard stop, because
 * nothing on the map can answer for a day that has not happened. The
 * season is a HIGHLIGHT inside that range (``inSeason`` on each cell), not
 * a fence around it: it tells you where the bulletins are without refusing
 * the days either side.
 *
 * There is no second, per-day mark for "this day actually has a bulletin".
 * There was one — a dot, driven by the ratings cache — and it went because
 * the thing it distinguished, a gap in the archive inside the season, is an
 * operator's concern and not a visitor's. A visitor who lands on such a day
 * sees an uncoloured map, which is the same answer told once instead of
 * twice.
 *
 * Public API — attached to ``window.pwaCalendarCore``:
 *
 *   monthKeyOf(dateKey)
 *     ``'2026-02-16'`` → ``'2026-02'``. Empty string for anything
 *     unparseable, so a caller can test it without a second guard.
 *   shiftMonth(monthKey, delta)
 *     ``('2026-02', -1)`` → ``'2026-01'``. Wraps the year in both
 *     directions.
 *   clampMonthKey(monthKey, minKey, maxKey)
 *     The month, pulled inside the reachable range if it falls outside it.
 *     This is what stops prev/next walking off into 1970 or next year.
 *   buildMonthGrid(monthKey, opts)
 *     The cells for one month, Monday-first, as a flat array of 7×N
 *     entries. See the function docstring for the cell shape.
 *   earliestKnownDate(ratingsCache, fallbackKey)
 *     The earliest date the ratings cache carries, or ``fallbackKey`` when
 *     it carries nothing usable — the floor the month arrows stop at, and
 *     now the only thing the cache is read for here.
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
   * Pull a month key inside the reachable range.
   *
   * The range is a date range, so its first and last months are partial;
   * clamping is by month, and ``buildMonthGrid`` is what disables the days
   * either side of the exact bounds — in practice only the tail of the
   * current month, since the future is the only hard stop.
   *
   * @param {string} monthKey A ``YYYY-MM`` month key.
   * @param {string} minKey Earliest reachable day as a ``YYYY-MM-DD`` key.
   * @param {string} maxKey Latest reachable day (today) as a key.
   * @returns {string} The clamped month key, or ``''`` if unparseable.
   */
  function clampMonthKey(monthKey, minKey, maxKey) {
    if (typeof monthKey !== 'string' || !MONTH_KEY_RE.test(monthKey)) return '';
    var first = monthKeyOf(minKey);
    var last = monthKeyOf(maxKey);
    if (first && monthKey < first) return first;
    if (last && monthKey > last) return last;
    return monthKey;
  }

  /**
   * The earliest date the ratings cache knows about.
   *
   * This is the floor the month arrows stop at. It comes from the cache
   * rather than from the server because the alternative — asking the DB
   * for its earliest row — is a query on the home page's critical path,
   * and the home page has a query-count budget (docs/query-counts.md).
   * The cache is already fetched for the scrubber, so this costs nothing.
   *
   * It is a floor on PAGING, not on selection: every day from here to
   * today is pickable, including the ones with no ratings, because the
   * map has weather to show for them.
   *
   * @param {Object|null} ratingsCache Date key → per-region ratings frame.
   * @param {string} fallbackKey Returned when the cache has no usable key.
   * @returns {string} A ``YYYY-MM-DD`` date key.
   */
  function earliestKnownDate(ratingsCache, fallbackKey) {
    var earliest = '';
    if (ratingsCache && typeof ratingsCache === 'object') {
      for (var key of Object.keys(ratingsCache)) {
        if (!DATE_KEY_RE.test(key)) continue;
        if (!earliest || key < earliest) earliest = key;
      }
    }
    if (!earliest) return fallbackKey || '';
    // The season may start before the first day carrying a rating — an
    // early-November window over an archive that begins mid-month. Take
    // whichever is earlier so the highlight is never cut off by the floor.
    if (fallbackKey && fallbackKey < earliest) return fallbackKey;
    return earliest;
  }

  /**
   * Build one month's grid of day cells, Monday-first.
   *
   * The array is ALWAYS 42 cells — six weeks — however few the month needs.
   * Leading and trailing padding cells (``{blank: true}``) make up the
   * difference, so a caller can lay it out as a seven-column grid without
   * doing any offset arithmetic of its own.
   *
   * Six rather than "a whole number of weeks" because the panel is
   * bottom-anchored: a five-row month is shorter than a six-row one, and
   * paging between them moved the popup's top edge and everything in it.
   * A constant height costs one blank row on some months and makes the
   * arrows a control you can press twice without the target moving.
   *
   * Six is always enough: the worst case is a 31-day month starting on a
   * Sunday — six leading blanks plus 31 days is 37 cells, still inside 42.
   *
   * A day cell is::
   *
   *     {
   *       blank: false,
   *       dateKey: '2026-02-16',
   *       day: 16,               // for the visible label
   *       selectable: true,      // inside [min, max] — i.e. not the future
   *       inSeason: true,        // inside the avalanche season: a HIGHLIGHT
   *       selected: false,       // the date the map is currently showing
   *       isToday: false,
   *     }
   *
   * ``inSeason`` is deliberately separate from ``selectable`` rather than
   * folded into it. A day outside the season is still a day the map has
   * weather for — refusing it was the bug this shape exists to prevent.
   * Only the future is unselectable.
   *
   * @param {string} monthKey A ``YYYY-MM`` month key.
   * @param {Object} opts Grid inputs.
   * @param {string} [opts.min] Earliest reachable day ``YYYY-MM-DD``.
   * @param {string} [opts.max] Latest reachable day (today) ``YYYY-MM-DD``.
   * @param {string} [opts.seasonStart] Season start ``YYYY-MM-DD``.
   * @param {string} [opts.seasonEnd] Season end ``YYYY-MM-DD``.
   * @param {string} [opts.selected] The currently-showing date, or ''.
   * @param {string} [opts.today] Today's date key, for the today marker.
   * @returns {Array<Object>} Cells, length a multiple of 7.
   */
  function buildMonthGrid(monthKey, opts) {
    var options = opts || {};
    if (typeof monthKey !== 'string' || !MONTH_KEY_RE.test(monthKey)) return [];

    var min = options.min || '';
    var max = options.max || '';
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
      cells.push({
        blank: false,
        dateKey: dateKey,
        day: i + 1,
        selectable: (!min || dateKey >= min) && (!max || dateKey <= max),
        inSeason:
          (!seasonStart || dateKey >= seasonStart) && (!seasonEnd || dateKey <= seasonEnd),
        selected: dateKey === selected,
        isToday: dateKey === today,
      });
    }

    // Pad to a constant six weeks — see the docstring for why the height
    // must not depend on the month.
    while (cells.length < 42) cells.push({ blank: true });

    return cells;
  }

  window.pwaCalendarCore = Object.freeze({
    monthKeyOf: monthKeyOf,
    shiftMonth: shiftMonth,
    clampMonthKey: clampMonthKey,
    earliestKnownDate: earliestKnownDate,
    buildMonthGrid: buildMonthGrid,
  });
})();
