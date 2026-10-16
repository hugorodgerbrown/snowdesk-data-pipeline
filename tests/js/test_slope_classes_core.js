/*
 * tests/js/test_slope_classes_core.js — the slope-class ramp and the
 * terrain filter's "colour by slope class" mode that paints it (SNOW-1073).
 *
 * The map's slope layer used to be swisstopo's pre-rendered raster
 * (SNOW-691). It is now painted on the device from our terrain-class tiles,
 * whose pixels carry a 5° slope band: band 6 is 30–35°, band 7 35–40° and
 * so on. An off-by-one there paints every face one class gentler or
 * steeper than the ground with no error anywhere, so each band is mapped
 * explicitly, as are the aspect and elevation masks the mode shares with
 * the match tint, level ground, the no-data hatch and the stored mode's
 * fallback.
 */

import { readFileSync } from 'node:fs';
import { beforeAll, describe, expect, it } from 'vitest';

/** @type {any} */
let classes;
/** @type {any} */
let core;

beforeAll(async () => {
  await import('../../static/js/slope_classes_core.js');
  await import('../../static/js/terrain_filter_core.js');
  classes = self.pwaSlopeClassesCore;
  core = self.pwaTerrainFilterCore;
});

/** A blue channel for an octant and band. */
const blue = (octant, band) => (octant << 5) | band;

/** A decoded data cell. */
const cell = (octant, band, height = 2500) => core.decodeCell(height >> 8, height & 255, blue(octant, band));

const TILE = { z: 13, x: 4260, y: 2911 };

/** The SLOPE_PALETTE id a classifier gives a cell. */
const idFor = (classify, c) => core.SLOPE_PALETTE[classify(c, 0, 0, TILE)].id;

describe('the class ramp', () => {
  it('lists the five painted classes, gentlest first, with no gaps', () => {
    expect(classes.CLASSES.map((c) => c.from)).toEqual([30, 35, 40, 45, 50]);
    for (let i = 0; i < classes.CLASSES.length - 1; i += 1) {
      expect(classes.CLASSES[i].to).toBe(classes.CLASSES[i + 1].from);
    }
    expect(classes.CLASSES.at(-1).to).toBeNull();
  });

  it('matches the tokens src/css/main.css declares', () => {
    const css = readFileSync('src/css/main.css', 'utf8');
    for (const klass of classes.CLASSES) {
      const match = new RegExp(`${klass.token}:\\s*(#[0-9a-fA-F]{6})`).exec(css);
      expect(match, klass.token).not.toBeNull();
      expect(match[1].toLowerCase()).toBe(klass.hex);
    }
  });

  it('keeps the SLF colours swisstopo painted with', () => {
    expect(classes.CLASSES.map((c) => c.hex)).toEqual([
      '#f2e50a', '#f46f24', '#de055b', '#c889bb', '#4b4b4b',
    ]);
  });
});

describe('SLOPE_PALETTE', () => {
  it('is transparent, the hatch, then one entry per class at 0.6', () => {
    expect(core.SLOPE_PALETTE[core.SLOPE_PALETTE_INDEX.NONE].rgba).toEqual([0, 0, 0, 0]);
    expect(core.SLOPE_PALETTE[core.SLOPE_PALETTE_INDEX.NODATA].hatch).toBe(true);
    const painted = core.SLOPE_PALETTE.slice(core.SLOPE_PALETTE_INDEX.FIRST_CLASS);
    expect(painted.map((e) => e.hex)).toEqual(classes.CLASSES.map((c) => c.hex));
    for (const entry of painted) expect(entry.rgba[3] / 255).toBeCloseTo(0.6, 2);
    expect(painted[0].rgba.slice(0, 3)).toEqual([0xf2, 0xe5, 0x0a]);
  });
});

