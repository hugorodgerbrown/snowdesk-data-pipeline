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
 * A LEG HERE IS IN SAMPLE INDICES TOO. `Leg.start` / `Leg.end` in
 * apps/routes/services/legs.py index `Route.points`, not the samples; the
 * server converts them before they reach this module, and `from` / `to`
 * are the first and last segment of the leg, both inclusive.
 *
 * This module owns no DOM and reads no globals. It holds two things —
 * the cursor `index` and the `openLeg` — and tells subscribers when they
 * change. It holds no selection: SNOW-1052 took band and passage
 * selection off the rails.
 *
 * A LEG OR A POINT, NEVER BOTH (SNOW-1065). The route has either a
 * highlighted leg or a placed point:
 *
 *   - opening a leg clears the index;
 *   - setting an index clears the open leg;
 *   - closing a leg leaves the index where it was (null, by the rule
 *     above);
 *   - an index is clamped to [0, N - 1].
 *
 * This replaced the rule that clamped the index into the open leg, which
 * existed for rail two's zoomed window; with rail two gone a leg is a
 * highlight, and a point anywhere on the route is a different question.
 * Re-opening the leg that is already open is a no-op here — the rail's
 * second press closes it with `closeLeg`.
 *
 * `null` is a real state for the index: no pointer over any surface. A
 * non-finite number is not — it is a conversion bug in the surface that
 * sent it, and it throws.
 *
 * Exports (frozen `self.pwaRouteCursorCore`):
 *
 *   createRouteCursor(count) — a cursor over `count` segments
 *
 * and the cursor it returns:
 *
 *   state()                 — the current frozen `{index, openLeg}`
 *   setIndex(index | null)
 *   openLeg(leg) / closeLeg()
 *   subscribe(fn)           — returns the unsubscribe function
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {{from: number, to: number}} Leg
   *   A leg in sample indices, both ends inclusive. Other properties the
   *   caller put on it (`i`, `climbing`) are kept and handed back.
   */

  /**
   * @typedef {{index: ?number, openLeg: ?Leg}} CursorState
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
   *   openLeg: function(Leg): void,
   *   closeLeg: function(): void,
   *   subscribe: function(function(CursorState): void): function(): void,
   * }}
   */
  function createRouteCursor(count) {
    if (!Number.isInteger(count) || count < 1) {
      throw new RangeError(`count must be a positive integer, got ${count}`);
    }
    const last = count - 1;

    /** @type {CursorState} */
    let current = Object.freeze({ index: null, openLeg: null });
    /** @type {Set<function(CursorState): void>} */
    const subscribers = new Set();

    /**
     * Replace the state and tell subscribers — unless nothing changed.
     *
     * @param {?number} index The new cursor index.
     * @param {?Leg} leg The new open leg.
     */
    function commit(index, leg) {
      if (index === current.index && leg === current.openLeg) return;
      current = Object.freeze({ index: index, openLeg: leg });
      subscribers.forEach((fn) => fn(current));
    }

    /**
     * Move the cursor, or clear it with `null`. A point clears the open
     * leg; clearing the point leaves the leg as it was.
     *
     * @param {?number} index The index a surface converted its pointer to.
     */
    function setIndex(index) {
      if (index === null) {
        commit(null, current.openLeg);
        return;
      }
      commit(clamp(toIndex(index, 'index'), 0, last), null);
    }

    /**
     * Open a leg, clearing the point.
     *
     * Opening the leg that is already open is a no-op, compared by its ends
     * rather than by identity, because a surface may rebuild the leg object
     * from the wire on every redraw.
     *
     * @param {Leg} leg The leg, in sample indices.
     */
    function openLeg(leg) {
      const from = toIndex(leg && leg.from, 'leg.from');
      const to = toIndex(leg && leg.to, 'leg.to');
      if (from < 0 || to > last || from > to) {
        throw new RangeError(`leg [${from}, ${to}] is outside [0, ${last}]`);
      }
      const open = current.openLeg;
      if (open && open.from === from && open.to === to) return;
      commit(null, Object.freeze({ ...leg, from: from, to: to }));
    }

    /** Close the open leg. The index stays where it was. */
    function closeLeg() {
      commit(current.index, null);
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
      openLeg: openLeg,
      closeLeg: closeLeg,
      subscribe: subscribe,
    });
  }

  self.pwaRouteCursorCore = Object.freeze({
    createRouteCursor: createRouteCursor,
  });
})();
