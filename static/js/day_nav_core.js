/*
 * static/js/day_nav_core.js — pure day-stepping maths for the map's date
 * row (SNOW-1195).
 *
 * The map's date row gained day steps and scrubber keys in SNOW-1195, and
 * every decision behind them that can be made without a DOM lives here, as
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

  window.pwaDayNavCore = Object.freeze({
    shiftDay: shiftDay,
    clampDay: clampDay,
    keyStep: keyStep,
  });
})();
