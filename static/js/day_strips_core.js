/*
 * static/js/day_strips_core.js — which three-hour block a point on the day
 * strips names (SNOW-1219).
 *
 * The weather sheet's day strips (templates/includes/_day_strips.html)
 * share one time axis split into eight three-hour blocks, each 12.5% of
 * the plot. A tap or a drag along the timeline selects the block under
 * it. This module is that arithmetic and nothing else;
 * static/js/day_strips.js is the DOM half.
 *
 * Exports (frozen `self.pwaDayStripsCore`):
 *
 *   BLOCK_COUNT                   — 8
 *   blockAt(x, left, width)      → the block under a client x, clamped to
 *     0..7, or null when the plot has no width
 */

// @ts-check

(function () {
  'use strict';

  var BLOCK_COUNT = 8;

  /**
   * The block under a horizontal position on a plot.
   *
   * A drag that runs off either end of the timeline keeps the end block, so
   * dragging past the last hour holds 21–24 rather than dropping the
   * selection.
   *
   * @param {number} x The pointer's client x.
   * @param {number} left The timeline's left edge, client px.
   * @param {number} width The timeline's width, px.
   * @returns {?number}
   */
  function blockAt(x, left, width) {
    if (!(width > 0) || !Number.isFinite(x) || !Number.isFinite(left)) {
      return null;
    }
    var index = Math.floor(((x - left) / width) * BLOCK_COUNT);
    return Math.max(0, Math.min(BLOCK_COUNT - 1, index));
  }

  self.pwaDayStripsCore = Object.freeze({
    BLOCK_COUNT: BLOCK_COUNT,
    blockAt: blockAt,
  });
})();
