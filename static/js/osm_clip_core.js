// @ts-check
/*
 * static/js/osm_clip_core.js — OpenStreetMap vector tiles clipped to outside
 * the swisstopo extent (SNOW-1460).
 *
 * Past the Swiss border the winter style draws OpenFreeMap's OpenMapTiles
 * vector tiles with its own layer definitions
 * (static/js/beyond_switzerland_core.js). Inside the swisstopo extent
 * (static/js/swisstopo_extent.js) swisstopo's base.vt draws, so every OSM
 * feature is clipped to outside it before MapLibre sees it. A `within`
 * filter cannot do that: it keeps or drops a whole feature, so a road or a
 * forest across the border would either draw twice or stop short.
 *
 * Pure geometry in TILE COORDINATES (0..extent, y down, as a vector tile
 * stores them), so the same file runs on the page for Vitest and in
 * static/js/osm_clip_worker.js through `importScripts`. It never decodes or
 * encodes a tile; the worker does that with static/js/vector_tile_kit.min.js.
 *
 *   - THE TILE. classifyTile says whether a tile is wholly inside the
 *     extent (it comes back empty), wholly outside (it passes through
 *     untouched) or on the border (each feature is clipped).
 *   - POLYGONS on a border tile are clipped to their outside part through
 *     polygon-clipping's difference; holes are kept.
 *   - LINES are cut where they cross the extent's edges, and only the
 *     pieces whose midpoint is outside are kept, so a line crossing twice
 *     becomes two pieces.
 *   - POINTS inside are dropped.
 *
 * Every coordinate it returns is rounded to a whole tile unit, which is
 * what a vector tile can store.
 *
 * Exports (frozen `self.pwaOsmClipCore`):
 *
 *   PROTOCOL / INSIDE / OUTSIDE / BORDER
 *   parseUrl(url) / tileUrl(template, tile)
 *   project(lng, lat, tile, extent)
 *   localExtent(polygons, tile, extent, clipping, margin)
 *   classifyTile(polygons, tile, extent, clipping)
 *   pointInPolygons(polygons, point)
 *   classifyRings(rings)
 *   clipPolygon(rings, local, clipping)
 *   cutLine(line, local)
 *   clipFeature(feature, local, clipping)
 *   clipLayers(layers, polygons, tile, clipping)
 */
