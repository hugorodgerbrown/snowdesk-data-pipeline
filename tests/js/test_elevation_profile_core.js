/*
 * tests/js/test_elevation_profile_core.js — the saved-route elevation
 * profile (static/js/elevation_profile_core.js).
 *
 * The module's whole contract is that it shows the GPX's OWN elevation and
 * nothing else, so most of what is asserted here is restraint rather than
 * arithmetic:
 *
 *   - a missing `<ele>` BREAKS the line instead of being bridged. Drawing
 *     a plausible straight segment across a gap would put terrain on the
 *     screen that the source file never recorded — the same class of lie
 *     `Route.ascent_m`'s null exists to prevent;
 *   - distance keeps accumulating across a gap, so the runs either side
 *     sit at their true along-track positions rather than butting together;
 *   - a track with no elevation at all draws NOTHING, rather than a flat
 *     line at zero;
 *   - 0 is a legitimate elevation and a legitimate range, and neither is
 *     treated as absent.
 *
 * The chart's totals are deliberately not tested, because it computes
 * none: `distance_m` / `ascent_m` / `descent_m` are the server's figures,
 * measured on the full-resolution track, and re-deriving them from the
 * simplified geometry the client holds would put two different numbers
 * for one route in the same popup.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/elevation_profile_core.js';

const core = self.pwaElevationProfileCore;

/** One degree of latitude is ~111.19 km; these fixtures step 0.01°. */
const LEG_M = 1111.9508023352598;

/**
 * A track climbing one meridian, one 0.01° step per point.
 *
 * @param {Array<number|null>} elevations One entry per point.
 * @returns {Array<Array<number|null>>} `[lon, lat, ele]` triples.
 */
function track(elevations) {
  return elevations.map((ele, i) => [7.0, 46.0 + i * 0.01, ele]);
}

describe('readProfile — walking the coordinates', () => {
  it('accumulates along-track distance for the x-axis', () => {
    const profile = core.readProfile(track([1000, 1100, 1200]));

    expect(profile.distanceM).toBeCloseTo(2 * LEG_M, 3);
    expect(profile.runs[0].map((p) => p.e)).toEqual([1000, 1100, 1200]);
    expect(profile.runs[0][1].d).toBeCloseTo(LEG_M, 3);
  });

  it('reports the elevation extremes', () => {
    const profile = core.readProfile(track([1500, 1200, 1900, 1600]));

    expect(profile.minEle).toBe(1200);
    expect(profile.maxEle).toBe(1900);
    expect(profile.hasElevation).toBe(true);
  });

  it('breaks the series into runs at a missing elevation', () => {
    const profile = core.readProfile(track([1000, 1100, null, 1300, 1400]));

    expect(profile.runs).toHaveLength(2);
    expect(profile.runs[0].map((p) => p.e)).toEqual([1000, 1100]);
    expect(profile.runs[1].map((p) => p.e)).toEqual([1300, 1400]);
  });

  it('does not interpolate an elevation across the gap', () => {
    const profile = core.readProfile(track([1000, null, 3000]));
    const every = profile.runs.flat().map((p) => p.e);

    // The two known readings, and nothing invented between them.
    expect(every).toEqual([1000, 3000]);
  });

  it('keeps counting distance through a gap, so the runs stay in place', () => {
    const profile = core.readProfile(track([1000, null, 1300]));

    // The third point is two legs along the track even though the second
    // contributed no elevation. Butting the runs together would draw the
    // far side of the gap at the wrong place on the axis.
    expect(profile.runs[1][0].d).toBeCloseTo(2 * LEG_M, 3);
  });

  it('treats a track with no elevation at all as having no profile', () => {
    const profile = core.readProfile(track([null, null, null]));

    expect(profile.hasElevation).toBe(false);
    expect(profile.runs).toEqual([]);
    // The horizontal maths is still done — only the vertical is unknown.
    expect(profile.distanceM).toBeCloseTo(2 * LEG_M, 3);
  });

  it('treats a position with no third slot as a gap, not a crash', () => {
    const profile = core.readProfile([
      [7.0, 46.0],
      [7.0, 46.01],
    ]);

    expect(profile.hasElevation).toBe(false);
  });

  it('accepts zero as a real elevation', () => {
    const profile = core.readProfile(track([0, 50, 0]));

    expect(profile.hasElevation).toBe(true);
    expect(profile.minEle).toBe(0);
  });

  it('skips a malformed coordinate rather than plotting NaN', () => {
    const profile = core.readProfile([
      [7.0, 46.0, 1000],
      ['nonsense', 46.01, 1100],
      [7.0, 46.02, 1200],
    ]);

    expect(profile.runs.flat().map((p) => p.e)).toEqual([1000, 1200]);
    expect(Number.isFinite(profile.distanceM)).toBe(true);
  });

  it('returns the empty profile for junk input', () => {
    for (const input of [null, undefined, [], 'not a track']) {
      expect(core.readProfile(input).hasElevation).toBe(false);
    }
  });
});

