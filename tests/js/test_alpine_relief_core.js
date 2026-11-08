/*
 * tests/js/test_alpine_relief_core.js — our relief past swisstopo's detail
 * (SNOW-1460, static/js/alpine_relief_core.js).
 *
 * What is pinned: Terrarium decoding; slope and aspect from the heights;
 * the rock tone only above 40°, darker on the shadow side; the hillshade
 * shadowing a slope facing away from the light and nothing on the flat;
 * the erase mask over the detailed-relief outline, holes honoured; a
 * painted tile erased inside it; and the tuning: its defaults, its
 * clamping and key, and each strength and the rock slope changing the paint.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/swisstopo_extent.js';
import '../../static/js/alpine_relief_core.js';

const core = window.pwaAlpineReliefCore;
const { DETAILED_RELIEF } = window.pwaSwisstopoExtent;

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

/** Terrarium RGB for a height. */
function encode(height) {
  const v = height + 32768;
  const r = Math.floor(v / 256);
  const g = Math.floor(v - r * 256);
  const b = Math.round((v - Math.floor(v)) * 256);
  return [r, g, b];
}

/** A DEM tile's RGBA from a height function of (col, row). */
function dem(size, heightAt) {
  const out = new Uint8ClampedArray(size * size * 4);
  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      const [r, g, b] = encode(heightAt(col, row));
      out.set([r, g, b, 255], (row * size + col) * 4);
    }
  }
  return out;
}

/** A plane rising `degrees` towards a compass bearing, sampled at a pixel. */
function plane(degrees, risesTowards, size = 3) {
  const grade = Math.tan((degrees * Math.PI) / 180);
  const rad = (risesTowards * Math.PI) / 180;
  const heights = new Float32Array(size * size);
  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      // east = col, north = -row, one metre per pixel.
      heights[row * size + col] = 1000 + grade * (col * Math.sin(rad) - row * Math.cos(rad));
    }
  }
  return core.slopeAspect(heights, size, 1, 1, 1);
}

describe('terrarium', () => {
  it('decodes a height', () => {
    expect(core.terrarium(...encode(4478))).toBeCloseTo(4478, 2);
    expect(core.terrarium(128, 0, 0)).toBe(0);
  });
});

describe('slopeAspect', () => {
  it('reads a plane\'s slope, and the way it faces downhill', () => {
    const north = plane(30, 0);
    expect(north.slope).toBeCloseTo(30, 2);
    // Rising to the north, it faces south.
    expect(north.aspect).toBeCloseTo(180, 2);
    expect(plane(45, 90).aspect).toBeCloseTo(270, 2);
  });

  it('reads the flat as flat', () => {
    expect(plane(0, 0).slope).toBe(0);
  });
});

describe('the rock tone', () => {
  it('starts above 40°', () => {
    expect(core.rockPixel(39, 315)).toBeNull();
    expect(core.rockPixel(40, 315)).toBeNull();
    expect(core.rockPixel(41, 315)).not.toBeNull();
  });

  it('is darker on the shadow side', () => {
    const lit = core.rockPixel(50, 315);
    const shadow = core.rockPixel(50, 135);
    const lum = ({ rgb }) => rgb[0] + rgb[1] + rgb[2];
    expect(lum(shadow)).toBeLessThan(lum(lit));
    expect(shadow.rgb[0]).toBe(shadow.rgb[1]);
    expect(shadow.rgb[1]).toBe(shadow.rgb[2]);
  });
});

describe('the hillshade', () => {
  it('is nothing on the flat', () => {
    expect(core.hillshadePixel(0, 0)).toBeNull();
  });

  it('shadows a slope facing away from the light, and lights one facing it', () => {
    const away = core.hillshadePixel(30, 135);
    const facing = core.hillshadePixel(30, 315);
    expect(away.rgb[0]).toBeLessThan(128);
    expect(facing.rgb[0]).toBe(255);
    expect(core.shade(30, 135)).toBeLessThan(core.shade(0, 0));
    expect(core.shade(30, 315)).toBeGreaterThan(core.shade(0, 0));
  });
});

describe('eraseMask', () => {
  it('erases a tile inside the detailed relief and none of one outside', () => {
    const inside = core.eraseMask(DETAILED_RELIEF, tileAt(7.75, 46.02, 12), 64);
    const outside = core.eraseMask(DETAILED_RELIEF, tileAt(11.39, 47.27, 12), 64);
    expect(inside.every((v) => v === 1)).toBe(true);
    expect(outside.every((v) => v === 0)).toBe(true);
  });

  it('erases part of a tile across its edge', () => {
    // The z8 tile from 9.84°E to 11.25°E holds the east edge, about
    // 10.55°E at 46.9°N.
    const mask = core.eraseMask(DETAILED_RELIEF, tileAt(10.4, 46.9, 8), 64);
    const erased = mask.reduce((sum, v) => sum + v, 0);
    expect(erased).toBeGreaterThan(0);
    expect(erased).toBeLessThan(mask.length);
    // West of the edge is erased, east of it is not.
    expect(mask[32 * 64]).toBe(1);
    expect(mask[32 * 64 + 63]).toBe(0);
  });

  it('honours a hole', () => {
    const square = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]].map(([a, b]) => [a * 10, 40 + b * 10]);
    const hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]].map(([a, b]) => [a, 40 + b]);
    const tile = tileAt(5, 45, 4);
    const mask = core.eraseMask([[square, hole]], tile, 64);
    const point = (lng, lat) => {
      const n = 2 ** tile.z;
      const sin = Math.sin((lat * Math.PI) / 180);
      const col = Math.floor((((lng + 180) / 360) * n - tile.x) * 64);
      const row = Math.floor(((0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * n - tile.y) * 64);
      return mask[row * 64 + col];
    };
    expect(point(5, 45)).toBe(0);
    expect(point(2, 42)).toBe(1);
  });
});