describe('createSlopeClassifier', () => {
  it('maps each band to its class on the 30/35/40/45/50+ ramp', () => {
    const classify = core.createSlopeClassifier({});
    expect(idFor(classify, cell(0, 5))).toBe('none'); // 25–30°
    expect(idFor(classify, cell(0, 6))).toBe('slope-30');
    expect(idFor(classify, cell(0, 7))).toBe('slope-35');
    expect(idFor(classify, cell(0, 8))).toBe('slope-40');
    expect(idFor(classify, cell(0, 9))).toBe('slope-45');
    expect(idFor(classify, cell(0, 10))).toBe('slope-50');
    expect(idFor(classify, cell(0, 17))).toBe('slope-50'); // 85–90°
  });

  it('honours the slope range from the sheet', () => {
    const classify = core.createSlopeClassifier({ minSlope: 35, maxSlope: 45 });
    expect(idFor(classify, cell(0, 6))).toBe('none');
    expect(idFor(classify, cell(0, 7))).toBe('slope-35');
    expect(idFor(classify, cell(0, 8))).toBe('slope-40');
    expect(idFor(classify, cell(0, 9))).toBe('none');
  });

  it('masks by aspect', () => {
    const classify = core.createSlopeClassifier({ aspects: [0, 7] });
    expect(idFor(classify, cell(0, 8))).toBe('slope-40');
    expect(idFor(classify, cell(7, 8))).toBe('slope-40');
    expect(idFor(classify, cell(4, 8))).toBe('none');
    expect(idFor(core.createSlopeClassifier({ aspects: [] }), cell(0, 8))).toBe('none');
  });

  it('masks by elevation, edges included', () => {
    const classify = core.createSlopeClassifier({ minElevation: 2400, maxElevation: 3000 });
    expect(idFor(classify, cell(0, 8, 2399))).toBe('none');
    expect(idFor(classify, cell(0, 8, 2400))).toBe('slope-40');
    expect(idFor(classify, cell(0, 8, 3000))).toBe('slope-40');
    expect(idFor(classify, cell(0, 8, 3001))).toBe('none');
  });

  it('never paints level ground and hatches no data', () => {
    const classify = core.createSlopeClassifier({});
    expect(classify(core.decodeCell(9, 196, core.B_LEVEL), 0, 0, TILE))
      .toBe(core.SLOPE_PALETTE_INDEX.NONE);
    expect(classify(core.decodeCell(0, 0, core.B_NODATA), 0, 0, TILE))
      .toBe(core.SLOPE_PALETTE_INDEX.NODATA);
  });

  it('paints a tile in the class colour, through paintTile', () => {
    const src = new Uint8ClampedArray(256 * 256 * 4);
    for (let i = 0; i < src.length; i += 4) {
      src[i] = 9;
      src[i + 1] = 196;
      src[i + 2] = blue(2, 7);
      src[i + 3] = 255;
    }
    const out = new Uint8ClampedArray(src.length);
    core.paintTile(src, out, core.createSlopeClassifier({}), core.SLOPE_PALETTE, TILE);
    expect([...out.slice(0, 4)]).toEqual([0xf4, 0x6f, 0x24, 153]);
  });
});

describe('picking by mode', () => {
  it('pairs the slope mode with the slope classifier and palette', () => {
    const filter = { mode: 'slope' };
    expect(core.paletteFor(filter)).toBe(core.SLOPE_PALETTE);
    expect(core.SLOPE_PALETTE[core.createClassifier(filter)(cell(0, 8), 0, 0, TILE)].id).toBe('slope-40');
  });

  it('falls back to the match mode for an absent or unknown mode', () => {
    for (const filter of [{}, { mode: 'shade' }, null]) {
      expect(core.paletteFor(filter)).toBe(core.PALETTE);
      expect(core.createClassifier(filter)(cell(0, 8), 0, 0, TILE)).toBe(core.PALETTE_INDEX.MATCH);
    }
    expect(core.parseFilter('{"aspects":[0,1]}').mode).toBe('match');
  });
});
