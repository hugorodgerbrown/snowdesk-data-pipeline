/*
 * static/js/route_rail_core.js — rail one's pure half: the whole route's
 * profile, cut at its transitions (SNOW-1018).
 *
 * The rail sits below the map while a route is open. It draws the route's
 * elevation profile as ONE FILLED SHAPE PER LEG — a leg being the stretch
 * between two transitions (apps/routes/services/legs.py) — under one
 * stroked outline of the whole track, with distance ticks along its foot
 * and the route's figures beside it. This module is the arithmetic of that
 * drawing and nothing else: no DOM, no globals read at parse time, so every
 * rule below is covered in tests/js/test_route_rail_core.js.
 * static/js/route_rail.js is the DOM half.
 *
 * ## Where a leg sits on the x-axis
 *
 * A leg arrives as `{i, from, to, climbing}` where `from` / `to` are SAMPLE
 * indices into `properties.slope.angles`, both inclusive — the server
 * converted them from point indices (apps/routes/services/leg_wire.py).
 * The profile's x-axis is summed distance along the simplified geometry,
 * which is a different length again, so a sample index is placed BY SHARE:
 * segment i owns the i-th of N equal shares of the track. That is the rule
 * `slopeBands` in elevation_profile_core.js argues for at length, and it is
 * the same rule here so the rail and the detail sheet's chart can never put
 * the same segment in two places.
 *
 * ## The tick step
 *
 * `niceStep` picks the smallest of 25 / 50 / 100 / 200 / 250 / 500 / 1000 /
 * 2000 / 5000 m that leaves at most 15 minor ticks strictly inside the
 * strip. Majors fall on a coarser step from `MAJOR_STEP`, and every label
 * on the strip is in ONE unit — metres when the majors are under a
 * kilometre apart, kilometres otherwise — so a strip never reads "500 m,
 * 1 km, 1.5 km".
 *
 * ## The figures line
 *
 * `formatFigures` is the ONE formatter for `distance · ▲ascent · ▼descent ·
 * start→end`, taken by the route today and by a leg on rail two
 * (SNOW-1019), so the two lines cannot drift apart. A null figure is
 * OMITTED, never shown as zero: a route whose GPX carried no elevation has
 * an unknown ascent, not a flat one (Route.ascent_m's docstring).
 *
 * Exports (frozen `self.pwaRouteRailCore`):
 *
 *   niceStep(spanM)                          → metres between minor ticks
 *   majorStep(spanM)                         → metres between labelled ticks
 *   tickUnit(spanM)                          → 'm' or 'km', once per strip
 *   ticks(spanM, units?)                     → [{d, major, label}]
 *   formatFigures(figures, strings?)         → the figures line
 *   legSpan(leg, sampleCount, distanceM)     → [startM, endM] on the profile
 *   clipRun(run, start, end)                 → a run clipped to [start, end],
 *                                              its ends interpolated (rail
 *                                              two clips its leg with it)
 *   legPaths(profile, legs, sampleCount, box) → one fill per leg + outline
 *   legAt(fraction, legs, sampleCount)       → the leg under an x fraction
 *   profileY(profile, d, box?)               → the outline's y at distance d
 *   BOX                                      → the lane's user-space box
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {{i: number, from: number, to: number, climbing: boolean}} Leg
   *   A leg in sample indices, both ends inclusive.
   */

  /**
   * @typedef {{d: number, e: number}} ProfilePoint
   * @typedef {{
   *   runs: Array<Array<ProfilePoint>>,
   *   distanceM: number,
   *   minEle: ?number,
   *   maxEle: ?number,
   *   hasElevation: boolean,
   * }} Profile
   *   A `pwaElevationProfileCore.readProfile` result.
   */

  /**
   * @typedef {{width: number, height: number}} Box
   */

  /** The steps a minor tick may take, in metres, smallest first. */
  var STEPS = [25, 50, 100, 200, 250, 500, 1000, 2000, 5000];

  /** At most this many minor ticks strictly inside the strip. */
  var MAX_MINORS = 15;

  /**
   * The labelled step for each minor step, in metres.
   *
   * Every major is a whole number of minors, so a label always sits on a
   * tick. From 1000 up every major is a whole number of kilometres, which is
   * what lets the strip carry kilometre labels without decimals.
   *
   * @type {Object<number, number>}
   */
  var MAJOR_STEP = {
    25: 100,
    50: 100,
    100: 500,
    200: 1000,
    250: 1000,
    500: 1000,
    1000: 5000,
    2000: 10000,
    5000: 10000,
  };

  /**
   * The lane's user-space box. The element is drawn with
   * `preserveAspectRatio="none"` across whatever width the rail has, so this
   * is a coordinate system rather than a pixel size; strokes carry
   * `vector-effect: non-scaling-stroke` so the stretch does not thicken them.
   */
  var BOX = Object.freeze({ width: 1000, height: 96 });

  /** Vertical inset above the highest point and below the lowest. */
  var PAD_Y = 6;

  /** The English units, the fallback when no strings are passed. */
  var DEFAULT_UNITS = Object.freeze({ m: '%(value)s m', km: '%(value)s km' });

  /** The English figure templates, the fallback when no strings are passed. */
  var DEFAULT_FIGURES = Object.freeze({
    'figure-distance': '%(km)s km',
    'figure-ascent': '▲%(m)s m',
    'figure-descent': '▼%(m)s m',
    'figure-range': '%(start)s→%(end)s m',
    'figure-separator': ' · ',
  });

  /**
   * Substitute `%(name)s` placeholders by name.
   *
   * The same rule as i18n_strings.js's `interpolate`, restated so this
   * module stays free of globals: a locale may reorder the placeholders, so
   * substitution is by name and never by position.
   *
   * @param {string} template The string with placeholders.
   * @param {Object<string, string>} params The values.
   * @returns {string}
   */
  function interpolate(template, params) {
    return String(template).replace(/%\((\w+)\)s/g, function (whole, name) {
      return Object.prototype.hasOwnProperty.call(params, name) ? params[name] : whole;
    });
  }

  /**
   * The metres between minor ticks for a strip `spanM` long.
   *
   * The smallest step that leaves at most `MAX_MINORS` ticks strictly
   * inside the strip — neither the start nor a tick landing exactly on the
   * end is counted. A strip too long for even the largest step takes the
   * largest; a strip with no length takes the smallest.
   *
   * @param {number} spanM The strip's length in metres.
   * @returns {number}
   */
  function niceStep(spanM) {
    if (!(spanM > 0)) return STEPS[0];
    for (var i = 0; i < STEPS.length; i += 1) {
      var interior = Math.ceil(spanM / STEPS[i]) - 1;
      if (interior <= MAX_MINORS) return STEPS[i];
    }
    return STEPS[STEPS.length - 1];
  }

  /**
   * The metres between labelled ticks.
   *
   * @param {number} spanM The strip's length in metres.
   * @returns {number}
   */
  function majorStep(spanM) {
    return MAJOR_STEP[niceStep(spanM)];
  }

  /**
   * The one unit every label on the strip is written in.
   *
   * @param {number} spanM The strip's length in metres.
   * @returns {'m'|'km'}
   */
  function tickUnit(spanM) {
    return majorStep(spanM) >= 1000 ? 'km' : 'm';
  }

  /**
   * Every tick on a strip, start first.
   *
   * @param {number} spanM The strip's length in metres.
   * @param {{m?: string, km?: string}} [units] Label templates carrying
   *   `%(value)s`, from the partial's strings template.
   * @returns {Array<{d: number, major: boolean, label: ?string}>} `label`
   *   is set on majors only, in the strip's one unit.
   */
  function ticks(spanM, units) {
    if (!(spanM > 0)) return [];
    var step = niceStep(spanM);
    var major = MAJOR_STEP[step];
    var unit = tickUnit(spanM);
    var templates = { ...DEFAULT_UNITS, ...(units || {}) };
    var template = templates[unit];

    /** @type {Array<{d: number, major: boolean, label: ?string}>} */
    var out = [];
    for (var n = 0; n * step <= spanM; n += 1) {
      var d = n * step;
      var isMajor = d % major === 0;
      out.push({
        d: d,
        major: isMajor,
        label: isMajor
          ? interpolate(template, { value: String(unit === 'km' ? d / 1000 : d) })
          : null,
      });
    }
    return out;
  }

  /**
   * The figures line for a route or a leg.
   *
   * @param {{
   *   distance_m?: ?number,
   *   ascent_m?: ?number,
   *   descent_m?: ?number,
   *   elevation_start?: ?number,
   *   elevation_end?: ?number,
   * }} figures What is known; a null or absent figure is left out.
   * @param {Object<string, string>} [strings] Templates keyed as
   *   `DEFAULT_FIGURES`, from the partial's strings template.
   * @returns {string}
   */
  function formatFigures(figures, strings) {
    var t = { ...DEFAULT_FIGURES, ...(strings || {}) };
    var f = figures || {};
    /**
     * @param {*} value
     * @returns {value is number}
     */
    var known = function (value) {
      return typeof value === 'number' && isFinite(value);
    };

    /** @type {Array<string>} */
    var parts = [];
    if (known(f.distance_m)) {
      parts.push(interpolate(t['figure-distance'], { km: (f.distance_m / 1000).toFixed(1) }));
    }
    if (known(f.ascent_m)) {
      parts.push(interpolate(t['figure-ascent'], { m: String(Math.round(f.ascent_m)) }));
    }
    if (known(f.descent_m)) {
      parts.push(interpolate(t['figure-descent'], { m: String(Math.round(f.descent_m)) }));
    }
    if (known(f.elevation_start) && known(f.elevation_end)) {
      parts.push(interpolate(t['figure-range'], {
        start: String(Math.round(f.elevation_start)),
        end: String(Math.round(f.elevation_end)),
      }));
    }
    return parts.join(t['figure-separator']);
  }

  /**
   * Where a leg starts and ends on the profile's x-axis, in metres.
   *
   * @param {Leg} leg The leg, in sample indices.
   * @param {number} sampleCount N, the length of `slope.angles`.
   * @param {number} distanceM The profile's own length.
   * @returns {[number, number]}
   */
  function legSpan(leg, sampleCount, distanceM) {
    return [
      (leg.from / sampleCount) * distanceM,
      ((leg.to + 1) / sampleCount) * distanceM,
    ];
  }

  /**
   * Linear interpolation of the elevation at `d` between two points.
   *
   * @param {ProfilePoint} a
   * @param {ProfilePoint} b
   * @param {number} d
   * @returns {ProfilePoint}
   */
  function between(a, b, d) {
    if (b.d === a.d) return { d: d, e: a.e };
    return { d: d, e: a.e + ((b.e - a.e) * (d - a.d)) / (b.d - a.d) };
  }

  /**
   * The part of one elevation run lying inside `[start, end]`.
   *
   * The two ends are INTERPOLATED onto the boundary rather than snapped to
   * the nearest vertex, so two adjacent legs share their boundary vertex
   * exactly and no sliver of profile falls between their fills.
   *
   * @param {Array<ProfilePoint>} run
   * @param {number} start
   * @param {number} end
   * @returns {Array<ProfilePoint>}
   */
  function clipRun(run, start, end) {
    /** @type {Array<ProfilePoint>} */
    var out = [];
    for (var i = 0; i < run.length; i += 1) {
      var p = run[i];
      var prev = i > 0 ? run[i - 1] : null;
      if (prev && prev.d < start && p.d > start) out.push(between(prev, p, start));
      if (prev && prev.d < end && p.d > end) {
        out.push(between(prev, p, end));
        break;
      }
      if (p.d >= start && p.d <= end) out.push(p);
      if (p.d > end) break;
    }
    return out;
  }

  /**
   * One fill per leg, plus one outline of the whole track.
   *
   * @param {Profile} profile A `readProfile` result.
   * @param {Array<Leg>} legs The route's legs; may be empty.
   * @param {number} sampleCount N, the length of `slope.angles`.
   * @param {Box} [box] The user-space box. Defaults to `BOX`.
   * @returns {{
   *   legs: Array<{leg: Leg, d: string, climbing: boolean}>,
   *   outline: string,
   * }} Empty paths when the profile has no drawable elevation. A leg whose
   *   stretch holds no elevation at all is left out rather than given an
   *   empty path.
   */
  function legPaths(profile, legs, sampleCount, box) {
    var b = box || BOX;
    var empty = { legs: [], outline: '' };
    if (!profile || !profile.hasElevation || !(profile.distanceM > 0)) return empty;
    var minEle = /** @type {number} */ (profile.minEle);
    var maxEle = /** @type {number} */ (profile.maxEle);

    var range = maxEle - minEle;
    var floor = b.height - PAD_Y;
    var usable = b.height - PAD_Y * 2;
    /** @param {number} d */
    var x = function (d) { return ((d / profile.distanceM) * b.width).toFixed(2); };
    /** @param {number} e */
    var y = function (e) {
      return (range ? floor - ((e - minEle) / range) * usable : b.height / 2).toFixed(2);
    };
    /** @param {Array<ProfilePoint>} points */
    var line = function (points) {
      return points
        .map(function (p, i) { return (i === 0 ? 'M' : 'L') + x(p.d) + ' ' + y(p.e); })
        .join(' ');
    };
    /** @param {Array<ProfilePoint>} points */
    var area = function (points) {
      var base = floor.toFixed(2);
      return line(points)
        + ' L' + x(points[points.length - 1].d) + ' ' + base
        + ' L' + x(points[0].d) + ' ' + base + ' Z';
    };

    var drawable = profile.runs.filter(function (run) { return run.length >= 2; });
    var outline = drawable.map(line).join(' ');

    /** @type {Array<{leg: Leg, d: string, climbing: boolean}>} */
    var fills = [];
    if (sampleCount > 0) {
      (legs || []).forEach(function (leg) {
        var span = legSpan(leg, sampleCount, profile.distanceM);
        var pieces = drawable
          .map(function (run) { return clipRun(run, span[0], span[1]); })
          .filter(function (piece) { return piece.length >= 2; });
        if (!pieces.length) return;
        fills.push({ leg: leg, d: pieces.map(area).join(' '), climbing: !!leg.climbing });
      });
    }
    return { legs: fills, outline: outline };
  }

  /**
   * Where the outline sits at one distance, in the lane's user space.
   *
   * The y `legPaths` draws at `d`, interpolated between the two points
   * either side of it — where the cursor line meets the profile, which is
   * where the leader line (route_leader.js) attaches to this rail.
   *
   * @param {Profile} profile A `readProfile` result.
   * @param {number} d A distance on the profile's own axis.
   * @param {Box} [box] The user-space box. Defaults to `BOX`.
   * @returns {?number} Null where the profile has no elevation at `d`.
   */
  function profileY(profile, d, box) {
    var b = box || BOX;
    if (!profile || !profile.hasElevation) return null;
    var minEle = /** @type {number} */ (profile.minEle);
    var range = /** @type {number} */ (profile.maxEle) - minEle;
    var floor = b.height - PAD_Y;
    var usable = b.height - PAD_Y * 2;
    for (var r = 0; r < profile.runs.length; r += 1) {
      var run = profile.runs[r];
      for (var i = 1; i < run.length; i += 1) {
        if (d < run[i - 1].d || d > run[i].d) continue;
        var e = between(run[i - 1], run[i], d).e;
        return range ? floor - ((e - minEle) / range) * usable : b.height / 2;
      }
    }
    return null;
  }

  /**
   * The leg under a point on the strip.
   *
   * @param {number} fraction How far along the strip, 0 to 1.
   * @param {Array<Leg>} legs The route's legs.
   * @param {number} sampleCount N, the length of `slope.angles`.
   * @returns {?Leg}
   */
  function legAt(fraction, legs, sampleCount) {
    if (!(sampleCount > 0) || !Array.isArray(legs)) return null;
    var index = Math.min(sampleCount - 1, Math.max(0, Math.floor(fraction * sampleCount)));
    for (var i = 0; i < legs.length; i += 1) {
      if (index >= legs[i].from && index <= legs[i].to) return legs[i];
    }
    return null;
  }

  self.pwaRouteRailCore = Object.freeze({
    niceStep: niceStep,
    majorStep: majorStep,
    tickUnit: tickUnit,
    ticks: ticks,
    formatFigures: formatFigures,
    legSpan: legSpan,
    clipRun: clipRun,
    legPaths: legPaths,
    legAt: legAt,
    profileY: profileY,
    BOX: BOX,
  });
})();
