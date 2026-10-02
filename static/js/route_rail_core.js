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
 * ## The meta line
 *
 * The rail's subtitle (SNOW-1065) is the routes list's meta line,
 * `formatMetaLine`: "12.9km · 337m ↑ · 1906m ↓ · 2h51m", with the
 * panel's strings and null rules. A null figure is OMITTED, never shown as
 * zero: a route whose GPX carried no elevation has an unknown ascent, not
 * a flat one (Route.ascent_m's docstring). The two-line header SNOW-1045
 * gave the rail, and its steep-terrain figure, went with rail two.
 *
 * ## A placed point's readout
 *
 * A point placed on the route is drawn on the profile as a vertical line
 * with a dot where it crosses the outline, and two figures beside it
 * (2026-10-02): the elevation at the top (`pointElevation`) and the
 * distance from the start at the bottom (`pointDistance`). They sit right
 * of the line, left-aligned, unless they would not fit (`readoutSide`).
 *
 * Exports (frozen `self.pwaRouteRailCore`):
 *
 *   niceStep(spanM)                          → metres between minor ticks
 *   majorStep(spanM)                         → metres between labelled ticks
 *   tickUnit(spanM)                          → 'm' or 'km', once per strip
 *   ticks(spanM, units?)                     → [{d, major, label}]
 *   pointDistance(index, sampleCount, spanM, units?) → a point's distance
 *                                              along the route, as a label
 *   formatDuration(seconds)                  → {hours, minutes}, or null
 *   formatMetaLine(figures, strings?)        → the routes list's meta line
 *   legSpan(leg, sampleCount, distanceM)     → [startM, endM] on the profile
 *   clipRun(run, start, end)                 → a run clipped to [start, end],
 *                                              its ends interpolated
 *   legPaths(profile, legs, sampleCount, box) → one fill per leg + outline
 *   profileY(profile, d, box?)               → the outline's y at distance d
 *   pointElevation(profile, index, sampleCount, units?) → a point's
 *                                              elevation, as a label
 *   onOutline(profile, fraction, offsetY, laneHeight, tolerance, box?)
 *                                            → whether a press is on the
 *                                              top line, loosely
 *   readoutSide(x, laneWidth, readoutWidth, gap) → which side of the
 *                                              cursor line its figures go
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

  /**
   * The English templates for the meta line (SNOW-1065): the routes
   * panel's msgids, byte for byte.
   */
  var DEFAULT_META_LINE = Object.freeze({
    'meta-km': '%(km)skm',
    'meta-both': '%(km)skm · %(ascent)sm ↑ · %(descent)sm ↓',
    'meta-ascent': '%(km)skm · %(ascent)sm ↑',
    'meta-descent': '%(km)skm · %(descent)sm ↓',
    'meta-hm': '%(hours)sh%(minutes)sm',
    'meta-m': '%(minutes)sm',
    'meta-duration': '%(figures)s · %(duration)s',
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
   * A point's distance along the route, as the profile's readout shows it
   * (the eyebrow until 2026-10-02): the segment's midpoint as a share of the route's
   * length, in metres to the nearest 10 under a kilometre and in
   * kilometres to one decimal from there.
   *
   * @param {number} index The segment index.
   * @param {number} sampleCount The segments the route has.
   * @param {number} spanM The route's length, metres.
   * @param {{m?: string, km?: string}} [units] Label templates carrying
   *   `%(value)s`, from the partial's strings template.
   * @returns {?string} Null for an index or a route it cannot place.
   */
  function pointDistance(index, sampleCount, spanM, units) {
    if (!Number.isFinite(index) || !(sampleCount > 0) || !(spanM > 0)) return null;
    var d = ((index + 0.5) / sampleCount) * spanM;
    var templates = { ...DEFAULT_UNITS, ...(units || {}) };
    var metres = Math.round(d / 10) * 10;
    if (metres < 1000) return interpolate(templates.m, { value: String(metres) });
    return interpolate(templates.km, { value: (d / 1000).toFixed(1) });
  }

  /**
   * @param {*} value
   * @returns {value is number}
   */
  function isKnown(value) {
    return typeof value === 'number' && isFinite(value);
  }

  /**
   * A recording's elapsed time, split for the meta line: whole minutes
   * rounded half up; an empty hours figure under an hour, with the minutes
   * unpadded, and padded minutes above it ("4h05m").
   *
   * The rule is `split_hours_minutes` in apps/core/durations.py, which the
   * routes panel renders from; `Math.round` already rounds a .5 up, the
   * tie Python's builtin `round` would break the other way.
   *
   * @param {*} seconds The elapsed time, seconds.
   * @returns {?{hours: string, minutes: string}} Null when unknown.
   */
  function formatDuration(seconds) {
    if (!isKnown(seconds) || seconds <= 0) return null;
    var total = Math.round(seconds / 60);
    var hours = Math.floor(total / 60);
    var minutes = total % 60;
    if (!hours) return { hours: '', minutes: String(minutes) };
    return { hours: String(hours), minutes: String(minutes).padStart(2, '0') };
  }

  /**
   * The routes list's meta line (SNOW-1065): "12.9km · 337m ↑ · 1906m ↓ ·
   * 2h51m".
   *
   * The same strings and the same null rules as the routes panel's row
   * (apps/routes/templates/routes/partials/_route.html), so one route reads
   * the same in both places: the distance always; ascent and descent each
   * only when known, never as zero; the elapsed time only when the
   * recording has one. Kilometres to one decimal and whole metres with no
   * separator, as the panel's `floatformat` filters write them.
   *
   * @param {{distance_m?: ?number, ascent_m?: ?number, descent_m?: ?number,
   *   duration_s?: ?number}} figures The route's figures, or a leg's (which
   *   has no duration).
   * @param {Object<string, string>} [strings] Templates keyed as
   *   `DEFAULT_META_LINE`, from the partial's strings template.
   * @returns {string} The line; '' with no distance.
   */
  function formatMetaLine(figures, strings) {
    var t = { ...DEFAULT_META_LINE, ...(strings || {}) };
    var f = figures || {};
    if (!isKnown(f.distance_m)) return '';
    var km = (f.distance_m / 1000).toFixed(1);
    var up = isKnown(f.ascent_m) ? Math.round(f.ascent_m).toFixed(0) : null;
    var down = isKnown(f.descent_m) ? Math.round(f.descent_m).toFixed(0) : null;
    var line;
    if (up !== null && down !== null) {
      line = interpolate(t['meta-both'], { km: km, ascent: up, descent: down });
    } else if (up !== null) {
      line = interpolate(t['meta-ascent'], { km: km, ascent: up });
    } else if (down !== null) {
      line = interpolate(t['meta-descent'], { km: km, descent: down });
    } else {
      line = interpolate(t['meta-km'], { km: km });
    }
    var duration = formatDuration(f.duration_s);
    if (!duration) return line;
    var spelt = duration.hours
      ? interpolate(t['meta-hm'], duration)
      : interpolate(t['meta-m'], duration);
    return interpolate(t['meta-duration'], { figures: line, duration: spelt });
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
   * The profile's elevation at one distance, interpolated between the two
   * points either side of it.
   *
   * @param {Profile} profile A `readProfile` result.
   * @param {number} d A distance on the profile's own axis.
   * @returns {?number} Null where the profile has no elevation at `d`.
   */
  function elevationAt(profile, d) {
    if (!profile || !profile.hasElevation) return null;
    for (var r = 0; r < profile.runs.length; r += 1) {
      var run = profile.runs[r];
      for (var i = 1; i < run.length; i += 1) {
        if (d < run[i - 1].d || d > run[i].d) continue;
        return between(run[i - 1], run[i], d).e;
      }
    }
    return null;
  }

  /**
   * Where the outline sits at one distance, in the lane's user space.
   *
   * The y `legPaths` draws at `d` — where the cursor line meets the
   * profile, which is where the dot marking the point's elevation sits
   * (2026-10-02).
   *
   * @param {Profile} profile A `readProfile` result.
   * @param {number} d A distance on the profile's own axis.
   * @param {Box} [box] The user-space box. Defaults to `BOX`.
   * @returns {?number} Null where the profile has no elevation at `d`.
   */
  function profileY(profile, d, box) {
    var b = box || BOX;
    var e = elevationAt(profile, d);
    if (e === null) return null;
    var minEle = /** @type {number} */ (profile.minEle);
    var range = /** @type {number} */ (profile.maxEle) - minEle;
    var floor = b.height - PAD_Y;
    var usable = b.height - PAD_Y * 2;
    return range ? floor - ((e - minEle) / range) * usable : b.height / 2;
  }

  /**
   * The elevation of a placed point, as the profile's readout shows it
   * (2026-10-02): the height the outline is drawn at under the cursor
   * line, to the nearest metre.
   *
   * Read off the profile at the segment's midpoint by share — where the
   * cursor line and its dot sit — so the figure is the height the dot
   * marks.
   *
   * @param {Profile} profile A `readProfile` result.
   * @param {number} index The segment index.
   * @param {number} sampleCount The segments the route has.
   * @param {{m?: string, km?: string}} [units] Label templates carrying
   *   `%(value)s`, from the partial's strings template.
   * @returns {?string} Null where the profile has no elevation there.
   */
  function pointElevation(profile, index, sampleCount, units) {
    if (!profile || !Number.isFinite(index) || !(sampleCount > 0)) return null;
    var e = elevationAt(profile, ((index + 0.5) / sampleCount) * profile.distanceM);
    if (e === null) return null;
    var templates = { ...DEFAULT_UNITS, ...(units || {}) };
    return interpolate(templates.m, { value: String(Math.round(e)) });
  }

  /**
   * Whether a press on the lane is on the profile's top line (2026-10-02).
   *
   * The target is the outline, loosely: anything within `tolerance` px of
   * it, above or below, at the press's x. A press lower down, in the
   * fill near the ticks, is not a reading of the profile and places
   * nothing — a finger resting at the foot of the panel should not send
   * the point somewhere.
   *
   * @param {Profile} profile A `readProfile` result.
   * @param {number} fraction How far along the lane, 0 to 1.
   * @param {number} offsetY The press's y, px from the lane's top edge.
   * @param {number} laneHeight The lane's rendered height, px.
   * @param {number} tolerance How far from the outline still counts, px.
   * @param {Box} [box] The user-space box. Defaults to `BOX`.
   * @returns {boolean} False where the profile has no elevation.
   */
  function onOutline(profile, fraction, offsetY, laneHeight, tolerance, box) {
    var b = box || BOX;
    if (!profile || !(profile.distanceM > 0) || !(laneHeight > 0)) return false;
    var y = profileY(profile, fraction * profile.distanceM, b);
    if (y === null) return false;
    return Math.abs(offsetY - (y / b.height) * laneHeight) <= tolerance;
  }

  /**
   * Which side of the cursor line the readout sits on (2026-10-02).
   *
   * The right, left-aligned, whenever it fits between the line and the
   * lane's right edge; otherwise the left, right-aligned, so a point near
   * the end of the route never has its figures clipped.
   *
   * @param {number} x The cursor line's x, px from the lane's left edge.
   * @param {number} laneWidth The lane's width, px.
   * @param {number} readoutWidth The wider of the two labels, px.
   * @param {number} gap The space between the line and the labels, px.
   * @returns {'right'|'left'}
   */
  function readoutSide(x, laneWidth, readoutWidth, gap) {
    return x + gap + readoutWidth <= laneWidth ? 'right' : 'left';
  }

  self.pwaRouteRailCore = Object.freeze({
    niceStep: niceStep,
    majorStep: majorStep,
    tickUnit: tickUnit,
    ticks: ticks,
    pointDistance: pointDistance,
    formatDuration: formatDuration,
    formatMetaLine: formatMetaLine,
    legSpan: legSpan,
    clipRun: clipRun,
    legPaths: legPaths,
    profileY: profileY,
    pointElevation: pointElevation,
    onOutline: onOutline,
    readoutSide: readoutSide,
    BOX: BOX,
  });
})();
