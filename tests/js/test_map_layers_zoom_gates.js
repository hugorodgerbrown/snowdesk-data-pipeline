/*
 * tests/js/test_map_layers_zoom_gates.js — the layers menu's zoom-limited
 * rows (SNOW-1280, reworked 3 Nov 2026).
 *
 * static/js/map_layers_zoom_gates.js against a reduced copy of the menu's
 * zoom-limited rows, with `MAP` and `window.pwaZoomGates` (map.js) stood
 * in for by stubs.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - a row dims and says "· zoom in" while the map is below its zoom,
 *     whether its layer is on or off, and recovers when the map gets there;
 *   - the switch is never touched: it keeps its state;
 *   - an up-to gate says "· zoom out";
 *   - a row whose gate the registry does not list stays plain;
 *   - "Sync problem terrain" (SNOW-1421, on level one with the map_modes
 *     flag) dims below z12 as the others do, whatever the bulletins do.
 *     Its registry entry is map.js's, pinned with the layer in
 *     tests/js/test_map_problem_terrain_bridge.js.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/zoom_control_core.js';

const MARKUP = `
  <div id="basemap-menu">
    <ul>
      <li data-zoom-gate="terrain_filter">
        <button><span data-row-label>Steep terrain</span><span data-zoom-hint hidden></span></button>
        <button data-layer-switch aria-checked="false"></button>
      </li>
      <li data-zoom-gate="ski_routes">
        <button data-layer-switch aria-checked="true"><span data-row-label>SAC ski touring</span>
          <span data-zoom-hint hidden></span></button>
      </li>
      <li data-zoom-gate="problem_terrain">
        <button data-layer-switch aria-checked="true"><span data-row-label>Sync problem terrain</span>
          <span data-zoom-hint hidden></span></button>
      </li>
      <li data-zoom-gate="far_view">
        <button><span data-row-label>Overview</span><span data-zoom-hint hidden></span></button>
      </li>
    </ul>
  </div>`;

let zoom;
let zoomHandler;
let gates;

const row = (key) => document.querySelector(`[data-zoom-gate="${key}"]`);
const hint = (key) => row(key).querySelector('[data-zoom-hint]');
const dimmed = (key) => row(key).hasAttribute('data-zoom-hidden');

/** Move the camera and fire the map's zoom event. */
function zoomTo(z) {
  zoom = z;
  zoomHandler();
}

/** Load a fresh copy of the wiring. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_layers_zoom_gates.js');
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  zoom = 9;
  gates = [
    { key: 'region_select', zoom: 10, upTo: true },
    { key: 'ski_routes', zoom: 10, upTo: false },
    { key: 'terrain_filter', zoom: 12, upTo: false },
    { key: 'problem_terrain', zoom: 12, upTo: false },
    { key: 'far_view', zoom: 8, upTo: true },
  ];
  window.pwaZoomGates = { list: () => gates };
  globalThis.MAP = {
    getZoom: () => zoom,
    on: (type, fn) => {
      if (type === 'zoom') zoomHandler = fn;
    },
  };
});

afterEach(() => {
  delete window.pwaZoomGates;
  delete globalThis.MAP;
});

describe('a row outside its zoom', () => {
  it('dims and says to zoom in while its layer is on', async () => {
    await load();
    expect(dimmed('ski_routes')).toBe(true);
    expect(hint('ski_routes').hidden).toBe(false);
    expect(hint('ski_routes').textContent).toBe('· zoom in');
  });

  it('dims too while its layer is off', async () => {
    await load();
    expect(dimmed('terrain_filter')).toBe(true);
    expect(hint('terrain_filter').textContent).toBe('· zoom in');
  });

  it('leaves its switch as it was', async () => {
    await load();
    const switches = [...document.querySelectorAll('[data-layer-switch]')];
    expect(switches.map((sw) => sw.getAttribute('aria-checked'))).toEqual(['false', 'true', 'true']);
    expect(switches.some((sw) => sw.hasAttribute('disabled'))).toBe(false);
  });

  it('says to zoom out for an up-to gate', async () => {
    await load();
    expect(dimmed('far_view')).toBe(true);
    expect(hint('far_view').textContent).toBe('· zoom out');
  });
});

describe('a row inside its zoom', () => {
  it('recovers once the map reaches it', async () => {
    await load();
    zoomTo(10);
    expect(dimmed('ski_routes')).toBe(false);
    expect(hint('ski_routes').hidden).toBe(true);
    expect(hint('ski_routes').textContent).toBe('');
    expect(dimmed('terrain_filter')).toBe(true);
    zoomTo(12);
    expect(dimmed('terrain_filter')).toBe(false);
  });

  it('stays plain for a gate the registry does not list', async () => {
    gates = gates.filter((g) => g.key !== 'ski_routes');
    await load();
    expect(dimmed('ski_routes')).toBe(false);
    expect(hint('ski_routes').hidden).toBe(true);
  });
});

describe('"Sync problem terrain" (SNOW-1421)', () => {
  it('dims and says to zoom in below z12, keeping its switch on', async () => {
    await load();
    zoomTo(11);
    expect(dimmed('problem_terrain')).toBe(true);
    expect(hint('problem_terrain').textContent).toBe('· zoom in');
    expect(row('problem_terrain').querySelector('[data-layer-switch]').getAttribute('aria-checked')).toBe('true');
  });

  it('recovers at z12, where its slopes are drawn', async () => {
    await load();
    zoomTo(12);
    expect(dimmed('problem_terrain')).toBe(false);
    expect(hint('problem_terrain').hidden).toBe(true);
  });

  it('leaves the bulletins\' own dimming to the layers menu', async () => {
    row('problem_terrain').setAttribute('data-bulletins-off', '');
    await load();
    zoomTo(12);
    expect(row('problem_terrain').hasAttribute('data-bulletins-off')).toBe(true);
    expect(dimmed('problem_terrain')).toBe(false);
  });
});
