/*
 * tests/js/test_day_strips_core.js — which block a point on the day strips
 * names (static/js/day_strips_core.js, SNOW-1219).
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/day_strips_core.js';

const { BLOCK_COUNT, blockAt } = self.pwaDayStripsCore;

describe('blockAt', () => {
  it('splits the plot into eight equal blocks', () => {
    expect(BLOCK_COUNT).toBe(8);
    expect(blockAt(100, 100, 320)).toBe(0);
    expect(blockAt(139.9, 100, 320)).toBe(0);
    expect(blockAt(140, 100, 320)).toBe(1);
    expect(blockAt(100 + 3.5 * 40, 100, 320)).toBe(3);
  });

  it('holds the end blocks when a drag runs off the plot', () => {
    expect(blockAt(20, 100, 320)).toBe(0);
    expect(blockAt(1000, 100, 320)).toBe(7);
    expect(blockAt(420, 100, 320)).toBe(7);
  });

  it('names nothing on a plot with no width or a bad position', () => {
    expect(blockAt(150, 100, 0)).toBeNull();
    expect(blockAt(Number.NaN, 100, 320)).toBeNull();
  });
});
