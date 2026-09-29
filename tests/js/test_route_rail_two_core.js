/*
 * tests/js/test_route_rail_two_core.js — rail two's pure half
 * (static/js/route_rail_two_core.js, SNOW-1019).
 *
 * The band runs (unmerged, unknowns kept apart), a leg opening fitted,
 * the view's clamps, scrolling a range into view by the least distance,
 * zoom about an anchor between its two limits, the wedges one per segment
 * and the passage bars' least width (SNOW-1031), and the leg's profile and
 * figures on the sample axis — the same axis rail one places its legs on —
 * plus where the readout sits and a stretch's length to the nearest 25 m
 * (SNOW-1024). SNOW-1044 adds the track row: the words-or-wedges switch,
 * each word's threshold, the stretches' merge, the kick turns (measured on
 * two canonical tours recorded by bin/record-rail-fixtures), the gradient
 * along the track, the card's steep length and the bank's side. SNOW-1032 adds the selection
 * box's minimum width. SNOW-1033 adds the leg picker's slots, the
 * nearest-range pick (the leg picker's, and rail two's band and passage
 * taps') and the opening motion's timeline.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_rail_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/bank_ribbon_core.js';
import '../../static/js/route_rail_two_core.js';

import backside from './fixtures/mont-fort-backside.json';
import chaux from './fixtures/mont-fort-col-de-la-chaux.json';

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

describe('selectionBox (SNOW-1032)', () => {
  // 6 px a sample across a 600 px lane.
  const VIEW = { from: 0, to: 100 };

  it('draws a one-sample part 12 px wide, centred on it', () => {
    expect(core.selectionBox({ from: 50, to: 51 }, VIEW, 600, 12)).toEqual({ x: 297, w: 12 });
  });

  it('keeps a wide part at its own width', () => {
    expect(core.selectionBox({ from: 10, to: 20 }, VIEW, 600, 12)).toEqual({ x: 60, w: 60 });
  });

  it('clamps the widened box to the lane at both edges', () => {
    expect(core.selectionBox({ from: 0, to: 1 }, VIEW, 600, 12)).toEqual({ x: 0, w: 12 });
    expect(core.selectionBox({ from: 99, to: 100 }, VIEW, 600, 12)).toEqual({ x: 588, w: 12 });
  });

  it('never draws wider than the lane', () => {
    expect(core.selectionBox({ from: 0, to: 1 }, { from: 0, to: 100 }, 8, 12))
      .toEqual({ x: 0, w: 8 });
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

describe('trackMode (SNOW-1044)', () => {
  it('is words under 10 px a segment and wedges from 10 px up', () => {
    // 433 segments over 390 px: 0.9 px each.
    expect(core.trackMode({ from: 0, to: 433 }, 390)).toBe('words');
    expect(core.trackMode({ from: 0, to: 61 }, 600)).toBe('words');
    expect(core.trackMode({ from: 0, to: 60 }, 600)).toBe('wedges');
    expect(core.trackMode({ from: 0, to: 20 }, 600)).toBe('wedges');
  });

  it('is words for a lane with no width', () => {
    expect(core.trackMode({ from: 0, to: 10 }, 0)).toBe('words');
  });
});

describe('resolveSpan', () => {
  it('is the widest span the wedges draw at, floor(width / 10)', () => {
    const span = core.resolveSpan({ from: 0, to: 999 }, 390);
    expect(span).toBe(39);
    expect(core.trackMode({ from: 0, to: span }, 390)).toBe('wedges');
    expect(core.trackMode({ from: 0, to: span + 1 }, 390)).toBe('words');
  });

  it('is clamped to the leg', () => {
    expect(core.resolveSpan({ from: 0, to: 49 }, 900)).toBe(50);
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

  it('draws one glyph per segment in view, on its centre', () => {
    const view = { from: 120, to: 140 };
    const out = glyphs({ view });
    expect(out.map((g) => g.index)).toEqual(Array.from({ length: 20 }, (_, i) => 120 + i));
    for (const g of out) {
      expect(g.x).toBeCloseTo(core.xOf(g.index + 0.5, view, 600));
      expect((g.ground.x1 + g.ground.x2) / 2).toBeCloseTo(g.x, 9);
      expect(g.up[1]).toEqual([g.x, 27]);
    }
  });

  it('draws each segment with its own sign', () => {
    const out = glyphs({ view: { from: 120, to: 140 } });
    const left = out.find((g) => g.index === 120);
    expect(left.roll).toBe(-30);
    // Negative: the pale wedge is left of centre.
    expect(Math.max(...left.down.map(([x]) => x))).toBeLessThanOrEqual(left.x);
  });

  it('sizes a glyph min(7, segment px / 2 − 0.5)', () => {
    // 60 segments over 600 px: 10 px each, half-width 4.5.
    const narrow = glyphs({ view: { from: 100, to: 160 } })[0];
    expect(narrow.halfWidth).toBeCloseTo(4.5, 9);
    expect(Math.abs(narrow.ground.x2 - narrow.ground.x1)).toBeCloseTo(9, 9);
    // 30 px a segment caps at 7.
    expect(glyphs({ view: { from: 120, to: 140 } })[0].halfWidth).toBe(7);
  });

  it('draws nothing for a segment whose bank is unknown', () => {
    const gappy = banks.slice();
    gappy[125] = null;
    const out = glyphs({ banks: gappy, view: { from: 120, to: 140 } });
    expect(out.map((g) => g.index)).not.toContain(125);
    expect(out).toHaveLength(19);
  });

  it('draws the segments a panned view cuts, and nothing outside the leg', () => {
    const cut = glyphs({ view: { from: 120.5, to: 140.5 } });
    expect(cut[0].index).toBe(120);
    expect(cut[cut.length - 1].index).toBe(140);
    const out = glyphs({ leg: { from: 0, to: 3 }, view: { from: 0, to: 4 } });
    expect(out.map((g) => g.index)).toEqual([0, 1, 2, 3]);
  });
});

describe('segmentWord (SNOW-1044)', () => {
  it('calls ground under 25° Gentle, or Skin on a climb', () => {
    expect(core.segmentWord(24.9, 40, 5, false)).toBe('gentle');
    expect(core.segmentWord(24.9, 40, 5, true)).toBe('skin');
    expect(core.segmentWord(0, null, null, true)).toBe('skin');
  });

  it('calls ground of 25° or more with a bank of 20° or more a Traverse', () => {
    expect(core.segmentWord(25, 20, 0, false)).toBe('traverse');
    expect(core.segmentWord(35, -20, 0, true)).toBe('traverse');
    expect(core.segmentWord(35, 19.9, 0, false)).toBe('steep');
  });

  it('calls ground of 25° or more with the fall line Steep', () => {
    expect(core.segmentWord(25, 0, -24, false)).toBe('steep');
    expect(core.segmentWord(40, -10, 10, true)).toBe('steep');
  });

  it('calls steep ground with an unknown bank Steep', () => {
    expect(core.segmentWord(30, null, null, false)).toBe('steep');
  });

  it('calls a gradient of 25° or more on a climb Bootpack, before anything else', () => {
    expect(core.segmentWord(30, 5, 25, true)).toBe('bootpack');
    expect(core.segmentWord(40, 30, 26, true)).toBe('bootpack');
    expect(core.segmentWord(30, 5, 24.9, true)).toBe('steep');
    // Never on a descent, and never read from a falling gradient.
    expect(core.segmentWord(30, 5, 30, false)).toBe('steep');
    expect(core.segmentWord(30, 5, -30, true)).toBe('steep');
  });

  it('calls an unknown angle unknown', () => {
    expect(core.segmentWord(null, 30, 30, true)).toBe('unknown');
    expect(core.segmentWord(undefined, null, null, false)).toBe('unknown');
  });
});

describe('stretches (SNOW-1044)', () => {
  /** @returns {number} Segments in a stretch. */
  const size = (s) => s.to - s.from + 1;

  /**
   * Angles laid in runs: [[angle, count], …] from index 0.
   *
   * @param {Array<[number, number]>} runs
   * @returns {Array<number>}
   */
  function laid(runs) {
    return runs.flatMap(([angle, count]) => Array(count).fill(angle));
  }

  it('gives one stretch per run of one word, each 150 m or more', () => {
    const angles = laid([[10, 8], [35, 10], [10, 6]]);
    const leg = { from: 0, to: angles.length - 1, climbing: false };
    expect(core.stretches(leg, angles, Array(24).fill(0), null)).toEqual([
      { from: 0, to: 7, word: 'gentle' },
      { from: 8, to: 17, word: 'steep' },
      { from: 18, to: 23, word: 'gentle' },
    ]);
  });

  it('merges a short run into its longer neighbour, and joins what then matches', () => {
    // 10 gentle, 2 steep, 10 gentle: the 2 go into a gentle and all join.
    const angles = laid([[10, 10], [35, 2], [10, 10]]);
    const leg = { from: 0, to: 21, climbing: false };
    expect(core.stretches(leg, angles, Array(22).fill(0), null)).toEqual([
      { from: 0, to: 21, word: 'gentle' },
    ]);
    // 6 gentle, 3 steep, 9 traverse: the 3 go right, into the longer.
    const mixed = laid([[10, 6], [35, 3], [35, 9]]);
    const banks = [...Array(9).fill(0), ...Array(9).fill(30)];
    expect(core.stretches({ from: 0, to: 17 }, mixed, banks, null)).toEqual([
      { from: 0, to: 5, word: 'gentle' },
      { from: 6, to: 17, word: 'traverse' },
    ]);
  });

  it('merges the shortest first', () => {
    // 8 gentle, 5 steep, 1 gentle, 8 steep: the 1 goes first (into the
    // 8 steep, the longer), then the 5 steep joins them.
    const angles = laid([[10, 8], [35, 5], [10, 1], [35, 8]]);
    expect(core.stretches({ from: 0, to: 21 }, angles, Array(22).fill(0), null)).toEqual([
      { from: 0, to: 7, word: 'gentle' },
      { from: 8, to: 21, word: 'steep' },
    ]);
  });

  it('keeps every segment in one stretch, and none under 150 m', () => {
    // A pseudo-random leg, deterministic.
    let seed = 7;
    const next = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    const angles = Array.from({ length: 400 }, () => Math.floor(next() * 45));
    const banks = Array.from({ length: 400 }, () => Math.floor(next() * 60) - 30);
    const leg = { from: 20, to: 379, climbing: true };
    const out = core.stretches(leg, angles, banks, null);
    expect(out[0].from).toBe(20);
    expect(out[out.length - 1].to).toBe(379);
    out.forEach((s, i) => {
      if (i) expect(s.from).toBe(out[i - 1].to + 1);
      if (i) expect(s.word).not.toBe(out[i - 1].word);
      expect(size(s)).toBeGreaterThanOrEqual(core.MIN_STRETCH_SEGMENTS);
    });
  });

  it('makes a leg shorter than 150 m one stretch', () => {
    const angles = laid([[10, 2], [35, 3]]);
    expect(core.stretches({ from: 0, to: 4 }, angles, Array(5).fill(0), null)).toEqual([
      { from: 0, to: 4, word: 'steep' },
    ]);
  });

  it('reads the gradient for Bootpack on a climb', () => {
    const angles = Array(12).fill(35);
    const gradients = [...Array(6).fill(10), ...Array(6).fill(30)];
    expect(core.stretches({ from: 0, to: 11, climbing: true }, angles, Array(12).fill(0), gradients))
      .toEqual([
        { from: 0, to: 5, word: 'steep' },
        { from: 6, to: 11, word: 'bootpack' },
      ]);
  });
});

