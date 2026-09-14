/*
 * tests/js/test_route_slope_core.js — colouring a route by the ground it
 * crosses (SNOW-910).
 *
 * Two things here are worth a test and one of them is a safety claim.
 *
 * THE BOUNDARIES. `from` is inclusive and `to` is exclusive, so exactly
 * 35° is in the 35–40 band and not in the 30–35 one. Get that the other
 * way round and every boundary sample on a route reads one band GENTLER
 * than the ground is — which is the direction that matters and the
 * direction nothing on screen would reveal.
 *
 * THE THREE STATES. Never sampled, sampled-with-an-answer, and
 * sampled-without-one are different facts, and the whole point of the
 * server keeping a reason rather than a null is that they stay apart. A
 * route with no `slope` property must produce NOTHING — not an unknown
 * line, because "nobody has looked" is not "we looked and could not tell"
 * — and a null angle must produce an unknown feature that NEVER carries a
 * class a step expression could paint green.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

/** Build a route feature carrying a slope record with the given angles. */
function sampled(angles, identity = { uuid: 'r1' }) {
  const points = [];
  for (let i = 0; i <= angles.length; i += 1) points.push([7.4 + i / 1000, 46.1]);
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: points },
    properties: Object.assign({ name: 'Tour', slope: { points, angles } }, identity),
  };
}

beforeAll(async () => {
  await import('../../static/js/route_slope_core.js');
  core = globalThis.pwaRouteSlopeCore;
});

describe('CLASSES', () => {
  it('is six buckets, gentlest first, with no gap between them', () => {
    expect(core.CLASSES).toHaveLength(6);
    for (let i = 1; i < core.CLASSES.length; i += 1) {
      expect(core.CLASSES[i].from).toBe(core.CLASSES[i - 1].to);
    }
  });

  it('carries the slope raster palette verbatim for the five steep bands', () => {
    // The line and the shading under it must be the same colour — these
    // are slope_overlay_core.js's CLASSES, value for value.
    expect(core.CLASSES.slice(1).map((c) => c.hex)).toEqual([
      '#f2e50a', '#f46f24', '#de055b', '#c889bb', '#4b4b4b',
    ]);
  });

  it('names a --color-slope-* token for every bucket', () => {
    for (const bucket of core.CLASSES) {
      expect(bucket.token).toMatch(/^--color-slope-/);
    }
  });

  it('ends open, so there is no angle it cannot classify', () => {
    expect(core.CLASSES[core.CLASSES.length - 1].to).toBeNull();
  });

  it('keeps the unknown colour off the scale entirely', () => {
    // "We do not know" must not be readable as a step on the steepness
    // scale, least of all as the gentle one.
    expect(core.CLASSES.map((c) => c.hex)).not.toContain(core.UNKNOWN_COLOUR);
  });
});

describe('classify', () => {
  it('puts a boundary sample in the band it opens, not the one it closes', () => {
    expect(core.classify(30)).toBe(1);
    expect(core.classify(35)).toBe(2);
    expect(core.classify(40)).toBe(3);
    expect(core.classify(45)).toBe(4);
    expect(core.classify(50)).toBe(5);
  });

  it('puts a sample just under a boundary in the gentler band', () => {
    expect(core.classify(29.9)).toBe(0);
    expect(core.classify(34.9)).toBe(1);
    expect(core.classify(49.9)).toBe(4);
  });

  it('has a gentle bucket, where the raster paints nothing', () => {
    expect(core.classify(0)).toBe(0);
    expect(core.classify(12.5)).toBe(0);
  });

  it('has no ceiling', () => {
    expect(core.classify(78)).toBe(5);
    expect(core.classify(90)).toBe(5);
  });

  it('answers null for an unknown rather than a class', () => {
    expect(core.classify(null)).toBeNull();
    expect(core.classify(undefined)).toBeNull();
  });

  it('answers null for a value that is not a number', () => {
    expect(core.classify(NaN)).toBeNull();
    expect(core.classify(Infinity)).toBeNull();
    expect(core.classify('35')).toBeNull();
  });
});

