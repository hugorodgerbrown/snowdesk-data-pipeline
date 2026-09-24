/*
 * tests/js/test_bank_ribbon_core.js — the bank ribbon's tick geometry
 * (static/js/bank_ribbon_core.js, SNOW-1021).
 *
 * Pure geometry: how many ticks a width holds, which segment each reads,
 * how far each leans, and where a null leaves a gap. The case most worth
 * holding is the sign — alternating rolls must alternate the lean, because
 * that alternation is the switchback pattern SNOW-1021 kept the sign for.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/bank_ribbon_core.js';

const { bankTicks } = self.pwaBankRibbonCore;

/** The lean of a tick from vertical, in degrees, positive to the right. */
function lean(tick) {
  return (Math.atan2(tick.x2 - tick.x1, tick.y1 - tick.y2) * 180) / Math.PI;
}

/** An x → index conversion spreading `count` segments across `width`. */
function linear(count, width) {
  return (x) => Math.floor((x / width) * count);
}

describe('bankTicks', () => {
  it('lays one tick per pitch, whatever the segment count', () => {
    const few = bankTicks({ banks: Array(20).fill(10), width: 600, indexAt: linear(20, 600) });
    const many = bankTicks({ banks: Array(600).fill(10), width: 600, indexAt: linear(600, 600) });
    expect(few).toHaveLength(75);
    expect(many).toHaveLength(75);
    expect(bankTicks({ banks: Array(20).fill(10), width: 600, indexAt: linear(20, 600), pitch: 12 }))
      .toHaveLength(50);
  });

  it('centres each tick in its slot and reads the segment under it', () => {
    const ticks = bankTicks({ banks: [5, 6, 7, 8], width: 32, indexAt: linear(4, 32) });
    expect(ticks.map((tick) => tick.x)).toEqual([4, 12, 20, 28]);
    expect(ticks.map((tick) => tick.index)).toEqual([0, 1, 2, 3]);
    expect(ticks.map((tick) => tick.roll)).toEqual([5, 6, 7, 8]);
  });

  it.each([0, 12, -12, 40, -40])('leans a %s° roll by that many degrees', (roll) => {
    const [tick] = bankTicks({ banks: [roll], width: 8, indexAt: () => 0, y: 20 });
    expect(lean(tick)).toBeCloseTo(roll, 9);
    expect(Math.hypot(tick.x2 - tick.x1, tick.y2 - tick.y1)).toBeCloseTo(18, 9);
    expect((tick.y1 + tick.y2) / 2).toBeCloseTo(20, 9);
  });

  it('leans a positive roll right and a negative one left (SNOW-1021)', () => {
    const ticks = bankTicks({ banks: [30, -30, 30, -30], width: 32, indexAt: linear(4, 32) });
    expect(ticks.map((tick) => Math.sign(tick.x2 - tick.x))).toEqual([1, -1, 1, -1]);
    expect(ticks.every((tick) => tick.y2 < tick.y1)).toBe(true);
  });

  it('leaves a gap for a null bank rather than drawing it flat', () => {
    const ticks = bankTicks({ banks: [10, null, 10], width: 24, indexAt: linear(3, 24) });
    expect(ticks.map((tick) => tick.index)).toEqual([0, 2]);
  });

  it('draws nothing for an index outside the banks', () => {
    const ticks = bankTicks({ banks: [10, 10], width: 32, indexAt: (x) => x / 8 - 1.4 });
    expect(ticks.map((tick) => tick.index)).toEqual([0, 1]);
  });

  it('marks a tick strong at the threshold, on either side', () => {
    const ticks = bankTicks({ banks: [24, 25, -25, -24], width: 32, indexAt: linear(4, 32) });
    expect(ticks.map((tick) => tick.strong)).toEqual([false, true, true, false]);
    const custom = bankTicks({ banks: [15], width: 8, indexAt: () => 0, strongDeg: 15 });
    expect(custom[0].strong).toBe(true);
  });

  it('rejects a non-positive pitch and a negative width', () => {
    expect(() => bankTicks({ banks: [], width: 10, indexAt: () => 0, pitch: 0 })).toThrow(RangeError);
    expect(() => bankTicks({ banks: [], width: -1, indexAt: () => 0 })).toThrow(RangeError);
  });
});
