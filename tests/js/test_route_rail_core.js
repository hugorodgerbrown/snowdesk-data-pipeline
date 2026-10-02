/*
 * tests/js/test_route_rail_core.js — the route rail's pure half
 * (static/js/route_rail_core.js, SNOW-1018).
 *
 * The tick step at the three spans the ticket names (500 m, 12.9 km,
 * 80 km), one unit per strip, one fill per leg carrying the leg's own
 * direction, the meta line in the routes list's format with every null
 * branch, with and without a time (SNOW-1065), the duration's rounding
 * rule shared with apps/core/durations.py, and a placed point's readout:
 * its elevation, whether a press is on the top line, and which side of
 * the cursor line its figures go (2026-10-02).
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

describe('pointDistance', () => {
  it('reads the segment middle as a share of the route, in metres under a km', () => {
    // 5.5 of 24 segments along 620 m is 142 m, to the nearest 10.
    expect(core.pointDistance(5, 24, 620)).toBe('140 m');
  });

  it('switches to kilometres to one decimal from 1000 m', () => {
    expect(core.pointDistance(99, 100, 12900)).toBe('12.8 km');
    // 996 m rounds to 1000 m, which reads as a kilometre, not "1000 m".
    expect(core.pointDistance(0, 1, 1992)).toBe('1.0 km');
  });

  it('uses the strings template’s units', () => {
    expect(core.pointDistance(0, 1, 5000, { km: '%(value)s km' })).toBe('2.5 km');
  });

  it('places nothing it cannot', () => {
    expect(core.pointDistance(null, 24, 620)).toBeNull();
    expect(core.pointDistance(1, 0, 620)).toBeNull();
    expect(core.pointDistance(1, 24, 0)).toBeNull();
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

describe('formatDuration', () => {
  it.each([
    [10260, { hours: '2', minutes: '51' }],
    [14700, { hours: '4', minutes: '05' }],
    [2460, { hours: '', minutes: '41' }],
    // 59.6 minutes rounds to the hour rather than reading as 59.
    [3576, { hours: '1', minutes: '00' }],
    // An exact half-minute rounds UP, as split_hours_minutes does.
    [16230, { hours: '4', minutes: '31' }],
  ])('splits %s s', (seconds, expected) => {
    expect(core.formatDuration(seconds)).toEqual(expected);
  });

  it.each([null, undefined, 0, -5, Number.NaN])('has no duration for %s', (seconds) => {
    expect(core.formatDuration(seconds)).toBeNull();
  });
});

describe('formatMetaLine', () => {
  const figures = { distance_m: 12900, ascent_m: 337, descent_m: 1906 };

  it('writes the routes list’s line', () => {
    expect(core.formatMetaLine(figures)).toBe('12.9km · 337m ↑ · 1906m ↓');
  });

  it('appends the time when the recording has one', () => {
    expect(core.formatMetaLine({ ...figures, duration_s: 10260 })).toBe(
      '12.9km · 337m ↑ · 1906m ↓ · 2h51m',
    );
    expect(core.formatMetaLine({ ...figures, duration_s: 2460 })).toBe(
      '12.9km · 337m ↑ · 1906m ↓ · 41m',
    );
  });

  it('omits an unknown side rather than showing zero', () => {
    expect(core.formatMetaLine({ ...figures, ascent_m: null })).toBe('12.9km · 1906m ↓');
    expect(core.formatMetaLine({ ...figures, descent_m: null })).toBe('12.9km · 337m ↑');
    expect(core.formatMetaLine({ distance_m: 12900, duration_s: 600 })).toBe('12.9km · 10m');
  });

  it('keeps a genuine zero', () => {
    expect(core.formatMetaLine({ distance_m: 800, ascent_m: 0, descent_m: 41.6 })).toBe(
      '0.8km · 0m ↑ · 42m ↓',
    );
  });

  it('is empty with no distance', () => {
    expect(core.formatMetaLine({ ascent_m: 10 })).toBe('');
    expect(core.formatMetaLine(null)).toBe('');
  });

  it('takes the templates it is given', () => {
    expect(
      core.formatMetaLine(
        { ...figures, duration_s: 600 },
        { 'meta-both': '%(km)s km, +%(ascent)s/-%(descent)s', 'meta-duration': '%(figures)s (%(duration)s)' },
      ),
    ).toBe('12.9 km, +337/-1906 (10m)');
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

describe('profileY', () => {
  it('is the outline\'s y at a distance, inside the box', () => {
    const profile = readProfile(track(81));
    const start = core.profileY(profile, 0);
    const top = core.profileY(profile, profile.distanceM / 2);

    expect(top).toBeLessThan(start);
    expect(start).toBeLessThanOrEqual(core.BOX.height);
    expect(core.profileY(readProfile([]), 10)).toBeNull();
  });
});

describe('pointElevation', () => {
  const profile = readProfile(track(81));

  it('reads the height under the cursor line, to the metre', () => {
    // One segment: its midpoint is the track's top.
    expect(core.pointElevation(profile, 0, 1)).toBe('1700 m');
    // Two segments: the first's midpoint is a quarter of the way along.
    expect(core.pointElevation(profile, 0, 2)).toBe('1600 m');
  });

  it('takes the partial\'s unit template', () => {
    expect(core.pointElevation(profile, 0, 1, { m: '%(value)s mètres' })).toBe('1700 mètres');
  });

  it('is null with no elevation or nothing to place', () => {
    const flat = readProfile([[7.4, 46.1, null], [7.41, 46.1, null]]);
    expect(core.pointElevation(flat, 0, 1)).toBeNull();
    expect(core.pointElevation(profile, null, 1)).toBeNull();
    expect(core.pointElevation(profile, 0, 0)).toBeNull();
    expect(core.pointElevation(null, 0, 1)).toBeNull();
  });
});

describe('onOutline', () => {
  const profile = readProfile(track(81));
  // An 80 px lane: the top of the track sits 5 px down, its start 75 px.

  it('takes a press on the top line, or loosely either side of it', () => {
    expect(core.onOutline(profile, 0.5, 5, 80, 20)).toBe(true);
    expect(core.onOutline(profile, 0.5, 24, 80, 20)).toBe(true);
    expect(core.onOutline(profile, 0, 75, 80, 20)).toBe(true);
  });

  it('refuses a press down in the fill, away from the line', () => {
    expect(core.onOutline(profile, 0.5, 60, 80, 20)).toBe(false);
    expect(core.onOutline(profile, 0, 5, 80, 20)).toBe(false);
  });

  it('refuses everything with no elevation or no lane', () => {
    const flat = readProfile([[7.4, 46.1, null], [7.41, 46.1, null]]);
    expect(core.onOutline(flat, 0.5, 40, 80, 20)).toBe(false);
    expect(core.onOutline(profile, 0.5, 5, 0, 20)).toBe(false);
  });
});

describe('readoutSide', () => {
  it('puts the figures right of the line while they fit', () => {
    expect(core.readoutSide(100, 360, 60, 6)).toBe('right');
    expect(core.readoutSide(294, 360, 60, 6)).toBe('right');
  });

  it('moves them left of the line near the end of the lane', () => {
    expect(core.readoutSide(295, 360, 60, 6)).toBe('left');
    expect(core.readoutSide(360, 360, 60, 6)).toBe('left');
  });
});