describe('segmentFeatures', () => {
  it('pairs N + 1 points into N two-point LineStrings', () => {
    const features = core.segmentFeatures(sampled([31, 36, 41]));

    expect(features).toHaveLength(3);
    for (const feature of features) {
      expect(feature.geometry.type).toBe('LineString');
      expect(feature.geometry.coordinates).toHaveLength(2);
    }
  });

  it('shares each boundary between the two segments it joins', () => {
    const features = core.segmentFeatures(sampled([31, 36]));

    expect(features[0].geometry.coordinates[1])
      .toEqual(features[1].geometry.coordinates[0]);
  });

  it('gives each segment its own class', () => {
    const features = core.segmentFeatures(sampled([10, 31, 52]));

    expect(features.map((f) => f.properties.slope_class)).toEqual([0, 1, 5]);
  });

  it('makes a null angle an unknown feature that carries no class', () => {
    const features = core.segmentFeatures(sampled([31, null]));

    expect(features[1].properties.unknown).toBe(true);
    expect(features[1].properties).not.toHaveProperty('slope_class');
  });

  it('never marks a classified segment unknown', () => {
    const features = core.segmentFeatures(sampled([31, null]));

    expect(features[0].properties.slope_class).toBe(1);
    expect(features[0].properties).not.toHaveProperty('unknown');
  });

  it('produces nothing at all for a route that has never been sampled', () => {
    // An unsampled route is not an unknown one. It keeps the flat line it
    // has always had; a dashed "we could not tell" would be a claim
    // nothing has earned.
    const route = { type: 'Feature', properties: { uuid: 'r1', name: 'Tour' } };

    expect(core.segmentFeatures(route)).toEqual([]);
  });

  it('produces nothing for an empty slope record', () => {
    const route = { type: 'Feature', properties: { slope: { points: [], angles: [] } } };

    expect(core.segmentFeatures(route)).toEqual([]);
  });

  it('produces nothing when the halves do not pair up', () => {
    // Segments drawn against the wrong ground is worse than no colouring.
    const route = {
      type: 'Feature',
      properties: { slope: { points: [[7.4, 46.1]], angles: [31, 36] } },
    };

    expect(core.segmentFeatures(route)).toEqual([]);
  });

  it('carries an owned route\'s uuid onto every segment', () => {
    // These layers are what a tap on a sampled route lands on, so map.js
    // has to get from a segment back to the route it belongs to.
    const features = core.segmentFeatures(sampled([31, 36], { uuid: 'abc' }));

    expect(features.every((f) => f.properties.uuid === 'abc')).toBe(true);
  });

  it('produces nothing for a pending route, sampled or not', () => {
    // A followed share's teal dash says "this one is not yours yet",
    // which is the only action it offers. Recolouring it by steepness
    // would spend the one line on a second message.
    const features = core.segmentFeatures(
      sampled([31, 36], { token: 'tok123', pending: true }),
    );

    expect(features).toEqual([]);
  });

  it('never puts a share token on a segment', () => {
    // The corollary of the rule above: these layers hand their properties
    // to a popup built for owners, so a non-owner's identifier must never
    // reach one.
    const features = core.segmentFeatures(sampled([31], { token: 'tok123' }));

    for (const feature of features) {
      expect(feature.properties).not.toHaveProperty('token');
    }
  });

  it('tolerates a null feature', () => {
    expect(core.segmentFeatures(null)).toEqual([]);
  });
});

describe('segmentCollection', () => {
  it('flattens every sampled route into one collection', () => {
    const collection = core.segmentCollection({
      type: 'FeatureCollection',
      features: [sampled([31, 36]), sampled([41], { uuid: 'r2' })],
    });

    expect(collection.type).toBe('FeatureCollection');
    expect(collection.features).toHaveLength(3);
  });

  it('skips the unsampled routes without dropping the sampled ones', () => {
    const collection = core.segmentCollection({
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', properties: { uuid: 'flat' } },
        sampled([41], { uuid: 'r2' }),
      ],
    });

    expect(collection.features).toHaveLength(1);
    expect(collection.features[0].properties.uuid).toBe('r2');
  });

  it('skips the pending routes, which keep their own line', () => {
    const collection = core.segmentCollection({
      type: 'FeatureCollection',
      features: [
        sampled([31], { token: 'tok', pending: true }),
        sampled([41], { uuid: 'r2' }),
      ],
    });

    expect(collection.features).toHaveLength(1);
    expect(collection.features[0].properties.uuid).toBe('r2');
  });

  it('is a valid empty collection when nothing has been sampled', () => {
    // setData throws on a null, so this must never be one.
    expect(core.segmentCollection(null)).toEqual({
      type: 'FeatureCollection',
      features: [],
    });
  });
});

