/*
 * tests/js/test_route_point_card_core.js — the point card's pure half
 * (static/js/route_point_card_core.js, SNOW-1064).
 *
 * The track word's edges at 5/15/25/35° and the ground word's at
 * 5/30/35/40°; the track scale agreeing with the wheel's inner-ring
 * steps; every headline family and its edges at 45° and 135° from
 * downhill; the "turning" case; flat, unsampled and pre-SNOW-976 ground;
 * the gradient read off the profile and stopped at a leg's ends, checked
 * against the Backside track's real figures; a turn's two ends, which
 * line it crossed, and the kind it makes (SNOW-1069), checked against the
 * Col de la Chaux zig-zag; and `reading` building both lines and the
 * wheel's accessible name from a strings object.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/aspect_wheel_core.js';
import '../../static/js/route_point_card_core.js';

import backside from './fixtures/mont-fort-backside.json';
import zigzag from './fixtures/col-de-la-chaux-zigzag.json';

const core = self.pwaRoutePointCardCore;
const { readProfile } = self.pwaElevationProfileCore;

/** The English strings the partial's template renders. */
const STRINGS = {
  label: '%(headline)s; %(ground)s',
  join: '%(before)s • %(after)s',
  'label-join': '%(before)s, %(after)s',
  'compass-0': 'N',
  'compass-1': 'NE',
  'compass-2': 'E',
  'compass-3': 'SE',
  'compass-4': 'S',
  'compass-5': 'SW',
  'compass-6': 'W',
  'compass-7': 'NW',
  'heading-pair': '%(first)s → %(second)s',
  'label-heading-pair': '%(first)s to %(second)s',
  'steepness-level': 'Level',
  'steepness-gentle': 'Gentle',
  'steepness-moderate': 'Moderate',
  'steepness-steep': 'Steep',
  'steepness-very-steep': 'Very steep',
  'kind-ascent': 'ascent',
  'kind-descent': 'descent',
  'kind-traverse': 'traverse',
  'kind-fall-line': 'fall line',
  'kind-switchback': 'switchback',
  'headline-no-height': 'No height data',
  'ground-flat': 'Flat ground',
  'ground-moderate': 'Moderate slope',
  'ground-steep': 'Steep slope',
  'ground-very-steep': 'Very steep slope',
  'ground-extremely-steep': 'Extremely steep slope',
  'ground-unknown': 'No terrain data',
  'ground-falling-left': "%(ground)s, falling skier's left",
  'ground-falling-right': "%(ground)s, falling skier's right",
  'ground-falling-left-then-right': "%(ground)s, falling skier's left, then right",
  'ground-falling-right-then-left': "%(ground)s, falling skier's right, then left",
};

/**
 * A straight 25 m path heading `deg`, near Verbier.
 *
 * @param {number} deg The compass bearing.
 * @returns {Array<Array<number>>} Two points.
 */
function pathHeading(deg) {
  const rad = (deg * Math.PI) / 180;
  const start = [7.2, 46];
  const dLat = (25 * Math.cos(rad)) / 111320;
  const dLon = (25 * Math.sin(rad)) / (111320 * Math.cos((46 * Math.PI) / 180));
  return [start, [start[0] + dLon, start[1] + dLat]];
}

/**
 * A two-step path that heads `first`, then `last`, 12.5 m each, near
 * Verbier.
 *
 * @param {number} first The first step's bearing.
 * @param {number} last The last step's bearing.
 * @returns {Array<Array<number>>} Three points.
 */
function pathTurning(first, last) {
  const [a, b] = pathHeading(first);
  const mid = [a[0] + (b[0] - a[0]) / 2, a[1] + (b[1] - a[1]) / 2];
  const [c, d] = pathHeading(last);
  return [a, mid, [mid[0] + (d[0] - c[0]) / 2, mid[1] + (d[1] - c[1]) / 2]];
}

/**
 * A rising-then-falling track: `count` points, 5 m of height apart.
 *
 * @param {number} count How many points.
 * @returns {Array<Array<number>>} `[lon, lat, ele]` points.
 */
