/*
 * tests/js/test_route_rail_two_core.js — rail two's pure half
 * (static/js/route_rail_two_core.js, SNOW-1019).
 *
 * The band runs (unmerged, unknowns kept apart), a leg opening fitted,
 * the view's clamps, scrolling a range into view by the least distance,
 * zoom about an anchor between its two limits, the bank row's grouping of
 * whole segments and its placeholder, the passage bars' least width
 * (SNOW-1031), and the leg's profile and figures on the
 * sample axis — the same axis rail one places its legs on — plus the
 * readout: the track's attitude, where the readout sits, and a stretch's
 * length to the nearest 25 m (SNOW-1024). SNOW-1033 adds the leg picker's
 * slots, the nearest-leg pick and the opening motion's timeline.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_rail_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/bank_ribbon_core.js';
import '../../static/js/route_rail_two_core.js';

const core = self.pwaRouteRailTwoCore;
const rail = self.pwaRouteRailCore;
const { classify } = self.pwaRouteSlopeCore;
const { readProfile } = self.pwaElevationProfileCore;

/** A 100-sample leg in the middle of a 300-sample route. */
const LEG = { from: 100, to: 199 };

describe('bandRuns', () => {
  it('makes one run per stretch of one class, unmerged', () => {
    expect(core.bandRuns([20, 25, 32, 20, 20, 36], classify)).toEqual([
      { from: 0, to: 1, classIndex: 0 },
      { from: 2, to: 2, classIndex: 1 },
      { from: 3, to: 4, classIndex: 0 },
      { from: 5, to: 5, classIndex: 2 },
    ]);
  });

  it('gives consecutive unknowns their own run, never joined to a class', () => {
    expect(core.bandRuns([20, null, null, 20, null], classify)).toEqual([
      { from: 0, to: 0, classIndex: 0 },
      { from: 1, to: 2, classIndex: null },
      { from: 3, to: 3, classIndex: 0 },
      { from: 4, to: 4, classIndex: null },
    ]);
  });

  it('keeps absolute indices inside a range', () => {
    expect(core.bandRuns([40, 40, 20, 20, 20, 40], classify, { from: 1, to: 3 })).toEqual([
      { from: 1, to: 1, classIndex: 3 },
      { from: 2, to: 3, classIndex: 0 },
    ]);
  });

  it('answers nothing for no angles', () => {
    expect(core.bandRuns(undefined, classify)).toEqual([]);
  });
});

describe('openingSpan', () => {
  it('opens every leg fitted, however long', () => {
    expect(core.openingSpan(LEG)).toBe(100);
    expect(core.openingSpan({ from: 0, to: 9 })).toBe(10);
    expect(core.openingSpan({ from: 0, to: 1999 })).toBe(2000);
  });
});

describe('placeView', () => {
  it('clamps at the leg start', () => {
    expect(core.placeView(LEG, 20, 50)).toEqual({ from: 100, to: 120 });
  });

  it('clamps at the leg end', () => {
    expect(core.placeView(LEG, 20, 190)).toEqual({ from: 180, to: 200 });
  });

  it('keeps a fractional left edge, so a pan is pixel-smooth', () => {
    expect(core.placeView(LEG, 20, 150.25)).toEqual({ from: 150.25, to: 170.25 });
  });
});

describe('ensureVisible', () => {
  const view = { from: 120, to: 140 };

  it('leaves the view alone for a range inside it', () => {
    expect(core.ensureVisible(LEG, view, 125, 130)).toBe(view);
  });

  it('scrolls right just far enough', () => {
    expect(core.ensureVisible(LEG, view, 145, 145)).toEqual({ from: 126, to: 146 });
  });

  it('scrolls left just far enough', () => {
    expect(core.ensureVisible(LEG, view, 110, 112)).toEqual({ from: 110, to: 130 });
  });

  it('aligns the start of a range longer than the window with the left edge', () => {
    expect(core.ensureVisible(LEG, view, 130, 170)).toEqual({ from: 130, to: 150 });
  });

  it('never scrolls past the leg end', () => {
    expect(core.ensureVisible(LEG, view, 199, 199)).toEqual({ from: 180, to: 200 });
  });
});

