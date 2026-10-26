/*
 * static/js/resort_guide_core.js — the pure half of the "Find a resort"
 * walkthrough (SNOW-1231).
 *
 * The map's welcome card offers "Find a resort": a three-step guide
 * (static/js/map_resort_guide.js, the DOM half) that teaches a resort has
 * no avalanche danger of its own and the region it sits in has. Each step
 * finishes when the visitor DOES it:
 *
 *   search  — search is open with a few popular resorts; picking any
 *             resort (the resort's sheet opening) finishes it.
 *   resort  — the resort's sheet; tapping its region row (the region's
 *             sheet opening) finishes it.
 *   region  — the region's sheet is the bulletin; confirmed with Finish.
 *
 * This module owns no DOM and reads no globals: step order, what shows
 * for a given state of the map (the bar, the lead in search, the paused
 * pill or nothing), when a step is done, where the bar sits beside a
 * sheet, and the sign-in link that comes back to the resort. Unit-tested
 * in tests/js/test_resort_guide_core.js.
 *
 * Exports (frozen `self.pwaResortGuideCore`):
 *
 *   STEPS
 *   stepNumber(step)                 — 1-based position, 0 if unknown
 *   nextStep(step)                   — the step after, or null
 *   isStepDone(step, state)          — whether the map shows it done
 *   surface(phase, step, state)      — {show, pill}: what to draw
 *   barPosition(box)                 — {top, left}: where the bar sits
 *   signInHref(signInUrl, mapUrl, slug) — sign-in, back to the resort
 */

// @ts-check

(function () {
  'use strict';

  /** @typedef {'search'|'resort'|'region'} Step */

  /**
   * What the map shows right now, as the guide reads it.
   *
   * @typedef {Object} MapState
   * @property {boolean} searchOpen Search is expanded.
   * @property {boolean} resortOpen The place sheet shows a resort.
   * @property {boolean} regionOpen The region sheet is open.
   * @property {boolean} onGuideDay The map is on the guide's day (always
   *   true in season, when the guide keeps the map's own day).
   */

  /** @type {ReadonlyArray<Step>} */
  var STEPS = Object.freeze(['search', 'resort', 'region']);

  /**
   * A step's 1-based position, for "2 of 3".
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
   * Whether the map shows `step` done. The last step is never done by the
   * map: Finish confirms it.
   *
   * @param {string} step
   * @param {MapState} state
   * @returns {boolean}
   */
  function isStepDone(step, state) {
    if (step === 'search') return state.resortOpen;
    if (step === 'resort') return state.regionOpen;
    return false;
  }

  /**
   * What to draw. The bar while the sheet its step talks about is open,
   * the lead inside search for the first step, and otherwise the paused
   * pill, which says why: the map left the guide's day, or the visitor
   * closed what the step was about.
   *
   * @param {string} phase 'waiting', 'running' or 'ended'.
   * @param {string} step
   * @param {MapState} state
   * @returns {{show: 'none'|'lead'|'bar'|'pill', pill: 'paused'|'off-day'}}
   */
  function surface(phase, step, state) {
    if (phase !== 'running') return { show: 'none', pill: 'paused' };
    if (!state.onGuideDay) return { show: 'pill', pill: 'off-day' };
    var open = step === 'search'
      ? state.searchOpen
      : step === 'resort' ? state.resortOpen : state.regionOpen;
    if (!open) return { show: 'pill', pill: 'paused' };
    return { show: step === 'search' ? 'lead' : 'bar', pill: 'paused' };
  }

  /**
   * Where the bar sits, in the map's own coordinates. On a phone it rides
   * on top of the sheet, so the words sit just above the row they ask for;
   * on a desktop it stands beside the left column the sheet docks in, level
   * with the sheet's top. Never above `floor`, the line under the controls
   * along the top edge.
   *
   * @param {{phone: boolean, sheet: ?{top: number, right: number},
   *   map: {top: number, left: number}, floor: number, height: number,
   *   gap: number, overSheet?: 'top'|'bottom'}} box Viewport boxes;
   *   `floor` is already map-relative.
   * A phone sheet raised so high that the bar has no room above it (the
   * sheet at full) gets the bar laid over it instead: under the top
   * controls when `overSheet` is 'top' (the resort step, whose row is low
   * in the sheet), along the foot of the map when it is 'bottom' (the
   * region step, whose level and problems lead the sheet).
   *
   * @returns {{top: ?number, bottom: ?number, left: ?number}} Exactly one
   *   of `top` and `bottom` is set; `left` null keeps the CSS's.
   */
  function barPosition(box) {
    if (!box.sheet) return { top: Math.round(box.floor), bottom: null, left: null };
    if (box.phone) {
      var above = box.sheet.top - box.map.top - box.height - box.gap;
      if (above < box.floor) {
        return box.overSheet === 'top'
          ? { top: Math.round(box.floor), bottom: null, left: null }
          : { top: null, bottom: box.gap, left: null };
      }
      return { top: Math.round(above), bottom: null, left: null };
    }
    return {
      top: Math.round(Math.max(box.floor, box.sheet.top - box.map.top)),
      bottom: null,
      left: Math.round(box.sheet.right - box.map.left + box.gap),
    };
  }

  /**
   * The sign-in link that returns to the map with the resort open, so
   * "Sign in to pin Verbier" lands where the star is one tap away.
   *
   * @param {string} signInUrl The sign-in page, without a query.
   * @param {string} mapUrl The map's path.
   * @param {string} slug The resort's slug.
   * @returns {string}
   */
  function signInHref(signInUrl, mapUrl, slug) {
    if (!slug) return signInUrl;
    var next = mapUrl + '?resort=' + encodeURIComponent(slug);
    return signInUrl + '?next=' + encodeURIComponent(next);
  }

  self.pwaResortGuideCore = Object.freeze({
    STEPS: STEPS,
    stepNumber: stepNumber,
    nextStep: nextStep,
    isStepDone: isStepDone,
    surface: surface,
    barPosition: barPosition,
    signInHref: signInHref,
  });
})();
