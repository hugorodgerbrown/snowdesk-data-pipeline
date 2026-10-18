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
 * zoom-gated layer: `{ key, zoom, active }`. `ticks` keeps the active ones
 * inside the map's range, sorted by zoom, and says for each where it sits
 * on the scale and whether the camera has reached it.
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
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {object} Gate
   * @property {string} key The layer's name in the gate registry.
   * @property {number} zoom The zoom from which the layer is drawn.
   * @property {boolean} active Whether the layer is on now.
   */

  /**
   * @typedef {object} Tick
   * @property {string} key The gate's key.
   * @property {number} zoom The gate's zoom.
   * @property {number} position Where the tick sits on the scale, 0..1.
   * @property {boolean} reached Whether the camera is at or past the gate.
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
   * The ticks the scale draws: one per active gate inside the map's range.
   *
   * A gate that is off draws nothing — a tick for a layer that is not on
   * the map would name a threshold for something the reader cannot see.
   * A gate outside the range is dropped rather than pinned to an end: a
   * tick at the edge would claim the layer starts there.
   *
   * @param {ReadonlyArray<Gate>|null|undefined} gates The gate registry's list.
   * @param {number} zoom The camera's zoom.
   * @param {number} minZoom The map's minimum zoom.
   * @param {number} maxZoom The map's maximum zoom.
   * @returns {Array<Tick>} Sorted by zoom, then key.
   */
  function ticks(gates, zoom, minZoom, maxZoom) {
    if (!Array.isArray(gates)) return [];
    return gates
      .filter((gate) => gate && Number.isFinite(gate.zoom)
        && gate.zoom >= minZoom && gate.zoom <= maxZoom)
      .map((gate) => ({
        key: String(gate.key),
        zoom: gate.zoom,
        position: scalePosition(gate.zoom, minZoom, maxZoom),
        active: !!gate.active,
        upTo: !!gate.upTo,
        // An `upTo` gate holds through its whole zoom (z10.9 still reads
        // "10"); every other gate holds from its zoom up.
        reached: gate.upTo
          ? Math.floor(zoom + EPSILON) <= gate.zoom
          : zoom + EPSILON >= gate.zoom,
      }))
      .sort((a, b) => a.zoom - b.zoom || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  }

  self.pwaZoomControlCore = Object.freeze({
    EPSILON: EPSILON,
    scalePosition: scalePosition,
    faceZoom: faceZoom,
    buttonState: buttonState,
    ticks: ticks,
  });
})();
