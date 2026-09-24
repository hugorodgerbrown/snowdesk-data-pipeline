/*
 * tests/js/test_route_legs_core.js — a saved route as its legs and its
 * transitions (static/js/route_legs_core.js, SNOW-1017).
 *
 * The facts worth holding here: each leg is sliced from the route's OWN
 * coordinates by its point indices, so an unsampled route still draws as
 * legs; a pending share draws nothing; a route of n legs has n − 1
 * numbered markers in track order; a passage takes the direction of the
 * leg it lies in; and the selection's opacity expression matches one leg
 * of one route, falling back to a plain number when nothing is open.
 *
 * map.js's wiring of these — the layers, the filters, the dimming — is
 * tests/js/test_map_route_leg_layers.js.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { beforeAll, describe, expect, it } from 'vitest';

const MAIN_CSS = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), '../../src/css/main.css',
);

let core;

beforeAll(async () => {
  await import('../../static/js/route_slope_core.js');
  await import('../../static/js/route_legs_core.js');
  core = globalThis.pwaRouteLegsCore;
});

/** Seven coordinates up and down: 0-3 climb, 3-6 descend. */
const COORDINATES = [
  [7.0, 46.0, 1500],
  [7.0, 46.001, 1550],
  [7.0, 46.002, 1600],
  [7.0, 46.003, 1650],
  [7.0, 46.004, 1600],
  [7.0, 46.005, 1550],
  [7.0, 46.006, 1500],
];

/** Two legs, in both index spaces. */
const LEGS = [
  { i: 1, from: 0, to: 1, climbing: true, point_from: 0, point_to: 3 },
  { i: 2, from: 2, to: 3, climbing: false, point_from: 3, point_to: 6 },
];

/**
 * A routes FeatureCollection holding one route.
 *
 * @param {object} [properties] Properties merged over an owned route's.
 * @returns {object}
 */
function routes(properties = {}) {
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: COORDINATES },
        properties: { uuid: 'r-1', legs: LEGS, ...properties },
      },
    ],
  };
}

describe('legCollection', () => {
  it('draws one line per leg, sliced from the route with the seam shared', () => {
    const features = core.legCollection(routes()).features;

    expect(features).toHaveLength(2);
    expect(features[0].geometry.coordinates).toEqual(COORDINATES.slice(0, 4));
    expect(features[1].geometry.coordinates).toEqual(COORDINATES.slice(3, 7));
    expect(features.map((f) => f.properties)).toEqual([
      { uuid: 'r-1', i: 1, climbing: true },
      { uuid: 'r-1', i: 2, climbing: false },
    ]);
  });

  it('draws an unsampled route from its point indices alone', () => {
    // No `slope` at all: a leg is a fact about the geometry.
    const fc = routes();
    delete fc.features[0].properties.slope;

    expect(core.legCollection(fc).features).toHaveLength(2);
  });

  it('draws nothing for a pending share', () => {
    const fc = routes({ pending: true, uuid: undefined, token: 'tok' });

    expect(core.legCollection(fc).features).toEqual([]);
  });

  it('skips a leg without point indices, as an older payload sends', () => {
    const fc = routes({
      legs: [{ i: 1, from: 0, to: 3, climbing: true }],
    });

    expect(core.legCollection(fc).features).toEqual([]);
  });

  it('skips a leg whose indices run past the geometry', () => {
    const fc = routes({
      legs: [{ i: 1, from: 0, to: 3, climbing: true, point_from: 0, point_to: 40 }],
    });

    expect(core.legCollection(fc).features).toEqual([]);
  });

  it('is an empty collection for no payload at all', () => {
    expect(core.legCollection(null)).toEqual({ type: 'FeatureCollection', features: [] });
  });
});

describe('transitionCollection', () => {
  it('marks legs − 1 transitions, numbered in track order', () => {
    const three = [
      { i: 1, from: 0, to: 0, climbing: true, point_from: 0, point_to: 2 },
      { i: 2, from: 1, to: 1, climbing: false, point_from: 2, point_to: 4 },
      { i: 3, from: 2, to: 2, climbing: true, point_from: 4, point_to: 6 },
    ];

    const features = core.transitionCollection(routes({ legs: three })).features;

    expect(features.map((f) => f.properties.n)).toEqual([1, 2]);
    expect(features.map((f) => f.geometry.coordinates)).toEqual([
      [7.0, 46.002],
      [7.0, 46.004],
    ]);
    expect(features.every((f) => f.properties.uuid === 'r-1')).toBe(true);
  });

  it('carries the direction of the leg each transition opens', () => {
    const features = core.transitionCollection(routes()).features;

    expect(features.map((f) => f.properties.climbing)).toEqual([false]);
  });

  it('marks nothing on a single-leg route', () => {
    const one = [{ i: 1, from: 0, to: 3, climbing: true, point_from: 0, point_to: 6 }];

    expect(core.transitionCollection(routes({ legs: one })).features).toEqual([]);
  });

  it('marks nothing on a pending share', () => {
    const fc = routes({ pending: true, uuid: undefined, token: 'tok' });

    expect(core.transitionCollection(fc).features).toEqual([]);
  });
});

