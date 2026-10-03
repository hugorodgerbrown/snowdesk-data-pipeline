/*
 * tests/js/test_terrain_filter_core.js — the terrain filter's pixel contract,
 * classifier and state (SNOW-978).
 *
 * Every pixel of a terrain-class tile carries height, aspect octant and a
 * 5° slope band; the device turns those into colour for the reader's
 * filter. An off-by-one at an octant or band edge paints the wrong slopes
 * with no error anywhere, so the edges are tested one by one, and the
 * decode is run against a REAL PNG (tests/js/fixtures/terrain-class/
 * fixture.png, written by bin/build-terrain-class-fixture from the DEBUG
 * synthetic tileset) rather than only against arrays built by hand.
 */

import { readFileSync } from 'node:fs';
import sharp from 'sharp';
import { beforeAll, describe, expect, it } from 'vitest';

/** @type {any} */
let core;

beforeAll(async () => {
  await import('../../static/js/terrain_filter_core.js');
  core = self.pwaTerrainFilterCore;
});

/** A blue channel for an octant and band. */
const blue = (octant, band) => (octant << 5) | band;

/** A decoded data cell, for driving the classifier directly. */
const cell = (octant, band, height = 2500) => core.decodeCell(height >> 8, height & 255, blue(octant, band));

const TILE = { z: 13, x: 4260, y: 2911 };

describe('decodeCell', () => {
  it('reads the height big-endian from R and G', () => {
    expect(core.decodeCell(0x0d, 0x48, blue(0, 0)).height).toBe(3400);
    expect(core.decodeCell(0xff, 0xff, blue(0, 0)).height).toBe(65535);
  });

  it('splits B into octant (top three bits) and band (bottom five)', () => {
    const c = core.decodeCell(0, 0, blue(7, 17));
    expect(c).toMatchObject({ kind: 'data', octant: 7, band: 17 });
  });

  it('reads 254 as level ground that keeps its height', () => {
    expect(core.decodeCell(7, 8, 254)).toMatchObject({ kind: 'level', height: 1800, octant: -1 });
  });

  it('reads 255 as no data', () => {
    expect(core.decodeCell(0, 0, 255)).toMatchObject({ kind: 'nodata', height: 0 });
  });

  it('writes into a supplied cell rather than allocating', () => {
    const out = { kind: 'nodata', height: 0, octant: -1, band: -1 };
    expect(core.decodeCell(1, 0, blue(3, 4), out)).toBe(out);
    expect(out).toMatchObject({ kind: 'data', height: 256, octant: 3, band: 4 });
  });
});

describe('createFilterClassifier', () => {
  const { NONE, MATCH, NODATA } = { NONE: 0, MATCH: 1, NODATA: 2 };

  it('uses the palette indices the module declares', () => {
    expect(core.PALETTE_INDEX).toEqual({ NONE, MATCH, NODATA });
  });

  it('matches only the chosen octants', () => {
    const classify = core.createFilterClassifier({ aspects: [0, 1, 7], minSlope: 30 });
    for (let octant = 0; octant < 8; octant += 1) {
      const expected = [0, 1, 7].includes(octant) ? MATCH : NONE;
      expect(classify(cell(octant, 7), 0, 0, TILE)).toBe(expected);
    }
  });

  it('starts at the minimum slope exactly: 35° is band 7, 34.9° is band 6', () => {
    const classify = core.createFilterClassifier({ minSlope: 35 });
    expect(classify(cell(0, 6), 0, 0, TILE)).toBe(NONE);
    expect(classify(cell(0, 7), 0, 0, TILE)).toBe(MATCH);
    expect(classify(cell(0, 17), 0, 0, TILE)).toBe(MATCH);
  });

  it('stops below the maximum slope: under 45° is band 8 and no higher', () => {
    const classify = core.createFilterClassifier({ minSlope: 30, maxSlope: 45 });
    expect(classify(cell(0, 5), 0, 0, TILE)).toBe(NONE);
    expect(classify(cell(0, 6), 0, 0, TILE)).toBe(MATCH);
    expect(classify(cell(0, 8), 0, 0, TILE)).toBe(MATCH);
    expect(classify(cell(0, 9), 0, 0, TILE)).toBe(NONE);
  });

  it('works for every rung of the ladder', () => {
    for (const min of core.SLOPE_LADDER) {
      const classify = core.createFilterClassifier({ minSlope: min });
      expect(classify(cell(0, min / 5 - 1), 0, 0, TILE)).toBe(NONE);
      expect(classify(cell(0, min / 5), 0, 0, TILE)).toBe(MATCH);
    }
  });

  it('bounds the elevation band at both ends, inclusively', () => {
    const classify = core.createFilterClassifier({ minElevation: 2400, maxElevation: 3000 });
    expect(classify(cell(0, 7, 2399), 0, 0, TILE)).toBe(NONE);
    expect(classify(cell(0, 7, 2400), 0, 0, TILE)).toBe(MATCH);
    expect(classify(cell(0, 7, 3000), 0, 0, TILE)).toBe(MATCH);
    expect(classify(cell(0, 7, 3001), 0, 0, TILE)).toBe(NONE);
  });

  it('marks no data with the hatch index, which is not "no match"', () => {
    const classify = core.createFilterClassifier({});
    const nodata = classify(core.decodeCell(0, 0, 255), 0, 0, TILE);
    expect(nodata).toBe(NODATA);
    expect(nodata).not.toBe(NONE);
  });

  it('never matches level ground, whatever the filter', () => {
    const classify = core.createFilterClassifier({ minSlope: 30, minElevation: 0 });
    expect(classify(core.decodeCell(7, 8, 254), 0, 0, TILE)).toBe(NONE);
  });

  it('matches nothing with every aspect switched off', () => {
    const classify = core.createFilterClassifier({ aspects: [] });
    expect(classify(cell(0, 10), 0, 0, TILE)).toBe(NONE);
  });
});

