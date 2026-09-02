/*
 * static/js/route_markers_core.js — the start and finish markers on a
 * saved route.
 *
 * A route line says where the track goes and nothing about which way round
 * it goes. These two symbols answer that: a filled dot where it starts, a
 * checkered flag where it ends. They are the Strava/Komoot convention and
 * they read the same way on a paper map — the point of a flag is that it
 * needs no legend.
 *
 * This module owns the PIXELS and the GEOMETRY, both of which are
 * arithmetic, and neither of which `map.js` could have tested. It owns no
 * colour: `map.js` resolves the design tokens (a CSS `@theme` value, so
 * possibly `oklch()`, which only a canvas fill parses reliably) and passes
 * the channels in — exactly the split `hatch_core.js` uses, and for the
 * same reason.
 *
 * ## Why the icons are raw pixels rather than canvas paths
 *
 * The favourite star and the community-report flag are Path2D fills
 * registered `sdf: true`, which is what lets `icon-color` retint them
 * per-layer. A CHECKERED flag cannot be an SDF: SDF discards colour and
 * keeps only the alpha mask, so a two-tone glyph collapses to a
 * silhouette — a black rectangle on a stick, which is not a checkered flag
 * and does not read as a finish line. It has to be a full-colour image.
 *
 * The other full-colour icons on this map (the weather symbol set)
 * are `<img>`-decoded SVGs, which is asynchronous and needs a file. A
 * checkerboard and a disc are a few lines of arithmetic, so these are
 * built as a MapLibre `StyleImage` — `{width, height, data}` over a
 * `Uint8ClampedArray` — the same shape `hatch_core.js` hands to
 * `map.addImage`. Synchronous, no asset to ship, and the invariant worth
 * testing (that a checker actually alternates) is a pure function of x
 * and y.
 *
 * ## Exports (frozen `self.pwaRouteMarkersCore`)
 *
 *   SIZE / PIXEL_RATIO
 *   endpointsGeojson(routesCollection)
 *   startDotPixels(r, g, b)
 *   finishFlagPixels(r, g, b)
 */