function track(count) {
  const half = Math.floor(count / 2);
  return Array.from({ length: count }, (_, i) => [
    7.4 + i / 10000,
    46.1,
    i <= half ? 1500 + i * 5 : 1500 + half * 5 - (i - half) * 5,
  ]);
}

describe('trackWord', () => {
  it.each([
    [0, 'level'],
    [4.9, 'level'],
    [5, 'gentle'],
    [-14.9, 'gentle'],
    [15, 'moderate'],
    [24.9, 'moderate'],
    [-25, 'steep'],
    [34.9, 'steep'],
    [35, 'very-steep'],
    [-62, 'very-steep'],
  ])('reads a %s° track as %s', (gradient, word) => {
    expect(core.trackWord(gradient)).toBe(word);
  });

  it('has no word for an unknown gradient', () => {
    expect(core.trackWord(null)).toBeNull();
    expect(core.trackWord(Number.NaN)).toBeNull();
  });

  it('shares its bounds with the wheel’s inner ring', () => {
    const steps = self.pwaAspectWheelCore.TRACK_STEPS.map((step) => step[0]);
    expect(core.TRACK_BOUNDS).toEqual(steps.slice(0, -1));
  });
});

describe('groundWord', () => {
  it.each([
    [0, 'flat'],
    [4.9, 'flat'],
    [5, 'moderate'],
    [29.9, 'moderate'],
    [30, 'steep'],
    [34.9, 'steep'],
    [35, 'very-steep'],
    [39.9, 'very-steep'],
    [40, 'extremely-steep'],
  ])('reads %s° ground as %s', (angle, word) => {
    expect(core.groundWord(angle)).toBe(word);
  });

  it('has no word for unknown ground', () => {
    expect(core.groundWord(null)).toBeNull();
  });
});

describe('angleBetween', () => {
  it('measures the short way round', () => {
    expect(core.angleBetween(10, 350)).toBe(20);
    expect(core.angleBetween(90, 270)).toBe(180);
    expect(core.angleBetween(45, 45)).toBe(0);
  });
});

describe('headingDeg', () => {
  it('reads the chord from first point to last', () => {
    expect(core.headingDeg(pathHeading(102))).toBeCloseTo(102, 0);
  });

  it('has no heading for a path with no length', () => {
    expect(core.headingDeg([[7, 46], [7, 46]])).toBeNull();
    expect(core.headingDeg(null)).toBeNull();
  });
});

describe('fallSide', () => {
  it('reads a clockwise fall as the skier’s right', () => {
    expect(core.fallSide(0, 90)).toBe('right');
    expect(core.fallSide(0, 270)).toBe('left');
    expect(core.fallSide(350, 20)).toBe('right');
    expect(core.fallSide(10, 300)).toBe('left');
  });
});

