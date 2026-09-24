/*
 * tests/js/test_route_rail_core.js — rail one's pure half
 * (static/js/route_rail_core.js, SNOW-1018).
 *
 * The tick step at the three spans the ticket names (500 m, 12.9 km,
 * 80 km), one unit per strip, one fill per leg carrying the leg's own
 * direction, and the figures formatter giving a route and a leg the same
 * shape — the property that lets rail two (SNOW-1017) reuse it unchanged.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_rail_core.js';

const core = self.pwaRouteRailCore;
const { readProfile } = self.pwaElevationProfileCore;

/**
 * A straight west-east track of `count` points, ~7.7 m apart, climbing
 * then descending so both leg directions have ground to draw.
 *
 * @param {number} count
 * @returns {Array<Array<number>>}
 */
function track(count) {
  const half = Math.floor(count / 2);
  return Array.from({ length: count }, (_, i) => [
    7.4 + i / 10000,
    46.1,
    i <= half ? 1500 + i * 5 : 1500 + half * 5 - (i - half) * 5,
  ]);
}

describe('niceStep', () => {
  it.each([
    [500, 50],
    [12900, 1000],
    [80000, 5000],
  ])('picks %d m → a %d m step', (span, step) => {
    expect(core.niceStep(span)).toBe(step);
  });

  it.each([500, 12900, 80000, 3000, 42000])(
    'leaves at most 15 minors strictly inside a %d m strip',
    (span) => {
      const interior = core.ticks(span).filter((t) => t.d > 0 && t.d < span);
      expect(interior.length).toBeLessThanOrEqual(15);
    },
  );

  it('takes the smallest step for a very short strip', () => {
    expect(core.niceStep(120)).toBe(25);
  });

  it('takes the largest step when nothing leaves 15 minors', () => {
    expect(core.niceStep(400000)).toBe(5000);
  });
});

describe('ticks', () => {
  it('labels every major in one unit for the whole strip', () => {
    const labels = core.ticks(12900).filter((t) => t.major).map((t) => t.label);
    expect(labels).toEqual(['0 km', '5 km', '10 km']);
  });

  it('writes a sub-kilometre strip in metres throughout', () => {
    const labels = core.ticks(500).filter((t) => t.major).map((t) => t.label);
    expect(labels).toEqual(['0 m', '100 m', '200 m', '300 m', '400 m', '500 m']);
    expect(core.tickUnit(500)).toBe('m');
  });

  it('never mixes units on one strip', () => {
    for (const span of [500, 1800, 12900, 80000]) {
      const labels = core.ticks(span).filter((t) => t.major).map((t) => t.label);
      const units = new Set(labels.map((label) => label.split(' ')[1]));
      expect(units.size).toBe(1);
    }
  });

  it('leaves minors unlabelled', () => {
    expect(core.ticks(12900).filter((t) => !t.major).every((t) => t.label === null)).toBe(
      true,
    );
  });

  it('takes the unit templates it is given', () => {
    const labels = core
      .ticks(12900, { km: '%(value)s km' })
      .filter((t) => t.major)
      .map((t) => t.label);
    expect(labels[1]).toBe('5 km');
  });
});

describe('formatFigures', () => {
  const full = {
    distance_m: 12900,
    ascent_m: 1234.4,
    descent_m: 1100,
    elevation_start: 1820,
    elevation_end: 2410,
  };

  it('writes distance · ascent · descent · start→end', () => {
    expect(core.formatFigures(full)).toBe('12.9 km · ▲1234 m · ▼1100 m · 1820→2410 m');
  });

  it('gives a route and a leg the identical shape', () => {
    const route = core.formatFigures(full);
    const leg = core.formatFigures({
      distance_m: 3200,
      ascent_m: 640,
      descent_m: 12,
      elevation_start: 1820,
      elevation_end: 2448,
    });
    const shape = (line) => line.replace(/[\d.]+/g, '#');
    expect(shape(leg)).toBe(shape(route));
  });

  it('omits a null figure rather than showing zero', () => {
    expect(
      core.formatFigures({ distance_m: 5000, ascent_m: null, descent_m: null }),
    ).toBe('5.0 km');
  });

  it('keeps a genuine zero', () => {
    expect(core.formatFigures({ distance_m: 1000, ascent_m: 0 })).toBe('1.0 km · ▲0 m');
  });

  it('drops the range when either end is unknown', () => {
    expect(core.formatFigures({ elevation_start: 1800, elevation_end: null })).toBe('');
  });
});

describe('legPaths', () => {
  const profile = readProfile(track(81));
  const legs = [
    { i: 1, from: 0, to: 11, climbing: true },
    { i: 2, from: 12, to: 23, climbing: false },
  ];

  it('draws one closed path per leg, and one outline', () => {
    const paths = core.legPaths(profile, legs, 24);
    expect(paths.legs).toHaveLength(2);
    for (const fill of paths.legs) expect(fill.d.endsWith('Z')).toBe(true);
    expect(paths.outline.startsWith('M')).toBe(true);
    expect(paths.outline.includes('Z')).toBe(false);
  });

  it('carries each leg’s own direction for its fill', () => {
    const paths = core.legPaths(profile, legs, 24);
    expect(paths.legs.map((fill) => fill.climbing)).toEqual([true, false]);
    expect(paths.legs.map((fill) => fill.leg.i)).toEqual([1, 2]);
  });

  it('shares the boundary vertex between adjacent legs', () => {
    const paths = core.legPaths(profile, legs, 24);
    const lastOf = (d) => d.split(' L').slice(-3)[0];
    const firstOf = (d) => d.split(' ')[0].slice(1);
    // The first leg's last profile vertex is the second leg's first.
    expect(lastOf(paths.legs[0].d).split(' ')[0]).toBe(firstOf(paths.legs[1].d));
  });

  it('places a leg by its share of the sample count', () => {
    const [start, end] = core.legSpan(legs[1], 24, profile.distanceM);
    expect(start / profile.distanceM).toBeCloseTo(0.5);
    expect(end).toBeCloseTo(profile.distanceM);
  });

  it('draws nothing for a track without elevation', () => {
    const flat = readProfile([[7.4, 46.1, null], [7.41, 46.1, null]]);
    expect(core.legPaths(flat, legs, 24)).toEqual({ legs: [], outline: '' });
  });

  it('draws the outline alone when there are no legs', () => {
    const paths = core.legPaths(profile, [], 24);
    expect(paths.legs).toEqual([]);
    expect(paths.outline).not.toBe('');
  });
});

describe('legAt', () => {
  const legs = [
    { i: 1, from: 0, to: 9, climbing: true },
    { i: 2, from: 10, to: 19, climbing: false },
  ];

  it('finds the leg under a fraction of the strip', () => {
    expect(core.legAt(0.1, legs, 20).i).toBe(1);
    expect(core.legAt(0.75, legs, 20).i).toBe(2);
  });

  it('clamps to the ends', () => {
    expect(core.legAt(1, legs, 20).i).toBe(2);
    expect(core.legAt(-0.2, legs, 20).i).toBe(1);
  });

  it('answers null with nothing to index', () => {
    expect(core.legAt(0.5, legs, 0)).toBeNull();
  });
});
