/*
 * tests/js/test_zoom_control_core.js — the zoom control's pure half
 * (static/js/zoom_control_core.js, SNOW-1077).
 *
 * Where a zoom sits on the scale, the whole zoom the face shows, − and +
 * disabled at the map's bounds, and ticks only for the gated layers that
 * are on.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/zoom_control_core.js';

const core = self.pwaZoomControlCore;

/** The map's range (static/js/map.js MIN_ZOOM / MAX_ZOOM). */
const MIN = 4;
const MAX = 18;

describe('scalePosition', () => {
  it('runs 0 at the minimum to 1 at the maximum', () => {
    expect(core.scalePosition(MIN, MIN, MAX)).toBe(0);
    expect(core.scalePosition(MAX, MIN, MAX)).toBe(1);
    expect(core.scalePosition(11, MIN, MAX)).toBeCloseTo(0.5);
    expect(core.scalePosition(12, MIN, MAX)).toBeCloseTo(8 / 14);
  });

  it('clamps a zoom outside the range to the nearer end', () => {
    expect(core.scalePosition(2, MIN, MAX)).toBe(0);
    expect(core.scalePosition(22, MIN, MAX)).toBe(1);
  });

  it('answers 0 for a degenerate range or a non-number', () => {
    expect(core.scalePosition(10, 10, 10)).toBe(0);
    expect(core.scalePosition(Number.NaN, MIN, MAX)).toBe(0);
  });
});

describe('faceZoom', () => {
  it('shows the tile zoom, whole', () => {
    expect(core.faceZoom(13, 13.99)).toBe(13);
  });

  it('falls back to floor(camera) with no tile zoom', () => {
    expect(core.faceZoom(null, 9.99)).toBe(9);
    expect(core.faceZoom(null, 10)).toBe(10);
  });
});

describe('buttonState', () => {
  it('disables − at the minimum and + at the maximum', () => {
    expect(core.buttonState(MIN, MIN, MAX)).toEqual({ canZoomOut: false, canZoomIn: true });
    expect(core.buttonState(MAX, MIN, MAX)).toEqual({ canZoomOut: true, canZoomIn: false });
  });

  it('treats a camera a hair short of a bound as at it', () => {
    expect(core.buttonState(MAX - 0.0001, MIN, MAX).canZoomIn).toBe(false);
    expect(core.buttonState(MIN + 0.0001, MIN, MAX).canZoomOut).toBe(false);
  });

  it('enables both in between', () => {
    expect(core.buttonState(11.5, MIN, MAX)).toEqual({ canZoomOut: true, canZoomIn: true });
  });
});

describe('ticks', () => {
  const GATES = [
    { key: 'route_slopes', zoom: 14, active: true },
    { key: 'terrain_filter', zoom: 12, active: false },
  ];

  it('keeps only the gates that are on', () => {
    expect(core.ticks(GATES, 13, MIN, MAX).map((t) => t.key)).toEqual(['route_slopes']);
  });

  it('sorts by zoom and places each on the scale', () => {
    const both = GATES.map((g) => ({ ...g, active: true }));
    const out = core.ticks(both, 13, MIN, MAX);
    expect(out.map((t) => t.key)).toEqual(['terrain_filter', 'route_slopes']);
    expect(out[0].position).toBeCloseTo(8 / 14);
    expect(out[1].position).toBeCloseTo(10 / 14);
  });

  it('says whether the camera has reached each gate', () => {
    const both = GATES.map((g) => ({ ...g, active: true }));
    const out = core.ticks(both, 13, MIN, MAX);
    expect(out.map((t) => t.reached)).toEqual([true, false]);
    expect(core.ticks(both, 14, MIN, MAX).every((t) => t.reached)).toBe(true);
  });

  it('drops a gate outside the range, and survives a missing list', () => {
    expect(core.ticks([{ key: 'x', zoom: 20, active: true }], 13, MIN, MAX)).toEqual([]);
    expect(core.ticks(null, 13, MIN, MAX)).toEqual([]);
  });
});
