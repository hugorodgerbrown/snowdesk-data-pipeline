/*
 * tests/js/test_osm_clip_core.js — OSM features clipped to outside the
 * swisstopo extent (SNOW-1460, static/js/osm_clip_core.js).
 *
 * What is pinned:
 *   - the tile: wholly inside the extent, wholly outside, or on its border,
 *     against the real extent (static/js/swisstopo_extent.js);
 *   - a polygon straddling the border keeps only its outside part, and its
 *     holes;
 *   - a line crossing from outside in keeps only the outside piece, likewise
 *     inside-out; a line crossing twice becomes two pieces; a line wholly
 *     inside is dropped;
 *   - points inside are dropped;
 *   - clipLayers: an inside tile has no layers, an outside one is passed
 *     through as it was.
 *
 * The geometry tests use a hand-made extent in tile coordinates, so each
 * expected answer can be read off the numbers.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/vector_tile_kit.min.js';
import '../../static/js/swisstopo_extent.js';
import '../../static/js/osm_clip_core.js';

const core = window.pwaOsmClipCore;
const clipping = window.VectorTileKit.polygonClipping;
const { EXTENT } = window.pwaSwisstopoExtent;

/** The tile at zoom z holding a point. */
function tileAt(lng, lat, z) {
  const n = 2 ** z;
  const sin = Math.sin((lat * Math.PI) / 180);
  return {
    z,
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor((0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * n),
  };
}

/** A closed rectangle ring, wound as a vector tile's exterior. */
function rect(x0, y0, x1, y1) {
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
}

/** The signed area (surveyor's formula), positive for an exterior. */
function area(ring) {
  let sum = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    sum += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
  }
  return sum / 2;
}

// The extent is the tile's left half: x 0..2048.
const LEFT_HALF = [[rect(-2048, -2048, 2048, 6144)]];
// The extent is a band down the middle: x 1500..2500.
const BAND = [[rect(1500, -2048, 2500, 6144)]];

describe('parseUrl and tileUrl', () => {
  it('read and write a tile', () => {
    expect(core.parseUrl('osmclip://12/2130/1455')).toEqual({ z: 12, x: 2130, y: 1455 });
    expect(core.parseUrl('https://example.test/1/2/3')).toBeNull();
    expect(core.tileUrl('https://t.example/{z}/{x}/{y}.pbf', { z: 1, x: 2, y: 3 }))
      .toBe('https://t.example/1/2/3.pbf');
  });
});

describe('classifyTile, against the swisstopo extent', () => {
  it.each([
    ['Interlaken', [7.86, 46.68], 12],
    ['Bern', [7.45, 46.95], 11],
  ])('a tile over %s is inside', (_place, [lng, lat], z) => {
    expect(core.classifyTile(EXTENT, tileAt(lng, lat, z), 4096, clipping)).toBe(core.INSIDE);
  });

  it.each([
    ['Innsbruck', [11.39, 47.27], 12],
    ['Grenoble', [5.72, 45.19], 10],
  ])('a tile over %s is outside', (_place, [lng, lat], z) => {
    expect(core.classifyTile(EXTENT, tileAt(lng, lat, z), 4096, clipping)).toBe(core.OUTSIDE);
  });

  it.each([
    ['Basel', [7.59, 47.56], 12],
    ['the Swiss Alps from far out', [8.2, 46.8], 6],
  ])('a tile over %s is on the border', (_place, [lng, lat], z) => {
    expect(core.classifyTile(EXTENT, tileAt(lng, lat, z), 4096, clipping)).toBe(core.BORDER);
  });
});

describe('clipPolygon', () => {
  it('keeps only the outside part of a polygon across the border, and its hole', () => {
    const outer = rect(1024, 1024, 3072, 3072);
    const outsideHole = rect(2304, 1536, 2816, 2560).reverse();
    const insideHole = rect(1280, 1536, 1792, 2560).reverse();
    const rings = core.clipPolygon([outer, outsideHole, insideHole], LEFT_HALF, clipping);
    expect(rings).toHaveLength(2);
    const [exterior, hole] = rings;
    expect(area(exterior)).toBeGreaterThan(0);
    expect(area(hole)).toBeLessThan(0);
    const xs = exterior.map(([x]) => x);
    expect(Math.min(...xs)).toBe(2048);
    expect(Math.max(...xs)).toBe(3072);
    expect(Math.abs(area(exterior))).toBe(1024 * 2048);
    expect(Math.abs(area(hole))).toBe(512 * 1024);
  });

  it('drops a polygon wholly inside, and keeps one wholly outside', () => {
    expect(core.clipPolygon([rect(100, 100, 900, 900)], LEFT_HALF, clipping)).toEqual([]);
    const kept = core.clipPolygon([rect(3000, 100, 3900, 900)], LEFT_HALF, clipping);
    expect(kept).toHaveLength(1);
    expect(Math.abs(area(kept[0]))).toBe(900 * 800);
  });
});

