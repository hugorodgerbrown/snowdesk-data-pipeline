/*
 * static/js/route_rail_two_core.js — rail two's pure half: one leg, zoomed,
 * with the ground under it (SNOW-1019).
 *
 * Rail two opens under rail one when a leg is pressed. It draws the open
 * leg on three rows sharing one x-axis — a strip of slope bands, the track
 * row (labelled stretches fitted, level-ski wedges showing its bank once
 * zoomed in: SNOW-1031, SNOW-1044), and one bar per no-fall passage — and
 * it pans and zooms within the leg. (It drew the
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
 * ## The track row: words, or wedges (SNOW-1044)
 *
 * The row under the band strip says what the TRACK does on that ground.
 * A bank glyph needs `GLYPH_MIN_PX` (10 px) to read, and a segment at the
 * fitted scale can be under 1 px, so the row has two modes (`trackMode`):
 *
 *   under 10 px a segment — WORDS: one labelled block per STRETCH of the
 *     leg (`stretches`), each a run of one `segmentWord`, at least
 *     `MIN_STRETCH_SEGMENTS` (6 × 25 m = 150 m) long;
 *   at 10 px or more — WEDGES: one level-ski wedge per segment
 *     (`bankGlyphs`, bank_ribbon_core.js's `bankWedge`), with a dashed tick
 *     at each stretch boundary, so the words' stretches stay placed.
 *
 * The words summarise what the wedges show, so the two never disagree:
 * Gentle is ground under `GROUND_STEEP_DEG` (25°); on steeper ground a
 * bank of `TRAVERSE_BANK_DEG` (20°) or more is Traverse and a smaller one
 * Steep — the track is with the fall line. On a climb Gentle is Skin, and
 * a gradient along the track of `BOOTPACK_GRADIENT_DEG` (25°) or more is
 * Bootpack. The gradient is `segmentGradients`, read off the geometry's
 * heights — the terrain model's since SNOW-1043. A run shorter than 150 m
 * is merged into its longer neighbour, the shortest first, until none is
 * left (a leg shorter than 150 m is one stretch).
 *
 * A KICK TURN (`kickTurns`) is the bank changing side between one segment
 * and the next on a climb, both banking `KICK_TURN_BANK_DEG` (15°) or
 * more; the row marks each at every zoom. `resolveSpan` is the widest
 * span at which the wedges draw, the target a double-tap zooms to. The
 * band strip and the passages are drawn per segment at every zoom,
 * however thin.
 *
 * ## Slope bands
 *
 * A band is a run of consecutive segments sharing one slope class
 * (`pwaRouteSlopeCore.classify`), UNMERGED: a one-segment 35° band between
 * two 30° ones is its own band, because that one segment is the reading.
 * Consecutive unknown (null) angles make a run of their own, and a null
 * never joins a known class — "not known" must not read as the class
 * beside it. A band too thin to press at the fitted scale is reached by
 * zooming, never by folding it into a neighbour (SNOW-1032's revision).
 *
 * Exports (frozen `self.pwaRouteRailTwoCore`):
 *
 *   MIN_SPAN                                  → the narrowest span, 6
 *   GLYPH_MIN_PX                              → the px a bank glyph needs, 10
 *   GROUND_STEEP_DEG, TRAVERSE_BANK_DEG,
 *   BOOTPACK_GRADIENT_DEG, KICK_TURN_BANK_DEG,
 *   MIN_STRETCH_SEGMENTS, STEEP_TERRAIN_DEG   → the track row's thresholds
 *   PASSAGE_MIN_PX                            → a passage bar's least width, 6
 *   ROWS                                      → the lane's vertical layout
 *   bandRuns(angles, classify, range?)        → [{from, to, classIndex}]
 *   selectionBox(part, view, width, minPx)    → {x, w} of the drawn box
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
 *   steepestBand(bands, x, view, width, radiusPx) → the band a tap picks
 *   trackMode(view, width)                    → 'words' or 'wedges'
 *   resolveSpan(leg, width)                   → the widest span the wedges draw at
 *   bankGlyphs(options)                       → one wedge per segment in view
 *   segmentGradients(profile, sampleCount, spanM) → signed gradient per segment
 *   segmentWord(angle, bank, gradient, climbing) → the track's word there
 *   stretches(leg, angles, banks, gradients)  → [{from, to, word}], ≥ 150 m
 *   kickTurns(leg, banks)                     → the segments a kick turn lands on
 *   steepLength(leg, angles, sampleCount, spanM) → metres of ground ≥ 30°
 *   bankSide(bank)                            → 'left', 'right' or null
 *   passageBox(part, view, width)             → a passage bar's {x, width} in px
 *   legProfile(profile, leg, sampleCount, clipRun) → the leg in sample units
 *   legFigures(legProfile, leg, sampleCount, spanM) → distance, ascent, descent
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
   *   roll: number,
   *   halfWidth: number,
   *   dy: number,
   *   ground: {x1: number, y1: number, x2: number, y2: number},
   *   up: ?Array<[number, number]>,
   *   down: ?Array<[number, number]>,
   * }} BankGlyph
   *   One level-ski glyph for one segment: its centre `x`, the sample
   *   `index` it draws, that sample's signed roll, the glyph's half-width,
   *   and `bankWedge`'s geometry.
   */

  /**
   * @typedef {{from: number, to: number, word: string}} Stretch
   *   A run of one track word, sample indices inclusive. `word` is one of
   *   'gentle', 'skin', 'traverse', 'steep', 'bootpack' or 'unknown'.
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

  /**
   * The px a bank glyph needs to read. A segment this wide or wider puts
   * the track row in wedges; a narrower one, in words (`trackMode`).
   */
  var GLYPH_MIN_PX = 10;

  /** A segment's length on the wire, metres: the sampler's stride. */
  var STRIDE_M = 25;

  /** Ground under this is Gentle (or Skin on a climb), degrees. */
  var GROUND_STEEP_DEG = 25;

  /** On steep ground, a bank of this or more is a Traverse, degrees. */
  var TRAVERSE_BANK_DEG = 20;

  /** On a climb, a gradient of this or more along the track is a Bootpack. */
  var BOOTPACK_GRADIENT_DEG = 25;

  /** Both sides of a kick turn bank at least this far, degrees. */
  var KICK_TURN_BANK_DEG = 15;

  /** The shortest stretch the words show: 6 segments, 150 m. */
  var MIN_STRETCH_SEGMENTS = 6;

  /** Ground of this or more counts as steep terrain on the card, degrees. */
  var STEEP_TERRAIN_DEG = 30;

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
   * The band strip (0–10), a 4 px gap, the track row (14–40: the wedges
   * centred on y 27, rising at most `ribbonHalf` — bank_ribbon_core.js's
   * CAP_PX — either side; fitted, the stretch blocks `blockTop` to
   * `blockTop + blockHeight`, with a kick turn's chevron above them), then
   * the no-fall bars 4 px tall at 40–44, directly under the track row, so
   * a capped wedge never covers a passage (SNOW-1031, SNOW-1044). SNOW-1024 took the distance ticks off the foot,
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
    blockTop: 20,
    blockHeight: 16,
    passageTop: 40,
    passageHeight: 4,
  });

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
   * The selection box drawn round a part of a range, at least `minPx`
   * wide (SNOW-1032).
   *
   * Centred on the part and clamped to the lane, so a one-sample band
   * still gets a box a reader can see. Only the box is widened: the
   * readout and the map read the range's real extent.
   *
   * @param {View} part The visible part of the range (a `clip` result).
   * @param {View} view
   * @param {number} width The lane's width in px.
   * @param {number} minPx The narrowest box.
   * @returns {{x: number, w: number}} Its left edge and width, px.
   */
  function selectionBox(part, view, width, minPx) {
    var x0 = xOf(part.from, view, width);
    var x1 = xOf(part.to, view, width);
    var w = Math.min(Math.max(minPx, x1 - x0), Math.max(0, width));
    var x = clamp((x0 + x1) / 2 - w / 2, 0, Math.max(0, width - w));
    return { x: x, w: w };
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
   * (SNOW-1032, SNOW-1033).
   *
   * Each range is clipped to the view and measured on screen as
   * `[x0, x1]`; its distance is 0 when `x` falls inside and the gap to
   * the nearer edge otherwise. So a leg, band or passage only a few px
   * wide is still picked by a tap `radiusPx` beside it. On a tie the range whose half-open
   * `[x0, x1)` holds `x` wins — the one `indexAt` puts the cursor in —
   * then the leftmost.
   *
   * @template {{from: number, to: number}} R
   * @param {Array<R>} ranges Leg slots, bands or passages, sample
   *   indices inclusive.
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
   * The band a tap at `x` picks: the STEEPEST within `radiusPx` (SNOW-1032).
   *
   * Bands tile the leg edge to edge, so "nearest extent" always answers
   * with the band under the finger and never widens a thin one: a
   * one-segment band on a fitted long leg stays about 2 px to a tap.
   * Picking the steepest class among every band whose on-screen extent
   * comes within `radiusPx` gives a thin steep band inside gentle ground a
   * 44 px target — the band a reader is most likely hunting for — while a
   * thin gentle band beside steep ground gets none, which errs to the
   * conservative side. Unknown (null) ranks below every class. Ties go to
   * the nearer extent, then the one holding `x`, then the leftmost.
   *
   * @template {{from: number, to: number, classIndex: ?number}} B
   * @param {Array<B>} bands `bandRuns` for the open leg.
   * @param {number} x The tap's px across the lane.
   * @param {View} view
   * @param {number} width The lane's width in px.
   * @param {number} radiusPx Farther than this, a band is not considered.
   * @returns {?B}
   */
  function steepestBand(bands, x, view, width, radiusPx) {
    if (!Array.isArray(bands)) return null;
    /** @type {?B} */
    var best = null;
    var bestRank = -Infinity;
    var bestDistance = Infinity;
    var bestHolds = false;
    bands.forEach(function (band) {
      var part = band ? clip(band, view) : null;
      if (!part) return;
      var x0 = xOf(part.from, view, width);
      var x1 = xOf(part.to, view, width);
      var distance = x < x0 ? x0 - x : x > x1 ? x - x1 : 0;
      if (distance > radiusPx) return;
      var rank = typeof band.classIndex === 'number' ? band.classIndex : -1;
      var holds = x >= x0 && x < x1;
      var better = rank > bestRank
        || (rank === bestRank && distance < bestDistance)
        || (rank === bestRank && distance === bestDistance && holds && !bestHolds);
      if (better) {
        best = band;
        bestRank = rank;
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
   * Which way the track row draws at this scale (SNOW-1044): 'wedges'
   * once a segment is `GLYPH_MIN_PX` (10 px) wide or more, 'words' below.
   *
   * @param {View} view
   * @param {number} width The lane's width in px.
   * @returns {'words'|'wedges'}
   */
  function trackMode(view, width) {
    var span = view.to - view.from;
    if (!(width > 0) || !(span > 0)) return 'words';
    // The epsilon keeps an exact 10 px segment in wedges.
    return width / span >= GLYPH_MIN_PX - EPSILON ? 'wedges' : 'words';
  }

  /**
   * The widest span at which the wedges draw — a segment of at least
   * `GLYPH_MIN_PX` — clamped to the leg's limits: the span a double-tap
   * zooms to.
   *
   * @param {Leg} leg
   * @param {number} width The lane's width in px.
   * @returns {number}
   */
  function resolveSpan(leg, width) {
    var widest = Math.floor(width / GLYPH_MIN_PX + EPSILON);
    return clamp(widest, minSpan(leg), legLength(leg));
  }

  /**
   * The wedges for the view: one level-ski glyph per segment, each on its
   * segment's centre with half-width `min(7, segment px / 2 − 0.5)`, so
   * neighbours never touch (SNOW-1031). A segment whose bank is unknown
   * draws nothing: a gap, not a level glyph. Called in wedges mode only
   * (`trackMode`); fitted, the row is words.
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
   * @returns {Array<BankGlyph>} In lane px, left to right.
   */
  function bankGlyphs(options) {
    var view = options.view;
    var width = options.width;
    var leg = options.leg;
    var banks = options.banks;
    /** @type {Array<BankGlyph>} */
    var glyphs = [];
    if (!Array.isArray(banks)) return glyphs;
    var first = Math.max(leg.from, Math.floor(view.from + EPSILON));
    var last = Math.min(leg.to, Math.ceil(view.to - EPSILON) - 1);
    for (var i = first; i <= last; i += 1) {
      var roll = banks[i];
      if (typeof roll !== 'number' || !Number.isFinite(roll)) continue;
      var left = xOf(i, view, width);
      var right = xOf(i + 1, view, width);
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
        index: i,
        roll: roll,
        halfWidth: halfWidth,
        dy: shape.dy,
        ground: shape.ground,
        up: shape.up,
        down: shape.down,
      });
    }
    return glyphs;
  }

  /**
   * @param {*} value
   * @returns {value is number}
   */
  function isKnown(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  /**
   * A profile's height at a distance, linear between its points; null
   * outside every run.
   *
   * @param {Array<Array<{d: number, e: number}>>} runs
   * @param {number} d
   * @returns {?number}
   */
  function heightAt(runs, d) {
    for (var r = 0; r < runs.length; r += 1) {
      var run = runs[r];
      if (!run.length || d < run[0].d - EPSILON || d > run[run.length - 1].d + EPSILON) continue;
      if (run.length === 1) return run[0].e;
      var lo = 0;
      var hi = run.length - 1;
      while (hi - lo > 1) {
        var mid = (lo + hi) >> 1;
        if (run[mid].d <= d) lo = mid;
        else hi = mid;
      }
      var a = run[lo];
      var b = run[hi];
      var gap = b.d - a.d;
      return gap > 0 ? a.e + (b.e - a.e) * clamp((d - a.d) / gap, 0, 1) : a.e;
    }
    return null;
  }

  /**
   * The signed gradient along the track at each segment, in degrees:
   * positive where the track rises in its own direction (SNOW-1044).
   *
   * Rise over run between the heights one stride (25 m of the route)
   * either side of the segment's midpoint, clamped to the track's ends,
   * read off `readProfile`'s runs — the geometry's z, which the feed
   * fills from the terrain model (SNOW-1043). The segment's midpoint sits
   * on the profile by share, as the rest of this module places it.
   *
   * @param {{runs: Array<Array<{d: number, e: number}>>, distanceM: number,
   *   hasElevation: boolean}} profile A `readProfile` result.
   * @param {number} sampleCount N, the length of `slope.angles`.
   * @param {number} spanM The route's length, rail one's `distance_m`.
   * @returns {Array<?number>} N entries, null where a height is missing.
   */
  function segmentGradients(profile, sampleCount, spanM) {
    var count = sampleCount > 0 ? Math.floor(sampleCount) : 0;
    /** @type {Array<?number>} */
    var out = new Array(count).fill(null);
    if (!profile || !profile.hasElevation || !(profile.distanceM > 0) || !count) return out;
    var distanceM = profile.distanceM;
    var half = STRIDE_M * (spanM > 0 ? distanceM / spanM : 1);
    for (var i = 0; i < count; i += 1) {
      var mid = ((i + 0.5) / count) * distanceM;
      var a = Math.max(0, mid - half);
      var b = Math.min(distanceM, mid + half);
      if (!(b - a > EPSILON)) continue;
      var ea = heightAt(profile.runs, a);
      var eb = heightAt(profile.runs, b);
      if (ea === null || eb === null) continue;
      out[i] = (Math.atan((eb - ea) / (b - a)) * 180) / Math.PI;
    }
    return out;
  }

  /**
   * The track row's word for one segment (SNOW-1044).
   *
   *   angle unknown                               → 'unknown';
   *   on a climb, gradient ≥ 25°                  → 'bootpack';
   *   ground < 25°                                → 'skin' on a climb,
   *                                                 'gentle' otherwise;
   *   ground ≥ 25°, |bank| ≥ 20°                  → 'traverse';
   *   ground ≥ 25°, |bank| < 20° or bank unknown  → 'steep'.
   *
   * @param {?number} angle The slope angle, degrees.
   * @param {?number} bank The signed bank, degrees.
   * @param {?number} gradient The signed gradient along the track, degrees.
   * @param {boolean} climbing Whether the leg climbs.
   * @returns {string}
   */
  function segmentWord(angle, bank, gradient, climbing) {
    if (!isKnown(angle)) return 'unknown';
    if (climbing && isKnown(gradient) && gradient >= BOOTPACK_GRADIENT_DEG) return 'bootpack';
    if (angle < GROUND_STEEP_DEG) return climbing ? 'skin' : 'gentle';
    if (isKnown(bank) && Math.abs(bank) >= TRAVERSE_BANK_DEG) return 'traverse';
    return 'steep';
  }

  /**
   * Join neighbouring stretches that carry the same word, in place.
   *
   * @param {Array<Stretch>} runs
   */
  function coalesce(runs) {
    for (var j = runs.length - 1; j > 0; j -= 1) {
      if (runs[j].word === runs[j - 1].word) {
        runs[j - 1].to = runs[j].to;
        runs.splice(j, 1);
      }
    }
  }

  /**
   * The leg's stretches: runs of one `segmentWord`, none shorter than
   * `MIN_STRETCH_SEGMENTS` (150 m) unless the leg itself is (SNOW-1044).
   *
   * The shortest run under the minimum (the leftmost on a tie) is merged
   * into its LONGER neighbour (the earlier on a tie) and takes its word;
   * neighbours left carrying one word are joined; and that repeats until
   * no short run is left or the leg is one stretch. Every segment of the
   * leg stays in exactly one stretch.
   *
   * @param {{from: number, to: number, climbing?: boolean}} leg
   * @param {Array<?number>} angles `slope.angles`.
   * @param {Array<?number>} banks `slope.banks`.
   * @param {?Array<?number>} gradients `segmentGradients`, or null.
   * @returns {Array<Stretch>} Left to right, touching end to end.
   */
  function stretches(leg, angles, banks, gradients) {
    var climbing = !!leg.climbing;
    /** @type {Array<Stretch>} */
    var runs = [];
    for (var i = leg.from; i <= leg.to; i += 1) {
      var word = segmentWord(
        Array.isArray(angles) ? angles[i] : null,
        Array.isArray(banks) ? banks[i] : null,
        Array.isArray(gradients) ? gradients[i] : null,
        climbing,
      );
      var open = runs.length ? runs[runs.length - 1] : null;
      if (open && open.word === word) {
        open.to = i;
      } else {
        runs.push({ from: i, to: i, word: word });
      }
    }
    /** @param {Stretch} run */
    function size(run) { return run.to - run.from + 1; }
    while (runs.length > 1) {
      var k = -1;
      for (var j = 0; j < runs.length; j += 1) {
        if (size(runs[j]) < MIN_STRETCH_SEGMENTS && (k < 0 || size(runs[j]) < size(runs[k]))) k = j;
      }
      if (k < 0) break;
      var left = k > 0 ? runs[k - 1] : null;
      var right = k < runs.length - 1 ? runs[k + 1] : null;
      var into = !right || (left && size(left) >= size(right)) ? left : right;
      if (!into) break;
      into.from = Math.min(into.from, runs[k].from);
      into.to = Math.max(into.to, runs[k].to);
      runs.splice(k, 1);
      coalesce(runs);
    }
    return runs;
  }

  /**
   * The kick turns on a leg (SNOW-1044): each segment `i` whose bank and
   * its predecessor's are both known, on opposite sides, and both at
   * least `KICK_TURN_BANK_DEG` (15°). A climb only — a descent's
   * side-changes are turns, not kick turns.
   *
   * @param {{from: number, to: number, climbing?: boolean}} leg
   * @param {Array<?number>} banks `slope.banks`.
   * @returns {Array<number>} The second segment of each pair, ascending.
   */
  function kickTurns(leg, banks) {
    /** @type {Array<number>} */
    var out = [];
    if (!leg.climbing || !Array.isArray(banks)) return out;
    for (var i = leg.from + 1; i <= leg.to; i += 1) {
      var before = banks[i - 1];
      var after = banks[i];
      if (!isKnown(before) || !isKnown(after)) continue;
      if (Math.abs(before) < KICK_TURN_BANK_DEG || Math.abs(after) < KICK_TURN_BANK_DEG) continue;
      if ((before > 0) !== (after > 0)) out.push(i);
    }
    return out;
  }

  /**
   * The ground of `STEEP_TERRAIN_DEG` (30°) or more on a leg, in metres:
   * its segments at that angle times one segment's share of the route.
   *
   * @param {Leg} leg
   * @param {Array<?number>} angles `slope.angles`.
   * @param {number} sampleCount N.
   * @param {number} spanM The route's length, rail one's `distance_m`.
   * @returns {?number} Null with no slope record or no length to share.
   */
  function steepLength(leg, angles, sampleCount, spanM) {
    if (!Array.isArray(angles) || !angles.length || !(sampleCount > 0) || !(spanM > 0)) {
      return null;
    }
    var count = 0;
    for (var i = leg.from; i <= leg.to; i += 1) {
      var angle = angles[i];
      if (isKnown(angle) && angle >= STEEP_TERRAIN_DEG) count += 1;
    }
    return (count / sampleCount) * spanM;
  }

  /**
   * The side the ground falls away to: 'right' for a positive bank
   * (bank.py's sign), 'left' for a negative one, null for a bank under
   * 3° or an unknown one.
   *
   * @param {?number} bank
   * @returns {?string}
   */
  function bankSide(bank) {
    if (!isKnown(bank) || Math.abs(bank) < LEVEL_BANK_DEG) return null;
    return bank > 0 ? 'right' : 'left';
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
   * The leg's figures: its length, ascent and descent (SNOW-1044's card
   * title reads the ascent on a climb and the descent otherwise).
   *
   * The distance is the leg's share of the route's length, so it agrees
   * with rail one's ticks. Ascent and descent sum the profile's own
   * steps; both are null for a leg with no elevation.
   *
   * @param {LegProfile} lp A `legProfile` result.
   * @param {Leg} leg
   * @param {number} sampleCount N.
   * @param {number} spanM The route's length, rail one's `distance_m`.
   * @returns {{distance_m: ?number, ascent_m: ?number, descent_m: ?number}}
   */
  function legFigures(lp, leg, sampleCount, spanM) {
    var distance = sampleCount > 0 && spanM > 0
      ? (legLength(leg) / sampleCount) * spanM
      : null;
    if (!lp.runs.length) {
      return { distance_m: distance, ascent_m: null, descent_m: null };
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
    return { distance_m: distance, ascent_m: ascent, descent_m: descent };
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
    readoutAnchor: readoutAnchor,
    roundStretch: roundStretch,
    MIN_SPAN: MIN_SPAN,
    GLYPH_MIN_PX: GLYPH_MIN_PX,
    GROUND_STEEP_DEG: GROUND_STEEP_DEG,
    TRAVERSE_BANK_DEG: TRAVERSE_BANK_DEG,
    BOOTPACK_GRADIENT_DEG: BOOTPACK_GRADIENT_DEG,
    KICK_TURN_BANK_DEG: KICK_TURN_BANK_DEG,
    MIN_STRETCH_SEGMENTS: MIN_STRETCH_SEGMENTS,
    STEEP_TERRAIN_DEG: STEEP_TERRAIN_DEG,
    PASSAGE_MIN_PX: PASSAGE_MIN_PX,
    ROWS: ROWS,
    bandRuns: bandRuns,
    selectionBox: selectionBox,
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
    steepestBand: steepestBand,
    trackMode: trackMode,
    resolveSpan: resolveSpan,
    bankGlyphs: bankGlyphs,
    segmentGradients: segmentGradients,
    segmentWord: segmentWord,
    stretches: stretches,
    kickTurns: kickTurns,
    steepLength: steepLength,
    bankSide: bankSide,
    passageBox: passageBox,
    legProfile: legProfile,
    legFigures: legFigures,
    legSlots: legSlots,
    MOTION: MOTION,
    motionPlan: motionPlan,
    motionSlice: motionSlice,
  });
})();
