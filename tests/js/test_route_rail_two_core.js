/*
 * tests/js/test_route_rail_two_core.js — rail two's pure half
 * (static/js/route_rail_two_core.js, SNOW-1019).
 *
 * The band runs (unmerged, unknowns kept apart), the window a leg opens
 * at, the view's clamps, scrolling a range into view by the least
 * distance, zoom about an anchor between its two limits, the ribbon's
 * pitch turning per-sample, and the leg's profile and figures on the
 * sample axis — the same axis rail one places its legs on.
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

describe('ribbonTicks', () => {
  const banks = Array.from({ length: 300 }, (_, i) => (i % 2 ? 30 : -30));

  it('puts one tick on each sample centre once zoomed in, however far panned', () => {
    const view = { from: 120.4, to: 140.4 };
    const ticks = core.ribbonTicks({
      bankTicks: self.pwaBankRibbonCore.bankTicks,
      banks,
      leg: LEG,
      view,
      width: 600,
    });
    expect(ticks.length).toBeGreaterThanOrEqual(19);
    for (const tick of ticks) {
      expect(tick.x).toBeCloseTo(core.xOf(tick.index + 0.5, view, 600));
    }
    expect(new Set(ticks.map((t) => t.index)).size).toBe(ticks.length);
  });

  it('keeps the base pitch at a wide view', () => {
    const ticks = core.ribbonTicks({
      bankTicks: self.pwaBankRibbonCore.bankTicks,
      banks,
      leg: LEG,
      view: { from: 100, to: 200 },
      width: 400,
    });
    expect(ticks[1].x - ticks[0].x).toBeCloseTo(8);
  });

  it('draws no tick for a null bank', () => {
    const gappy = banks.slice();
    gappy[125] = null;
    const ticks = core.ribbonTicks({
      bankTicks: self.pwaBankRibbonCore.bankTicks,
      banks: gappy,
      leg: LEG,
      view: { from: 120, to: 130 },
      width: 600,
    });
    expect(ticks.map((t) => t.index)).toEqual([120, 121, 122, 123, 124, 126, 127, 128, 129]);
  });

  it('draws nothing outside the leg', () => {
    const ticks = core.ribbonTicks({
      bankTicks: self.pwaBankRibbonCore.bankTicks,
      banks,
      leg: { from: 0, to: 3 },
      view: { from: 0, to: 4 },
      width: 600,
    });
    expect(ticks.map((t) => t.index)).toEqual([0, 1, 2, 3]);
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
    expect(rail.formatFigures(figures)).toBe('0.3 km · ▲200 m · ▼0 m · 1500→1700 m');
  });

  it('draws only the part inside the view', () => {
    const lp = core.legProfile(profile, leg, 24, rail.clipRun);
    const { line, area } = core.profilePaths(lp, { from: 3, to: 9 }, 600, rail.clipRun);
    const xs = line.match(/[ML](-?[\d.]+)/g).map((m) => Number(m.slice(1)));
    expect(Math.min(...xs)).toBeCloseTo(0);
    expect(Math.max(...xs)).toBeCloseTo(600);
    expect(area.endsWith('Z')).toBe(true);
  });

  it('finds the curve\'s y at a sample, top of the leg highest', () => {
    const lp = core.legProfile(profile, leg, 24, rail.clipRun);
    const low = core.profileYAt(lp, 0.5);
    const high = core.profileYAt(lp, 11.5);

    // A climb: the end sits higher on screen, which is a smaller y.
    expect(high).toBeLessThan(low);
    expect(low).toBeLessThanOrEqual(core.ROWS.profileBottom);
    expect(core.profileYAt(core.legProfile(readProfile([]), leg, 24, rail.clipRun), 3))
      .toBeNull();
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

describe('distanceTicks', () => {
  it('labels the window in route metres, the numbers rail one prints', () => {
    // 300 samples over 15 km: the view 100–140 is 5000–7000 m.
    const ticks = core.distanceTicks({ from: 100, to: 140 }, 300, 15000, rail);
    const labels = ticks.filter((t) => t.major).map((t) => t.label);
    expect(labels).toEqual(['5 km', '6 km', '7 km']);
    expect(ticks[0].x).toBeCloseTo(0);
    expect(ticks[ticks.length - 1].x).toBeCloseTo(1);
  });

  it('starts at the first step inside a window that begins between steps', () => {
    const ticks = core.distanceTicks({ from: 101, to: 111 }, 300, 15000, rail);
    expect(ticks[0].d).toBeGreaterThanOrEqual(5050);
  });
});
