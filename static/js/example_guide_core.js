/*
 * static/js/example_guide_core.js — the pure half of the example route
 * walkthrough (SNOW-1230).
 *
 * The map's welcome card offers "Explore an example route": one real tour,
 * opened on a past day with a full bulletin, and a four-step guide over it
 * (static/js/map_example_guide.js, the DOM half). Each step but the first
 * and last is something the visitor DOES rather than reads, and moves on
 * by itself when they have done it:
 *
 *   intro    — "we've moved the map to <day>"; confirmed with Got it,
 *              because the date jump is the one thing that must be said
 *              before anything else happens.
 *   drag     — the point is already placed (startIndex); move it along
 *              the profile across at least DRAG_FRACTION of the route (a
 *              tap is not a drag). Next appears after NEXT_AFTER_MS.
 *   zoom     — zoom in until the route draws in slope classes, which it
 *              does from ROUTE_SLOPE_MINZOOM (route_drawing_core.js);
 *              "Show me" flies there, to the steepest stretch.
 *   colours  — what the colours mean; confirmed with Finish.
 *
 * This module owns no DOM and reads no globals: step order, the two
 * completion tests, the zoom bar's fill, the day's wording and the URL
 * parameter the example arrives with. Unit-tested in
 * tests/js/test_example_guide_core.js.
 *
 * Exports (frozen `self.pwaExampleGuideCore`):
 *
 *   STEPS, DRAG_FRACTION, NEXT_AFTER_MS, EXAMPLE_PARAM
 *   stepNumber(step)                     — 1-based position, 0 if unknown
 *   nextStep(step)                       — the step after, or null
 *   createDragTracker(sampleCount)       — {record(index) → bool, reset()}
 *   startIndex(sampleCount)              — where the point is placed first
 *   steepestIndex(angles)                — where "Show me" flies
 *   zoomProgress(zoom, startZoom, target) — 0..1, the zoom bar's fill
 *   formatDay(iso, locale)               — "10 April 2026"
 *   formatShortDay(iso, locale)          — "10 Apr"
 *   isExampleArrival(search)             — whether ?example=1 is present
 *   withoutExampleParam(search)          — the query string without it
 */

// @ts-check

