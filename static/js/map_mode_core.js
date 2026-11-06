/*
 * static/js/map_mode_core.js — the map's two modes, Conditions and Terrain
 * (SNOW-1394, SNOW-1421).
 *
 * Conditions (code: `dated`) is the map for a day: the bulletins, the
 * problem-terrain sync and the weather. Terrain is the mountains
 * themselves: Steep terrain, SAC's ski tours and summits, and the wildlife
 * rest zones. Each mode holds the other's own layers off the map
 * (SNOW-1421; until then Terrain only took the dated ones away). The rest —
 * the region boundaries, resorts, the basemap, the field reports and the
 * reader's own data — is shared, drawn in both as its switch says. Neither
 * mode switches anything on (Hugo, 2 Nov 2026).
 *
 * The rule this module exists to keep: SUPPRESSION, NEVER PREFERENCE. The
 * mode never rewrites a reader's stored choice. What is drawn is the stored
 * preference AND-ed with "not held by the mode", so coming back to a mode
 * brings each of its layers back exactly as the reader had it, and a
 * choice made in one mode changes that choice alone.
 *
 * Pure: map.js reads it at boot, before the first paint, so `?mode=terrain`
 * never flashes the Conditions layers (nor a Conditions boot the Terrain
 * ones); static/js/map_mode.js reads it to draw the controls and write the
 * URL. See docs/decisions/the-map-has-a-dated-mode-and-a-terrain-mode.md.
 *
 * Exports (frozen `self.pwaMapModeCore`):
 *
 *   MODES / STORAGE_KEY / URL_PARAM / ONLY_IN
 *   parseMode(value)
 *   readMode(search, stored)
 *   withMode(search, mode)
 *   withDate(search, dateKey)
 *   modeOf(key)
 *   isSuppressed(key, mode)
 *   suppressedIn(mode)
 *   effective(key, preference, mode)
 *   problemTerrainShown(input)
 *   toggled(mode)
 */

// @ts-check

(function () {
  'use strict';

  /**
   * The two modes. Dated ("Conditions" on screen) is the default, and what
   * an unknown value means.
   */
  const MODES = Object.freeze({ DATED: 'dated', TERRAIN: 'terrain' });

  /** Where the reader's last mode is kept on this device. */
  const STORAGE_KEY = 'snowdesk.map.mode';

  /** The query parameter a link carries the mode in. */
  const URL_PARAM = 'mode';

  /**
   * The view's parameter and its 3-D value (map_view_core.URL_PARAM and
   * VIEWS.THREE_D), which a date write carries in either mode. Spelt here
   * rather than read from that core: map_shared.js calls withDate from
   * pages that do not load it.
   */
  const VIEW_PARAM = 'view';
  const VIEW_3D = '3d';

  /**
   * The layers each mode has to itself — map.js's `overlayState` keys, plus
   * `l3`, the bulletin grouping outline, which has no row of its own. A key
   * in one list is held off the map in the other mode; a key in neither is
   * shared.
   *
   * The region tiers (`l1`, `l2`, `l4`) are shared: a region is fixed, and
   * exists beyond the day's bulletin (Hugo, 4 Nov 2026). `l3`, the bulletin
   * grouping, changes daily, so it is Conditions'.
   *
   * `community_reports` (Field observations) is shared too (Hugo,
   * 5 Nov 2026): the reports are dated, but what people have seen is worth
   * as much to someone planning in Terrain.
   */
  const ONLY_IN = Object.freeze({
    [MODES.DATED]: Object.freeze(['bulletins', 'l3', 'problem_terrain', 'weather']),
    [MODES.TERRAIN]: Object.freeze(['terrain_filter', 'ski_tours', 'ski_summits', 'rest_zones']),
  });

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
   * The query string a date commit writes: `?d=`, the mode and the view,
   * if any.
   *
   * map_shared.js's `writeUrlDateParam` has always written `?d=` alone; the
   * mode is the one parameter a date write must carry through, because a
   * reader in Terrain who moves the day is still in Terrain. SNOW-1449: and
   * the view with it (`?view=3d`, map_view_core.js), in either mode since
   * SNOW-1452, so a reader in 3-D who moves the day is still in 3-D.
   *
   * @param {string} search `location.search`.
   * @param {string} dateKey `YYYY-MM-DD`.
   * @returns {string}
   */
  function withDate(search, dateKey) {
    const current = new URLSearchParams(search || '');
    const params = new URLSearchParams();
    params.set('d', dateKey);
    if (parseMode(current.get(URL_PARAM)) === MODES.TERRAIN) params.set(URL_PARAM, MODES.TERRAIN);
    if (current.get(VIEW_PARAM) === VIEW_3D) params.set(VIEW_PARAM, VIEW_3D);
    return '?' + params.toString();
  }

  /**
   * The mode an overlay key belongs to, or null for a shared one.
   *
   * @param {string} key
   * @returns {?string} A member of `MODES`, or null.
   */
  function modeOf(key) {
    if (ONLY_IN[MODES.DATED].includes(key)) return MODES.DATED;
    if (ONLY_IN[MODES.TERRAIN].includes(key)) return MODES.TERRAIN;
    return null;
  }

  /**
   * Whether the mode is holding an overlay off the map: the key belongs to
   * the other mode.
   *
   * @param {string} key
   * @param {string} mode
   * @returns {boolean}
   */
  function isSuppressed(key, mode) {
    const own = modeOf(key);
    return own !== null && own !== parseMode(mode);
  }

  /**
   * Every key a mode holds off the map: the other mode's own.
   *
   * @param {string} mode
   * @returns {ReadonlyArray<string>}
   */
  function suppressedIn(mode) {
    return toggled(mode) === MODES.TERRAIN ? ONLY_IN[MODES.TERRAIN] : ONLY_IN[MODES.DATED];
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
   * precedence over it (SNOW-1377) where both are drawn, which, with the
   * two in different modes, is only ever off the map_modes flag; Terrain
   * suppresses the sync. Neither writes the reader's choice.
   *
   * @param {ProblemTerrainInput} input
   * @returns {boolean}
   */
  function problemTerrainShown(input) {
    return !!input.available
      && effective('problem_terrain', input.problemTerrain, input.mode)
      && !effective('terrain_filter', input.steepTerrain, input.mode);
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
    ONLY_IN,
    parseMode,
    readMode,
    withMode,
    withDate,
    modeOf,
    isSuppressed,
    suppressedIn,
    effective,
    problemTerrainShown,
    toggled,
  });
})();
