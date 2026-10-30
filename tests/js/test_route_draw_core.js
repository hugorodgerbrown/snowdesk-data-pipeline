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
      key: null,
      failed: null,
      principal: null,
      sent: null,
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
    expect(core.parseDraft('{"v":1,"points":[[7,46]]}')).toEqual({
      points: [[7, 46]],
      name: '',
      taps: [0],
      key: null,
      failed: null,
      principal: null,
      sent: null,
    });
  });
});

describe('the draft tag and the account stamp (SNOW-1262)', () => {
  const POINTS = [[7, 46], [7, 46.001]];

  it('round-trips the save key, a failed key and the account', () => {
    const raw = core.serialiseDraft(POINTS, 'Mont Fort', [0, 1], {
      key: 'k-1',
      failed: 'k-0',
      principal: 'account-uuid',
    });

    expect(core.parseDraft(raw)).toMatchObject({ key: 'k-1', failed: 'k-0', principal: 'account-uuid' });
  });

  it('stores no tag for an untagged, anonymous draft', () => {
    const value = JSON.parse(core.serialiseDraft(POINTS, '', [0, 1], { key: null, principal: null }));

    expect(Object.keys(value).sort()).toEqual(['name', 'points', 'taps', 'v']);
  });
});

describe('the save as a queued operation (SNOW-1262)', () => {
  it('posts the points and name as JSON, with the CSRF token and the draft key', () => {
    const op = core.createOperation('/routes/drawn/', 'csrf', [[7, 46], [7, 46.001]], 'Mont Fort', 'k-1');

    expect(op).toEqual({
      method: 'POST',
      url: '/routes/drawn/',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': 'csrf' },
      body: JSON.stringify({ points: [[7, 46], [7, 46.001]], name: 'Mont Fort' }),
      idempotencyKey: 'k-1',
      kind: core.ROUTE_CREATE_KIND,
    });
    expect(core.ROUTE_CREATE_KIND).toBe('route-create');
  });

  it('mints a different key every time', () => {
    const a = core.mintKey();

    expect(typeof a).toBe('string');
    expect(a).not.toBe(core.mintKey());
  });

  it('frames a list of points', () => {
    expect(core.pointsBounds([[7, 46.1], [7.2, 46], [7.1, 46.3]])).toEqual([7, 46, 7.2, 46.3]);
    expect(core.pointsBounds([])).toBeNull();
  });
});

describe('what a stored draft is now (SNOW-1262)', () => {
  const tagged = { key: 'k', principal: 'me' };

  it('is nothing without a draft', () => {
    expect(core.draftState(null, 'me', null, null)).toBe('none');
  });

  it("is another account's only when a DIFFERENT account is signed in", () => {
    expect(core.draftState(tagged, 'someone-else', null, null)).toBe('foreign');
  });

  it('is hidden, not discarded, signed out or before the account is known', () => {
    expect(core.draftState(tagged, null, null, null)).toBe('hidden');
    expect(core.draftState(tagged, undefined, null, { uuid: 'u' })).toBe('hidden');
  });

  it("is the signed-in account's when stamped for no one", () => {
    expect(core.draftState({ key: null, principal: null }, 'me', null, null)).toBe('draft');
  });

  it('has landed once its key is acknowledged, row or no row', () => {
    expect(core.draftState(tagged, 'me', null, { uuid: 'u' })).toBe('landed');
  });

  it('is queued while its row waits, and failed once the row is refused', () => {
    expect(core.draftState(tagged, 'me', { status: 'queued' }, null)).toBe('queued');
    expect(core.draftState(tagged, 'me', { status: 'retry-scheduled' }, null)).toBe('queued');
    expect(core.draftState(tagged, 'me', { status: 'failed' }, null)).toBe('failed');
  });

  it('is unsent — kept, with its key — when there is neither row nor acknowledgement', () => {
    expect(core.draftState(tagged, 'me', null, null)).toBe('unsent');
  });
});

describe('the body fingerprint (SNOW-1262)', () => {
  const POINTS = [[7, 46], [7, 46.001]];

  it('is the same for the same body and differs once the points or name change', () => {
    const fp = core.bodyFingerprint(POINTS, 'Mont Fort');

    expect(core.bodyFingerprint([[7, 46], [7, 46.001]], 'Mont Fort')).toBe(fp);
    expect(core.bodyFingerprint([[7, 46], [7, 46.002]], 'Mont Fort')).not.toBe(fp);
    expect(core.bodyFingerprint(POINTS, 'Mont Fort II')).not.toBe(fp);
  });

  it('is stored beside the key, and only with one', () => {
    const tagged = core.serialiseDraft(POINTS, 'n', [0, 1], { key: 'k', sent: 'abc' });
    const untagged = core.serialiseDraft(POINTS, 'n', [0, 1], { key: null, sent: 'abc' });

    expect(core.parseDraft(tagged).sent).toBe('abc');
    expect(core.parseDraft(untagged).sent).toBe(null);
  });
});

describe('forgetDraftFor (SNOW-1262)', () => {
  const POINTS = [[7, 46], [7, 46.001]];

  function storage(value) {
    const items = new Map(value ? [[core.DRAFT_KEY, value]] : []);
    return {
      getItem: (k) => (items.has(k) ? items.get(k) : null),
      removeItem: (k) => items.delete(k),
      has: () => items.has(core.DRAFT_KEY),
    };
  }

  it("removes the draft tagged with the discarded save's key, in flight or refused", () => {
    const inFlight = storage(core.serialiseDraft(POINTS, 'n', [0, 1], { key: 'k-1' }));
    const refused = storage(core.serialiseDraft(POINTS, 'n', [0, 1], { failed: 'k-1' }));

    expect(core.forgetDraftFor('k-1', inFlight)).toBe(true);
    expect(inFlight.has()).toBe(false);
    expect(core.forgetDraftFor('k-1', refused)).toBe(true);
    expect(refused.has()).toBe(false);
  });

  it('leaves a draft with another key, or none, alone', () => {
    const other = storage(core.serialiseDraft(POINTS, 'n', [0, 1], { key: 'k-2' }));
    const untagged = storage(core.serialiseDraft(POINTS, 'n', [0, 1]));

    expect(core.forgetDraftFor('k-1', other)).toBe(false);
    expect(other.has()).toBe(true);
    expect(core.forgetDraftFor('k-1', untagged)).toBe(false);
    expect(untagged.has()).toBe(true);
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