describe('kickTurns (SNOW-1044)', () => {
  it('marks a change of side between two segments both banking 15° or more', () => {
    const banks = [20, -20, -15, 15, 14, -20, 30];
    expect(core.kickTurns({ from: 0, to: 6, climbing: true }, banks)).toEqual([1, 3, 6]);
  });

  it('marks nothing across an unknown bank', () => {
    expect(core.kickTurns({ from: 0, to: 2, climbing: true }, [20, null, -20])).toEqual([]);
  });

  it('marks nothing on a descent', () => {
    expect(core.kickTurns({ from: 0, to: 1, climbing: false }, [20, -20])).toEqual([]);
  });

  it('keeps to the leg', () => {
    const banks = [20, -20, 20, -20];
    expect(core.kickTurns({ from: 1, to: 2, climbing: true }, banks)).toEqual([2]);
  });
});

describe('the canonical tours (SNOW-1044)', () => {
  /**
   * The kick turns on every climbing leg of a recorded tour.
   *
   * @param {{banks: Array<?number>, legs: Array<object>}} tour
   * @returns {Object<number, Array<number>>} By leg number.
   */
  function kicksByLeg(tour) {
    return Object.fromEntries(
      tour.legs.filter((leg) => leg.climbing).map((leg) => [leg.i, core.kickTurns(leg, tour.banks)]),
    );
  }

  it('finds four kick turns on the Col de la Chaux climbs', () => {
    // SNOW-1044 quoted five from its own measurement; this record, on
    // terrain-model heights (SNOW-1043), gives these four.
    expect(kicksByLeg(chaux)).toEqual({ 2: [57, 65, 66], 4: [172], 6: [] });
  });

  it('finds none on the Backside climb', () => {
    expect(kicksByLeg(backside)).toEqual({ 2: [] });
  });

  it('cuts every leg into stretches of 150 m or more', () => {
    for (const tour of [chaux, backside]) {
      const profile = readProfile(tour.coordinates);
      const gradients = core.segmentGradients(profile, tour.angles.length, tour.distance_m);
      for (const leg of tour.legs) {
        const out = core.stretches(leg, tour.angles, tour.banks, gradients);
        expect(out[0].from).toBe(leg.from);
        expect(out[out.length - 1].to).toBe(leg.to);
        for (const s of out) {
          if (out.length > 1) expect(s.to - s.from + 1).toBeGreaterThanOrEqual(6);
        }
      }
    }
  });
});

