/*
 * tests/js/test_map_3d_flight_core.js — a fly-past's camera path (SNOW-1466).
 *
 * static/js/map_3d_flight_core.js is pure, so this pins it directly:
 *   - densifying keeps the ends, grows the distance monotonically and joins
 *     a MultiLineString's parts;
 *   - t = 0 is the establish camera and t = 1 the reveal's;
 *   - `fit` is null outside the reveal, and its weight rises 0 → 1 through
 *     it while `camera` carries the closing pitch and bearing;
 *   - the drawn line grows monotonically through follow and ends whole;
 *   - the bearing is continuous across ±180° and keeps to the turn limit on
 *     a hairpin;
 *   - the same t gives the same frame, a short route still flies, and fewer
 *     than two points throw.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_3d_flight_core.js';

const core = window.pwaMap3dFlightCore;

/** swisstopo's Breithorn ski tour, as bin/flyby/breithorn.geojson holds it. */
const BREITHORN = [[
  [7.729238, 45.93749], [7.730268, 45.934709], [7.731002, 45.933315], [7.7317, 45.93263],
  [7.733129, 45.931776], [7.733939, 45.931565], [7.735196, 45.931446], [7.738639, 45.931659],
  [7.741357, 45.932225], [7.742758, 45.932613], [7.744714, 45.933907], [7.745385, 45.934547],
  [7.746668, 45.936551], [7.747039, 45.937384], [7.747175, 45.93798], [7.747078, 45.938617],
  [7.745695, 45.940206], [7.745441, 45.940646], [7.745447, 45.940971], [7.745749, 45.941194],
  [7.746233, 45.941266], [7.74775, 45.940993],
]];

/** The frames a 30 fps, 20 s recording asks for. */
function frames(flight, n = 600) {
  return Array.from({ length: n }, (_, i) => core.flightFrame(flight, i / (n - 1)));
}

/** The smallest signed turn between two bearings. */
function turn(a, b) {
  let d = (b - a) % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return d;
}

describe('buildFlight', () => {
  it('keeps both ends and grows the distance monotonically', () => {
    const flight = core.buildFlight(BREITHORN);
    expect(flight.points[0]).toEqual(BREITHORN[0][0]);
    expect(flight.points.at(-1)).toEqual(BREITHORN[0].at(-1));
    expect(flight.points.length).toBeGreaterThan(BREITHORN[0].length);
    for (let i = 1; i < flight.cum.length; i += 1) expect(flight.cum[i]).toBeGreaterThan(flight.cum[i - 1]);
    // The tour is about 2.4 km on the ground; the spline adds little to it.
    expect(flight.length).toBeGreaterThan(2000);
    expect(flight.length).toBeLessThan(3000);
  });

  it('joins a MultiLineString’s parts end to end, a LineString alike', () => {
    const line = [[7.70, 45.90], [7.71, 45.90], [7.72, 45.90], [7.73, 45.90]];
    const multi = core.buildFlight([line.slice(0, 2), line.slice(1)]);
    const single = core.buildFlight(line);
    expect(multi.points[0]).toEqual(line[0]);
    expect(multi.points.at(-1)).toEqual(line.at(-1));
    expect(multi.length).toBeCloseTo(single.length, 3);
  });

  it('throws on fewer than two distinct points', () => {
    expect(() => core.buildFlight([])).toThrow(/at least two points/);
    expect(() => core.buildFlight([[7.7, 45.9]])).toThrow(/at least two points/);
    expect(() => core.buildFlight([[7.7, 45.9], [7.7, 45.9]])).toThrow(/at least two points/);
  });
});

