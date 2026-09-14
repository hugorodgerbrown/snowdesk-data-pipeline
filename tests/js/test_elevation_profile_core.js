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
 *
 * SNOW-960 added the slope colouring, and its half of this file asserts
 * the two things that can silently go wrong:
 *
 *   - the bands are placed by their SHARE of the track, because the
 *     sample points were walked along the full-resolution geometry and
 *     the x-axis is summed from the simplified one — so the two series
 *     agree in fractions and disagree in metres;
 *   - a class is drawn as ONE path however many pieces of the track it
 *     owns. Six hundred segments on a long tour must not become six
 *     hundred DOM nodes in a popup.
 *
 * The colour is on the STROKE only. A per-class tint under the curve was
 * built first and taken back out against two real tracks; the region is
 * one shape in the route's own colour whether the track has been sampled
 * or not, and only its opacity answers to the curve above it.
 */

import { readFileSync } from 'node:fs';

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/elevation_profile_core.js';
// The palette and the bucketing both live there, and the profile reads
// them rather than holding a second copy. Importing it is what puts
// `self.pwaRouteSlopeCore` in place; without it the chart draws
// uncoloured, which is what the trip map gets today.
import '../../static/js/route_slope_core.js';

const core = self.pwaElevationProfileCore;
const slopeCore = self.pwaRouteSlopeCore;

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

/**
 * A slope record whose sample points spread evenly along one meridian.
 *
 * N + 1 points bounding N angles, which is the pairing the wire form
 * promises. The span is a parameter because the point of the fraction
 * mapping is that it does NOT matter: a record covering a different
 * absolute distance from the profile's own must still land its
 * boundaries in the same places.
 *
 * @param {Array<number|null>} angles One per segment; null for a segment
 *   the terrain had no answer for.
 * @param {number} [spanDegrees] Latitude the sample points cover.
 * @returns {{points: Array<Array<number>>, angles: Array<number|null>}}
 *   The record, in the compact form `_compact_slope` serves.
 */