describe('segmentGradients (SNOW-1044)', () => {
  it('reads rise over run either side of each segment', () => {
    // 81 points, 5 m apart in height, rising then falling (track()).
    const profile = readProfile(track(81));
    const n = 24;
    const out = core.segmentGradients(profile, n, profile.distanceM);
    expect(out).toHaveLength(n);
    // Early on the track climbs, late it falls, by the same slope.
    expect(out[2]).toBeGreaterThan(0);
    expect(out[21]).toBeLessThan(0);
    expect(out[2]).toBeCloseTo(-out[21], 6);
  });

  it('is null throughout for a profile with no heights', () => {
    expect(core.segmentGradients(readProfile([]), 4, 100)).toEqual([null, null, null, null]);
  });

  it('is null where a height is missing', () => {
    const coordinates = track(81).map((p, i) => (i >= 30 && i <= 50 ? [p[0], p[1], null] : p));
    const profile = readProfile(coordinates);
    const out = core.segmentGradients(profile, 24, profile.distanceM);
    expect(out[12]).toBeNull();
    expect(out[2]).not.toBeNull();
  });

  it('never measures across a gap narrower than its window', () => {
    // One missing height (point 20, ~154 m in) leaves a ~15 m gap between
    // two runs: segment 5's ±25 m window has a height at both ends, one in
    // each run, and must still read null rather than a rise nothing recorded.
    const coordinates = track(81).map((p, i) => (i === 20 ? [p[0], p[1], null] : p));
    const profile = readProfile(coordinates);
    expect(profile.runs).toHaveLength(2);
    const out = core.segmentGradients(profile, 24, profile.distanceM);
    expect(out[5]).toBeNull();
    expect(out[2]).not.toBeNull();
    expect(out[9]).not.toBeNull();
  });
});