describe('paintTile', () => {
  /** A 256 px class tile with one value everywhere. */
  const uniform = (r, g, b) => {
    const data = new Uint8ClampedArray(256 * 256 * 4);
    for (let i = 0; i < data.length; i += 4) {
      data[i] = r;
      data[i + 1] = g;
      data[i + 2] = b;
      data[i + 3] = 255;
    }
    return data;
  };

  it('paints a match in the tint, a miss transparent', () => {
    const out = new Uint8ClampedArray(256 * 256 * 4);
    core.paintTile(uniform(9, 196, blue(0, 8)), out, core.createFilterClassifier({}), core.PALETTE, TILE);
    expect([...out.slice(0, 4)]).toEqual([0x25, 0x63, 0xeb, 115]);
    core.paintTile(uniform(9, 196, blue(0, 2)), out, core.createFilterClassifier({}), core.PALETTE, TILE);
    expect([...out.slice(0, 4)]).toEqual([0, 0, 0, 0]);
  });

  it('draws no data as a hatch from pixel coordinates, seamless across tiles', () => {
    const out = new Uint8ClampedArray(256 * 256 * 4);
    core.paintTile(core.noDataTile(), out, core.createFilterClassifier({}), core.PALETTE, TILE);
    const alphaAt = (x, y) => out[(y * 256 + x) * 4 + 3];
    expect(alphaAt(0, 0)).toBeGreaterThan(0); // (0 + 0) % 8 < 2
    expect(alphaAt(4, 0)).toBe(0);
    expect(alphaAt(7, 1)).toBeGreaterThan(0); // (7 + 1) % 8 = 0
    expect(256 % core.HATCH_PERIOD).toBe(0);
  });

  it('hands a custom classifier the tile and the pixel coordinates', () => {
    // SNOW-979's seam: a four-state palette and per-region clipping need
    // to know where a pixel is, not just what it says.
    const palette = [
      { id: 'a', rgba: [0, 0, 0, 0] },
      { id: 'b', rgba: [255, 0, 0, 255] },
      { id: 'c', rgba: [0, 255, 0, 255] },
      { id: 'd', rgba: [0, 0, 255, 255] },
    ];
    const seen = [];
    const classify = (c, px, py, tile) => {
      if (px === 3 && py === 5) seen.push({ kind: c.kind, tile });
      return (px + py) % 4;
    };
    const out = new Uint8ClampedArray(256 * 256 * 4);
    core.paintTile(uniform(9, 196, blue(2, 7)), out, classify, palette, TILE);
    expect(seen).toEqual([{ kind: 'data', tile: TILE }]);
    expect([...out.slice((5 * 256 + 3) * 4, (5 * 256 + 3) * 4 + 4)]).toEqual([0, 0, 0, 0]); // 8 % 4 = 0
    expect([...out.slice(4, 8)]).toEqual([255, 0, 0, 255]); // (1 + 0) % 4 = 1
    expect([...out.slice(12, 16)]).toEqual([0, 0, 255, 255]); // (3 + 0) % 4 = 3
  });
});

