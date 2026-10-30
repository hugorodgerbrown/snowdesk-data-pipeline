/*
 * tests/js/test_route_draw_core.js — the pure half of drawing a route
 * (static/js/route_draw_core.js, SNOW-1256; taps and legs, SNOW-1277).
 *
 * The waypoint list and its edits, a tap's whole leg and its undo, the figures the panel shows, the
 * guide the map draws, the draft stored on the device, and the network's
 * paint (SNOW-1319).
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

describe('addLeg and undoLeg', () => {
  it('adds a routed leg as one tap at its last point', () => {
    const next = core.addLeg([[7, 46]], [0], [[7, 46.001], [7.001, 46.002], [7.002, 46.002]]);

    expect(next.points).toHaveLength(4);
    expect(next.taps).toEqual([0, 3]);
  });

  it('drops a leg point on the last one, and a leg that adds nothing is no tap', () => {
    const points = [[7, 46]];

    expect(core.addLeg(points, [0], [[7, 46], [7, 46.001]]).points).toEqual([[7, 46], [7, 46.001]]);
    expect(core.addLeg(points, [0], [[7, 46]])).toEqual({ points: points, taps: [0] });
    expect(core.addLeg([], [], [[7, 46]])).toEqual({ points: [[7, 46]], taps: [0] });
  });

  it('takes back the whole last leg, and the first tap leaves nothing', () => {
    const points = [[7, 46], [7, 46.001], [7, 46.002], [7, 46.003]];

    expect(core.undoLeg(points, [0, 1, 3])).toEqual({ points: points.slice(0, 2), taps: [0, 1] });
    expect(core.undoLeg([[7, 46]], [0])).toEqual({ points: [], taps: [] });
    expect(core.undoLeg([], [])).toEqual({ points: [], taps: [] });
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

  it('marks only the taps, and sends the next leg to the snap ring', () => {
    const points = [[7, 46], [7, 46.001], [7, 46.002]];
    const fc = core.guideCollection(points, [7.01, 46.01], { taps: [0, 2], snap: [7.002, 46.002] });
    const roles = fc.features.map((f) => f.properties.role);

    expect(roles).toEqual(['waypoint', 'waypoint', 'line', 'pending', 'snap']);
    expect(fc.features[1].geometry.coordinates).toEqual([7, 46.002]);
    expect(fc.features[3].geometry.coordinates[1]).toEqual([7.002, 46.002]);
    expect(fc.features[4].geometry.coordinates).toEqual([7.002, 46.002]);
  });
});

describe('the stored draft', () => {
  it('round-trips points, name and taps', () => {
    const raw = core.serialiseDraft([[7, 46], [7, 46.001], [7, 46.002]], 'Mont Fort', [0, 2]);

    expect(core.parseDraft(raw)).toEqual({
      points: [[7, 46], [7, 46.001], [7, 46.002]],
      name: 'Mont Fort',
      taps: [0, 2],
    });
  });

  it('puts a tap at every point when the stored taps do not fit', () => {
    const at = (taps) => core.parseDraft(JSON.stringify({ v: 1, points: [[7, 46], [7, 46.001]], taps }));

    expect(core.parseDraft(core.serialiseDraft([[7, 46], [7, 46.001]], '')).taps).toEqual([0, 1]);
    expect(at(undefined).taps).toEqual([0, 1]);
    expect(at([0]).taps).toEqual([0, 1]);
    expect(at([1, 1]).taps).toEqual([0, 1]);
    expect(at([0, 0.5, 1]).taps).toEqual([0, 1]);
  });

  it('reads anything that is not a draft as none', () => {
    expect(core.parseDraft(null)).toBeNull();
    expect(core.parseDraft('not json')).toBeNull();
    expect(core.parseDraft('{"v":2,"points":[[7,46]]}')).toBeNull();
    expect(core.parseDraft('{"v":1,"points":[]}')).toBeNull();
    expect(core.parseDraft('{"v":1,"points":[[7,"46"]]}')).toBeNull();
  });

  it('keeps a draft whose name is missing', () => {
    expect(core.parseDraft('{"v":1,"points":[[7,46]]}')).toEqual({ points: [[7, 46]], name: '', taps: [0] });
  });
});

describe('networkPaint', () => {
  it('draws the network as a solid amber line on a halo while following', () => {
    expect(core.networkPaint(true)).toEqual({
      core: { 'line-color': core.NETWORK_COLOUR, 'line-width': 2.5, 'line-opacity': 1 },
      halo: { 'line-color': '#ffffff', 'line-width': 5, 'line-opacity': 0.9 },
    });
  });

  it('quietens the network and drops the halo when not following', () => {
    expect(core.networkPaint(false)).toEqual({
      core: { 'line-color': core.NETWORK_COLOUR, 'line-width': 1.5, 'line-opacity': 0.35 },
      halo: { 'line-color': '#ffffff', 'line-width': 5, 'line-opacity': 0 },
    });
  });

  it('never dashes the network, so it cannot read as a basemap trail', () => {
    [true, false].forEach((follow) => {
      const paint = core.networkPaint(follow);
      expect(paint.core).not.toHaveProperty('line-dasharray');
      expect(paint.halo).not.toHaveProperty('line-dasharray');
    });
  });

  it('gives the network a colour the draft does not use', () => {
    expect(core.NETWORK_COLOUR).toBe('#d97706');
    expect(core.NETWORK_COLOUR).not.toBe('#c026d3');
  });
});
