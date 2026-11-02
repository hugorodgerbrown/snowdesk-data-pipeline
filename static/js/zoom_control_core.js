/*
 * static/js/zoom_control_core.js — the pure half of the map's zoom control
 * (SNOW-1077).
 *
 * The control is the roundel at the top of the bottom-left stack: its face
 * says the zoom the map is drawing, and the flyout it opens holds − and +,
 * a horizontal scale across the map's zoom range, and one tick for each
 * zoom-gated layer that is on — the terrain filter from z12, a route's
 * slope colours from z14. Two of the map's layers appear only past a zoom
 * the map never stated, and zooming had been gesture-only, which a touch
 * user cannot always do (WCAG 2.5.1).
 *
 * This file holds the arithmetic; static/js/map_zoom_control.js holds the
 * DOM and MapLibre. It is its own module for the reason every `*_core.js`
 * is: the control reads `MAP` as a bare global and runs at parse time, so
 * jsdom cannot import it on its own, and an off-by-one at a bound would
 * leave + live at the map's maximum without any error anywhere.
 *
 * THE FACE IS THE TILE ZOOM, a whole number. The camera is continuous, and
 * MapLibre fetches vector tiles at floor(camera) clamped to the source's
 * range, so z13.99 is still drawing z13 tiles. `faceZoom` takes the tile
 * zoom the control read off MapLibre and falls back to floor(camera) when
 * there is none (a style mid-swap, or the offline fallback style with no
 * vector source).
 *
 * THE GATES come from `window.pwaZoomGates` (map.js), one entry per
 * zoom-gated feature: `{ key, zoom, upTo }`. `ticks` keeps every one inside
 * the map's range, switched on or not, sorted by zoom, and says for each
 * where it sits on the scale and whether the camera is at a zoom where it
 * is available. `levels` numbers every whole zoom on the line.
 *
 * THE LABELS sit in their own column, away from the axis, and a curved
 * connector runs from each gate's level to its label (2026-11-02: five
 * gates crowd z9–z12, and two can share a level). `labelRows` places the
 * labels: each wants to sit level with its gate, and where neighbours
 * would overlap they are spread as one evenly spaced group centred on
 * where they want to be, so no label strays further than it must and any
 * number can share a level. `connectorPath` draws the curve between the
 * two — horizontal at both ends, so it leaves the axis and meets the label
 * square on.
 *
 * Every function here is pure.
 *
 * Exports (frozen `self.pwaZoomControlCore`):
 *
 *   EPSILON
 *   scalePosition(zoom, minZoom, maxZoom)   → 0..1
 *   faceZoom(tileZoom, cameraZoom)          → whole number
 *   buttonState(zoom, minZoom, maxZoom)     → { canZoomOut, canZoomIn }
 *   ticks(gates, zoom, minZoom, maxZoom)    → Array<Tick>
 *   levels(minZoom, maxZoom)                → Array<{zoom, position}>
 *   isReached(gate, zoom)                   → boolean
 *   zoomRange(gates)                        → "10–12" | "10" | ""
 *   labelRows(ticks, height, pitch)         → Array<number> (px, top-down)
 *   connectorPath(x0, y0, x1, y1)           → SVG path data
 *
 * The last two serve the layers menu too (SNOW-1280,
 * static/js/map_layers_zoom_gates.js): a zoom-limited row fades while its
 * layer is on and `isReached` is false, and the menu's note names the
 * span of the gates it shows.
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {object} Gate
   * @property {string} key The feature's name in the gate registry.
   * @property {number} zoom The zoom from which it is available, or up to
   *   which for an `upTo` gate.
   * @property {boolean} [upTo] Whether it holds up to its zoom, not from it.
   */

  /**
   * @typedef {object} Tick
   * @property {string} key The gate's key.
   * @property {number} zoom The gate's zoom.
   * @property {number} position Where the tick sits on the scale, 0..1.
   * @property {boolean} upTo Whether the gate holds up to its zoom.
   * @property {boolean} reached Whether the feature is available at the
   *   camera's zoom.
   */

  /**
   * How close to a bound counts as AT it. MapLibre clamps the camera to
   * `maxZoom`, but an eased zoom can settle a hair short of it, and a +
   * left live for a step that cannot move the map is a control that lies.
   */
  const EPSILON = 0.001;

  /**
   * Where a zoom sits on the scale, as a fraction of its width.
   *
   * Clamped, so a camera outside the range (a degenerate range, or a style
   * reporting a bound the map does not) still marks an end of the scale
   * rather than a point off it.
   *
   * @param {number} zoom The zoom to place.
   * @param {number} minZoom The map's minimum zoom.
   * @param {number} maxZoom The map's maximum zoom.
   * @returns {number} 0 at minZoom, 1 at maxZoom.
   */
  function scalePosition(zoom, minZoom, maxZoom) {
    const span = maxZoom - minZoom;
    if (!(span > 0) || !Number.isFinite(zoom)) return 0;
    return Math.min(1, Math.max(0, (zoom - minZoom) / span));
  }

  /**
   * The whole zoom the roundel's face shows.
   *
   * @param {number|null} tileZoom The tile zoom MapLibre is fetching, or
   *   null when no vector source answered.
   * @param {number} cameraZoom The camera's continuous zoom.
   * @returns {number} The tile zoom, or floor(camera) without one.
   */
  function faceZoom(tileZoom, cameraZoom) {
    if (typeof tileZoom === 'number' && Number.isFinite(tileZoom)) {
      return Math.floor(tileZoom);
    }
    return Math.floor(cameraZoom + EPSILON);
  }

  /**
   * Whether − and + can still move the map.
   *
   * @param {number} zoom The camera's zoom.
   * @param {number} minZoom The map's minimum zoom.
   * @param {number} maxZoom The map's maximum zoom.
   * @returns {{canZoomOut: boolean, canZoomIn: boolean}}
   */
  function buttonState(zoom, minZoom, maxZoom) {
    return {
      canZoomOut: zoom > minZoom + EPSILON,
      canZoomIn: zoom < maxZoom - EPSILON,
    };
  }

  /**
   * Whether a gated feature is available at a zoom.
   *
   * An `upTo` gate holds through its whole zoom (z10.9 still reads "10");
   * every other gate holds from its zoom up, a hair early so an eased zoom
   * that settles at 9.9999 counts as 10.
   *
   * @param {Gate} gate The gate.
   * @param {number} zoom The camera's zoom.
   * @returns {boolean}
   */
  function isReached(gate, zoom) {
    return gate.upTo
      ? Math.floor(zoom + EPSILON) <= gate.zoom
      : zoom + EPSILON >= gate.zoom;
  }

  /**
   * The span of zooms a set of gates covers, for a sentence that names it:
   * "10–12" across two or more distinct zooms, "10" for one, "" for none.
   *
   * @param {ReadonlyArray<Gate>|null|undefined} gates
   * @returns {string}
   */
  function zoomRange(gates) {
    if (!Array.isArray(gates)) return '';
    const zooms = gates.map((gate) => gate && gate.zoom).filter(Number.isFinite);
    if (!zooms.length) return '';
    const low = Math.min(...zooms);
    const high = Math.max(...zooms);
    return low === high ? String(low) : low + '\u2013' + high;
  }

  /**
   * The ticks the scale draws: one per gate inside the map's range.
   *
   * Whether the feature is switched on does not matter: the scale says
   * where each becomes available, before the reader turns it on. A gate
   * outside the range is dropped rather than pinned to an end: a
   * tick at the edge would claim the layer starts there.
   *
   * @param {ReadonlyArray<Gate>|null|undefined} gates The gate registry's list.
   * @param {number} zoom The camera's zoom.
   * @param {number} minZoom The map's minimum zoom.
   * @param {number} maxZoom The map's maximum zoom.
   * At a shared zoom an `upTo` gate sorts after a `from` one, so
   * `labelRows` sets it below: it holds from there DOWN the scale, and the
   * other from there up.
   *
   * @returns {Array<Tick>} Sorted by zoom, then from-before-upTo, then key.
   */
  function ticks(gates, zoom, minZoom, maxZoom) {
    if (!Array.isArray(gates)) return [];
    /** @type {Array<Tick>} */
    const out = gates
      .filter((gate) => gate && Number.isFinite(gate.zoom)
        && gate.zoom >= minZoom && gate.zoom <= maxZoom)
      .map((gate) => ({
        key: String(gate.key),
        zoom: gate.zoom,
        position: scalePosition(gate.zoom, minZoom, maxZoom),
        upTo: !!gate.upTo,
        reached: isReached(gate, zoom),
      }))
      .sort((a, b) => a.zoom - b.zoom
        || Number(a.upTo) - Number(b.upTo)
        || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    return out;
  }

  /**
   * Every whole zoom level inside the map's range, placed on the scale.
   *
   * @param {number} minZoom The map's minimum zoom.
   * @param {number} maxZoom The map's maximum zoom.
   * @returns {Array<{zoom: number, position: number}>} Lowest first.
   */
  function levels(minZoom, maxZoom) {
    if (!Number.isFinite(minZoom) || !Number.isFinite(maxZoom)) return [];
    const out = [];
    for (let z = Math.ceil(minZoom); z <= Math.floor(maxZoom); z += 1) {
      out.push({ zoom: z, position: scalePosition(z, minZoom, maxZoom) });
    }
    return out;
  }

  /**
   * Where each tick's label sits, in px down the scale.
   *
   * Each label wants the height of its gate's level. Labels closer than
   * `pitch` are merged into one group, spaced `pitch` apart and centred on
   * the mean of where its members want to be — then kept inside the scale,
   * which can push a group into its neighbour, so merging repeats until
   * nothing overlaps. Two gates on one level fan either side of it, the
   * one listed first (a `from` gate, by `ticks`' order) above.
   *
   * @param {ReadonlyArray<{position: number}>} items Ticks, as `ticks` sorts them.
   * @param {number} height The scale's height in px.
   * @param {number} pitch The distance between two labels' centres in px.
   * @returns {Array<number>} One centre per item, in the items' order.
   */
  function labelRows(items, height, pitch) {
    if (!Array.isArray(items) || !items.length) return [];
    const ideal = items.map((item) => (1 - item.position) * height);
    // Top of the scale first; on a shared level, the earlier tick first.
    const order = ideal.map((_y, i) => i).sort((a, b) => ideal[a] - ideal[b] || a - b);
    const lo = pitch / 2;
    const hi = height - pitch / 2;
    /** @type {Array<{members: Array<number>, sum: number, top: number}>} */
    const groups = [];
    const place = (/** @type {{members: Array<number>, sum: number, top: number}} */ g) => {
      const span = (g.members.length - 1) * pitch;
      const centred = g.sum / g.members.length - span / 2;
      g.top = Math.max(lo, Math.min(hi - span, centred));
    };
    for (const index of order) {
      const group = { members: [index], sum: ideal[index], top: 0 };
      place(group);
      groups.push(group);
      while (groups.length > 1) {
        const last = groups[groups.length - 1];
        const prev = groups[groups.length - 2];
        if (prev.top + prev.members.length * pitch <= last.top + EPSILON) break;
        prev.members.push(...last.members);
        prev.sum += last.sum;
        groups.pop();
        place(prev);
      }
    }
    const out = new Array(items.length);
    for (const g of groups) {
      g.members.forEach((index, k) => { out[index] = g.top + k * pitch; });
    }
    return out;
  }

  /**
   * The connector from a gate's level on the axis to its label: a cubic
   * curve, horizontal where it leaves the axis and where it meets the
   * label, so a label level with its gate gets a straight line.
   *
   * @param {number} x0 The axis, in px.
   * @param {number} y0 The gate's level, in px down the scale.
   * @param {number} x1 Where the label begins, in px.
   * @param {number} y1 The label's centre, in px down the scale.
   * @returns {string} SVG path data.
   */
  function connectorPath(x0, y0, x1, y1) {
    const r = (/** @type {number} */ n) => Math.round(n * 100) / 100;
    const mid = x0 + (x1 - x0) * 0.55;
    return 'M' + r(x0) + ' ' + r(y0)
      + ' C' + r(mid) + ' ' + r(y0) + ' ' + r(mid) + ' ' + r(y1) + ' ' + r(x1) + ' ' + r(y1);
  }

  self.pwaZoomControlCore = Object.freeze({
    labelRows: labelRows,
    connectorPath: connectorPath,
    levels: levels,
    EPSILON: EPSILON,
    scalePosition: scalePosition,
    faceZoom: faceZoom,
    buttonState: buttonState,
    ticks: ticks,
    isReached: isReached,
    zoomRange: zoomRange,
  });
})();