describe('cutLine', () => {
  it('keeps the outside piece of a line crossing from outside in', () => {
    expect(core.cutLine([[3000, 100], [1000, 100]], LEFT_HALF)).toEqual([[[3000, 100], [2048, 100]]]);
  });

  it('keeps the outside piece of a line crossing from inside out', () => {
    expect(core.cutLine([[1000, 100], [3000, 100]], LEFT_HALF)).toEqual([[[2048, 100], [3000, 100]]]);
  });

  it('makes two pieces of a line crossing twice', () => {
    expect(core.cutLine([[1000, 2000], [2000, 2000], [3000, 2000]], BAND)).toEqual([
      [[1000, 2000], [1500, 2000]],
      [[2500, 2000], [3000, 2000]],
    ]);
  });

  it('drops a line wholly inside', () => {
    expect(core.cutLine([[100, 100], [900, 900]], LEFT_HALF)).toEqual([]);
  });

  it('keeps a line wholly outside as it is', () => {
    expect(core.cutLine([[3000, 100], [3500, 900], [3900, 100]], LEFT_HALF))
      .toEqual([[[3000, 100], [3500, 900], [3900, 100]]]);
  });
});

describe('clipFeature', () => {
  it('drops the points inside and keeps the rest', () => {
    const feature = { type: 1, geometry: [[[100, 100]], [[3000, 100]]], tags: { name: 'x' } };
    expect(core.clipFeature(feature, LEFT_HALF, clipping)).toEqual({
      type: 1, geometry: [[3000, 100]], tags: { name: 'x' },
    });
  });

  it('is null when nothing is outside', () => {
    expect(core.clipFeature({ type: 1, geometry: [[[100, 100]]] }, LEFT_HALF, clipping)).toBeNull();
    expect(core.clipFeature({ type: 2, geometry: [[[100, 100], [200, 200]]] }, LEFT_HALF, clipping)).toBeNull();
  });
});

describe('clipLayers', () => {
  const layers = {
    water: { extent: 4096, features: [{ type: 3, geometry: [rect(100, 100, 900, 900)], tags: {} }] },
  };

  it('answers an inside tile with no layers', () => {
    const result = core.clipLayers(layers, EXTENT, tileAt(7.86, 46.68, 12), clipping);
    expect(result).toEqual({ status: core.INSIDE, layers: {} });
  });

  it('passes an outside tile through as it was', () => {
    const result = core.clipLayers(layers, EXTENT, tileAt(11.39, 47.27, 12), clipping);
    expect(result.status).toBe(core.OUTSIDE);
    expect(result.layers).toBe(layers);
  });

  it('clips each layer of a border tile, and drops a layer left empty', () => {
    // A synthetic extent: the western hemisphere, so tile 1/0/0 is inside
    // and tile 0/0/0 has its left half inside.
    const west = [[[[-180, -85.06], [0, -85.06], [0, 85.06], [-180, 85.06], [-180, -85.06]]]];
    const tile = { z: 0, x: 0, y: 0 };
    const mixed = {
      water: { extent: 4096, features: [{ type: 3, geometry: [rect(1024, 1024, 3072, 3072)], tags: {} }] },
      place: { extent: 4096, features: [{ type: 1, geometry: [[[100, 100]]], tags: {} }] },
    };
    const result = core.clipLayers(mixed, west, tile, clipping);
    expect(result.status).toBe(core.BORDER);
    expect(Object.keys(result.layers)).toEqual(['water']);
    const [ring] = result.layers.water.features[0].geometry;
    expect(Math.min(...ring.map(([x]) => x))).toBe(2048);
  });
});
