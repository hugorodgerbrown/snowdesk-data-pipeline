/*
 * static/js/map_mode_core.js — the map's two modes, Dated and Terrain
 * (SNOW-1394).
 *
 * Some of what the map draws changes by the day: the bulletins (and the
 * regions that exist only to hold them), the problem-terrain sync, the
 * weather and the field reports. Everything else — Steep terrain, routes,
 * resorts, ski tours, rest zones, pins — is the same tomorrow as today.
 * Terrain mode is the map with the dated overlays switched off, and nothing
 * more (Hugo, 2 Nov 2026): it switches nothing on, Steep terrain included.
 *
 * The rule this module exists to keep: SUPPRESSION, NEVER PREFERENCE. The
 * mode never rewrites a reader's stored choice. What is drawn is the stored
 * preference AND-ed with "not suppressed by Terrain", so leaving Terrain
 * brings each overlay back exactly as the reader had it, and a choice made
 * in Terrain (Steep terrain on or off) changes that choice alone.
 *
 * Pure: map.js reads it at boot, before the first paint, so `?mode=terrain`
 * never flashes the dated layers; static/js/map_mode.js reads it to draw the
 * control and write the URL. See
 * docs/decisions/the-map-has-a-dated-mode-and-a-terrain-mode.md.
 *
 * Exports (frozen `self.pwaMapModeCore`):
 *
 *   MODES / STORAGE_KEY / URL_PARAM / DATED_OVERLAYS / DATED_ROW_SELECTORS
 *   parseMode(value)
 *   readMode(search, stored)
 *   withMode(search, mode)
 *   withDate(search, dateKey)
 *   isDated(key)
 *   isSuppressed(key, mode)
 *   effective(key, preference, mode)
 *   problemTerrainShown(input)
 *   toggled(mode)
 */

// @ts-check