describe('headline', () => {
  // The ground falls to the east (90°) at 33°.
  const ASPECT = 90;
  const ANGLE = 33;

  it('reads within 45° of downhill as a fall line descent', () => {
    expect(core.headline(90, ASPECT, -37, 42)).toEqual({ key: 'fall-descent', steepness: 'very-steep', side: null });
    expect(core.headline(134.9, ASPECT, -20, ANGLE)).toEqual({ key: 'fall-descent', steepness: 'moderate', side: null });
  });

  it('reads within 45° of uphill as a fall line climb', () => {
    expect(core.headline(270, ASPECT, 22, ANGLE)).toEqual({ key: 'fall-climb', steepness: 'moderate', side: null });
    expect(core.headline(225.1, ASPECT, 8, ANGLE)).toEqual({ key: 'fall-climb', steepness: 'gentle', side: null });
  });

  it('reads 45° and 135° themselves as traverses', () => {
    expect(core.headline(135, ASPECT, -10, ANGLE).key).toBe('descending-traverse');
    expect(core.headline(225, ASPECT, 10, ANGLE).key).toBe('rising-traverse');
  });

  it('picks a traverse’s direction from the gradient', () => {
    // The mockup's point B: heading 113° across ground falling north.
    expect(core.headline(113, 0, 13.3, 32.7)).toEqual({ key: 'rising-traverse', steepness: 'gentle', side: 'left' });
    expect(core.headline(0, ASPECT, -6, ANGLE)).toEqual({ key: 'descending-traverse', steepness: 'gentle', side: 'right' });
    expect(core.headline(0, ASPECT, 2, ANGLE)).toEqual({ key: 'level-traverse', steepness: null, side: 'right' });
  });

  it('says turning when the gradient disagrees with the heading', () => {
    expect(core.headline(90, ASPECT, 6, ANGLE)).toEqual({ key: 'climb-turning', steepness: 'gentle', side: null });
    expect(core.headline(270, ASPECT, -12, ANGLE)).toEqual({ key: 'descent-turning', steepness: 'gentle', side: null });
  });

  it('reads the track alone on flat or unsampled ground', () => {
    expect(core.headline(90, null, -8, 3)).toEqual({ key: 'descent', steepness: 'gentle', side: null });
    expect(core.headline(90, null, 18, null)).toEqual({ key: 'climb', steepness: 'moderate', side: null });
    expect(core.headline(90, null, 1, null)).toEqual({ key: 'level', steepness: null, side: null });
    // An aspect on ground under 5° is ignored: flat ground faces nowhere.
    expect(core.headline(90, ASPECT, -8, 4)).toEqual({ key: 'descent', steepness: 'gentle', side: null });
    // No heading, nothing to cross with.
    expect(core.headline(null, ASPECT, -8, ANGLE)).toEqual({ key: 'descent', steepness: 'gentle', side: null });
  });

  it('says so when the track has no height', () => {
    expect(core.headline(90, ASPECT, null, ANGLE)).toEqual({ key: 'no-height', steepness: null, side: null });
  });
});

describe('turnOf', () => {
  // The ground falls north (0°): uphill is south.
  it('reads one side for a segment that stays on it', () => {
    expect(core.turnOf(pathTurning(80, 100), 0)).toEqual({ firstSide: 'left', lastSide: 'left', crossing: null });
  });

  it('reads a turn through uphill as an uphill crossing, either way round', () => {
    // East, then west by way of south: clockwise through 180°.
    expect(core.turnOf(pathTurning(100, 250), 0)).toEqual({ firstSide: 'left', lastSide: 'right', crossing: 'uphill' });
    // West, then east by way of south: anticlockwise through 180°.
    expect(core.turnOf(pathTurning(250, 100), 0)).toEqual({ firstSide: 'right', lastSide: 'left', crossing: 'uphill' });
  });

  it('reads a turn through downhill as a downhill crossing', () => {
    expect(core.turnOf(pathTurning(60, 300), 0)).toEqual({ firstSide: 'left', lastSide: 'right', crossing: 'downhill' });
  });

  it('has no reading without an aspect or a step with length', () => {
    expect(core.turnOf(pathTurning(100, 250), null)).toBeNull();
    expect(core.turnOf([[7, 46], [7, 46]], 0)).toBeNull();
    expect(core.turnOf(null, 0)).toBeNull();
  });
});