(function () {
  'use strict';

  /**
   * Icon edge in DEVICE pixels. Registered at `pixelRatio: 2`, so both
   * symbols occupy 20 CSS pixels — near the ~18px the community-report
   * flag renders at, so a route's ends sit in the same size class as every
   * other marker on the map rather than shouting over them.
   */
  const SIZE = 40;

  /** The ratio `map.addImage` must be given to match `SIZE`. */
  const PIXEL_RATIO = 2;

  /** Radius of the start disc, in device pixels. */
  const DOT_RADIUS = 11;

  /** White ring around the start disc, in device pixels. */
  const DOT_RING = 3;

  /** The flagpole's left edge, width, top and bottom, in device pixels. */
  const POLE_X = 8;
  const POLE_W = 3;
  const POLE_TOP = 5;
  const POLE_BOTTOM = 35;

  /** The cloth's box, in device pixels. Four columns by three rows. */
  const CLOTH_X = POLE_X + POLE_W;
  const CLOTH_Y = 5;
  const CLOTH_COLS = 4;
  const CLOTH_ROWS = 3;
  const CELL = 6;

  /**
   * Whether a coordinate pair is two finite numbers.
   *
   * @param {*} position A candidate GeoJSON position.
   * @returns {boolean}
   */
  function isPosition(position) {
    return (
      Array.isArray(position)
      && typeof position[0] === 'number'
      && typeof position[1] === 'number'
      && isFinite(position[0])
      && isFinite(position[1])
    );
  }

  /**
   * Derive the start/finish marker points from the routes layer's data.
   *
   * The markers are their own point source rather than anything computed
   * from the line layer, because MapLibre cannot symbolise "the ends of a
   * LineString" — `symbol-placement: 'line'` repeats a symbol ALONG a
   * line, which is a different thing entirely.
   *
   * A CLOSED TRACK GETS ONE MARKER, NOT TWO. An out-and-back finishes at
   * the car it started from, and that is most ski tours: emitting both
   * would stack a flag exactly on a dot, hiding one of them and leaving
   * the reader unable to tell a loop from a one-way route whose other end
   * is off screen. So when the two ends are the same position the flag is
   * emitted alone — it is the marker that was asked for, and "the track
   * both starts and ends here" is what one marker at one place says.
   *
   * Equality is exact rather than within a tolerance. These coordinates
   * are the stored track's own endpoints, not a computation over them, so
   * a genuine loop closes on the identical pair of numbers; an
   * epsilon would additionally swallow a short there-and-back that ends a
   * few metres from its start, which has two ends and should show two.
   *
   * @param {object} collection The routes FeatureCollection, as served by
   *   `routes:geojson` (LineStrings; positions may carry a third
   *   elevation ordinate, which is dropped here).
   * @returns {{type: string, features: Array<object>}} A point
   *   FeatureCollection. Each feature carries `role` (`'start'` or
   *   `'end'`), plus the route's `uuid` and `name` so a marker can be
   *   traced back to its line.
   */
  function endpointsGeojson(collection) {
    const features = [];
    const routes = (collection && collection.features) || [];

    for (let i = 0; i < routes.length; i += 1) {
      const route = routes[i] || {};
      const geometry = route.geometry || {};
      const coordinates = geometry.coordinates;
      // Under two positions there is no line, so no direction to mark.
      if (!Array.isArray(coordinates) || coordinates.length < 2) continue;

      const first = coordinates[0];
      const last = coordinates[coordinates.length - 1];
      if (!isPosition(first) || !isPosition(last)) continue;

      const props = route.properties || {};
      const marker = (position, role) => ({
        type: 'Feature',
        // Longitude and latitude only: a symbol layer has no use for the
        // elevation the line's own positions carry.
        geometry: { type: 'Point', coordinates: [position[0], position[1]] },
        properties: { role: role, uuid: props.uuid, name: props.name },
      });

      const closed = first[0] === last[0] && first[1] === last[1];
      if (!closed) features.push(marker(first, 'start'));
      features.push(marker(last, 'end'));
    }

    return { type: 'FeatureCollection', features: features };
  }

  /**
   * Allocate a transparent RGBA buffer for one icon.
   *
   * @returns {Uint8ClampedArray} `SIZE * SIZE * 4` zeroed bytes.
   */
  function blank() {
    return new Uint8ClampedArray(SIZE * SIZE * 4);
  }

  /**
   * Paint one pixel.
   *
   * @param {Uint8ClampedArray} data The buffer.
   * @param {number} x Column.
   * @param {number} y Row.
   * @param {Array<number>} rgba Four channels, 0-255.
   */
  function put(data, x, y, rgba) {
    if (x < 0 || y < 0 || x >= SIZE || y >= SIZE) return;
    const offset = (y * SIZE + x) * 4;
    data[offset] = rgba[0];
    data[offset + 1] = rgba[1];
    data[offset + 2] = rgba[2];
    data[offset + 3] = rgba[3];
  }

  /**
   * Fill an axis-aligned rectangle.
   *
   * @param {Uint8ClampedArray} data The buffer.
   * @param {number} x Left edge.
   * @param {number} y Top edge.
   * @param {number} width Width in pixels.
   * @param {number} height Height in pixels.
   * @param {Array<number>} rgba Four channels, 0-255.
   */
  function rect(data, x, y, width, height, rgba) {
    for (let row = y; row < y + height; row += 1) {
      for (let column = x; column < x + width; column += 1) {
        put(data, column, row, rgba);
      }
    }
  }

  /**
   * The start marker: a filled disc in the route's own colour, ringed white.
   *
   * The ring is not decoration. The disc is the same fuchsia as the line it
   * sits on, so without a ring the marker dissolves into the track at the
   * exact place the reader is looking; white separates the two and also
   * carries the disc over a dark basemap.
   *
   * @param {number} r Red channel of the route colour, 0-255.
   * @param {number} g Green channel.
   * @param {number} b Blue channel.
   * @returns {{width: number, height: number, data: Uint8ClampedArray}} A
   *   MapLibre StyleImage.
   */
  function startDotPixels(r, g, b) {
    const data = blank();
    const centre = SIZE / 2 - 0.5;
    const fill = [r, g, b, 255];
    const ring = [255, 255, 255, 255];
    const outer = DOT_RADIUS + DOT_RING;

    for (let y = 0; y < SIZE; y += 1) {
      for (let x = 0; x < SIZE; x += 1) {
        const dx = x - centre;
        const dy = y - centre;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance <= DOT_RADIUS) put(data, x, y, fill);
        else if (distance <= outer) put(data, x, y, ring);
      }
    }
    return { width: SIZE, height: SIZE, data: data };
  }

  /**
   * The finish marker: a checkered flag on a pole.
   *
   * The dark cells take the caller's ink (the route line's own casing
   * colour, so the flag belongs to the track it ends) and the light cells
   * are white — a checkered flag is black-and-white by definition, and a
   * retinted "checker" in two brand colours stops reading as a finish
   * line, which is the entire reason to use one.
   *
   * The cloth carries a one-pixel border in the same ink. Without it the
   * white cells on the cloth's edge bleed into a pale basemap and the flag
   * loses its rectangle.
   *
   * @param {number} r Red channel of the ink, 0-255.
   * @param {number} g Green channel.
   * @param {number} b Blue channel.
   * @returns {{width: number, height: number, data: Uint8ClampedArray}} A
   *   MapLibre StyleImage.
   */
  function finishFlagPixels(r, g, b) {
    const data = blank();
    const ink = [r, g, b, 255];
    const light = [255, 255, 255, 255];

    rect(data, POLE_X, POLE_TOP, POLE_W, POLE_BOTTOM - POLE_TOP, ink);

    for (let row = 0; row < CLOTH_ROWS; row += 1) {
      for (let column = 0; column < CLOTH_COLS; column += 1) {
        // The parity that makes it a checker: neighbours in either
        // direction always differ.
        const dark = (row + column) % 2 === 0;
        rect(
          data,
          CLOTH_X + column * CELL,
          CLOTH_Y + row * CELL,
          CELL,
          CELL,
          dark ? ink : light,
        );
      }
    }

    const clothWidth = CLOTH_COLS * CELL;
    const clothHeight = CLOTH_ROWS * CELL;
    rect(data, CLOTH_X, CLOTH_Y - 1, clothWidth, 1, ink);
    rect(data, CLOTH_X, CLOTH_Y + clothHeight, clothWidth, 1, ink);
    rect(data, CLOTH_X + clothWidth, CLOTH_Y - 1, 1, clothHeight + 2, ink);

    return { width: SIZE, height: SIZE, data: data };
  }

  self.pwaRouteMarkersCore = Object.freeze({
    SIZE: SIZE,
    PIXEL_RATIO: PIXEL_RATIO,
    endpointsGeojson: endpointsGeojson,
    startDotPixels: startDotPixels,
    finishFlagPixels: finishFlagPixels,
  });
})();
