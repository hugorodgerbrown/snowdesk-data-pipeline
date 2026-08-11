/*
 * tests/js/test_hatch_core.js — the downloaded-areas overlay's hatch pixels.
 *
 * The assertion this file exists for is SEAMLESSNESS. MapLibre repeats the
 * image across every downloaded polygon, so a stroke that does not continue
 * from one copy's right edge into the next copy's left edge shows up as a
 * grid of joins ruled over the map — at every zoom, on every area, and with
 * nothing in a console to say why. It holds only while the stroke period
 * divides the image edge, which is one modulo away from being wrong and
 * silently wrong when it is.
 *
 * Everything else here is the arithmetic that makes the mark readable over
 * the choropleth: strokes at 45°, binary alpha, and a coverage fraction low
 * enough that the danger colour underneath still reads.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

/** The alpha channel of the pixel at (x, y). */
function alphaAt(image, x, y) {
  return image.data[(y * image.width + x) * 4 + 3];
}

/** The RGB triple of the pixel at (x, y). */
function rgbAt(image, x, y) {
  const i = (y * image.width + x) * 4;
  return [image.data[i], image.data[i + 1], image.data[i + 2]];
}

beforeAll(async () => {
  await import('../../static/js/hatch_core.js');
  core = globalThis.pwaHatchCore;
});

describe('hatchPixels', () => {
  it('returns a square MapLibre StyleImage of the declared size', () => {
    const image = core.hatchPixels(1, 2, 3);

    expect(image.width).toBe(core.SIZE);
    expect(image.height).toBe(core.SIZE);
    expect(image.data).toBeInstanceOf(Uint8ClampedArray);
    expect(image.data.length).toBe(core.SIZE * core.SIZE * 4);
  });

  it('carries the caller\'s colour in every pixel, transparent ones included', () => {
    // The RGB is written everywhere rather than only under the strokes:
    // a transparent pixel with a stale colour would bleed through any
    // filtering that interpolates neighbours.
    const image = core.hatchPixels(10, 20, 30);

    for (let y = 0; y < image.height; y += 1) {
      for (let x = 0; x < image.width; x += 1) {
        expect(rgbAt(image, x, y)).toEqual([10, 20, 30]);
      }
    }
  });

  it('draws strokes at 45°, so a step down-left stays on the same one', () => {
    const image = core.hatchPixels(0, 0, 0);

    // x + y is what the stroke is keyed on, so (x+1, y-1) is the same
    // stroke and (x+1, y) is one pixel across it.
    for (let y = 1; y < image.height; y += 1) {
      for (let x = 0; x < image.width - 1; x += 1) {
        expect(alphaAt(image, x + 1, y - 1)).toBe(alphaAt(image, x, y));
      }
    }
  });

  it('uses binary alpha — fill-opacity is the only strength knob', () => {
    const image = core.hatchPixels(0, 0, 0);

    for (let i = 3; i < image.data.length; i += 4) {
      expect([0, 255]).toContain(image.data[i]);
    }
  });

  it('leaves most of the area showing the layer underneath', () => {
    const image = core.hatchPixels(0, 0, 0);

    let opaque = 0;
    for (let i = 3; i < image.data.length; i += 4) {
      if (image.data[i] === 255) opaque += 1;
    }
    const covered = opaque / (image.width * image.height);

    expect(covered).toBeCloseTo(core.WIDTH / core.PERIOD, 5);
    // A hatch that covered half the polygon would be a tint by another
    // name, and the danger colour under it would shift.
    expect(covered).toBeLessThan(0.5);
  });

  it('tiles seamlessly in both directions', () => {
    // The real assertion: walking off the right edge must continue into
    // the left edge of the next copy, and the same downwards. Stated as
    // "the pixel one period in is the pixel at the edge", which is what
    // repeating the image actually does.
    const image = core.hatchPixels(0, 0, 0);

    for (let y = 0; y < image.height; y += 1) {
      expect(alphaAt(image, 0, y)).toBe(alphaAt(image, image.width - core.PERIOD, y));
    }
    for (let x = 0; x < image.width; x += 1) {
      expect(alphaAt(image, x, 0)).toBe(alphaAt(image, x, image.height - core.PERIOD));
    }
  });
});

describe('tilesSeamlessly', () => {
  it('holds for the shipped constants', () => {
    expect(core.tilesSeamlessly(core.SIZE, core.PERIOD)).toBe(true);
  });

  it('fails a period that does not divide the edge', () => {
    expect(core.tilesSeamlessly(12, 5)).toBe(false);
    expect(core.tilesSeamlessly(12, 7)).toBe(false);
  });

  it('fails a period of zero rather than dividing by it', () => {
    expect(core.tilesSeamlessly(12, 0)).toBe(false);
  });
});
