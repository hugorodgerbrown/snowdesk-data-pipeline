/*
 * tests/js/test_aspect_wheel_core.js — the aspect wheel's pure half
 * (static/js/aspect_wheel_core.js, SNOW-1063).
 *
 * Sector edges and the forward azimuth; a turn inside a segment lighting
 * two sectors; neighbours deduplicated against the current heading and
 * absent at either end of the route; the four terrain kinds and how each
 * draws the outer ring; the inner ring on the track scale (SNOW-1064) with the outer ring on
 * the slope classes; the centre's bar against triangle at the 5° edge,
 * the triangle's direction and the empty centre below 36 px; the 2 px gap
 * from 96 px; and the label and line built from a strings object.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/route_slope_core.js';
import '../../static/js/aspect_wheel_core.js';

const core = self.pwaAspectWheelCore;

/** The latitude the test paths sit at, near Verbier. */
const LAT = 46;

/**
 * A point `metres` from `from` on compass bearing `deg`, flat-earth.
 *
 * @param {Array<number>} from `[lon, lat]`.
 * @param {number} deg The bearing.
 * @param {number} metres The distance.
 * @returns {Array<number>} `[lon, lat]`.
 */
function step(from, deg, metres) {
  const rad = (deg * Math.PI) / 180;
  const dLat = (metres * Math.cos(rad)) / 111320;
  const dLon = (metres * Math.sin(rad)) / (111320 * Math.cos((from[1] * Math.PI) / 180));
  return [from[0] + dLon, from[1] + dLat];
}

/**
 * A path from a list of step bearings, 10 m each.
 *
 * @param {Array<number>} bearings One per step.
 * @param {Array<number>} [start] The first point.
 * @returns {Array<Array<number>>} The path.
 */
function path(bearings, start = [7.2, LAT]) {
  const points = [start];
  bearings.forEach((deg) => points.push(step(points[points.length - 1], deg, 10)));
  return points;
}

/** The English strings the partial's template renders. */
const STRINGS = {
  'compass-0': 'N',
  'compass-1': 'NE',
  'compass-2': 'E',
  'compass-3': 'SE',
  'compass-4': 'S',
  'compass-5': 'SW',
  'compass-6': 'W',
  'compass-7': 'NW',
  'heading-pair': '%(first)s then %(second)s',
  label: '%(track)s; %(terrain)s',
  'label-climbing': 'Heading %(heading)s, climbing %(grade)s°',
  'label-descending': 'Heading %(heading)s, descending %(grade)s°',
  'label-level': 'Heading %(heading)s, level',
  'label-heading': 'Heading %(heading)s',
  'label-no-heading': 'No heading',
  'label-faces': 'slope faces %(aspect)s, %(slope)s°',
  'label-flat': 'flat ground',
  'label-unknown': 'no terrain data',
  line: '%(track)s · %(terrain)s',
  'line-heading': 'Heading %(heading)s',
  'line-faces': 'slope faces %(aspect)s',
  'line-flat': 'flat ground',
  'line-unknown': 'no terrain data',
};

/** A state with every part set, for the drawing tests to vary. */
const FACES = {
  track: [7],
  gradeDeg: -25,
  prev: null,
  next: null,
  terrain: { kind: 'faces', sector: 5, slopeDeg: 32 },
};

/**
 * Parse the wheel's markup.
 *
 * @param {string} markup SVG markup.
 * @returns {SVGSVGElement} The root element.
 */
function parse(markup) {
  const doc = new DOMParser().parseFromString(markup, 'image/svg+xml');
  return /** @type {SVGSVGElement} */ (/** @type {unknown} */ (doc.documentElement));
}

describe('sectorOf', () => {
  it('puts 22.5° in NE and 337.5° in N, the boundary going clockwise', () => {
    expect(core.sectorOf(22.4)).toBe(0);
    expect(core.sectorOf(22.5)).toBe(1);
    expect(core.sectorOf(337.4)).toBe(7);
    expect(core.sectorOf(337.5)).toBe(0);
  });

  it('wraps 360° to N', () => {
    expect(core.sectorOf(360)).toBe(0);
    expect(core.sectorOf(0)).toBe(0);
  });

  it('answers null for a value that is not a number', () => {
    expect(core.sectorOf(NaN)).toBeNull();
    expect(core.sectorOf(Infinity)).toBeNull();
    expect(core.sectorOf(null)).toBeNull();
  });
});