describe('flightFrame', () => {
  const flight = core.buildFlight(BREITHORN);

  it('opens on the establish camera and ends on the reveal’s', () => {
    const first = core.flightFrame(flight, 0);
    expect(first.phase).toBe('establish');
    expect(first.camera.zoom).toBe(core.DEFAULTS.establish.zoom);
    expect(first.camera.pitch).toBe(core.DEFAULTS.establish.pitch);
    expect(first.drawn).toEqual([]);
    const last = core.flightFrame(flight, 1);
    expect(last.phase).toBe('reveal');
    expect(last.camera.pitch).toBe(core.DEFAULTS.reveal.pitch);
    expect(last.fit.weight).toBe(1);
    expect(last.fit.bounds).toEqual(flight.bounds);
  });

  it('runs the phases in order', () => {
    const seen = [];
    for (const frame of frames(flight)) if (seen.at(-1) !== frame.phase) seen.push(frame.phase);
    expect(seen).toEqual(['establish', 'descend', 'follow', 'reveal']);
  });

  it('gives a fit only in the reveal, rising from 0 to 1', () => {
    let weight = -1;
    for (const frame of frames(flight)) {
      if (frame.phase !== 'reveal') {
        expect(frame.fit).toBeNull();
        continue;
      }
      expect(frame.fit.weight).toBeGreaterThanOrEqual(weight);
      weight = frame.fit.weight;
    }
    expect(weight).toBe(1);
  });

  it('honours a reveal bearing, the shorter way round', () => {
    const turned = core.buildFlight(BREITHORN, { reveal: { bearingDeg: 15 } });
    const end = core.flightFrame(turned, 1).camera.bearing;
    expect(Math.abs(turn(end, 15))).toBeLessThan(1e-9);
  });

  it('draws the line monotonically through follow and ends with all of it', () => {
    let drawn = 0;
    for (const frame of frames(flight)) {
      if (frame.phase === 'establish' || frame.phase === 'descend') {
        expect(frame.drawn).toEqual([]);
        expect(frame.head).toEqual(flight.points[0]);
        continue;
      }
      expect(frame.drawn.length).toBeGreaterThanOrEqual(drawn);
      drawn = frame.drawn.length;
      if (frame.drawn.length) expect(frame.drawn.at(-1)).toEqual(frame.head);
    }
    expect(core.flightFrame(flight, 1).drawn).toEqual(flight.points);
  });

  it('moves the camera without a jump between phases', () => {
    const all = frames(flight);
    for (let i = 1; i < all.length; i += 1) {
      const [a, b] = [all[i - 1].camera, all[i].camera];
      expect(Math.abs(b.zoom - a.zoom)).toBeLessThan(0.1);
      expect(Math.abs(b.pitch - a.pitch)).toBeLessThan(1);
      expect(Math.abs(turn(a.bearing, b.bearing))).toBeLessThan(3);
      expect(core.distanceM(a.center, b.center)).toBeLessThan(150);
    }
  });

  it('keeps the bearing continuous across ±180° and to the turn limit on a hairpin', () => {
    // South, a hairpin, back north: the line's direction swings through 180°.
    const hairpin = [[7.70, 45.95], [7.70, 45.93], [7.7008, 45.9298], [7.7016, 45.93], [7.7016, 45.95]];
    const seconds = 20;
    const fps = 30;
    const hp = core.buildFlight(hairpin, { seconds });
    const all = frames(hp, seconds * fps).filter((f) => f.phase === 'follow');
    const limit = core.DEFAULTS.turnDegPerS / fps;
    for (let i = 1; i < all.length; i += 1) {
      const step = Math.abs(turn(all[i - 1].camera.bearing, all[i].camera.bearing));
      // A frame's step is the table's rate over the eased frame; allow the
      // easing's peak speed (1.5× for the cubic) and interpolation slack.
      expect(step).toBeLessThanOrEqual(limit * 1.5 + 1e-6);
    }
    // And it does turn round in the end, rather than being held forever.
    const first = all[0].camera.bearing;
    const last = all.at(-1).camera.bearing;
    expect(Math.abs(turn(first, last))).toBeGreaterThan(90);
  });

  it('gives the same frame for the same t', () => {
    for (const t of [0, 0.1, 0.33, 0.5, 0.77, 1]) {
      expect(core.flightFrame(flight, t)).toEqual(core.flightFrame(core.buildFlight(BREITHORN), t));
    }
  });

  it('flies a route shorter than the look-ahead', () => {
    const short = core.buildFlight([[7.70, 45.90], [7.7015, 45.9005]]);
    expect(short.length).toBeLessThan(core.DEFAULTS.lookAheadM);
    for (const frame of frames(short, 60)) {
      expect(Number.isFinite(frame.camera.bearing)).toBe(true);
      expect(Number.isFinite(frame.camera.center[0])).toBe(true);
    }
    expect(core.flightFrame(short, 1).drawn).toEqual(short.points);
  });

  it('clamps t outside [0, 1]', () => {
    expect(core.flightFrame(flight, -1)).toEqual(core.flightFrame(flight, 0));
    expect(core.flightFrame(flight, 2)).toEqual(core.flightFrame(flight, 1));
  });
});
