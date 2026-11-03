/*
 * static/js/map_region_pick_core.js — the pure half of "Download a region"
 * (SNOW-1414).
 *
 * The Downloads sheet's second add action starts a short map task
 * (static/js/map_region_pick.js): plain region outlines, a tap to choose
 * one, a bar that names it and says what it costs, and Download. It is the
 * one way into a region download in Terrain mode, which holds the region
 * tap and the region sheet (docs/decisions/the-map-has-a-dated-mode-and-a-
 * terrain-mode.md).
 *
 * This file holds the decisions that have no DOM or MapLibre in them, for
 * the reason every `*_core.js` is its own module: the task reads `MAP` as a
 * bare global and runs at parse time, so jsdom cannot import it on its own.
 *
 *   barState(regionId, name, estimate)
 *     What the bar says once a region is chosen. `estimate` is
 *     `pwaRegionDownload.estimate(regionId)` — the SAME priced figure the
 *     region roundel shows (SNOW-843: no surface shows the unpriced one).
 *     A region with no estimate (no precomputed download summary) cannot
 *     be downloaded, and one over this device's ceiling cannot either.
 *
 *   refusalKind(outcome)
 *     What to tell the reader after `pwaRegionDownload.start()` answers.
 *     Shared with the Downloads sheet's "Download here", which said the
 *     same three things before this existed. `null` means say nothing: the
 *     run started, or the reader has already been sent to sign in and is
 *     looking at another page.
 *
 * Exports (frozen `self.pwaRegionPickCore`):
 *
 *   STATES
 *   barState(regionId, name, estimate) → BarState
 *   formatMb(mb)                       → string
 *   refusalKind(outcome)               → 'busy' | 'already' | 'failed' | null
 */

// @ts-check

(function () {
  'use strict';

  /**
   * The bar's states. `choosing` is before any tap; `ready` can download;
   * `unavailable` has no size to offer; `too-big` is over this device's
   * ceiling.
   */
  const STATES = Object.freeze({
    CHOOSING: 'choosing',
    READY: 'ready',
    UNAVAILABLE: 'unavailable',
    TOO_BIG: 'too-big',
  });

  /**
   * @typedef {Object} Estimate
   * @property {number} mb The basemap download's priced size, in MB.
   * @property {number} terrainMb What terrain data adds, 0 when the device
   *   does not include it.
   * @property {boolean} tooBig Whether the total is over this device's
   *   ceiling.
   */

  /**
   * @typedef {Object} BarState
   * @property {string} state A member of `STATES`.
   * @property {?string} regionId The chosen region, or null.
   * @property {string} name The chosen region's name, '' before a choice.
   * @property {string} mb The total size, formatted, '' when there is none.
   * @property {boolean} canDownload Whether Download is enabled.
   */

  /**
   * A size in MB as the map's download surfaces write it: whole megabytes
   * from 10 up, one decimal below, and never "0".
   *
   * @param {number} mb
   * @returns {string}
   */
  function formatMb(mb) {
    if (!Number.isFinite(mb) || mb <= 0) return '0.1';
    if (mb >= 10) return String(Math.round(mb));
    const rounded = Math.round(mb * 10) / 10;
    return String(rounded < 0.1 ? 0.1 : rounded);
  }

  /**
   * What the bar shows for a choice, or for no choice yet.
   *
   * @param {?string} regionId
   * @param {?string} name
   * @param {?Estimate} estimate
   * @returns {BarState}
   */
  function barState(regionId, name, estimate) {
    if (!regionId) {
      return { state: STATES.CHOOSING, regionId: null, name: '', mb: '', canDownload: false };
    }
    const label = name || regionId;
    if (!estimate || !Number.isFinite(estimate.mb)) {
      return { state: STATES.UNAVAILABLE, regionId, name: label, mb: '', canDownload: false };
    }
    const total = estimate.mb + (Number.isFinite(estimate.terrainMb) ? estimate.terrainMb : 0);
    if (estimate.tooBig) {
      return { state: STATES.TOO_BIG, regionId, name: label, mb: formatMb(total), canDownload: false };
    }
    return { state: STATES.READY, regionId, name: label, mb: formatMb(total), canDownload: true };
  }

  /**
   * What to tell the reader about one `pwaRegionDownload.start()` outcome.
   *
   * Only the refusals a reader actually reaches get their own sentence:
   * `busy` (one region run at a time), and `done` / `incomplete` (the
   * region is already on this device). Everything else is the generic
   * "couldn't be started". A started run, and a `signin` refusal (the
   * reader has been redirected), say nothing.
   *
   * @param {?{started?: boolean, reason?: string}} outcome
   * @returns {?string} 'busy' | 'already' | 'failed', or null for silence.
   */
  function refusalKind(outcome) {
    if (outcome && outcome.started) return null;
    const reason = (outcome && outcome.reason) || '';
    if (reason === 'signin') return null;
    if (reason === 'busy') return 'busy';
    if (reason === 'done' || reason === 'incomplete') return 'already';
    return 'failed';
  }

  self.pwaRegionPickCore = Object.freeze({ STATES, barState, formatMb, refusalKind });
})();