(function () {
  'use strict';

  /** The two modes. Dated is the default, and what an unknown value means. */
  const MODES = Object.freeze({ DATED: 'dated', TERRAIN: 'terrain' });

  /** Where the reader's last mode is kept on this device. */
  const STORAGE_KEY = 'snowdesk.map.mode';

  /** The query parameter a link carries the mode in. */
  const URL_PARAM = 'mode';

  /**
   * The overlay keys Terrain mode takes off the map — map.js's
   * `overlayState` keys, plus `l3`, the bulletin grouping outline, which
   * has no row of its own.
   *
   * The region tiers (`l1`, `l2`, `l4`) are here because a region exists
   * only to hold a bulletin (Hugo, 2 Nov 2026): with no bulletin drawn, a
   * boundary is a line about nothing, and a tap on one would open a sheet
   * whose whole content is the day's danger.
   *
   * `terrain_filter` (Steep terrain) is deliberately absent: it is the
   * reader's own switch in both modes.
   */
  const DATED_OVERLAYS = Object.freeze([
    'bulletins',
    'l1',
    'l2',
    'l3',
    'l4',
    'problem_terrain',
    'weather',
    'community_reports',
  ]);

  /**
   * The layers-menu controls that drive a dated overlay. In Terrain each
   * shows disabled and a tap on it does nothing. The bulletins and
   * boundaries rows open pages of their own, so disabling the row on the
   * menu's first page covers everything under it; field reports and the
   * problem-terrain sync live on the bulletins page and are listed too, for
   * a reader who had that page open when the mode changed.
   */
  const DATED_ROW_SELECTORS = Object.freeze([
    '[data-layers-open="bulletins"]',
    '[data-layer-master="bulletins"]',
    '[data-overlay-key="weather"]',
    '[data-layers-open="boundaries"]',
    '[data-layer-master="boundaries"]',
    '[data-overlay-key="community_reports"]',
    '[data-overlay-key="problem_terrain"]',
  ]);

  /**
   * Read one stored or linked value as a mode.
   *
   * @param {unknown} value
   * @returns {string} A member of `MODES`; `dated` for anything unknown.
   */
  function parseMode(value) {
    return value === MODES.TERRAIN ? MODES.TERRAIN : MODES.DATED;
  }

  /**
   * The mode the map opens in. A link's `?mode=` wins, so a shared link
   * reproduces what its sender saw; a bare URL takes this device's last
   * mode.
   *
   * @param {string} search `location.search`, with or without the `?`.
   * @param {?string} stored The raw stored value, or null.
   * @returns {string}
   */
  function readMode(search, stored) {
    const linked = new URLSearchParams(search || '').get(URL_PARAM);
    return parseMode(linked !== null ? linked : stored);
  }

  /**
   * The query string with the mode written in, every other parameter kept.
   * Dated is the default, so it is spelt by leaving the parameter out.
   *
   * @param {string} search `location.search`.
   * @param {string} mode
   * @returns {string} `''` or `'?…'`.
   */
  function withMode(search, mode) {
    const params = new URLSearchParams(search || '');
    if (parseMode(mode) === MODES.TERRAIN) params.set(URL_PARAM, MODES.TERRAIN);
    else params.delete(URL_PARAM);
    const query = params.toString();
    return query ? '?' + query : '';
  }

  /**
   * The query string a date commit writes: `?d=` and the mode, if any.
   *
   * map_shared.js's `writeUrlDateParam` has always written `?d=` alone; the
   * mode is the one parameter a date write must carry through, because a
   * reader in Terrain who moves the day is still in Terrain.
   *
   * @param {string} search `location.search`.
   * @param {string} dateKey `YYYY-MM-DD`.
   * @returns {string}
   */
  function withDate(search, dateKey) {
    const mode = new URLSearchParams(search || '').get(URL_PARAM);
    const params = new URLSearchParams();
    params.set('d', dateKey);
    if (parseMode(mode) === MODES.TERRAIN) params.set(URL_PARAM, MODES.TERRAIN);
    return '?' + params.toString();
  }

  /**
   * Whether an overlay key is one Terrain takes off the map.
   *
   * @param {string} key
   * @returns {boolean}
   */
  function isDated(key) {
    return DATED_OVERLAYS.includes(key);
  }

  /**
   * Whether the mode is holding an overlay off the map.
   *
   * @param {string} key
   * @param {string} mode
   * @returns {boolean}
   */
  function isSuppressed(key, mode) {
    return parseMode(mode) === MODES.TERRAIN && isDated(key);
  }

  /**
   * What is drawn: the reader's preference, unless the mode suppresses it.
   * The preference is an input, never an output.
   *
   * @param {string} key
   * @param {unknown} preference Truthy for "the reader has it on".
   * @param {string} mode
   * @returns {boolean}
   */
  function effective(key, preference, mode) {
    return !!preference && !isSuppressed(key, mode);
  }

  /**
   * @typedef {Object} ProblemTerrainInput
   * @property {boolean} available The page carries the layer at all.
   * @property {boolean} problemTerrain The reader's "Sync with map terrain".
   * @property {boolean} steepTerrain The reader's Steep terrain switch.
   * @property {string} mode
   */

  /**
   * Whether the problem-terrain sync is drawn. Steep terrain takes
   * precedence over it (SNOW-1377) in either mode, and Terrain suppresses
   * it; neither writes the reader's choice.
   *
   * @param {ProblemTerrainInput} input
   * @returns {boolean}
   */
  function problemTerrainShown(input) {
    return !!input.available
      && effective('problem_terrain', input.problemTerrain, input.mode)
      && !input.steepTerrain;
  }

  /**
   * The other mode.
   *
   * @param {string} mode
   * @returns {string}
   */
  function toggled(mode) {
    return parseMode(mode) === MODES.TERRAIN ? MODES.DATED : MODES.TERRAIN;
  }

  self.pwaMapModeCore = Object.freeze({
    MODES,
    STORAGE_KEY,
    URL_PARAM,
    DATED_OVERLAYS,
    DATED_ROW_SELECTORS,
    parseMode,
    readMode,
    withMode,
    withDate,
    isDated,
    isSuppressed,
    effective,
    problemTerrainShown,
    toggled,
  });
})();
