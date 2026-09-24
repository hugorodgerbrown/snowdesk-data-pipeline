/*
 * static/js/route_cursor_map_core.js — the route cursor, placed on the map
 * (SNOW-1019).
 *
 * The route cursor (route_cursor_core.js) is one sample index, one open
 * leg and one selection, shared by the map and both rails. The rails draw
 * it by share along their own x-axis; the map draws it in GEOGRAPHY, from
 * the boundary points the slope record already carries:
 *
 *     properties.slope = { points: [[lon, lat], …],   // N + 1
 *                          angles: [ … ] }             // N
 *
 * Segment i runs from points[i] to points[i + 1]. So a selection
 * `{from, to}` — sample indices, BOTH inclusive — is the line through
 * points[from] … points[to + 1], and the cursor index i is drawn at the
 * middle of its segment. Going the other way, a pointer on the map is
 * converted to the sample whose segment middle is nearest on SCREEN,
 * which is the question a reader's pointer asks: "which bit of line am I
 * on", in the pixels they can see.
 *
 * A route with no slope record — never sampled, or a pending share, whose
 * slope is not drawn — has no sample axis at all, and every function here
 * answers null for it rather than guessing a position.
 *
 * Pure: no DOM, no map, no globals read. map.js projects the midpoints
 * (`map.project`) and hands the pixels in.
 *
 * Exports (frozen `self.pwaRouteCursorMapCore`):
 *
 *   sampleCount(slope)                   → N, or 0 with no usable record
 *   selectionLine(slope, selection)      → LineString Feature, or null
 *   cursorPoint(slope, index)            → Point Feature, or null
 *   segmentMidpoints(slope)              → [[lon, lat]] per segment
 *   nearestSample(midpointsPx, px, maxPx) → the nearest index, or null
 *   legAt(legs, index)                   → the leg holding an index, or null
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {{points?: Array<Array<number>>, angles?: Array<?number>}} Slope
   *   The slope record off a route feature, already parsed.
   */

  /**
   * @typedef {{type: 'Feature', geometry: {type: string,
   *   coordinates: Array<*>}, properties: Object<string, *>}} Feature
   */

  /**
   * Whether a value is a usable [lon, lat] pair.
   *
   * @param {*} point
   * @returns {boolean}
   */
  function isPoint(point) {
    return Array.isArray(point)
      && point.length >= 2
      && Number.isFinite(point[0])
      && Number.isFinite(point[1]);
  }

  /**
   * How many segments a slope record draws: one fewer than its points.
   *
   * @param {?Slope} slope
   * @returns {number} 0 when there is no record or it has under two points.
   */
  function sampleCount(slope) {
    if (!slope || !Array.isArray(slope.points)) return 0;
    return Math.max(0, slope.points.length - 1);
  }

  /**
   * The stretch of line a selection covers.
   *
   * @param {?Slope} slope
   * @param {?{kind?: string, from: number, to: number}} selection Sample
   *   indices, both inclusive.
   * @returns {?Feature} Null for no selection, no record, or a range that
   *   is not whole indices inside the route.
   */
  function selectionLine(slope, selection) {
    const count = sampleCount(slope);
    if (!selection || !count || !slope || !slope.points) return null;
    const from = selection.from;
    const to = selection.to;
    if (!Number.isInteger(from) || !Number.isInteger(to)) return null;
    if (from < 0 || to >= count || from > to) return null;
    const coordinates = slope.points.slice(from, to + 2);
    if (!coordinates.every(isPoint)) return null;
    return {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: coordinates.map((p) => [p[0], p[1]]),
      },
      properties: { kind: selection.kind || null, from: from, to: to },
    };
  }

  /**
   * The middle of each segment, in [lon, lat].
   *
   * A straight average of the two ends: a segment is 25 m, far too short
   * for the curve of a meridian to matter.
   *
   * @param {?Slope} slope
   * @returns {Array<?Array<number>>} One per segment; null where either end
   *   is not a usable point.
   */
  function segmentMidpoints(slope) {
    const count = sampleCount(slope);
    if (!count || !slope || !slope.points) return [];
    const points = slope.points;
    /** @type {Array<?Array<number>>} */
    const out = [];
    for (let i = 0; i < count; i += 1) {
      const a = points[i];
      const b = points[i + 1];
      out.push(isPoint(a) && isPoint(b) ? [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] : null);
    }
    return out;
  }

  /**
   * Where the cursor index sits: the middle of its segment.
   *
   * @param {?Slope} slope
   * @param {?number} index
   * @returns {?Feature} Null for a null index, no record, or an index
   *   outside the route.
   */
  function cursorPoint(slope, index) {
    const count = sampleCount(slope);
    if (index === null || !Number.isInteger(index) || !count) return null;
    if (index < 0 || index >= count) return null;
    const middle = segmentMidpoints(slope)[index];
    if (!middle) return null;
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: middle },
      properties: { index: index },
    };
  }

  /**
   * The sample whose segment middle is nearest a screen point.
   *
   * @param {Array<?{x: number, y: number}>} midpointsPx Each segment's
   *   middle, projected to screen pixels; null for one with no position.
   * @param {{x: number, y: number}} px The pointer.
   * @param {number} [maxPx] Beyond this distance nothing is near. Defaults
   *   to no limit.
   * @returns {?number} The index, or null when nothing is within `maxPx`.
   */
  function nearestSample(midpointsPx, px, maxPx) {
    if (!Array.isArray(midpointsPx) || !px) return null;
    const limit = maxPx === undefined ? Infinity : maxPx;
    let best = null;
    let bestDistance = Infinity;
    for (let i = 0; i < midpointsPx.length; i += 1) {
      const m = midpointsPx[i];
      if (!m || !Number.isFinite(m.x) || !Number.isFinite(m.y)) continue;
      const distance = Math.hypot(m.x - px.x, m.y - px.y);
      if (distance < bestDistance) {
        best = i;
        bestDistance = distance;
      }
    }
    return best !== null && bestDistance <= limit ? best : null;
  }

  /**
   * The leg holding a sample index.
   *
   * @template {{from: number, to: number}} L
   * @param {?Array<L>} legs A route's legs, in sample indices.
   * @param {?number} index
   * @returns {?L}
   */
  function legAt(legs, index) {
    if (!Array.isArray(legs) || index === null || !Number.isInteger(index)) return null;
    return legs.find((leg) => leg && index >= leg.from && index <= leg.to) || null;
  }

  self.pwaRouteCursorMapCore = Object.freeze({
    sampleCount: sampleCount,
    selectionLine: selectionLine,
    cursorPoint: cursorPoint,
    segmentMidpoints: segmentMidpoints,
    nearestSample: nearestSample,
    legAt: legAt,
  });
})();
