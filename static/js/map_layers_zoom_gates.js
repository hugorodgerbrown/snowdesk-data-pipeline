/*
 * static/js/map_layers_zoom_gates.js — the layers menu marks a layer the
 * current zoom makes unusable (SNOW-1280, reworked 3 Nov 2026).
 *
 * Three layers draw only past a zoom: SAC ski touring, wildlife rest zones
 * and the steep-terrain filter. Outside its range a layer draws nothing,
 * whatever its switch says, so its row says so:
 *
 *   - THE ROW DIMS — icon, name, second line and chevron, never the switch
 *     — and "· zoom in" (or "· zoom out" for an up-to gate) follows the
 *     name on the same line. This holds whether the layer is on or off:
 *     the row is telling you what the map can draw, not what you picked.
 *   - THE SWITCH KEEPS ITS STATE AND STAYS LIVE, so a layer can be armed
 *     before zooming in.
 *   - NOTHING NAMES THE LEVEL. The zoom control's scale does that (Hugo,
 *     3 Nov 2026), so the "· ≥z10" suffix and the menu's note are gone.
 *   - NOTHING CHANGES HEIGHT. The hint sits on the name's line, which CSS
 *     keeps to one line, so the menu never shifts as you pinch (the old
 *     "Zoom in to view" second line did).
 *
 * The rows are the `[data-zoom-gate]` elements in #basemap-menu
 * (public/partials/_map_layers_zoom_label.html); a gate key the registry
 * (`window.pwaZoomGates`, map.js) does not list — the ski-touring row
 * outside Switzerland, say — leaves its row plain.
 *
 * LOAD ORDER: reads `MAP` at parse time, so after map.js, and after
 * zoom_control_core.js, whose `isReached` it uses.
 * Strings from `map-layers-zoom-strings-template`.
 */

(function mapLayersZoomGatesInit() {
  'use strict';

  const menu = document.getElementById('basemap-menu');
  const core = self.pwaZoomControlCore;
  if (!menu || !core) return;
  if (typeof MAP === 'undefined' || !MAP) return;
  const map = MAP;

  const rows = Array.from(menu.querySelectorAll('[data-zoom-gate]'));
  if (!rows.length) return;

  const STRINGS = self.pwaStrings.read('map-layers-zoom-strings-template', {
    'zoom-in': '· zoom in',
    'zoom-out': '· zoom out',
  });

  // What was last written, so a pinch — a `zoom` event every frame —
  // touches the DOM only when a row crosses its gate.
  let drawn = null;

  /**
   * Bring every zoom-limited row into line with the zoom.
   *
   * @returns {void}
   */
  function apply() {
    const list = window.pwaZoomGates ? window.pwaZoomGates.list() : [];
    const byKey = new Map(list.map((gate) => [gate.key, gate]));
    const zoom = map.getZoom();
    const states = rows.map((row) => {
      const gate = byKey.get(/** @type {HTMLElement} */ (row).dataset.zoomGate || '') || null;
      return { row, gate, hidden: !!gate && !core.isReached(gate, zoom) };
    });
    const signature = states.map((s) => (s.hidden ? '1' : '0')).join('');
    if (signature === drawn) return;
    drawn = signature;

    for (const { row, gate, hidden } of states) {
      const hint = /** @type {HTMLElement|null} */ (row.querySelector('[data-zoom-hint]'));
      if (hint) {
        hint.textContent = hidden && gate ? STRINGS[gate.upTo ? 'zoom-out' : 'zoom-in'] : '';
        hint.hidden = !hidden;
      }
      row.toggleAttribute('data-zoom-hidden', hidden);
    }
  }

  map.on('zoom', apply);
  document.addEventListener('snowdesk:zoom-gates-changed', apply);

  apply();
})();
