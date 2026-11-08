// @ts-check
/*
 * static/js/alpine_relief_core.js — our relief past swisstopo's detail
 * (SNOW-1460).
 *
 * swisstopo's winter style draws its relief from relief.vt: hachures and a
 * hillshade, which stop where swisstopo's detailed relief does
 * (static/js/swisstopo_extent.js DETAILED_RELIEF). Past it the winter map
 * would be flat, so the `alpinerelief://` raster protocol paints relief from
 * the elevation tiles the 3D view already reads (MAP_3D_DEM_TILE_URL,
 * Terrarium-encoded), in static/js/alpine_relief_worker.js. Two things:
 *
 *   - A NEUTRAL HILLSHADE, lit from the north-west at 45°: grey shadow on
 *     slopes facing away from the light, a faint white on slopes facing it,
 *     nothing on the flat.
 *   - A NEUTRAL GREY ROCK TONE on slopes steeper than ROCK_SLOPE_DEGREES,
 *     darker on the shadow side, where the ground is too steep to hold snow
 *     and swisstopo would draw rock.
 *
 * Inside the detailed relief the tile is erased (alpha 0), so swisstopo's
 * hachures are never covered. The layer sits above swisstopo's flat edge
 * shading (beyond_switzerland_core.js places it).
 *
 * Everything that needs no canvas is here, so Vitest can pin it:
 * Terrarium decoding, slope and aspect, the shade, the rock tone, the erase
 * mask and the whole tile's paint into RGBA.
 *
 * Exports (frozen `self.pwaAlpineReliefCore`):
 *
 *   PROTOCOL / TILE_SIZE / MIN_ZOOM / MAX_ZOOM / ROCK_SLOPE_DEGREES
 *   LIGHT_AZIMUTH / LIGHT_ALTITUDE
 *   parseUrl(url) / tileUrl(template, tile)
 *   terrarium(r, g, b)
 *   metresPerPixel(tile, size)
 *   slopeAspect(heights, size, col, row, cell)
 *   shade(slope, aspect)
 *   hillshadePixel(slope, aspect)
 *   rockPixel(slope, aspect)
 *   eraseMask(polygons, tile, size)
 *   paint(dem, size, tile, polygons)
 */
