/*
 * static/js/hatch_core.js — the diagonal-hatch tile the downloaded-areas
 * overlay is painted with.
 *
 * The downloaded squares and the danger choropleth fill the same polygons.
 * As two translucent tints they were unreadable together — worse, a green
 * wash over a yellow region reads as a danger colour that region does not
 * have — so SNOW-656 made the two mutually exclusive. A hatch makes no
 * colour claim: it covers a third of the area in hard-edged strokes and
 * leaves the rest showing whatever is underneath, which is how an atlas
 * draws one area layer over another. That is what lets both be on at once.
 *
 * This module owns the PIXELS, not the colour: `map.js` resolves a basemap's
 * identity colour (a CSS token value, so possibly `oklch()` — parsed by a
 * 1×1 canvas fill, because nothing else parses every CSS colour syntax) and
 * passes the three channels in. Everything here is arithmetic, which is the
 * half worth testing: the stroke spacing has one invariant that fails
 * silently, and a seam down every pattern tile is not something a unit test
 * of `map.js` could ever catch.
 *
 * THE INVARIANT: `PERIOD` must divide `SIZE` exactly. A 45° stroke is the
 * set of pixels where `(x + y) mod PERIOD` is under `WIDTH`; that repeats
 * with period `PERIOD` along both axes, so the image only tiles seamlessly
 * when its edge is a whole number of periods. Get it wrong and MapLibre
 * repeats an image whose right edge does not continue into its own left one
 * — a visible grid of joins, at every zoom, on every downloaded area.
 * `tilesSeamlessly()` states it as a predicate so the test asserts the
 * property rather than a hard-coded pixel count.
 *
 * Exports (frozen `self.pwaHatchCore`):
 *
 *   SIZE / PERIOD / WIDTH
 *   hatchPixels(r, g, b)   — a MapLibre StyleImage: {width, height, data}
 *   tilesSeamlessly(size, period)
 */

(function () {
  'use strict';

  /**
   * The image edge, in device pixels. Added to the map at `pixelRatio: 2`,
   * so this is 6 CSS pixels on screen — fine enough to read as texture at
   * country zoom, coarse enough that the colour underneath shows between
   * the strokes rather than being averaged away.
   */
  const SIZE = 12;

  /** Device pixels from one stroke to the next. Must divide `SIZE`. */
  const PERIOD = 6;

  /** Stroke thickness in device pixels — a third of the period, so a third
   * of the area is covered and two thirds show the layer below. */
  const WIDTH = 2;

  /**
   * Whether an image of `size` tiles seamlessly at `period`.
   *
   * @param {number} size Image edge in pixels.
   * @param {number} period Pixels between strokes.
   * @returns {boolean}
   */
  function tilesSeamlessly(size, period) {
    return period > 0 && size % period === 0;
  }

  /**
   * The hatch as raw RGBA, in one colour.
   *
   * Alpha is binary — a stroke pixel is fully opaque, everything else fully
   * transparent — and the layer's `fill-opacity` is the single knob for how
   * strongly the whole hatch reads. Two multiplying alphas would mean
   * neither number is the one to tune.
   *
   * @param {number} r Red, 0–255.
   * @param {number} g Green, 0–255.
   * @param {number} b Blue, 0–255.
   * @returns {{width: number, height: number, data: Uint8ClampedArray}}
   *   A MapLibre StyleImage.
   */
  function hatchPixels(r, g, b) {
    const data = new Uint8ClampedArray(SIZE * SIZE * 4);
    for (let y = 0; y < SIZE; y += 1) {
      for (let x = 0; x < SIZE; x += 1) {
        const i = (y * SIZE + x) * 4;
        data[i] = r;
        data[i + 1] = g;
        data[i + 2] = b;
        data[i + 3] = (x + y) % PERIOD < WIDTH ? 255 : 0;
      }
    }
    return { width: SIZE, height: SIZE, data: data };
  }

  self.pwaHatchCore = Object.freeze({
    SIZE: SIZE,
    PERIOD: PERIOD,
    WIDTH: WIDTH,
    hatchPixels: hatchPixels,
    tilesSeamlessly: tilesSeamlessly,
  });
})();