describe('the committed fixture tile', () => {
  /** @type {Uint8ClampedArray} */
  let pixels;

  beforeAll(async () => {
    const { data, info } = await sharp('tests/js/fixtures/terrain-class/fixture.png')
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    expect([info.width, info.height, info.channels]).toEqual([256, 256, 4]);
    pixels = new Uint8ClampedArray(data);
  });

  const at = (x, y) => {
    const i = (y * 256 + x) * 4;
    return core.decodeCell(pixels[i], pixels[i + 1], pixels[i + 2]);
  };

  it('is opaque everywhere', () => {
    for (let i = 3; i < pixels.length; i += 4) expect(pixels[i]).toBe(255);
  });

  it('decodes the known pixels the generator placed', () => {
    // Values from apps/public/dev_terrain_class.py for tile 12/2130/1455.
    expect(at(81, 122)).toMatchObject({ kind: 'data', height: 3400, octant: 1, band: 0 });
    expect(at(81, 107)).toMatchObject({ height: 3269, octant: 0, band: 7 }); // north flank
    expect(at(96, 122)).toMatchObject({ height: 3268, octant: 2, band: 7 }); // east flank
    expect(at(81, 137)).toMatchObject({ height: 3272, octant: 4, band: 6 }); // south flank
    expect(at(66, 122)).toMatchObject({ height: 3269, octant: 6, band: 7 }); // west flank
    expect(at(111, 122)).toMatchObject({ height: 2683, octant: 2, band: 14 });
    expect(at(43, 206)).toMatchObject({ kind: 'level', height: 1800 }); // the lake
    expect(at(197, 51)).toMatchObject({ kind: 'nodata' }); // the hole
    expect(at(0, 0)).toMatchObject({ height: 1388, octant: 4, band: 0 }); // the base plane
  });

  it('paints the north flank for a north filter and leaves the south flank', () => {
    const out = new Uint8ClampedArray(pixels.length);
    core.paintTile(pixels, out, core.createFilterClassifier({ aspects: [0], minSlope: 30 }), core.PALETTE, TILE);
    expect(out[(107 * 256 + 81) * 4 + 3]).toBe(115);
    expect(out[(137 * 256 + 81) * 4 + 3]).toBe(0);
  });
});

describe('availability', () => {
  it('asks for a zoom-in below z12 inside coverage', () => {
    expect(core.availability({ zoom: 11.9, lng: 7.23, lat: 46.1 })).toBe('zoom-in');
    expect(core.availability({ zoom: 12, lng: 7.23, lat: 46.1 })).toBe('ok');
    expect(core.availability({ zoom: 16, lng: 7.23, lat: 46.1 })).toBe('ok');
  });

  it('says out of coverage before it says zoom in', () => {
    expect(core.availability({ zoom: 8, lng: 11.87, lat: 47.17 })).toBe('out-of-coverage');
    expect(core.availability({ zoom: 14, lng: 9.19, lat: 45.46 })).toBe('out-of-coverage'); // Milan
  });

  it('answers out of coverage for a non-finite camera', () => {
    expect(core.availability({ zoom: 13, lng: NaN, lat: 46 })).toBe('out-of-coverage');
  });

  it('tells a covered tile from one outside', () => {
    expect(core.tileInCoverage(12, 2130, 1455)).toBe(true);
    expect(core.tileInCoverage(12, 2200, 1455)).toBe(false);
  });
});