(function () {
  'use strict';

  /** The MapLibre protocol the relief tiles come through. */
  const PROTOCOL = 'alpinerelief';

  /**
   * Where an elevation tile comes from when our copy has none: AWS's
   * Terrarium tiles, the same fallback map_3d_core's DEM loader uses
   * (DEM_FALLBACK_TILE_URL there; a Vitest pins the two together). Our
   * copy answers 204 outside its supported area and 404 for a missing
   * tile, and without this the relief would have holes where 3D has none.
   */
  const DEM_FALLBACK_TILE_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';

  /** Our DEM tiles are 512 px; one relief tile is painted from one. */
  const TILE_SIZE = 512;

  /** The relief's zoom range; MapLibre enlarges the top tile above it. */
  const MIN_ZOOM = 6;
  const MAX_ZOOM = 14;

  /** Steeper than this, the ground is drawn as rock. */
  const ROCK_SLOPE_DEGREES = 40;

  /** Where the light comes from: the north-west, 45° up. */
  const LIGHT_AZIMUTH = 315;
  const LIGHT_ALTITUDE = 45;

  /** The shadow's grey and its strongest alpha (0..255). */
  const SHADOW_RGB = Object.freeze([64, 64, 64]);
  const SHADOW_MAX_ALPHA = 115;
  /** The highlight's white and its strongest alpha. */
  const HIGHLIGHT_RGB = Object.freeze([255, 255, 255]);
  const HIGHLIGHT_MAX_ALPHA = 60;
  /** The rock tone: lit and shadow-side greys, and its alpha. */
  const ROCK_LIT_RGB = Object.freeze([158, 158, 158]);
  const ROCK_SHADOW_RGB = Object.freeze([112, 112, 112]);
  const ROCK_ALPHA = 140;

  const EARTH_CIRCUMFERENCE = 40075016.686;
  const RAD = Math.PI / 180;

  /**
   * @typedef {{z: number, x: number, y: number}} Tile
   * @typedef {{rgb: ReadonlyArray<number>, alpha: number}} Pixel
   */

  /**
   * Read `alpinerelief://{z}/{x}/{y}`.
   *
   * @param {string} url
   * @returns {?Tile}
   */
  function parseUrl(url) {
    const match = /^alpinerelief:\/\/(\d+)\/(\d+)\/(\d+)(?:[?#].*)?$/.exec(url || '');
    if (!match) return null;
    return { z: Number(match[1]), x: Number(match[2]), y: Number(match[3]) };
  }

  /**
   * Fill an XYZ template.
   *
   * @param {string} template
   * @param {Tile} tile
   * @returns {string}
   */
  function tileUrl(template, tile) {
    return template
      .replace('{z}', String(tile.z))
      .replace('{x}', String(tile.x))
      .replace('{y}', String(tile.y));
  }

  /**
   * A Terrarium pixel's height in metres.
   *
   * @param {number} r
   * @param {number} g
   * @param {number} b
   * @returns {number}
   */
  function terrarium(r, g, b) {
    return r * 256 + g + b / 256 - 32768;
  }

  /**
   * The ground width of one pixel at a tile's centre latitude, in metres.
   *
   * @param {Tile} tile
   * @param {number} size
   * @returns {number}
   */
  function metresPerPixel(tile, size) {
    const n = Math.PI - (2 * Math.PI * (tile.y + 0.5)) / 2 ** tile.z;
    const lat = Math.atan(Math.sinh(n));
    return (EARTH_CIRCUMFERENCE * Math.cos(lat)) / (2 ** tile.z * size);
  }

  /**
   * The slope (degrees) and aspect (degrees clockwise from north, the way
   * the ground faces downhill) at one pixel, from its four neighbours; an
   * edge pixel reads its own height for the neighbour it lacks.
   *
   * @param {Float32Array|ReadonlyArray<number>} heights Row-major, `size` square.
   * @param {number} size
   * @param {number} col
   * @param {number} row
   * @param {number} cell Metres per pixel.
   * @returns {{slope: number, aspect: number}}
   */
  function slopeAspect(heights, size, col, row, cell) {
    const at = (/** @type {number} */ c, /** @type {number} */ r) =>
      heights[Math.min(size - 1, Math.max(0, r)) * size + Math.min(size - 1, Math.max(0, c))];
    const left = col > 0 ? col - 1 : col;
    const right = col < size - 1 ? col + 1 : col;
    const up = row > 0 ? row - 1 : row;
    const down = row < size - 1 ? row + 1 : row;
    const dx = (right - left) * cell || cell;
    const dy = (down - up) * cell || cell;
    const east = (at(right, row) - at(left, row)) / dx;
    // Rows run south, so north is up a row.
    const north = (at(col, up) - at(col, down)) / dy;
    const slope = Math.atan(Math.hypot(east, north)) / RAD;
    const aspect = east === 0 && north === 0
      ? 0
      : ((Math.atan2(-east, -north) / RAD) + 360) % 360;
    return { slope, aspect };
  }

  /**
   * How much light a slope gets, 0..1: 1 square on to the light, the cosine
   * of the light's zenith on the flat.
   *
   * @param {number} slope Degrees.
   * @param {number} aspect Degrees.
   * @returns {number}
   */
  function shade(slope, aspect) {
    const zenith = (90 - LIGHT_ALTITUDE) * RAD;
    const s = slope * RAD;
    const lit = Math.cos(zenith) * Math.cos(s)
      + Math.sin(zenith) * Math.sin(s) * Math.cos((LIGHT_AZIMUTH - aspect) * RAD);
    return Math.max(0, Math.min(1, lit));
  }

  /**
   * The neutral hillshade at one slope: a grey shadow, a faint white
   * highlight, or nothing on the flat.
   *
   * @param {number} slope
   * @param {number} aspect
   * @returns {?Pixel}
   */
  function hillshadePixel(slope, aspect) {
    const flat = Math.cos((90 - LIGHT_ALTITUDE) * RAD);
    const lit = shade(slope, aspect);
    if (lit < flat) {
      const alpha = Math.round(((flat - lit) / flat) * SHADOW_MAX_ALPHA);
      return alpha ? { rgb: SHADOW_RGB, alpha } : null;
    }
    const alpha = Math.round(((lit - flat) / (1 - flat)) * HIGHLIGHT_MAX_ALPHA);
    return alpha ? { rgb: HIGHLIGHT_RGB, alpha } : null;
  }

  /**
   * The rock tone, on a slope steeper than ROCK_SLOPE_DEGREES: the darker
   * grey on the side facing away from the light.
   *
   * @param {number} slope
   * @param {number} aspect
   * @returns {?Pixel}
   */
  function rockPixel(slope, aspect) {
    if (!(slope > ROCK_SLOPE_DEGREES)) return null;
    const towardsLight = Math.cos((LIGHT_AZIMUTH - aspect) * RAD) >= 0;
    return { rgb: towardsLight ? ROCK_LIT_RGB : ROCK_SHADOW_RGB, alpha: ROCK_ALPHA };
  }

  /**
   * A pixel row's crossings with a ring, in pixel columns.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring Pixel coordinates.
   * @param {number} y
   * @param {number[]} out
   */
  function crossings(ring, y, out) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y)) out.push(xi + ((y - yi) * (xj - xi)) / (yj - yi));
    }
  }

  /**
   * Which pixels of a tile are inside the polygons (holes honoured): 1 to
   * erase, 0 to paint. Filled a row at a time, at each pixel's centre.
   *
   * @param {ReadonlyArray<ReadonlyArray<ReadonlyArray<ReadonlyArray<number>>>>} polygons [lng, lat].
   * @param {Tile} tile
   * @param {number} size
   * @returns {Uint8Array}
   */
  function eraseMask(polygons, tile, size) {
    const mask = new Uint8Array(size * size);
    const scale = 2 ** tile.z;
    const toPixel = (/** @type {ReadonlyArray<number>} */ point) => {
      const sin = Math.sin(point[1] * RAD);
      const worldX = ((point[0] + 180) / 360) * scale;
      const worldY = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale;
      return [(worldX - tile.x) * size, (worldY - tile.y) * size];
    };
    const rings = polygons.flatMap((polygon) => polygon.map((ring) => ring.map(toPixel)));
    const touches = rings.some((ring) => {
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (const [px, py] of ring) {
        minX = Math.min(minX, px);
        maxX = Math.max(maxX, px);
        minY = Math.min(minY, py);
        maxY = Math.max(maxY, py);
      }
      return maxX >= 0 && minX <= size && maxY >= 0 && minY <= size;
    });
    if (!touches) return mask;
    /** @type {number[]} */
    const xs = [];
    for (let row = 0; row < size; row += 1) {
      xs.length = 0;
      for (const ring of rings) crossings(ring, row + 0.5, xs);
      if (!xs.length) continue;
      xs.sort((a, b) => a - b);
      // Every ring is listed, holes included, so even-odd honours holes.
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const from = Math.max(0, Math.ceil(xs[k] - 0.5));
        const to = Math.min(size - 1, Math.floor(xs[k + 1] - 0.5));
        for (let col = from; col <= to; col += 1) mask[row * size + col] = 1;
      }
    }
    return mask;
  }

  /**
   * Lay one pixel over another (source over), in place.
   *
   * @param {Uint8ClampedArray} out
   * @param {number} at Byte offset.
   * @param {Pixel} pixel
   */
  function over(out, at, pixel) {
    const a = pixel.alpha / 255;
    const below = out[at + 3] / 255;
    const alpha = a + below * (1 - a);
    if (!alpha) return;
    for (let c = 0; c < 3; c += 1) {
      out[at + c] = Math.round((pixel.rgb[c] * a + out[at + c] * below * (1 - a)) / alpha);
    }
    out[at + 3] = Math.round(alpha * 255);
  }

  /**
   * Paint one relief tile from one Terrarium tile.
   *
   * @param {Uint8ClampedArray|Uint8Array} dem The DEM tile's RGBA, `size` square.
   * @param {number} size
   * @param {Tile} tile
   * @param {ReadonlyArray<ReadonlyArray<ReadonlyArray<ReadonlyArray<number>>>>} polygons
   *   Where to erase: swisstopo's detailed relief.
   * @returns {Uint8ClampedArray} RGBA.
   */
  function paint(dem, size, tile, polygons) {
    const heights = new Float32Array(size * size);
    for (let i = 0; i < size * size; i += 1) {
      heights[i] = terrarium(dem[i * 4], dem[i * 4 + 1], dem[i * 4 + 2]);
    }
    const mask = eraseMask(polygons, tile, size);
    const cell = metresPerPixel(tile, size);
    const out = new Uint8ClampedArray(size * size * 4);
    for (let row = 0; row < size; row += 1) {
      for (let col = 0; col < size; col += 1) {
        const index = row * size + col;
        if (mask[index]) continue;
        const { slope, aspect } = slopeAspect(heights, size, col, row, cell);
        const at = index * 4;
        const hill = hillshadePixel(slope, aspect);
        if (hill) over(out, at, hill);
        const rock = rockPixel(slope, aspect);
        if (rock) over(out, at, rock);
      }
    }
    return out;
  }

  self.pwaAlpineReliefCore = Object.freeze({
    PROTOCOL,
    DEM_FALLBACK_TILE_URL,
    TILE_SIZE,
    MIN_ZOOM,
    MAX_ZOOM,
    ROCK_SLOPE_DEGREES,
    LIGHT_AZIMUTH,
    LIGHT_ALTITUDE,
    parseUrl,
    tileUrl,
    terrarium,
    metresPerPixel,
    slopeAspect,
    shade,
    hillshadePixel,
    rockPixel,
    eraseMask,
    paint,
  });
})();