describe('bearingDeg', () => {
  it('reads the four cardinal directions', () => {
    const origin = [7.2, LAT];
    expect(core.bearingDeg(origin, step(origin, 0, 100))).toBeCloseTo(0, 1);
    expect(core.bearingDeg(origin, step(origin, 90, 100))).toBeCloseTo(90, 1);
    expect(core.bearingDeg(origin, step(origin, 180, 100))).toBeCloseTo(180, 1);
    expect(core.bearingDeg(origin, step(origin, 270, 100))).toBeCloseTo(270, 1);
  });

  it('reads a diagonal', () => {
    const origin = [7.2, LAT];
    expect(core.bearingDeg(origin, step(origin, 315, 100))).toBeCloseTo(315, 0);
  });
});

describe('headingSectors', () => {
  it('gives one sector for a straight segment', () => {
    expect(core.headingSectors(path([310, 312, 315]))).toEqual([7]);
  });

  it('gives two sectors, first step first, for a turn inside the segment', () => {
    expect(core.headingSectors(path([0, 45, 90]))).toEqual([0, 2]);
  });

  it('skips a zero-length step', () => {
    const p = path([90]);
    expect(core.headingSectors([p[0], p[0], p[1], p[1]])).toEqual([2]);
  });

  it('answers nothing for a path with no length', () => {
    expect(core.headingSectors([[7.2, LAT], [7.2, LAT]])).toEqual([]);
    expect(core.headingSectors(undefined)).toEqual([]);
  });
});

describe('wheelState', () => {
  const paths = [path([0]), path([315]), path([180])];
  const input = {
    index: 1,
    paths,
    gradients: [-10, -25, 12],
    angles: [20, 32, 3],
    aspects: [5, 5, null],
  };

  it('reads the heading, the gradient and the neighbours', () => {
    const state = core.wheelState(input);
    expect(state.track).toEqual([7]);
    expect(state.gradeDeg).toBe(-25);
    expect(state.prev).toEqual({ sector: 0, gradeDeg: -10 });
    expect(state.next).toEqual({ sector: 4, gradeDeg: 12 });
  });

  it('drops a neighbour that shares the current heading', () => {
    const state = core.wheelState({ ...input, paths: [path([315]), path([315]), path([180])] });
    expect(state.prev).toBeNull();
    expect(state.next).toEqual({ sector: 4, gradeDeg: 12 });
  });

  it('has no previous at the route start and no next at its end', () => {
    expect(core.wheelState({ ...input, index: 0 }).prev).toBeNull();
    expect(core.wheelState({ ...input, index: 2 }).next).toBeNull();
  });

  it('says the ground faces a sector, with its angle', () => {
    expect(core.wheelState(input).terrain).toEqual({ kind: 'faces', sector: 5, slopeDeg: 32 });
  });

  it('says the ground is flat below 5°', () => {
    expect(core.wheelState({ ...input, index: 2 }).terrain).toEqual({ kind: 'flat' });
  });

  it('says the ground is unknown where the angle is null', () => {
    const state = core.wheelState({ ...input, angles: [20, null, 3], aspects: [5, null, null] });
    expect(state.terrain).toEqual({ kind: 'unknown' });
  });

  it('says none for a payload with no aspects key', () => {
    const { aspects, ...older } = input;
    expect(aspects).toBeDefined();
    expect(core.wheelState(older).terrain).toEqual({ kind: 'none' });
  });

  it('reads a null gradient as null', () => {
    expect(core.wheelState({ ...input, gradients: [null, null, null] }).gradeDeg).toBeNull();
  });
});

describe('trackFill', () => {
  it.each([
    [0, 'var(--color-track-level)'],
    [4.9, 'var(--color-track-level)'],
    [5, 'var(--color-slope-gentle)'],
    [-14.9, 'var(--color-slope-gentle)'],
    [15, 'var(--color-slope-30)'],
    [24.9, 'var(--color-slope-30)'],
    [-25, 'var(--color-slope-35)'],
    [34.9, 'var(--color-slope-35)'],
    [35, 'var(--color-slope-40)'],
    [-60, 'var(--color-slope-40)'],
  ])('fills a %s° track with %s', (gradient, token) => {
    expect(core.trackFill(gradient)).toBe(token);
  });

  it('fills an unknown gradient with the unknown token', () => {
    expect(core.trackFill(null)).toBe('var(--color-slope-unknown)');
  });

  it('starts the level step at LEVEL_DEG', () => {
    expect(core.LEVEL_DEG).toBe(5);
    expect(core.TRACK_STEPS[0][0]).toBe(core.LEVEL_DEG);
  });
});

