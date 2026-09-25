/*
 * static/js/route_rail_two_core.js — rail two's pure half: one leg, zoomed,
 * with the ground under it (SNOW-1019).
 *
 * Rail two opens under rail one when a leg is pressed. It draws the open
 * leg on three rows sharing one x-axis — a strip of slope bands, the track
 * drawn as a row of level-ski wedges showing its bank (bank_ribbon_core.js,
 * SNOW-1031), and one bar per no-fall passage — and it pans and zooms within the leg. (It drew the
 * leg's elevation profile above them until SNOW-1019 took the row out;
 * the leg's profile is still read here, for the identity cell's figures.
 * Its distance scale went in SNOW-1024: rail one already prints where
 * the window sits, and the readout reads the point.) This module is the
 * arithmetic of that drawing and of where its readout sits: no
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
 * put a segment in the same place and measure it with the same number.
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
 * show ground outside the open leg; the next leg is reached on rail one.
 * A leg always OPENS FITTED, however long it is (SNOW-1031's revision):
 * fitted, rail two is an overview, and zooming is how it is read in
 * detail. The rail is never widened to make something tappable.
 *
 * ## The bank row follows the zoom
 *
 * A glyph needs about `GLYPH_MIN_PX` (10 px) to read, and a segment at the
 * fitted scale can be under 1 px, so each glyph covers N whole segments,
 * N = ceil(10 px / segment width) (`glyphGroup`). Groups are aligned to
 * the leg's start, so a group never splits a segment and never shifts as
 * the view pans. A glyph draws the segment with the LARGEST |roll| in its
 * group, with that segment's side — never the mean, which cancels a
 * zig-zag out. Past `MAX_GROUP` (3 segments, 75 m) one glyph would
 * summarise too much ground, so the row draws NO glyphs and the mount
 * shows a placeholder that asks for a zoom instead. The scale is constant
 * across the lane, so the row is either all drawn or all placeholder.
 * `resolveSpan` is the widest span that draws, the target a double-tap
 * zooms to. The band strip and the passages are drawn per segment at
 * every zoom, however thin.
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
 *   GLYPH_MIN_PX                              → the px a bank glyph needs, 10
 *   MAX_GROUP                                 → most segments a glyph reads, 3
 *   PASSAGE_MIN_PX                            → a passage bar's least width, 6
 *   ROWS                                      → the lane's vertical layout
 *   bandRuns(angles, classify, range?)        → [{from, to, classIndex}]
 *   legLength(leg)                            → samples in the leg
 *   minSpan(leg)                              → the narrowest span it allows
 *   openingSpan(leg)                          → the span it opens at: all of it
 *   placeView(leg, span, from)                → a view clamped to the leg
 *   ensureVisible(leg, view, from, to)        → the view, scrolled the least
 *   followView(leg, view, from, to)           → the view, centred on a range
 *   zoom(leg, span, nextSpan, anchor, fraction) → {span, view}
 *   fullyVisible(view)                        → [first, last] whole samples
 *   xOf(s, view, width)                       → px for an axis coordinate
 *   sampleAt(x, view, width)                  → the axis coordinate at px
 *   indexAt(x, view, width)                   → the sample index at px
 *   clip(range, view)                         → visible part, or null
 *   nearestRange(ranges, x, view, width, radiusPx) → the range a tap picks
 *   glyphGroup(span, width)                   → segments per bank glyph, N
 *   resolveSpan(leg, width)                   → the widest span whose row draws
 *   bankGlyphs(options)                       → {placeholder, glyphs} for the view
 *   passageBox(part, view, width)             → a passage bar's {x, width} in px
 *   legProfile(profile, leg, sampleCount, clipRun) → the leg in sample units
 *   legFigures(legProfile, leg, sampleCount, spanM) → for formatFigures
 *   trackAttitude(angle, roll, climbing)      → {term, side}, or null
 *   readoutAnchor(x, width)                   → {align, left} for the readout
 *   roundStretch(metres)                      → a length to the nearest 25 m
 *   legSlots(legs, sampleCount)               → the leg picker's segments
 *   MOTION                                    → the opening motion's phases, ms
 *   motionPlan(reverse)                       → the phases on one timeline
 *   motionSlice(plan, name, a, b)             → WAAPI timing for part of one
 *
 * ## The leg picker and the opening motion (SNOW-1033)
 *
 * With no leg open, rail two's lane shows the route's legs as buttons,
 * one per leg, on rail one's scale: a leg's slot runs from `from / N` to
 * `(to + 1) / N` of the lane, which is `legSpan`'s placing with the
 * distance factor taken out, so each segment sits under its leg on the
 * profile. True proportions: a short leg gets no minimum width, and a tap
 * beside it is picked by `nearestRange` instead.
 *
 * Opening a leg is one motion of `MOTION.totalMs` (340 ms) in three
 * phases — PRESS (the segment fills solid, the others fade), STRETCH (it
 * widens to the lane while the card grows) and FILL (it becomes the band
 * strip, then the bank row, the controls and the readout come in).
 * Closing runs the same phases in reverse order on the same total.
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
   * @typedef {{
   *   dy: number,
   *   ground: {x1: number, y1: number, x2: number, y2: number},
   *   up: ?Array<[number, number]>,
   *   down: ?Array<[number, number]>,
   * }} WedgeShape
   *   One glyph's geometry, as `pwaBankRibbonCore.bankWedge` returns it.
   */

  /**
   * @typedef {{
   *   x: number,
   *   index: number,
   *   from: number,
   *   to: number,
   *   roll: number,
   *   halfWidth: number,
   *   dy: number,
   *   ground: {x1: number, y1: number, x2: number, y2: number},
   *   up: ?Array<[number, number]>,
   *   down: ?Array<[number, number]>,
   * }} BankGlyph
   *   One level-ski glyph for a group of segments: its centre `x` (the
   *   group's centre), the sample `index` it draws (the group's largest
   *   |roll|), the group's first and last samples, that sample's signed
   *   roll, the glyph's half-width, and `bankWedge`'s geometry.
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

  /** The px a bank glyph needs to read; a group spans at least this. */
  var GLYPH_MIN_PX = 10;

  /**
   * The most segments one bank glyph may stand for: 3 × 25 m. Past it the
   * row shows the zoom placeholder instead of glyphs.
   */
  var MAX_GROUP = 3;

  /** A glyph's largest half-width in px, bank_ribbon_core.js's. */
  var GLYPH_HALF_WIDTH = 7;

  /** A passage bar is never drawn narrower than this, in px. */
  var PASSAGE_MIN_PX = 6;

  /** Two numbers closer than this are the same point on the axis. */
  var EPSILON = 1e-6;

  /**
   * The lane's vertical layout, in px. The svg is drawn at this height in
   * real pixels (the partial's `h-11`, 44 px), so these are screen units.
   *
   * The band strip (0–10), a 4 px gap, the bank row (14–40: the wedges
   * centred on y 27, rising at most `ribbonHalf` — bank_ribbon_core.js's
   * CAP_PX — either side), then the no-fall bars 4 px tall at 40–44,
   * directly under the bank row, so a capped wedge never covers a
   * passage (SNOW-1031). SNOW-1024 took the distance ticks off the foot,
   * so no dead space sits between the band, the bank row and the readout.
   * SNOW-1019 took the leg's elevation profile off the top: it drew
   * near-flat and said nothing rail one's highlighted leg does not.
   */
  var ROWS = Object.freeze({
    height: 44,
    bandTop: 0,
    bandHeight: 10,
    ribbonY: 27,
    ribbonHalf: 13,
    passageTop: 40,
    passageHeight: 4,
  });

  /**
   * The fall-line tolerance of `FALL_LINE_TOLERANCE_DEG` in
   * apps/routes/services/passages.py, the tolerance its fall-line vote
   * uses. Kept here for that cross-reference; the readout's own words use
   * the narrower `FALL_LINE_DEG` (SNOW-1024's design review).
   */
  var FALL_LINE_TOLERANCE_DEG = 30;

  /** Under this far off the fall line, the readout says "Fall line". */
  var FALL_LINE_DEG = 20;

  /** Past this far off the fall line, a track is traversing. */
  var TRAVERSE_DEG = 65;

  /** Under this slope angle the ground is flat and has no fall line. */
  var FLAT_DEG = 5;

  /** Under this slope angle the ground is a gentle ascent or descent. */
  var GENTLE_DEG = 10;

  /** Under this bank the ground falls away to neither side. */
  var LEVEL_BANK_DEG = 3;

  /** Under this fraction of the lane the readout hangs right of `x`. */
  var ANCHOR_LEFT = 0.25;

  /** Over this fraction of the lane the readout hangs left of `x`. */
  var ANCHOR_RIGHT = 0.75;

  /** A stretch's length is read to the nearest this many metres. */
  var STRETCH_STEP_M = 25;

  /**
   * The opening motion's phases, in ms (SNOW-1033): PRESS, STRETCH and
   * FILL, 340 ms in all.
   */
  var MOTION = Object.freeze({
    pressMs: 80,
    stretchMs: 140,
    fillMs: 120,
    totalMs: 340,
  });

  /**
   * Each phase's easing when opening; closing swaps `ease-out` for
   * `ease-in`, so a reversed stretch starts where the opening one ended.
   */
  var PHASE_EASING = Object.freeze({
    press: 'linear',
    stretch: 'ease-out',
    fill: 'ease-in-out',
  });

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
   * The span a leg opens at: the whole leg, however long (SNOW-1031).
   * Fitted, rail two is an overview; zooming is how it is read.
   *
   * @param {Leg} leg
   * @returns {number}
   */
  function openingSpan(leg) {
    return legLength(leg);
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
   * The range a tap at `x` picks: the one whose drawn extent is nearest
   * (SNOW-1033).
   *
   * Each range is clipped to the view and measured on screen as
   * `[x0, x1]`; its distance is 0 when `x` falls inside and the gap to
   * the nearer edge otherwise. So a leg only a few px wide is still picked
   * by a tap `radiusPx` beside it. On a tie the range whose half-open
   * `[x0, x1)` holds `x` wins — the one `indexAt` puts the cursor in —
   * then the leftmost.
   *
   * @template {{from: number, to: number}} R
   * @param {Array<R>} ranges Leg slots (or any ranges), sample indices
   *   inclusive.
   * @param {number} x The tap's px across the lane.
   * @param {View} view
   * @param {number} width The lane's width in px.
   * @param {number} radiusPx Farther than this, nothing is picked.
   * @returns {?R}
   */
  function nearestRange(ranges, x, view, width, radiusPx) {
    if (!Array.isArray(ranges)) return null;
    /** @type {?R} */
    var best = null;
    var bestDistance = Infinity;
    var bestHolds = false;
    ranges.forEach(function (range) {
      var part = range ? clip(range, view) : null;
      if (!part) return;
      var x0 = xOf(part.from, view, width);
      var x1 = xOf(part.to, view, width);
      var distance = x < x0 ? x0 - x : x > x1 ? x - x1 : 0;
      if (distance > radiusPx) return;
      var holds = x >= x0 && x < x1;
      if (distance < bestDistance || (distance === bestDistance && holds && !bestHolds)) {
        best = range;
        bestDistance = distance;
        bestHolds = holds;
      }
    });
    return best;
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
   * How many whole segments one bank glyph covers at this scale:
   * N = ceil(`GLYPH_MIN_PX` / the width of one segment), never under 1.
   *
   * @param {number} span Samples across the lane.
   * @param {number} width The lane's width in px.
   * @returns {number} Infinity for a lane with no width.
   */
  function glyphGroup(span, width) {
    if (!(width > 0) || !(span > 0)) return Infinity;
    var segPx = width / span;
    // The epsilon keeps an exact 10 / 3 px at N = 3 rather than 4.
    return Math.max(1, Math.ceil(GLYPH_MIN_PX / segPx - EPSILON));
  }

  /**
   * The widest span at which the bank row draws (N ≤ `MAX_GROUP`),
   * clamped to the leg's limits: the span a double-tap zooms to.
   *
   * @param {Leg} leg
   * @param {number} width The lane's width in px.
   * @returns {number}
   */
  function resolveSpan(leg, width) {
    var widest = Math.floor((width * MAX_GROUP) / GLYPH_MIN_PX);
    return clamp(widest, minSpan(leg), legLength(leg));
  }

  /**
   * The bank row for the view: one glyph per group of N whole segments,
   * or the placeholder when N is past `MAX_GROUP` (SNOW-1031).
   *
   * Groups start at `leg.from` and step by N, so they hold still as the
   * view pans. Each glyph draws the segment with the largest |roll| in its
   * group, with its sign — never a mean, which would cancel a zig-zag to
   * level. A group whose banks are all unknown draws nothing: a gap, not
   * a level glyph. The glyph sits on the group's centre with half-width
   * `min(7, group px / 2 − 0.5)`, so neighbours never touch.
   *
   * @param {{
   *   bankWedge: function(number, number, Object): WedgeShape,
   *   banks: Array<?number>,
   *   leg: Leg,
   *   view: View,
   *   width: number,
   *   exaggeration?: number,
   *   capPx?: number,
   *   minFillPx?: number,
   *   y?: number,
   * }} options `bankWedge` is `pwaBankRibbonCore.bankWedge`; the rest as
   *   that function and this module name them.
   * @returns {{placeholder: boolean, glyphs: Array<BankGlyph>}} In lane px.
   */
  function bankGlyphs(options) {
    var view = options.view;
    var width = options.width;
    var leg = options.leg;
    var banks = options.banks;
    /** @type {Array<BankGlyph>} */
    var glyphs = [];
    var n = glyphGroup(view.to - view.from, width);
    if (n > MAX_GROUP) return { placeholder: true, glyphs: glyphs };
    if (!Array.isArray(banks)) return { placeholder: false, glyphs: glyphs };
    var firstGroup = Math.max(0, Math.floor((view.from - leg.from) / n));
    for (var start = leg.from + firstGroup * n; start <= leg.to && start < view.to; start += n) {
      var end = Math.min(leg.to, start + n - 1);
      var index = -1;
      var roll = 0;
      for (var i = start; i <= end; i += 1) {
        var value = banks[i];
        if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        if (index < 0 || Math.abs(value) > Math.abs(roll)) {
          index = i;
          roll = value;
        }
      }
      if (index < 0) continue;
      var left = xOf(start, view, width);
      var right = xOf(end + 1, view, width);
      if (right <= 0 || left >= width) continue;
      var halfWidth = Math.min(GLYPH_HALF_WIDTH, (right - left) / 2 - 0.5);
      if (!(halfWidth > 0)) continue;
      var x = (left + right) / 2;
      var shape = options.bankWedge(x, roll, {
        halfWidth: halfWidth,
        exaggeration: options.exaggeration,
        capPx: options.capPx,
        minFillPx: options.minFillPx,
        y: options.y,
      });
      glyphs.push({
        x: x,
        index: index,
        from: start,
        to: end,
        roll: roll,
        halfWidth: halfWidth,
        dy: shape.dy,
        ground: shape.ground,
        up: shape.up,
        down: shape.down,
      });
    }
    return { placeholder: false, glyphs: glyphs };
  }

  /**
   * A passage bar's box across the lane: its real extent, widened to at
   * least `PASSAGE_MIN_PX` about its centre and kept inside the lane, so a
   * passage stays visible and tappable at the fitted scale (SNOW-1031).
   *
   * @param {?View} part The passage's part inside the view (`clip`).
   * @param {View} view
   * @param {number} width The lane's width in px.
   * @returns {?{x: number, width: number}} Null for no part.
   */
  function passageBox(part, view, width) {
    if (!part) return null;
    var left = xOf(part.from, view, width);
    var right = xOf(part.to, view, width);
    var real = Math.max(0, right - left);
    if (real >= PASSAGE_MIN_PX) return { x: left, width: real };
    var boxWidth = Math.min(PASSAGE_MIN_PX, Math.max(0, width));
    var x = clamp((left + right) / 2 - boxWidth / 2, 0, Math.max(0, width - boxWidth));
    return { x: x, width: boxWidth };
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
   * What the track is doing on the ground at one segment, in the words of
   * SNOW-1024's design review.
   *
   * The slope angle `a` is the ground's steepness and the bank `roll` is
   * how far it tilts ACROSS the track (apps/routes/services/bank.py), so
   * tan|roll| = tan(a) · |sin δ|, where δ is the angle between the track
   * and the fall line. That gives δ from the two numbers the wire already
   * carries, clamped to [0, 1] before the `asin` because both are rounded
   * to whole degrees.
   *
   *   a < 5°            → 'flat': no fall line to be on or off;
   *   5° ≤ a < 10°      → 'gentle-descent' on a descending leg,
   *                       'gentle-ascent' on a climbing one, no side;
   *   δ < 20°           → 'fall-line', either way along it;
   *   20° ≤ δ ≤ 65°     → 'falls-away': the ground drops to one side;
   *   δ > 65°           → 'traverse'.
   *
   * `side` is where the ground falls away: 'right' for a positive roll
   * (bank.py's sign), 'left' for a negative one, and null under 3°, on
   * flat or gentle ground, or with the bank unknown. On ground of 10° or
   * more a bank under 3° puts δ under 17.4°, so 'falls-away' and
   * 'traverse' always carry a side when the bank is known.
   *
   * @param {?number} angle The segment's slope angle, degrees.
   * @param {?number} roll The segment's signed bank, degrees.
   * @param {boolean} climbing Whether the open leg climbs.
   * @returns {?{term: string, side: ?string}} Null when the angle is
   *   unknown, or the bank is unknown on ground of 10° or more — below
   *   that the angle alone decides the term.
   */
  function trackAttitude(angle, roll, climbing) {
    if (typeof angle !== 'number' || !Number.isFinite(angle)) return null;
    if (angle < FLAT_DEG) return { term: 'flat', side: null };
    if (angle < GENTLE_DEG) {
      return { term: climbing ? 'gentle-ascent' : 'gentle-descent', side: null };
    }
    if (typeof roll !== 'number' || !Number.isFinite(roll)) return null;
    var rad = Math.PI / 180;
    var sinDelta = clamp(Math.tan(Math.abs(roll) * rad) / Math.tan(angle * rad), 0, 1);
    var delta = Math.asin(sinDelta) / rad;
    var term;
    if (delta < FALL_LINE_DEG) {
      term = 'fall-line';
    } else if (delta > TRAVERSE_DEG) {
      term = 'traverse';
    } else {
      term = 'falls-away';
    }
    var side = Math.abs(roll) < LEVEL_BANK_DEG ? null : roll > 0 ? 'right' : 'left';
    return { term: term, side: side };
  }

  /**
   * Where the readout sits under the lane, stepped rather than clamped.
   *
   * In the lane's left quarter the readout is left-aligned and starts at
   * `x`; in the middle half it is centred on `x`; in the right quarter it
   * is right-aligned and ends at `x`. The step is the design review's
   * (SNOW-1024): a smooth clamp would slide the text against the line it
   * belongs to.
   *
   * @param {number} x The cursor's (or the selection's middle's) px.
   * @param {number} width The lane's width in px.
   * @returns {{align: string, left: number}} `align` is 'left', 'center'
   *   or 'right'; `left` is the px the anchor sits at, which is `x`.
   */
  function readoutAnchor(x, width) {
    var fraction = width > 0 ? x / width : 0;
    var align = 'center';
    if (fraction < ANCHOR_LEFT) align = 'left';
    else if (fraction > ANCHOR_RIGHT) align = 'right';
    return { align: align, left: x };
  }

  /**
   * A stretch's length to the nearest 25 m, never under 25 m.
   *
   * @param {number} metres
   * @returns {number}
   */
  function roundStretch(metres) {
    if (!Number.isFinite(metres)) return STRETCH_STEP_M;
    return Math.max(STRETCH_STEP_M, Math.round(metres / STRETCH_STEP_M) * STRETCH_STEP_M);
  }

  /**
   * The leg picker's segments: one per leg, on rail one's scale
   * (SNOW-1033).
   *
   * `left` and `width` are fractions of the lane, `from / N` and
   * `(to + 1 − from) / N`, with no minimum width. The slots come back in
   * route order; a leg that is malformed or outside the route is dropped.
   *
   * @template {{from: number, to: number}} L
   * @param {Array<L>} legs The route's legs, sample indices inclusive.
   * @param {number} sampleCount N, the segments the legs index.
   * @returns {Array<{leg: L, left: number, width: number}>}
   */
  function legSlots(legs, sampleCount) {
    if (!Array.isArray(legs) || !(sampleCount > 0)) return [];
    return legs
      .filter(function (leg) {
        return !!leg && Number.isInteger(leg.from) && Number.isInteger(leg.to)
          && leg.from >= 0 && leg.to >= leg.from && leg.to < sampleCount;
      })
      .slice()
      .sort(function (a, b) { return a.from - b.from; })
      .map(function (leg) {
        return {
          leg: leg,
          left: leg.from / sampleCount,
          width: (leg.to + 1 - leg.from) / sampleCount,
        };
      });
  }

  /**
   * @typedef {{name: string, start: number, end: number, easing: string}} Phase
   *   One phase of the motion, its start and end in ms from the first
   *   frame.
   */

  /**
   * The motion's phases laid on one timeline (SNOW-1033).
   *
   * Opening runs press (0–80), stretch (80–220) and fill (220–340);
   * closing runs fill, stretch and press on the same 340 ms, with the
   * stretch eased in rather than out.
   *
   * @param {boolean} reverse True for closing.
   * @returns {{totalMs: number, phases: Array<Phase>}}
   */
  function motionPlan(reverse) {
    var order = reverse
      ? [['fill', MOTION.fillMs], ['stretch', MOTION.stretchMs], ['press', MOTION.pressMs]]
      : [['press', MOTION.pressMs], ['stretch', MOTION.stretchMs], ['fill', MOTION.fillMs]];
    var at = 0;
    var phases = order.map(function (entry) {
      var name = /** @type {string} */ (entry[0]);
      /** @type {string} */
      var easing = PHASE_EASING[/** @type {'press'|'stretch'|'fill'} */ (name)];
      if (reverse && easing === 'ease-out') easing = 'ease-in';
      var phase = { name: name, start: at, end: at + /** @type {number} */ (entry[1]), easing: easing };
      at = phase.end;
      return phase;
    });
    return { totalMs: at, phases: phases };
  }

  /**
   * The WAAPI timing for the part of one phase between fractions `a` and
   * `b` of it (SNOW-1033).
   *
   * @param {{phases: Array<Phase>}} plan A `motionPlan`.
   * @param {string} name 'press', 'stretch' or 'fill'.
   * @param {number} a Where the part starts, 0–1 of the phase.
   * @param {number} b Where it ends, 0–1 of the phase, `b ≥ a`.
   * @returns {{delay: number, duration: number, easing: string}}
   */
  function motionSlice(plan, name, a, b) {
    var phase = plan.phases.find(function (p) { return p.name === name; });
    if (!phase) throw new RangeError('no phase ' + name);
    var length = phase.end - phase.start;
    var from = clamp(a, 0, 1);
    var to = clamp(b, from, 1);
    return {
      delay: phase.start + from * length,
      duration: (to - from) * length,
      easing: phase.easing,
    };
  }

  self.pwaRouteRailTwoCore = Object.freeze({
    FALL_LINE_TOLERANCE_DEG: FALL_LINE_TOLERANCE_DEG,
    FALL_LINE_DEG: FALL_LINE_DEG,
    TRAVERSE_DEG: TRAVERSE_DEG,
    GENTLE_DEG: GENTLE_DEG,
    trackAttitude: trackAttitude,
    readoutAnchor: readoutAnchor,
    roundStretch: roundStretch,
    MIN_SPAN: MIN_SPAN,
    GLYPH_MIN_PX: GLYPH_MIN_PX,
    MAX_GROUP: MAX_GROUP,
    PASSAGE_MIN_PX: PASSAGE_MIN_PX,
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
    nearestRange: nearestRange,
    glyphGroup: glyphGroup,
    resolveSpan: resolveSpan,
    bankGlyphs: bankGlyphs,
    passageBox: passageBox,
    legProfile: legProfile,
    legFigures: legFigures,
    legSlots: legSlots,
    MOTION: MOTION,
    motionPlan: motionPlan,
    motionSlice: motionSlice,
  });
})();
