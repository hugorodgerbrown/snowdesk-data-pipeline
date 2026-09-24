/*
 * static/js/route_rail_two_core.js — rail two's pure half: one leg, zoomed,
 * with the ground under it (SNOW-1019).
 *
 * Rail two opens under rail one when a leg is pressed. It draws the open
 * leg on three rows sharing one x-axis — a strip of slope bands, the track
 * line drawn as the bank ribbon (bank_ribbon_core.js), and one bar per
 * no-fall passage — and it pans and zooms within the leg. (It drew the
 * leg's elevation profile above them until SNOW-1019 took the row out;
 * the leg's profile is still read here, for the identity cell's figures.) This module is the arithmetic of that drawing: no
 * DOM, no globals read at parse time, so every rule below is covered in
 * tests/js/test_route_rail_two_core.js. static/js/route_rail_two.js is the
 * DOM half.
 *
 * ## The x-axis is in sample units
 *
 * Sample i — the i-th entry of `slope.angles` — owns the interval
 * [i, i + 1) on the axis. That is rail one's placing-by-share rule
 * (`legSpan` in route_rail_core.js) with the distance factor taken out: a
 * profile point at distance d on `readProfile`'s axis sits at
 * s = d / distanceM × N, and a label at s reads s / N × spanM metres,
 * where spanM is the route length rail one prints. The two rails therefore
 * put a segment in the same place and label it with the same number.
 *
 * A leg `{from, to}` (inclusive sample indices) covers [from, to + 1].
 *
 * ## The view
 *
 * The window rail two shows is `{from, to}` in the same continuous units,
 * `to` exclusive, so `to − from` is the SPAN — how many samples are across
 * the lane. The edges are not whole samples: a drag moves the view by the
 * pixels the pointer moved, and rounding it to a sample would make a
 * zoomed-in pan jump by a sample's width at a time. The view and span are
 * rail two's own state and never the cursor's (route_cursor_core.js).
 *
 * The span runs from `min(MIN_SPAN, legLength)` up to the whole leg, and
 * every view is clamped to the leg's ends, so a pan or a zoom can never
 * show ground outside the open leg.
 *
 * ## Slope bands
 *
 * A band is a run of consecutive segments sharing one slope class
 * (`pwaRouteSlopeCore.classify`), UNMERGED: a one-segment 35° band between
 * two 30° ones is its own band, because that one segment is the reading.
 * Consecutive unknown (null) angles make a run of their own, and a null
 * never joins a known class — "not known" must not read as the class
 * beside it.
 *
 * Exports (frozen `self.pwaRouteRailTwoCore`):
 *
 *   MIN_SPAN                                  → the narrowest span, 6
 *   WINDOW_M                                  → the opening window, 2000 m
 *   ROWS                                      → the lane's vertical layout
 *   bandRuns(angles, classify, range?)        → [{from, to, classIndex}]
 *   legLength(leg)                            → samples in the leg
 *   minSpan(leg)                              → the narrowest span it allows
 *   openingSpan(leg, sampleCount, spanM, windowM?) → the span it opens at
 *   placeView(leg, span, from)                → a view clamped to the leg
 *   ensureVisible(leg, view, from, to)        → the view, scrolled the least
 *   followView(leg, view, from, to)           → the view, centred on a range
 *   zoom(leg, span, nextSpan, anchor, fraction) → {span, view}
 *   fullyVisible(view)                        → [first, last] whole samples
 *   xOf(s, view, width)                       → px for an axis coordinate
 *   sampleAt(x, view, width)                  → the axis coordinate at px
 *   indexAt(x, view, width)                   → the sample index at px
 *   clip(range, view)                         → visible part, or null
 *   tickPitch(span, width, basePitch)         → the ribbon's tick pitch
 *   tickPhase(view, width, pitch)             → px the ticks scroll by
 *   ribbonTicks(options)                      → bankTicks, laid on the view
 *   legProfile(profile, leg, sampleCount, clipRun) → the leg in sample units
 *   legFigures(legProfile, leg, sampleCount, spanM) → for formatFigures
 *   distanceTicks(view, sampleCount, spanM, rail, units?) → [{x, major, label}]
 *   trackAttitude(angle, roll, climbing)      → {term, side}, or null
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {{from: number, to: number}} Leg
   *   A leg in sample indices, both ends inclusive.
   */

  /**
   * @typedef {{from: number, to: number}} View
   *   A window on the axis in continuous sample units, `to` exclusive.
   */

  /**
   * @typedef {{from: number, to: number, classIndex: ?number}} BandRun
   *   A run of segments sharing one class, sample indices inclusive;
   *   `classIndex` is the index into `pwaRouteSlopeCore.CLASSES`, or null
   *   for a run of unknown angles.
   */

  /**
   * @typedef {{s: number, e: number}} LegPoint
   *   A profile point: `s` on the sample axis, `e` its elevation.
   */

  /**
   * @typedef {{
   *   runs: Array<Array<LegPoint>>,
   *   minEle: ?number,
   *   maxEle: ?number,
   *   from: number,
   *   to: number,
   * }} LegProfile
   *   The open leg's profile in sample units, which `legFigures` reads.
   *   `from` / `to` are the leg's ends on the axis; `minEle` / `maxEle` are
   *   the LEG's own range.
   */

  /**
   * @typedef {function(Array<{d: number, e: number}>, number, number):
   *   Array<{d: number, e: number}>} ClipRun
   *   route_rail_core.js's `clipRun`: the part of a run inside [start, end],
   *   its ends interpolated onto the boundary.
   */

  /** The narrowest span, in samples, a zoom may reach. */
  var MIN_SPAN = 6;

  /** How much ground rail two shows when a leg opens, in metres. */
  var WINDOW_M = 2000;

  /** Two numbers closer than this are the same point on the axis. */
  var EPSILON = 1e-6;

  /**
   * The lane's vertical layout, in px. The svg is drawn at this height in
   * real pixels (the partial's `h-14`, 56 px), so these are screen units.
   *
   * Three rows and the distance ticks: the band strip, the bank ribbon,
   * the no-fall bars, the tick marks at the foot. SNOW-1019 took the leg's
   * elevation profile off the top: at a 2 km window it drew near-flat and
   * said nothing rail one's highlighted leg does not.
   */
  var ROWS = Object.freeze({
    height: 56,
    bandTop: 4,
    bandHeight: 10,
    ribbonY: 28,
    ribbonHalf: 9,
    passageTop: 41,
    passageHeight: 6,
  });

  /**
   * How far off the fall line a track may run and still be ON it, in
   * degrees — `FALL_LINE_TOLERANCE_DEG` in apps/routes/services/passages.py,
   * the tolerance its fall-line vote uses, restated so the readout and the
   * passage labels agree on what "down the fall line" means.
   */
  var FALL_LINE_TOLERANCE_DEG = 30;

  /** At or past this far off the fall line, a track is traversing. */
  var TRAVERSE_DEG = 60;

  /** Under this slope angle the ground is flat and has no fall line. */
  var FLAT_DEG = 5;

  /** Under this bank the ground falls away to neither side. */
  var LEVEL_BANK_DEG = 3;

  /** The English units, the fallback when no strings are passed. */
  var DEFAULT_UNITS = Object.freeze({ m: '%(value)s m', km: '%(value)s km' });

  /**
   * Clamp a number into a closed range.
   *
   * @param {number} value
   * @param {number} low
   * @param {number} high
   * @returns {number}
   */
  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /**
   * Substitute `%(name)s` placeholders by name — route_rail_core.js's rule,
   * restated so this module reads no globals.
   *
   * @param {string} template
   * @param {Object<string, string>} params
   * @returns {string}
   */
  function interpolate(template, params) {
    return String(template).replace(/%\((\w+)\)s/g, function (whole, name) {
      return Object.prototype.hasOwnProperty.call(params, name) ? params[name] : whole;
    });
  }

  /**
   * Runs of consecutive segments sharing one slope class.
   *
   * @param {Array<?number>} angles `slope.angles`, null where unknown.
   * @param {function(?number): ?number} classify
   *   `pwaRouteSlopeCore.classify`.
   * @param {Leg} [range] Only the segments inside it, indices kept
   *   absolute. Defaults to the whole array.
   * @returns {Array<BandRun>} Left to right, touching end to end.
   */
  function bandRuns(angles, classify, range) {
    if (!Array.isArray(angles) || !angles.length) return [];
    var first = range ? Math.max(0, range.from) : 0;
    var last = range ? Math.min(angles.length - 1, range.to) : angles.length - 1;
    /** @type {Array<BandRun>} */
    var runs = [];
    for (var i = first; i <= last; i += 1) {
      var key = classify(angles[i]);
      var classIndex = typeof key === 'number' ? key : null;
      var open = runs.length ? runs[runs.length - 1] : null;
      if (open && open.classIndex === classIndex) {
        open.to = i;
      } else {
        runs.push({ from: i, to: i, classIndex: classIndex });
      }
    }
    return runs;
  }

  /**
   * How many samples a leg holds.
   *
   * @param {Leg} leg
   * @returns {number}
   */
  function legLength(leg) {
    return leg.to - leg.from + 1;
  }

  /**
   * The narrowest span a leg allows: `MIN_SPAN`, or the whole leg when it
   * is shorter, so a leg under six samples cannot zoom in at all.
   *
   * @param {Leg} leg
   * @returns {number}
   */
  function minSpan(leg) {
    return Math.min(MIN_SPAN, legLength(leg));
  }

  /**
   * The span a leg opens at: `windowM` of ground, or the whole leg when
   * it is shorter.
   *
   * @param {Leg} leg
   * @param {number} sampleCount N, the length of `slope.angles`.
   * @param {number} spanM The route's length, rail one's `distance_m`.
   * @param {number} [windowM] The ground to show. Defaults to `WINDOW_M`.
   * @returns {number}
   */
  function openingSpan(leg, sampleCount, spanM, windowM) {
    var length = legLength(leg);
    if (!(sampleCount > 0) || !(spanM > 0)) return length;
    var metres = windowM === undefined ? WINDOW_M : windowM;
    var samples = Math.round((metres / spanM) * sampleCount);
    return clamp(samples, minSpan(leg), length);
  }

  /**
   * A view `span` wide starting at `from`, clamped to the leg's ends.
   *
   * @param {Leg} leg
   * @param {number} span Samples across the lane; clamped to the leg's limits.
   * @param {number} from The wanted left edge.
   * @returns {View}
   */
  function placeView(leg, span, from) {
    var width = clamp(span, minSpan(leg), legLength(leg));
    var left = clamp(from, leg.from, leg.to + 1 - width);
    return { from: left, to: left + width };
  }

  /**
   * Scroll the view the least distance that shows `[from, to]` whole.
   *
   * A range already inside is a no-op and returns the same view. A range
   * wider than the view aligns its START with the left edge — the start
   * is where a reader looks first.
   *
   * @param {Leg} leg
   * @param {View} view
   * @param {number} from First sample, inclusive.
   * @param {number} to Last sample, inclusive.
   * @returns {View}
   */
  function ensureVisible(leg, view, from, to) {
    var a = Math.min(from, to);
    var b = Math.max(from, to) + 1;
    var span = view.to - view.from;
    if (a >= view.from - EPSILON && b <= view.to + EPSILON) return view;
    if (b - a > span || a < view.from) return placeView(leg, span, a);
    return placeView(leg, span, b - span);
  }

  /**
   * Bring `[from, to]` into view for a write from ANOTHER surface.
   *
   * A range already inside is a no-op and returns the same view. One that
   * fits is CENTRED, not scrolled the least: an index the map or rail one
   * moved lands mid-lane, where rail two's cursor line and the leader line
   * that ends on it can be seen, rather than on the lane's edge. A range
   * wider than the view aligns its start with the left edge, as
   * `ensureVisible` does. Rail two's own writes (keys, pointer) keep
   * `ensureVisible`, so stepping with the arrows does not jump the view on
   * every step.
   *
   * @param {Leg} leg
   * @param {View} view
   * @param {number} from First sample, inclusive.
   * @param {number} to Last sample, inclusive.
   * @returns {View}
   */
  function followView(leg, view, from, to) {
    var a = Math.min(from, to);
    var b = Math.max(from, to) + 1;
    var span = view.to - view.from;
    if (a >= view.from - EPSILON && b <= view.to + EPSILON) return view;
    if (b - a > span) return placeView(leg, span, a);
    return placeView(leg, span, (a + b) / 2 - span / 2);
  }

  /**
   * Zoom to `nextSpan`, keeping `anchor` at `fraction` of the lane.
   *
   * The span is clamped to the leg's limits, and the anchor stays put
   * unless the clamp to the leg's ends has to move the view.
   *
   * @param {Leg} leg
   * @param {number} span The current span.
   * @param {number} nextSpan The wanted span.
   * @param {number} anchor The axis coordinate to hold still.
   * @param {number} fraction Where across the lane it sits, 0 to 1.
   * @returns {{span: number, view: View}}
   */
  function zoom(leg, span, nextSpan, anchor, fraction) {
    var wanted = Number.isFinite(nextSpan) ? nextSpan : span;
    var next = clamp(wanted, minSpan(leg), legLength(leg));
    var view = placeView(leg, next, anchor - clamp(fraction, 0, 1) * next);
    return { span: next, view: view };
  }

  /**
   * The first and last samples the view shows whole.
   *
   * When no sample is whole — a span under one sample, which the limits
   * never allow — the sample under the centre stands for both.
   *
   * @param {View} view
   * @returns {[number, number]}
   */
  function fullyVisible(view) {
    var first = Math.ceil(view.from - EPSILON);
    var last = Math.floor(view.to + EPSILON) - 1;
    if (last < first) {
      var centre = Math.floor((view.from + view.to) / 2);
      return [centre, centre];
    }
    return [first, last];
  }

  /**
   * The px an axis coordinate sits at across a lane `width` wide.
   *
   * @param {number} s
   * @param {View} view
   * @param {number} width
   * @returns {number}
   */
  function xOf(s, view, width) {
    return ((s - view.from) / (view.to - view.from)) * width;
  }

  /**
   * The axis coordinate under a px.
   *
   * @param {number} x
   * @param {View} view
   * @param {number} width
   * @returns {number}
   */
  function sampleAt(x, view, width) {
    return view.from + (width > 0 ? x / width : 0) * (view.to - view.from);
  }

  /**
   * The sample index under a px: the sample whose interval holds it.
   *
   * @param {number} x
   * @param {View} view
   * @param {number} width
   * @returns {number}
   */
  function indexAt(x, view, width) {
    return Math.floor(sampleAt(x, view, width) + EPSILON);
  }

  /**
   * The part of a band or passage inside the view.
   *
   * @param {{from: number, to: number}} range Sample indices, inclusive.
   * @param {View} view
   * @returns {?View} Continuous units, `to` exclusive; null when none of
   *   it is inside.
   */
  function clip(range, view) {
    var a = Math.max(range.from, view.from);
    var b = Math.min(range.to + 1, view.to);
    return b > a ? { from: a, to: b } : null;
  }

  /**
   * The ribbon's tick pitch in px.
   *
   * `basePitch` until a sample is wider than that, then one sample's
   * width, so `bankTicks` draws one tick per sample once zoomed in.
   *
   * @param {number} span Samples across the lane.
   * @param {number} width The lane's width in px.
   * @param {number} basePitch The pitch at a wide view.
   * @returns {number}
   */
  function tickPitch(span, width, basePitch) {
    return Math.max(basePitch, span > 0 ? width / span : basePitch);
  }

  /**
   * How far the ribbon's ticks are scrolled left, in px.
   *
   * The ticks are pinned to the GROUND, not to the lane: a tick sits at
   * whole multiples of `pitch` from the leg's axis origin, so a pan
   * carries them along instead of making them shimmer between samples.
   * At a per-sample pitch that puts each tick on its sample's centre.
   *
   * @param {View} view
   * @param {number} width
   * @param {number} pitch
   * @returns {number} In [0, pitch).
   */
  function tickPhase(view, width, pitch) {
    var perSample = width / (view.to - view.from);
    var offset = (view.from * perSample) % pitch;
    return offset < 0 ? offset + pitch : offset;
  }

  /**
   * The bank ribbon's ticks for the view, pinned to the ground.
   *
   * `bankTicks` lays ticks from the lane's left edge; this asks it for one
   * pitch more than the lane and slides the row left by `tickPhase`, so
   * each tick stays on the same ground as the view pans. Ticks off either
   * edge, and any reading a sample outside the leg, are dropped.
   *
   * @param {{
   *   bankTicks: function(Object): Array<{x: number, index: number,
   *     roll: number, x1: number, y1: number, x2: number, y2: number,
   *     strong: boolean}>,
   *   banks: Array<?number>,
   *   leg: Leg,
   *   view: View,
   *   width: number,
   *   basePitch?: number,
   *   halfLength?: number,
   *   strongDeg?: number,
   *   y?: number,
   * }} options `bankTicks` is `pwaBankRibbonCore.bankTicks`; the rest as
   *   that function and this module name them.
   * @returns {Array<{x: number, index: number, roll: number, x1: number,
   *   y1: number, x2: number, y2: number, strong: boolean}>} In lane px.
   */
  function ribbonTicks(options) {
    var view = options.view;
    var width = options.width;
    var leg = options.leg;
    if (!Array.isArray(options.banks) || !(width > 0)) return [];
    var span = view.to - view.from;
    var pitch = tickPitch(span, width, options.basePitch === undefined ? 8 : options.basePitch);
    var phase = tickPhase(view, width, pitch);
    var ticks = options.bankTicks({
      banks: options.banks,
      width: width + pitch,
      pitch: pitch,
      halfLength: options.halfLength,
      strongDeg: options.strongDeg,
      y: options.y,
      /** @param {number} x */
      indexAt: function (x) {
        var index = indexAt(x - phase, view, width);
        return index >= leg.from && index <= leg.to ? index : -1;
      },
    });
    return ticks
      .filter(function (tick) { return tick.x - phase >= 0 && tick.x - phase <= width; })
      .map(function (tick) {
        return {
          x: tick.x - phase,
          index: tick.index,
          roll: tick.roll,
          x1: tick.x1 - phase,
          y1: tick.y1,
          x2: tick.x2 - phase,
          y2: tick.y2,
          strong: tick.strong,
        };
      });
  }

  /**
   * The open leg's profile, moved onto the sample axis.
   *
   * @param {{runs: Array<Array<{d: number, e: number}>>, distanceM: number,
   *   hasElevation: boolean}} profile A `readProfile` result.
   * @param {Leg} leg
   * @param {number} sampleCount N, the length of `slope.angles`.
   * @param {ClipRun} clipRun route_rail_core.js's.
   * @returns {LegProfile} Empty runs and null bounds when the leg has no
   *   drawable elevation.
   */
  function legProfile(profile, leg, sampleCount, clipRun) {
    /** @type {LegProfile} */
    var out = { runs: [], minEle: null, maxEle: null, from: leg.from, to: leg.to + 1 };
    if (!profile || !profile.hasElevation || !(profile.distanceM > 0) || !(sampleCount > 0)) {
      return out;
    }
    var distanceM = profile.distanceM;
    var start = (leg.from / sampleCount) * distanceM;
    var end = ((leg.to + 1) / sampleCount) * distanceM;
    var low = Infinity;
    var high = -Infinity;
    profile.runs.forEach(function (run) {
      var piece = clipRun(run, start, end);
      if (piece.length < 2) return;
      out.runs.push(piece.map(function (p) {
        if (p.e < low) low = p.e;
        if (p.e > high) high = p.e;
        return { s: (p.d / distanceM) * sampleCount, e: p.e };
      }));
    });
    if (out.runs.length) {
      out.minEle = low;
      out.maxEle = high;
    }
    return out;
  }

  /**
   * The leg's figures, in the shape `formatFigures` takes.
   *
   * The distance is the leg's share of the route's length, so it agrees
   * with rail one's ticks. Ascent and descent sum the profile's own
   * steps. The start and end elevations are only given when the profile
   * reaches the leg's ends — a reading inside the leg is a height the leg
   * passes, not the one it starts or finishes at.
   *
   * @param {LegProfile} lp A `legProfile` result.
   * @param {Leg} leg
   * @param {number} sampleCount N.
   * @param {number} spanM The route's length, rail one's `distance_m`.
   * @returns {{distance_m: ?number, ascent_m: ?number, descent_m: ?number,
   *   elevation_start: ?number, elevation_end: ?number}}
   */
  function legFigures(lp, leg, sampleCount, spanM) {
    var distance = sampleCount > 0 && spanM > 0
      ? (legLength(leg) / sampleCount) * spanM
      : null;
    if (!lp.runs.length) {
      return {
        distance_m: distance,
        ascent_m: null,
        descent_m: null,
        elevation_start: null,
        elevation_end: null,
      };
    }
    var ascent = 0;
    var descent = 0;
    lp.runs.forEach(function (run) {
      for (var i = 1; i < run.length; i += 1) {
        var step = run[i].e - run[i - 1].e;
        if (step > 0) ascent += step;
        else descent -= step;
      }
    });
    var first = lp.runs[0][0];
    var lastRun = lp.runs[lp.runs.length - 1];
    var last = lastRun[lastRun.length - 1];
    return {
      distance_m: distance,
      ascent_m: ascent,
      descent_m: descent,
      elevation_start: Math.abs(first.s - lp.from) < EPSILON ? first.e : null,
      elevation_end: Math.abs(last.s - lp.to) < EPSILON ? last.e : null,
    };
  }

  /**
   * The distance ticks inside the view, labelled in route metres.
   *
   * The step is rail one's rule applied to the ground the view shows, and
   * the ticks fall on whole multiples of it from the ROUTE's start, so a
   * label here is the same number rail one prints at that place.
   *
   * @param {View} view
   * @param {number} sampleCount N.
   * @param {number} spanM The route's length, rail one's `distance_m`.
   * @param {{niceStep: function(number): number,
   *   majorStep: function(number): number,
   *   tickUnit: function(number): string}} rail `pwaRouteRailCore`.
   * @param {{m?: string, km?: string}} [units] Label templates.
   * @param {number} [width] The lane's width in px. Defaults to 1, so `x`
   *   is a fraction.
   * @returns {Array<{x: number, d: number, major: boolean, label: ?string}>}
   */
  function distanceTicks(view, sampleCount, spanM, rail, units, width) {
    if (!(sampleCount > 0) || !(spanM > 0)) return [];
    var w = width === undefined ? 1 : width;
    var perSample = spanM / sampleCount;
    var startM = view.from * perSample;
    var endM = view.to * perSample;
    var windowM = endM - startM;
    if (!(windowM > 0)) return [];
    var step = rail.niceStep(windowM);
    var major = rail.majorStep(windowM);
    var unit = rail.tickUnit(windowM);
    var templates = { ...DEFAULT_UNITS, ...(units || {}) };
    var template = unit === 'km' ? templates.km : templates.m;

    /** @type {Array<{x: number, d: number, major: boolean, label: ?string}>} */
    var out = [];
    for (var n = Math.ceil(startM / step - EPSILON); n * step <= endM + EPSILON; n += 1) {
      var d = n * step;
      var isMajor = d % major === 0;
      out.push({
        x: xOf(d / perSample, view, w),
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
   * What the track is doing on the ground at one segment.
   *
   * The slope angle `a` is the ground's steepness and the bank `roll` is
   * how far it tilts ACROSS the track (apps/routes/services/bank.py), so
   * tan|roll| = tan(a) · |sin δ|, where δ is the angle between the track
   * and the fall line. That gives δ from the two numbers the wire already
   * carries, clamped to [0, 1] before the `asin` because both are rounded
   * to whole degrees.
   *
   *   a < 5°          → 'flat': no fall line to be on or off;
   *   δ ≤ 30°         → 'fall-line', either way along it;
   *   30° < δ < 60°   → 'downhill-traverse' on a descending leg,
   *                     'uphill-traverse' on a climbing one;
   *   δ ≥ 60°         → 'traverse'.
   *
   * `side` is where the ground falls away: 'right' for a positive roll
   * (bank.py's sign), 'left' for a negative one, and null under 3° or on
   * flat ground.
   *
   * @param {?number} angle The segment's slope angle, degrees.
   * @param {?number} roll The segment's signed bank, degrees.
   * @param {boolean} climbing Whether the open leg climbs.
   * @returns {?{term: string, side: ?string}} Null when either number is
   *   unknown.
   */
  function trackAttitude(angle, roll, climbing) {
    if (typeof angle !== 'number' || !Number.isFinite(angle)) return null;
    if (typeof roll !== 'number' || !Number.isFinite(roll)) return null;
    if (angle < FLAT_DEG) return { term: 'flat', side: null };
    var rad = Math.PI / 180;
    var sinDelta = clamp(Math.tan(Math.abs(roll) * rad) / Math.tan(angle * rad), 0, 1);
    var delta = Math.asin(sinDelta) / rad;
    var term;
    if (delta <= FALL_LINE_TOLERANCE_DEG) {
      term = 'fall-line';
    } else if (delta >= TRAVERSE_DEG) {
      term = 'traverse';
    } else {
      term = climbing ? 'uphill-traverse' : 'downhill-traverse';
    }
    var side = Math.abs(roll) < LEVEL_BANK_DEG ? null : roll > 0 ? 'right' : 'left';
    return { term: term, side: side };
  }

  self.pwaRouteRailTwoCore = Object.freeze({
    FALL_LINE_TOLERANCE_DEG: FALL_LINE_TOLERANCE_DEG,
    trackAttitude: trackAttitude,
    MIN_SPAN: MIN_SPAN,
    WINDOW_M: WINDOW_M,
    ROWS: ROWS,
    bandRuns: bandRuns,
    legLength: legLength,
    minSpan: minSpan,
    openingSpan: openingSpan,
    placeView: placeView,
    ensureVisible: ensureVisible,
    followView: followView,
    zoom: zoom,
    fullyVisible: fullyVisible,
    xOf: xOf,
    sampleAt: sampleAt,
    indexAt: indexAt,
    clip: clip,
    tickPitch: tickPitch,
    tickPhase: tickPhase,
    ribbonTicks: ribbonTicks,
    legProfile: legProfile,
    legFigures: legFigures,
    distanceTicks: distanceTicks,
  });
})();