describe('steepLength (SNOW-1044)', () => {
  it('sums the segments of 30° or more at one segment’s share', () => {
    const angles = [10, 30, 29.9, 45, null, 31];
    expect(core.steepLength({ from: 0, to: 5 }, angles, 6, 150)).toBeCloseTo(75);
    expect(core.steepLength({ from: 0, to: 2 }, angles, 6, 150)).toBeCloseTo(25);
  });

  it('is null with no slope record', () => {
    expect(core.steepLength({ from: 0, to: 5 }, [], 6, 150)).toBeNull();
    expect(core.steepLength({ from: 0, to: 5 }, null, 6, 150)).toBeNull();
  });

  it('is null when no angle on the leg is known, and zero when surveyed and gentle', () => {
    // A route wholly outside terrain coverage sends angles that are all null.
    expect(core.steepLength({ from: 0, to: 3 }, [null, null, null, null], 4, 100)).toBeNull();
    expect(core.steepLength({ from: 0, to: 1 }, [10, null, 45, 50], 4, 100)).toBe(0);
    expect(core.steepLength({ from: 1, to: 1 }, [10, null, 45, 50], 4, 100)).toBeNull();
  });
});

describe('bankSide (SNOW-1044)', () => {
  it('reads the side from the sign, none under 3° or unknown', () => {
    expect(core.bankSide(15)).toBe('right');
    expect(core.bankSide(-3)).toBe('left');
    expect(core.bankSide(2)).toBeNull();
    expect(core.bankSide(null)).toBeNull();
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

  it('gives the leg’s length, ascent and descent', () => {
    const lp = core.legProfile(profile, leg, 24, rail.clipRun);
    const figures = core.legFigures(lp, leg, 24, 620);
    expect(figures.distance_m).toBeCloseTo(310);
    expect(figures.ascent_m).toBeCloseTo(200);
    expect(figures.descent_m).toBeCloseTo(0);
    expect(Object.keys(figures).sort()).toEqual(['ascent_m', 'descent_m', 'distance_m']);
  });

  it('knows the distance alone for a leg with no elevation', () => {
    const lp = core.legProfile(readProfile([]), leg, 24, rail.clipRun);
    expect(core.legFigures(lp, leg, 24, 620)).toEqual({
      distance_m: 310,
      ascent_m: null,
      descent_m: null,
    });
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

describe('steepestBand (SNOW-1032)', () => {
  // 10 px a sample across a 100 px lane.
  const VIEW = { from: 0, to: 10 };
  const band = (from, to, classIndex) => ({ from, to, classIndex });

  it('takes the steepest band within the radius, not the one under the tap', () => {
    const bands = [band(0, 4, 0), band(5, 5, 3), band(6, 9, 0)];
    // 15 px left of the one-segment 40–45° band, inside the gentle one.
    expect(core.steepestBand(bands, 35, VIEW, 100, 22)).toBe(bands[1]);
  });

  it('ignores a steeper band beyond the radius', () => {
    const bands = [band(0, 4, 0), band(5, 5, 3), band(6, 9, 0)];
    expect(core.steepestBand(bands, 25, VIEW, 100, 22)).toBe(bands[0]);
  });

  it('breaks a tie of class by the nearer extent, then the one holding the tap', () => {
    const bands = [band(0, 1, 2), band(2, 7, 0), band(8, 9, 2)];
    expect(core.steepestBand(bands, 35, VIEW, 100, 22)).toBe(bands[0]);
    expect(core.steepestBand(bands, 65, VIEW, 100, 22)).toBe(bands[2]);
  });

  it('ranks unknown below every class', () => {
    const bands = [band(0, 4, null), band(5, 9, 0)];
    expect(core.steepestBand(bands, 45, VIEW, 100, 22)).toBe(bands[1]);
  });

  it('picks nothing with nothing in view', () => {
    expect(core.steepestBand([band(20, 30, 5)], 50, VIEW, 100, 22)).toBeNull();
    expect(core.steepestBand(undefined, 50, VIEW, 100, 22)).toBeNull();
  });
});

describe('nearestRange (SNOW-1032, SNOW-1033)', () => {
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
