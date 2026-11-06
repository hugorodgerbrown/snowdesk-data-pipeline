// @ts-check
/*
 * static/js/swisstopo_edge_core.js — swisstopo's raster winter map beyond
 * the Swiss border (SNOW-1457).
 *
 * The swisstopo winter basemap is a vector style, and its data stops at
 * the border: labels at the border, rock hachures at about 10.5°E. The
 * raster winter national map (`ch.swisstopo.pixelkarte-farbe-winter`) keeps
 * full detail well past it. Measured on 6 Nov 2026:
 *
 *   z13 and below  detail over a quad from about (5.32, 48.22) to
 *                  (11.42, 48.17), (11.25, 45.31) and (5.40, 45.33);
 *                  below z12 a small-scale overview everywhere;
 *   z14–15         to about 10.5°E;
 *   z16 and above  close to Switzerland only.
 *
 * Past the edge of its detail a tile comes back pure white.
 *
 * So the vector style draws Switzerland, and the raster is laid over it
 * with Switzerland erased from every tile. The raster draws its own labels
 * and hachures beyond the border. That is why it goes on top of the whole
 * basemap rather than under the labels: the vector base source carries
 * roads and place names outside Switzerland at low zooms, and under the
 * raster's own they would double up. The page's own layers are added
 * after the basemap's, so they stay above it.
 *
 * The tiles come through the `swisstopoedge://` protocol
 * (static/js/swisstopo_edge.js). Everything it decides that needs no
 * canvas is here, so Vitest can pin it
 * (tests/js/test_swisstopo_edge_core.js):
 *
 *   - THE SPLICE. One raster source and one layer, appended to the fetched
 *     style, idempotent.
 *   - THE MASK. Switzerland is the union of CH's L1 regions
 *     (`major-regions.geojson?country=ch`). Each polygon's rings are
 *     oriented (outer one way, holes the other) so the protocol can fill
 *     them all as ONE path with the non-zero rule: shared edges between
 *     neighbouring regions leave no anti-aliased seam, and a hole stays
 *     open unless another region covers it.
 *   - THE FALLBACK. A blank tile above z13 walks up to the nearest
 *     ancestor with detail, never past z13, and is cut from it.
 *   - THE 3D GATE. 3D opens where the raster has detail at z13: the quad
 *     above, as a ring for map_view_core.covers().
 *
 * Exports (frozen `self.pwaSwisstopoEdgeCore`):
 *
 *   BASEMAP_KEY / PROTOCOL / SOURCE_ID / LAYER_ID / TILE_SIZE / DISPLAY_TILE_SIZE
 *   WMTS_TEMPLATE / WMTS_ORIGIN / ATTRIBUTION / MAX_ZOOM / FALLBACK_FLOOR
 *   GATE_OUTLINE
 *   spliceStyle(style)
 *   parseTileUrl(url)
 *   wmtsUrl(z, x, y)
 *   tileBounds(z, x, y)
 *   projector(z, x, y, size)
 *   maskPolygons(featureCollection)
 *   polygonsTouching(polygons, bounds)
 *   pointInUnion(polygons, lng, lat)
 *   isBlank(rgba, stride)
 *   isFullyCovered(rgba)
 *   ancestors(z, x, y)
 */
