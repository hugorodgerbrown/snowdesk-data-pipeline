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
 * So the reachable range runs from the earliest day the site holds anything
 * for to the latest — both read off the ratings cache
 * (``earliestKnownDate`` / ``latestKnownDate``). The hard stop is what the
 * data covers, not the clock: SNOW-927 made the ceiling follow the payload
 * the way the floor always had, because the evening bulletin forecasts
 * TOMORROW and a picker fenced at today could not open the day it had
 * already been sent. Off season the ceiling stays at today, which is the
 * later of the two. The season is a HIGHLIGHT inside that range
 * (``inSeason`` on each cell), not a fence around it: it tells you where the
 * bulletins are without refusing the days either side.
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
 *     it carries nothing usable — the floor the month arrows stop at.
 *   latestKnownDate(ratingsCache, fallbackKey)
 *     SNOW-927: the ceiling, mirroring the floor above. The latest date the
 *     cache carries, or ``fallbackKey`` (today) when that is later — which
 *     off season it is, and must be. Capped at ``MAX_FORWARD_DAYS`` past
 *     the fallback.
 *   MAX_FORWARD_DAYS
 *     The cap above, exported so the scrubber's boot path can assume the
 *     same ceiling before the ratings payload has resolved.
 */

// @ts-check

(function () {
  'use strict';

  var MS_PER_DAY = 86400000;

  // SNOW-927: how far past today ``latestKnownDate`` may ever reach. See its
  // docstring — a bad-data guard, not a product rule. Providers publish one
  // day ahead, so three is clear of the real window and nowhere near a
  // plausible typo. Exported because the scrubber's boot path has to make the
  // same optimistic assumption before the payload has resolved, and the two
  // must not drift.
  var MAX_FORWARD_DAYS = 3;

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
   * month the ceiling falls in.
   *
   * @param {string} monthKey A ``YYYY-MM`` month key.
   * @param {string} minKey Earliest reachable day as a ``YYYY-MM-DD`` key.
   * @param {string} maxKey Latest reachable day as a key — today, or the
   *   last day the payload covers when that is later (SNOW-927).
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
   * The latest date the ratings cache knows about.
   *
   * SNOW-927: the ceiling, and the mirror of ``earliestKnownDate`` above —
   * same cache, same reasoning about why it is read here rather than
   * queried, opposite end.
   *
   * It exists because the ceiling used to be ``today``, full stop, while the
   * floor had always come from the data. Providers publish twice a day and
   * the evening issue forecasts TOMORROW
   * (``apps/bulletins/services/day_rating.py``'s
   * ``target_day_for_valid_from``), so from about 16:00 the cache carries a
   * day the picker refused to offer — the one a person packing for the
   * morning actually wants.
   *
   * ``fallbackKey`` is today, and **taking whichever is LATER is load-bearing
   * rather than tidy**. Off season the archive ends in April while today is
   * September; returning the cache's last day would put the ceiling below
   * today and make today itself unselectable, which would break the one rule
   * SNOW-793 lays down (the map may always default to today). The mirror
   * image of the ``min`` ``earliestKnownDate`` takes for the season-start
   * case, and load-bearing for the same kind of reason.
   *
   * Like the floor, this bounds the RANGE and says nothing about which day
   * is chosen: a day inside it with no ratings is still pickable and simply
   * paints uncoloured, exactly as an off-season today already does.
   *
   * The result is capped at ``fallbackKey + MAX_FORWARD_DAYS``. That cap is
   * a guard on BAD DATA, not a product rule — one mis-dated provider
   * timestamp or a bad backfill would otherwise hand the month arrows
   * whatever year the bad row names. It lives here rather than in either
   * caller because both surfaces have to agree on the ceiling to the day: a
   * calendar that offers a date the scrubber then refuses on reload is the
   * exact defect SNOW-794 was raised to fix, one end of the range along.
   *
   * @param {Object|null} ratingsCache Date key → per-region ratings frame.
   * @param {string} fallbackKey Today. Returned when the cache has no usable
   *   key, preferred whenever it is later than the cache's last day, and the
   *   anchor the forward cap is measured from.
   * @returns {string} A ``YYYY-MM-DD`` date key.
   */
  function latestKnownDate(ratingsCache, fallbackKey) {
    var latest = '';
    if (ratingsCache && typeof ratingsCache === 'object') {
      for (var key of Object.keys(ratingsCache)) {
        if (!DATE_KEY_RE.test(key)) continue;
        if (!latest || key > latest) latest = key;
      }
    }
    if (!latest) return fallbackKey || '';
    if (fallbackKey && fallbackKey > latest) return fallbackKey;
    var anchorMs = Date.parse(fallbackKey);
    if (Number.isFinite(anchorMs)) {
      var capKey = keyFromMs(anchorMs + MAX_FORWARD_DAYS * MS_PER_DAY);
      if (latest > capKey) return capKey;
    }
    return latest;
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
   *       selectable: true,      // inside [min, max]
   *       inSeason: true,        // inside the avalanche season: a HIGHLIGHT
   *       rating: 3,             // EAWS danger int for the focused region,
   *                              // or null — SNOW-794
   *       selected: false,       // the date the map is currently showing
   *       isToday: false,
   *     }
   *
   * ``inSeason`` is deliberately separate from ``selectable`` rather than
   * folded into it. A day outside the season is still a day the map has
   * weather for — refusing it was the bug this shape exists to prevent.
   * Unselectable means outside ``[min, max]`` and nothing else; SNOW-927
   * moved ``max`` off the clock, so "not the future" is no longer what it
   * comes to.
   *
   * @param {string} monthKey A ``YYYY-MM`` month key.
   * @param {Object} opts Grid inputs.
   * @param {string} [opts.min] Earliest reachable day ``YYYY-MM-DD``.
   * @param {string} [opts.max] Latest reachable day ``YYYY-MM-DD`` — today,
   *   or the last day the payload covers when that is later (SNOW-927).
   * @param {string} [opts.seasonStart] Season start ``YYYY-MM-DD``.
   * @param {string} [opts.seasonEnd] Season end ``YYYY-MM-DD``.
   * @param {string} [opts.selected] The currently-showing date, or ''.
   * @param {string} [opts.today] Today's date key, for the today marker.
   * @param {Object<string, number>} [opts.ratings] SNOW-794: date key →
   *   EAWS danger int for the focused region. Days absent from it get
   *   ``rating: null``. This is what lets the grid carry the danger colour
   *   the scrubber ribbon paints into its track — the one thing the
   *   scrubber gave a phone that the calendar did not, which is why the
   *   scrubber can now be dropped entirely below 640px.
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
    var ratings = options.ratings || null;

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
        rating: ratings && ratings[dateKey] != null ? ratings[dateKey] : null,
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
    latestKnownDate: latestKnownDate,
    MAX_FORWARD_DAYS: MAX_FORWARD_DAYS,
    buildMonthGrid: buildMonthGrid,
  });
})();
