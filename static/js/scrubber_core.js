/*
 * static/js/scrubber_core.js — Pure, context-free season-scrubber/timelapse
 * helpers (SNOW-496).
 *
 * Extracted from the inline closures in static/js/map.js's
 * ``seasonScrubberInit``, ``timelapseInit`` and ``seasonRibbonInit`` IIFEs so
 * they can be unit-tested directly (see ``tests/js/test_scrubber_core.js``) —
 * MapLibre never fires ``map.on('load')`` in headless Playwright, so this
 * transport math was previously exercised only indirectly (or not at all).
 * ``map.js`` itself is otherwise unchanged: each extracted definition is
 * replaced with a thin local delegator that forwards the same closure values
 * to the function of the same name here — every call site, control flow, and
 * DOM/MapLibre interaction is untouched.
 *
 * Attached to ``window`` (not ``self``) — unlike ``mutation_queue_core.js``,
 * every consumer is a page script; there is no service-worker context to
 * share with.
 *
 * Deliberately dependency-free: every export is a pure function of its
 * arguments. Season bounds, the ratings cache, the active-country map and
 * the rating-int→key lookup table are all passed in explicitly rather than
 * read from module-scope globals.
 *
 * Public API — attached to ``window.pwaScrubberCore``:
 *
 *   pctToDateKey(pct, seasonStartMs, seasonSpanMs)
 *     Thumb percentage (0..100) → ISO date string, snapped to UTC midnight.
 *   dateKeyToPct(dateKey, seasonStartMs, seasonSpanMs, fallbackPct)
 *     ISO date string → thumb percentage, clamped to [0, 100]. Returns
 *     ``fallbackPct`` when ``dateKey`` doesn't parse or the season span is
 *     non-positive/non-finite.
 *   snapToNearestDataDay(dateKey, sortedDates)
 *     Nearest entry in ``sortedDates`` to ``dateKey`` by absolute ms delta.
 *     Returns ``dateKey`` unchanged when ``sortedDates`` is empty/absent.
 *   nearestFrameIndex(sortedDates, ariaValueNow, seasonStartMs, seasonSpanMs)
 *     Index into ``sortedDates`` nearest the position implied by
 *     ``ariaValueNow`` (a 0..100 thumb percentage) — used to resume
 *     timelapse playback from the current thumb position. Returns 0 when
 *     the inputs can't place a position.
 *   nextFrame(frameIdx, direction, totalFrames)
 *     Advance one timelapse tick. Returns ``{frameIdx, done}`` —
 *     ``frameIdx`` is ``frameIdx + direction`` unconditionally (matching the
 *     original inline behaviour of leaving the counter one step past the
 *     boundary when playback stops); ``done`` is true once that new index
 *     runs off either end of ``[0, totalFrames)``.
 *   deriveEffectiveTodayKey(dates, cache, countryState, fallbackKey)
 *     Latest entry in ``dates`` whose ``cache[date]`` payload contains at
 *     least one region belonging to an active country (per ``countryState``,
 *     a ``{prefix: boolean}`` map). Falls back to the last date in ``dates``
 *     if none match, or to ``fallbackKey`` if ``dates`` is empty. Logs an
 *     unexpected-prefix warning (deduped per call) exactly as the original
 *     inline version did.
 *   isInSeason(dateKey, seasonStartMs, seasonEndMs)
 *     True when ``dateKey`` parses to a timestamp within
 *     ``[seasonStartMs, seasonEndMs]`` inclusive.
 *   intToKey(n, ratingTable)
 *     Rating int → EAWS key string via ``ratingTable`` (index lookup);
 *     ``'no_rating'`` for ``null``/``undefined``/out-of-range ``n``.
 */

