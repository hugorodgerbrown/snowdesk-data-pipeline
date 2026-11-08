/*
 * tests/js/test_swisstopo_extent.js — the swisstopo extent and the
 * detailed-relief outline (SNOW-1460, static/js/swisstopo_extent.js).
 *
 * What is pinned: every ring is closed and frozen; places in Switzerland
 * and Liechtenstein are inside the extent, the two enclaves and the
 * neighbours are not; the detailed relief holds the extent and the strip
 * east to about 10.5°E, and not Innsbruck or the Pyrenees side.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/swisstopo_extent.js';

const { EXTENT, EXTENT_BBOX, DETAILED_RELIEF, DETAILED_RELIEF_BBOX } = window.pwaSwisstopoExtent;

/** Even-odd point in ring. */
function inRing(ring, [x, y]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Inside a list of polygons with holes. */
function inPolygons(polygons, point) {
  return polygons.some(([outer, ...holes]) => inRing(outer, point) && !holes.some((h) => inRing(h, point)));
}

describe('the rings', () => {
  it('are closed and frozen', () => {
    for (const polygons of [EXTENT, DETAILED_RELIEF]) {
      expect(Object.isFrozen(polygons)).toBe(true);
      for (const polygon of polygons) {
        for (const ring of polygon) {
          expect(ring.length).toBeGreaterThan(3);
          expect(ring[0]).toEqual(ring[ring.length - 1]);
          expect(Object.isFrozen(ring)).toBe(true);
          expect(Object.isFrozen(ring[0])).toBe(true);
        }
      }
    }
  });

  it('carry bounding boxes that hold them', () => {
    for (const [polygons, [w, s, e, n]] of [[EXTENT, EXTENT_BBOX], [DETAILED_RELIEF, DETAILED_RELIEF_BBOX]]) {
      for (const [lng, lat] of polygons.flat(2)) {
        expect(lng >= w && lng <= e && lat >= s && lat <= n).toBe(true);
      }
    }
  });
});

describe('the extent', () => {
  it.each([
    ['Zermatt', [7.75, 46.02]],
    ['Basel', [7.59, 47.56]],
    ['Geneva', [6.14, 46.2]],
    ['Val Müstair', [10.4, 46.62]],
    ['Vaduz', [9.52, 47.14]],
    ['Malbun', [9.61, 47.1]],
  ])('holds %s', (_place, point) => {
    expect(inPolygons(EXTENT, point)).toBe(true);
  });

  it.each([
    ['Büsingen am Hochrhein', [8.69, 47.697]],
    ['Campione d\'Italia', [8.971, 45.97]],
    ['Chamonix', [6.87, 45.92]],
    ['Aosta', [7.32, 45.74]],
    ['St Anton', [10.27, 47.13]],
    ['Feldkirch', [9.6, 47.24]],
    ['Livigno', [10.13, 46.54]],
  ])('leaves out %s', (_place, point) => {
    expect(inPolygons(EXTENT, point)).toBe(false);
  });
});

describe('the detailed relief', () => {
  it.each([
    ['Zermatt', [7.75, 46.02]],
    ['Chamonix', [6.87, 45.92]],
    ['Livigno', [10.13, 46.54]],
    ['Feldkirch', [9.6, 47.24]],
    ['St Anton', [10.27, 47.13]],
  ])('holds %s', (_place, point) => {
    expect(inPolygons(DETAILED_RELIEF, point)).toBe(true);
  });

  it.each([
    ['Landeck', [10.57, 47.14]],
    ['Innsbruck', [11.39, 47.27]],
    ['Sölden', [11.01, 46.97]],
    ['Grenoble', [5.72, 45.19]],
  ])('leaves out %s', (_place, point) => {
    expect(inPolygons(DETAILED_RELIEF, point)).toBe(false);
  });
});
