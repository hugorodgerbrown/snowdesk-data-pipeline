/*
 * tests/js/test_route_cursor_map_core.js — the route cursor placed on the
 * map (static/js/route_cursor_map_core.js, SNOW-1019).
 *
 * A selection's inclusive `to` reaching point to + 1, the cursor dot on
 * its segment's middle, the nearest sample on screen with its distance
 * cap, and null for everything a route with no slope record cannot answer.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/route_cursor_map_core.js';

const core = self.pwaRouteCursorMapCore;

/** Four points bounding three segments along a meridian. */
const SLOPE = {
  points: [[7.0, 46.0], [7.0, 46.002], [7.0, 46.004], [7.0, 46.006]],
  angles: [20, 35, null],
};

describe('selectionLine', () => {
  it('runs from point `from` to point `to + 1`', () => {
    const line = core.selectionLine(SLOPE, { kind: 'band', from: 1, to: 2 });

    expect(line.geometry).toEqual({
      type: 'LineString',
      coordinates: [[7.0, 46.002], [7.0, 46.004], [7.0, 46.006]],
    });
    expect(line.properties).toEqual({ kind: 'band', from: 1, to: 2 });
  });

  it('draws a one-sample selection as its one segment', () => {
    expect(core.selectionLine(SLOPE, { kind: 'passage', from: 0, to: 0 }).geometry.coordinates)
      .toEqual([[7.0, 46.0], [7.0, 46.002]]);
  });

  it('answers null for no selection, or one outside the route', () => {
    expect(core.selectionLine(SLOPE, null)).toBeNull();
    expect(core.selectionLine(SLOPE, { kind: 'band', from: 2, to: 3 })).toBeNull();
    expect(core.selectionLine(SLOPE, { kind: 'band', from: -1, to: 0 })).toBeNull();
    expect(core.selectionLine(SLOPE, { kind: 'band', from: 2, to: 1 })).toBeNull();
  });

  it('answers null for a route with no slope record, like a pending share', () => {
    expect(core.selectionLine(null, { kind: 'band', from: 0, to: 0 })).toBeNull();
    expect(core.selectionLine({}, { kind: 'band', from: 0, to: 0 })).toBeNull();
  });
});

describe('cursorPoint', () => {
  it('sits at the middle of the index\'s segment', () => {
    const point = core.cursorPoint(SLOPE, 1);

    expect(point.geometry.type).toBe('Point');
    expect(point.geometry.coordinates[0]).toBeCloseTo(7.0);
    expect(point.geometry.coordinates[1]).toBeCloseTo(46.003);
    expect(point.properties.index).toBe(1);
  });

  it('answers null for a null index, an index off the route, or no record', () => {
    expect(core.cursorPoint(SLOPE, null)).toBeNull();
    expect(core.cursorPoint(SLOPE, 3)).toBeNull();
    expect(core.cursorPoint(SLOPE, -1)).toBeNull();
    expect(core.cursorPoint(SLOPE, 1.5)).toBeNull();
    expect(core.cursorPoint(null, 0)).toBeNull();
  });
});

describe('segmentMidpoints', () => {
  it('gives one middle per segment', () => {
    const middles = core.segmentMidpoints(SLOPE);

    expect(middles).toHaveLength(3);
    expect(middles[2][1]).toBeCloseTo(46.005);
  });

  it('gives none without a record', () => {
    expect(core.segmentMidpoints(undefined)).toEqual([]);
    expect(core.sampleCount({ points: [[7, 46]] })).toBe(0);
  });
});

describe('nearestSample', () => {
  const px = [{ x: 0, y: 0 }, { x: 30, y: 0 }, null, { x: 90, y: 0 }];

  it('picks the segment middle nearest on screen', () => {
    expect(core.nearestSample(px, { x: 34, y: 5 }, 24)).toBe(1);
  });

  it('skips a segment with no position', () => {
    expect(core.nearestSample(px, { x: 62, y: 0 })).toBe(3);
  });

  it('answers null beyond the distance cap', () => {
    expect(core.nearestSample(px, { x: 30, y: 40 }, 24)).toBeNull();
  });

  it('answers null with no midpoints at all', () => {
    expect(core.nearestSample([], { x: 0, y: 0 }, 24)).toBeNull();
  });
});

describe('legAt', () => {
  const legs = [
    { i: 1, from: 0, to: 4, climbing: true },
    { i: 2, from: 5, to: 9, climbing: false },
  ];

  it('finds the leg holding an index, ends inclusive', () => {
    expect(core.legAt(legs, 4).i).toBe(1);
    expect(core.legAt(legs, 5).i).toBe(2);
  });

  it('answers null outside every leg or with no legs', () => {
    expect(core.legAt(legs, 10)).toBeNull();
    expect(core.legAt(null, 1)).toBeNull();
    expect(core.legAt(legs, null)).toBeNull();
  });
});