describe('aspectWheelSvg', () => {
  it('fills the inner ring on the track scale and the outer on the slope classes', () => {
    // A 13° climb across 33° ground: gentle on the track scale, and the
    // 30–35° class on the ground's — the mockup's point B.
    const state = { ...FACES, gradeDeg: 13, terrain: { kind: 'faces', sector: 0, slopeDeg: 33 } };
    const svg = parse(core.aspectWheelSvg({ size: 48, state }));
    expect(svg.querySelector('[data-lit="heading"]')?.getAttribute('fill')).toBe('var(--color-slope-gentle)');
    expect(svg.querySelector('[data-lit="faces"]')?.getAttribute('fill')).toBe('var(--color-slope-30)');
    const level = parse(core.aspectWheelSvg({ size: 48, state: { ...state, gradeDeg: 3 } }));
    expect(level.querySelector('[data-lit="heading"]')?.getAttribute('fill')).toBe('var(--color-track-level)');
  });

  it('is an image with its label', () => {
    const svg = parse(core.aspectWheelSvg({ size: 48, state: FACES, label: 'A "wheel"' }));
    expect(svg.getAttribute('role')).toBe('img');
    expect(svg.getAttribute('aria-label')).toBe('A "wheel"');
    expect(svg.getAttribute('viewBox')).toBe('0 0 100 100');
    expect(svg.getAttribute('width')).toBe('48');
  });

  it('clamps its size to 16–200', () => {
    expect(parse(core.aspectWheelSvg({ size: 8, state: FACES })).getAttribute('width')).toBe('16');
    expect(parse(core.aspectWheelSvg({ size: 400, state: FACES })).getAttribute('width')).toBe('200');
  });

  it('lights the ground sector in its slope class and the heading in the track’s', () => {
    const svg = parse(core.aspectWheelSvg({ size: 48, state: FACES }));
    const faces = svg.querySelectorAll('[data-ring="terrain"][data-lit="faces"]');
    expect(faces).toHaveLength(1);
    expect(faces[0].getAttribute('fill')).toBe('var(--color-slope-30)');
    const heading = svg.querySelectorAll('[data-ring="track"][data-lit="heading"]');
    expect(heading).toHaveLength(1);
    // FACES descends at 25°: steep on the track scale, not gentle.
    expect(heading[0].getAttribute('fill')).toBe('var(--color-slope-35)');
    expect(svg.querySelectorAll('[fill="var(--color-card-hover)"]')).toHaveLength(14);
  });

  it('draws a keyline on each lit sector', () => {
    const svg = parse(core.aspectWheelSvg({ size: 48, state: FACES }));
    const keylines = svg.querySelectorAll('[stroke="var(--color-text-1)"]');
    expect(keylines).toHaveLength(2);
    expect(keylines[0].getAttribute('stroke-opacity')).toBe('0.55');
  });

  it('leaves the outer ring unlit on flat ground', () => {
    const svg = parse(core.aspectWheelSvg({ size: 48, state: { ...FACES, terrain: { kind: 'flat' } } }));
    expect(svg.querySelectorAll('[data-ring="terrain"][data-lit]')).toHaveLength(0);
    expect(svg.querySelectorAll('[data-ring="terrain"][fill="var(--color-card-hover)"]')).toHaveLength(8);
  });

  it.each(['unknown', 'none'])('fills the whole outer ring grey for %s ground', (kind) => {
    const svg = parse(core.aspectWheelSvg({ size: 48, state: { ...FACES, terrain: { kind } } }));
    const grey = svg.querySelectorAll('[data-ring="terrain"][fill="var(--color-slope-unknown)"]');
    expect(grey).toHaveLength(8);
  });

  it('draws the neighbours at 35% in their own classes', () => {
    const state = {
      ...FACES,
      prev: { sector: 0, gradeDeg: -36 },
      next: { sector: 4, gradeDeg: 10 },
    };
    const svg = parse(core.aspectWheelSvg({ size: 48, state }));
    const neighbours = svg.querySelectorAll('[data-lit="neighbour"]');
    expect(neighbours).toHaveLength(2);
    expect(neighbours[0].getAttribute('fill')).toBe('var(--color-slope-40)');
    expect(neighbours[0].getAttribute('fill-opacity')).toBe('0.35');
    expect(neighbours[1].getAttribute('fill')).toBe('var(--color-slope-gentle)');
  });

  it('draws two neighbours heading one way as one sector', () => {
    const state = {
      ...FACES,
      prev: { sector: 0, gradeDeg: -20 },
      next: { sector: 0, gradeDeg: 41 },
    };
    const svg = parse(core.aspectWheelSvg({ size: 48, state }));
    const neighbours = svg.querySelectorAll('[data-lit="neighbour"]');
    expect(neighbours).toHaveLength(1);
    expect(neighbours[0].getAttribute('fill')).toBe('var(--color-slope-40)');
  });

  it('lights both sectors of a turn', () => {
    const svg = parse(core.aspectWheelSvg({ size: 48, state: { ...FACES, track: [0, 2] } }));
    expect(svg.querySelectorAll('[data-lit="heading"]')).toHaveLength(2);
  });

  it('draws a bar at 4.9° and a triangle at 5.0°', () => {
    const level = parse(core.aspectWheelSvg({ size: 48, state: { ...FACES, gradeDeg: 4.9 } }));
    expect(level.querySelector('[data-centre]')?.getAttribute('data-centre')).toBe('level');
    const climb = parse(core.aspectWheelSvg({ size: 48, state: { ...FACES, gradeDeg: 5.0 } }));
    expect(climb.querySelector('[data-centre]')?.getAttribute('data-centre')).toBe('climbing');
  });

  it('points the triangle up for a climb and down for a descent', () => {
    /**
     * The apex and base y of the centre triangle.
     *
     * @param {number} gradeDeg The gradient.
     * @returns {Array<number>} [apex y, base y].
     */
    function ys(gradeDeg) {
      const svg = parse(core.aspectWheelSvg({ size: 48, state: { ...FACES, gradeDeg } }));
      const d = svg.querySelector('[data-centre]')?.getAttribute('d') || '';
      const numbers = d.match(/-?\d+(\.\d+)?/g)?.map(Number) || [];
      return [numbers[1], numbers[3]];
    }
    const [upApex, upBase] = ys(12);
    expect(upApex).toBeLessThan(upBase);
    const [downApex, downBase] = ys(-12);
    expect(downApex).toBeGreaterThan(downBase);
  });

  it('sizes the triangle from max(3.2, size × 0.034) px', () => {
    const svg = parse(core.aspectWheelSvg({ size: 200, state: FACES }));
    const d = svg.querySelector('[data-centre]')?.getAttribute('d') || '';
    const numbers = d.match(/-?\d+(\.\d+)?/g)?.map(Number) || [];
    // 6.8 px half-width at 200 px is 3.4 viewBox units.
    expect(numbers[2] - 50).toBeCloseTo(3.4, 3);
  });

  it('leaves the centre empty below 36 px', () => {
    expect(parse(core.aspectWheelSvg({ size: 35, state: FACES })).querySelector('[data-centre]')).toBeNull();
    expect(parse(core.aspectWheelSvg({ size: 36, state: FACES })).querySelector('[data-centre]')).not.toBeNull();
  });

  it('cuts 1.5 px gaps below 96 px and 2 px from 96 px', () => {
    /**
     * The gap stroke's width in CSS px.
     *
     * @param {number} size The wheel's size.
     * @returns {number} The width.
     */
    function gapPx(size) {
      const svg = parse(core.aspectWheelSvg({ size, state: FACES }));
      const sector = svg.querySelector('[stroke="var(--color-card)"]');
      return (Number(sector?.getAttribute('stroke-width')) * size) / 100;
    }
    expect(gapPx(95)).toBeCloseTo(1.5, 2);
    expect(gapPx(96)).toBeCloseTo(2, 2);
  });
});

