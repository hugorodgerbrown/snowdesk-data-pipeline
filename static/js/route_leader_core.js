/*
 * static/js/route_leader_core.js — the leader line's path (SNOW-1019).
 *
 * The leader is a dashed line from the route cursor's dot on the map down
 * to rail one's cursor and on to rail two's, so the eye can follow one
 * place across the three drawings of the route. This module is its shape
 * and nothing else: the SVG path `d` through two or three screen points.
 * static/js/route_leader.js is the DOM half.
 *
 * Each segment is one cubic whose tangents are VERTICAL at both ends —
 * control points `(a.x, mid.y)` and `(b.x, mid.y)` with mid.y halfway
 * between the ends — so the line leaves each stop straight down and
 * arrives at the next straight down, bending once between. A straight
 * diagonal would cross the rails' own marks at an angle; this meets each
 * cursor line along its own direction.
 *
 * Exports (frozen `self.pwaRouteLeaderCore`):
 *
 *   leaderPath(points) → an SVG path `d`, or '' for fewer than two points
 */

// @ts-check

(function () {
  'use strict';

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
   * @returns {string} The path `d`; '' when fewer than two stops remain.
   */
  function leaderPath(points) {
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
      const midY = (a.y + b.y) / 2;
      parts.push(
        'C' + num(a.x) + ' ' + num(midY)
        + ', ' + num(b.x) + ' ' + num(midY)
        + ', ' + num(b.x) + ' ' + num(b.y),
      );
    }
    return parts.join(' ');
  }

  self.pwaRouteLeaderCore = Object.freeze({
    leaderPath: leaderPath,
  });
})();
