/*
 * static/js/map_region_chip_core.js — what the map's region chip says
 * (SNOW-1376).
 *
 * From z10 the region fill has faded to a trace (SNOW-1375), so the day's
 * level stops being a colour on the map, and from z12 one region usually
 * fills the screen. The chip names the region at the map's centre and its
 * level, so the level never leaves the screen, and its second line says
 * what the slopes show. This module decides all of that from plain inputs;
 * static/js/map_region_chip.js reads the map and draws it.
 *
 * Exports (frozen `self.pwaRegionChipCore`):
 *
 *   MIN_ZOOM / SLOPES_ZOOM / TERRAIN_COUNTRIES
 *   chipState(input)
 */

// @ts-check

(function () {
  'use strict';

  /** The chip shows from here: where the fill has faded (SNOW-1375). */
  const MIN_ZOOM = 10;
  /** Where slopes start to be painted, so the second line starts too. */
  const SLOPES_ZOOM = 12;
  /** Countries the terrain-class tiles cover (apps/public/problem_terrain.py). */
  const TERRAIN_COUNTRIES = Object.freeze(['CH']);

  /** Rating key → EAWS level digit. */
  const LEVEL_DIGIT = Object.freeze({
    low: '1', moderate: '2', considerable: '3', high: '4', very_high: '5',
  });

  /**
   * @typedef {Object} ChipInput
   * @property {number} zoom The camera's zoom.
   * @property {?string} regionId The region at the map's centre, or null.
   * @property {string} [name] Its name.
   * @property {string} [country] Its ISO-2 country code.
   * @property {?string} [rating] Its painted rating key for the day.
   * @property {boolean} [slopesPainted] Whether its slopes are painted now.
   * @property {boolean} [slopesOn] Whether the bulletin's slopes are on.
   * @property {boolean} [filterOn] Whether the Steep terrain filter is on.
   */

  /**
   * @typedef {Object} ChipState
   * @property {boolean} hidden Whether the chip is hidden.
   * @property {string} [regionId]
   * @property {string} [name]
   * @property {string} [level] The `data-level` for its `.danger-tile`.
   * @property {string} [digit] The level digit.
   * @property {''|'slopes'|'filter'|'ch-only'} [detail] Which second line.
   */

  /**
   * Decide what the chip shows.
   *
   * Hidden below z10, with no region at the centre, or with no rating for
   * the day: a chip with no level would say nothing the map does not. The
   * second line, from z12 only: the reader's own filter when Steep terrain
   * is on (it replaces the bulletin's slopes); "Slopes are drawn in
   * Switzerland only" for a region outside the tiles while the slopes are
   * on; "slopes" when this region's slopes are painted; otherwise nothing.
   *
   * @param {ChipInput} input
   * @returns {ChipState}
   */
  function chipState(input) {
    const digit = LEVEL_DIGIT[/** @type {keyof typeof LEVEL_DIGIT} */ (input.rating || '')];
    if (!(input.zoom >= MIN_ZOOM) || !input.regionId || !digit) return { hidden: true };
    /** @type {ChipState['detail']} */
    let detail = '';
    if (input.zoom >= SLOPES_ZOOM) {
      if (input.filterOn) detail = 'filter';
      else if (input.slopesOn && !TERRAIN_COUNTRIES.includes(input.country || '')) detail = 'ch-only';
      else if (input.slopesPainted) detail = 'slopes';
    }
    return {
      hidden: false,
      regionId: input.regionId,
      name: input.name || '',
      level: /** @type {string} */ (input.rating),
      digit,
      detail,
    };
  }

  self.pwaRegionChipCore = Object.freeze({
    MIN_ZOOM,
    SLOPES_ZOOM,
    TERRAIN_COUNTRIES,
    chipState,
  });
})();