(function () {
  'use strict';

  /**
   * Thumb percentage (0..100) → ISO date string, UTC-midnight snapped.
   *
   * @param {number} pct
   * @param {number} seasonStartMs
   * @param {number} seasonSpanMs
   * @returns {string}
   */
  function pctToDateKey(pct, seasonStartMs, seasonSpanMs) {
    var ms = seasonStartMs + (pct / 100) * seasonSpanMs;
    var day = new Date(ms);
    var y = day.getUTCFullYear();
    var m = String(day.getUTCMonth() + 1).padStart(2, '0');
    var d = String(day.getUTCDate()).padStart(2, '0');
    return y + '-' + m + '-' + d;
  }

  /**
   * ISO date string → thumb percentage, clamped to [0, 100].
   *
   * @param {string} dateKey
   * @param {number} seasonStartMs
   * @param {number} seasonSpanMs
   * @param {number} fallbackPct Returned when ``dateKey`` doesn't parse or
   *   ``seasonSpanMs`` is non-finite/non-positive.
   * @returns {number}
   */
  function dateKeyToPct(dateKey, seasonStartMs, seasonSpanMs, fallbackPct) {
    var ms = Date.parse(dateKey);
    if (Number.isNaN(ms) || !Number.isFinite(seasonSpanMs) || seasonSpanMs <= 0) {
      return fallbackPct;
    }
    return Math.max(0, Math.min(100, ((ms - seasonStartMs) / seasonSpanMs) * 100));
  }

  /**
   * Nearest entry in ``sortedDates`` to ``dateKey`` by absolute ms delta.
   *
   * @param {string} dateKey
   * @param {string[]|null} sortedDates
   * @returns {string} ``dateKey`` unchanged when ``sortedDates`` is empty/null.
   */
  function snapToNearestDataDay(dateKey, sortedDates) {
    if (!sortedDates || sortedDates.length === 0) return dateKey;
    // SNOW-614: hoisted out of the loop. ``dateKey`` does not change across
    // it, and this runs on every pointermove of a scrubber drag over a
    // season's worth of dates — so it was one redundant date parse per
    // candidate per frame.
    var targetMs = Date.parse(dateKey);
    var best = sortedDates[0];
    var bestDelta = Math.abs(Date.parse(best) - targetMs);
    for (var i = 0; i < sortedDates.length; i++) {
      var d = sortedDates[i];
      var delta = Math.abs(Date.parse(d) - targetMs);
      if (delta < bestDelta) {
        best = d;
        bestDelta = delta;
      }
    }
    return best;
  }

  /**
   * Index into ``sortedDates`` nearest the position implied by
   * ``ariaValueNow`` (a 0..100 thumb percentage) — resumes timelapse
   * playback from the current thumb position rather than always
   * rewinding to frame 0.
   *
   * @param {string[]|null} sortedDates
   * @param {number} ariaValueNow
   * @param {number} seasonStartMs
   * @param {number} seasonSpanMs
   * @returns {number}
   */
  function nearestFrameIndex(sortedDates, ariaValueNow, seasonStartMs, seasonSpanMs) {
    if (!sortedDates || sortedDates.length === 0) return 0;
    if (
      !Number.isFinite(ariaValueNow) ||
      !Number.isFinite(seasonSpanMs) ||
      seasonSpanMs <= 0
    ) {
      return 0;
    }
    var targetMs = seasonStartMs + (ariaValueNow / 100) * seasonSpanMs;
    var best = 0;
    var bestDelta = Math.abs(Date.parse(sortedDates[0]) - targetMs);
    for (var i = 1; i < sortedDates.length; i++) {
      var delta = Math.abs(Date.parse(sortedDates[i]) - targetMs);
      if (delta < bestDelta) {
        best = i;
        bestDelta = delta;
      }
    }
    return best;
  }

  /**
   * Advance one timelapse tick.
   *
   * @param {number} frameIdx
   * @param {1|-1} direction
   * @param {number} totalFrames
   * @returns {{frameIdx: number, done: boolean}} ``frameIdx`` is
   *   ``frameIdx + direction`` unconditionally — matching the original
   *   inline behaviour of leaving the counter one step past the boundary
   *   when playback stops; ``done`` is true once that new index runs off
   *   either end of ``[0, totalFrames)``.
   */
  function nextFrame(frameIdx, direction, totalFrames) {
    var next = frameIdx + direction;
    var done =
      (direction === 1 && next >= totalFrames) || (direction === -1 && next < 0);
    return { frameIdx: next, done: done };
  }

  /**
   * Latest entry in ``dates`` whose ``cache[date]`` payload contains at
   * least one region belonging to an active country.
   *
   * @param {string[]|null} dates Ascending date keys.
   * @param {Object<string, Object<string, number>>} cache
   * @param {Object<string, boolean>} countryState ``{prefix: active}``.
   * @param {string} fallbackKey Returned when ``dates`` is empty/null.
   * @returns {string}
   */
  function deriveEffectiveTodayKey(dates, cache, countryState, fallbackKey) {
    if (!dates || dates.length === 0) return fallbackKey;
    // Object.create(null) rather than {} — a plain object's prototype
    // chain would make `!warnedPrefixes[prefix]` return falsy (already
    // "warned") for any prefix that collides with an inherited Object.prototype
    // name (e.g. 'constructor', 'toString'), silently suppressing that
    // prefix's first warning.
    var warnedPrefixes = Object.create(null);
    for (var i = dates.length - 1; i >= 0; i--) {
      var dateKey = dates[i];
      var frame = cache[dateKey] || {};
      var regionIds = Object.keys(frame);
      for (var j = 0; j < regionIds.length; j++) {
        var prefix = regionIds[j].split('-')[0].toLowerCase();
        if (countryState[prefix] === true) return dateKey;
        if (!(prefix in countryState) && !warnedPrefixes[prefix]) {
          warnedPrefixes[prefix] = true;
          console.warn('[map] SNOW-236: unexpected region prefix', prefix, 'in ratings payload');
        }
      }
    }
    return dates[dates.length - 1];
  }

  /**
   * True when ``dateKey`` parses to a timestamp within
   * ``[seasonStartMs, seasonEndMs]`` inclusive.
   *
   * @param {string} dateKey
   * @param {number} seasonStartMs
   * @param {number} seasonEndMs
   * @returns {boolean}
   */
  function isInSeason(dateKey, seasonStartMs, seasonEndMs) {
    var ms = Date.parse(dateKey);
    return Number.isFinite(ms) && ms >= seasonStartMs && ms <= seasonEndMs;
  }

  /**
   * Rating int → EAWS key string via ``ratingTable`` index lookup.
   *
   * @param {number|null|undefined} n
   * @param {string[]} ratingTable
   * @returns {string} ``'no_rating'`` for ``null``/``undefined``/out-of-range.
   */
  function intToKey(n, ratingTable) {
    if (n == null || n < 0 || n >= ratingTable.length) return 'no_rating';
    return ratingTable[n];
  }

  window.pwaScrubberCore = Object.freeze({
    pctToDateKey: pctToDateKey,
    dateKeyToPct: dateKeyToPct,
    snapToNearestDataDay: snapToNearestDataDay,
    nearestFrameIndex: nearestFrameIndex,
    nextFrame: nextFrame,
    deriveEffectiveTodayKey: deriveEffectiveTodayKey,
    isInSeason: isInSeason,
    intToKey: intToKey,
  });
})();