describe('followView', () => {
  const view = { from: 120, to: 140 };

  it('leaves the view alone for a range inside it', () => {
    expect(core.followView(LEG, view, 125, 130)).toBe(view);
  });

  it('centres a range that fits', () => {
    expect(core.followView(LEG, view, 160, 161)).toEqual({ from: 151, to: 171 });
  });

  it('aligns the start of a range longer than the window', () => {
    expect(core.followView(LEG, view, 150, 190)).toEqual({ from: 150, to: 170 });
  });

  it('clamps the centred window to the leg', () => {
    expect(core.followView(LEG, view, 198, 198)).toEqual({ from: 180, to: 200 });
    expect(core.followView(LEG, { from: 150, to: 170 }, 101, 101)).toEqual({ from: 100, to: 120 });
  });
});

describe('zoom', () => {
  it('keeps the anchor at its fraction of the lane', () => {
    const { span, view } = core.zoom(LEG, 40, 20, 150, 0.25);
    expect(span).toBe(20);
    expect(view).toEqual({ from: 145, to: 165 });
    expect(core.xOf(150, view, 400)).toBeCloseTo(100);
  });

  it('stops at six samples', () => {
    expect(core.zoom(LEG, 10, 1, 150, 0.5).span).toBe(6);
  });

  it('stops at the whole leg', () => {
    const { span, view } = core.zoom(LEG, 40, 1000, 150, 0.5);
    expect(span).toBe(100);
    expect(view).toEqual({ from: 100, to: 200 });
  });

  it('cannot zoom into a leg under six samples', () => {
    const short = { from: 10, to: 13 };
    expect(core.minSpan(short)).toBe(4);
    expect(core.zoom(short, 4, 2, 12, 0.5)).toEqual({
      span: 4,
      view: { from: 10, to: 14 },
    });
  });

  it('makes a short fitted leg pannable once zoomed in', () => {
    const short = { from: 0, to: 9 };
    const opened = core.placeView(short, core.openingSpan(short), 0);
    expect(opened).toEqual({ from: 0, to: 10 });
    expect(core.placeView(short, 10, 3)).toEqual(opened);

    const { span, view } = core.zoom(short, 10, 6, 0, 0);
    expect(view).toEqual({ from: 0, to: 6 });
    expect(core.placeView(short, span, 3)).toEqual({ from: 3, to: 9 });
  });
});

describe('fullyVisible', () => {
  it('names the whole samples inside a fractional view', () => {
    expect(core.fullyVisible({ from: 10.4, to: 20.6 })).toEqual([11, 19]);
  });
});

describe('clip', () => {
  const view = { from: 120, to: 140 };

  it('stops a range at the window edge', () => {
    expect(core.clip({ from: 110, to: 125 }, view)).toEqual({ from: 120, to: 126 });
    expect(core.clip({ from: 135, to: 150 }, view)).toEqual({ from: 135, to: 140 });
  });

  it('answers null for a range outside it', () => {
    expect(core.clip({ from: 141, to: 150 }, view)).toBeNull();
  });
});

describe('glyphGroup', () => {
  it('groups twelve segments at Leg 7 fitted, 0.9 px a segment', () => {
    // 390 px over 433 segments.
    expect(core.glyphGroup(433, 390)).toBe(12);
  });

  it('groups three at Leg 7 ×4, 3.7 px a segment', () => {
    expect(core.glyphGroup(390 / 3.7, 390)).toBe(3);
  });

  it('gives each segment its own glyph from 10 px up', () => {
    expect(core.glyphGroup(60, 600)).toBe(1);
    expect(core.glyphGroup(20, 600)).toBe(1);
  });

  it('holds an exact 10 / 3 px at three', () => {
    expect(core.glyphGroup(180, 600)).toBe(3);
  });
});

describe('resolveSpan', () => {
  it('is the widest span that draws, floor(width × 3 / 10)', () => {
    expect(core.resolveSpan({ from: 0, to: 999 }, 390)).toBe(117);
    expect(core.glyphGroup(core.resolveSpan({ from: 0, to: 999 }, 390), 390)).toBeLessThanOrEqual(3);
    expect(core.glyphGroup(core.resolveSpan({ from: 0, to: 999 }, 390) + 1, 390)).toBeGreaterThan(3);
  });

  it('is clamped to the leg', () => {
    expect(core.resolveSpan({ from: 0, to: 49 }, 390)).toBe(50);
    expect(core.resolveSpan({ from: 0, to: 999 }, 10)).toBe(6);
  });
});

