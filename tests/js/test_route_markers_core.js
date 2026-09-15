/*
 * tests/js/test_route_markers_core.js — the start dot and finish flag on a
 * saved route (static/js/route_markers_core.js).
 *
 * Two halves, both pure arithmetic and neither reachable from map.js under
 * jsdom:
 *
 *   - the GEOMETRY: which points get a marker. The case worth writing down
 *     is the closed track — an out-and-back finishes at the car it started
 *     from, which is most ski tours, and emitting both markers there would
 *     stack a flag exactly on a dot;
 *   - the PIXELS: that a checker actually alternates. A checkerboard whose
 *     parity is wrong is still a rectangle of squares, so it fails
 *     silently — it just stops reading as a finish flag, in a way no
 *     assertion on the layer or the image id would catch.
 *
 * The fall-line arrow is here for the second reason and one sharper than
 * it: the arrow is ROTATED by a compass bearing, so it has to be drawn
 * pointing UP in the buffer. An arrow drawn pointing down is still an
 * arrow, still rotates, and is wrong by 180 degrees everywhere at once —
 * a map of fall lines pointing uphill, which nothing but a test or a
 * skier on the ground would catch.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/route_markers_core.js';

const core = self.pwaRouteMarkersCore;

/**
 * A routes FeatureCollection over the given coordinate lists.
 *
 * @param {...Array<Array<number>>} tracks One coordinate list per route.
 * @returns {object} A FeatureCollection of LineStrings.
 */
function routes(...tracks) {
  return {
    type: 'FeatureCollection',
    features: tracks.map((coordinates, i) => ({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates },
      properties: { uuid: `uuid-${i}`, name: `Route ${i}` },
    })),
  };
}

/** Read one pixel's RGBA out of a StyleImage. */
function pixel(image, x, y) {
  const offset = (y * image.width + x) * 4;
  return Array.from(image.data.slice(offset, offset + 4));
}

describe('endpointsGeojson — which points get a marker', () => {
  it('marks the first and last position of a track', () => {
    const result = core.endpointsGeojson(
      routes([[7.0, 46.0], [7.0, 46.01], [7.0, 46.02]]),
    );

    expect(result.features).toHaveLength(2);
    expect(result.features[0].properties.role).toBe('start');
    expect(result.features[0].geometry.coordinates).toEqual([7.0, 46.0]);
    expect(result.features[1].properties.role).toBe('end');
    expect(result.features[1].geometry.coordinates).toEqual([7.0, 46.02]);
  });

  it('drops the elevation ordinate a route position carries', () => {
    const result = core.endpointsGeojson(
      routes([[7.0, 46.0, 1500], [7.0, 46.01, 1900]]),
    );

    // A symbol layer has no use for a Z, and leaving it on makes the
    // point geometry disagree in shape with every other point source.
    for (const feature of result.features) {
      expect(feature.geometry.coordinates).toHaveLength(2);
    }
  });

  it('gives a closed track ONE marker — the flag', () => {
    // The out-and-back: finishes at the car it started from. Two markers
    // here would put a flag exactly on top of a dot, hiding one and
    // leaving a loop indistinguishable from a one-way route whose far end
    // is off screen.
    const result = core.endpointsGeojson(
      routes([[7.0, 46.0], [7.0, 46.02], [7.0, 46.0]]),
    );

    expect(result.features).toHaveLength(1);
    expect(result.features[0].properties.role).toBe('end');
    expect(result.features[0].geometry.coordinates).toEqual([7.0, 46.0]);
  });

  it('still gives two markers to a track that ends NEAR its start', () => {
    // The inverse, and why the closed test is exact rather than fuzzy: a
    // there-and-back finishing a few metres from its start has two ends.
    const result = core.endpointsGeojson(
      routes([[7.0, 46.0], [7.0, 46.02], [7.0, 46.0001]]),
    );

    expect(result.features.map((f) => f.properties.role)).toEqual(['start', 'end']);
  });

  it('carries the route identity onto both markers', () => {
    const result = core.endpointsGeojson(routes([[7.0, 46.0], [7.0, 46.01]]));

    for (const feature of result.features) {
      expect(feature.properties.uuid).toBe('uuid-0');
      expect(feature.properties.name).toBe('Route 0');
    }
  });

  it('handles several routes at once', () => {
    const result = core.endpointsGeojson(
      routes([[7.0, 46.0], [7.0, 46.01]], [[8.0, 47.0], [8.0, 47.01]]),
    );

    expect(result.features).toHaveLength(4);
  });

  it('skips a track too short to have direction', () => {
    expect(core.endpointsGeojson(routes([[7.0, 46.0]])).features).toEqual([]);
    expect(core.endpointsGeojson(routes([])).features).toEqual([]);
  });

  it('skips a malformed position rather than emitting NaN coordinates', () => {
    const result = core.endpointsGeojson(routes([['nope', 46.0], [7.0, 46.01]]));

    expect(result.features).toEqual([]);
  });

  it('returns an empty collection for junk input', () => {
    for (const input of [null, undefined, {}, { features: null }]) {
      expect(core.endpointsGeojson(input)).toEqual({
        type: 'FeatureCollection',
        features: [],
      });
    }
  });
});

