/*
 * tests/js/test_bulletin_terrain_core.js — the problem-terrain layer's
 * rules, states and clipping (SNOW-979).
 *
 * The layer paints a region's steep ground by the day's bulletin. A wrong
 * state paints the wrong slopes with no error anywhere, so each precedence
 * rule is tested on a hand-made cell, and the clipping is tested against a
 * square whose pixel edges are known exactly.
 */

import { readFileSync } from 'node:fs';
import { beforeAll, describe, expect, it } from 'vitest';

/** @type {any} */
let core;
/** @type {any} */
let terrain;

beforeAll(async () => {
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/terrain_filter_core.js');
  await import('../../static/js/bulletin_terrain_core.js');
  core = self.pwaBulletinTerrainCore;
  terrain = self.pwaTerrainFilterCore;
});

/** A decoded data cell. */
const cell = (octant, band, height = 2500) =>
  terrain.decodeCell(height >> 8, height & 255, (octant << 5) | band);

const N = 0;
const S = 4;
const NW = 7;

/** A wind slab: considerable, NW–N–NE, above 2400 m. */
const WIND_SLAB = { rating: 3, aspects: [7, 0, 1], lower: 2400, upper: null, decided: true };
/** Persistent weak layers: moderate, every aspect but S, above 2200 m. */
const PWL = { rating: 2, aspects: [0, 1, 2, 3, 5, 6, 7], lower: 2200, upper: null, decided: true };

describe('normaliseRules', () => {
  it('turns aspects into a bitmask and missing bounds into open ends', () => {
    const [rule] = core.normaliseRules([WIND_SLAB]);
    expect(rule).toEqual({ rating: 3, mask: 0b10000011, lower: 2400, upper: Infinity, decided: true });
  });

  it('reads no aspects as every aspect', () => {
    expect(core.normaliseRules([{ ...PWL, aspects: [] }])[0].mask).toBe(0xff);
  });

  it('drops a rule with no usable rating, and anything that is not a list', () => {
    expect(core.normaliseRules([{ rating: 0 }, { rating: 6 }, null, { rating: 'x' }])).toEqual([]);
    expect(core.normaliseRules(null)).toEqual([]);
    expect(core.normaliseRules({ rating: 3 })).toEqual([]);
  });

  it('ignores octants outside 0–7 and treats a missing decided as decided', () => {
    const [rule] = core.normaliseRules([{ rating: 1, aspects: [0, 9, -1, 'N'] }]);
    expect(rule.mask).toBe(1);
    expect(rule.decided).toBe(true);
  });
});

describe('createClassifier', () => {
  const classify = (rules, c) => core.createClassifier(core.normaliseRules(rules))(c);
  const described = (rating) => core.STATE.DESCRIBED_BASE + rating;

  it('hatches no data before anything else', () => {
    expect(classify([WIND_SLAB], terrain.decodeCell(0, 0, 255))).toBe(core.STATE.NODATA);
  });

  it('leaves level ground and anything under 30° transparent', () => {
    expect(classify([WIND_SLAB], terrain.decodeCell(10, 0, 254))).toBe(core.STATE.NONE);
    expect(classify([WIND_SLAB], cell(N, 5))).toBe(core.STATE.NONE);
    expect(classify([WIND_SLAB], cell(N, 6))).toBe(described(3));
  });

  it('paints the highest-rated problem that admits the pixel', () => {
    expect(classify([PWL, WIND_SLAB], cell(NW, 7, 2500))).toBe(described(3));
    // Below the wind slab's band, only the weak layer admits it.
    expect(classify([PWL, WIND_SLAB], cell(NW, 7, 2300))).toBe(described(2));
  });

  it('tests the lower bound inclusively and the upper exclusively', () => {
    const band = { rating: 2, aspects: [], lower: 2000, upper: 2500, decided: true };
    expect(classify([band], cell(S, 8, 2000))).toBe(described(2));
    expect(classify([band], cell(S, 8, 1999))).toBe(core.STATE.UNDESCRIBED);
    expect(classify([band], cell(S, 8, 2500))).toBe(core.STATE.UNDESCRIBED);
  });

  it('paints steep ground no problem describes as its own state, never blank', () => {
    expect(classify([PWL, WIND_SLAB], cell(S, 8, 3000))).toBe(core.STATE.UNDESCRIBED);
    expect(classify([], cell(S, 8))).toBe(core.STATE.UNDESCRIBED);
  });

  it('paints a treeline or prose-only problem as undecided, under a decided match', () => {
    const treeline = { rating: 4, aspects: [S], lower: null, upper: null, decided: false };
    expect(classify([treeline], cell(S, 8))).toBe(core.STATE.UNDECIDED);
    expect(classify([treeline], cell(N, 8))).toBe(core.STATE.UNDESCRIBED);
    expect(classify([treeline, PWL], cell(N, 8, 2500))).toBe(described(2));
  });

  it('matches every aspect when a problem lists none', () => {
    const everywhere = { rating: 1, aspects: [], lower: null, upper: null, decided: true };
    for (let octant = 0; octant < 8; octant += 1) {
      expect(classify([everywhere], cell(octant, 9))).toBe(described(1));
    }
  });
});

