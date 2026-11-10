/*
 * static/js/map_hold_core.js — the press-and-hold detector behind "hold to
 * place" on the map (SNOW-1485).
 *
 * A finger held still on the map for HOLD_MS drops a pin there; on a desktop
 * a right-click (the map's `contextmenu`) does the same at once. Everything
 * that decides WHETHER a hold happened lives here, as
 * ``window.pwaMapHoldCore``, with no DOM and no map, so
 * tests/js/test_map_hold_core.js can drive it with fake timers.
 * static/js/map_hold.js feeds it the map's touch events and acts on a hold.
 *
 *   createDetector({ onHold, delayMs?, tolerancePx?, setTimer?, clearTimer?, now? })
 *     A detector. ``onHold(point, source)`` is called once per hold, with
 *     the screen point and ``'touch'`` or ``'mouse'``.
 *
 *     touchStart(points)  A finger went down; ``points`` is every finger
 *                         now on the screen. One arms the timer; two or
 *                         more is a pinch, and cancels.
 *     touchMove(points)   A finger moved. A second finger, or the first
 *                         travelling past ``tolerancePx``, cancels: that is
 *                         a pan, and the map keeps it.
 *     touchEnd()          A finger lifted before or after the hold.
 *     cancel()            The map started a drag or a zoom; no hold.
 *     contextMenu(point)  A `contextmenu`. True when it is a hold (a mouse
 *                         right-click); false when it is the synthetic one
 *                         a touch screen sends for a long touch, which is
 *                         swallowed so the hold never fires twice.
 *     takeClick()         True, once, for the click that follows a touch
 *                         hold — the binding swallows it so the object
 *                         under the finger does not open its sheet.
 *     dispose()           Drop any pending timer.
 *
 * A contextmenu counts as synthetic while a finger is down or within
 * SYNTHETIC_MS of one lifting. The click after a touch hold is taken while
 * the finger is down or within CLICK_GRACE_MS of it lifting, and never
 * later, so a browser that sends no such click cannot cost a real tap.
 */

// @ts-check

(function () {
  'use strict';

  /** How long a finger must stay still to be a hold. */
  var HOLD_MS = 500;
  /** How far a still finger may drift before it is a pan. */
  var TOLERANCE_PX = 8;
  /** A contextmenu this soon after a finger lifts came from the finger. */
  var SYNTHETIC_MS = 800;
  /** The click a lifted finger sends lands within this of the lift. */
  var CLICK_GRACE_MS = 400;

  /**
   * @typedef {{ x: number, y: number }} Point
   * @typedef {'touch'|'mouse'} HoldSource
   * @typedef {{
   *   onHold: (point: Point, source: HoldSource) => void,
   *   delayMs?: number,
   *   tolerancePx?: number,
   *   setTimer?: (fn: () => void, ms: number) => any,
   *   clearTimer?: (handle: any) => void,
   *   now?: () => number,
   * }} DetectorOptions
   */

  /**
   * Build a hold detector.
   *
   * @param {DetectorOptions} options
   * @returns {{
   *   touchStart: (points: Point[]) => void,
   *   touchMove: (points: Point[]) => void,
   *   touchEnd: () => void,
   *   cancel: () => void,
   *   contextMenu: (point: Point) => boolean,
   *   takeClick: () => boolean,
   *   dispose: () => void,
   * }}
   */
  function createDetector(options) {
    var onHold = options.onHold;
    var delayMs = options.delayMs != null ? options.delayMs : HOLD_MS;
    var tolerancePx = options.tolerancePx != null ? options.tolerancePx : TOLERANCE_PX;
    var setTimer = options.setTimer || function (fn, ms) { return setTimeout(fn, ms); };
    var clearTimer = options.clearTimer || function (handle) { clearTimeout(handle); };
    var now = options.now || function () { return Date.now(); };

    /** @type {any} */
    var timer = null;
    /** @type {Point|null} Where the pending touch went down. */
    var origin = null;
    var fingerDown = false;
    /** @type {number} When the last finger lifted; -Infinity before any. */
    var liftedAt = -Infinity;
    /** Whether the current or last touch fired a hold. */
    var touchHeld = false;
    /** @type {number} The click after a touch hold is taken until then. */
    var clickUntil = -Infinity;

    /** Drop a pending hold, if there is one. */
    function clearPending() {
      if (timer !== null) clearTimer(timer);
      timer = null;
      origin = null;
    }

    /** The timer ran out with the finger still: that is a hold. */
    function fire() {
      var at = origin;
      timer = null;
      origin = null;
      if (!at) return;
      touchHeld = true;
      clickUntil = Infinity;
      onHold(at, 'touch');
    }

    return {
      touchStart: function (points) {
        fingerDown = true;
        if (points.length !== 1) {
          clearPending();
          return;
        }
        clearPending();
        touchHeld = false;
        clickUntil = -Infinity;
        origin = { x: points[0].x, y: points[0].y };
        timer = setTimer(fire, delayMs);
      },

      touchMove: function (points) {
        if (!origin) return;
        if (points.length !== 1) {
          clearPending();
          return;
        }
        var dx = points[0].x - origin.x;
        var dy = points[0].y - origin.y;
        if (Math.sqrt(dx * dx + dy * dy) > tolerancePx) clearPending();
      },

      touchEnd: function () {
        clearPending();
        fingerDown = false;
        liftedAt = now();
        if (touchHeld) clickUntil = liftedAt + CLICK_GRACE_MS;
      },

      cancel: function () {
        clearPending();
      },

      contextMenu: function (point) {
        if (fingerDown || now() - liftedAt < SYNTHETIC_MS) return false;
        clearPending();
        onHold({ x: point.x, y: point.y }, 'mouse');
        return true;
      },

      takeClick: function () {
        if (now() > clickUntil) return false;
        clickUntil = -Infinity;
        return true;
      },

      dispose: function () {
        clearPending();
      },
    };
  }

  window.pwaMapHoldCore = Object.freeze({
    HOLD_MS: HOLD_MS,
    TOLERANCE_PX: TOLERANCE_PX,
    SYNTHETIC_MS: SYNTHETIC_MS,
    CLICK_GRACE_MS: CLICK_GRACE_MS,
    createDetector: createDetector,
  });
})();
