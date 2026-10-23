/*
 * static/js/map_problem_terrain_key.js — the problem-terrain layer's key and
 * its layers-menu row keep in step with the layer (SNOW-1196).
 *
 * The layer is switched by the layers menu's "Highlight problem terrain"
 * row and by the region sheet's switch. While it paints, this module:
 *
 *   - shows the legend's problem-terrain section
 *     ([data-problem-terrain-legend]), the layer's key;
 *   - writes the menu row's second line ([data-problem-terrain-detail]):
 *     what the row does, or (SNOW-1195) that the day has no bulletin to
 *     paint.
 *
 * SNOW-1196 also gave the layer a chip in the date row, "Problem terrain ·
 * {day}" with a Key and a ×. SNOW-1206 removed it: it repeated what the
 * layers menu and the legend already say, and on a tablet it ran under the
 * bottom-right roundel.
 *
 * Reads ``window.pwaBulletinTerrain`` (map.js). Strings from
 * ``map-problem-terrain-strings-template``.
 */
(function mapProblemTerrainKeyInit() {
  'use strict';

  const STRINGS = self.pwaStrings.read('map-problem-terrain-strings-template', {
    detail: 'Replaces the region colour from zoom 12',
    'no-bulletin': 'No bulletin for this day',
  });

  /** Show the key while the layer paints; say when the day has nothing. */
  function apply() {
    const bridge = window.pwaBulletinTerrain;
    const on = !!(bridge && bridge.isEnabled());
    const legend = document.querySelector('[data-problem-terrain-legend]');
    if (legend) legend.hidden = !on;
    const detail = document.querySelector('[data-problem-terrain-detail]');
    if (detail) {
      const empty = !!(bridge && typeof bridge.dayEmpty === 'function' && bridge.dayEmpty());
      detail.textContent = empty ? STRINGS['no-bulletin'] : STRINGS.detail;
    }
  }

  document.addEventListener('snowdesk:date-changed', apply);
  document.addEventListener('snowdesk:problem-terrain-changed', apply);

  apply();
})();