describe('maskClassifier', () => {
  it('paints nothing outside the mask', () => {
    const mask = new Uint8Array(4);
    mask[3] = 1;
    const clipped = core.maskClassifier(() => 7, mask);
    expect(clipped(cell(N, 8), 0, 0)).toBe(core.STATE.NONE);
    expect(clipped(cell(N, 8), 1, 1)).toBe(7);
  });
});

describe('ringsOf', () => {
  it('flattens a MultiPolygon and passes a Polygon through', () => {
    const ring = [[0, 0], [1, 0], [1, 1], [0, 0]];
    expect(core.ringsOf({ type: 'Polygon', coordinates: [ring] })).toEqual([ring]);
    expect(core.ringsOf({ type: 'MultiPolygon', coordinates: [[ring], [ring, ring]] })).toHaveLength(3);
    expect(core.ringsOf({ type: 'Point', coordinates: [0, 0] })).toEqual([]);
    expect(core.ringsOf(null)).toEqual([]);
  });
});

describe('regionMask', () => {
  // z1 tile 0/0 covers lng -180..0 and lat 0..85.05; at a 256 px edge one
  // pixel is 360 / 512 degrees of longitude. The ring spans pixels 64–127
  // across, and rows from the equator (row 256, outside) up.
  const TILE = { z: 1, x: 0, y: 0 };
  const lngAt = (px) => (px / 512) * 360 - 180;

  it('fills pixels whose centres fall inside the ring, and only those', () => {
    const ring = [[lngAt(64), 1], [lngAt(128), 1], [lngAt(128), 60], [lngAt(64), 60], [lngAt(64), 1]];
    const mask = core.regionMask([ring], TILE);
    const row = 200;
    expect(mask[row * 256 + 63]).toBe(0);
    expect(mask[row * 256 + 64]).toBe(1);
    expect(mask[row * 256 + 127]).toBe(1);
    expect(mask[row * 256 + 128]).toBe(0);
  });

  it('leaves a hole in a ring with an inner ring (even-odd)', () => {
    const outer = [[lngAt(0), 1], [lngAt(200), 1], [lngAt(200), 80], [lngAt(0), 80], [lngAt(0), 1]];
    const inner = [[lngAt(80), 20], [lngAt(120), 20], [lngAt(120), 40], [lngAt(80), 40], [lngAt(80), 20]];
    const mask = core.regionMask([outer, inner], TILE);
    // Latitude 30 is inside the hole's band; pick its row by projecting.
    const y = ((1 - Math.log(Math.tan(Math.PI / 6) + 1 / Math.cos(Math.PI / 6)) / Math.PI) / 2) * 512;
    const row = Math.floor(y);
    expect(mask[row * 256 + 40]).toBe(1);
    expect(mask[row * 256 + 100]).toBe(0);
    expect(mask[row * 256 + 160]).toBe(1);
  });

  it('is null for a tile the region does not reach', () => {
    const ring = [[10, 46], [11, 46], [11, 47], [10, 46]];
    expect(core.regionMask([ring], TILE)).toBeNull();
    expect(core.regionMask([], TILE)).toBeNull();
  });
});

describe('the tile URL', () => {
  it('changes with the region and with the rules, and round-trips', () => {
    const rules = core.normaliseRules([WIND_SLAB]);
    const a = core.rulesHash('CH-4115', rules);
    expect(core.rulesHash('CH-4116', rules)).not.toBe(a);
    expect(core.rulesHash('CH-4115', core.normaliseRules([PWL]))).not.toBe(a);
    const url = core.protocolTileUrl(a).replace('{z}', '13').replace('{x}', '4262').replace('{y}', '2905');
    expect(core.parseProtocolUrl(url)).toEqual({ z: 13, x: 4262, y: 2905 });
    expect(core.parseProtocolUrl('terrainfilter://13/1/1')).toBeNull();
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

  it('puts each EAWS level at DESCRIBED_BASE + its number', () => {
    expect(core.PALETTE[core.STATE.DESCRIBED_BASE + 3].id).toBe('considerable');
    expect(core.PALETTE[core.STATE.DESCRIBED_BASE + 5].id).toBe('very_high');
    expect(core.PALETTE).toHaveLength(core.STATE.DESCRIBED_BASE + 6);
  });

  it('hatches undescribed ground the other way from no data', () => {
    const size = 256;
    const src = new Uint8ClampedArray(size * size * 4);
    // Row 0: one steep south-facing pixel per column, so every pixel is UNDESCRIBED.
    for (let i = 0; i < src.length; i += 4) {
      src[i] = 2500 >> 8;
      src[i + 1] = 2500 & 255;
      src[i + 2] = (S << 5) | 8;
      src[i + 3] = 255;
    }
    const out = new Uint8ClampedArray(src.length);
    terrain.paintTile(src, out, core.createClassifier([]), core.PALETTE, { z: 13, x: 0, y: 0 });
    const alphaAt = (px, py) => out[(py * size + px) * 4 + 3];
    // The 135° hatch: px - py ≡ 0 (mod 8) is a stroke; px + py ≡ 0 is not, off the diagonal.
    expect(alphaAt(9, 1)).toBeGreaterThan(0);
    expect(alphaAt(7, 1)).toBe(0);
  });
});