describe('kindOf', () => {
  const across = { firstSide: 'left', lastSide: 'right', crossing: 'uphill' };
  const down = { firstSide: 'left', lastSide: 'right', crossing: 'downhill' };
  const along = { firstSide: 'left', lastSide: 'left', crossing: null };

  it('names a crossing in the direction of travel a switchback', () => {
    expect(core.kindOf({ key: 'fall-climb', steepness: 'gentle', side: null }, across, 13)).toBe('switchback');
    expect(core.kindOf({ key: 'fall-descent', steepness: 'gentle', side: null }, down, -13)).toBe('switchback');
  });

  it('names a crossing against it the plain ascent or descent', () => {
    expect(core.kindOf({ key: 'rising-traverse', steepness: 'gentle', side: 'left' }, down, 9)).toBe('ascent');
    expect(core.kindOf({ key: 'descending-traverse', steepness: 'gentle', side: 'left' }, across, -9)).toBe('descent');
  });

  it('names a level crossing a traverse', () => {
    expect(core.kindOf({ key: 'level-traverse', steepness: null, side: 'left' }, across, 2)).toBe('traverse');
  });

  it.each([
    ['fall-descent', -20, 'fall-line'],
    ['fall-climb', 20, 'fall-line'],
    ['rising-traverse', 10, 'traverse'],
    ['descending-traverse', -10, 'traverse'],
    ['level-traverse', 1, 'traverse'],
    ['climb', 10, 'ascent'],
    ['climb-turning', 10, 'ascent'],
    ['descent', -10, 'descent'],
    ['descent-turning', -10, 'descent'],
    ['level', 1, null],
  ])('names the chord’s %s at %s° as %s', (key, gradient, kind) => {
    expect(core.kindOf({ key, steepness: null, side: null }, along, gradient)).toBe(kind);
  });

  it('has no kind without a height', () => {
    expect(core.kindOf({ key: 'no-height', steepness: null, side: null }, across, null)).toBeNull();
  });
});

describe('the Col de la Chaux zig-zag', () => {
  /**
   * One zig-zag segment's reading, by its index on the route.
   *
   * @param {number} index The segment's index on the whole route.
   * @returns {object} `reading`'s result.
   */
  function at(index) {
    return core.reading({ ...zigzag, index: index - zigzag.first_index }, STRINGS);
  }

  it.each([
    [60, 'E → S • Gentle • switchback', "Moderate slope, falling skier's left, then right"],
    [61, 'S • Gentle • traverse', "Moderate slope, falling skier's right"],
    [62, 'S → NE • Gentle • switchback', "Very steep slope, falling skier's right, then left"],
    [63, 'NE → E • Gentle • traverse', "Steep slope, falling skier's left"],
    [64, 'E → S • Gentle • switchback', "Steep slope, falling skier's left, then right"],
    [65, 'S • Moderate • traverse', "Moderate slope, falling skier's right"],
    [66, 'S → NE • Moderate • switchback', "Moderate slope, falling skier's right, then left"],
    [67, 'NE • Moderate • traverse', "Very steep slope, falling skier's left"],
  ])('reads segment %s as "%s"', (index, headline, ground) => {
    const words = at(index);
    expect(words.headline).toBe(headline);
    expect(words.ground).toBe(ground);
  });

  it('names the sectors the wheel lights', () => {
    const words = at(62);
    expect(words.state.track).toEqual([4, 1]);
    expect(words.headline.startsWith('S → NE')).toBe(true);
  });

  it('reads the arrow as "to" and the bullets as commas aloud', () => {
    expect(at(62).label).toBe("S to NE, Gentle, switchback; Very steep slope, falling skier's right, then left");
  });
});

describe('segmentGradients', () => {
  it('reads rise over run either side of each segment', () => {
    const profile = readProfile(track(81));
    const out = core.segmentGradients(profile, 24, profile.distanceM);
    expect(out).toHaveLength(24);
    expect(out[2]).toBeGreaterThan(0);
    expect(out[21]).toBeLessThan(0);
    expect(out[2]).toBeCloseTo(-out[21], 6);
  });

  it('is null throughout for a profile with no heights', () => {
    expect(core.segmentGradients(readProfile([]), 4, 100)).toEqual([null, null, null, null]);
    expect(core.segmentGradients(null, 2, 100)).toEqual([null, null]);
  });

  it('stops the window at the leg’s ends', () => {
    const profile = readProfile(track(81));
    const legs = [{ from: 0, to: 11 }, { from: 12, to: 23 }];
    const open = core.segmentGradients(profile, 24, profile.distanceM);
    const cut = core.segmentGradients(profile, 24, profile.distanceM, legs);
    expect(Math.abs(open[11])).toBeLessThan(Math.abs(open[2]) - 5);
    expect(cut[11]).toBeCloseTo(cut[2], 6);
    expect(cut[12]).toBeCloseTo(cut[21], 6);
  });

  it('never measures across a gap in the heights', () => {
    const coordinates = track(81).map((p, i) => (i === 20 ? [p[0], p[1], null] : p));
    const out = core.segmentGradients(readProfile(coordinates), 24, readProfile(coordinates).distanceM);
    expect(out[5]).toBeNull();
    expect(out[2]).not.toBeNull();
  });

  it('reads the Backside track’s two mockup points', () => {
    const profile = readProfile(backside.coordinates);
    const out = core.segmentGradients(profile, backside.angles.length, backside.distance_m, backside.legs);
    // Point A, a steep descent down the 42° face; point B, the gentle
    // climb. (The mockup read A at -37°; this fixture's heights put it a
    // step gentler, so the bound is the steep step's.)
    expect(out[27]).toBeLessThan(-25);
    expect(core.trackWord(out[151])).toBe('gentle');
    expect(out[151]).toBeGreaterThan(0);
  });
});

