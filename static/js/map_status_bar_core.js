/*
 * static/js/map_status_bar_core.js — what the map's surface shows (SNOW-1392).
 *
 * Routes, resorts and pins say what they are; the painted ground does not.
 * Which colour is on it depends on the zoom (the region fill fades out over
 * z8.5–10 and a thin edge band takes over, SNOW-1375), on two switches (the
 * bulletin's slopes, "Sync with map terrain", from z12; Steep terrain, which
 * replaces them) and on where the map is (the slopes are Switzerland only).
 * The status bar beside the region chip names each thing painted, and says
 * when zooming in would show more.
 *
 * This module decides all of that from plain inputs, so jsdom can test it;
 * map_status_bar.js reads the map and draws the result.
 *
 * Exports (frozen `self.pwaStatusBarCore`):
 *
 *   EDGE_ZOOM / SLOPES_ZOOM / TERRAIN_COUNTRIES
 *   statusState(input) → { segments, hint }
 */

// @ts-check

(function () {
  'use strict';

  /** Where the fill has faded and the edge band carries the colour. */
  const EDGE_ZOOM = 10;
  /** Where the terrain tiles, and so the slopes, start. */
  const SLOPES_ZOOM = 12;
  /** Where the terrain tiles are. */
  const TERRAIN_COUNTRIES = Object.freeze(['CH']);

  /**
   * @typedef {Object} StatusInput
   * @property {number} zoom
   * @property {boolean} bulletinOn The bulletin layer is drawn (its opacity step is not off).
   * @property {?string} [level] The centre region's rating key, for the swatches.
   * @property {?string} [country] The centre region's country, when there is one.
   * @property {boolean} slopesOn "Sync with map terrain" is on and drawn.
   * @property {boolean} [slopesPainted] The centre region's slopes are painted
   *   (its feature-state ``terrain``) — false on a day with no bulletin, or
   *   while the day's rules have not arrived.
   * @property {boolean} slopesEligible The page has the slopes layer at all.
   * @property {boolean} filterOn Steep terrain is on.
   * @property {string} [basemap] The basemap's label.
   */

  /**
   * @typedef {Object} Segment
   * @property {'region-fill'|'region-edge'|'slopes'|'slopes-off'|'slopes-none'|'slopes-ch-only'|'filter'|'filter-ch-only'|'basemap'} kind
   * @property {string} level The rating key its swatch is coloured by.
   * @property {boolean} muted Says something is off or out of reach.
   * @property {string} [text] Literal text (the basemap's label).
   */

  /**
   * @typedef {Object} StatusState
   * @property {Segment[]} segments In reading order.
   * @property {''|'zoom-slopes'|'zoom-filter'} hint Why zooming in would show more.
   */

  /**
   * Decide what the status bar says.
   *
   * The region segment while the bulletin layer is on: a fill below z10,
   * the edges from it. Then, from z12, the reader's Steep terrain filter
   * when it is on (it replaces the slopes), else the bulletin's slopes —
   * named as off when switched off, as none when the centre region has no
   * slopes painted (no bulletin that day, or its rules not yet arrived),
   * and as Switzerland only, for either layer, for a region outside the
   * tiles. Below z12 a switched-on layer that would paint becomes the hint
   * instead. The basemap last, always.
   *
   * @param {StatusInput} input
   * @returns {StatusState}
   */
  function statusState(input) {
    const level = input.level || 'no_rating';
    const outside = !!input.country && !TERRAIN_COUNTRIES.includes(input.country);
    const close = input.zoom >= SLOPES_ZOOM;
    /** @type {Segment[]} */
    const segments = [];
    /** @type {StatusState['hint']} */
    let hint = '';

    if (input.bulletinOn) {
      segments.push({ kind: input.zoom >= EDGE_ZOOM ? 'region-edge' : 'region-fill', level, muted: false });
    }

    if (input.filterOn) {
      if (close) segments.push(outside ? { kind: 'filter-ch-only', level, muted: true } : { kind: 'filter', level, muted: false });
      else if (!outside) hint = 'zoom-filter';
    } else if (input.slopesEligible) {
      if (!input.slopesOn) {
        if (close) segments.push({ kind: 'slopes-off', level, muted: true });
      } else if (outside) {
        if (close) segments.push({ kind: 'slopes-ch-only', level, muted: true });
      } else if (close) {
        segments.push(input.slopesPainted
          ? { kind: 'slopes', level, muted: false }
          : { kind: 'slopes-none', level, muted: true });
      } else {
        hint = 'zoom-slopes';
      }
    }

    if (input.basemap) segments.push({ kind: 'basemap', level, muted: true, text: input.basemap });
    return { segments, hint };
  }

  self.pwaStatusBarCore = Object.freeze({
    EDGE_ZOOM,
    SLOPES_ZOOM,
    TERRAIN_COUNTRIES,
    statusState,
  });
})();
