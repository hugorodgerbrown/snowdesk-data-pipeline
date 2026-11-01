/*
 * tests/js/test_route_leader_core.js — the leader line's path
 * (static/js/route_leader_core.js, SNOW-1019).
 *
 * One cubic per segment with vertical tangents at both ends, two or three
 * stops, and nothing at all for fewer than two.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/route_leader_core.js';

const { leaderPath, swingPath } = self.pwaRouteLeaderCore;

describe('leaderPath', () => {
  it('bends once between two stops, leaving and arriving vertically', () => {
    expect(leaderPath([{ x: 10, y: 0 }, { x: 50, y: 100 }]))
      .toBe('M10 0 C10 50, 50 50, 50 100');
  });

  it('runs on through a third stop, one cubic per segment', () => {
    const d = leaderPath([{ x: 10, y: 0 }, { x: 50, y: 100 }, { x: 30, y: 140 }]);

    expect(d).toBe('M10 0 C10 50, 50 50, 50 100 C50 120, 30 120, 30 140');
  });

  it('turns the bend on its side for the x axis', () => {
    expect(leaderPath([{ x: 0, y: 10 }, { x: 100, y: 50 }], 'x'))
      .toBe('M0 10 C50 10, 50 50, 100 50');
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

/**
 * Points along an M / L / C path, `steps` per segment.
 *
 * @param {string} d
 * @param {number} [steps]
 * @returns {Array<{x: number, y: number}>}
 */
function sample(d, steps = 50) {
  const out = [];
  let at = null;
  for (const [, cmd, args] of d.matchAll(/([MLC])([^MLC]*)/g)) {
    const n = args.split(/[ ,]+/).filter(Boolean).map(Number);
    if (cmd === 'M') {
      at = { x: n[0], y: n[1] };
      out.push(at);
      continue;
    }
    const end = { x: n[n.length - 2], y: n[n.length - 1] };
    for (let i = 1; i <= steps; i += 1) {
      const t = i / steps;
      if (cmd === 'L') {
        out.push({ x: at.x + (end.x - at.x) * t, y: at.y + (end.y - at.y) * t });
        continue;
      }
      const u = 1 - t;
      const b = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
      out.push({
        x: b[0] * at.x + b[1] * n[0] + b[2] * n[2] + b[3] * n[4],
        y: b[0] * at.y + b[1] * n[1] + b[2] * n[3] + b[3] * n[5],
      });
    }
    at = end;
  }
  return out;
}

describe('swingPath (SNOW-1354)', () => {
  // A panel pinned top-left, its notch on its bottom edge.
  const panel = { left: 12, top: 12, right: 412, bottom: 300 };
  const notch = { x: 120, y: 300 };
  // Strictly inside: the notch itself sits on the edge.
  const inside = (p) => p.x > panel.left && p.x < panel.right
    && p.y > panel.top && p.y < panel.bottom - 0.01;

  it('never enters the panel, from a dot beside it and above its bottom', () => {
    for (const start of [{ x: 500, y: 120 }, { x: 413, y: 20 }, { x: 420, y: 290 }]) {
      const points = sample(swingPath(start, notch, 40));
      expect(points.filter(inside)).toEqual([]);
      expect(points[0]).toEqual(start);
      expect(points[points.length - 1].x).toBeCloseTo(notch.x);
      expect(points[points.length - 1].y).toBeCloseTo(notch.y);
    }
  });

  it('drops straight down, turns, and runs 40 px under the lower end', () => {
    expect(swingPath({ x: 500, y: 120 }, notch, 40))
      .toBe('M500 120 L500 320 C500 331.05, 491.05 340, 480 340 C120 340, 120 340, 120 300');
  });

  it('arrives at the notch going up', () => {
    const points = sample(swingPath({ x: 500, y: 120 }, notch, 40));
    const last = points[points.length - 1];
    const before = points[points.length - 2];
    expect(before.y).toBeGreaterThan(last.y);
    expect(before.x).toBeCloseTo(last.x, 1);
  });

  it('runs under a dot that is the lower end', () => {
    expect(swingPath({ x: 500, y: 310 }, notch, 40)).toMatch(/ 350 C120 350, 120 350, 120 300$/);
  });

  it('draws nothing with an end missing', () => {
    expect(swingPath(null, { x: 1, y: 1 }, 40)).toBe('');
    expect(swingPath({ x: NaN, y: 1 }, { x: 1, y: 1 }, 40)).toBe('');
  });
});