describe('buildPaths — projecting into the viewBox', () => {
  /**
   * Pull every `[ML]x y` vertex out of a path `d` string.
   *
   * @param {string} d The path data.
   * @returns {Array<{x: number, y: number}>} The vertices in order.
   */
  function vertices(d) {
    return [...d.matchAll(/[ML]([\d.]+) ([\d.]+)/g)].map((m) => ({
      x: Number(m[1]),
      y: Number(m[2]),
    }));
  }

  it('spans the full width and inverts the y-axis', () => {
    const paths = core.buildPaths(core.readProfile(track([1000, 2000])));
    const points = vertices(paths[0].line);

    expect(paths).toHaveLength(1);
    expect(points[0].x).toBeCloseTo(0, 5);
    expect(points[1].x).toBeCloseTo(core.VIEWBOX.width, 5);
    // SVG y grows downward, so the higher point must sit at the SMALLER y.
    expect(points[0].y).toBeGreaterThan(points[1].y);
  });

  it('places a mid-track point at its true distance along the axis', () => {
    // Three equal legs: the middle vertex belongs at the halfway mark.
    const paths = core.buildPaths(core.readProfile(track([1000, 1500, 2000])));
    const points = vertices(paths[0].line);

    expect(points[1].x).toBeCloseTo(core.VIEWBOX.width / 2, 1);
  });

  it('keeps every vertex inside the viewBox', () => {
    const paths = core.buildPaths(core.readProfile(track([1200, 900, 2400, 1800])));

    for (const point of vertices(paths[0].line)) {
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(core.VIEWBOX.height);
    }
  });

  it('emits one path pair per run, so a gap is a real hole', () => {
    const paths = core.buildPaths(core.readProfile(track([1000, 1100, null, 1300, 1400])));

    expect(paths).toHaveLength(2);
    // Each area closes on itself rather than spanning the gap.
    expect(paths[0].area.endsWith('Z')).toBe(true);
    expect(paths[1].area.endsWith('Z')).toBe(true);
  });

  it('drops a single-point run, which has no line to draw', () => {
    // 1000 alone, gap, then a drawable pair.
    const paths = core.buildPaths(core.readProfile(track([1000, null, 1300, 1400])));

    expect(paths).toHaveLength(1);
  });

  it('centres a track with no vertical range instead of dividing by zero', () => {
    const paths = core.buildPaths(core.readProfile(track([1500, 1500, 1500])));

    const ys = [...paths[0].line.matchAll(/[ML][\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));
    expect(new Set(ys).size).toBe(1);
    expect(ys[0]).toBeCloseTo(core.VIEWBOX.height / 2, 5);
  });

  it('survives a zero-length track without producing NaN', () => {
    const stationary = [
      [7.0, 46.0, 1000],
      [7.0, 46.0, 1200],
    ];
    const paths = core.buildPaths(core.readProfile(stationary));

    expect(paths[0].line).not.toMatch(/NaN/);
  });

  it('draws nothing for a profile with no elevation', () => {
    expect(core.buildPaths(core.readProfile(track([null, null])))).toEqual([]);
  });
});

describe('createProfileSvg — the element', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('returns an svg carrying one area and one line per run', () => {
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1100, 1300])));

    expect(svg.tagName.toLowerCase()).toBe('svg');
    expect(svg.querySelectorAll('path')).toHaveLength(2);
    expect(svg.getAttribute('viewBox')).toBe(
      `0 0 ${core.VIEWBOX.width} ${core.VIEWBOX.height}`,
    );
  });

  it('takes its colour from a design token, never a literal', () => {
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1200])));

    // --color-route-line is the same token map.js paints the track with,
    // so the chart and the line on the map read as one object.
    expect(svg.getAttribute('class')).toContain('text-route-line');
    for (const path of svg.querySelectorAll('path')) {
      const paint = `${path.getAttribute('fill')}${path.getAttribute('stroke')}`;
      expect(paint).not.toMatch(/#[0-9a-f]{3,8}/i);
    }
  });

  it('uses the caller-supplied label as its accessible name', () => {
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1200])), {
      label: 'Profil altimétrique',
    });

    expect(svg.getAttribute('role')).toBe('img');
    expect(svg.getAttribute('aria-label')).toBe('Profil altimétrique');
  });

  it('returns null when the track has no elevation, so nothing is mounted', () => {
    expect(core.createProfileSvg(core.readProfile(track([null, null])))).toBeNull();
  });
});