function slopeRecord(angles, spanDegrees = 0.02) {
  const points = [];
  for (let i = 0; i <= angles.length; i += 1) {
    points.push([7.0, 46.0 + (spanDegrees * i) / angles.length]);
  }
  return { points, angles };
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

describe('slopeBands — placing the classes along the track', () => {
  it('merges consecutive segments of one class into a single band', () => {
    const bands = core.slopeBands(slopeRecord([10, 12, 14, 41, 44]));

    expect(bands.map((band) => band.classIndex)).toEqual([0, 3]);
    expect(bands[0].from).toBe(0);
    expect(bands[0].to).toBeCloseTo(0.6, 6);
    expect(bands[1].from).toBeCloseTo(0.6, 6);
  });

  it('covers the track to its very end, at exactly 1', () => {
    const bands = core.slopeBands(slopeRecord([10, 41, 55]));

    // Not "close to 1": the two distance series differ in the last
    // decimal, and a band stopping at 0.9998 would leave a sliver of
    // the curve at the end of the track belonging to no class at all.
    expect(bands[bands.length - 1].to).toBe(1);
  });

  it('keeps an unknown stretch out of the gentle bucket', () => {
    const bands = core.slopeBands(slopeRecord([10, null, 10]));

    // Three bands, not one: "we looked and could not tell" is a
    // different fact from "not steep", and merging them would paint the
    // first as the second.
    expect(bands.map((band) => band.classIndex)).toEqual([0, null, 0]);
  });

  it('merges a run of consecutive unknowns into one dashed stretch', () => {
    const bands = core.slopeBands(slopeRecord([null, null, null, 41]));

    expect(bands.map((band) => band.classIndex)).toEqual([null, 3]);
  });

  it('places a band by its share of the track, never by its metres', () => {
    // The same two classes over ten times the ground. The sample points
    // were walked along the full-resolution track and the profile's
    // x-axis is summed from the simplified one, so absolute distances
    // do not line up and only the fractions can.
    const near = core.slopeBands(slopeRecord([10, 41], 0.02));
    const far = core.slopeBands(slopeRecord([10, 41], 0.2));

    expect(far).toHaveLength(near.length);
    far.forEach((band, index) => {
      expect(band.from).toBeCloseTo(near[index].from, 9);
    });
    expect(far[1].from).toBeCloseTo(0.5, 9);
  });

  it('refuses a record whose halves do not pair up', () => {
    const malformed = slopeRecord([10, 41]);
    malformed.points.pop();

    // N + 1 to N or nothing: a record off by one would place every band
    // against the wrong ground rather than fail visibly.
    expect(core.slopeBands(malformed)).toEqual([]);
  });

  it('has no bands for a route that has never been sampled', () => {
    // The property is ABSENT, not null, for an unsampled route — and an
    // unsampled route is not an unknown one, so it must draw exactly as
    // it did before the colouring existed.
    expect(core.slopeBands(undefined)).toEqual([]);
    expect(core.slopeBands(null)).toEqual([]);
  });
});

describe('buildSlopePaths — one path per class, not one per segment', () => {
  it('groups every piece of a class into a single path', () => {
    const profile = core.readProfile(track([1000, 1100, 1200, 1300, 1400]));
    const paths = core.buildSlopePaths(profile, slopeRecord([10, 41, 10, 41], 0.04));

    // Four alternating pieces, two paths. This is the property that
    // keeps a 16 km tour's ~640 segments from becoming 640 DOM nodes.
    expect(paths).toHaveLength(2);
    for (const path of paths) {
      expect(path.line.match(/M/g)).toHaveLength(2);
    }
  });

  it('returns lines and no areas at all', () => {
    const profile = core.readProfile(track([1000, 1200]));
    const paths = core.buildSlopePaths(profile, slopeRecord([10, 41]));

    // The region beneath the curve is ONE route-coloured shape from
    // buildPaths, not one tint per class: on a real tour the steep
    // classes are 25 m slivers, and tinting by class draws them as
    // hairline stripes that read as noise rather than as a place.
    for (const path of paths) expect(path).not.toHaveProperty('area');
  });

  it('orders the classes gentlest first, with the unknown last', () => {
    const profile = core.readProfile(track([1000, 1100, 1200, 1300]));
    const paths = core.buildSlopePaths(profile, slopeRecord([41, null, 10], 0.03));

    // Track order is 40° then unknown then gentle; the paths come back
    // gentle, 40°, unknown — the scale, and then the thing that is not
    // on the scale.
    expect(paths.map((path) => path.classIndex)).toEqual([0, 3, null]);
    expect(paths[2].dashed).toBe(true);
    expect(paths[0].dashed).toBe(false);
  });

  it('cuts a leg at the class boundary, not at the nearest vertex', () => {
    // One leg, two classes: the split falls halfway along it and the
    // elevation is interpolated there, so the colour changes where the
    // ground does rather than at either end of a simplified segment.
    const profile = core.readProfile(track([1000, 2000]));
    const paths = core.buildSlopePaths(profile, slopeRecord([10, 41]));

    expect(paths[0].line).toBe('M0.00 66.00 L144.00 36.00');
    expect(paths[1].line).toBe('M144.00 36.00 L288.00 6.00');
  });

  it('does not bridge an elevation gap', () => {
    const profile = core.readProfile(track([1000, 1100, null, 1300, 1400]));
    const paths = core.buildSlopePaths(profile, slopeRecord([10, 10, 10, 10], 0.04));

    // One class, but two subpaths: the gap the missing <ele> opened
    // survives the colouring rather than being closed by it.
    expect(paths).toHaveLength(1);
    expect(paths[0].line.match(/M/g)).toHaveLength(2);
  });

  it('names the same token the map line and the legend swatch use', () => {
    const profile = core.readProfile(track([1000, 1200]));
    const paths = core.buildSlopePaths(profile, slopeRecord([41]));

    expect(paths[0].token).toBe(slopeCore.CLASSES[3].token);
  });

  it('has nothing to colour for a route that has never been sampled', () => {
    const profile = core.readProfile(track([1000, 1200]));

    expect(core.buildSlopePaths(profile, undefined)).toEqual([]);
  });

  it('has nothing to colour for a track with no length', () => {
    const stationary = [
      [7.0, 46.0, 1000],
      [7.0, 46.0, 1200],
    ];

    // Every band would divide by a zero-length axis. The caller draws
    // the plain curve instead, which `buildPaths` already centres.
    expect(
      core.buildSlopePaths(core.readProfile(stationary), slopeRecord([41])),
    ).toEqual([]);
  });
});

describe('createProfileSvg — the slope colouring', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('keeps the region one route-coloured shape, whatever the classes', () => {
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1100, 1200])), {
      slope: slopeRecord([10, 31, 41, 55], 0.02),
    });
    const fills = [...svg.querySelectorAll('path')].filter(
      (path) => path.getAttribute('stroke') === 'none',
    );

    // One region for the one elevation run, in the route's own colour —
    // NOT one tint per class. Four classes are drawn on the line above it.
    expect(fills).toHaveLength(1);
    expect(fills[0].getAttribute('fill')).toBe('currentColor');
  });

  it('quietens the region when the curve above it is coloured', () => {
    const profile = core.readProfile(track([1000, 1200]));
    const opacityOf = (svg) => [...svg.querySelectorAll('path')]
      .find((path) => path.getAttribute('stroke') === 'none')
      .getAttribute('fill-opacity');

    // Same fuchsia region either way. At the strength that sits correctly
    // under a fuchsia curve it overpowers a class-coloured one, so the
    // sampled chart draws it fainter — and the unsampled chart is left
    // exactly as it was.
    expect(opacityOf(core.createProfileSvg(profile))).toBe('0.16');
    expect(opacityOf(core.createProfileSvg(profile, { slope: slopeRecord([41]) })))
      .toBe('0.06');
  });

  it('paints each class from its own custom property', () => {
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1200])), {
      slope: slopeRecord([10, 41]),
    });
    const strokes = [...svg.querySelectorAll('path')].map((path) =>
      path.getAttribute('stroke'),
    );

    // var(--token), not a hex: an SVG in the page CAN read a custom
    // property, unlike a MapLibre paint property, so it reads the one
    // the legend swatch is painted with rather than copying its value.
    expect(strokes).toContain(`var(${slopeCore.CLASSES[0].token})`);
    expect(strokes).toContain(`var(${slopeCore.CLASSES[3].token})`);
    for (const path of svg.querySelectorAll('path')) {
      const paint = `${path.getAttribute('fill')}${path.getAttribute('stroke')}`;
      expect(paint).not.toMatch(/#[0-9a-f]{3,8}/i);
    }
  });

  it('dashes the stretches the terrain had no answer for', () => {
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1200])), {
      slope: slopeRecord([null]),
    });
    const line = [...svg.querySelectorAll('path')].find(
      (path) => path.getAttribute('fill') === 'none',
    );

    expect(line.getAttribute('stroke')).toBe(`var(${slopeCore.UNKNOWN_TOKEN})`);
    // The region underneath is the route's colour, NOT the unknown grey:
    // the dashed line is what says "we looked and could not tell", and a
    // grey ground would say it a second time about the whole chart.
    // In multiples of the stroke width, so it holds the proportions of
    // routes-slope-unknown's own [2, 1.5] line-dasharray on the map.
    expect(line.getAttribute('stroke-dasharray')).toBe('3.5 2.625');
  });

  it('leaves an unsampled route exactly as it was', () => {
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1200])));

    expect(svg.getAttribute('class')).toContain('text-route-line');
    for (const path of svg.querySelectorAll('path')) {
      const paint = `${path.getAttribute('fill')}${path.getAttribute('stroke')}`;
      expect(paint).not.toMatch(/var\(/);
    }
  });

  it('names only tokens src/css/main.css actually defines', () => {
    // A var() naming a property nothing defines paints NOTHING, silently
    // — no console error, no failing assertion anywhere else. This is
    // the check that catches a typo in the palette's one shared name.
    const css = readFileSync('src/css/main.css', 'utf8');
    const svg = core.createProfileSvg(core.readProfile(track([1000, 1400])), {
      slope: slopeRecord([10, 31, 36, 41, 46, 55, null], 0.07),
    });

    const tokens = [...svg.querySelectorAll('path')]
      .map((path) => path.getAttribute('stroke'))
      .filter((stroke) => stroke.startsWith('var('))
      .map((stroke) => stroke.slice(4, -1));

    expect(tokens).toHaveLength(7);
    for (const token of tokens) expect(css).toContain(`${token}:`);
  });
});