describe('wheelLabel and headingLine', () => {
  it('says the heading, the descent and the ground', () => {
    expect(core.wheelLabel(FACES, STRINGS)).toBe('Heading NW, descending 25°; slope faces SW, 32°');
    expect(core.headingLine(FACES, STRINGS)).toBe('Heading NW · slope faces SW');
  });

  it('says a climb, a level track and a turn', () => {
    expect(core.wheelLabel({ ...FACES, gradeDeg: 18.4 }, STRINGS)).toBe(
      'Heading NW, climbing 18°; slope faces SW, 32°',
    );
    expect(core.wheelLabel({ ...FACES, gradeDeg: 1 }, STRINGS)).toBe(
      'Heading NW, level; slope faces SW, 32°',
    );
    expect(core.headingLine({ ...FACES, track: [0, 2] }, STRINGS)).toBe(
      'Heading N then E · slope faces SW',
    );
  });

  it('says flat ground and no terrain data', () => {
    expect(core.headingLine({ ...FACES, terrain: { kind: 'flat' } }, STRINGS)).toBe(
      'Heading NW · flat ground',
    );
    expect(core.headingLine({ ...FACES, terrain: { kind: 'unknown' } }, STRINGS)).toBe(
      'Heading NW · no terrain data',
    );
    expect(core.wheelLabel({ ...FACES, terrain: { kind: 'none' } }, STRINGS)).toBe(
      'Heading NW, descending 25°; no terrain data',
    );
  });

  it('reads every word from the strings object', () => {
    const french = { ...STRINGS, 'compass-7': 'NO', 'line-heading': 'Cap %(heading)s' };
    expect(core.headingLine(FACES, french)).toBe('Cap NO · slope faces SW');
  });
});