describe('reading', () => {
  /**
   * One three-segment route around the segment under test.
   *
   * @param {object} over Overrides for the middle segment.
   * @returns {object} The `reading` input.
   */
  function input(over) {
    const o = { heading: 90, gradient: -37, angle: 42, aspect: 2, ...over };
    return {
      index: 1,
      paths: [pathHeading(80), pathHeading(o.heading), pathHeading(170)],
      gradients: [-36, o.gradient, -26],
      angles: [40, o.angle, 30],
      aspects: 'aspects' in o ? o.aspects : [2, o.aspect, 3],
    };
  }

  it('reads the mockup’s point A in words', () => {
    const words = core.reading(input({}), STRINGS);
    expect(words.headline).toBe('E • Very steep • fall line');
    expect(words.ground).toBe('Extremely steep slope');
    expect(words.label).toBe('E, Very steep, fall line; Extremely steep slope');
    expect(words.kind).toBe('fall-line');
    expect(words.state.terrain).toEqual({ kind: 'faces', sector: 2, slopeDeg: 42 });
  });

  it('reads flat ground', () => {
    const words = core.reading(input({ angle: 3, aspect: null, gradient: -8 }), STRINGS);
    expect(words.headline).toBe('E • Gentle • descent');
    expect(words.ground).toBe('Flat ground');
  });

  it('reads unsampled ground', () => {
    const words = core.reading(input({ angle: null, aspect: null, gradient: 2 }), STRINGS);
    expect(words.headline).toBe('E • Level');
    expect(words.ground).toBe('No terrain data');
    expect(words.kind).toBeNull();
  });

  it('reads a payload with no aspects as no terrain data, never flat', () => {
    const words = core.reading(input({ aspects: undefined, gradient: -20 }), STRINGS);
    expect(words.headline).toBe('E • Moderate • descent');
    expect(words.ground).toBe('No terrain data');
  });

  it('says so when the track has no height', () => {
    const words = core.reading(input({ gradient: null }), STRINGS);
    expect(words.headline).toBe('E • No height data');
  });

  it('says which side the slope falls on a traverse', () => {
    // Heading north across ground falling east, then west.
    const right = core.reading(input({ heading: 0, gradient: -20, aspect: 2 }), STRINGS);
    expect(right.headline).toBe('N • Moderate • traverse');
    expect(right.ground).toBe("Extremely steep slope, falling skier's right");
    expect(right.label).toBe("N, Moderate, traverse; Extremely steep slope, falling skier's right");
    const left = core.reading(input({ heading: 0, gradient: 2, aspect: 6, angle: 33 }), STRINGS);
    expect(left.headline).toBe('N • Level • traverse');
    expect(left.ground).toBe("Steep slope, falling skier's left");
  });

  it('names no side on the fall line or against the heading', () => {
    expect(core.reading(input({}), STRINGS).ground).toBe('Extremely steep slope');
    const against = core.reading(input({ gradient: 6 }), STRINGS);
    expect(against.headline).toBe('E • Gentle • ascent');
    expect(against.ground).toBe('Extremely steep slope');
  });

  it('carries no degrees', () => {
    const words = core.reading(input({}), STRINGS);
    expect(`${words.headline} ${words.ground}`).not.toMatch(/°|\d/);
  });
});