describe('bankGlyphs', () => {
  const { bankWedge } = self.pwaBankRibbonCore;
  const banks = Array.from({ length: 300 }, (_, i) => (i % 2 ? 30 : -30));

  /** The glyphs for a view, the rest defaulted. */
  function glyphs(options) {
    return core.bankGlyphs({ bankWedge, banks, leg: LEG, width: 600, y: 27, ...options });
  }

  it('shows the placeholder, and no glyphs, past three segments a glyph', () => {
    // 200 segments over 600 px is 3 px each: N = 4.
    expect(glyphs({ leg: { from: 0, to: 299 }, view: { from: 0, to: 200 } }))
      .toEqual({ placeholder: true, glyphs: [] });
  });

  it('draws one glyph per segment from 10 px up, on its centre', () => {
    const view = { from: 120, to: 140 };
    const out = glyphs({ view });
    expect(out.placeholder).toBe(false);
    expect(out.glyphs.map((g) => g.index)).toEqual(Array.from({ length: 20 }, (_, i) => 120 + i));
    for (const g of out.glyphs) {
      expect(g.x).toBeCloseTo(core.xOf(g.index + 0.5, view, 600));
      expect((g.ground.x1 + g.ground.x2) / 2).toBeCloseTo(g.x, 9);
      expect(g.up[1]).toEqual([g.x, 27]);
    }
  });

  it('draws the largest |roll| in a group, with its side, never the mean', () => {
    const zigzag = banks.slice();
    // Group 103–105 at N = 3 (groups start at the leg's 100): +20, −35,
    // +20 would average to level.
    zigzag[103] = 20;
    zigzag[104] = -35;
    zigzag[105] = 20;
    const out = glyphs({ banks: zigzag, view: { from: 100, to: 280 } });
    const glyph = out.glyphs.find((g) => g.from === 103);
    expect(glyph.to).toBe(105);
    expect(glyph.index).toBe(104);
    expect(glyph.roll).toBe(-35);
    // Negative: the pale wedge is left of centre.
    expect(Math.max(...glyph.down.map(([x]) => x))).toBeLessThanOrEqual(glyph.x);
  });

  it('keeps its groups on the same segments as the view pans', () => {
    const at = glyphs({ view: { from: 100, to: 280 } }).glyphs;
    const panned = glyphs({ view: { from: 101.3, to: 281.3 } }).glyphs;
    const bounds = (list) => new Set(list.map((g) => g.from));
    for (const g of panned) expect((g.from - LEG.from) % 3).toBe(0);
    expect([...bounds(panned)].filter((from) => bounds(at).has(from)).length)
      .toBeGreaterThan(panned.length - 3);
  });

  it('sizes a glyph min(7, group px / 2 − 0.5)', () => {
    // N = 3 at 10/3 px a segment: 10 px groups, half-width 4.5.
    const grouped = glyphs({ view: { from: 100, to: 280 } }).glyphs[0];
    expect(grouped.halfWidth).toBeCloseTo(4.5, 9);
    expect(Math.abs(grouped.ground.x2 - grouped.ground.x1)).toBeCloseTo(9, 9);
    // 30 px a segment caps at 7.
    expect(glyphs({ view: { from: 120, to: 140 } }).glyphs[0].halfWidth).toBe(7);
  });

  it('draws nothing for a group whose banks are all unknown', () => {
    const gappy = banks.slice();
    gappy[103] = null;
    gappy[104] = null;
    gappy[105] = null;
    gappy[106] = null;
    const out = glyphs({ banks: gappy, view: { from: 100, to: 280 } });
    expect(out.glyphs.map((g) => g.from)).not.toContain(103);
    // One null in the group 106–108 leaves the known ones to read.
    expect(out.glyphs.find((g) => g.from === 106).index).toBe(107);
  });

  it('draws nothing outside the leg', () => {
    const out = glyphs({ leg: { from: 0, to: 3 }, view: { from: 0, to: 4 } });
    expect(out.glyphs.map((g) => g.index)).toEqual([0, 1, 2, 3]);
  });
});

describe('passageBox', () => {
  const view = { from: 0, to: 600 };

  it('spans a wide passage\'s real extent', () => {
    expect(core.passageBox({ from: 100, to: 120 }, view, 600)).toEqual({ x: 100, width: 20 });
  });

  it('widens a narrow one to 6 px about its centre', () => {
    expect(core.passageBox({ from: 100, to: 102 }, view, 600)).toEqual({ x: 98, width: 6 });
  });

  it('keeps a widened bar inside the lane', () => {
    expect(core.passageBox({ from: 0, to: 1 }, view, 600)).toEqual({ x: 0, width: 6 });
    expect(core.passageBox({ from: 599, to: 600 }, view, 600)).toEqual({ x: 594, width: 6 });
  });

  it('answers null for no part', () => {
    expect(core.passageBox(null, view, 600)).toBeNull();
  });
});

