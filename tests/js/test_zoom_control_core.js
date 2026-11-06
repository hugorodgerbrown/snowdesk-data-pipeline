/*
 * tests/js/test_zoom_control_core.js — the zoom control's pure half
 * (static/js/zoom_control_core.js, SNOW-1077).
 *
 * Where a zoom sits on the scale, the whole zoom the face shows, − and +
 * disabled at the map's bounds, a tick for every gated layer, and where
 * each tick's label and connector sit.
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
  it('shows the camera zoom, rounded down', () => {
    expect(core.faceZoom(13.99)).toBe(13);
    expect(core.faceZoom(10)).toBe(10);
  });

  it('does not clamp to the basemap\'s tiles: camera 15.6 reads 15', () => {
    expect(core.faceZoom(15.6)).toBe(15);
  });

  it('counts a camera a hair short of a level as at it', () => {
    expect(core.faceZoom(9.9999)).toBe(10);
  });

  it('answers 0 for a non-number', () => {
    expect(core.faceZoom(Number.NaN)).toBe(0);
  });
});

describe('enlargedFrom', () => {
  it('is the level after the tiles\' maximum', () => {
    expect(core.enlargedFrom(14, MAX)).toBe(15);
  });

  it('is null with no tile maximum, or one the map cannot zoom past', () => {
    expect(core.enlargedFrom(null, MAX)).toBeNull();
    expect(core.enlargedFrom(18, MAX)).toBeNull();
    expect(core.enlargedFrom(22, MAX)).toBeNull();
  });
});

describe('isEnlarged', () => {
  it('holds from the level after the tiles\' maximum', () => {
    expect(core.isEnlarged(14.9, 14)).toBe(false);
    expect(core.isEnlarged(15, 14)).toBe(true);
    expect(core.isEnlarged(15.6, 14)).toBe(true);
  });

  it('treats a fractional camera over the last tiles as ordinary', () => {
    expect(core.isEnlarged(14.6, 14)).toBe(false);
  });

  it('never holds without a tile maximum', () => {
    expect(core.isEnlarged(18, null)).toBe(false);
  });
});

describe('markerZoom', () => {
  it('gives the camera to one decimal, rounded down to agree with the face', () => {
    expect(core.markerZoom(12.37)).toBe(12.3);
    expect(core.markerZoom(12.97)).toBe(12.9);
    expect(core.markerZoom(14)).toBe(14);
  });

  it('counts a camera a hair short of a tenth as at it', () => {
    expect(core.markerZoom(12.39999)).toBe(12.4);
  });

  it('answers 0 for a non-number', () => {
    expect(core.markerZoom(Number.NaN)).toBe(0);
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

  it('keeps every gate, switched on or not', () => {
    expect(core.ticks(GATES, 13, MIN, MAX).map((t) => t.key))
      .toEqual(['terrain_filter', 'route_slopes']);
  });

  it('holds an up-to gate through its whole zoom, and not past it', () => {
    const gate = [{ key: 'region_select', zoom: 10, active: true, upTo: true }];
    expect(core.ticks(gate, 8, MIN, MAX)[0]).toMatchObject({ upTo: true, reached: true });
    expect(core.ticks(gate, 10.9, MIN, MAX)[0].reached).toBe(true);
    expect(core.ticks(gate, 11, MIN, MAX)[0].reached).toBe(false);
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

  it('numbers every whole level in the range, lowest first', () => {
    const out = core.levels(MIN, MAX);
    expect(out.map((l) => l.zoom)).toEqual([4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]);
    expect(out[0].position).toBe(0);
    expect(out.at(-1).position).toBe(1);
    expect(core.levels(NaN, MAX)).toEqual([]);
  });

  it('orders a from gate before an up-to gate on a shared level', () => {
    const shared = [
      { key: 'region_select', zoom: 10, upTo: true },
      { key: 'ski_routes', zoom: 10 },
    ];
    expect(core.ticks(shared, 11, MIN, MAX).map((t) => t.key))
      .toEqual(['ski_routes', 'region_select']);
  });

  it('drops a gate outside the range, and survives a missing list', () => {
    expect(core.ticks([{ key: 'x', zoom: 20, active: true }], 13, MIN, MAX)).toEqual([]);
    expect(core.ticks(null, 13, MIN, MAX)).toEqual([]);
  });
});

// SNOW-1280: the layers menu dims a row by isReached.
describe('isReached', () => {
  it('holds a from gate from its zoom up, a hair early', () => {
    const gate = { key: 'ski_routes', zoom: 10 };
    expect(core.isReached(gate, 9.5)).toBe(false);
    expect(core.isReached(gate, 9.9999)).toBe(true);
    expect(core.isReached(gate, 12)).toBe(true);
  });

  it('holds an up-to gate through its whole zoom', () => {
    const gate = { key: 'region_select', zoom: 10, upTo: true };
    expect(core.isReached(gate, 10.9)).toBe(true);
    expect(core.isReached(gate, 11)).toBe(false);
  });
});

// 2026-11-02: the labels sit in their own column, tied to their levels by
// curves, so a crowded or shared level spreads its labels apart.
describe('labelRows', () => {
  const H = 280;
  const PITCH = 30;
  const at = (zoom) => ({ position: core.scalePosition(zoom, MIN, MAX) });

  it('leaves a label with room level with its gate', () => {
    expect(core.labelRows([at(10), at(14)], H, PITCH)).toEqual([160, 80]);
  });

  it('spreads a crowded run evenly, centred on where it wants to be', () => {
    const rows = core.labelRows([at(9), at(10), at(11), at(12), at(14)], H, PITCH);
    // Five labels want 180, 160, 140, 120 and 80: one group, mean 136.
    expect(rows).toEqual([196, 166, 136, 106, 76]);
  });

  it('fans two gates on one level either side of it, the first above', () => {
    const rows = core.labelRows([at(10), at(10)], H, PITCH);
    expect(rows).toEqual([145, 175]);
  });

  it('never lets two labels overlap, nor leave the scale', () => {
    const many = [4, 4, 4, 5, 18, 18].map(at);
    const rows = core.labelRows(many, H, PITCH);
    const sorted = [...rows].sort((a, b) => a - b);
    for (let i = 1; i < sorted.length; i += 1) {
      expect(sorted[i] - sorted[i - 1]).toBeGreaterThanOrEqual(PITCH - 1e-9);
    }
    expect(sorted[0]).toBeGreaterThanOrEqual(PITCH / 2);
    expect(sorted.at(-1)).toBeLessThanOrEqual(H - PITCH / 2);
  });

  it('answers nothing for no ticks', () => {
    expect(core.labelRows([], H, PITCH)).toEqual([]);
    expect(core.labelRows(null, H, PITCH)).toEqual([]);
  });
});

describe('connectorPath', () => {
  it('leaves the axis and meets the label horizontally', () => {
    expect(core.connectorPath(36, 180, 96, 196)).toBe('M36 180 C69 180 69 196 96 196');
  });

  it('is a straight line for a label level with its gate', () => {
    expect(core.connectorPath(36, 80, 96, 80)).toBe('M36 80 C69 80 69 80 96 80');
  });
});
