/*
 * static/js/route_cursor_map_core.js — the route cursor, placed on the map
 * (SNOW-1019).
 *
 * The route cursor (route_cursor_core.js) is one sample index and one open
 * leg, shared by the map and both rails. The rails draw
 * it by share along their own x-axis; the map draws it in GEOGRAPHY, from
 * the boundary points the slope record already carries:
 *
 *     properties.slope = { points: [[lon, lat], …],   // N + 1
 *                          angles: [ … ] }             // N
 *
 * Segment i runs from points[i] to points[i + 1], and the cursor index i
 * is drawn at the middle of its segment. Going the other way, a pointer on the map is
 * converted to the sample whose segment middle is nearest on SCREEN,
 * which is the question a reader's pointer asks: "which bit of line am I
 * on", in the pixels they can see.
 *
 * A route with no slope record — never sampled, or a pending share, whose
 * slope is not drawn — has no sample axis at all, and every function here
 * answers null for it rather than guessing a position.
 *
 * Pure: no DOM, no map, and one global read — `cursorPoint` looks the
 * segment's colour up in `self.pwaRouteSlopeCore` when it is called, never
 * at parse time. map.js projects the midpoints (`map.project`) and hands
 * the pixels in.
 *
 * ## The dot is the colour of the ground under it (SNOW-1052)
 *
 * The cursor's feature carries `colour`: the hex of its segment's slope
 * class, `pwaRouteSlopeCore.CLASSES[classify(angle)].hex`, which mirrors
 * the `--color-slope-*` token rail two fills that band with — so the dot
 * on the map and the band under rail two's cursor line are one colour. A
 * segment the terrain had no answer for takes `UNKNOWN_COLOUR`, as the
 * line does. With no slope core loaded it carries no `colour`, and map.js
 * falls back to the route's own colour.
 *
 * Nothing here draws a stretch of line. SNOW-1052 removed band and
 * passage selection from rail two, and with it the highlighted stretch
 * (`selectionLine`) the map drew for a selection; the map draws the
 * cursor's dot and nothing else.
 *
 * Exports (frozen `self.pwaRouteCursorMapCore`):
 *
 *   sampleCount(slope)                   → N, or 0 with no usable record
 *   cursorPoint(slope, index)            → Point Feature, or null; its
 *                                          `colour` is the segment's class
 *   segmentMidpoints(slope)              → [[lon, lat]] per segment
 *   nearestSample(midpointsPx, px, maxPx) → the nearest index, or null
 *   legAt(legs, index)                   → the leg holding an index, or null
 *   visibleRect(canvas, railTop, topInset) → the map the rails leave visible
 *   isInside(point, rect)                → whether a point is in a rect
 *   panOffset(point, rect, margin)       → the pan that brings it in, or null
 *
 * ## The visible map (SNOW-1019)
 *
 * On a phone, with a leg open, the rails cover the bottom two-thirds of
 * the map, and the cursor's dot can land behind them. The map that is
 * actually visible is the canvas above the rail's top edge and below the
 * top chrome. `panOffset` answers how far to pan so the dot is back
 * inside it, `margin` px in from every edge — in `panBy`'s own sign: a
 * dot below the rail's top gives a POSITIVE y, which moves the view down
 * the map and the dot up the screen.
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
   * The hex a segment is drawn in: its slope class's, or the unknown grey.
   *
   * @param {?Slope} slope
   * @param {number} index
   * @returns {?string} Null when no slope core is loaded.
   */
  function segmentColour(slope, index) {
    const slopeCore = self.pwaRouteSlopeCore;
    if (!slopeCore || !Array.isArray(slopeCore.CLASSES)) return null;
    const angle = slope && Array.isArray(slope.angles) ? slope.angles[index] : null;
    const classIndex = slopeCore.classify(angle);
    if (classIndex === null) return slopeCore.UNKNOWN_COLOUR || null;
    const entry = slopeCore.CLASSES[classIndex];
    return entry && typeof entry.hex === 'string' ? entry.hex : null;
  }

  /**
   * Where the cursor index sits: the middle of its segment, in its colour.
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
    /** @type {Object<string, *>} */
    const properties = { index: index };
    const colour = segmentColour(slope, index);
    if (colour) properties.colour = colour;
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: middle },
      properties: properties,
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

  /**
   * @typedef {{left: number, top: number, right: number, bottom: number}} Rect
   */

  /**
   * The part of the map canvas the rail and the top chrome leave visible.
   *
   * @param {?Rect} canvas The map container's rect, viewport px.
   * @param {?number} railTop The rail's top edge, viewport px; null when
   *   no rail is open.
   * @param {number} topInset The top chrome's height, px.
   * @returns {?Rect} Null for a canvas with no size (not laid out yet).
   */
  function visibleRect(canvas, railTop, topInset) {
    if (!canvas || !(canvas.right > canvas.left) || !(canvas.bottom > canvas.top)) return null;
    const bottom = typeof railTop === 'number' && Number.isFinite(railTop)
      ? Math.min(canvas.bottom, railTop)
      : canvas.bottom;
    return {
      left: canvas.left,
      top: Math.min(bottom, canvas.top + (topInset || 0)),
      right: canvas.right,
      bottom: bottom,
    };
  }

  /**
   * Whether a point lies inside a rect, edges included.
   *
   * @param {?{x: number, y: number}} point
   * @param {?Rect} rect
   * @returns {boolean}
   */
  function isInside(point, rect) {
    if (!point || !rect) return false;
    return point.x >= rect.left && point.x <= rect.right
      && point.y >= rect.top && point.y <= rect.bottom;
  }

  /**
   * One axis of `panOffset`.
   *
   * @param {number} value The point's coordinate.
   * @param {number} low The rect's low edge.
   * @param {number} high The rect's high edge.
   * @param {number} margin
   * @returns {number}
   */
  function axisOffset(value, low, high, margin) {
    if (high - low <= margin * 2) return value - (low + high) / 2;
    if (value < low + margin) return value - (low + margin);
    if (value > high - margin) return value - (high - margin);
    return 0;
  }

  /**
   * The pan that brings a point `margin` px inside a rect.
   *
   * @param {?{x: number, y: number}} point The point, viewport px.
   * @param {?Rect} rect The visible map, viewport px.
   * @param {number} margin How far inside the edges to bring it.
   * @returns {?{x: number, y: number}} `panBy`'s offset, or null when the
   *   point is already inside (or either is unknown).
   */
  function panOffset(point, rect, margin) {
    if (!point || !rect || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
    const m = margin || 0;
    const x = axisOffset(point.x, rect.left, rect.right, m);
    const y = axisOffset(point.y, rect.top, rect.bottom, m);
    if (Math.abs(x) < 0.5 && Math.abs(y) < 0.5) return null;
    return { x: x, y: y };
  }

  self.pwaRouteCursorMapCore = Object.freeze({
    visibleRect: visibleRect,
    isInside: isInside,
    panOffset: panOffset,
    sampleCount: sampleCount,
    cursorPoint: cursorPoint,
    segmentMidpoints: segmentMidpoints,
    nearestSample: nearestSample,
    legAt: legAt,
  });
})();
