/*
 * tests/js/test_slope_overlay_core.js — the slope overlay's coverage
 * rectangle and class table (SNOW-691).
 *
 * The rectangle is the whole ticket in one constant. It decides which tiles
 * are requested at all (outside it the service answers HTTP 400 with a JSON
 * body, so an over-wide box spends requests on errors), where the boundary
 * line is drawn, and when the layers-menu row disables itself — and the
 * reason the last two exist is the safety line: unshaded ground inside the
 * box means "under 30°", unshaded ground outside it means "not surveyed",
 * and the raster cannot tell those apart on its own.
 *
 * So the cases below are real places, checked against the box during
 * planning: Zermatt inside; the Zillertal and the Mercantour outside,
 * which is why SNOW-693 (an Alps-wide raster we build ourselves) exists.
 * A transposed or inverted comparison would still pass a test written
 * against invented coordinates near the middle.
 */

import { beforeAll, describe, expect, it } from 'vitest';

/** @type {any} */
let core;

beforeAll(async () => {
  await import('../../static/js/slope_overlay_core.js');
  core = self.pwaSlopeOverlayCore;
});

describe('coverage bounds', () => {
  it('is the capabilities document\'s WGS84BoundingBox, in MapLibre order', () => {
    // [west, south, east, north] — the order a MapLibre source's `bounds`
    // takes, which is NOT the order the capabilities document states the
    // corners in (lower corner "5.140242 45.398181" is lng then lat).
    expect(core.COVERAGE_BOUNDS).toEqual([5.140242, 45.398181, 11.47757, 48.230651]);
  });

  it('stops at z16, where the pyramid stops adding detail', () => {
    // The service advertises z0-17, but z17 is a server-side upscale of
    // z16: a z16 tile upscaled 2x differs from its four native z17 children
    // in 1.3% of pixels, against 2.7% for z15->z16 and 7.0% for z14->z15.
    // Capping here costs nothing visible and saves four requests per tile
    // of screen at the zooms people actually tour at.
    //
    // The value must stay BELOW the service's 18 (which answers HTTP 400)
    // whatever else changes — MapLibre defaults a raster maxzoom to 22, and
    // that default is what SNOW-604 blanked the basemap with.
    expect(core.MIN_ZOOM).toBe(0);
    expect(core.MAX_ZOOM).toBe(16);
    expect(core.MAX_ZOOM).toBeLessThan(18);
  });
});

describe('coversPoint', () => {
  it('covers places inside the rectangle', () => {
    const inside = [
      [7.7491, 46.0207],   // Zermatt
      [9.8355, 46.4908],   // Davos
      [6.8652, 45.9237],   // Chamonix
      [11.3548, 47.2692],  // Innsbruck
    ];
    for (const [lng, lat] of inside) {
      expect(core.coversPoint(lng, lat)).toBe(true);
    }
  });

  it('does not cover the places the rectangle misses', () => {
    // Every one of these was probed during planning and found blank. They
    // are outside the box, not a fade at its edge — which is the fact
    // SNOW-693 is justified by.
    const outside = [
      [11.8672, 47.1731],  // Mayrhofen, Zillertal — just past the east edge
      [11.7994, 46.5405],  // Cortina d'Ampezzo, the Dolomites
      [7.4155, 44.1234],   // Mercantour — south of the box
      [6.6836, 45.1885],   // Modane, southern Écrins-ish — also south
    ];
    for (const [lng, lat] of outside) {
      expect(core.coversPoint(lng, lat)).toBe(false);
    }
  });

  it('includes each edge and excludes the point just beyond it', () => {
    const [west, south, east, north] = core.COVERAGE_BOUNDS;
    const midLng = (west + east) / 2;
    const midLat = (south + north) / 2;
    const nudge = 0.000001;

    expect(core.coversPoint(west, midLat)).toBe(true);
    expect(core.coversPoint(west - nudge, midLat)).toBe(false);
    expect(core.coversPoint(east, midLat)).toBe(true);
    expect(core.coversPoint(east + nudge, midLat)).toBe(false);
    expect(core.coversPoint(midLng, south)).toBe(true);
    expect(core.coversPoint(midLng, south - nudge)).toBe(false);
    expect(core.coversPoint(midLng, north)).toBe(true);
    expect(core.coversPoint(midLng, north + nudge)).toBe(false);

    // And the corners, which is where a transposed comparison survives an
    // edge-by-edge check.
    expect(core.coversPoint(west, south)).toBe(true);
    expect(core.coversPoint(east, north)).toBe(true);
  });

  it('answers false for a missing or non-finite coordinate', () => {
    // The callers are a `moveend` handler and a source guard; neither has
    // anywhere useful to put an exception.
    expect(core.coversPoint(NaN, 46)).toBe(false);
    expect(core.coversPoint(7.7, undefined)).toBe(false);
    expect(core.coversPoint(Infinity, Infinity)).toBe(false);
  });
});

describe('the class table', () => {
  it('lists the five painted classes, weakest first, with no gaps', () => {
    expect(core.CLASSES.map((c) => c.from)).toEqual([30, 35, 40, 45, 50]);
    // Each class ends where the next begins; the last is open-ended.
    for (let i = 0; i < core.CLASSES.length - 1; i += 1) {
      expect(core.CLASSES[i].to).toBe(core.CLASSES[i + 1].from);
    }
    expect(core.CLASSES[core.CLASSES.length - 1].to).toBeNull();
  });

  it('starts at 30°, because everything below it is unpainted', () => {
    // The layer is `hangneigung-ueber_30` and the name is literal: under
    // 30° the raster is fully transparent, so a sixth "0–30°" entry would
    // claim a colour the tiles never carry.
    expect(core.CLASSES[0].from).toBe(30);
  });

  it('names a design token per class, matching the hex it records', () => {
    // The legend swatches are painted from the tokens; the hexes are what
    // src/css/main.css declares them as. Keeping both here lets a drift
    // between the two fail somewhere.
    const expected = {
      '--color-slope-30': '#f2e50a',
      '--color-slope-35': '#f46f24',
      '--color-slope-40': '#de055b',
      '--color-slope-45': '#c889bb',
      '--color-slope-50': '#4b4b4b',
    };
    for (const klass of core.CLASSES) {
      expect(expected[klass.token]).toBe(klass.hex);
    }
    expect(new Set(core.CLASSES.map((c) => c.token)).size).toBe(core.CLASSES.length);
  });
});
