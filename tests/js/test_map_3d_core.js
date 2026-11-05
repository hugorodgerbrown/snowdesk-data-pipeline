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
    expect(source).toMatchObject({
      type: 'raster-dem',
      tiles: ['https://dem.example/{z}/{x}/{y}.png'],
      encoding: 'terrarium',
      tileSize: 512,
      maxzoom: 15,
    });
    expect(source.attribution).toContain('Mapterhorn');
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

describe('boundsFor', () => {
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
    expect(core.boundsFor(legs, 'uuid', 'a')).toEqual([[7, 45.9], [7.3, 46.1]]);
  });

  it.each([
    [legs, 'missing'],
    [legs, ''],
    [null, 'a'],
    [{}, 'a'],
  ])('is null when nothing matches (%#)', (collection, uuid) => {
    expect(core.boundsFor(collection, 'uuid', uuid)).toBeNull();
  });

  it('matches a shared route by its token, which carries no uuid', () => {
    const shared = {
      features: [
        { properties: { token: 't1', pending: true }, geometry: { type: 'LineString', coordinates: [[8, 46], [8.2, 46.3]] } },
        { properties: { uuid: 't1' }, geometry: { type: 'LineString', coordinates: [[9, 47], [9.1, 47.1]] } },
      ],
    };
    expect(core.boundsFor(shared, 'token', 't1')).toEqual([[8, 46], [8.2, 46.3]]);
  });
});

describe('pageUrl', () => {
  it('carries the Map3dShare token, then the route share, then the view', () => {
    expect(core.pageUrl('https://s.example', '/map/3d/', { share: 'a b', routeShare: 'r1' }, 'at=1&layers=')).toBe(
      'https://s.example/map/3d/?share=a%20b&route_share=r1&at=1&layers=',
    );
  });

  it('leaves out an empty token', () => {
    expect(core.pageUrl('https://s.example', '/map/3d/', { share: '', routeShare: '' }, 'at=1')).toBe(
      'https://s.example/map/3d/?at=1',
    );
  });
});

describe('routeShare3dUrl', () => {
  it('hangs 3d/ and the view off the route share link', () => {
    expect(core.routeShare3dUrl('https://s.example/routes/s/tok/', 'at=7,46,12,60,0&layers=tours')).toBe(
      'https://s.example/routes/s/tok/3d/?at=7,46,12,60,0&layers=tours',
    );
  });

  it('copes with a link without its trailing slash', () => {
    expect(core.routeShare3dUrl('https://s.example/routes/s/tok', 'at=1')).toBe(
      'https://s.example/routes/s/tok/3d/?at=1',
    );
  });
});

describe('wildlifeTileUrl', () => {
  it('builds a swisstopo WMTS Web Mercator template for each wildlife layer', () => {
    for (const layer of core.WILDLIFE_LAYERS) {
      expect(core.wildlifeTileUrl(layer)).toBe(
        `https://wmts.geo.admin.ch/1.0.0/${layer}/default/current/3857/{z}/{x}/{y}.png`,
      );
    }
  });
});

describe('viewQuery / parseView', () => {
  const camera = { center: { lng: 7.871234567, lat: 46.0712345 }, zoom: 12.6789, pitch: 64.04, bearing: -145.26 };

  it('writes the camera, rounded, and the switches that are on', () => {
    expect(core.viewQuery(camera, { steep: true, tours: false, wildlife: true })).toBe(
      'at=7.87123,46.07123,12.68,64,-145.3&layers=steep,wildlife',
    );
  });

  it('round-trips a view', () => {
    const query = core.viewQuery(camera, { steep: false, tours: true, wildlife: true });
    expect(core.parseView(`?${query}`)).toEqual({
      camera: { center: [7.87123, 46.07123], zoom: 12.68, pitch: 64, bearing: -145.3 },
      layers: { steep: false, tours: true, wildlife: true },
    });
  });

  it('keeps an empty layers list as every switch off', () => {
    expect(core.parseView('?layers=').layers).toEqual({ steep: false, tours: false, wildlife: false });
  });

  it('falls back to the defaults with no query', () => {
    expect(core.parseView('')).toEqual({ camera: null, layers: { ...core.DEFAULT_LAYERS } });
  });

  it.each([
    'at=7,46,12',
    'at=a,46,12,60,0',
    'at=200,46,12,60,0',
    'at=7,46,12,89,0',
  ])('drops a malformed camera (%s)', (query) => {
    expect(core.parseView(`?${query}`).camera).toBeNull();
  });
});

describe('viewQuery longitude', () => {
  it('wraps a centre from another world copy back into [-180, 180]', () => {
    const query = core.viewQuery(
      { center: { lng: 367.87, lat: 46.07 }, zoom: 12, pitch: 60, bearing: 0 },
      core.DEFAULT_LAYERS,
    );
    expect(core.parseView(`?${query}`).camera.center).toEqual([7.87, 46.07]);
  });
});

describe('MAX_PIXEL_RATIO', () => {
  it('caps the canvas at 2x', () => {
    expect(core.MAX_PIXEL_RATIO).toBe(2);
  });
});

describe('prefetchDemTiles / tileUrl', () => {
  it('returns the 3x3 block two zooms below the map, around the centre', () => {
    const tiles = core.prefetchDemTiles({ lng: 7.87, lat: 46.07 }, 12.6);
    expect(tiles).toHaveLength(9);
    expect(new Set(tiles.map((t) => t.z))).toEqual(new Set([11]));
    // The centre tile of z11 over Saas-Fee.
    expect(tiles[4]).toEqual({ z: 11, x: 1068, y: 728 });
  });

  it('never asks past the DEM source maxzoom', () => {
    const tiles = core.prefetchDemTiles({ lng: 7.87, lat: 46.07 }, 20);
    expect(tiles.every((t) => t.z === 15)).toBe(true);
  });

  it('wraps x across the antimeridian and drops rows off the world', () => {
    const tiles = core.prefetchDemTiles({ lng: 179.99, lat: 85 }, 4);
    expect(tiles.every((t) => t.x >= 0 && t.x < 4)).toBe(true);
    expect(tiles.every((t) => t.y >= 0)).toBe(true);
    expect(tiles.length).toBeLessThan(9);
  });

  it('fills an XYZ template', () => {
    expect(core.tileUrl('https://t.example/{z}/{x}/{y}.webp', { z: 1, x: 2, y: 3 }))
      .toBe('https://t.example/1/2/3.webp');
  });
});
