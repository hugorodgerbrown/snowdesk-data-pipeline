/*
 * static/js/route_cursor_core.js — one cursor shared by the map and both
 * rails (SNOW-1016).
 *
 * The map, rail one and rail two are three drawings of the same route, and
 * they are only worth having together if a place on one is the same place
 * on the others. That needs one shared coordinate, and a DISTANCE is not
 * it: the elevation profile's x-axis is summed distance along the
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
 * This module owns no DOM and reads no globals. It holds three things —
 * the cursor `index`, the `openLeg`, the `selection` — clamps them, and
 * tells subscribers when they change.
 *
 * Clamping:
 *
 *   - an index is clamped to [0, N - 1];
 *   - while a leg is open it is also clamped to [leg.from, leg.to], so a
 *     drag off the end of rail two cannot move the cursor on rail one or
 *     the map outside the open leg;
 *   - opening a leg pulls an out-of-range cursor into it rather than
 *     rejecting the leg; closing a leg leaves the cursor where it was;
 *   - a selection is clamped to the route and put in order, never to the
 *     open leg: a passage that runs past the leg's end is still the
 *     passage, and rail two clips it when it draws.
 *
 * `null` is a real state for the index and the selection: no pointer over
 * any surface, nothing selected. A non-finite number is not — it is a
 * conversion bug in the surface that sent it, and it throws.
 *
 * Exports (frozen `self.pwaRouteCursorCore`):
 *
 *   createRouteCursor(count) — a cursor over `count` segments
 *
 * and the cursor it returns:
 *
 *   state()                 — the current frozen `{index, openLeg, selection}`
 *   setIndex(index | null)
 *   openLeg(leg) / closeLeg()
 *   select({kind, from, to}) / clearSelection()
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
   * @typedef {{kind: string, from: number, to: number}} Selection
   *   A selected range in sample indices, both ends inclusive. `kind` names
   *   what was pressed (`'band'`, `'passage'`, …) so a surface can draw it.
   */

  /**
   * @typedef {{index: ?number, openLeg: ?Leg, selection: ?Selection}} CursorState
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
   *   select: function(Selection): void,
   *   clearSelection: function(): void,
   *   subscribe: function(function(CursorState): void): function(): void,
   * }}
   */
  function createRouteCursor(count) {
    if (!Number.isInteger(count) || count < 1) {
      throw new RangeError(`count must be a positive integer, got ${count}`);
    }
    const last = count - 1;

    /** @type {CursorState} */
    let current = Object.freeze({ index: null, openLeg: null, selection: null });
    /** @type {Set<function(CursorState): void>} */
    const subscribers = new Set();

    /**
     * Replace the state and tell subscribers — unless nothing changed.
     *
     * @param {?number} index The new cursor index.
     * @param {?Leg} leg The new open leg.
     * @param {?Selection} selection The new selection.
     */
    function commit(index, leg, selection) {
      if (
        index === current.index
        && leg === current.openLeg
        && selection === current.selection
      ) return;
      current = Object.freeze({ index: index, openLeg: leg, selection: selection });
      subscribers.forEach((fn) => fn(current));
    }

    /**
     * Clamp an index to the route and, if one is open, to the open leg.
     *
     * @param {number} index An integer index.
     * @param {?Leg} leg The open leg, if any.
     * @returns {number}
     */
    function bound(index, leg) {
      const clamped = clamp(index, 0, last);
      return leg ? clamp(clamped, leg.from, leg.to) : clamped;
    }

    /**
     * Move the cursor, or clear it with `null`.
     *
     * @param {?number} index The index a surface converted its pointer to.
     */
    function setIndex(index) {
      const next = index === null ? null : bound(toIndex(index, 'index'), current.openLeg);
      commit(next, current.openLeg, current.selection);
    }

    /**
     * Open a leg, pulling the cursor into it if it lies outside.
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

      const next = Object.freeze({ ...leg, from: from, to: to });
      const index = current.index === null ? null : bound(current.index, next);
      commit(index, next, current.selection);
    }

    /** Close the open leg. The cursor stays where it was. */
    function closeLeg() {
      commit(current.index, null, current.selection);
    }

    /**
     * Select a range, replacing any selection already held.
     *
     * @param {Selection} selection What was pressed, in sample indices.
     */
    function select(selection) {
      const a = clamp(toIndex(selection && selection.from, 'selection.from'), 0, last);
      const b = clamp(toIndex(selection && selection.to, 'selection.to'), 0, last);
      const from = Math.min(a, b);
      const to = Math.max(a, b);
      const held = current.selection;
      if (held && held.kind === selection.kind && held.from === from && held.to === to) return;
      commit(
        current.index,
        current.openLeg,
        Object.freeze({ kind: selection.kind, from: from, to: to }),
      );
    }

    /** Clear the selection. */
    function clearSelection() {
      commit(current.index, current.openLeg, null);
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
      select: select,
      clearSelection: clearSelection,
      subscribe: subscribe,
    });
  }

  self.pwaRouteCursorCore = Object.freeze({
    createRouteCursor: createRouteCursor,
  });
})();