(function () {
  'use strict';

  /** The MapLibre protocol the clipped tiles come through. */
  const PROTOCOL = 'osmclip';

  /** What classifyTile answers. */
  const INSIDE = 'inside';
  const OUTSIDE = 'outside';
  const BORDER = 'border';

  /**
   * How far past its edges a tile's local extent reaches, as a share of
   * the tile: features carry a buffer beyond the tile, and a vertex out
   * there must still be judged against the real border.
   */
  const MARGIN = 0.5;

  /**
   * @typedef {{z: number, x: number, y: number}} Tile
   * @typedef {[number, number]} Pair
   * @typedef {Pair[]} Ring
   * @typedef {Ring[]} Polygon
   * @typedef {Polygon[]} MultiPolygon
   * @typedef {{type: number, geometry: any, tags?: object, id?: number}} Feature
   */

  /**
   * Read `osmclip://{z}/{x}/{y}`.
   *
   * @param {string} url
   * @returns {?Tile}
   */
  function parseUrl(url) {
    const match = /^osmclip:\/\/(\d+)\/(\d+)\/(\d+)(?:[?#].*)?$/.exec(url || '');
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
   * A longitude and latitude in a tile's own coordinates.
   *
   * @param {number} lng
   * @param {number} lat
   * @param {Tile} tile
   * @param {number} extent The tile's extent, usually 4096.
   * @returns {Pair}
   */
  function project(lng, lat, tile, extent) {
    const scale = 2 ** tile.z;
    const sin = Math.sin((lat * Math.PI) / 180);
    const worldX = ((lng + 180) / 360) * scale;
    const worldY = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale;
    return [(worldX - tile.x) * extent, (worldY - tile.y) * extent];
  }

  /**
   * The signed area of a ring, by the surveyor's formula: positive for a
   * vector tile's exterior ring (clockwise on screen, y down).
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring
   * @returns {number}
   */
  function signedArea(ring) {
    let sum = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      sum += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
    }
    return sum / 2;
  }

  /**
   * A square ring around a tile, `margin` tiles wide on each side.
   *
   * @param {number} extent
   * @param {number} margin
   * @returns {Polygon}
   */
  function tileRect(extent, margin) {
    const lo = -margin * extent;
    const hi = (1 + margin) * extent;
    return [[[lo, lo], [hi, lo], [hi, hi], [lo, hi], [lo, lo]]];
  }

  /**
   * Whether a ring's longitude/latitude box can touch the tile area.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring Tile coordinates.
   * @param {number} lo
   * @param {number} hi
   * @returns {boolean}
   */
  function ringTouches(ring, lo, hi) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const [px, py] of ring) {
      if (px < minX) minX = px;
      if (px > maxX) maxX = px;
      if (py < minY) minY = py;
      if (py > maxY) maxY = py;
    }
    return maxX >= lo && minX <= hi && maxY >= lo && minY <= hi;
  }

  /**
   * The extent's polygons in a tile's coordinates, cut to the tile and a
   * margin around it. Empty when the extent does not reach the tile.
   *
   * @param {ReadonlyArray<ReadonlyArray<ReadonlyArray<ReadonlyArray<number>>>>} polygons
   *   The extent, [lng, lat].
   * @param {Tile} tile
   * @param {number} extent
   * @param {any} clipping polygon-clipping.
   * @param {number} [margin]
   * @returns {MultiPolygon}
   */
  function localExtent(polygons, tile, extent, clipping, margin = MARGIN) {
    const lo = -margin * extent;
    const hi = (1 + margin) * extent;
    /** @type {MultiPolygon} */
    const projected = [];
    for (const polygon of polygons) {
      const rings = polygon.map((ring) =>
        ring.map(([lng, lat]) => project(lng, lat, tile, extent)));
      if (!ringTouches(rings[0], lo, hi)) continue;
      projected.push(rings);
    }
    if (!projected.length) return [];
    return clipping.intersection(projected, tileRect(extent, margin));
  }

  /** @param {MultiPolygon} polygons */
  function totalArea(polygons) {
    let area = 0;
    for (const polygon of polygons) {
      for (const ring of polygon) area += signedArea(ring);
    }
    return Math.abs(area);
  }

  /**
   * Whether a tile is wholly inside the extent, wholly outside it, or on
   * its border.
   *
   * @param {ReadonlyArray<ReadonlyArray<ReadonlyArray<ReadonlyArray<number>>>>} polygons
   * @param {Tile} tile
   * @param {number} extent
   * @param {any} clipping
   * @returns {string} INSIDE, OUTSIDE or BORDER.
   */
  function classifyTile(polygons, tile, extent, clipping) {
    const exact = localExtent(polygons, tile, extent, clipping, 0);
    const area = totalArea(exact);
    const full = extent * extent;
    if (area <= full * 1e-9) return OUTSIDE;
    if (area >= full * (1 - 1e-9)) return INSIDE;
    return BORDER;
  }

  /**
   * Even-odd: inside a ring.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring
   * @param {ReadonlyArray<number>} point
   * @returns {boolean}
   */
  function inRing(ring, point) {
    const [x, y] = point;
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  /**
   * Whether a point is inside polygons with holes.
   *
   * @param {ReadonlyArray<ReadonlyArray<ReadonlyArray<ReadonlyArray<number>>>>} polygons
   * @param {ReadonlyArray<number>} point
   * @returns {boolean}
   */
  function pointInPolygons(polygons, point) {
    return polygons.some(([outer, ...holes]) =>
      inRing(outer, point) && !holes.some((hole) => inRing(hole, point)));
  }

  /**
   * A vector tile's polygon rings, grouped into polygons: a ring wound like
   * the first starts a polygon, one wound the other way is its hole. Each
   * ring is closed.
   *
   * @param {Ring[]} rings
   * @returns {Polygon[]}
   */
  function classifyRings(rings) {
    /** @type {Polygon[]} */
    const polygons = [];
    let exteriorSign = 0;
    for (const raw of rings) {
      const ring = closeRing(raw);
      const area = signedArea(ring);
      if (!area || ring.length < 4) continue;
      const sign = Math.sign(area);
      if (!exteriorSign) exteriorSign = sign;
      if (sign === exteriorSign || !polygons.length) polygons.push([ring]);
      else polygons[polygons.length - 1].push(ring);
    }
    return polygons;
  }

  /**
   * @param {Ring} ring
   * @returns {Ring}
   */
  function closeRing(ring) {
    if (!ring.length) return ring;
    const [fx, fy] = ring[0];
    const [lx, ly] = ring[ring.length - 1];
    return fx === lx && fy === ly ? ring : [...ring, [fx, fy]];
  }

  /**
   * Round to whole tile units and drop repeated points.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} points
   * @returns {Ring}
   */
  function roundPoints(points) {
    /** @type {Ring} */
    const out = [];
    for (const [px, py] of points) {
      const x = Math.round(px);
      const y = Math.round(py);
      const last = out[out.length - 1];
      if (!last || last[0] !== x || last[1] !== y) out.push([x, y]);
    }
    return out;
  }

  /**
   * A ring rounded and wound the vector-tile way: exterior positive, holes
   * negative. Null once rounding has collapsed it.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} ring
   * @param {boolean} exterior
   * @returns {?Ring}
   */
  function tidyRing(ring, exterior) {
    const rounded = roundPoints(ring);
    if (rounded.length < 4) return null;
    const area = signedArea(rounded);
    if (!area) return null;
    if ((area > 0) !== exterior) rounded.reverse();
    return rounded;
  }

  /**
   * The part of a polygon outside the extent, holes kept.
   *
   * @param {Ring[]} rings The feature's rings, as the tile stores them.
   * @param {MultiPolygon} local The extent in this tile (localExtent).
   * @param {any} clipping
   * @returns {Ring[]} Rings, exteriors and their holes in order; empty when
   *   nothing is outside.
   */
  function clipPolygon(rings, local, clipping) {
    const polygons = classifyRings(rings);
    if (!polygons.length) return [];
    const outside = local.length ? clipping.difference(polygons, local) : polygons;
    /** @type {Ring[]} */
    const out = [];
    for (const polygon of outside) {
      const exterior = tidyRing(polygon[0], true);
      if (!exterior) continue;
      out.push(exterior);
      for (const hole of polygon.slice(1)) {
        const tidy = tidyRing(hole, false);
        if (tidy) out.push(tidy);
      }
    }
    return out;
  }

  /**
   * Where segment a→b crosses segment c→d, as a share of a→b, or null.
   *
   * @param {ReadonlyArray<number>} a
   * @param {ReadonlyArray<number>} b
   * @param {ReadonlyArray<number>} c
   * @param {ReadonlyArray<number>} d
   * @returns {?number}
   */
  function crossing(a, b, c, d) {
    const rx = b[0] - a[0];
    const ry = b[1] - a[1];
    const sx = d[0] - c[0];
    const sy = d[1] - c[1];
    const denom = rx * sy - ry * sx;
    if (!denom) return null;
    const qx = c[0] - a[0];
    const qy = c[1] - a[1];
    const t = (qx * sy - qy * sx) / denom;
    const u = (qx * ry - qy * rx) / denom;
    if (t <= 0 || t >= 1 || u < 0 || u > 1) return null;
    return t;
  }

  /**
   * A line cut at the extent's edges, keeping the pieces whose midpoint is
   * outside it.
   *
   * @param {ReadonlyArray<ReadonlyArray<number>>} line
   * @param {MultiPolygon} local
   * @returns {Ring[]} Lines of two points or more.
   */
  function cutLine(line, local) {
    if (line.length < 2) return [];
    if (!local.length) {
      const whole = roundPoints(line);
      return whole.length >= 2 ? [whole] : [];
    }
    /** @type {Array<[ReadonlyArray<number>, ReadonlyArray<number>]>} */
    const edges = [];
    for (const polygon of local) {
      for (const ring of polygon) {
        for (let i = 1; i < ring.length; i += 1) edges.push([ring[i - 1], ring[i]]);
      }
    }
    /** @type {Ring[]} */
    const pieces = [];
    /** @type {?Ring} */
    let current = null;
    for (let i = 1; i < line.length; i += 1) {
      const a = line[i - 1];
      const b = line[i];
      const cuts = [0, 1];
      for (const [c, d] of edges) {
        const t = crossing(a, b, c, d);
        if (t !== null) cuts.push(t);
      }
      cuts.sort((p, q) => p - q);
      for (let k = 1; k < cuts.length; k += 1) {
        const t0 = cuts[k - 1];
        const t1 = cuts[k];
        if (t1 - t0 <= 1e-12) continue;
        const at = (/** @type {number} */ t) => /** @type {Pair} */ ([
          a[0] + (b[0] - a[0]) * t,
          a[1] + (b[1] - a[1]) * t,
        ]);
        const mid = at((t0 + t1) / 2);
        if (pointInPolygons(local, mid)) {
          if (current) pieces.push(current);
          current = null;
          continue;
        }
        if (!current) current = [at(t0)];
        current.push(at(t1));
      }
    }
    if (current) pieces.push(current);
    return pieces.map(roundPoints).filter((piece) => piece.length >= 2);
  }

  /**
   * One feature clipped to outside the extent, in the shape the encoder
   * takes (geojson-vt's: points a flat list, lines and polygons a list of
   * rings). Null when nothing of it is outside.
   *
   * @param {Feature} feature `geometry` as the decoder's loadGeometry
   *   gives it, as `[x, y]` pairs.
   * @param {MultiPolygon} local
   * @param {any} clipping
   * @returns {?Feature}
   */
  function clipFeature(feature, local, clipping) {
    /** @type {Ring[]} */
    const rings = feature.geometry;
    let geometry;
    if (feature.type === 1) {
      geometry = roundPoints(rings.flat()).filter((point) => !pointInPolygons(local, point));
    } else if (feature.type === 2) {
      geometry = rings.flatMap((line) => cutLine(line, local));
    } else if (feature.type === 3) {
      geometry = clipPolygon(rings, local, clipping);
    } else {
      return null;
    }
    if (!geometry.length) return null;
    return { ...feature, geometry };
  }

  /**
   * @typedef {{extent: number, features: Feature[]}} Layer
   */

  /**
   * Every layer of a tile clipped to outside the extent. A tile wholly
   * inside comes back with no layers; one wholly outside comes back as it
   * was, with `status` saying which.
   *
   * @param {Record<string, Layer>} layers
   * @param {ReadonlyArray<ReadonlyArray<ReadonlyArray<ReadonlyArray<number>>>>} polygons
   * @param {Tile} tile
   * @param {any} clipping
   * @returns {{status: string, layers: Record<string, Layer>}}
   */
  function clipLayers(layers, polygons, tile, clipping) {
    const names = Object.keys(layers);
    const extent = names.length ? layers[names[0]].extent || 4096 : 4096;
    const status = classifyTile(polygons, tile, extent, clipping);
    if (status === INSIDE) return { status, layers: {} };
    if (status === OUTSIDE) return { status, layers };
    /** @type {Record<string, Layer>} */
    const out = {};
    /** @type {Map<number, MultiPolygon>} */
    const locals = new Map();
    for (const name of names) {
      const layer = layers[name];
      const layerExtent = layer.extent || 4096;
      if (!locals.has(layerExtent)) {
        locals.set(layerExtent, localExtent(polygons, tile, layerExtent, clipping));
      }
      const local = /** @type {MultiPolygon} */ (locals.get(layerExtent));
      /** @type {Feature[]} */
      const features = [];
      for (const feature of layer.features) {
        const clipped = clipFeature(feature, local, clipping);
        if (clipped) features.push(clipped);
      }
      if (features.length) out[name] = { extent: layerExtent, features };
    }
    return { status, layers: out };
  }

  self.pwaOsmClipCore = Object.freeze({
    PROTOCOL,
    INSIDE,
    OUTSIDE,
    BORDER,
    parseUrl,
    tileUrl,
    project,
    localExtent,
    classifyTile,
    pointInPolygons,
    classifyRings,
    clipPolygon,
    cutLine,
    clipFeature,
    clipLayers,
  });
})();
