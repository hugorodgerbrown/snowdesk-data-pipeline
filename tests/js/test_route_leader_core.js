/*
 * tests/js/test_route_leader_core.js — the leader line's path
 * (static/js/route_leader_core.js, SNOW-1019).
 *
 * One cubic per segment with vertical tangents at both ends, two or three
 * stops, and nothing at all for fewer than two.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/route_leader_core.js';

const { leaderPath } = self.pwaRouteLeaderCore;

describe('leaderPath', () => {
  it('bends once between two stops, leaving and arriving vertically', () => {
    expect(leaderPath([{ x: 10, y: 0 }, { x: 50, y: 100 }]))
      .toBe('M10 0 C10 50, 50 50, 50 100');
  });

  it('runs on through a third stop, one cubic per segment', () => {
    const d = leaderPath([{ x: 10, y: 0 }, { x: 50, y: 100 }, { x: 30, y: 140 }]);

    expect(d).toBe('M10 0 C10 50, 50 50, 50 100 C50 120, 30 120, 30 140');
  });

  it('draws nothing for fewer than two stops', () => {
    expect(leaderPath([])).toBe('');
    expect(leaderPath([{ x: 1, y: 2 }])).toBe('');
    expect(leaderPath(null)).toBe('');
  });

  it('skips a stop with no position', () => {
    expect(leaderPath([{ x: 0, y: 0 }, null, { x: 0, y: 10 }])).toBe('M0 0 C0 5, 0 5, 0 10');
    expect(leaderPath([{ x: 0, y: 0 }, { x: NaN, y: 3 }])).toBe('');
  });

  it('trims coordinates to two decimals', () => {
    expect(leaderPath([{ x: 0.123456, y: 0 }, { x: 1, y: 1 }])).toBe(
      'M0.12 0 C0.12 0.5, 1 0.5, 1 1',
    );
  });
});