describe('filter state', () => {
  it('defaults to every aspect, 30° and up, no elevation band', () => {
    expect(core.normaliseFilter(undefined)).toEqual({
      aspects: [0, 1, 2, 3, 4, 5, 6, 7],
      minSlope: 30,
      maxSlope: null,
      minElevation: null,
      maxElevation: null,
    });
  });

  it('falls back to the defaults for garbage', () => {
    for (const text of [null, '', 'not json', '42', '"x"', '[]']) {
      expect(core.parseFilter(text)).toEqual(core.normaliseFilter(null));
    }
    expect(core.parseFilter('{"aspects":[0,9],"minSlope":33,"maxSlope":"x"}')).toEqual(
      core.normaliseFilter(null),
    );
  });

  it('drops a maximum that is not above the minimum', () => {
    expect(core.normaliseFilter({ minSlope: 40, maxSlope: 40 }).maxSlope).toBeNull();
    expect(core.normaliseFilter({ minElevation: 3000, maxElevation: 2000 }).maxElevation).toBeNull();
  });

  it('snaps elevations to 100 m and rejects ones out of range', () => {
    expect(core.normaliseFilter({ minElevation: 2449 }).minElevation).toBe(2400);
    expect(core.normaliseFilter({ minElevation: -100 }).minElevation).toBeNull();
    expect(core.normaliseFilter({ maxElevation: 9000 }).maxElevation).toBeNull();
  });

  it('round-trips through storage and dedupes aspects', () => {
    const f = { aspects: [7, 0, 0, 1], minSlope: 35, maxSlope: 45, minElevation: 2400, maxElevation: null };
    expect(core.parseFilter(core.serialiseFilter(f))).toEqual({
      aspects: [0, 1, 7], minSlope: 35, maxSlope: 45, minElevation: 2400, maxElevation: null,
    });
  });

  it('gives two different filters two different hashes', () => {
    expect(core.filterHash({})).toBe('ff-30---');
    expect(core.filterHash({ aspects: [0] })).not.toBe(core.filterHash({ aspects: [1] }));
  });
});

describe('summarise', () => {
  const strings = {
    'aspects-all': 'All aspects',
    'aspects-none': 'No aspects',
    'aspects-one': '1 aspect',
    'aspects-count': '%(count)s aspects',
    'slope-min': '%(min)s°+',
    'slope-range': '%(min)s–%(max)s°',
    'elevation-above': 'above %(min)s m',
    'elevation-below': 'below %(max)s m',
    'elevation-range': '%(min)s–%(max)s m',
    'compass-0': 'N', 'compass-1': 'NE', 'compass-7': 'NW',
  };

  it('names the aspects, slope and band for the map chip', () => {
    expect(core.summarise({ aspects: [0, 1, 7], minSlope: 35, minElevation: 2400 }, strings))
      .toBe('N, NE, NW · 35°+ · above 2,400 m');
  });

  it('counts the aspects for the menu row, and leaves the band out', () => {
    expect(core.summarise({ aspects: [0, 1, 7], minSlope: 35, minElevation: 2400 }, strings, { short: true }))
      .toBe('3 aspects · 35°+');
  });

  it('reads a closed slope range and a closed band', () => {
    expect(core.summarise({ minSlope: 30, maxSlope: 40, minElevation: 2000, maxElevation: 3000 }, strings))
      .toBe('All aspects · 30–40° · 2,000–3,000 m');
  });
});

describe('urls and the tile cache', () => {
  it('fills the XYZ template', () => {
    expect(core.tileUrl('https://t.test/{z}/{x}/{y}.png', 13, 4260, 2911))
      .toBe('https://t.test/13/4260/2911.png');
  });

  it('round-trips the protocol url', () => {
    const url = core.protocolTileUrl('ff-30---').replace('{z}', '13').replace('{x}', '4260').replace('{y}', '2911');
    expect(core.parseProtocolUrl(url)).toEqual({ z: 13, x: 4260, y: 2911 });
    expect(core.parseProtocolUrl('https://elsewhere/1/2/3')).toBeNull();
  });

  it('evicts the least recently used tile', () => {
    const cache = core.createTileCache(2);
    cache.set('a', 1);
    cache.set('b', 2);
    cache.get('a');
    cache.set('c', 3);
    expect(cache.get('b')).toBeUndefined();
    expect(cache.get('a')).toBe(1);
    expect(cache.size()).toBe(2);
  });
});

describe('palette', () => {
  it('matches the tokens src/css/main.css declares', () => {
    const css = readFileSync('src/css/main.css', 'utf8');
    for (const entry of core.PALETTE.filter((e) => e.token)) {
      const match = new RegExp(`${entry.token}:\\s*(#[0-9a-fA-F]{6})`).exec(css);
      expect(match, entry.token).not.toBeNull();
      expect(match[1].toLowerCase()).toBe(entry.hex);
    }
  });

  it('is the accent blue at about 45%', () => {
    const match = core.PALETTE[core.PALETTE_INDEX.MATCH];
    expect(match.hex).toBe('#2563eb');
    expect(match.rgba[3] / 255).toBeCloseTo(0.45, 2);
  });
});
