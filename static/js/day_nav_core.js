/*
 * static/js/day_nav_core.js — pure day-stepping maths for the map's date
 * row (SNOW-1195).
 *
 * The map's date row gained three things in SNOW-1195, and every decision
 * behind them that can be made without a DOM lives here, as
 * ``window.pwaDayNavCore``, so tests/js/test_day_nav_core.js can pin them:
 *
 *   shiftDay(dateKey, delta)
 *     ``('2026-02-28', 1)`` → ``'2026-03-01'``. UTC arithmetic on the key,
 *     so a DST change never moves a day.
 *   clampDay(dateKey, minKey, maxKey)
 *     The key pulled inside [min, max]. An empty bound is no bound.
 *   keyStep(key)
 *     What a key pressed on the season scrubber (role="slider") asks for:
 *     ``{ delta: n }`` for the arrows (one day) and Page Up / Page Down (a
 *     week), ``{ edge: 'min' | 'max' }`` for Home / End, ``null`` for any
 *     other key. Up and Right are later, as the WAI-ARIA slider pattern has
 *     it.
 *   dayHasBulletin(frame, countryState)
 *     Whether one day of the ratings cache carries a rating for any region
 *     of a country the visitor has on.
 *   nearestIssued(dateKey, ratingsCache, countryState, todayKey)
 *     For a day with no bulletin: on today, tomorrow when it has one
 *     (``direction: 'following'`` — an evening issue for tomorrow, out
 *     today, SNOW-1204), else the latest earlier day that
 *     has one (``direction: 'last'``), else the earliest later one
 *     (``direction: 'next'``), or ``null``. The empty-day chip offers it.
 *
 * Dependency-free: every export is a pure function of its arguments, so a
 * date key never reaches ``Date`` except through ``Date.UTC``.
 */

// @ts-check

(function () {
  'use strict';

  var MS_PER_DAY = 86400000;
  var DATE_KEY_RE = /^\d{4}-\d{2}-\d{2}$/;

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
   * The day ``delta`` days from ``dateKey``.
   *
   * @param {string} dateKey A ``YYYY-MM-DD`` date key.
   * @param {number} delta Days to move; negative is earlier.
   * @returns {string} The new key, or ``''`` if ``dateKey`` is unparseable.
   */
  function shiftDay(dateKey, delta) {
    if (typeof dateKey !== 'string' || !DATE_KEY_RE.test(dateKey)) return '';
    var parts = dateKey.split('-');
    var ms = Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    return keyFromMs(ms + Math.round(delta || 0) * MS_PER_DAY);
  }

  /**
   * A day key pulled inside the reachable range.
   *
   * Keys compare as strings because the format is fixed-width.
   *
   * @param {string} dateKey
   * @param {string} minKey The earliest reachable day, or ``''``.
   * @param {string} maxKey The latest reachable day, or ``''``.
   * @returns {string}
   */
  function clampDay(dateKey, minKey, maxKey) {
    if (minKey && dateKey < minKey) return minKey;
    if (maxKey && dateKey > maxKey) return maxKey;
    return dateKey;
  }

  /**
   * What a key pressed on the season scrubber asks for.
   *
   * @param {string} key A ``KeyboardEvent.key``.
   * @returns {{ delta: number } | { edge: 'min' | 'max' } | null}
   */
  function keyStep(key) {
    switch (key) {
      case 'ArrowLeft':
      case 'ArrowDown':
        return { delta: -1 };
      case 'ArrowRight':
      case 'ArrowUp':
        return { delta: 1 };
      case 'PageDown':
        return { delta: -7 };
      case 'PageUp':
        return { delta: 7 };
      case 'Home':
        return { edge: 'min' };
      case 'End':
        return { edge: 'max' };
      default:
        return null;
    }
  }

  /**
   * Whether one day's ratings carry any region of a visible country.
   *
   * A region id's prefix is its country (``CH-4115`` → ``ch``), the same
   * reading the scrubber's ``deriveEffectiveTodayKey`` makes. A null value
   * is a region the payload names without a rating, which is not a
   * bulletin.
   *
   * @param {?Object<string, *>} frame One date's ``{regionId: rating}``.
   * @param {Object<string, boolean>} countryState ``COUNTRY_STATE``.
   * @returns {boolean}
   */
  function dayHasBulletin(frame, countryState) {
    if (!frame || typeof frame !== 'object') return false;
    for (var regionId of Object.keys(frame)) {
      if (frame[regionId] == null) continue;
      var prefix = regionId.split('-')[0].toLowerCase();
      if (countryState && countryState[prefix] === true) return true;
    }
    return false;
  }

  /**
   * The nearest day with a bulletin: on today, tomorrow if it has one;
   * otherwise looking back first.
   *
   * Tomorrow first, on today only (SNOW-1204), because the ratings cache
   * files a bulletin under the day it forecasts, and an evening issue
   * forecasts tomorrow. While tomorrow has not begun, a rating for it can
   * only have come from an issue already out today, which the region sheet
   * shows as the one in force; naming an older day as the "last issued"
   * there was false (the chip said 21 Oct while the sheet showed a 23 Oct
   * 15:00 reissue). On a past day the next day's rating may be that day's
   * own morning issue, so the inference does not hold and is not made.
   *
   * Otherwise back first, because the question an empty day raises is
   * almost always "what did the last one say?" — off season, a gap in a
   * provider's archive. Forward only when nothing came before, which is a
   * day ahead of the season's first bulletin.
   *
   * @param {string} dateKey The empty day.
   * @param {?Object<string, Object<string, *>>} ratingsCache
   * @param {Object<string, boolean>} countryState
   * @param {string} [todayKey] Today's key; without it, no 'following'.
   * @returns {?{ date: string, direction: 'following' | 'last' | 'next' }}
   */
  function nearestIssued(dateKey, ratingsCache, countryState, todayKey) {
    if (!ratingsCache || typeof ratingsCache !== 'object') return null;
    var following = shiftDay(dateKey, 1);
    if (todayKey && dateKey === todayKey && dayHasBulletin(ratingsCache[following], countryState)) {
      return { date: following, direction: 'following' };
    }
    var last = '';
    var next = '';
    for (var key of Object.keys(ratingsCache)) {
      if (!DATE_KEY_RE.test(key) || key === dateKey) continue;
      if (!dayHasBulletin(ratingsCache[key], countryState)) continue;
      if (key < dateKey) {
        if (!last || key > last) last = key;
      } else if (!next || key < next) {
        next = key;
      }
    }
    if (last) return { date: last, direction: 'last' };
    if (next) return { date: next, direction: 'next' };
    return null;
  }

  window.pwaDayNavCore = Object.freeze({
    shiftDay: shiftDay,
    clampDay: clampDay,
    keyStep: keyStep,
    dayHasBulletin: dayHasBulletin,
    nearestIssued: nearestIssued,
  });
})();