(function () {
  'use strict';

  /** @typedef {'intro'|'drag'|'zoom'|'colours'} Step */

  /** @type {ReadonlyArray<Step>} */
  var STEPS = Object.freeze(['intro', 'drag', 'zoom', 'colours']);

  // About a fifth of the profile: far enough that the dot is seen to
  // travel on the map, short enough that nobody has to drag end to end.
  var DRAG_FRACTION = 0.2;

  // SNOW-1244: where the guide places the point before the drag step, as a
  // share of the route — near the start, so a drag has room to run.
  var START_FRACTION = 0.1;

  // SNOW-1244: how long a step that waits on a gesture waits before it also
  // offers Next. Long enough to try the gesture; a tester whose drag did not
  // register was left with no way on.
  var NEXT_AFTER_MS = 8000;

  // The parameter routes:example adds to the map URL. It starts the guide
  // and is stripped once read, so a reload does not start it again.
  var EXAMPLE_PARAM = 'example';

  /**
   * A step's 1-based position, for "2 of 4".
   *
   * @param {string} step
   * @returns {number} 0 for a name that is not a step.
   */
  function stepNumber(step) {
    return STEPS.indexOf(/** @type {Step} */ (step)) + 1;
  }

  /**
   * The step after `step`.
   *
   * @param {string} step
   * @returns {?Step} null after the last step, or for an unknown one.
   */
  function nextStep(step) {
    var i = STEPS.indexOf(/** @type {Step} */ (step));
    if (i < 0 || i === STEPS.length - 1) return null;
    return STEPS[i + 1];
  }

  /**
   * Track how far the point has travelled along a route of `sampleCount`
   * segments. The span is the widest stretch between any two indices seen,
   * so a drag back and forth over the same ground does not add up, and a
   * single tap — one index — spans nothing.
   *
   * @param {number} sampleCount
   * @returns {{record: function(?number): boolean, reset: function(): void, span: function(): number}}
   */
  function createDragTracker(sampleCount) {
    /** @type {?number} */
    var low = null;
    /** @type {?number} */
    var high = null;
    var last = Math.max(1, sampleCount - 1);

    /** @returns {number} The fraction of the route spanned so far. */
    function span() {
      if (low === null || high === null) return 0;
      return (high - low) / last;
    }

    return Object.freeze({
      /**
       * Note an index the cursor visited.
       *
       * @param {?number} index null (no point) is ignored.
       * @returns {boolean} Whether the drag has now spanned enough.
       */
      record: function (index) {
        if (typeof index === 'number' && isFinite(index)) {
          low = low === null ? index : Math.min(low, index);
          high = high === null ? index : Math.max(high, index);
        }
        return sampleCount > 1 && span() >= DRAG_FRACTION;
      },
      reset: function () {
        low = null;
        high = null;
      },
      span: span,
    });
  }

  /**
   * The index the guide places the point at before the drag step.
   *
   * @param {number} sampleCount The route's cursor segments.
   * @returns {?number} null for a route with none.
   */
  function startIndex(sampleCount) {
    if (!(sampleCount > 0)) return null;
    return Math.floor(START_FRACTION * (sampleCount - 1));
  }

  /**
   * The segment with the steepest ground under it — where "Show me" flies,
   * because that is where the slope colours have the most to say.
   *
   * @param {Array<?number>} angles The slope per cursor segment, degrees.
   * @returns {?number} The first steepest index; null with no angle at all.
   */
  function steepestIndex(angles) {
    var best = null;
    var bestAngle = -Infinity;
    (angles || []).forEach(function (angle, index) {
      if (typeof angle === 'number' && isFinite(angle) && angle > bestAngle) {
        best = index;
        bestAngle = angle;
      }
    });
    return best;
  }

  /**
   * How full the zoom step's bar is: the share of the way from the zoom the
   * step began at to the zoom where slope classes appear. Already past it
   * is full; zooming out below the start is empty.
   *
   * @param {number} zoom The camera's zoom now.
   * @param {number} startZoom The zoom when the step began.
   * @param {number} target The zoom the step completes at.
   * @returns {number} 0 to 1.
   */
  function zoomProgress(zoom, startZoom, target) {
    if (!isFinite(zoom)) return 0;
    if (zoom >= target) return 1;
    if (!isFinite(startZoom) || startZoom >= target) return 0;
    return Math.max(0, Math.min(1, (zoom - startZoom) / (target - startZoom)));
  }

  /**
   * Parse a YYYY-MM-DD day as a calendar date, never through a clock.
   *
   * @param {string} iso
   * @returns {?Date} Noon UTC on that day, or null if it is not one.
   */
  function parseDay(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!m) return null;
    var d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12));
    return isNaN(d.getTime()) ? null : d;
  }

  /**
   * Format a day with the given Intl options, in UTC so the visitor's own
   * clock can never move it a day.
   *
   * @param {string} iso
   * @param {string|undefined} locale
   * @param {Intl.DateTimeFormatOptions} options
   * @returns {string} The formatted day, or the input when it is not one.
   */
  function format(iso, locale, options) {
    var d = parseDay(iso);
    if (!d) return String(iso || '');
    try {
      return new Intl.DateTimeFormat(locale || undefined, Object.assign({ timeZone: 'UTC' }, options)).format(d);
    } catch (_err) {
      // An unknown locale tag: the default locale says it instead.
      return new Intl.DateTimeFormat(undefined, Object.assign({ timeZone: 'UTC' }, options)).format(d);
    }
  }

  /**
   * "10 April 2026" — the day as the guide's sentences name it.
   *
   * @param {string} iso
   * @param {string} [locale]
   * @returns {string}
   */
  function formatDay(iso, locale) {
    return format(iso, locale, { day: 'numeric', month: 'long', year: 'numeric' });
  }

  /**
   * "10 Apr" — the day on a button, where space is short.
   *
   * @param {string} iso
   * @param {string} [locale]
   * @returns {string}
   */
  function formatShortDay(iso, locale) {
    return format(iso, locale, { day: 'numeric', month: 'short' });
  }

  /**
   * Whether the map was opened by routes:example.
   *
   * @param {string} search A location.search string.
   * @returns {boolean}
   */
  function isExampleArrival(search) {
    var value = new URLSearchParams(search).get(EXAMPLE_PARAM);
    return value === '1' || value === 'true';
  }

  /**
   * The query string without the example parameter, everything else kept.
   *
   * @param {string} search A location.search string.
   * @returns {string} '' or '?…'.
   */
  function withoutExampleParam(search) {
    var params = new URLSearchParams(search);
    params.delete(EXAMPLE_PARAM);
    var query = params.toString();
    return query ? '?' + query : '';
  }

  self.pwaExampleGuideCore = Object.freeze({
    STEPS: STEPS,
    DRAG_FRACTION: DRAG_FRACTION,
    NEXT_AFTER_MS: NEXT_AFTER_MS,
    EXAMPLE_PARAM: EXAMPLE_PARAM,
    stepNumber: stepNumber,
    nextStep: nextStep,
    createDragTracker: createDragTracker,
    startIndex: startIndex,
    steepestIndex: steepestIndex,
    zoomProgress: zoomProgress,
    formatDay: formatDay,
    formatShortDay: formatShortDay,
    isExampleArrival: isExampleArrival,
    withoutExampleParam: withoutExampleParam,
  });
})();