describe('indexAt and xOf', () => {
  it('round-trip a sample centre', () => {
    const view = { from: 120.3, to: 150.3 };
    for (const i of [121, 130, 149]) {
      expect(core.indexAt(core.xOf(i + 0.5, view, 480), view, 480)).toBe(i);
    }
  });
});

/**
 * A straight track, ~7.7 m between points, climbing then descending.
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

describe('legProfile and legFigures', () => {
  const profile = readProfile(track(81));
  const leg = { from: 0, to: 11 };

  it('puts the leg on the sample axis, with the leg’s own elevation range', () => {
    const lp = core.legProfile(profile, leg, 24, rail.clipRun);
    const first = lp.runs[0][0];
    const last = lp.runs[0][lp.runs[0].length - 1];
    expect(first.s).toBeCloseTo(0);
    expect(last.s).toBeCloseTo(12);
    expect(lp.minEle).toBe(1500);
    expect(lp.maxEle).toBeCloseTo(1700);
  });

  it('gives the leg’s figures in formatFigures’ shape', () => {
    const lp = core.legProfile(profile, leg, 24, rail.clipRun);
    const figures = core.legFigures(lp, leg, 24, 620);
    expect(figures.distance_m).toBeCloseTo(310);
    expect(figures.ascent_m).toBeCloseTo(200);
    expect(figures.descent_m).toBeCloseTo(0);
    expect(figures.elevation_start).toBe(1500);
    expect(figures.elevation_end).toBeCloseTo(1700);
    expect(rail.formatFigures(figures)).toBe('0.3 km · ▲ 200 m · ▼ 0 m · 1500 → 1700 m');
  });

  it('knows the distance alone for a leg with no elevation', () => {
    const lp = core.legProfile(readProfile([]), leg, 24, rail.clipRun);
    expect(core.legFigures(lp, leg, 24, 620)).toEqual({
      distance_m: 310,
      ascent_m: null,
      descent_m: null,
      elevation_start: null,
      elevation_end: null,
    });
  });
});

/**
 * The bank a track δ degrees off the fall line has on ground `angle`
 * steep: tan|roll| = tan(angle) · sin δ, unrounded.
 *
 * @param {number} angle
 * @param {number} delta
 * @returns {number}
 */
function bankFor(angle, delta) {
  const rad = Math.PI / 180;
  return Math.atan(Math.tan(angle * rad) * Math.sin(delta * rad)) / rad;
}

describe('trackAttitude', () => {
  it('keeps the passages tolerance for its cross-reference', () => {
    expect(core.FALL_LINE_TOLERANCE_DEG).toBe(30);
    expect(core.FALL_LINE_DEG).toBe(20);
    expect(core.TRAVERSE_DEG).toBe(65);
    expect(core.GENTLE_DEG).toBe(10);
  });

  it('is flat under 5°, with no side', () => {
    expect(core.trackAttitude(4.9, 4, false)).toEqual({ term: 'flat', side: null });
    expect(core.trackAttitude(4.9, 4, true)).toEqual({ term: 'flat', side: null });
    expect(core.trackAttitude(5, 4, false).term).not.toBe('flat');
  });

  it('is a gentle descent or ascent from 5° to under 10°, by the leg, with no side', () => {
    expect(core.trackAttitude(5, 4, false)).toEqual({ term: 'gentle-descent', side: null });
    expect(core.trackAttitude(5, -4, true)).toEqual({ term: 'gentle-ascent', side: null });
    expect(core.trackAttitude(9.9, 9, false)).toEqual({ term: 'gentle-descent', side: null });
    expect(core.trackAttitude(10, 0, false).term).toBe('fall-line');
  });

  it('is the fall line under 20° off it, on either leg', () => {
    expect(core.trackAttitude(40, bankFor(40, 19), false).term).toBe('fall-line');
    expect(core.trackAttitude(40, bankFor(40, 19), true).term).toBe('fall-line');
    expect(core.trackAttitude(40, 0, false)).toEqual({ term: 'fall-line', side: null });
  });

  it('falls away from 20° to 65° off the fall line', () => {
    expect(core.trackAttitude(40, bankFor(40, 20), false).term).toBe('falls-away');
    expect(core.trackAttitude(40, bankFor(40, 65), true).term).toBe('falls-away');
  });

  it('is a traverse past 65° off the fall line', () => {
    expect(core.trackAttitude(40, bankFor(40, 66), false).term).toBe('traverse');
    // A bank equal to the slope is straight across it.
    expect(core.trackAttitude(40, 40, true).term).toBe('traverse');
    // Rounding can push the bank past the slope; the ratio is clamped.
    expect(core.trackAttitude(40, 41, false).term).toBe('traverse');
  });

  it('names the side the ground falls away to by the roll\'s sign', () => {
    expect(core.trackAttitude(40, 30, false)).toEqual({ term: 'falls-away', side: 'right' });
    expect(core.trackAttitude(40, -30, false)).toEqual({ term: 'falls-away', side: 'left' });
    expect(core.trackAttitude(40, -39, true)).toEqual({ term: 'traverse', side: 'left' });
    expect(core.trackAttitude(40, 2.5, false).side).toBeNull();
    expect(core.trackAttitude(40, -3, false).side).toBe('left');
  });

  it('reads flat and gentle ground from the angle alone, with the bank unknown', () => {
    expect(core.trackAttitude(4, null, false)).toEqual({ term: 'flat', side: null });
    expect(core.trackAttitude(7, null, true)).toEqual({ term: 'gentle-ascent', side: null });
  });

  it('is unknown with no angle, or no bank on ground of 10° or more', () => {
    expect(core.trackAttitude(null, 10, false)).toBeNull();
    expect(core.trackAttitude(30, null, false)).toBeNull();
    expect(core.trackAttitude(undefined, undefined, true)).toBeNull();
  });
});

