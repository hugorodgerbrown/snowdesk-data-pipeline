/*
 * tests/js/test_swisstopo_edge_core.js — swisstopo's raster winter map
 * beyond the Swiss border, its pure half (SNOW-1457).
 *
 * What is pinned:
 *   - the splice: one raster source and layer, on top of the style, drawn
 *     at 128 px, with swisstopo's credit and a z16 ceiling; idempotent;
 *   - tile maths: the protocol URL, the WMTS URL, a tile's box and the
 *     projection into its pixels;
 *   - the mask: Polygons and MultiPolygons, holes, several features, rings
 *     oriented for one non-zero fill, and the bbox pre-filter;
 *   - blank detection and full coverage;
 *   - the fallback: nearest ancestor first, never past z13, and the crop
 *     for one, two and three levels up.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/swisstopo_edge_core.js';

const core = window.pwaSwisstopoEdgeCore;

/** A square ring, anticlockwise when `ccw`. */
const square = (west, south, east, north, ccw = true) => {
  const ring = [[west, south], [east, south], [east, north], [west, north], [west, south]];
  return ccw ? ring : ring.slice().reverse();
};

/** Twice a ring's signed area; positive is anticlockwise. */
const area = (ring) => {
  let sum = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    sum += (ring[j][0] - ring[i][0]) * (ring[j][1] + ring[i][1]);
  }
  return sum;
};

const feature = (geometry) => ({ type: 'Feature', properties: {}, geometry });

/** A tile index at a zoom for a lon/lat. */
const tileAt = (lng, lat, z) => {
  const n = 2 ** z;
  const rad = (lat * Math.PI) / 180;
  return {
    z,
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2) * n),
  };
};

describe('the splice', () => {
  const style = () => ({
    version: 8,
    sources: { base_v1: { type: 'vector', url: 'https://example.test/tiles.json' } },
    layers: [
      { id: 'background', type: 'background' },
      { id: 'roads', type: 'line', source: 'base_v1' },
      { id: 'places', type: 'symbol', source: 'base_v1' },
    ],
  });

  it('adds one raster source and layer, on top of every basemap layer', () => {
    const out = core.spliceStyle(style());
    expect(out.sources[core.SOURCE_ID]).toEqual({
      type: 'raster',
      tiles: ['swisstopoedge://{z}/{x}/{y}'],
      tileSize: 128,
      maxzoom: 16,
      attribution: '© swisstopo',
    });
    expect(out.layers.map((l) => l.id)).toEqual(['background', 'roads', 'places', core.LAYER_ID]);
    expect(out.layers.at(-1)).toEqual({ id: core.LAYER_ID, type: 'raster', source: core.SOURCE_ID });
  });

  it('is idempotent', () => {
    const once = core.spliceStyle(style());
    const twice = core.spliceStyle(once);
    expect(twice.layers.filter((l) => l.id === core.LAYER_ID)).toHaveLength(1);
    expect(Object.keys(twice.sources)).toEqual(['base_v1', core.SOURCE_ID]);
  });

  it('leaves the vector sources alone', () => {
    expect(core.spliceStyle(style()).sources.base_v1).toEqual({
      type: 'vector',
      url: 'https://example.test/tiles.json',
    });
  });

  it('copes with a style missing its arrays, and passes a non-style through', () => {
    const out = core.spliceStyle({ version: 8 });
    expect(out.layers).toHaveLength(1);
    expect(core.spliceStyle(null)).toBeNull();
  });
});

describe('tile maths', () => {
  it('parses the protocol URL and nothing else', () => {
    expect(core.parseTileUrl('swisstopoedge://14/8602/5754')).toEqual({ z: 14, x: 8602, y: 5754 });
    expect(core.parseTileUrl('https://example.test/14/1/2')).toBeNull();
    expect(core.parseTileUrl('swisstopoedge://14/a/2')).toBeNull();
  });

  it('builds the WMTS URL', () => {
    expect(core.wmtsUrl(13, 4340, 2880)).toBe(
      'https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe-winter/default/current/3857/13/4340/2880.jpeg',
    );
    expect(new URL(core.wmtsUrl(1, 0, 0)).origin).toBe(core.WMTS_ORIGIN);
  });

  it('boxes a tile', () => {
    const [west, south, east, north] = core.tileBounds(1, 1, 0);
    expect(west).toBe(0);
    expect(east).toBe(180);
    expect(south).toBeCloseTo(0, 9);
    expect(north).toBeCloseTo(85.0511, 3);
  });

  it('projects a tile’s corners onto its pixel corners', () => {
    const tile = tileAt(10.5, 47, 14);
    const [west, south, east, north] = core.tileBounds(tile.z, tile.x, tile.y);
    const project = core.projector(tile.z, tile.x, tile.y, 256);
    const [x0, y0] = project(west, north);
    const [x1, y1] = project(east, south);
    expect(x0).toBeCloseTo(0, 6);
    expect(y0).toBeCloseTo(0, 6);
    expect(x1).toBeCloseTo(256, 6);
    expect(y1).toBeCloseTo(256, 6);
  });
});

