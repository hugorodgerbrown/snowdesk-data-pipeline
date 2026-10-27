/*
 * tests/js/test_route_draw_core.js — the pure half of drawing a route
 * (static/js/route_draw_core.js, SNOW-1256).
 *
 * The waypoint list and its two edits, the figures the panel shows, the
 * guide the map draws, and the draft stored on the device.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/route_draw_core.js';

const core = self.pwaRouteDrawCore;

describe('addPoint', () => {
  it('appends a rounded point and leaves the original list alone', () => {
    const before = [[7, 46]];
    const after = core.addPoint(before, 7.123456789, 46.001);

    expect(after).toEqual([[7, 46], [7.123457, 46.001]]);
    expect(before).toEqual([[7, 46]]);
  });

  it('ignores a second tap on the same spot', () => {
    const points = [[7, 46]];

    expect(core.addPoint(points, 7.000001, 46)).toBe(points);
  });

  it('ignores a point that is not a coordinate', () => {
    const points = [[7, 46]];

    expect(core.addPoint(points, NaN, 46)).toBe(points);
    expect(core.addPoint(points, 7, 95)).toBe(points);
  });
});

describe('undo', () => {
  it('drops the last point, and nothing from an empty list', () => {
    expect(core.undo([[7, 46], [7, 46.001]])).toEqual([[7, 46]]);
    expect(core.undo([])).toEqual([]);
  });
});

describe('lengthM', () => {
  it('measures the straight line along the points', () => {
    // 0.001° of meridian is about 111 m.
    expect(core.lengthM([[7, 46], [7, 46.001]])).toBeCloseTo(111.2, 0);
    expect(core.lengthM([[7, 46]])).toBe(0);
  });
});

describe('formatKm and durationParts', () => {
  it('quotes a distance to one decimal place', () => {
    expect(core.formatKm(5429)).toBe('5.4');
    expect(core.formatKm(-1)).toBe('0.0');
  });

  it('splits an estimate into hours and padded minutes', () => {
    expect(core.durationParts(4 * 3600 + 600)).toEqual({ hours: 4, minutes: '10' });
    expect(core.durationParts(3600)).toEqual({ hours: 1, minutes: '00' });
    expect(core.durationParts(3000)).toEqual({ hours: 0, minutes: '50' });
  });
});

describe('statsParts', () => {
  const points = [[7, 46], [7, 46.001]];

  it('uses the straight line until the server has answered', () => {
    const parts = core.statsParts(null, points);

    expect(parts.count).toBe(2);
    expect(parts.distanceM).toBeCloseTo(111.2, 0);
    expect(parts.ascentM).toBeNull();
    expect(parts.terrain).toBe(false);
  });

  it("uses the server's figures once it has", () => {
    const parts = core.statsParts(
      { distance_m: 120, ascent_m: 40, descent_m: 0, estimate_s: 600, slope: {} },
      points,
    );

    expect(parts).toMatchObject({
      distanceM: 120,
      ascentM: 40,
      descentM: 0,
      estimateS: 600,
      terrain: true,
    });
  });

  it('reads a preview with no slope as unmodelled ground', () => {
    const parts = core.statsParts({ distance_m: 120, ascent_m: null }, points);

    expect(parts.terrain).toBe(false);
    expect(parts.ascentM).toBeNull();
    expect(parts.estimateS).toBeNull();
  });
});

describe('guideCollection', () => {
  it('draws each waypoint, the line through them and the next leg', () => {
    const fc = core.guideCollection([[7, 46], [7, 46.001]], [7.001, 46.001]);
    const roles = fc.features.map((f) => f.properties.role);

    expect(roles).toEqual(['waypoint', 'waypoint', 'line', 'pending']);
    expect(fc.features[0].properties.first).toBe(true);
    expect(fc.features[3].geometry.coordinates).toEqual([[7, 46.001], [7.001, 46.001]]);
  });

  it('draws no line for one point and no leg without a cursor', () => {
    const fc = core.guideCollection([[7, 46]], null);

    expect(fc.features.map((f) => f.properties.role)).toEqual(['waypoint']);
  });

  it('draws nothing for no points', () => {
    expect(core.guideCollection([], [7, 46]).features).toEqual([]);
  });
});

describe('the stored draft', () => {
  it('round-trips points and name', () => {
    const raw = core.serialiseDraft([[7, 46], [7, 46.001]], 'Mont Fort');

    expect(core.parseDraft(raw)).toEqual({ points: [[7, 46], [7, 46.001]], name: 'Mont Fort' });
  });

  it('reads anything that is not a draft as none', () => {
    expect(core.parseDraft(null)).toBeNull();
    expect(core.parseDraft('not json')).toBeNull();
    expect(core.parseDraft('{"v":2,"points":[[7,46]]}')).toBeNull();
    expect(core.parseDraft('{"v":1,"points":[]}')).toBeNull();
    expect(core.parseDraft('{"v":1,"points":[[7,"46"]]}')).toBeNull();
  });

  it('keeps a draft whose name is missing', () => {
    expect(core.parseDraft('{"v":1,"points":[[7,46]]}')).toEqual({ points: [[7, 46]], name: '' });
  });
});
