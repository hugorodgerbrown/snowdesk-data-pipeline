/*
 * static/js/route_cursor_core.js — one cursor shared by the map, the rail
 * and the point card (SNOW-1016).
 *
 * The map and the rail are two drawings of the same route, and they are
 * only worth having together if a place on one is the same place on the
 * other (rail two was a third until SNOW-1065 removed it). That needs one
 * shared coordinate, and a DISTANCE is not it: the elevation profile's x-axis is summed distance along the
 * simplified geometry, while the sampler's segments are strides along its
 * own walk, and the two disagree by a chord per switchback
 * (apps/routes/services/slope_summary.py, "two length sources").
 *
 * THE COORDINATE IS THE SAMPLE INDEX — an integer into the `angles` array
 * every surface already holds on `properties.slope` (route_slope_core.js).
 * N + 1 boundary points bound N segments, so a route has indices 0 to
 * N - 1. Each surface converts its own geometry to an index and back in its
 * own code; nothing passes a distance or a fraction across this boundary,
 * because that conversion is exactly the one the surfaces disagree on.
 *
 * This module owns no DOM and reads no globals. It holds one thing — the
 * cursor `index` — and tells subscribers when it changes. It holds no
 * selection: SNOW-1052 took band and passage selection off the rails, and
 * leg selection went on 2026-10-02 (a point is the only thing a reader
 * places on a route now). An index is clamped to [0, N - 1].
 *
 * `null` is a real state for the index: no point placed. A
 * non-finite number is not — it is a conversion bug in the surface that
 * sent it, and it throws.
 *
 * Exports (frozen `self.pwaRouteCursorCore`):
 *
 *   createRouteCursor(count) — a cursor over `count` segments
 *
 * and the cursor it returns:
 *
 *   state()                 — the current frozen `{index}`
 *   setIndex(index | null)
 *   subscribe(fn)           — returns the unsubscribe function
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {{index: ?number}} CursorState
   */

  /**
   * Clamp a number into a closed range.
   *
   * @param {number} value The number.
   * @param {number} low The lowest value allowed.
   * @param {number} high The highest value allowed.
   * @returns {number}
   */
  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /**
   * Turn a surface's index into an integer, or fail.
   *
   * A surface converting from pixels may hand over a fraction, which rounds
   * to the nearest segment. NaN or Infinity means its conversion broke, and
   * clamping it would put the cursor at one end of the route with no trace
   * of why.
   *
   * @param {*} value The candidate index.
   * @param {string} name What it is, for the error message.
   * @returns {number}
   */
  function toIndex(value, name) {
    if (typeof value !== 'number' || !isFinite(value)) {
      throw new TypeError(`${name} must be a finite number, got ${value}`);
    }
    return Math.round(value);
  }

  /**
   * Create a cursor over one route's segments.
   *
   * @param {number} count How many segments the route has — the length of
   *   `properties.slope.angles`.
   * @returns {{
   *   state: function(): CursorState,
   *   setIndex: function(?number): void,
   *   subscribe: function(function(CursorState): void): function(): void,
   * }}
   */
  function createRouteCursor(count) {
    if (!Number.isInteger(count) || count < 1) {
      throw new RangeError(`count must be a positive integer, got ${count}`);
    }
    const last = count - 1;

    /** @type {CursorState} */
    let current = Object.freeze({ index: null });
    /** @type {Set<function(CursorState): void>} */
    const subscribers = new Set();

    /**
     * Replace the state and tell subscribers — unless nothing changed.
     *
     * @param {?number} index The new cursor index.
     */
    function commit(index) {
      if (index === current.index) return;
      current = Object.freeze({ index: index });
      subscribers.forEach((fn) => fn(current));
    }

    /**
     * Move the cursor, or clear it with `null`.
     *
     * @param {?number} index The index a surface converted its pointer to.
     */
    function setIndex(index) {
      commit(index === null ? null : clamp(toIndex(index, 'index'), 0, last));
    }

    /**
     * Call `fn` with the new state after every change.
     *
     * Not called on subscription and not called for a set that changes
     * nothing, so a surface redraws exactly once per real change.
     *
     * @param {function(CursorState): void} fn The redraw.
     * @returns {function(): void} Removes the subscription.
     */
    function subscribe(fn) {
      subscribers.add(fn);
      return () => {
        subscribers.delete(fn);
      };
    }

    return Object.freeze({
      state: () => current,
      setIndex: setIndex,
      subscribe: subscribe,
    });
  }

  self.pwaRouteCursorCore = Object.freeze({
    createRouteCursor: createRouteCursor,
  });
})();