describe('the mask', () => {
  it('reads a Polygon, orienting its outer ring anticlockwise and its hole clockwise', () => {
    const [polygon] = core.maskPolygons({
      type: 'FeatureCollection',
      features: [feature({ type: 'Polygon', coordinates: [square(0, 0, 4, 4, false), square(1, 1, 2, 2, true)] })],
    });
    expect(area(polygon.rings[0])).toBeGreaterThan(0);
    expect(area(polygon.rings[1])).toBeLessThan(0);
    expect(polygon.bbox).toEqual([0, 0, 4, 4]);
  });

  it('reads every component of a MultiPolygon, and every feature', () => {
    const polygons = core.maskPolygons({
      type: 'FeatureCollection',
      features: [
        feature({ type: 'MultiPolygon', coordinates: [[square(0, 0, 1, 1)], [[...square(5, 5, 6, 6)]]] }),
        feature({ type: 'Polygon', coordinates: [square(10, 10, 11, 11)] }),
      ],
    });
    expect(polygons.map((p) => p.bbox)).toEqual([[0, 0, 1, 1], [5, 5, 6, 6], [10, 10, 11, 11]]);
  });

  it('skips what is not a polygon', () => {
    expect(core.maskPolygons({
      type: 'FeatureCollection',
      features: [feature({ type: 'Point', coordinates: [1, 1] }), feature(null), { geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 1]]] } }],
    })).toEqual([]);
    expect(core.maskPolygons(null)).toEqual([]);
  });

  const holed = core.maskPolygons({
    type: 'FeatureCollection',
    features: [
      feature({ type: 'Polygon', coordinates: [square(0, 0, 4, 4), square(1, 1, 2, 2)] }),
      feature({ type: 'MultiPolygon', coordinates: [[square(4, 0, 6, 2)], [square(10, 10, 12, 12)]] }),
    ],
  });

  it.each([
    ['inside the first feature', 3, 3, true],
    ['in its hole', 1.5, 1.5, false],
    ['in the second feature, first component', 5, 1, true],
    ['in its second component', 11, 11, true],
    ['between the components', 8, 8, false],
  ])('counts a point %s', (_where, lng, lat, inside) => {
    expect(core.pointInUnion(holed, lng, lat)).toBe(inside);
  });

  it('keeps only the polygons whose box meets the tile', () => {
    expect(core.polygonsTouching(holed, [20, 20, 21, 21])).toEqual([]);
    expect(core.polygonsTouching(holed, [0.5, 0.5, 0.6, 0.6]).map((p) => p.bbox)).toEqual([[0, 0, 4, 4]]);
    expect(core.polygonsTouching(holed, [3.5, 0.5, 4.5, 1.5]).map((p) => p.bbox)).toEqual([[0, 0, 4, 4], [4, 0, 6, 2]]);
  });
});

describe('blank and covered tiles', () => {
  const tile = (rgb, alpha = 255) => {
    const out = new Uint8ClampedArray(256 * 256 * 4);
    for (let i = 0; i < out.length; i += 4) {
      out.set([...rgb, alpha], i);
    }
    return out;
  };

  it('calls a pure white tile blank, and anything with detail not', () => {
    expect(core.isBlank(tile([255, 255, 255]))).toBe(true);
    const detail = tile([255, 255, 255]);
    detail.set([230, 241, 245, 255], 0);
    expect(core.isBlank(detail)).toBe(false);
    expect(core.isBlank(tile([254, 255, 255]))).toBe(false);
  });

  it('calls a mask covering every pixel full, and one short of it not', () => {
    expect(core.isFullyCovered(tile([0, 0, 0]))).toBe(true);
    const gap = tile([0, 0, 0]);
    gap[256 * 4 * 128 + 3] = 254;
    expect(core.isFullyCovered(gap)).toBe(false);
    expect(core.isFullyCovered(new Uint8ClampedArray(0))).toBe(false);
  });
});

describe('the fallback', () => {
  it('walks a z16 tile at 11.0°E up to z13, nearest first, and stops there', () => {
    const tile = tileAt(11.0, 47.1, 16);
    expect(core.ancestors(tile.z, tile.x, tile.y).map((a) => a.z)).toEqual([15, 14, 13]);
  });

  it('gives z13 and below no fallback', () => {
    expect(core.ancestors(13, 4340, 2880)).toEqual([]);
    expect(core.ancestors(12, 2170, 1440)).toEqual([]);
  });

  it.each([
    [1, 128],
    [2, 64],
    [3, 32],
  ])('cuts the right square %i level(s) up', (up, size) => {
    const z = 16;
    const x = 34402;
    const y = 23012;
    const ancestor = core.ancestors(z, x, y)[up - 1];
    const scale = 2 ** up;
    expect(ancestor).toEqual({
      z: z - up,
      x: Math.floor(x / scale),
      y: Math.floor(y / scale),
      sx: (x % scale) * size,
      sy: (y % scale) * size,
      size,
    });
    expect(ancestor.sx + ancestor.size).toBeLessThanOrEqual(256);
  });
});

describe('the 3D gate', () => {
  it('is a closed ring', () => {
    expect(core.GATE_OUTLINE[0]).toEqual(core.GATE_OUTLINE.at(-1));
    expect(core.GATE_OUTLINE.length).toBe(5);
  });
});