describe('readoutAnchor', () => {
  it('left-aligns in the left quarter, starting at the line', () => {
    expect(core.readoutAnchor(0, 400)).toEqual({ align: 'left', left: 0 });
    expect(core.readoutAnchor(99, 400)).toEqual({ align: 'left', left: 99 });
  });

  it('centres on the line in the middle half, stepping at a quarter', () => {
    expect(core.readoutAnchor(100, 400)).toEqual({ align: 'center', left: 100 });
    expect(core.readoutAnchor(200, 400)).toEqual({ align: 'center', left: 200 });
    expect(core.readoutAnchor(300, 400)).toEqual({ align: 'center', left: 300 });
  });

  it('right-aligns in the right quarter, ending at the line', () => {
    expect(core.readoutAnchor(301, 400)).toEqual({ align: 'right', left: 301 });
    expect(core.readoutAnchor(400, 400)).toEqual({ align: 'right', left: 400 });
  });

  it('left-aligns for a lane with no width', () => {
    expect(core.readoutAnchor(10, 0).align).toBe('left');
  });
});

describe('roundStretch', () => {
  it('rounds to the nearest 25 m', () => {
    expect(core.roundStretch(602)).toBe(600);
    expect(core.roundStretch(187)).toBe(175);
    expect(core.roundStretch(188)).toBe(200);
  });

  it('never reads under 25 m', () => {
    expect(core.roundStretch(10)).toBe(25);
    expect(core.roundStretch(0)).toBe(25);
  });
});

describe('nearestRange (SNOW-1033)', () => {
  // 10 px a sample across a 100 px lane.
  const VIEW = { from: 0, to: 10 };

  it('picks the range a tap falls inside', () => {
    const ranges = [{ from: 0, to: 1 }, { from: 2, to: 2 }, { from: 3, to: 9 }];
    expect(core.nearestRange(ranges, 25, VIEW, 100, 22)).toBe(ranges[1]);
  });

  it('gives a shared edge to the range that starts there, as indexAt does', () => {
    const ranges = [{ from: 0, to: 1 }, { from: 2, to: 2 }];
    expect(core.nearestRange(ranges, 20, VIEW, 100, 22)).toBe(ranges[1]);
  });

  it('picks a range up to 22 px beside the tap', () => {
    const ranges = [{ from: 0, to: 0 }, { from: 5, to: 9 }];
    // 22 px right of the first, 18 px left of the second.
    expect(core.nearestRange(ranges, 32, VIEW, 100, 22)).toBe(ranges[1]);
    expect(core.nearestRange([ranges[0]], 32, VIEW, 100, 22)).toBe(ranges[0]);
  });

  it('picks nothing farther than 22 px away', () => {
    expect(core.nearestRange([{ from: 0, to: 0 }], 33, VIEW, 100, 22)).toBeNull();
  });

  it('breaks a tie between two neighbours to the left', () => {
    const ranges = [{ from: 0, to: 0 }, { from: 5, to: 9 }];
    expect(core.nearestRange(ranges, 30, VIEW, 100, 22)).toBe(ranges[0]);
  });

  it('ignores a range outside the view', () => {
    expect(core.nearestRange([{ from: 20, to: 30 }], 99, VIEW, 100, 22)).toBeNull();
  });
});


