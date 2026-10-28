/*
 * static/js/map_layers_zoom_gates.js — the layers menu says which layers
 * draw only at certain zoom levels, and when one you switched on cannot
 * draw yet (SNOW-1280).
 *
 * Two layers appear only past a zoom: ski-touring routes from z10 and the
 * steep-terrain filter from z12. Switched on below it, each drew nothing
 * and its switch still read "on", so the layer looked broken. The zoom
 * control's flyout named the gates, but nobody looks there for a layer.
 *
 * What the menu does now, all from `window.pwaZoomGates` (map.js), so
 * each number has one source:
 *
 *   - EVERY ZOOM-LIMITED ROW carries a suffix after its name, "· ≥z10",
 *     whether the layer is on or off: the limit is learnable before it
 *     matters.
 *   - A ROW WHOSE LAYER IS ON BUT OUTSIDE ITS RANGE fades (its name and
 *     icon, never its switch — the layer IS on) and shows "Zoom in to
 *     view" under its name. The row still toggles the layer; there is no
 *     zoom action (Hugo, 28 Oct 2026: "they can work it out").
 *   - WHILE ANY ROW IS FADED, a note heads the menu: "Features marked
 *     z10–12 are visible at specific zoom levels only.", the range being
 *     the span of the gates the menu shows.
 *
 * The rows are the `[data-zoom-gate]` elements in #basemap-menu
 * (public/partials/_map_layers_zoom_label.html); a gate key the registry
 * does not list — the ski-touring row outside Switzerland, say — leaves
 * its row plain. Whether a row's layer is on is read off its own
 * `[data-layer-switch]`'s aria-checked, watched with a MutationObserver,
 * so this needs no event from each layer's bridge.
 *
 * LOAD ORDER: reads `MAP` at parse time, so after map.js, and after
 * zoom_control_core.js, whose `isReached` and `zoomRange` it uses.
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
    'suffix-from': '· ≥z%(zoom)s',
    'suffix-up-to': '· ≤z%(zoom)s',
    note: 'Features marked z%(range)s are visible at specific zoom levels only.',
  });

  const note = menu.querySelector('[data-zoom-note]');
  const noteText = note && note.querySelector('[data-zoom-note-text]');

  /**
   * Whether a row's layer is switched on.
   *
   * @param {Element} row A `[data-zoom-gate]` row.
   * @returns {boolean}
   */
  function isOn(row) {
    const sw = row.querySelector('[data-layer-switch]');
    return !!sw && sw.getAttribute('aria-checked') === 'true';
  }

  // What was last written, so a pinch — a `zoom` event every frame —
  // touches the DOM only when a row crosses its gate.
  let drawn = null;

  /**
   * Bring every zoom-limited row, and the note, into line with the zoom.
   *
   * @returns {void}
   */
  function apply() {
    const list = window.pwaZoomGates ? window.pwaZoomGates.list() : [];
    const byKey = new Map(list.map((gate) => [gate.key, gate]));
    const zoom = map.getZoom();
    const states = rows.map((row) => {
      const gate = byKey.get(/** @type {HTMLElement} */ (row).dataset.zoomGate || '') || null;
      return { row, gate, hidden: !!gate && isOn(row) && !core.isReached(gate, zoom) };
    });
    const signature = states
      .map((s) => (s.gate ? s.gate.key + ':' + s.gate.zoom + ':' + s.hidden : '-'))
      .join('|');
    if (signature === drawn) return;
    drawn = signature;

    for (const { row, gate, hidden } of states) {
      const suffix = row.querySelector('[data-zoom-suffix]');
      const hint = /** @type {HTMLElement|null} */ (row.querySelector('[data-zoom-hint]'));
      if (suffix) {
        suffix.textContent = gate
          ? self.pwaStrings.interpolate(
            STRINGS[gate.upTo ? 'suffix-up-to' : 'suffix-from'], { zoom: gate.zoom },
          )
          : '';
      }
      if (hint) hint.hidden = !hidden;
      row.toggleAttribute('data-zoom-hidden', hidden);
    }

    const anyHidden = states.some((s) => s.hidden);
    if (note) note.hidden = !anyHidden;
    if (noteText) {
      const gates = states.map((s) => s.gate).filter(Boolean);
      noteText.textContent = anyHidden
        ? self.pwaStrings.interpolate(STRINGS.note, { range: core.zoomRange(gates) })
        : '';
    }
  }

  const observer = new MutationObserver(apply);
  for (const row of rows) {
    const sw = row.querySelector('[data-layer-switch]');
    if (sw) observer.observe(sw, { attributes: true, attributeFilter: ['aria-checked'] });
  }
  map.on('zoom', apply);
  document.addEventListener('snowdesk:zoom-gates-changed', apply);

  apply();
})();
