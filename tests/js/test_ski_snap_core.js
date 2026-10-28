/*
 * tests/js/test_ski_snap_core.js — snapping a drawn route to the ski-touring
 * network (static/js/ski_snap_core.js, SNOW-1277).
 *
 * A small network drawn as a plus sign with a long detour loop: the graph,
 * the nearest place on it, the shortest way along it, and what one tap adds
 * — routed, snapped but straight, or plain.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/ski_snap_core.js';

const core = self.pwaSkiSnapCore;

// About 76 m per 0.001° of longitude and 111 m per 0.001° of latitude here.
const LAT = 46.85;
const C = [10.05, LAT];
const W = [10.04, LAT];
const E = [10.06, LAT];
const N = [10.05, LAT + 0.01];
const FAR = [10.2, LAT];

/** A segment Feature through `coords`. */
function seg(id, ...coords) {
  return { type: 'Feature', id, geometry: { type: 'MultiLineString', coordinates: [coords] } };
}

// W—C—E along the parallel, C—N up the meridian, and a loop E→far→N.
const NETWORK = [
  seg('wc', W, C),
  seg('ce', C, E),
  seg('cn', C, [10.05, LAT + 0.005], N),
  seg('loop', E, [10.06, LAT + 0.05], [10.05, LAT + 0.05], N),
  seg('island', FAR, [10.21, LAT]),
];
const graph = core.buildGraph(NETWORK);

describe('metresPerPixel', () => {
  it('halves with each zoom level and shrinks towards the poles', () => {
    const z12 = core.metresPerPixel(0, 12);
    expect(z12).toBeCloseTo(19.11, 1);
    expect(core.metresPerPixel(0, 13)).toBeCloseTo(z12 / 2, 6);
    expect(core.metresPerPixel(60, 12)).toBeCloseTo(z12 / 2, 1);
  });
});

describe('buildGraph', () => {
  it('makes one edge per line and joins them at their shared ends', () => {
    expect(graph.edges).toHaveLength(5);
    expect(graph.adjacency.get('10.050000,46.850000')).toHaveLength(3);
  });

  it('reads a LineString and skips what is not a line', () => {
    const g = core.buildGraph([
      { geometry: { type: 'LineString', coordinates: [W, C] } },
      { geometry: { type: 'Point', coordinates: W } },
      { geometry: { type: 'LineString', coordinates: [W] } },
      {},
    ]);
    expect(g.edges).toHaveLength(1);
    expect(core.buildGraph(null).edges).toHaveLength(0);
  });
});

describe('nearestOnNetwork', () => {
  it('finds the foot of the perpendicular on the nearest line', () => {
    const snap = core.nearestOnNetwork(graph, [10.045, LAT + 0.0001], 30);
    expect(snap.point).toEqual([10.045, LAT]);
    expect(snap.distance).toBeCloseTo(11.1, 0);
    expect(graph.edges[snap.edge].from).toBe('10.040000,46.850000');
  });

  it('is null when nothing is within reach', () => {
    expect(core.nearestOnNetwork(graph, [10.045, LAT + 0.001], 30)).toBeNull();
  });
});

describe('shortestPath', () => {
  const at = (p) => core.nearestOnNetwork(graph, p, 5);

  it('runs along one line between two places on it', () => {
    const path = core.shortestPath(graph, at([10.041, LAT]), at([10.049, LAT]));
    expect(path.coordinates).toEqual([[10.041, LAT], [10.049, LAT]]);
    expect(path.lengthM).toBeCloseTo(608, -1);
  });

  it('turns at the junction rather than taking the loop', () => {
    const path = core.shortestPath(graph, at([10.055, LAT]), at([10.05, LAT + 0.008]));
    expect(path.coordinates[0]).toEqual([10.055, LAT]);
    expect(path.coordinates).toContainEqual(C);
    expect(path.coordinates.at(-1)).toEqual([10.05, 46.858]);
    expect(path.lengthM).toBeCloseTo(380 + 890, -2);
  });

  it('is null between lines that never meet', () => {
    expect(core.shortestPath(graph, at(W), at(FAR))).toBeNull();
  });
});

describe('legFor', () => {
  const reach = 30;

  it('is the tap itself with no network, or nothing near', () => {
    expect(core.legFor(null, W, [10.045, LAT], reach)).toEqual({
      points: [[10.045, LAT]], snapped: false, routed: false,
    });
    expect(core.legFor(graph, W, [10.045, LAT + 0.01], reach).snapped).toBe(false);
  });

  it('snaps a first tap, and a tap after a point off the network, straight', () => {
    expect(core.legFor(graph, null, [10.045, LAT + 0.0001], reach)).toEqual({
      points: [[10.045, LAT]], snapped: true, routed: false,
    });
    const off = core.legFor(graph, [10.045, LAT + 0.003], [10.045, LAT + 0.0001], reach);
    expect(off.routed).toBe(false);
  });

  it('routes along the network, round the corner, the start left out', () => {
    const leg = core.legFor(graph, [10.045, LAT], [10.05, LAT + 0.004], reach);
    expect(leg.routed).toBe(true);
    expect(leg.points[0]).toEqual(C);
    expect(leg.points.at(-1)).toEqual([10.05, LAT + 0.004]);
  });

  it('draws straight when the way round is over three times the distance', () => {
    // A hairpin: up one side 5.5 km, across 150 m, down the other. Its two
    // feet are 150 m apart, 11 km round.
    const hairpin = core.buildGraph([
      seg('up', [10, LAT], [10, LAT + 0.05]),
      seg('top', [10, LAT + 0.05], [10.002, LAT + 0.05]),
      seg('down', [10.002, LAT + 0.05], [10.002, LAT]),
    ]);
    const across = core.legFor(hairpin, [10, LAT + 0.001], [10.002, LAT + 0.001], reach);
    expect(across).toEqual({ points: [[10.002, LAT + 0.001]], snapped: true, routed: false });
    const round = core.legFor(hairpin, [10, LAT + 0.049], [10.002, LAT + 0.049], reach);
    expect(round.routed).toBe(true);
    expect(round.points).toContainEqual([10.002, LAT + 0.05]);
  });
});