/*
 * summaryLines — the same record in words (SNOW-961).
 *
 * The claim worth testing is the one about silence. A route the terrain
 * could not answer for must say SO, and a route nobody has sampled must
 * say NOTHING: the first is a coverage gap the reader has to know about,
 * the second is a line that has simply not been looked at yet, and
 * collapsing them would either invent a gap or hide one. Neither may
 * report a zero — "0m over 30°" is a claim that ground is gentle, made
 * about ground nothing measured.
 */
describe('summaryLines', () => {
  it('states the steepest angle, the steep length and the gap', () => {
    const lines = core.summaryLines({
      sampled_m: 10000,
      surveyed_m: 8000,
      steep_m: 2500,
      steepest_deg: 43.4,
      bands: { 'slope-gentle': 5500, 'slope-40': 2500 },
    });

    expect(lines).toEqual([
      { key: 'route-terrain-steepest', params: { deg: '43' } },
      { key: 'route-terrain-steep-km', params: { km: '2.5', deg: '30' } },
      { key: 'route-terrain-unsurveyed-km', params: { km: '2.0' } },
    ]);
  });

  it('writes a short length in metres rather than as a fraction of a km', () => {
    const lines = core.summaryLines({
      sampled_m: 5000,
      surveyed_m: 5000,
      steep_m: 80,
      steepest_deg: 33,
      bands: { 'slope-30': 80 },
    });

    expect(lines[1]).toEqual({
      key: 'route-terrain-steep-m',
      params: { m: '80', deg: '30' },
    });
  });

  it('says a wholly unsurveyed route is unsurveyed, and nothing else', () => {
    const lines = core.summaryLines({
      sampled_m: 9000,
      surveyed_m: 0,
      steep_m: 0,
      bands: {},
    });

    // One line. No steepest (there is none), and no "0m over 30°", which
    // would read as a claim that the ground is gentle.
    expect(lines).toEqual([{ key: 'route-terrain-unsurveyed-all', params: {} }]);
  });

  it('says nothing at all about a route that has never been sampled', () => {
    expect(core.summaryLines(null)).toEqual([]);
    expect(core.summaryLines(undefined)).toEqual([]);
    // A record that walked nothing is the same silence: there is no
    // coverage gap to report because there was no walk.
    expect(core.summaryLines({ sampled_m: 0, surveyed_m: 0, bands: {} })).toEqual([]);
  });

  it('omits the steep line when no surveyed ground reaches the threshold', () => {
    const lines = core.summaryLines({
      sampled_m: 4000,
      surveyed_m: 4000,
      steep_m: 0,
      steepest_deg: 22,
      bands: { 'slope-gentle': 4000 },
    });

    expect(lines).toEqual([
      { key: 'route-terrain-steepest', params: { deg: '22' } },
    ]);
  });

  it('reports a gap as short as one unknown segment', () => {
    // The two figures are summed from the same lengths, so the shortfall
    // is EXACTLY the unknown segments' length — about one stride for a
    // single one. Suppressing that would hide a real hole in the coverage
    // on a short track, which is the track most likely to have one.
    const lines = core.summaryLines({
      sampled_m: 1025,
      surveyed_m: 1000,
      steep_m: 0,
      steepest_deg: 12,
      bands: { 'slope-gentle': 1000 },
    });

    expect(lines).toContainEqual({
      key: 'route-terrain-unsurveyed-m',
      params: { m: '25' },
    });
  });

  it('does not report a tenth of a metre of float residue as a gap', () => {
    // The only way a wholly-surveyed track differs from itself: the two
    // figures are rounded independently.
    const lines = core.summaryLines({
      sampled_m: 10000.1,
      surveyed_m: 10000,
      steep_m: 0,
      steepest_deg: 12,
      bands: { 'slope-gentle': 10000 },
    });

    expect(lines.map((line) => line.key)).not.toContain('route-terrain-unsurveyed-km');
    expect(lines.map((line) => line.key)).not.toContain('route-terrain-unsurveyed-m');
  });
});
