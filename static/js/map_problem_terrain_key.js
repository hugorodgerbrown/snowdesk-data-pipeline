/*
 * static/js/map_problem_terrain_key.js — the problem-terrain layer's key
 * keeps in step with the layer (SNOW-1196).
 *
 * The layer is switched by the layers menu's "Sync with map terrain" row
 * (SNOW-1377; it was "Highlight problem terrain") and by the region sheet's
 switch. While it paints, this module shows the legend's problem-terrain
 * section ([data-problem-terrain-legend]), the layer's key.
 *
 * It also wrote the menu row's second line ("From zoom 12, Switzerland", or
 * "No bulletin for this day", SNOW-1195). SNOW-1410 removed that line: on
 * the bulletins options page it only repeated the context (Hugo, 3 Nov 2026).
 *
 * SNOW-1196 also gave the layer a chip in the date row, "Problem terrain ·
 * {day}" with a Key and a ×. SNOW-1206 removed it: it repeated what the
 * layers menu and the legend already say, and on a tablet it ran under the
 * bottom-right roundel.
 *
 * Reads ``window.pwaBulletinTerrain`` (map.js).
 */
(function mapProblemTerrainKeyInit() {
  'use strict';

  /** Show the key while the layer paints. */
  function apply() {
    const bridge = window.pwaBulletinTerrain;
    const on = !!(bridge && bridge.isEnabled());
    const legend = document.querySelector('[data-problem-terrain-legend]');
    if (legend) legend.hidden = !on;
  }

  document.addEventListener('snowdesk:date-changed', apply);
  document.addEventListener('snowdesk:problem-terrain-changed', apply);

  apply();
})();