describe('legSlots (SNOW-1033)', () => {
  const LEGS = [
    { i: 1, from: 0, to: 99, climbing: true },
    { i: 2, from: 100, to: 102, climbing: false },
    { i: 3, from: 103, to: 299, climbing: true },
  ];

  it('places each leg on rail one\'s scale, in true proportion', () => {
    const slots = core.legSlots(LEGS, 300);

    expect(slots.map((s) => s.leg.i)).toEqual([1, 2, 3]);
    expect(slots[0].left).toBe(0);
    expect(slots[0].width).toBeCloseTo(100 / 300);
    // A three-sample leg keeps its three samples' width: no minimum.
    expect(slots[1].left).toBeCloseTo(100 / 300);
    expect(slots[1].width).toBeCloseTo(3 / 300);
  });

  it('is contiguous and fills the lane', () => {
    const slots = core.legSlots(LEGS, 300);

    for (let k = 1; k < slots.length; k += 1) {
      expect(slots[k].left).toBeCloseTo(slots[k - 1].left + slots[k - 1].width);
    }
    const last = slots[slots.length - 1];
    expect(last.left + last.width).toBeCloseTo(1);
  });

  it('comes back in route order whatever order the legs arrive in', () => {
    const slots = core.legSlots([LEGS[2], LEGS[0], LEGS[1]], 300);

    expect(slots.map((s) => s.leg.i)).toEqual([1, 2, 3]);
  });

  it('gives a single leg the whole lane', () => {
    expect(core.legSlots([{ from: 0, to: 49 }], 50)).toEqual([
      { leg: { from: 0, to: 49 }, left: 0, width: 1 },
    ]);
  });

  it('drops malformed legs and returns none with no samples', () => {
    expect(core.legSlots([{ from: 5, to: 2 }, null, { from: 0, to: 400 }], 300)).toEqual([]);
    expect(core.legSlots(LEGS, 0)).toEqual([]);
    expect(core.legSlots(null, 300)).toEqual([]);
  });
});

describe('motionPlan (SNOW-1033)', () => {
  it('opens as press 0–80, stretch 80–220 eased out, fill 220–340', () => {
    const plan = core.motionPlan(false);

    expect(plan.totalMs).toBe(340);
    expect(plan.phases).toEqual([
      { name: 'press', start: 0, end: 80, easing: 'linear' },
      { name: 'stretch', start: 80, end: 220, easing: 'ease-out' },
      { name: 'fill', start: 220, end: 340, easing: 'ease-in-out' },
    ]);
  });

  it('closes as fill, stretch, press on the same total, the stretch eased in', () => {
    const plan = core.motionPlan(true);

    expect(plan.totalMs).toBe(340);
    expect(plan.phases).toEqual([
      { name: 'fill', start: 0, end: 120, easing: 'ease-in-out' },
      { name: 'stretch', start: 120, end: 260, easing: 'ease-in' },
      { name: 'press', start: 260, end: 340, easing: 'linear' },
    ]);
  });

  it('matches MOTION', () => {
    expect(core.MOTION.pressMs + core.MOTION.stretchMs + core.MOTION.fillMs)
      .toBe(core.MOTION.totalMs);
  });
});

describe('motionSlice (SNOW-1033)', () => {
  it('times a whole phase and a part of one', () => {
    const plan = core.motionPlan(false);

    expect(core.motionSlice(plan, 'stretch', 0, 1)).toEqual({
      delay: 80,
      duration: 140,
      easing: 'ease-out',
    });
    expect(core.motionSlice(plan, 'fill', 0.5, 1)).toEqual({
      delay: 280,
      duration: 60,
      easing: 'ease-in-out',
    });
  });

  it('clamps its fractions to the phase', () => {
    const plan = core.motionPlan(true);

    expect(core.motionSlice(plan, 'press', -1, 2)).toEqual({
      delay: 260,
      duration: 80,
      easing: 'linear',
    });
  });

  it('refuses a phase it does not have', () => {
    expect(() => core.motionSlice(core.motionPlan(false), 'spin', 0, 1)).toThrow(RangeError);
  });
});
