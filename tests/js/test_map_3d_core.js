/*
 * tests/js/test_map_3d_core.js — the 3-D terrain map's pure parts (SNOW-1438).
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the DEM source is Terrarium-encoded and the terrain spec names it;
 *   - the exaggeration stays modest, so 30° does not read as 40°;
 *   - the tours box never spans more than /api/ski-tours.geojson answers
 *     (0.5°), whatever half-span is asked for;
 *   - a tapped line's bounds cover LineString and MultiLineString, and
 *     anything else is null rather than a camera move to nowhere;
 *   - a tap frames the whole route or tour (every leg with its uuid), not
 *     the clipped fragment MapLibre hands back.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_3d_core.js';

const core = window.pwaMap3dCore;

describe('demSource / terrainSpec', () => {
  it('builds a Terrarium raster-dem source from the template', () => {
    const source = core.demSource('https://dem.example/{z}/{x}/{y}.png');
    expect(source).toEqual({
      type: 'raster-dem',
      tiles: ['https://dem.example/{z}/{x}/{y}.png'],
      encoding: 'terrarium',
      tileSize: 256,
      maxzoom: 15,
    });
  });

  it('points the terrain at the DEM source with a modest exaggeration', () => {
    const spec = core.terrainSpec();
    expect(spec.source).toBe(core.DEM_SOURCE_ID);
    expect(spec.exaggeration).toBeGreaterThanOrEqual(1);
    expect(spec.exaggeration).toBeLessThanOrEqual(1.5);
  });
});

describe('toursBbox', () => {
  /** @param {string} box */
  const spans = (box) => {
    const [w, s, e, n] = box.split(',').map(Number);
    return [e - w, n - s];
  };

  it('is the centre plus the default half-span, west,south,east,north', () => {
    expect(core.toursBbox({ lng: 7.87, lat: 46.07 })).toBe('7.63,45.83,8.11,46.31');
  });

  it('never asks for more than the endpoint answers', () => {
    for (const box of [core.toursBbox({ lng: 7, lat: 46 }, 1), core.toursBbox({ lng: 7, lat: 46 })]) {
      const [width, height] = spans(box);
      expect(width).toBeLessThanOrEqual(0.5);
      expect(height).toBeLessThanOrEqual(0.5);
    }
  });

  it('keeps latitude on the globe', () => {
    const [, s, , n] = core.toursBbox({ lng: 0, lat: 89.9 }).split(',').map(Number);
    expect(n).toBe(90);
    expect(s).toBeCloseTo(89.66, 5);
  });
});

describe('lineBounds', () => {
  it('bounds a LineString', () => {
    expect(core.lineBounds({
      type: 'LineString',
      coordinates: [[7.9, 46.1, 3000], [7.8, 46.0, 2000], [7.85, 46.2]],
    })).toEqual([[7.8, 46.0], [7.9, 46.2]]);
  });

  it('bounds every part of a MultiLineString', () => {
    expect(core.lineBounds({
      type: 'MultiLineString',
      coordinates: [[[7, 46], [7.1, 46.1]], [[6.9, 46.3], [7.2, 45.9]]],
    })).toEqual([[6.9, 45.9], [7.2, 46.3]]);
  });

  it.each([
    [null],
    [undefined],
    [{ type: 'Point', coordinates: [7, 46] }],
    [{ type: 'LineString', coordinates: [] }],
  ])('is null for %j', (geometry) => {
    expect(core.lineBounds(geometry)).toBeNull();
  });
});

describe('boundsForUuid', () => {
  const legs = {
    type: 'FeatureCollection',
    features: [
      { properties: { uuid: 'a' }, geometry: { type: 'LineString', coordinates: [[7, 46], [7.1, 46.1]] } },
      { properties: { uuid: 'b' }, geometry: { type: 'LineString', coordinates: [[9, 47], [9.1, 47.1]] } },
      { properties: { uuid: 'a' }, geometry: { type: 'LineString', coordinates: [[7.1, 46.1], [7.3, 45.9]] } },
      { properties: { uuid: 'a', kind: 'summit' }, geometry: { type: 'Point', coordinates: [8, 48] } },
    ],
  };

  it('unions every leg with the uuid, and skips other uuids and points', () => {
    expect(core.boundsForUuid(legs, 'a')).toEqual([[7, 45.9], [7.3, 46.1]]);
  });

  it.each([
    [legs, 'missing'],
    [legs, ''],
    [null, 'a'],
    [{}, 'a'],
  ])('is null when nothing matches (%#)', (collection, uuid) => {
    expect(core.boundsForUuid(collection, uuid)).toBeNull();
  });
});