describe('startDotPixels — the start marker', () => {
  const FUCHSIA = [192, 38, 211];

  it('is a StyleImage of the declared size', () => {
    const image = core.startDotPixels(...FUCHSIA);

    expect(image.width).toBe(core.SIZE);
    expect(image.height).toBe(core.SIZE);
    expect(image.data).toHaveLength(core.SIZE * core.SIZE * 4);
  });

  it('fills the centre with the colour it was given', () => {
    const image = core.startDotPixels(...FUCHSIA);

    expect(pixel(image, core.SIZE / 2, core.SIZE / 2)).toEqual([...FUCHSIA, 255]);
  });

  it('rings the disc in white', () => {
    const image = core.startDotPixels(...FUCHSIA);
    const centre = core.SIZE / 2;

    // Walking out from the centre: colour, then white, then transparent.
    const walk = [];
    for (let x = centre; x < core.SIZE; x += 1) walk.push(pixel(image, x, centre));

    const opaque = walk.filter((p) => p[3] === 255);
    const white = opaque.filter((p) => p[0] === 255 && p[1] === 255 && p[2] === 255);
    // Without the ring the dot dissolves into the fuchsia line it sits on.
    expect(white.length).toBeGreaterThan(0);
    expect(walk.at(-1)[3]).toBe(0);
  });

  it('leaves the corners transparent, so the marker reads as round', () => {
    const image = core.startDotPixels(...FUCHSIA);

    expect(pixel(image, 0, 0)[3]).toBe(0);
    expect(pixel(image, core.SIZE - 1, core.SIZE - 1)[3]).toBe(0);
  });
});

describe('finishFlagPixels — the finish marker', () => {
  const INK = [26, 25, 22];

  it('is a StyleImage of the declared size', () => {
    const image = core.finishFlagPixels(...INK);

    expect(image.width).toBe(core.SIZE);
    expect(image.height).toBe(core.SIZE);
  });

  it('alternates dark and light across the cloth', () => {
    const image = core.finishFlagPixels(...INK);

    // Sample the middle of each cell in the top row of the cloth. The
    // cloth starts at x = 11 (pole at 8, width 3), y = 5, cells of 6.
    const row = [];
    for (let column = 0; column < 4; column += 1) {
      row.push(pixel(image, 11 + column * 6 + 3, 5 + 3));
    }

    const isDark = row.map((p) => p[0] === INK[0]);
    // The property that makes it a checker rather than a striped
    // rectangle: every neighbour differs.
    expect(isDark).toEqual([true, false, true, false]);
  });

  it('alternates down the cloth as well as across it', () => {
    const image = core.finishFlagPixels(...INK);

    const column = [];
    for (let row = 0; row < 3; row += 1) {
      column.push(pixel(image, 11 + 3, 5 + row * 6 + 3)[0] === INK[0]);
    }

    expect(column).toEqual([true, false, true]);
  });

  it('uses white for the light cells, not a tint of the ink', () => {
    const image = core.finishFlagPixels(...INK);

    // A checkered flag is black-and-white by definition; a "checker" in
    // two brand colours stops reading as a finish line.
    expect(pixel(image, 11 + 6 + 3, 5 + 3)).toEqual([255, 255, 255, 255]);
  });

  it('draws a pole below the cloth', () => {
    const image = core.finishFlagPixels(...INK);

    // Well under the cloth (which ends at y = 23), still on the pole.
    expect(pixel(image, 9, 30)).toEqual([...INK, 255]);
  });

  it('leaves the area right of the pole below the cloth transparent', () => {
    const image = core.finishFlagPixels(...INK);

    expect(pixel(image, 30, 32)[3]).toBe(0);
  });
});

