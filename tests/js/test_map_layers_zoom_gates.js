/*
 * tests/js/test_map_layers_zoom_gates.js — the layers menu's zoom-limited
 * rows (SNOW-1280).
 *
 * static/js/map_layers_zoom_gates.js against a reduced copy of the menu's
 * two zoom-limited rows and its note, with `MAP` and
 * `window.pwaZoomGates` (map.js) stood in for by stubs.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the "· ≥z10" suffix shows whether the layer is on or off;
 *   - a row fades and says "Zoom in to view" only while its layer is on
 *     AND the map is below its zoom, and recovers when either changes;
 *   - the note shows only while some row is faded, naming the span of
 *     every gate the menu shows;
 *   - a row whose gate the registry does not list stays plain.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/zoom_control_core.js';

const MARKUP = `
  <div id="basemap-menu">
    <ul>
      <li data-zoom-note hidden><span data-zoom-note-text></span></li>
      <li data-zoom-gate="terrain_filter">
        <button><span data-row-label>Steep terrain</span><span data-zoom-suffix></span>
          <span data-zoom-hint hidden>Zoom in to view</span></button>
        <button data-layer-switch aria-checked="false"></button>
      </li>
      <li data-zoom-gate="ski_routes">
        <button data-layer-switch aria-checked="true"><span data-row-label>Ski-touring routes</span>
          <span data-zoom-suffix></span><span data-zoom-hint hidden>Zoom in to view</span></button>
      </li>
    </ul>
  </div>`;

let zoom;
let zoomHandler;
let gates;

const row = (key) => document.querySelector(`[data-zoom-gate="${key}"]`);
const note = () => document.querySelector('[data-zoom-note]');
const setOn = (key, on) => row(key).querySelector('[data-layer-switch]')
  .setAttribute('aria-checked', String(on));
/** Let the MutationObserver's callback run. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

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
    { key: 'route_slopes', zoom: 14, upTo: false },
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

describe('the zoom suffix', () => {
  it('follows every zoom-limited name, on or off', async () => {
    await load();
    expect(row('terrain_filter').querySelector('[data-zoom-suffix]').textContent).toBe('· ≥z12');
    expect(row('ski_routes').querySelector('[data-zoom-suffix]').textContent).toBe('· ≥z10');
  });

  it('is left empty for a gate the registry does not list', async () => {
    gates = gates.filter((g) => g.key !== 'ski_routes');
    await load();
    expect(row('ski_routes').querySelector('[data-zoom-suffix]').textContent).toBe('');
    expect(row('ski_routes').hasAttribute('data-zoom-hidden')).toBe(false);
  });
});

describe('a faded row', () => {
  it('fades and says to zoom in while on and below its zoom', async () => {
    await load();
    expect(row('ski_routes').hasAttribute('data-zoom-hidden')).toBe(true);
    expect(row('ski_routes').querySelector('[data-zoom-hint]').hidden).toBe(false);
  });

  it('stays plain while its layer is off', async () => {
    await load();
    expect(row('terrain_filter').hasAttribute('data-zoom-hidden')).toBe(false);
    expect(row('terrain_filter').querySelector('[data-zoom-hint]').hidden).toBe(true);
  });

  it('recovers once the map reaches its zoom', async () => {
    await load();
    zoomTo(10);
    expect(row('ski_routes').hasAttribute('data-zoom-hidden')).toBe(false);
    expect(row('ski_routes').querySelector('[data-zoom-hint]').hidden).toBe(true);
  });

  it('follows its switch', async () => {
    await load();
    setOn('terrain_filter', true);
    await flush();
    expect(row('terrain_filter').hasAttribute('data-zoom-hidden')).toBe(true);
    setOn('ski_routes', false);
    await flush();
    expect(row('ski_routes').hasAttribute('data-zoom-hidden')).toBe(false);
  });
});

describe("the menu's note", () => {
  it('names the span of the gates the menu shows while a row is faded', async () => {
    await load();
    expect(note().hidden).toBe(false);
    expect(note().textContent.trim())
      .toBe('Features marked z10–12 are visible at specific zoom levels only.');
  });

  it('goes when nothing is faded', async () => {
    await load();
    zoomTo(11);
    expect(note().hidden).toBe(true);
    expect(note().textContent.trim()).toBe('');
  });
});