(function () {
  'use strict';

  /** The basemap this applies to (BASEMAP_STYLES in config/settings/base.py). */
  const BASEMAP_KEY = 'swisstopo_winter';

  /** The MapLibre protocol the raster's tiles come through. */
  const PROTOCOL = 'swisstopoedge';

  /** The spliced source and layer share one id. */
  const SOURCE_ID = 'swisstopo-edge';
  const LAYER_ID = 'swisstopo-edge';

  /** swisstopo's WMTS tiles are 256 px. */
  const TILE_SIZE = 256;

  /**
   * The size MapLibre draws each tile at, in CSS px: half its pixels. At
   * 256 the raster's lettering came out about twice the size of the vector
   * map's across the border, and on a phone's screen each pixel was
   * enlarged. At 128 MapLibre asks for the next zoom up, so the lettering
   * matches the vector map and is sharp at a device pixel ratio of 2 (the
   * size swisstopo's own viewer shows it at). It costs four tiles where
   * there was one, outside Switzerland only.
   */
  const DISPLAY_TILE_SIZE = 128;

  /** The raster winter national map, Web Mercator. */
  const WMTS_TEMPLATE =
    'https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe-winter/default/current/3857/{z}/{x}/{y}.jpeg';

  /** Where the tiles come from, for the service worker's allowlist. */
  const WMTS_ORIGIN = 'https://wmts.geo.admin.ch';

  /**
   * The credit. The exact string the vector style's own sources carry, so
   * updateMapAttribution's exact-string union shows it once.
   */
  const ATTRIBUTION = '© swisstopo';

  /**
   * The raster's highest tile zoom (the camera's z15, at DISPLAY_TILE_SIZE).
   * Above it MapLibre enlarges the z16 tile itself, and the closest zooms
   * cost no requests.
   */
  const MAX_ZOOM = 16;

  /**
   * The fallback never walks past this zoom: z13 has detail over the whole
   * of GATE_OUTLINE, and below it the overview has detail everywhere.
   */
  const FALLBACK_FLOOR = 13;

  /**
   * Where 3D opens: the quad the raster has detail over at z13, west to
   * east along the north edge, then back along the south. Closed.
   *
   * @type {ReadonlyArray<ReadonlyArray<number>>}
   */
  const GATE_OUTLINE = Object.freeze([
    Object.freeze([5.32, 48.22]),
    Object.freeze([11.42, 48.17]),
    Object.freeze([11.25, 45.31]),
    Object.freeze([5.4, 45.33]),
    Object.freeze([5.32, 48.22]),
  ]);

  /**
   * Add the raster to a fetched swisstopo winter style, on top of every
   * layer it has. A style that already carries it is returned as it is.
   *
   * @param {any} style The parsed style JSON. Mutated and returned.
   * @returns {any}
   */
  function spliceStyle(style) {
    if (!style || typeof style !== 'object') return style;
    style.sources = style.sources || {};
    style.layers = Array.isArray(style.layers) ? style.layers : [];
    if (style.sources[SOURCE_ID]) return style;
    style.sources[SOURCE_ID] = {
      type: 'raster',
      tiles: [PROTOCOL + '://{z}/{x}/{y}'],
      tileSize: DISPLAY_TILE_SIZE,
      maxzoom: MAX_ZOOM,
      attribution: ATTRIBUTION,
    };
    style.layers.push({ id: LAYER_ID, type: 'raster', source: SOURCE_ID });
    return style;
  }

  /**
   * The tile a `swisstopoedge://z/x/y` URL names, or null.
   *
   * @param {string} url
   * @returns {{z: number, x: number, y: number}|null}
   */
  function parseTileUrl(url) {
    const match = /^swisstopoedge:\/\/(\d+)\/(\d+)\/(\d+)$/.exec(String(url || ''));
    if (!match) return null;
    return { z: Number(match[1]), x: Number(match[2]), y: Number(match[3]) };
  }

  /**
   * The WMTS URL for one tile.
   *
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @returns {string}
   */
  function wmtsUrl(z, x, y) {
    return WMTS_TEMPLATE.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y));
  }

  /**
   * The latitude of a tile row's top edge.
   *
   * @param {number} y
   * @param {number} n Tiles per side at the zoom.
   * @returns {number}
   */
  function rowLat(y, n) {
    return (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
  }

  /**
   * A tile's `[west, south, east, north]` in degrees.
   *
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @returns {Array<number>}
   */
  function tileBounds(z, x, y) {
    const n = 2 ** z;
    return [(x / n) * 360 - 180, rowLat(y + 1, n), ((x + 1) / n) * 360 - 180, rowLat(y, n)];
  }

  /**
   * A function from lon/lat to pixels in one tile, `size` px across. Points
   * outside the tile land outside `0..size`, which a canvas clips.
   *
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @param {number} size
   * @returns {(lng: number, lat: number) => Array<number>}
   */
  function projector(z, x, y, size) {
    const n = 2 ** z;
    return (lng, lat) => {
      const rad = (lat * Math.PI) / 180;
      const worldX = ((lng + 180) / 360) * n;
      const worldY = ((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2) * n;
      return [(worldX - x) * size, (worldY - y) * size];
    };
  }

  /**
   * Twice a ring's signed area in lon/lat; positive is anticlockwise.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring
   * @returns {number}
   */
  function signedArea(ring) {
    let sum = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      sum += (ring[j][0] - ring[i][0]) * (ring[j][1] + ring[i][1]);
    }
    return sum;
  }

  /**
   * A ring turned to the asked direction.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring
   * @param {boolean} anticlockwise
   * @returns {Array<ReadonlyArray<number>>}
   */
  function oriented(ring, anticlockwise) {
    const copy = ring.slice();
    return signedArea(copy) > 0 === anticlockwise ? copy : copy.reverse();
  }

  /**
   * @typedef {Object} MaskPolygon
   * @property {Array<Array<ReadonlyArray<number>>>} rings Outer ring
   *   anticlockwise, then holes clockwise.
   * @property {Array<number>} bbox `[west, south, east, north]` of the outer ring.
   */

  /**
   * Every polygon of a FeatureCollection of Polygons and MultiPolygons,
   * oriented and boxed. Anything else is skipped.
   *
   * @param {any} collection
   * @returns {Array<MaskPolygon>}
   */
  function maskPolygons(collection) {
    /** @type {Array<MaskPolygon>} */
    const out = [];
    const features = collection && Array.isArray(collection.features) ? collection.features : [];
    for (const feature of features) {
      const geometry = feature && feature.geometry;
      if (!geometry) continue;
      /** @type {Array<any>} */
      let polygons = [];
      if (geometry.type === 'Polygon') polygons = [geometry.coordinates];
      else if (geometry.type === 'MultiPolygon') polygons = geometry.coordinates;
      for (const polygon of polygons) {
        if (!Array.isArray(polygon) || !polygon.length || polygon[0].length < 4) continue;
        const rings = polygon.map((/** @type {any} */ ring, /** @type {number} */ i) => oriented(ring, i === 0));
        let west = Infinity;
        let south = Infinity;
        let east = -Infinity;
        let north = -Infinity;
        for (const [lng, lat] of rings[0]) {
          if (lng < west) west = lng;
          if (lng > east) east = lng;
          if (lat < south) south = lat;
          if (lat > north) north = lat;
        }
        out.push({ rings, bbox: [west, south, east, north] });
      }
    }
    return out;
  }

  /**
   * The polygons whose box meets a `[west, south, east, north]` box.
   *
   * @param {ReadonlyArray<MaskPolygon>} polygons
   * @param {ReadonlyArray<number>} bounds
   * @returns {Array<MaskPolygon>}
   */
  function polygonsTouching(polygons, bounds) {
    const [west, south, east, north] = bounds;
    return polygons.filter(
      (p) => p.bbox[0] <= east && p.bbox[2] >= west && p.bbox[1] <= north && p.bbox[3] >= south,
    );
  }

  /**
   * Whether a point is inside a ring (even-odd ray cast).
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring
   * @param {number} lng
   * @param {number} lat
   * @returns {boolean}
   */
  function inRing(ring, lng, lat) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  /**
   * Whether a point is in the mask: inside some polygon's outer ring and
   * none of that polygon's holes.
   *
   * @param {ReadonlyArray<MaskPolygon>} polygons
   * @param {number} lng
   * @param {number} lat
   * @returns {boolean}
   */
  function pointInUnion(polygons, lng, lat) {
    return polygons.some(
      (p) => inRing(p.rings[0], lng, lat) && !p.rings.slice(1).some((hole) => inRing(hole, lng, lat)),
    );
  }

  /**
   * Whether an RGBA tile is swisstopo's "no detail here": every sampled
   * pixel pure white. Every `stride`-th pixel is read.
   *
   * @param {ArrayLike<number>} rgba
   * @param {number} [stride]
   * @returns {boolean}
   */
  function isBlank(rgba, stride = 7) {
    if (!rgba || !rgba.length) return true;
    const step = Math.max(1, Math.floor(stride)) * 4;
    for (let i = 0; i < rgba.length; i += step) {
      if (rgba[i] !== 255 || rgba[i + 1] !== 255 || rgba[i + 2] !== 255) return false;
    }
    return true;
  }

  /**
   * Whether a rasterised mask covers every pixel (alpha 255 throughout).
   *
   * @param {ArrayLike<number>} rgba
   * @returns {boolean}
   */
  function isFullyCovered(rgba) {
    if (!rgba || !rgba.length) return false;
    for (let i = 3; i < rgba.length; i += 4) {
      if (rgba[i] !== 255) return false;
    }
    return true;
  }

  /**
   * @typedef {Object} Ancestor
   * @property {number} z
   * @property {number} x
   * @property {number} y
   * @property {number} sx Left of the tile's square in the ancestor, px.
   * @property {number} sy Top of it, px.
   * @property {number} size Its side, px.
   */

  /**
   * The ancestors a blank tile falls back to, nearest first, down to
   * FALLBACK_FLOOR. A tile at or below the floor has none.
   *
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @returns {Array<Ancestor>}
   */
  function ancestors(z, x, y) {
    /** @type {Array<Ancestor>} */
    const out = [];
    for (let up = 1; z - up >= FALLBACK_FLOOR; up += 1) {
      const scale = 2 ** up;
      const ax = Math.floor(x / scale);
      const ay = Math.floor(y / scale);
      const size = TILE_SIZE / scale;
      out.push({ z: z - up, x: ax, y: ay, sx: (x - ax * scale) * size, sy: (y - ay * scale) * size, size });
    }
    return out;
  }

  self.pwaSwisstopoEdgeCore = Object.freeze({
    BASEMAP_KEY,
    PROTOCOL,
    SOURCE_ID,
    LAYER_ID,
    TILE_SIZE,
    DISPLAY_TILE_SIZE,
    WMTS_TEMPLATE,
    WMTS_ORIGIN,
    ATTRIBUTION,
    MAX_ZOOM,
    FALLBACK_FLOOR,
    GATE_OUTLINE,
    spliceStyle,
    parseTileUrl,
    wmtsUrl,
    tileBounds,
    projector,
    maskPolygons,
    polygonsTouching,
    pointInUnion,
    isBlank,
    isFullyCovered,
    ancestors,
  });
})();
