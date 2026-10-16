// @ts-check
/*
 * static/js/sheet_detents_core.js — where a phone map sheet settles after a
 * drag (SNOW-1097).
 *
 * Below the `sm` breakpoint a sheet that declares detents
 * (includes/_overlay_sheet.html's `detents`) rests at one of up to three
 * heights: PEEK (a short summary, the map left clear), HALF (the detail
 * beside the ground) and FULL (the map covered, the body scrolls). A drag on
 * the handle moves the sheet freely; when the finger lifts, the sheet has to
 * pick one of them. This module is that choice, and nothing else: pure
 * functions of numbers and detent names, no DOM, so the rules are tested in
 * Vitest (tests/js/test_sheet_detents_core.js) and map_sheet.js only binds
 * them to pointer events.
 *
 * THE RULE. The sheet settles at the declared detent nearest to where it
 * would have come to rest had the flick carried on: the release height
 * projected forward by the release velocity over `PROJECTION_MS`. A slow
 * drag therefore lands on the nearest detent, and a quick flick carries past
 * a detent it was still short of — the behaviour readers know from Apple and
 * Google Maps. A projection that falls below `CLOSE_FRACTION` of the lowest
 * declared detent closes the sheet instead, which is how a drag down
 * dismisses it.
 *
 * Heights are in CSS pixels measured from the bottom of the sheet, so a
 * larger number is a taller sheet. Velocity is in pixels per millisecond and
 * positive while the sheet GROWS (the finger moving up).
 */

(function sheetDetentsCoreInit() {
  'use strict';

  /** The order detents rise in. A sheet declares a subset of these. */
  var ORDER = Object.freeze(['peek', 'half', 'full']);

  /**
   * How far ahead a release velocity is projected, in milliseconds. Long
   * enough that a deliberate flick from peek reaches half, short enough
   * that a slow drag that stops between two detents is not thrown on.
   */
  var PROJECTION_MS = 150;

  /**
   * A release projected below this fraction of the lowest detent closes the
   * sheet. Half the lowest detent: the reader has visibly pulled most of it
   * away, so settling back up would fight them.
   */
  var CLOSE_FRACTION = 0.5;

  /**
   * The detents a sheet declares, in rising order, with unknown names and
   * duplicates dropped.
   *
   * @param {string|null|undefined} declared Comma-separated detent names, as
   *   the template's `data-detents` attribute carries them.
   * @returns {Array<string>} Never contains a name outside ORDER.
   */
  function parseDetents(declared) {
    if (!declared) return [];
    var names = String(declared)
      .split(',')
      .map(function (name) {
        return name.trim();
      });
    return ORDER.filter(function (name) {
      return names.indexOf(name) !== -1;
    });
  }

  /**
   * The detent a sheet opens at: the requested one when it is declared,
   * otherwise the lowest declared detent.
   *
   * @param {Array<string>} detents Declared detents, rising.
   * @param {string|null|undefined} requested
   * @returns {string|null} Null when nothing is declared.
   */
  function initialDetent(detents, requested) {
    if (!detents.length) return null;
    if (requested && detents.indexOf(requested) !== -1) return requested;
    return detents[0];
  }

  /**
   * The detent the handle button moves to: the next one up, and back to the
   * lowest from the top. A press on the handle is the keyboard and switch
   * user's way through the three states, so it cycles rather than toggling
   * between two.
   *
   * @param {Array<string>} detents Declared detents, rising.
   * @param {string|null|undefined} current
   * @returns {string|null} Null when nothing is declared.
   */
  function nextDetent(detents, current) {
    if (!detents.length) return null;
    var at = current ? detents.indexOf(current) : -1;
    if (at === -1) return detents[0];
    return detents[(at + 1) % detents.length];
  }

  /**
   * Keep a dragged height between nothing and the full detent: the sheet
   * may be pulled down to its close, but never up past the screen.
   *
   * @param {number} height The height the finger would make.
   * @param {number} max The full detent's height (or the tallest declared).
   * @returns {number}
   */
  function clampHeight(height, max) {
    if (!Number.isFinite(height)) return 0;
    if (height < 0) return 0;
    if (Number.isFinite(max) && height > max) return max;
    return height;
  }

  /**
   * Where a released sheet settles.
   *
   * @param {{
   *   detents: Array<string>,
   *   heights: Record<string, number>,
   *   height: number,
   *   velocity?: number,
   * }} release
   *   `detents` — the declared detents, rising.
   *   `heights` — each declared detent's height in px.
   *   `height` — the sheet's height at release.
   *   `velocity` — px/ms at release, positive while growing; 0 if unknown.
   * @returns {string|null} A declared detent name, `'close'`, or null when
   *   nothing is declared.
   */
  function settle(release) {
    var detents = release.detents;
    if (!detents.length) return null;
    var velocity = Number.isFinite(release.velocity) ? Number(release.velocity) : 0;
    var projected = release.height + velocity * PROJECTION_MS;

    var lowest = release.heights[detents[0]];
    if (projected < lowest * CLOSE_FRACTION) return 'close';

    var best = detents[0];
    var bestDistance = Infinity;
    detents.forEach(function (name) {
      var distance = Math.abs(release.heights[name] - projected);
      if (distance < bestDistance) {
        best = name;
        bestDistance = distance;
      }
    });
    return best;
  }

  window.pwaSheetDetentsCore = Object.freeze({
    ORDER: ORDER,
    PROJECTION_MS: PROJECTION_MS,
    CLOSE_FRACTION: CLOSE_FRACTION,
    parseDetents: parseDetents,
    initialDetent: initialDetent,
    nextDetent: nextDetent,
    clampHeight: clampHeight,
    settle: settle,
  });
})();
