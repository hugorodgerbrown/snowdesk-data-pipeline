/*
 * static/js/route_leader_core.js — the leader line's path (SNOW-1019).
 *
 * The leader is a dashed line from the route cursor's dot on the map up
 * to a notch on the route panel's bottom edge below the profile's cursor
 * line (SNOW-1065, SNOW-1068), so the eye can follow one place across the two drawings of
 * the route. This module is its shape and nothing else: the SVG path `d`
 * through its screen points (two today; it takes any number).
 * static/js/route_leader.js is the DOM half.
 *
 * Each segment is one cubic whose tangents are VERTICAL at both ends —
 * control points `(a.x, mid.y)` and `(b.x, mid.y)` with mid.y halfway
 * between the ends — so the line leaves each stop vertically and arrives
 * at the next vertically, bending once between. A straight diagonal would
 * meet the panel's edge at an angle; this arrives straight up, in line
 * with the profile's cursor line above it.
 *
 * The map sheets' leader (sheet_leader.js, Hugo, 24 Oct 2026) runs
 * SIDEWAYS on a desktop, from the symbol to the left-docked panel's right
 * edge, so `axis` 'x' turns the same bend on its side: tangents
 * HORIZONTAL at both ends, control points `(mid.x, a.y)` and `(mid.x, b.y)`.
 *
 * Exports (frozen `self.pwaRouteLeaderCore`):
 *
 *   leaderPath(points, axis?) → an SVG path `d`, or '' for fewer than two
 *     points; `axis` 'y' (the default) or 'x', the direction the line
 *     leaves and meets each stop
 *   swingPath(start, notch, depth) → an SVG path `d` from a point beside
 *     a panel, level with its bottom-edge notch or above it: straight
 *     down past the edge, a quarter turn, then along under the edge and
 *     up into the notch, never inside the panel (SNOW-1354); '' for a
 *     missing or non-finite end
 */

// @ts-check

(function () {
  'use strict';

  /** A quarter circle's control-arm ratio, as a cubic Bézier draws it. */
  const KAPPA = 0.5523;

  /**
   * Format one number for a path, trimmed to two decimals.
   *
   * @param {number} n
   * @returns {string}
   */
  function num(n) {
    return String(Math.round(n * 100) / 100);
  }

  /**
   * The leader's path through its stops, top to bottom.
   *
   * @param {Array<?{x: number, y: number}>} points The stops, in the order
   *   the line runs; a null or non-finite stop is skipped.
   * @param {string} [axis] 'y' (default): leave and meet each stop
   *   vertically; 'x': horizontally.
   * @returns {string} The path `d`; '' when fewer than two stops remain.
   */
  function leaderPath(points, axis) {
    /** @type {Array<{x: number, y: number}>} */
    const stops = [];
    (Array.isArray(points) ? points : []).forEach((p) => {
      if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) stops.push(p);
    });
    if (stops.length < 2) return '';
    const parts = ['M' + num(stops[0].x) + ' ' + num(stops[0].y)];
    for (let i = 1; i < stops.length; i += 1) {
      const a = stops[i - 1];
      const b = stops[i];
      if (axis === 'x') {
        const midX = (a.x + b.x) / 2;
        parts.push(
          'C' + num(midX) + ' ' + num(a.y)
          + ', ' + num(midX) + ' ' + num(b.y)
          + ', ' + num(b.x) + ' ' + num(b.y),
        );
        continue;
      }
      const midY = (a.y + b.y) / 2;
      parts.push(
        'C' + num(a.x) + ' ' + num(midY)
        + ', ' + num(b.x) + ' ' + num(midY)
        + ', ' + num(b.x) + ' ' + num(b.y),
      );
    }
    return parts.join(' ');
  }

  /**
   * The leader from a point BESIDE the panel to its bottom notch
   * (SNOW-1354).
   *
   * A point level with the notch or above it would make `leaderPath`'s
   * bend arrive at the notch from above, through the panel. This path
   * keeps clear of the panel by construction: it drops STRAIGHT down from
   * the point, so it stays at the point's x, beside the panel, until it is
   * below the panel's edge; turns a quarter round there, `depth / 2` px
   * in radius; and runs to the notch with every point at or below the
   * notch's y, arriving going up, as the leader always does. The run sits
   * `depth` px below the lower of the two ends.
   *
   * @param {?{x: number, y: number}} start The point on the map.
   * @param {?{x: number, y: number}} notch The notch on the panel's edge.
   * @param {number} depth How far below the lower end the run reaches.
   * @returns {string} The path `d`, or ''.
   */
  function swingPath(start, notch, depth) {
    /** @param {?{x: number, y: number}} p */
    const ok = (p) => Boolean(p && Number.isFinite(p.x) && Number.isFinite(p.y));
    if (!ok(start) || !ok(notch)) return '';
    const s = /** @type {{x: number, y: number}} */ (start);
    const n = /** @type {{x: number, y: number}} */ (notch);
    const d = Number.isFinite(depth) && depth > 0 ? depth : 0;
    const k = Math.max(s.y, n.y) + d;
    const r = d / 2;
    // Toward the notch; the quarter turn bends that way.
    const sign = n.x < s.x ? -1 : 1;
    // A circle's quarter as a cubic: the control arms are KAPPA × r.
    const arm = KAPPA * r;
    return 'M' + num(s.x) + ' ' + num(s.y)
      + ' L' + num(s.x) + ' ' + num(k - r)
      + ' C' + num(s.x) + ' ' + num(k - r + arm)
      + ', ' + num(s.x + sign * (r - arm)) + ' ' + num(k)
      + ', ' + num(s.x + sign * r) + ' ' + num(k)
      + ' C' + num(n.x) + ' ' + num(k)
      + ', ' + num(n.x) + ' ' + num(k)
      + ', ' + num(n.x) + ' ' + num(n.y);
  }

  self.pwaRouteLeaderCore = Object.freeze({
    leaderPath: leaderPath,
    swingPath: swingPath,
  });
})();