describe('passageCollection', () => {
  /** Four segments, a passage in each leg. */
  const SLOPE = {
    points: [[7.0, 46.0], [7.0, 46.002], [7.0, 46.004], [7.0, 46.005], [7.0, 46.006]],
    angles: [52.0, 20.0, 20.0, 53.0],
    passages: [
      { from: 0, to: 0, m: 25.0, fall_line: 'climbing' },
      { from: 3, to: 3, m: 25.0, fall_line: 'descending' },
    ],
  };

  it('holds only the passages, each tagged with its leg\'s direction', () => {
    const features = core.passageCollection(routes({ slope: SLOPE })).features;

    expect(features).toHaveLength(2);
    expect(features.map((f) => f.properties.passage)).toEqual([true, true]);
    expect(features.map((f) => f.properties.climbing)).toEqual([true, false]);
    expect(features[1].geometry.coordinates).toEqual([[7.0, 46.005], [7.0, 46.006]]);
  });

  it('holds nothing for an unsampled route', () => {
    expect(core.passageCollection(routes()).features).toEqual([]);
  });

  it('holds nothing for a pending share', () => {
    const fc = routes({ slope: SLOPE, pending: true, uuid: undefined, token: 'tok' });

    expect(core.passageCollection(fc).features).toEqual([]);
  });
});

describe('dimOpacity', () => {
  it('matches the open leg of the open route, and dims the rest', () => {
    expect(core.dimOpacity({ uuid: 'r-1', i: 2 }, 1, 0.25)).toEqual([
      'case',
      ['all', ['==', ['get', 'uuid'], 'r-1'], ['==', ['get', 'i'], 2]],
      1,
      0.25,
    ]);
  });

  it('is plain `on` when nothing is open', () => {
    expect(core.dimOpacity(null, 0.55, 0.15)).toBe(0.55);
  });

  it('is plain `on` for a leg with no route to match', () => {
    expect(core.dimOpacity({ uuid: null, i: 1 }, 1, 0.25)).toBe(1);
  });
});

describe('hasDrawableLegs', () => {
  it('is true when every leg slices the geometry', () => {
    expect(core.hasDrawableLegs(routes().features[0])).toBe(true);
  });

  it('is false for legs without point indices, as an older payload sends', () => {
    const feature = routes({ legs: [{ i: 1, from: 0, to: 3, climbing: true }] }).features[0];

    expect(core.hasDrawableLegs(feature)).toBe(false);
  });

  it('is false when any one leg cannot be sliced — all or nothing', () => {
    const legs = [LEGS[0], { ...LEGS[1], point_to: 40 }];

    expect(core.hasDrawableLegs(routes({ legs }).features[0])).toBe(false);
  });

  it('is false for non-integer indices and for no legs at all', () => {
    const fractional = [{ ...LEGS[0], point_to: 2.5 }];

    expect(core.hasDrawableLegs(routes({ legs: fractional }).features[0])).toBe(false);
    expect(core.hasDrawableLegs(routes({ legs: [] }).features[0])).toBe(false);
    expect(core.hasDrawableLegs(null)).toBe(false);
  });
});

describe('withDrawableLegs', () => {
  it('removes undrawable legs from a copy and leaves the payload alone', () => {
    const fc = routes({ legs: [{ i: 1, from: 0, to: 3, climbing: true }] });

    const copy = core.withDrawableLegs(fc);

    expect(copy.features[0].properties).not.toHaveProperty('legs');
    expect(copy.features[0].properties.uuid).toBe('r-1');
    // The rail reads its legs from the original.
    expect(fc.features[0].properties.legs).toHaveLength(1);
  });

  it('hands a drawable route through as the same object', () => {
    const fc = routes();

    expect(core.withDrawableLegs(fc).features[0]).toBe(fc.features[0]);
  });

  it('hands a route with no legs through as the same object', () => {
    const fc = routes();
    delete fc.features[0].properties.legs;

    expect(core.withDrawableLegs(fc).features[0]).toBe(fc.features[0]);
  });

  it('passes a missing payload straight through', () => {
    expect(core.withDrawableLegs(null)).toBeNull();
  });
});

describe('the colours', () => {
  it('mirror the rail\'s two tokens', () => {
    // The map line and the rail below it are one drawing of one route; a
    // leg in two different fuchsias would read as two different things.
    const css = readFileSync(MAIN_CSS, 'utf8');

    expect(css).toMatch(new RegExp(`--color-route-rail-climb:\\s*${core.LEG_CLIMB_COLOUR}`));
    expect(css).toMatch(new RegExp(`--color-route-rail-descent:\\s*${core.LEG_DESCENT_COLOUR}`));
  });
});