describe('paint', () => {
  it('paints rock on a steep slope outside the detailed relief', () => {
    const tile = tileAt(11.39, 47.27, 13);
    const cell = core.metresPerPixel(tile, 16);
    // A 60° face rising to the east: it faces west, towards the light's side.
    const rgba = core.paint(dem(16, (col) => 2000 + col * cell * Math.tan(Math.PI / 3)), 16, tile, DETAILED_RELIEF);
    const centre = (8 * 16 + 8) * 4;
    expect(rgba[centre + 3]).toBeGreaterThan(0);
    expect(rgba[centre]).toBe(rgba[centre + 1]);
  });

  it('paints nothing on the flat, and erases inside the detailed relief', () => {
    const flat = core.paint(dem(16, () => 500), 16, tileAt(11.39, 47.27, 13), DETAILED_RELIEF);
    expect(flat.every((v) => v === 0)).toBe(true);
    const tile = tileAt(7.75, 46.02, 13);
    const cell = core.metresPerPixel(tile, 16);
    const steep = core.paint(dem(16, (col) => 2000 + col * cell * 2), 16, tile, DETAILED_RELIEF);
    expect(steep.every((v) => v === 0)).toBe(true);
  });
});

describe('the tuning', () => {
  it('defaults to a lighter relief than SNOW-1460 shipped', () => {
    expect(core.TUNING_DEFAULTS).toEqual({
      rockSlope: 40, rockStrength: 0.42, shadowStrength: 0.36, highlightStrength: 0.24,
    });
    expect(Object.isFrozen(core.TUNING_DEFAULTS)).toBe(true);
    expect(core.TUNING_DEFAULTS.rockSlope).toBe(core.ROCK_SLOPE_DEGREES);
  });

  it('fills the defaults, clamps each value and ignores junk', () => {
    expect(core.normaliseTuning()).toEqual(core.TUNING_DEFAULTS);
    expect(core.normaliseTuning('junk')).toEqual(core.TUNING_DEFAULTS);
    expect(core.normaliseTuning({ rockSlope: 10, rockStrength: 2, shadowStrength: -1, highlightStrength: 'x' }))
      .toEqual({ rockSlope: 25, rockStrength: 1, shadowStrength: 0, highlightStrength: 0.24 });
    expect(core.normaliseTuning({ rockSlope: 90, shadowStrength: NaN, highlightStrength: '0.5' }))
      .toEqual({ rockSlope: 60, rockStrength: 0.42, shadowStrength: 0.36, highlightStrength: 0.5 });
  });

  it('names a tuning the same way for the same values, and differently for others', () => {
    expect(core.tuningKey()).toBe('40-42-36-24');
    expect(core.tuningKey(core.TUNING_DEFAULTS)).toBe(core.tuningKey({}));
    expect(core.tuningKey({ rockStrength: 0.3 })).toBe('40-30-36-24');
    expect(core.tuningKey({ rockSlope: 35 })).not.toBe(core.tuningKey());
  });

  it('paints a weaker rock tone for a lower rockStrength', () => {
    const lit = core.rockPixel(50, 300);
    const weaker = core.rockPixel(50, 300, core.normaliseTuning({ rockStrength: 0.2 }));
    expect(lit.alpha).toBe(Math.round(0.42 * 255));
    expect(weaker.alpha).toBe(Math.round(0.2 * 255));
    expect(core.rockPixel(50, 300, core.normaliseTuning({ rockStrength: 0 }))).toBeNull();
  });

  it('starts the rock at the tuning\'s slope', () => {
    expect(core.rockPixel(38, 300)).toBeNull();
    expect(core.rockPixel(38, 300, core.normaliseTuning({ rockSlope: 30 }))).not.toBeNull();
    expect(core.rockPixel(45, 300, core.normaliseTuning({ rockSlope: 50 }))).toBeNull();
  });

  it('scales the shadow and the highlight', () => {
    const shadow = core.hillshadePixel(30, 135);
    const softer = core.hillshadePixel(30, 135, core.normaliseTuning({ shadowStrength: 0.18 }));
    expect(softer.alpha).toBeLessThan(shadow.alpha);
    const highlight = core.hillshadePixel(30, 315);
    expect(core.hillshadePixel(30, 315, core.normaliseTuning({ highlightStrength: 0 }))).toBeNull();
    expect(highlight.alpha).toBeGreaterThan(0);
  });

  it('paints a whole tile with the tuning it is given', () => {
    const tile = tileAt(11.39, 47.27, 13);
    const cell = core.metresPerPixel(tile, 16);
    const steep = dem(16, (col) => 2000 + col * cell * Math.tan(Math.PI / 3));
    const centre = (8 * 16 + 8) * 4 + 3;
    const plain = core.paint(steep, 16, tile, DETAILED_RELIEF);
    const light = core.paint(steep, 16, tile, DETAILED_RELIEF, { rockStrength: 0.1, highlightStrength: 0.05 });
    expect(light[centre]).toBeLessThan(plain[centre]);
    // A 50° face is rock from 40°, not from 55°: then only its highlight
    // is left, and with none, nothing.
    const fifty = dem(16, (col) => 2000 + col * cell * Math.tan((50 * Math.PI) / 180));
    expect(core.paint(fifty, 16, tile, DETAILED_RELIEF, { highlightStrength: 0 })[centre]).toBeGreaterThan(0);
    expect(core.paint(fifty, 16, tile, DETAILED_RELIEF, { rockSlope: 55, highlightStrength: 0 })[centre]).toBe(0);
  });
});
