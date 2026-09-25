/*
 * tests/js/test_route_rail_two_core.js — rail two's pure half
 * (static/js/route_rail_two_core.js, SNOW-1019).
 *
 * The band runs (unmerged, unknowns kept apart), the window a leg opens
 * at, the view's clamps, scrolling a range into view by the least
 * distance, zoom about an anchor between its two limits, the ribbon's
 * pitch turning per-sample, and the leg's profile and figures on the
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
  it('opens a long leg at 2 km of ground', () => {
    // 300 samples over 15 km: 50 m a sample, so 2 km is 40 samples.
    expect(core.openingSpan(LEG, 300, 15000)).toBe(40);
  });

  it('opens a leg shorter than 2 km whole', () => {
    expect(core.openingSpan({ from: 0, to: 9 }, 300, 15000)).toBe(10);
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
    const opened = core.placeView(short, core.openingSpan(short, 300, 15000), 0);
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

describe('tickPitch', () => {
  it('keeps the base pitch at a wide view', () => {
    expect(core.tickPitch(200, 600, 8)).toBe(8);
  });

  it('turns per-sample once a sample is wider than the base pitch', () => {
    expect(core.tickPitch(20, 600, 8)).toBe(30);
  });
});

describe('ribbonWedges', () => {
  const banks = Array.from({ length: 300 }, (_, i) => (i % 2 ? 30 : -30));

  it('puts one glyph on each sample centre once zoomed in, however far panned', () => {
    const view = { from: 120.4, to: 140.4 };
    const wedges = core.ribbonWedges({
      bankWedges: self.pwaBankRibbonCore.bankWedges,
      banks,
      leg: LEG,
      view,
      width: 600,
    });
    expect(wedges.length).toBeGreaterThanOrEqual(19);
    for (const w of wedges) {
      expect(w.x).toBeCloseTo(core.xOf(w.index + 0.5, view, 600));
    }
    expect(new Set(wedges.map((w) => w.index)).size).toBe(wedges.length);
  });

  it('shifts the ground line and both wedges by the same phase as the centre', () => {
    const view = { from: 120.4, to: 140.4 };
    const wedges = core.ribbonWedges({
      bankWedges: self.pwaBankRibbonCore.bankWedges,
      banks,
      leg: LEG,
      view,
      width: 600,
      y: 27,
    });
    for (const w of wedges) {
      expect((w.ground.x1 + w.ground.x2) / 2).toBeCloseTo(w.x, 9);
      expect(w.up[1]).toEqual([w.x, 27]);
      expect(w.down[0]).toEqual([w.x, 27]);
      // A positive roll falls away right: the pale wedge is right of centre.
      const pale = w.down.map(([x]) => x);
      if (w.roll > 0) expect(Math.min(...pale)).toBeGreaterThanOrEqual(w.x);
      else expect(Math.max(...pale)).toBeLessThanOrEqual(w.x);
    }
  });

  it('keeps the 15 px glyph pitch at a wide view', () => {
    const wedges = core.ribbonWedges({
      bankWedges: self.pwaBankRibbonCore.bankWedges,
      banks,
      leg: LEG,
      view: { from: 100, to: 200 },
      width: 600,
    });
    expect(wedges[1].x - wedges[0].x).toBeCloseTo(15);
  });

  it('draws no glyph for a null bank', () => {
    const gappy = banks.slice();
    gappy[125] = null;
    const wedges = core.ribbonWedges({
      bankWedges: self.pwaBankRibbonCore.bankWedges,
      banks: gappy,
      leg: LEG,
      view: { from: 120, to: 130 },
      width: 600,
    });
    expect(wedges.map((w) => w.index)).toEqual([120, 121, 122, 123, 124, 126, 127, 128, 129]);
  });

  it('draws nothing outside the leg', () => {
    const wedges = core.ribbonWedges({
      bankWedges: self.pwaBankRibbonCore.bankWedges,
      banks,
      leg: { from: 0, to: 3 },
      view: { from: 0, to: 4 },
      width: 600,
    });
    expect(wedges.map((w) => w.index)).toEqual([0, 1, 2, 3]);
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