describe('fallLineArrowPixels — the fall-line mark', () => {
  /** The alpha channel of one pixel. */
  function alpha(image, x, y) {
    return pixel(image, x, y)[3];
  }

  /** The widest row's half-width, measured from the buffer's centre. */
  function coveredHalfWidth(image, y) {
    let widest = -1;
    for (let x = 0; x < image.width; x += 1) {
      if (alpha(image, x, y) > 0) widest = Math.max(widest, Math.abs(x - 19.5));
    }
    return widest;
  }

  it('points UP, so icon-rotate can turn it onto a compass bearing', () => {
    const image = core.fallLineArrowPixels();

    // The head is a triangle narrowing towards the tip, so the rows
    // near the top of the buffer are narrower than the rows below them.
    // Drawn upside down this comparison reverses, and every arrow on
    // every map would be 180 degrees wrong.
    expect(coveredHalfWidth(image, 8)).toBeLessThan(coveredHalfWidth(image, 18));
  });

  it('is widest at the head, then narrows to a shaft', () => {
    const image = core.fallLineArrowPixels();

    const head = coveredHalfWidth(image, 21);
    const shaft = coveredHalfWidth(image, 30);
    // The shaft is what makes it read as an arrow rather than a wedge
    // at 20 CSS pixels; a head with no shaft would measure the same
    // width all the way down.
    expect(head).toBeGreaterThan(shaft);
    expect(shaft).toBeGreaterThan(0);
  });

  it('is symmetric about its centre line', () => {
    const image = core.fallLineArrowPixels();

    // An asymmetric arrow reads as pointing a few degrees off the
    // bearing it was given.
    for (const y of [10, 18, 28]) {
      for (const dx of [1, 3, 5]) {
        expect(alpha(image, 19 - dx + 1, y)).toBe(alpha(image, 20 + dx - 1, y));
      }
    }
  });

  it('is an alpha mask in white, so icon-color can paint it', () => {
    const image = core.fallLineArrowPixels();

    // Registered `sdf: true`: an SDF keeps only the alpha channel, so
    // the ink has to stay a paint property. Any colour in the pixel
    // data would be discarded, and a two-tone glyph would collapse.
    expect(pixel(image, 20, 30).slice(0, 3)).toEqual([255, 255, 255]);
  });

  it('is antialiased along the head\'s sloped edge', () => {
    const image = core.fallLineArrowPixels();

    // A hard-edged slope at this size reads as a staircase. Somewhere
    // along the head's edge there is a partially-covered pixel.
    const partials = [];
    for (let y = 8; y <= 20; y += 1) {
      for (let x = 0; x < image.width; x += 1) {
        const a = alpha(image, x, y);
        if (a > 0 && a < 255) partials.push([x, y]);
      }
    }
    expect(partials.length).toBeGreaterThan(0);
  });

  it('leaves the buffer above the tip and below the tail empty', () => {
    const image = core.fallLineArrowPixels();

    // The arrow is shorter than the crux ring is wide, so the two read
    // as different marks where they co-occur — which is most passages.
    expect(alpha(image, 20, 2)).toBe(0);
    expect(alpha(image, 20, 38)).toBe(0);
  });
});
