/*
 * tests/js/test_bank_ribbon_core.js — the bank's level-ski wedge geometry
 * (static/js/bank_ribbon_core.js, SNOW-1021, redrawn by SNOW-1031).
 *
 * Pure geometry: one glyph's shape (`bankWedge`), how many glyphs a width
 * holds, which segment each reads, how far each ground line rises, where the cap and the no-fill threshold
 * cut in, and where a null leaves a gap. The case most worth holding is
 * the sign — a positive roll must put the pale (downhill) wedge on the
 * right and a negative one on the left, because that side is what
 * SNOW-1021 kept the sign for.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/bank_ribbon_core.js';

const ribbon = self.pwaBankRibbonCore;
const { bankWedge, bankWedges } = ribbon;

/** An x → index conversion spreading `count` segments across `width`. */
function linear(count, width) {
  return (x) => Math.floor((x / width) * count);
}

/** One glyph for one roll, centred at x 7.5, y 27 (`bankWedge`). */
function one(roll, extra = {}) {
  return { x: 7.5, ...bankWedge(7.5, roll, { y: 27, ...extra }) };
}

/** The rise the spec asks for: 7 × 1.5 × tan|roll|, capped at 13. */
function expectedDy(roll) {
  return Math.min(13, 7 * 1.5 * Math.tan((Math.abs(roll) * Math.PI) / 180));
}

describe('constants', () => {
  it('names the pitch, width, exaggeration, cap and fill threshold', () => {
    expect(ribbon.GLYPH_PITCH).toBe(15);
    expect(ribbon.GLYPH_HALF_WIDTH).toBe(7);
    expect(ribbon.EXAGGERATION).toBe(1.5);
    expect(ribbon.CAP_PX).toBe(13);
    expect(ribbon.MIN_FILL_PX).toBe(0.6);
  });
});

describe('bankWedge', () => {
  it('draws a 0° roll flat on the centre line, with no fills', () => {
    const w = one(0);
    expect(w.dy).toBe(0);
    expect(w.ground).toEqual({ x1: 0.5, y1: 27, x2: 14.5, y2: 27 });
    expect(w.up).toBeNull();
    expect(w.down).toBeNull();
  });

  it.each([5, -5, 12, -12, 30, -30, 45, -45])('rises a %s° roll by 7·1.5·tan', (roll) => {
    const w = one(roll);
    expect(w.dy).toBeCloseTo(expectedDy(roll), 9);
    expect(w.ground.y1).toBeCloseTo(27 - w.dy, 9);
    expect(w.ground.y2).toBeCloseTo(27 + w.dy, 9);
    // The ground line pivots on the glyph's centre.
    expect((w.ground.x1 + w.ground.x2) / 2).toBeCloseTo(7.5, 9);
    expect(Math.abs(w.ground.x2 - w.ground.x1)).toBeCloseTo(14, 9);
  });

  it.each([60, -60, 80, -80, 89])('caps a %s° roll at 13 px', (roll) => {
    expect(one(roll).dy).toBe(13);
  });

  it('holds a custom cap and exaggeration', () => {
    expect(one(60, { capPx: 9 }).dy).toBe(9);
    expect(one(20, { exaggeration: 1 }).dy).toBeCloseTo(7 * Math.tan((20 * Math.PI) / 180), 9);
  });

  it('puts the pale wedge right for a positive roll and left for a negative one', () => {
    const right = one(30);
    expect(right.ground.x1).toBeLessThan(right.ground.x2);
    expect(right.down.every(([x]) => x >= right.x)).toBe(true);
    expect(right.up.every(([x]) => x <= right.x)).toBe(true);

    const left = one(-30);
    expect(left.ground.x1).toBeGreaterThan(left.ground.x2);
    expect(left.down.every(([x]) => x <= left.x)).toBe(true);
    expect(left.up.every(([x]) => x >= left.x)).toBe(true);
  });

  it('draws the uphill wedge above the skis and the downhill one below', () => {
    const w = one(30);
    expect(w.up).toEqual([[0.5, 27 - w.dy], [7.5, 27], [0.5, 27]]);
    expect(w.down).toEqual([[7.5, 27], [14.5, 27 + w.dy], [14.5, 27]]);
  });

  it('keeps the ground line but drops the fills for a tiny roll', () => {
    const w = one(1);
    expect(w.dy).toBeCloseTo(0.183, 3);
    expect(w.ground).not.toBeNull();
    expect(w.up).toBeNull();
    expect(w.down).toBeNull();
  });

  it('fills from the threshold up', () => {
    // 7 × 1.5 × tan(4°) ≈ 0.73 px, over the 0.6 px threshold.
    expect(one(4).up).not.toBeNull();
    expect(one(3).up).toBeNull();
  });
});

describe('bankWedges', () => {
  it('lays each glyph with bankWedge\'s geometry', () => {
    const [w] = bankWedges({ banks: [30], width: 15, indexAt: () => 0, y: 27 });
    const shape = bankWedge(7.5, 30, { y: 27 });
    expect(w.dy).toBe(shape.dy);
    expect(w.ground).toEqual(shape.ground);
    expect(w.up).toEqual(shape.up);
    expect(w.down).toEqual(shape.down);
  });

  it('lays one glyph per pitch, whatever the segment count', () => {
    const few = bankWedges({ banks: Array(20).fill(10), width: 600, indexAt: linear(20, 600) });
    const many = bankWedges({ banks: Array(600).fill(10), width: 600, indexAt: linear(600, 600) });
    expect(few).toHaveLength(40);
    expect(many).toHaveLength(40);
    expect(bankWedges({ banks: Array(20).fill(10), width: 600, indexAt: linear(20, 600), pitch: 12 }))
      .toHaveLength(50);
  });

  it('centres each glyph in its slot and reads the segment under it', () => {
    const wedges = bankWedges({ banks: [5, 6, 7, 8], width: 60, indexAt: linear(4, 60) });
    expect(wedges.map((w) => w.x)).toEqual([7.5, 22.5, 37.5, 52.5]);
    expect(wedges.map((w) => w.index)).toEqual([0, 1, 2, 3]);
    expect(wedges.map((w) => w.roll)).toEqual([5, 6, 7, 8]);
  });

  it('leaves a gap for a null bank rather than drawing it flat', () => {
    const wedges = bankWedges({ banks: [10, null, 10], width: 45, indexAt: linear(3, 45) });
    expect(wedges.map((w) => w.index)).toEqual([0, 2]);
  });

  it('draws nothing for an index outside the banks', () => {
    const wedges = bankWedges({ banks: [10, 10], width: 60, indexAt: (x) => x / 15 - 1.4 });
    expect(wedges.map((w) => w.index)).toEqual([0, 1]);
  });

  it('rejects a non-positive pitch and a negative width', () => {
    expect(() => bankWedges({ banks: [], width: 10, indexAt: () => 0, pitch: 0 })).toThrow(RangeError);
    expect(() => bankWedges({ banks: [], width: -1, indexAt: () => 0 })).toThrow(RangeError);
  });
});
